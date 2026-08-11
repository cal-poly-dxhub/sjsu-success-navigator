"""Conversation history in DynamoDB: the server's copy of the turn, and the only one.

The second slice of per-user accounts and chat history (docs/accounts-and-storage.md,
Storage and Turn lifecycle). The table and its grant landed first; this is the code that
finally reads and writes them.

WHY THE SERVER OWNS THIS. The client used to post its own transcript and the loop replayed
it verbatim. That is a PROMPT INJECTION VECTOR, not a memory bug: a forged assistant turn
lets an attacker establish rules the model then treats as its own prior commitment, which
is a different order of problem in an app that receives crisis disclosures. There is no
sanitising a forged turn - a well-formed lie is indistinguishable from a true record - so
the only fix is that the record never leaves the server.

The item shapes are the doc's, not this module's invention:

    conversation header   pk=USER#<sub>   sk=CONV#<convId>
                          title, createdAt, lastActivityAt, messageCount
    message               pk=USER#<sub>   sk=MSG#<convId>#<ulid>
                          role, text, cards (URLs already resolved), createdAt

`USER#<sub>` is built HERE from the JWT claim the handler read, never from anything in the
request body. That is the whole isolation story for this table: a request cannot address
another student's partition, because the partition is not something a request can name.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

# Crockford base32 - ULID's alphabet. I, L, O and U are absent so a transcribed id cannot
# be confused with 1, 0 or V.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# How much of the first user message becomes the header's title. A label for a future
# conversation list, not content: the message itself is stored whole, one item along.
_TITLE_MAX_CHARS = 80

# The last ULID this container minted, as an integer. See new_ulid.
_last_ulid_int = 0


def _encode_crockford(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid() -> str:
    """A 26-character ULID: 48 bits of millisecond timestamp, then 80 bits of randomness.

    NOT a UUID4, and the sort key is the reason. Messages are ordered by their sort key and
    nothing else, so the id has to sort lexicographically in the order the messages were
    written; uuid4 sorts arbitrarily, which would shuffle a conversation. The doc fixes this
    ("ULID in the sort key, not a timestamp"): a bare timestamp collides, and two messages
    that collide in the key are one message.

    MONOTONIC WITHIN A CONTAINER. Two ids minted in the same millisecond would otherwise be
    ordered by their random halves - a coin toss between a question and its answer. A Lambda
    container handles one request at a time, so remembering the last value and stepping past
    it is enough; across containers the timestamp already separates them.

    The shape is pinned by models.CONVERSATION_ID_PATTERN, which is what a client-supplied
    conversation id is validated against - so a minted id and an accepted id cannot drift.
    """
    global _last_ulid_int

    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    if value <= _last_ulid_int:
        value = _last_ulid_int + 1
    _last_ulid_int = value
    return _encode_crockford(value, 26)


def new_conversation_id() -> str:
    """The id for a conversation the client did not name. THE SERVER MINTS IT.

    The client never chooses one (docs/accounts-and-storage.md, Turn lifecycle). A forged id
    is not a threat - the partition still comes from the JWT, so it reads as an empty
    conversation belonging to the forger - but minting server-side is what makes that true
    without a lookup: there is no id the client can supply that resolves to somebody else.
    """
    return new_ulid()


def _now_iso() -> str:
    """ISO 8601 UTC, the format every timestamp in these items uses except `expiresAt`,
    which is epoch seconds because that is the only format TTL reads. Nothing writes
    `expiresAt` yet - the retention window is an open policy question with the university."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class StoredMessage:
    """One message as the CONTEXT projection sees it: role and original text.

    Deliberately not the whole item. Two projections of the same query (the doc): the model
    is fed original message text, and the stored cards - resolved URLs, rendered titles -
    are for a display read that does not exist yet. Rendered cards are never fed back to the
    model, and the cheapest way to guarantee that is not to fetch them.
    """

    role: str
    text: str
    sort_key: str


class ConversationStore:
    """The table, and the four things a turn does to it.

    One client per container, built lazily: a cold start that never reaches a chat turn
    should not pay for a connection it does not use.
    """

    def __init__(self, table_name: str):
        self._table_name = table_name
        self._table = None

    def _table_resource(self):
        if self._table is None:
            # No explicit region: the table lives in the function's own region and Lambda
            # always sets AWS_REGION. Passing one from settings would invent the possibility
            # of them differing, which the stack has no way to produce.
            #
            # Short timeouts, and they are not arbitrary. These calls sit INSIDE the turn's
            # 29-second budget alongside a Bedrock call that needs most of it, so a stalled
            # DynamoDB socket has to fail fast enough to leave the student an answer.
            resource = boto3.resource(
                "dynamodb",
                config=Config(
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=1,
                    read_timeout=3,
                ),
            )
            self._table = resource.Table(self._table_name)
        return self._table

    # --- writes ---------------------------------------------------------------

    def append_message(
        self,
        *,
        user_id: str,
        conversation_id: str,
        role: str,
        text: str,
        cards: list[dict[str, Any]] | None = None,
    ) -> str:
        """Append one message and return its sort key.

        ONE ITEM PER MESSAGE, never a conversation rewritten in place: a whole-conversation
        item hits the 400 KB cap, pays a full rewrite on every reply, and loses a turn
        outright when two of them race.

        `cards` is stored with URLs already resolved, which is what a display read will want
        and what the model must never be handed back. It is absent on a user message and on
        an assistant reply that made none - an empty list would claim the model produced a
        card group that it did not.

        The safety panel is NOT stored. It is server-authored from the model's keys against
        the table in app/safety.py, so it is reproducible rather than recorded, and the doc
        names exactly three attributes on a message.
        """
        sort_key = f"MSG#{conversation_id}#{new_ulid()}"
        item: dict[str, Any] = {
            "pk": f"USER#{user_id}",
            "sk": sort_key,
            "role": role,
            "text": text,
            "createdAt": _now_iso(),
        }
        if cards:
            item["cards"] = cards

        self._table_resource().put_item(Item=item)
        self._touch_header(
            user_id=user_id,
            conversation_id=conversation_id,
            title=_title_from(text) if role == "user" else None,
        )
        return sort_key

    def _touch_header(
        self,
        *,
        user_id: str,
        conversation_id: str,
        title: str | None,
    ) -> None:
        """Bump the header's counter and its last-activity stamp, creating it on first use.

        An ATOMIC ADD rather than a read-modify-write, because two turns in flight would
        otherwise each write the count they read. `messageCount` is an aggregate and never a
        key: the messages themselves are the record, and this is the number a conversation
        list shows beside a title.

        SWALLOWS ITS OWN FAILURES. The message is already durable by the time this runs, and
        a lost counter increment must not turn a recorded disclosure into a failed one. A
        drifted count is repairable from the messages; the messages are not repairable from
        anything.

        Every attribute goes through ExpressionAttributeNames. Not superstition: DynamoDB's
        reserved-word list is long and unmemorable, and a name that collides fails the call
        at runtime rather than at synth.
        """
        expression_names = {
            "#createdAt": "createdAt",
            "#lastActivityAt": "lastActivityAt",
            "#messageCount": "messageCount",
        }
        expression_values: dict[str, Any] = {":now": _now_iso(), ":one": 1}
        sets = [
            "#lastActivityAt = :now",
            "#createdAt = if_not_exists(#createdAt, :now)",
        ]
        if title:
            # if_not_exists, so the FIRST user message names the conversation and no later
            # one renames it under the student.
            expression_names["#title"] = "title"
            expression_values[":title"] = title
            sets.append("#title = if_not_exists(#title, :title)")

        try:
            self._table_resource().update_item(
                Key={"pk": f"USER#{user_id}", "sk": f"CONV#{conversation_id}"},
                UpdateExpression=f"SET {', '.join(sets)} ADD #messageCount :one",
                ExpressionAttributeNames=expression_names,
                ExpressionAttributeValues=expression_values,
            )
        except Exception:
            logger.exception(
                "Could not update the conversation header; the message itself is stored"
            )

    # --- read -----------------------------------------------------------------

    def recent_messages(
        self,
        *,
        user_id: str,
        conversation_id: str,
        limit: int,
        exclude_sort_key: str | None = None,
    ) -> list[StoredMessage]:
        """The previous `limit` messages, oldest first. ONE descending, limited query.

        DESCENDING with a Limit, then reversed in memory: the newest N is the only window
        the model gets, and asking DynamoDB for it directly means a long conversation costs
        the same read as a short one. Ascending would page through the whole conversation to
        reach its end.

        STRONGLY CONSISTENT, deliberately. This never reads back its own write -
        `exclude_sort_key` drops the message this turn just wrote, and the current message is
        appended in memory by the orchestrator - so consistency is not about the write that
        just happened. It is about the turn BEFORE: two quick messages against an eventually
        consistent read can miss the previous assistant reply, which silently loses a turn
        and lands the model in exactly the alternation the doc warns about.

        The `limit + 1` fetch is the slot this turn's own user message occupies. Asking for
        `limit` and then dropping our own would return one turn less than configured, which
        is a quiet shortening of context nobody would notice.
        """
        if limit <= 0:
            return []

        fetch = limit + 1 if exclude_sort_key else limit
        result = self._table_resource().query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeNames={"#role": "role", "#text": "text"},
            ExpressionAttributeValues={
                ":pk": f"USER#{user_id}",
                ":prefix": f"MSG#{conversation_id}#",
            },
            # The CONTEXT projection: original message text only. Stored cards are for the
            # display read and are never fed back to the model.
            ProjectionExpression="sk, #role, #text",
            ScanIndexForward=False,
            Limit=fetch,
            ConsistentRead=True,
        )

        messages: list[StoredMessage] = []
        for item in result.get("Items") or []:
            sort_key = item.get("sk")
            if exclude_sort_key is not None and sort_key == exclude_sort_key:
                continue
            role = item.get("role")
            text = (item.get("text") or "").strip()
            # An unreadable item is skipped rather than failing the turn. The only way one
            # gets here is a shape this code did not write, and refusing to answer because
            # of a stray item would be a worse outcome than answering with less context.
            if role not in ("user", "assistant") or not text:
                logger.warning("Skipping an unreadable history item: sk=%r", sort_key)
                continue
            messages.append(StoredMessage(role=role, text=text, sort_key=sort_key))
            if len(messages) == limit:
                break

        messages.reverse()
        return messages


def _title_from(text: str) -> str:
    title = " ".join((text or "").split())
    if len(title) <= _TITLE_MAX_CHARS:
        return title
    return title[: _TITLE_MAX_CHARS - 1].rstrip() + "…"

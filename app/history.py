"""Conversation history in DynamoDB: the server's copy of the turn, and the only one.

The second and third slices of per-user accounts and chat history
(docs/accounts-and-storage.md, Storage and Turn lifecycle). The table and its grant landed
first, then the turn that writes it; this is the code that reads and writes them.

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
                          role, text, sources (the ref-to-URL pairs the reply cited),
                          escalation (the assembled email draft), createdAt

`text` ON AN ASSISTANT MESSAGE IS THE MODEL'S OWN REPLY, TAGS AND ALL. That is the whole of
the record and it is deliberately not the rendered halves: the card blocks, the safety tag,
the escalation tag and - critically - WHICH SIDE OF THE CARD GROUP each piece of prose sat on
are all still in the string, so re-parsing it reproduces the turn instead of approximating
it. It used to be stored pre-rendered, as the lead-in and the closing line glued together
with the cards in a second attribute, and nothing in that shape could say where the card
group belonged. A reply written as lead-in, cards, closing question came back as one bubble
with the cards underneath, and the question the student was actually being asked was no
longer under the cards it referred to.

`sources` is the other half of that, and it exists because the model never sees a URL and so
can never write one: `<card ref="2">` resolves against pairs the SERVER recorded during the
turn (app/cards.py, cited_source_urls). Without them a stored reply re-parses into cards with
no links, and re-running the retrieval instead would resolve the same ref against today's
index - a different page from the one the student was shown.

`cards` IS NO LONGER WRITTEN AND IS STILL READ. Every message stored before this carries one,
and its `text` is already-rendered prose rather than model text, so the read path hands those
back exactly as it always did rather than re-parsing prose that has already been through the
parser once. There is no backfill: nothing in an old row is wrong, it is only less than a new
row knows.

plus two items that are not history at all and share the partition because the partition is
already the user (app/ratelimit.py and app/streaming.py; claim_message_allowance and
open_connection below):

    rate counter          pk=USER#<sub>   sk=RATE#DAY#<YYYY-MM-DD>
                          count, expiresAt
    open connection       pk=USER#<sub>   sk=CONN#<connectionId>
                          connectedAt, expiresAt

Both expire by the table's TTL attribute, and neither is visible to any read here: the
conversation list is begins_with('CONV#') and both message reads are
begins_with('MSG#<convId>#').

`USER#<sub>` is built HERE from the JWT claim the handler read, never from anything in the
request body. That is the whole isolation story for this table: a request cannot address
another student's partition, because the partition is not something a request can name.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

# Crockford base32 - ULID's alphabet. I, L, O and U are absent so a transcribed id cannot
# be confused with 1, 0 or V.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# How much of the first user message becomes the header's title, when nothing better has
# named it. A label for the conversation list, not content: the message itself is stored
# whole, one item along.
#
# This is the FALLBACK title, and it is written on the first turn precisely so that the
# model titling that follows (app/titles.py) can fail without leaving the conversation
# nameless. The number is settings.title_max_chars, passed to the store; the constant here
# is what a ConversationStore built without one uses.
_TITLE_MAX_CHARS = 80

# Shown for a header that carries no title. See list_conversations for the one way that
# happens - it is not the ordinary case.
_UNTITLED = "Untitled chat"

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


def _is_conditional_check_failure(exc: Exception) -> bool:
    """Is this the exception DynamoDB raises when a ConditionExpression was not met?

    Read off the error CODE rather than caught as botocore's ClientError class, and that is
    deliberate twice over. The code is the contract - botocore's exception classes are
    generated and their identity has changed shape before - and catching a narrow, named
    condition means every OTHER failure (throttling, a network fault) is re-raised into the
    handler's 502 instead of being quietly reported as "no such conversation". A throttled
    rename that told the student their chat did not exist would be a lie the logs would not
    even record.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = (response.get("Error") or {}).get("Code")
    return code == "ConditionalCheckFailedException"


def _now_iso() -> str:
    """ISO 8601 UTC, the format every timestamp in these items uses except `expiresAt`,
    which is epoch seconds because that is the only format TTL reads. Nothing writes
    `expiresAt` yet - the retention window is an open policy question with the university."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class StoredMessage:
    """One message as the CONTEXT projection sees it: role and original text.

    Deliberately not the whole item. TWO PROJECTIONS OF THE SAME STORED TURNS (the doc): the
    model is fed original message text, and the stored cards - resolved URLs, rendered
    titles - go to the browser through DisplayMessage below. Rendered cards are never fed
    back to the model, and the cheapest way to guarantee that is not to fetch them.
    """

    role: str
    text: str
    sort_key: str


@dataclass(frozen=True)
class DisplayMessage:
    """One message as the DISPLAY projection sees it: the same item, read for a browser.

    The other half of the doc's two projections, and the reason this is a separate type
    rather than a flag on StoredMessage. What the browser needs is exactly what the model
    must not be given - the reply's markup and the URLs behind it - so the two reads return
    two shapes and a caller cannot pass one where the other belongs.

    IT IS THE STORED ITEM, NOT A RENDERED TURN. Turning `text` and `sources` back into prose,
    cards and a safety panel is app/orchestrator.py's job, through the same parser the live
    turn used. This type deliberately stops at the row, so there is no second renderer here
    to drift from that one.
    """

    role: str
    text: str
    # The email draft this turn offered, as it was assembled then. Display-only, exactly
    # like the cards beside it: it never goes back to the model.
    escalation: dict[str, Any] | None
    created_at: str | None
    # What this reply's `<card ref="N">` blocks resolve against. Empty on a user message, on
    # a reply that cited nothing, and on every message stored before the record kept the
    # model's own text.
    sources: dict[int, str] = field(default_factory=dict)
    # LEGACY, AND READ-ONLY. Messages written before the record kept model text carry their
    # cards already rendered, and their `text` is prose that has been through the parser
    # once. Present means "this row was written by the old shape"; the read path renders it
    # as it always did rather than re-parsing prose. Nothing writes this any more.
    cards: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ConversationSummary:
    """One row of the conversation list: a header item, as the sidebar shows it."""

    conversation_id: str
    title: str
    created_at: str | None
    last_activity_at: str | None
    message_count: int


class ConversationStore:
    """The table, and the four things a turn does to it.

    One client per container, built lazily: a cold start that never reaches a chat turn
    should not pay for a connection it does not use.
    """

    def __init__(self, table_name: str, title_max_chars: int = _TITLE_MAX_CHARS):
        self._table_name = table_name
        self._title_max_chars = title_max_chars
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
        sources: dict[int, str] | None = None,
        escalation: dict[str, Any] | None = None,
    ) -> str:
        """Append one message and return its sort key.

        ONE ITEM PER MESSAGE, never a conversation rewritten in place: a whole-conversation
        item hits the 400 KB cap, pays a full rewrite on every reply, and loses a turn
        outright when two of them race.

        `text` ON AN ASSISTANT MESSAGE IS WHAT THE MODEL WROTE, tags and all - see the module
        docstring for why the rendered halves are not the record. On a user message it is the
        student's own words, unchanged, as it always was.

        `sources` is the ref-to-URL map the reply cited, and it is the only thing here the
        model could not have written: it never sees a URL. Absent on a user message and on a
        reply that cited nothing.

        `escalation` is the assembled email draft, stored for the OPPOSITE reason the safety
        panel is not (below): it is not reproducible. It was assembled from a recipient and
        a subject that live in deploy config and from the address on the token that turn was
        sent with, so re-deriving it later would render whatever those say today rather than
        what the student was actually shown. Absent when the turn made no offer.

        The safety panel is NOT stored, and now it does not need to be: the model's keys are
        still in `text`, so app/safety.py resolves them again on the way out and a reopened
        crisis turn gets its contacts back. When the panel was the only thing derived from a
        reply this server had already discarded, it was the one part of a turn that could
        never come back.

        DynamoDB map keys are strings, so the source refs are written as strings and read
        back as ints at the boundary in conversation_messages - once, rather than leaving
        every consumer to remember which side of the wire it is on.
        """
        sort_key = f"MSG#{conversation_id}#{new_ulid()}"
        item: dict[str, Any] = {
            "pk": f"USER#{user_id}",
            "sk": sort_key,
            "role": role,
            "text": text,
            "createdAt": _now_iso(),
        }
        if sources:
            item["sources"] = {str(ref_id): url for ref_id, url in sources.items()}
        if escalation:
            item["escalation"] = escalation

        self._table_resource().put_item(Item=item)
        self._touch_header(
            user_id=user_id,
            conversation_id=conversation_id,
            title=(
                _title_from(text, self._title_max_chars) if role == "user" else None
            ),
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

    def claim_message_allowance(
        self,
        *,
        user_id: str,
        window_key: str,
        limit: int,
        expires_at: int,
    ) -> bool:
        """Take one message off this user's allowance for `window_key`. True if there was one.

        ONE ATOMIC CONDITIONAL WRITE, and no read before it. The read-then-write shape - load
        the count, compare it, write it back - is exactly the race this has to survive: two
        turns in flight both read 59, both decide they are under the limit, and both write
        60. DynamoDB serialises updates to a single item and evaluates the condition against
        the value it holds at that moment, so the increment and the decision are the same
        operation and the second of two concurrent requests at the limit sees the first one's
        write.

        The item lives in the CALLER'S OWN PARTITION under a third sort-key prefix:

            rate counter   pk=USER#<sub>   sk=RATE#DAY#<window>   count, expiresAt

        which is invisible to every read in this module - the conversation list is
        begins_with('CONV#') and both message reads are begins_with('MSG#<convId>#') - so
        nothing has to learn to skip it. `USER#<sub>` is built from the JWT claim like every
        other key here, and the window key comes from the server clock, so THERE IS NOTHING
        IN A REQUEST BODY THAT CAN REACH EITHER HALF of this item's identity.

        `expiresAt` is the table's TTL attribute (epoch seconds), written with if_not_exists
        so the value is fixed by the request that opened the window rather than rewritten on
        every message. This is the first thing in the app to set it: a counter for a day that
        has passed is meaningless, which makes it the one item kind whose retention is not
        the open policy question the transcripts are (docs/accounts-and-storage.md, Open).

        A conditional failure is NOT an error - it is the guard doing its job - so it comes
        back as False. Every other failure is re-raised for the caller to decide about; see
        app/ratelimit.py, which fails OPEN on them.
        """
        try:
            self._table_resource().update_item(
                Key={"pk": f"USER#{user_id}", "sk": f"RATE#DAY#{window_key}"},
                UpdateExpression=(
                    "SET #expiresAt = if_not_exists(#expiresAt, :expiresAt) "
                    "ADD #count :one"
                ),
                # attribute_not_exists covers the first message of a window, where there is
                # no count to compare. `<` and not `<=`: the count is how many have already
                # been taken, so at limit-1 this is the last one allowed through and the
                # write that follows makes it equal to the limit.
                ConditionExpression=(
                    "attribute_not_exists(#count) OR #count < :limit"
                ),
                # Both names go through ExpressionAttributeNames for the reason _touch_header
                # gives: `count` is a DynamoDB reserved word, and a reserved word used bare
                # fails the call at runtime rather than at synth.
                ExpressionAttributeNames={
                    "#count": "count",
                    "#expiresAt": "expiresAt",
                },
                ExpressionAttributeValues={
                    ":one": 1,
                    ":limit": limit,
                    ":expiresAt": expires_at,
                },
            )
        except Exception as exc:
            if _is_conditional_check_failure(exc):
                return False
            raise
        return True

    def open_connection(
        self,
        *,
        user_id: str,
        connection_id: str,
        expires_at: int,
    ) -> None:
        """Record that this user has a WebSocket connection open.

            connection   pk=USER#<sub>   sk=CONN#<connectionId>   connectedAt, expiresAt

        A FOURTH SORT-KEY PREFIX IN THE USER'S OWN PARTITION, which is the same trick the
        rate counter above plays and for the same reason: the partition is already the
        user, so an item that is about the user needs no new table and no new grant. It is
        invisible to every read in this module - the conversation list is
        begins_with('CONV#') and both message reads are begins_with('MSG#<convId>#') - so
        nothing had to learn to skip it.

        THE PARTITION IS THE USER, NOT THE CONNECTION, and that was a real choice. Keying
        on `CONN#<connectionId>` would make the record addressable by connection id alone,
        which sounds useful until you notice nothing needs it: API Gateway carries the
        $connect authorizer's context onto every later route on the connection, so the
        message route already has a `sub` that came out of a verified token. Keeping the
        partition the user preserves the property the whole table is built on
        (docs/accounts-and-storage.md, Storage) - every key here derives from a JWT claim -
        rather than opening one item kind that does not.

        TTL IS NOT HOUSEKEEPING HERE, it is the whole lifetime story. API Gateway closes an
        idle connection after 10 minutes and any connection after 2 hours, so a record that
        outlives those quotas describes a connection that cannot exist. `expiresAt` is the
        table's existing TTL attribute (epoch seconds), set well past the hard cap so DynamoDB
        collects the rows $disconnect never reached - a Lambda error, a throttle, a region
        blip - rather than leaving them to accumulate forever.

        SWALLOWS ITS OWN FAILURES. This record is a record, not a gate: nothing reads it to
        decide whether a student may stream, so a write that fails must not cost them their
        connection. Same posture as _touch_header above.
        """
        try:
            self._table_resource().put_item(
                Item={
                    "pk": f"USER#{user_id}",
                    "sk": f"CONN#{connection_id}",
                    "connectedAt": _now_iso(),
                    "expiresAt": expires_at,
                }
            )
        except Exception:
            logger.exception(
                "Could not record a WebSocket connection; streaming continues without it"
            )

    def close_connection(self, *, user_id: str, connection_id: str) -> None:
        """Forget a connection that has closed. Idempotent, and swallows its failures.

        A delete of something that is not there is not an error - $disconnect can fire for
        a connection whose open record never landed - and the TTL is the backstop for the
        case where this never runs at all.
        """
        try:
            self._table_resource().delete_item(
                Key={"pk": f"USER#{user_id}", "sk": f"CONN#{connection_id}"}
            )
        except Exception:
            logger.exception(
                "Could not clear a WebSocket connection record; its TTL will collect it"
            )

    def set_generated_title(
        self,
        *,
        user_id: str,
        conversation_id: str,
        title: str,
    ) -> bool:
        """Replace the header's title with the model's, unless a student has named it.

        TWO CONDITIONS, and they guard different things.

        `attribute_exists(sk)` keeps this from CREATING a header. An UpdateItem with no
        condition is an upsert, so a titling call for a conversation whose writes all failed
        would otherwise mint a header with a title and no messages under it - a row in the
        sidebar that opens empty.

        `attribute_not_exists(#userTitled)` is the promise this feature makes to a student
        who renamed a chat: no automatic titling can ever overwrite a name they chose.
        Today the ordering alone would do it - titling runs on the first turn, renaming
        cannot have happened yet - but that is an accident of when things run, and this is
        the property. If a later change ever titles a conversation again, it still cannot
        take a student's name away.

        Returns True if the title was written. A conditional failure is NOT an error and is
        not logged as one: it is the guard doing its job.
        """
        try:
            self._table_resource().update_item(
                Key={"pk": f"USER#{user_id}", "sk": f"CONV#{conversation_id}"},
                UpdateExpression="SET #title = :title",
                ConditionExpression=(
                    "attribute_exists(sk) AND attribute_not_exists(#userTitled)"
                ),
                ExpressionAttributeNames={"#title": "title", "#userTitled": "userTitled"},
                ExpressionAttributeValues={":title": title},
            )
        except Exception as exc:
            if _is_conditional_check_failure(exc):
                logger.info(
                    "Not applying a generated title: the conversation is gone or the "
                    "student named it themselves."
                )
                return False
            raise
        return True

    def rename_conversation(
        self,
        *,
        user_id: str,
        conversation_id: str,
        title: str,
    ) -> bool:
        """Give a conversation the name a student chose. Returns False if there is none.

        MARKS THE HEADER `userTitled`, which is the whole point of the attribute: it is not
        a display flag, it is the record that this name came from a person. set_generated_
        title above refuses to write over it.

        Conditional on the header existing, for the reason that condition exists everywhere
        in this module: without it a rename of a forged or already-deleted conversation id
        would CREATE a header - a titled, empty conversation appearing in the student's own
        sidebar because they asked to rename something that was not there. A forged id must
        touch nothing.
        """
        try:
            self._table_resource().update_item(
                Key={"pk": f"USER#{user_id}", "sk": f"CONV#{conversation_id}"},
                UpdateExpression="SET #title = :title, #userTitled = :true",
                ConditionExpression="attribute_exists(sk)",
                ExpressionAttributeNames={"#title": "title", "#userTitled": "userTitled"},
                ExpressionAttributeValues={":title": title, ":true": True},
            )
        except Exception as exc:
            if _is_conditional_check_failure(exc):
                return False
            raise
        return True

    # --- deletes --------------------------------------------------------------

    def delete_conversation(self, *, user_id: str, conversation_id: str) -> int:
        """Delete one conversation: every message, then the header. Returns the count.

        A HARD DELETE. There is no tombstone attribute and nothing left behind to filter
        out later: a student who deletes a conversation has said what they want, and a
        record that is invisible but present is a promise this code would be making on
        their behalf and could not keep. `expiresAt` is unset on these items, so nothing
        would ever come along and finish the job either.

        MESSAGES FIRST, HEADER LAST, and the ordering is the only part of this that is
        subtle. A failure partway through leaves either:

          - some messages gone, header still there: an empty but VISIBLE conversation. The
            student can see it and delete it again, and the retry finishes the job.
          - header gone, messages still there: invisible orphaned transcript. Nothing lists
            it, nothing can address it, and no TTL will collect it - a student's own words
            left on a table with no way to reach them.

        The first is recoverable and the second is not, so the header is deleted last.

        PAGINATED, because Query returns at most 1 MB and a long conversation exceeds that:
        stopping at the first page would silently leave the tail behind, which is the same
        orphaned-data failure arriving quietly.

        The partition comes from the JWT claim like every other operation here, so a forged
        conversation id addresses a prefix inside the caller's own partition that holds
        nothing. It deletes zero messages and a header that does not exist.
        """
        table = self._table_resource()
        prefix = f"MSG#{conversation_id}#"
        deleted = 0
        start_key = None

        while True:
            query: dict[str, Any] = {
                "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
                "ExpressionAttributeValues": {
                    ":pk": f"USER#{user_id}",
                    ":prefix": prefix,
                },
                # The sort key is all a delete needs. Not reading the text back is also the
                # cheaper read and keeps a transcript out of this function's memory.
                "ProjectionExpression": "sk",
                # Strongly consistent, so a message written seconds ago by the turn the
                # student is deleting cannot be missed and left orphaned.
                "ConsistentRead": True,
            }
            if start_key:
                query["ExclusiveStartKey"] = start_key

            result = table.query(**query)
            items = result.get("Items") or []
            if items:
                with table.batch_writer() as batch:
                    for item in items:
                        batch.delete_item(
                            Key={"pk": f"USER#{user_id}", "sk": item["sk"]}
                        )
                deleted += len(items)

            start_key = result.get("LastEvaluatedKey")
            if not start_key:
                break

        table.delete_item(
            Key={"pk": f"USER#{user_id}", "sk": f"CONV#{conversation_id}"}
        )
        logger.info("Deleted a conversation and its %s message(s).", deleted)
        return deleted

    # --- reads ----------------------------------------------------------------

    def list_conversations(self, *, user_id: str, limit: int) -> list[ConversationSummary]:
        """A user's conversations, most recently active first. The doc's first access
        pattern: one Query on `USER#<sub>` with `begins_with(sk, 'CONV#')`.

        THE PARTITION IS THE WHOLE ACCESS CONTROL. There is no owner attribute to filter on
        and none to forget, because `USER#<sub>` is built from the JWT claim - a request
        cannot name a partition that is not its own, so "list my conversations" and "list
        only mine" are the same query.

        STRONGLY CONSISTENT, for the case this endpoint exists to serve: a student sends a
        turn and reloads. An eventually consistent read can miss the header that turn just
        created, and a conversation missing from the list the moment after it was created
        reads as data loss whether or not it is.

        DESCENDING, so the Limit takes the NEWEST conversations rather than the oldest - the
        sort key is `CONV#<ulid>` and a ULID orders by mint time. The page is then re-sorted
        by `lastActivityAt`, because "most recent" to a student means the last one they
        typed in, not the last one they started. The limitation that buys: a long-dormant
        conversation revived today does not climb back INTO the page if it fell out of the
        newest `limit` by creation. Fixing that needs a secondary index keyed on activity,
        which is the doc's "purely additive" GSI and not needed at pilot scale.
        """
        if limit <= 0:
            return []

        result = self._table_resource().query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeNames={
                "#title": "title",
                "#createdAt": "createdAt",
                "#lastActivityAt": "lastActivityAt",
                "#messageCount": "messageCount",
            },
            ExpressionAttributeValues={":pk": f"USER#{user_id}", ":prefix": "CONV#"},
            ProjectionExpression=(
                "sk, #title, #createdAt, #lastActivityAt, #messageCount"
            ),
            ScanIndexForward=False,
            Limit=limit,
            ConsistentRead=True,
        )

        summaries: list[ConversationSummary] = []
        for item in result.get("Items") or []:
            sort_key = item.get("sk") or ""
            conversation_id = sort_key[len("CONV#") :]
            if not conversation_id:
                logger.warning("Skipping an unreadable conversation header: sk=%r", sort_key)
                continue
            summaries.append(
                ConversationSummary(
                    conversation_id=conversation_id,
                    # A header with no title is not the ordinary case: the first user
                    # message names the conversation. It is what a header created by the
                    # assistant write alone looks like - the turn whose user write failed -
                    # and that conversation still deserves a row rather than a blank one.
                    title=(item.get("title") or "").strip() or _UNTITLED,
                    created_at=item.get("createdAt"),
                    last_activity_at=item.get("lastActivityAt"),
                    # A DynamoDB number arrives as a Decimal, which json.dumps cannot
                    # serialise. Converted at the boundary, once, rather than left for the
                    # response encoder to trip over.
                    message_count=int(item.get("messageCount") or 0),
                )
            )

        summaries.sort(key=lambda s: s.last_activity_at or "", reverse=True)
        return summaries

    def conversation_messages(
        self,
        *,
        user_id: str,
        conversation_id: str,
        limit: int,
    ) -> list[DisplayMessage]:
        """One conversation's messages, oldest first, as the browser renders them.

        The doc's second access pattern - Query `USER#<sub>`, `begins_with('MSG#<convId>#')`
        - read through the DISPLAY projection: role, the reply as the model wrote it, the
        sources its cards resolve against, the stored draft and the timestamp. This is not
        the context read and must never be used as one; that one is `recent_messages`, and it
        deliberately fetches neither the sources nor the draft.

        A FORGED OR FOREIGN CONVERSATION ID RETURNS EMPTY. Not a 403, and not a lookup that
        would have to be got right: the partition comes from the JWT, so an id belonging to
        another student addresses a prefix that does not exist inside the caller's own
        partition. There is nothing here to authorize because there is nothing here to
        reach.

        Descending with a Limit and then reversed, exactly as the context read does it: a
        conversation longer than `limit` shows its NEWEST messages, which is the end a
        student is returning to. The alternative - ascending - would cap a long conversation
        at its opening exchanges and hide everything since.
        """
        if limit <= 0:
            return []

        result = self._table_resource().query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeNames={
                "#role": "role",
                "#text": "text",
                "#sources": "sources",
                "#cards": "cards",
                "#escalation": "escalation",
                "#createdAt": "createdAt",
            },
            ExpressionAttributeValues={
                ":pk": f"USER#{user_id}",
                ":prefix": f"MSG#{conversation_id}#",
            },
            ProjectionExpression=(
                "sk, #role, #text, #sources, #cards, #escalation, #createdAt"
            ),
            ScanIndexForward=False,
            Limit=limit,
            # Same reason as the list above: a student who sends a turn and immediately
            # reopens the conversation must see the turn they just sent.
            ConsistentRead=True,
        )

        messages: list[DisplayMessage] = []
        for item in result.get("Items") or []:
            role = item.get("role")
            text = (item.get("text") or "").strip()
            if role not in ("user", "assistant") or not text:
                logger.warning(
                    "Skipping an unreadable history item: sk=%r", item.get("sk")
                )
                continue
            cards = item.get("cards")
            escalation = item.get("escalation")
            messages.append(
                DisplayMessage(
                    role=role,
                    text=text,
                    escalation=dict(escalation) if isinstance(escalation, dict) else None,
                    created_at=item.get("createdAt"),
                    sources=_sources_from(item.get("sources")),
                    cards=list(cards) if isinstance(cards, list) else [],
                )
            )

        messages.reverse()
        return messages

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
            # The CONTEXT projection: message text only. The sources and the stored draft are
            # for the display read and are never fed back to the model. The text of an
            # assistant reply still carries the tags the model wrote; those come off at the
            # one place history becomes model input (app/orchestrator.py,
            # _build_converse_messages), not here - the record stays whole.
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


def _sources_from(stored: Any) -> dict[int, str]:
    """A stored `sources` map as ints and strings, or empty.

    DynamoDB map keys are strings and there is no numeric key type, so the refs come back as
    `"2"` and are turned into `2` HERE - one place, at the boundary, rather than leaving the
    card parser to wonder which side of the wire it is on. Same instinct as the Decimal
    conversion in list_conversations above.

    A ref that is not a number is dropped rather than raising: the only way one gets here is
    a row this code did not write, and refusing to open a conversation over a stray key would
    be a worse outcome than opening it with one card missing its link.
    """
    if not isinstance(stored, dict):
        return {}

    urls: dict[int, str] = {}
    for ref_id, url in stored.items():
        try:
            urls[int(ref_id)] = str(url)
        except (TypeError, ValueError):
            logger.warning("Skipping an unreadable stored source ref: %r", ref_id)
    return urls


def _title_from(text: str, cap: int = _TITLE_MAX_CHARS) -> str:
    title = " ".join((text or "").split())
    if len(title) <= cap:
        return title
    return title[: cap - 1].rstrip() + "…"

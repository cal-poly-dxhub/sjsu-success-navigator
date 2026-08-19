"""Conversation history in DynamoDB: the server's copy of the turn, and the only one.

Three sort-key prefixes share one partition, and every key derives from the JWT `sub`;
see docs/accounts-and-storage.md and docs/chat-service.md, Storage.
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

# Crockford base32: I, L, O and U are absent, so a transcribed id cannot read as 1, 0 or V.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# The FALLBACK title, written on the first turn so model titling may fail without leaving
# the conversation nameless. Settings passes the real cap in; this is a bare-store default.
_TITLE_MAX_CHARS = 80

# Shown for a header that carries no title. See list_conversations for the one way that is.
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
    """A 26-character ULID: 48 bits of millisecond timestamp, then 80 bits of randomness."""
    global _last_ulid_int

    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    if value <= _last_ulid_int:
        value = _last_ulid_int + 1
    _last_ulid_int = value
    return _encode_crockford(value, 26)


def new_conversation_id() -> str:
    """The id for a conversation the client did not name. THE SERVER MINTS IT."""
    return new_ulid()


def _is_conditional_check_failure(exc: Exception) -> bool:
    """Is this the exception DynamoDB raises when a ConditionExpression was not met?"""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = (response.get("Error") or {}).get("Code")
    return code == "ConditionalCheckFailedException"


def _now_iso() -> str:
    """ISO 8601 UTC, the format every timestamp in these items uses except `expiresAt`."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class StoredMessage:
    """One message as the CONTEXT projection sees it: role and original text."""

    role: str
    text: str
    sort_key: str


@dataclass(frozen=True)
class DisplayMessage:
    """One message as the DISPLAY projection sees it: the same item, read for a browser."""

    role: str
    text: str
    escalation: dict[str, Any] | None
    created_at: str | None
    sources: dict[int, str] = field(default_factory=dict)
    # LEGACY AND READ-ONLY. Nothing writes this any more; the read path still serves it.
    cards: list[dict[str, Any]] = field(default_factory=list)
    # Still written, unlike `cards`: it is where the office WAS when the student asked.
    place: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConversationSummary:
    """One row of the conversation list: a header item, as the sidebar shows it."""

    conversation_id: str
    title: str
    created_at: str | None
    last_activity_at: str | None
    message_count: int


class ConversationStore:
    """The table, and everything a turn does to it. One client per container, built lazily."""

    def __init__(self, table_name: str, title_max_chars: int = _TITLE_MAX_CHARS):
        self._table_name = table_name
        self._title_max_chars = title_max_chars
        self._table = None

    def _table_resource(self):
        if self._table is None:
            # No explicit region: Lambda always sets AWS_REGION and the table is local to it.
            # Short timeouts, because these calls sit inside the turn's own 29-second budget.
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
        place: dict[str, Any] | None = None,
    ) -> str:
        """Append one message and return its sort key."""
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
        if place:
            item["place"] = place

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
        """Bump the header's counter and its last-activity stamp, creating it on first use."""
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
            # if_not_exists, so the FIRST user message names the conversation.
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
        """Take one message off this user's allowance for `window_key`. True if there was
        one."""
        try:
            self._table_resource().update_item(
                Key={"pk": f"USER#{user_id}", "sk": f"RATE#DAY#{window_key}"},
                UpdateExpression=(
                    "SET #expiresAt = if_not_exists(#expiresAt, :expiresAt) "
                    "ADD #count :one"
                ),
                # attribute_not_exists covers the first message of a window. `<` not `<=`:
                # the count is how many have already been taken.
                ConditionExpression=(
                    "attribute_not_exists(#count) OR #count < :limit"
                ),
                # `count` is a DynamoDB reserved word, so both names go through
                # ExpressionAttributeNames or the call fails at runtime.
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

    def set_generated_title(
        self,
        *,
        user_id: str,
        conversation_id: str,
        title: str,
    ) -> bool:
        """Replace the header's title with the model's, unless a student has named it."""
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
        """Give a conversation the name a student chose. Returns False if there is none."""
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
        """Delete one conversation: every message, then the header. Returns the count."""
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
                # The sort key is all a delete needs, and it keeps a transcript out of memory.
                "ProjectionExpression": "sk",
                # Strongly consistent, so a message written seconds ago cannot be orphaned.
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
        """A user's conversations, most recently active first."""
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
                    # Not the ordinary case: the first user message names the conversation.
                    # This is a header the assistant write alone created.
                    title=(item.get("title") or "").strip() or _UNTITLED,
                    created_at=item.get("createdAt"),
                    last_activity_at=item.get("lastActivityAt"),
                    # A DynamoDB number is a Decimal, which json.dumps cannot serialise.
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
        """One conversation's messages, oldest first, as the browser renders them."""
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
                "#place": "place",
                "#createdAt": "createdAt",
            },
            ExpressionAttributeValues={
                ":pk": f"USER#{user_id}",
                ":prefix": f"MSG#{conversation_id}#",
            },
            ProjectionExpression=(
                "sk, #role, #text, #sources, #cards, #escalation, #place, #createdAt"
            ),
            ScanIndexForward=False,
            Limit=limit,
            # A student who sends a turn and immediately reopens it must see that turn.
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
            place = item.get("place")
            messages.append(
                DisplayMessage(
                    role=role,
                    text=text,
                    escalation=dict(escalation) if isinstance(escalation, dict) else None,
                    created_at=item.get("createdAt"),
                    sources=_sources_from(item.get("sources")),
                    cards=list(cards) if isinstance(cards, list) else [],
                    place=dict(place) if isinstance(place, dict) else None,
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
        """The previous `limit` messages, oldest first. ONE descending, limited query."""
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
            # The CONTEXT projection: message text only. An assistant reply still carries
            # the model's tags; those come off at the one point history becomes model input.
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
            # An unreadable item is skipped rather than failing the turn.
            if role not in ("user", "assistant") or not text:
                logger.warning("Skipping an unreadable history item: sk=%r", sort_key)
                continue
            messages.append(StoredMessage(role=role, text=text, sort_key=sort_key))
            if len(messages) == limit:
                break

        messages.reverse()
        return messages


def _sources_from(stored: Any) -> dict[int, str]:
    """A stored `sources` map as ints and strings, or empty. Converted once, at the boundary."""
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

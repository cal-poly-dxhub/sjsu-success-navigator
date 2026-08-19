"""Put app/ on sys.path so the Lambda's flat imports resolve as they do when deployed.

Also stubs boto3 before any app module is imported: the suite is hermetic; see
docs/chat-service.md, What the suites pin.
"""

import json
import os
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Set before collection, not in a fixture: load_settings() raises without them and
# handler.py calls it at import. setdefault, so a test can still override.
for _name, _value in {
    "KNOWLEDGE_BASE_ID": "KB-TEST",
    "GENERATION_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
    "TITLE_MODEL_ID": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "BEDROCK_REGION": "us-west-2",
    "INPUT_GUARDRAIL_ID": "gr-test",
    "INPUT_GUARDRAIL_VERSION": "1",
    "CHAT_HISTORY_TABLE_NAME": "chat-history-test",
}.items():
    os.environ.setdefault(_name, _value)

if "boto3" not in sys.modules:
    boto3_stub = types.ModuleType("boto3")

    def _no_client(*args, **kwargs):  # pragma: no cover - guard, not behaviour
        raise AssertionError(
            "a test reached boto3.client(); AWS calls must be monkeypatched"
        )

    def _no_resource(*args, **kwargs):  # pragma: no cover - guard, not behaviour
        raise AssertionError(
            "a test reached boto3.resource(); DynamoDB calls must be monkeypatched"
        )

    boto3_stub.client = _no_client
    boto3_stub.resource = _no_resource
    sys.modules["boto3"] = boto3_stub

if "botocore" not in sys.modules:
    botocore_stub = types.ModuleType("botocore")
    botocore_config = types.ModuleType("botocore.config")

    class _Config:  # pragma: no cover - a stand-in for botocore.config.Config
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    botocore_config.Config = _Config
    botocore_stub.config = botocore_config
    sys.modules["botocore"] = botocore_stub
    sys.modules["botocore.config"] = botocore_config


import pytest  # noqa: E402 - after the stubs, deliberately

from history import ConversationSummary, DisplayMessage, StoredMessage  # noqa: E402


# The sub every test signs with, unless it is testing what happens without one.
TEST_SUB = "11111111-2222-3333-4444-555555555555"


# An ordinary student's app client. A real deployment has exactly two.
TEST_CLIENT_ID = "web-client-id"

# The eval harness's machine client, the one the stack puts in RATE_LIMIT_EXEMPT_CLIENT_IDS.
EXEMPT_CLIENT_ID = "eval-client-id"


def _authorized(event, sub, client_id=TEST_CLIENT_ID):
    """Attach the claims the JWT authorizer would have put on the event."""
    if sub is not None:
        claims = {"sub": sub}
        if client_id is not None:
            claims["client_id"] = client_id
        event["requestContext"] = {"authorizer": {"jwt": {"claims": claims}}}
    return event


def chat_event(
    body,
    sub=TEST_SUB,
    is_base64=False,
    route="POST /chat",
    client_id=TEST_CLIENT_ID,
):
    event = {"body": body if isinstance(body, str) else json.dumps(body)}
    if route is not None:
        event["routeKey"] = route
    if is_base64:
        event["isBase64Encoded"] = True
    return _authorized(event, sub, client_id)


def conversations_event(sub=TEST_SUB):
    """GET /conversations. No body and no parameters - the only input is the claim."""
    return _authorized({"routeKey": "GET /conversations"}, sub)


def conversation_event(conversation_id, sub=TEST_SUB):
    """GET /conversations/{conversationId}, with the path parameter API Gateway extracts."""
    return _authorized(
        {
            "routeKey": "GET /conversations/{conversationId}",
            "pathParameters": {"conversationId": conversation_id},
        },
        sub,
    )


def rename_event(conversation_id, body, sub=TEST_SUB):
    """PATCH /conversations/{conversationId}. The id is in the PATH, never the body."""
    return _authorized(
        {
            "routeKey": "PATCH /conversations/{conversationId}",
            "pathParameters": {"conversationId": conversation_id},
            "body": body if isinstance(body, str) or body is None else json.dumps(body),
        },
        sub,
    )


def delete_event(conversation_id, sub=TEST_SUB):
    """DELETE /conversations/{conversationId}. No body at all."""
    return _authorized(
        {
            "routeKey": "DELETE /conversations/{conversationId}",
            "pathParameters": {"conversationId": conversation_id},
        },
        sub,
    )


class FakeConversationStore:
    """A ConversationStore stand-in that records the turn's table access IN ORDER, which is
    the assertion in most tests that use it."""

    def __init__(
        self,
        history=(),
        fail_on=(),
        conversations=(),
        messages=(),
        renamed=True,
        titled=True,
        deleted_messages=0,
        counters=None,
    ):
        self.history = list(history)
        self.fail_on = set(fail_on)
        self.conversations = list(conversations)
        self.messages = list(messages)
        # Keyed exactly as the real item is. Seeded by a test that wants a partway day.
        self.counters = dict(counters or {})
        # The lock models what DynamoDB buys from a conditional ADD on one item: compare
        # and increment as one operation.
        self._counter_lock = threading.Lock()
        # What the two CONDITIONAL writes report. False is the condition holding, not an error.
        self.renamed = renamed
        self.titled = titled
        self.deleted_messages = deleted_messages
        self.calls = []
        self.appended = []
        # The real item is pk=USER#<sub>, sk=CONN#<connectionId>, so the pair is the identity.
        self.connections = set()

    def append_message(self, **kwargs):
        self.calls.append(("append", kwargs))
        if kwargs["role"] in self.fail_on:
            raise RuntimeError(f"DynamoDB is unavailable ({kwargs['role']} write)")
        sort_key = f"MSG#{kwargs['conversation_id']}#SK{len(self.appended) + 1:04d}"
        self.appended.append({**kwargs, "sort_key_returned": sort_key})
        return sort_key

    def claim_message_allowance(self, *, user_id, window_key, limit, expires_at):
        """DynamoDB's conditional ADD, modelled: compare and increment as ONE step."""
        self.calls.append(("allowance", {"user_id": user_id, "window_key": window_key}))
        if "allowance" in self.fail_on:
            raise RuntimeError("DynamoDB is unavailable (rate limit write)")
        with self._counter_lock:
            count = self.counters.get((user_id, window_key), 0)
            if count >= limit:
                return False
            self.counters[(user_id, window_key)] = count + 1
            self.expires_at = expires_at
            return True

    def recent_messages(self, **kwargs):
        self.calls.append(("read", kwargs))
        if "read" in self.fail_on:
            raise RuntimeError("DynamoDB is unavailable (read)")
        return list(self.history)

    def list_conversations(self, **kwargs):
        self.calls.append(("list", kwargs))
        if "list" in self.fail_on:
            raise RuntimeError("DynamoDB is unavailable (list)")
        return list(self.conversations)

    def conversation_messages(self, **kwargs):
        self.calls.append(("display", kwargs))
        if "display" in self.fail_on:
            raise RuntimeError("DynamoDB is unavailable (display read)")
        return list(self.messages)

    def set_generated_title(self, **kwargs):
        self.calls.append(("title", kwargs))
        if "title" in self.fail_on:
            raise RuntimeError("DynamoDB is unavailable (title write)")
        return self.titled

    def rename_conversation(self, **kwargs):
        self.calls.append(("rename", kwargs))
        if "rename" in self.fail_on:
            raise RuntimeError("DynamoDB is unavailable (rename)")
        return self.renamed

    def delete_conversation(self, **kwargs):
        self.calls.append(("delete", kwargs))
        if "delete" in self.fail_on:
            raise RuntimeError("DynamoDB is unavailable (delete)")
        return self.deleted_messages

    def open_connection(self, **kwargs):
        """The connection record. It does NOT raise on `fail_on`: the real one swallows its
        own failures, because the record is a record and not a gate."""
        self.calls.append(("connect", kwargs))
        self.connections.add((kwargs["user_id"], kwargs["connection_id"]))

    def close_connection(self, **kwargs):
        self.calls.append(("disconnect", kwargs))
        self.connections.discard((kwargs["user_id"], kwargs["connection_id"]))

    @property
    def call_names(self):
        return [name for name, _ in self.calls]


@pytest.fixture
def store(monkeypatch):
    import handler

    fake = FakeConversationStore()
    monkeypatch.setattr(handler, "STORE", fake)
    return fake


@pytest.fixture
def daily_limit(monkeypatch):
    """Turn the per-user daily cap on for one test. It is OFF everywhere else, because the
    deployed default reads an environment variable the stack omits when it is disabled."""
    import dataclasses

    import handler

    def _set(limit, exempt=(EXEMPT_CLIENT_ID,)):
        settings = dataclasses.replace(
            handler.SETTINGS,
            daily_message_limit=limit,
            rate_limit_exempt_client_ids=frozenset(exempt),
        )
        monkeypatch.setattr(handler, "SETTINGS", settings)
        return settings

    return _set


def stored(role, text):
    return StoredMessage(role=role, text=text, sort_key=f"MSG#C#{role}-{text[:4]}")


def displayed(
    role,
    text,
    sources=None,
    escalation=None,
    created_at="2026-08-11T00:00:00Z",
    cards=None,
    place=None,
):
    """One stored row, as the display read hands it back."""
    return DisplayMessage(
        role=role,
        text=text,
        escalation=escalation,
        created_at=created_at,
        sources=dict(sources or {}),
        cards=list(cards or []),
        place=place,
    )


def summary(conversation_id, title="Tutoring", last_activity_at="2026-08-11T00:00:00Z"):
    return ConversationSummary(
        conversation_id=conversation_id,
        title=title,
        created_at="2026-08-10T00:00:00Z",
        last_activity_at=last_activity_at,
        message_count=4,
    )

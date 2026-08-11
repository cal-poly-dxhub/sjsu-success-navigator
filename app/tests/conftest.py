"""Put app/ on sys.path so the Lambda's flat imports (`from settings import ...`) resolve
in tests exactly as they do in the deployed function, where the handler and its modules sit
side by side at the bundle root.

Also stubs boto3 before any app module is imported. The suite is hermetic by design: the
chat path is Bedrock calls all the way down, and none of them can be exercised without an
account, so a test run must never depend on boto3 being installed or on credentials
existing. Anything that would touch AWS is monkeypatched per test - DynamoDB included,
which is what the fake store at the bottom of this file is for.
"""

import json
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The identity variables the CDK stack sets. settings.load_settings() raises without them,
# by design (app/settings.py), and handler.py calls it at import - so they have to exist
# before collection, not inside a fixture. setdefault so a test can still override.
for _name, _value in {
    "KNOWLEDGE_BASE_ID": "KB-TEST",
    "GENERATION_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
    "TITLE_MODEL_ID": "us.anthropic.claude-haiku-4-5",
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


# The Cognito sub every test signs its requests with, unless it is testing what happens
# without one.
TEST_SUB = "11111111-2222-3333-4444-555555555555"


def _authorized(event, sub):
    """Attach the claims the JWT authorizer would have put on the event.

    Tests build events THROUGH these helpers so nothing accidentally asserts on a request
    the deployed stack could not produce: every route is authorizer-gated, so a request with
    no `sub` is a misconfiguration rather than an anonymous student.
    """
    if sub is not None:
        event["requestContext"] = {"authorizer": {"jwt": {"claims": {"sub": sub}}}}
    return event


def chat_event(body, sub=TEST_SUB, is_base64=False, route="POST /chat"):
    event = {"body": body if isinstance(body, str) else json.dumps(body)}
    if route is not None:
        event["routeKey"] = route
    if is_base64:
        event["isBase64Encoded"] = True
    return _authorized(event, sub)


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


class FakeConversationStore:
    """A ConversationStore stand-in that records the turn's table access in order.

    The order is the assertion in most tests that use it: the student's message is written
    BEFORE the model is called, so a disclosure that then times out is still on record.
    """

    def __init__(
        self,
        history=(),
        fail_on=(),
        conversations=(),
        messages=(),
        titled=True,
    ):
        self.history = list(history)
        self.fail_on = set(fail_on)
        self.conversations = list(conversations)
        self.messages = list(messages)
        # What the CONDITIONAL title write reports back. False is not an error: it is the
        # condition holding - no such header, or one the student named themselves.
        self.titled = titled
        self.calls = []
        self.appended = []

    def append_message(self, **kwargs):
        self.calls.append(("append", kwargs))
        if kwargs["role"] in self.fail_on:
            raise RuntimeError(f"DynamoDB is unavailable ({kwargs['role']} write)")
        sort_key = f"MSG#{kwargs['conversation_id']}#SK{len(self.appended) + 1:04d}"
        self.appended.append({**kwargs, "sort_key_returned": sort_key})
        return sort_key

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

    @property
    def call_names(self):
        return [name for name, _ in self.calls]


@pytest.fixture
def store(monkeypatch):
    import handler

    fake = FakeConversationStore()
    monkeypatch.setattr(handler, "STORE", fake)
    return fake


def stored(role, text):
    return StoredMessage(role=role, text=text, sort_key=f"MSG#C#{role}-{text[:4]}")


def displayed(role, text, cards=None, created_at="2026-08-11T00:00:00Z"):
    return DisplayMessage(
        role=role, text=text, cards=list(cards or []), created_at=created_at
    )


def summary(conversation_id, title="Tutoring", last_activity_at="2026-08-11T00:00:00Z"):
    return ConversationSummary(
        conversation_id=conversation_id,
        title=title,
        created_at="2026-08-10T00:00:00Z",
        last_activity_at=last_activity_at,
        message_count=4,
    )

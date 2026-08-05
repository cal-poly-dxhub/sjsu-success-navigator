"""The deterministic safety intercept, and its position in the request pipeline.

The ordering test is the load-bearing one: it is the only thing standing between a
student in crisis and a fixed "I can't help with that" refusal.
"""

import json

import pytest

import handler
import safety


def _event(query):
    return {"body": json.dumps({"query": query})}


def _body(response):
    return json.loads(response["body"])


class _RecordingBedrock:
    """Records whether the guardrail was reached, and always blocks if it was."""

    def __init__(self):
        self.calls = []

    def apply_guardrail(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "action": "GUARDRAIL_INTERVENED",
            "outputs": [{"text": "I can't help with that request."}],
        }


@pytest.fixture
def blocking_guardrail(monkeypatch):
    fake = _RecordingBedrock()
    monkeypatch.setattr(handler, "_bedrock_client", lambda: fake)
    return fake


@pytest.fixture
def no_loop(monkeypatch):
    """The loop must not run in these tests; if it does, that is the failure."""

    def _forbidden(*args, **kwargs):
        raise AssertionError("the agent loop ran on a safety-intercepted request")

    monkeypatch.setattr(handler, "run_chat", _forbidden)


def test_the_safety_intercept_runs_before_the_guardrail(blocking_guardrail, no_loop):
    """THE ordering test (docs/synthesis.md, docs/architecture-v1.py).

    The guardrail here blocks everything it sees. If the guardrail ran first, this crisis
    message would come back as the fixed refusal string - the worst possible answer to it.
    Because the intercept runs first, the guardrail is never called at all and the student
    gets the crisis panel."""
    response = handler.lambda_handler(_event("I want to kill myself"), None)
    body = _body(response)

    assert blocking_guardrail.calls == [], (
        "the guardrail must not be called for an intercepted crisis message"
    )
    assert body["safetyHandoff"] is not None
    assert body["safetyHandoff"]["headline"] == "Talk to someone now"
    assert body["conversationalText"] != "I can't help with that request."


def test_a_non_crisis_message_does_reach_the_guardrail(blocking_guardrail, no_loop):
    """The converse of the ordering test: the intercept is a gate, not a bypass."""
    handler.lambda_handler(_event("where do I get tutoring?"), None)
    assert len(blocking_guardrail.calls) == 1


def test_the_intercept_makes_no_aws_call(monkeypatch, no_loop):
    """Deterministic means deterministic: no model, no guardrail, no network. A crisis
    answer must not depend on Bedrock being reachable."""

    def _forbidden():
        raise AssertionError("an AWS client was constructed on the safety path")

    monkeypatch.setattr(handler, "_bedrock_client", _forbidden)
    response = handler.lambda_handler(_event("I am in crisis"), None)
    assert _body(response)["safetyHandoff"] is not None


def test_the_panel_contacts_are_campos_verbatim():
    """The human-handoff destination is still unconfirmed with Student Affairs, so camp's
    contacts are carried unchanged - nothing here is invented."""
    handoff = safety.get_standard_safety_handoff()
    ids = [contact.id for contact in handoff.contacts]
    assert ids == ["988", "after-hours", "caps", "crisis-page"]


@pytest.mark.parametrize(
    "query",
    ["I want to kill myself", "thinking about suicide", "I was raped", "not safe at home"],
)
def test_representative_crisis_phrases_are_intercepted(query):
    assert safety.detect_safety(query) is not None


@pytest.mark.parametrize("query", ["where is the library?", "how do I apply for aid?"])
def test_ordinary_questions_are_not_intercepted(query):
    assert safety.detect_safety(query) is None


def test_the_output_scanner_attaches_the_panel_when_the_model_cites_a_hotline():
    """Camp's second net: if the model names a crisis line in prose but forgets the flag,
    the official panel is attached anyway and the cards are stripped."""
    from models import ChatResponse

    response = ChatResponse(
        conversationalText="Please call 988 for support.",
        statementBatches=None,
        talkToPersonAvailable=True,
    )
    result = safety.apply_safety_handoff_to_response(
        response, conversational_text=response.conversational_text, requested=False
    )
    assert result.safety_handoff is not None
    assert result.statement_batches is None

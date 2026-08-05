"""The Converse loop's two caps, and the wire contract camp's frontend depends on.

Nothing here reaches Bedrock: every model call is a fake whose script the test controls.
The point is the loop's control flow (when it stops, what it returns), which is exactly
what cannot be checked against a real account anyway.
"""

import copy

import pytest

import orchestrator
from models import ChatRequest
from settings import Settings

_SETTINGS = Settings(
    knowledge_base_id="KB123",
    generation_model_id="us.anthropic.claude-sonnet-4-6",
    bedrock_region="us-west-2",
    input_guardrail_id="gr-1",
    input_guardrail_version="3",
    max_converse_iterations=6,
    converse_deadline_seconds=22,
)


class _FakeBedrock:
    """A Converse stand-in. `script` is one response per call; the last repeats."""

    def __init__(self, script, on_call=None):
        self.script = script
        self.calls = 0
        self._on_call = on_call

    def converse(self, **kwargs):
        self.calls += 1
        # Deep-copied: the loop appends the assistant turn to the SAME list object after
        # the call returns, so holding a reference would let a later mutation change what
        # a test believes was sent.
        self.kwargs = copy.deepcopy(kwargs)
        if self._on_call is not None:
            self._on_call()
        index = min(self.calls - 1, len(self.script) - 1)
        return self.script[index]


def _text_turn(text):
    """A Converse response that ends the turn without calling a tool."""
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
    }


def _tool_turn(name, tool_input, tool_use_id="tu-1"):
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"toolUse": {"name": name, "toolUseId": tool_use_id, "input": tool_input}}
                ],
            }
        },
        "stopReason": "tool_use",
    }


@pytest.fixture
def no_retrieval(monkeypatch):
    """Retrieval returns nothing, so tests exercise loop control flow rather than cards."""
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])
    monkeypatch.setattr(orchestrator, "retrieve_statement_cards", lambda query, settings: [])


def _run(monkeypatch, fake, deadline=None, settings=_SETTINGS):
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
    return orchestrator.run_chat(
        ChatRequest(query="where do I get tutoring?"), settings, deadline=deadline
    )


def test_submit_chat_response_ends_the_loop(monkeypatch, no_retrieval):
    fake = _FakeBedrock(
        [_tool_turn("submit_chat_response", {"conversationalText": "Try Peer Connections.", "cards": []})]
    )
    response = _run(monkeypatch, fake)
    assert fake.calls == 1
    assert response.conversational_text == "Try Peer Connections."


def test_the_loop_stops_at_the_iteration_cap(monkeypatch, no_retrieval, caplog):
    """A model that never submits must not loop forever. Camp fell through silently;
    the cap is config here and the exhaustion is logged."""
    settings = Settings(**{**_SETTINGS.__dict__, "max_converse_iterations": 3})
    fake = _FakeBedrock([_tool_turn("retrieve_campus_resources", {"query": "tutoring"})])

    with caplog.at_level("WARNING"):
        response = _run(monkeypatch, fake, settings=settings)

    assert fake.calls == 3, "the loop must stop at exactly max_converse_iterations"
    assert "3-iteration cap" in caplog.text
    assert response.conversational_text


def test_the_loop_stops_at_the_wall_clock_deadline(monkeypatch, no_retrieval, caplog):
    """The cap that actually bounds a request. Six iterations of a slow model outlast the
    function without ever reaching the iteration cap - and being killed mid-Converse bills
    the invocation and returns nothing to the student."""
    # Each call burns 10 seconds of the monotonic clock; the deadline is 15 away.
    clock = {"now": 1000.0}
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: clock["now"])

    def burn_time():
        clock["now"] += 10.0

    fake = _FakeBedrock(
        [_tool_turn("retrieve_campus_resources", {"query": "tutoring"})], on_call=burn_time
    )

    with caplog.at_level("WARNING"):
        response = _run(monkeypatch, fake, deadline=1015.0)

    assert fake.calls == 2, "must not START a call once the deadline has passed"
    assert "wall-clock deadline" in caplog.text
    assert response.conversational_text


def test_the_deadline_path_does_not_retrieve_again(monkeypatch):
    """The fallback's last-resort retrieval is a fresh network call. Running it after the
    budget is spent is the exact overrun the deadline exists to prevent."""
    clock = {"now": 500.0}
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])

    def _forbidden(query, settings):
        raise AssertionError("retrieval ran after the deadline had passed")

    monkeypatch.setattr(orchestrator, "retrieve_statement_cards", _forbidden)

    fake = _FakeBedrock([_tool_turn("retrieve_campus_resources", {"query": "x"})])
    response = _run(monkeypatch, fake, deadline=499.0)

    assert fake.calls == 0, "an already-passed deadline must not start any model call"
    assert response.conversational_text


def test_an_absent_deadline_is_derived_from_config(monkeypatch, no_retrieval):
    """run_chat is callable without a deadline (tests, local runs); the budget then comes
    from settings rather than being unbounded."""
    settings = Settings(**{**_SETTINGS.__dict__, "converse_deadline_seconds": 0})
    fake = _FakeBedrock([_tool_turn("retrieve_campus_resources", {"query": "x"})])
    response = _run(monkeypatch, fake, settings=settings)
    assert fake.calls == 0, "a zero-second budget is already spent when the loop starts"
    assert response.conversational_text


def test_inference_config_comes_from_settings_not_literals(monkeypatch, no_retrieval):
    """Camp hardcoded maxTokens/temperature in the Converse call; they are config knobs
    here, and the stack wires them from config.yaml."""
    settings = Settings(
        **{**_SETTINGS.__dict__, "generation_max_tokens": 777, "generation_temperature": 0.9}
    )
    fake = _FakeBedrock([_text_turn("hi")])
    _run(monkeypatch, fake, settings=settings)
    assert fake.kwargs["inferenceConfig"] == {"maxTokens": 777, "temperature": 0.9}
    assert fake.kwargs["modelId"] == "us.anthropic.claude-sonnet-4-6"


def test_history_is_trimmed_server_side_to_the_configured_window(monkeypatch, no_retrieval):
    """The client is never trusted to cap its own history - it is a paid-token control."""
    settings = Settings(**{**_SETTINGS.__dict__, "max_history_messages": 2})
    fake = _FakeBedrock([_text_turn("hi")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    request = ChatRequest(
        query="and financial aid?",
        history=[
            {"role": "user", "text": "one"},
            {"role": "assistant", "text": "two"},
            {"role": "user", "text": "three"},
            {"role": "assistant", "text": "four"},
        ],
    )
    orchestrator.run_chat(request, settings)

    # 2 trimmed history turns + the current user message.
    sent = str(fake.kwargs["messages"])
    assert len(fake.kwargs["messages"]) == 3
    assert "one" not in sent and "two" not in sent, "older turns must be dropped"
    assert "three" in sent and "four" in sent, "the window's turns must survive"


def test_the_response_serialises_to_camps_camelcase_wire_contract(monkeypatch, no_retrieval):
    """Camp's own frontend arrives at a later bullet and reads these exact keys, so a
    rename here is a silent break."""
    fake = _FakeBedrock(
        [_tool_turn("submit_chat_response", {"conversationalText": "Here you go.", "cards": []})]
    )
    response = _run(monkeypatch, fake)
    body = response.model_dump(by_alias=True)
    assert body["conversationalText"] == "Here you go."
    assert "statementBatches" in body
    assert "safetyHandoff" in body
    assert "talkToPersonAvailable" in body

"""Model-triage safety: the model emits resource keys, this module owns every digit.

The resolution tests are the load-bearing ones. Failure direction is always toward showing
help: unknown keys drop with a WARNING, a tag with nothing valid resolves to the default
crisis set, and prose that cites a hotline without the tag still gets the panel attached.
There is no pre-model phrase gate any more (decision, 2026-08-10) - the pipeline test at
the bottom pins that a crisis message flows through the guardrail into the loop.
"""

import json

import pytest

import handler
import safety
from models import ChatResponse


def _plain_response(text):
    return ChatResponse(
        conversationalText=text,
        statementBatches=None,
        talkToPersonAvailable=True,
    )


# --- key resolution ----------------------------------------------------------------------


def test_every_key_the_prompt_teaches_resolves():
    """The prompt roster and the resolver read the same table, so a taught key can never
    miss. This test is what makes that claim structural rather than aspirational."""
    roster = safety.safety_roster_for_prompt()
    assert roster, "the prompt roster must not be empty"
    for key, when in roster:
        handoff = safety.resolve_safety_handoff((key,))
        assert [c.id for c in handoff.contacts] == [key]
        assert when


def test_keys_resolve_in_emitted_order_and_dedupe():
    handoff = safety.resolve_safety_handoff(("sas", "crisis-988", "SAS", "crisis-988"))
    assert [c.id for c in handoff.contacts] == ["sas", "crisis-988"]


def test_unknown_keys_drop_with_a_warning_and_valid_ones_survive(caplog):
    with caplog.at_level("WARNING"):
        handoff = safety.resolve_safety_handoff(("counseling-center", "caps"))
    assert [c.id for c in handoff.contacts] == ["caps"]
    assert "counseling-center" in caplog.text


def test_nothing_valid_resolves_to_the_default_crisis_set():
    """Never an empty panel: a model that emitted garbage keys still shows the student the
    standard crisis contacts."""
    handoff = safety.resolve_safety_handoff(("bogus", ""))
    assert [c.id for c in handoff.contacts] == list(safety.DEFAULT_SAFETY_KEYS)


def test_a_bare_tag_resolves_to_the_default_crisis_set():
    handoff = safety.resolve_safety_handoff(())
    assert [c.id for c in handoff.contacts] == list(safety.DEFAULT_SAFETY_KEYS)


def test_every_contact_is_authored_in_the_table_never_by_the_model():
    """Keys are the model's entire vocabulary. Each resolves to a fixed contact whose id
    matches its key and whose href is real, so an invented number has no path to the
    screen - the same construction as the card ref contract."""
    for key, resource in safety.SAFETY_RESOURCES.items():
        assert resource.contact.id == key
        assert resource.contact.href
    for key in safety.DEFAULT_SAFETY_KEYS:
        assert key in safety.SAFETY_RESOURCES


# --- attaching the panel to a response ---------------------------------------------------


def test_emitted_keys_choose_the_panel_and_drop_the_cards():
    result = safety.apply_safety_handoff_to_response(
        _plain_response("You deserve support right now."),
        conversational_text="You deserve support right now.",
        safety_keys=("sas",),
    )
    assert [c.id for c in result.safety_handoff.contacts] == ["sas"]
    assert result.statement_batches is None


def test_the_output_scanner_attaches_the_default_panel_when_prose_cites_a_hotline():
    """The second net: a model that names a crisis line in prose but forgets the tag still
    produces the official panel, never a bare mention of 988 with nothing tappable."""
    result = safety.apply_safety_handoff_to_response(
        _plain_response("Please call 988 for support."),
        conversational_text="Please call 988 for support.",
        safety_keys=None,
    )
    assert result.safety_handoff is not None
    assert [c.id for c in result.safety_handoff.contacts] == list(safety.DEFAULT_SAFETY_KEYS)
    assert result.statement_batches is None


def test_plain_answers_pass_through_untouched():
    result = safety.apply_safety_handoff_to_response(
        _plain_response("The tutoring office is in SSC 600."),
        conversational_text="The tutoring office is in SSC 600.",
        safety_keys=None,
    )
    assert result.safety_handoff is None


# --- the pipeline: no pre-model gate -----------------------------------------------------


def test_a_crisis_message_flows_through_the_guardrail_into_the_loop(monkeypatch):
    """Triage belongs to the model now (decision, 2026-08-10). A crisis phrase is not
    short-circuited before the loop: the guardrail screens it and the loop answers it,
    with the safety panel coming out of the model's own <safety> block."""

    class _PassingGuardrail:
        def __init__(self):
            self.calls = 0

        def apply_guardrail(self, **kwargs):
            self.calls += 1
            return {"action": "NONE", "outputs": []}

    guardrail = _PassingGuardrail()
    monkeypatch.setattr(handler, "_bedrock_client", lambda: guardrail)

    loop_queries = []

    def _fake_loop(*args, **kwargs):
        request = next(
            (a for a in list(args) + list(kwargs.values()) if hasattr(a, "query")), None
        )
        loop_queries.append(getattr(request, "query", None))
        return safety.apply_safety_handoff_to_response(
            _plain_response("You're not alone."),
            conversational_text="You're not alone.",
            safety_keys=("crisis-988",),
        )

    monkeypatch.setattr(handler, "run_chat", _fake_loop)

    response = handler.lambda_handler(
        {"body": json.dumps({"query": "I want to kill myself"})}, None
    )
    body = json.loads(response["body"])

    assert guardrail.calls == 1, "the guardrail screens every message now"
    assert loop_queries == ["I want to kill myself"], "the loop answers crisis messages"
    assert body["safetyHandoff"] is not None
    assert [c["id"] for c in body["safetyHandoff"]["contacts"]] == ["crisis-988"]

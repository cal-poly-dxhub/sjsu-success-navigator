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
from conftest import chat_event
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


def test_the_panel_takes_a_location_card_with_it():
    """THE OTHER ROUTE INTO A SAFETY TURN, which is the one that can actually carry a map:
    the model wrote an ordinary reply, named a place, and mentioned a crisis line in prose
    without tagging the turn. The panel gets bolted on here, and it takes everything else
    with it - a map and a walking route are an errand, and this turn is a phone call."""
    from models import PlaceCard

    response = _plain_response("Please call 988. The Wellness Center is open too.")
    response.place = PlaceCard(
        key="student-wellness-center",
        name="Student Wellness Center",
        address="Across from the Event Center",
        directionsUrl="https://www.google.com/maps/dir/?api=1&destination=x",
    )

    result = safety.apply_safety_handoff_to_response(
        response,
        conversational_text=response.conversational_text,
        safety_keys=None,
    )

    assert result.safety_handoff is not None
    assert result.place is None


def test_plain_answers_pass_through_untouched():
    result = safety.apply_safety_handoff_to_response(
        _plain_response("The tutoring office is in SSC 600."),
        conversational_text="The tutoring office is in SSC 600.",
        safety_keys=None,
    )
    assert result.safety_handoff is None


# --- the pipeline: no pre-model gate -----------------------------------------------------


def test_a_crisis_message_flows_through_the_guardrail_into_the_loop(monkeypatch, store):
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
        chat_event({"query": "I want to kill myself"}), None
    )
    body = json.loads(response["body"])

    assert guardrail.calls == 1, "the guardrail screens every message now"
    assert store.call_names == ["append", "read", "append"], (
        "a disclosure is on record before the model is called, and the reply after it"
    )
    assert loop_queries == ["I want to kill myself"], "the loop answers crisis messages"
    assert body["safetyHandoff"] is not None
    assert [c["id"] for c in body["safetyHandoff"]["contacts"]] == ["crisis-988"]


# --- the table is data/contacts.csv, and a bad row stops the process ------------------------
#
# The resolver tests above run against the committed file. These run against a written-out one,
# because what needs pinning is the failure: this table is where a crisis phone number lives,
# and every one of these malformations would otherwise produce a panel that renders, looks
# right, and is missing a number.


@pytest.fixture
def contacts(tmp_path, monkeypatch):
    """Write a contacts.csv and make it the only file safety.py can find."""
    import campus_data

    monkeypatch.setattr(campus_data, "_DATA_DIRS", (tmp_path,))

    def _write(*rows):
        (tmp_path / "contacts.csv").write_text(
            "kind,id,label,detail,href,when,in_default_panel,note\n" + "".join(f"{r}\n" for r in rows),
            encoding="utf-8",
        )

    return _write


def test_the_committed_table_is_the_one_the_module_loaded():
    """The tables are read from data/contacts.csv at import, not written out in the module.
    This is the assertion that the FILE is what the resolver answers from - the rest of this
    suite would pass just as well against a hardcoded dict."""
    from campus_data import load_rows

    rows = [
        row
        for row in load_rows(
            "contacts.csv", ("kind", "id"), optional=("label", "detail", "href", "when", "in_default_panel", "note")
        )
        if row["kind"] == "safety"
    ]
    assert [row["id"] for row in rows] == list(safety.SAFETY_RESOURCES)
    for row in rows:
        contact = safety.SAFETY_RESOURCES[row["id"]].contact
        assert (contact.label, contact.detail, contact.href) == (
            row["label"],
            row["detail"],
            row["href"],
        )


def test_the_default_panel_is_the_files_own_order():
    """DEFAULT_SAFETY_KEYS is `in_default_panel` read down the file, not a second list. A
    second list is the thing that drifts, and the drift would be a panel whose order nobody
    chose."""
    from campus_data import load_rows

    expected = tuple(
        row["id"]
        for row in load_rows(
            "contacts.csv", ("kind", "id"), optional=("label", "detail", "href", "when", "in_default_panel", "note")
        )
        if row["kind"] == "safety" and row["in_default_panel"] == "yes"
    )
    assert safety.DEFAULT_SAFETY_KEYS == expected
    assert expected, "the committed file must mark a default panel"


def test_a_file_with_no_safety_rows_raises(contacts):
    """A file that loaded quietly with its safety rows gone is a crisis panel with nothing on
    it. Cares rows alone are not a table."""
    from campus_data import CampusDataError

    contacts("cares,sjsu-cares-phone,Phone,408.924.1234,,,no,")
    with pytest.raises(CampusDataError, match="carries no `safety` rows"):
        safety._load_safety_table()


def test_safety_rows_with_nothing_marked_for_the_default_panel_raise(contacts):
    """The default panel is what a student gets when the model tags an emergency and names no
    resource. Empty is a handoff with no numbers on it, so it is fatal rather than a fallback
    to the first row."""
    from campus_data import CampusDataError

    contacts("safety,caps,CAPS,Counseling,https://example.org/,soon,no,")
    with pytest.raises(CampusDataError, match="in_default_panel"):
        safety._load_safety_table()


@pytest.mark.parametrize(
    "row",
    [
        "safety,caps,,Counseling,https://example.org/,soon,yes,",  # no label
        "safety,caps,CAPS,,https://example.org/,soon,yes,",  # no detail
        "safety,caps,CAPS,Counseling,,soon,yes,",  # nowhere to go
    ],
)
def test_a_safety_row_missing_any_of_its_three_cells_raises(contacts, row):
    """Every safety row becomes a button on the crisis panel. A blank label is a button with
    no words on it and a blank href is one that goes nowhere - and the OTHER kinds in this
    file leave these cells empty legitimately, so the check belongs here rather than in the
    file reader."""
    from campus_data import CampusDataError

    contacts(row)
    with pytest.raises(CampusDataError, match="crisis panel"):
        safety._load_safety_table()


def test_two_safety_rows_with_one_id_raise(contacts):
    """The second would quietly win, and the panel would show whichever came last."""
    from campus_data import CampusDataError

    contacts(
        "safety,caps,CAPS,Counseling,https://example.org/a,soon,yes,",
        "safety,caps,CAPS,Counseling,https://example.org/b,soon,no,",
    )
    with pytest.raises(CampusDataError, match="share the id"):
        safety._load_safety_table()


def test_an_empty_when_keeps_a_row_resolvable_without_offering_it(contacts):
    """The shape crisis-page uses: in the default set, never a triage choice the model makes.
    An empty cell has to mean that rather than failing or becoming an empty roster line."""
    contacts(
        "safety,crisis-988,Call 988,Lifeline,https://988lifeline.org/,thoughts of self-harm,yes,",
        "safety,crisis-page,Crisis page,Guidance,https://example.org/,,yes,",
    )
    resources, defaults = safety._load_safety_table()
    assert resources["crisis-page"].when is None
    assert defaults == ("crisis-988", "crisis-page")
    assert [key for key, _ in [(k, r) for k, r in resources.items() if r.when]] == ["crisis-988"]

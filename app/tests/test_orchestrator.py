"""The Converse loop's two caps, and the wire contract camp's frontend depends on.

Nothing here reaches Bedrock: every model call is a fake whose script the test controls.
The point is the loop's control flow (when it stops, what it returns), which is exactly
what cannot be checked against a real account anyway.
"""

import copy

import pytest

import orchestrator
from models import ChatRequest
from retrieve import RetrievedChunk
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


def _retrieval_turn(query="tutoring", tool_use_id="tu-1"):
    return _tool_turn("retrieve_campus_resources", {"query": query}, tool_use_id)


@pytest.fixture
def no_retrieval(monkeypatch):
    """Retrieval returns nothing, so tests exercise loop control flow rather than cards."""
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])


def _run(monkeypatch, fake, deadline=None, settings=_SETTINGS):
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
    return orchestrator.run_chat(
        ChatRequest(query="where do I get tutoring?"), settings, deadline=deadline
    )


def test_the_models_text_reply_ends_the_loop(monkeypatch, no_retrieval):
    """There is no submit tool any more. `end_turn` is the only way a turn ends, and the
    text of that turn IS the answer - which is what removed the second exit path and the
    mechanical card builder behind it."""
    fake = _FakeBedrock([_text_turn("Try Peer Connections.")])
    response = _run(monkeypatch, fake)
    assert fake.calls == 1
    assert response.conversational_text == "Try Peer Connections."
    assert response.statement_batches is None


def test_submit_chat_response_is_no_longer_offered_as_a_tool(monkeypatch, no_retrieval):
    """Its removal is the substance of this change, not a side effect: while it existed the
    model typed its own sourceUrl, so an invented URL was something to detect after the fact
    rather than something it had no way to express."""
    fake = _FakeBedrock([_text_turn("hi")])
    _run(monkeypatch, fake)

    tool_names = {tool["toolSpec"]["name"] for tool in fake.kwargs["toolConfig"]["tools"]}
    assert tool_names == {"retrieve_campus_resources"}


def test_a_retrieved_source_becomes_a_card_the_model_wrote(monkeypatch):
    """The end-to-end contract: retrieve, hand the model numbered sources, take back tagged
    text, resolve the ref to the URL the server itself holds."""
    monkeypatch.setattr(
        orchestrator,
        "retrieve_chunks",
        lambda query, settings: [
            RetrievedChunk(
                text="Drop-in tutoring for math.",
                score=0.9,
                source_url="https://www.sjsu.edu/tutoring/index.php",
                title="Peer Connections",
                section="tutoring-academic-support",
                s3_uri=None,
            )
        ],
    )

    fake = _FakeBedrock(
        [
            _retrieval_turn(),
            _text_turn(
                'Tutoring is free.\n\n<card ref="1">'
                "<title>Free math tutoring</title>"
                "<desc>Drop-in help for lower-division math.</desc>"
                "<followup>How do I book a tutor?</followup></card>"
            ),
        ]
    )
    response = _run(monkeypatch, fake)

    card = response.statement_batches[0].cards[0]
    assert response.conversational_text == "Tutoring is free."
    assert card.title == "Free math tutoring"
    assert card.source_url == "https://www.sjsu.edu/tutoring/index.php"
    assert [action.type for action in card.actions] == ["source", "followup"]


def test_the_model_is_handed_numbered_sources_and_no_urls(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "retrieve_chunks",
        lambda query, settings: [
            RetrievedChunk(
                text="Drop-in tutoring for math.",
                score=0.9,
                source_url="https://www.sjsu.edu/tutoring/index.php",
                title="Peer Connections",
                section="tutoring",
                s3_uri=None,
            )
        ],
    )

    fake = _FakeBedrock([_retrieval_turn(), _text_turn("done")])
    _run(monkeypatch, fake)

    sent = str(fake.kwargs["messages"])
    assert '"id": 1' in sent
    assert "sjsu.edu" not in sent, "a URL in the transcript is a URL the model can copy"


def _tutoring_retrieval(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "retrieve_chunks",
        lambda query, settings: [
            RetrievedChunk(
                text="Drop-in tutoring for math.",
                score=0.9,
                source_url="https://www.sjsu.edu/tutoring/index.php",
                title="Peer Connections",
                section="tutoring-academic-support",
                s3_uri=None,
            )
        ],
    )


def test_prose_written_after_the_cards_reaches_the_wire_below_them(monkeypatch):
    """The acceptance case, end to end: a reply with prose on both sides of its cards
    arrives split the way it was written. `conversationalText` is what renders above the
    grid, `trailingText` what renders below, so the closing question stops appearing over
    the answer it is asking about."""
    _tutoring_retrieval(monkeypatch)

    fake = _FakeBedrock(
        [
            _retrieval_turn(),
            _text_turn(
                "Tutoring is free, and here is where to start.\n\n"
                '<card ref="1">'
                "<title>Free math tutoring</title>"
                "<desc>Drop-in help for lower-division math.</desc>"
                "<followup>How do I book a tutor?</followup></card>\n\n"
                "Want me to look at writing help too?"
            ),
        ]
    )
    response = _run(monkeypatch, fake)

    assert response.conversational_text == "Tutoring is free, and here is where to start."
    assert response.trailing_text == "Want me to look at writing help too?"
    assert [card.title for card in response.statement_batches[0].cards] == ["Free math tutoring"]

    body = response.model_dump(by_alias=True)
    assert body["trailingText"] == "Want me to look at writing help too?"


def test_a_reply_that_ends_with_its_cards_carries_no_trailing_text(monkeypatch):
    """The ordinary shape stays exactly as it was: one bubble, then the grid."""
    _tutoring_retrieval(monkeypatch)

    fake = _FakeBedrock(
        [
            _retrieval_turn(),
            _text_turn(
                'Tutoring is free.\n\n<card ref="1"><title>T</title><desc>D</desc></card>'
            ),
        ]
    )
    response = _run(monkeypatch, fake)

    assert response.conversational_text == "Tutoring is free."
    assert response.trailing_text is None


def test_a_reply_whose_prose_is_all_below_the_cards_keeps_it_there(monkeypatch):
    """Nothing promotes the prose back above the grid to fill the empty bubble. The model
    put it under the cards, so that is where it goes, and the no-output fallback must not
    fire on a turn that plainly has output."""
    _tutoring_retrieval(monkeypatch)

    fake = _FakeBedrock(
        [
            _retrieval_turn(),
            _text_turn(
                '<card ref="1"><title>T</title><desc>D</desc></card>\n\nThat is the one I would try.'
            ),
        ]
    )
    response = _run(monkeypatch, fake)

    assert response.conversational_text == ""
    assert response.trailing_text == "That is the one I would try."
    assert orchestrator._NO_OUTPUT_TEXT not in (response.conversational_text or "")


def test_a_safety_turn_is_one_bubble_above_the_panel(monkeypatch, no_retrieval):
    """The panel's placement is a safety property. A safety turn drops its cards, so prose
    the model wrote under them has nothing left to sit below - and half a message rendering
    beneath the contact panel is exactly the burying this must not do."""
    fake = _FakeBedrock(
        [
            _text_turn(
                "You're not alone.\n\n<safety>crisis-988</safety>\n\n"
                '<card ref="1"><title>T</title><desc>D</desc></card>\n\n'
                "I can help with the rest whenever you want."
            )
        ]
    )
    response = _run(monkeypatch, fake)

    assert response.safety_handoff is not None
    assert response.trailing_text is None
    assert response.statement_batches is None
    assert response.conversational_text == (
        "You're not alone.\n\nI can help with the rest whenever you want."
    )


def test_a_hotline_named_under_the_cards_still_attaches_the_panel(monkeypatch):
    """The output scanner reads the whole message, both sides of the split. A crisis line
    mentioned below the grid is as much a bare mention as one above it."""
    _tutoring_retrieval(monkeypatch)

    fake = _FakeBedrock(
        [
            _retrieval_turn(),
            _text_turn(
                'Here is the tutoring office.\n\n<card ref="1"><title>T</title><desc>D</desc></card>'
                "\n\nAnd if it gets heavier than coursework, call 988."
            ),
        ]
    )
    response = _run(monkeypatch, fake)

    assert response.safety_handoff is not None
    assert response.statement_batches is None
    assert response.trailing_text is None
    assert "call 988" in response.conversational_text


def test_an_unparseable_reply_becomes_one_bubble_with_the_tags_stripped(monkeypatch, no_retrieval):
    """The regression that must never come back. v1 answered a broken reply with cards built
    out of retrieved page text - confident referrals nobody had written."""
    fake = _FakeBedrock(
        [_text_turn('Here is what I found.\n\n<card ref="1"><title>Tutoring</title><desc>Free help.')]
    )
    response = _run(monkeypatch, fake)

    assert response.statement_batches is None
    assert "<card" not in response.conversational_text
    assert "Here is what I found." in response.conversational_text
    assert "Free help." in response.conversational_text, "content survives the markup"


def test_a_safety_tag_drops_the_cards_and_attaches_the_fixed_panel(monkeypatch, no_retrieval):
    fake = _FakeBedrock(
        [
            _text_turn(
                "<safety/>\n\nPlease reach out to someone below.\n\n"
                '<card ref="1"><title>T</title><desc>D</desc></card>'
            )
        ]
    )
    response = _run(monkeypatch, fake)

    assert response.safety_handoff is not None
    assert response.statement_batches is None
    assert "<safety" not in response.conversational_text
    assert "T</title>" not in response.conversational_text


def test_the_loop_stops_at_the_iteration_cap(monkeypatch, no_retrieval, caplog):
    """A model that never submits must not loop forever. Camp fell through silently;
    the cap is config here and the exhaustion is logged."""
    settings = Settings(**{**_SETTINGS.__dict__, "max_converse_iterations": 3})
    fake = _FakeBedrock([_retrieval_turn()])

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
    """v1's fallback made a fresh retrieval call on the way out, which is the exact overrun
    the deadline exists to prevent. There is no such path now - the loop answers from the
    text it already has - and this pins that no network call happens on the way out."""
    clock = {"now": 500.0}
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: clock["now"])

    def _forbidden(query, settings):
        raise AssertionError("retrieval ran after the deadline had passed")

    monkeypatch.setattr(orchestrator, "retrieve_chunks", _forbidden)

    fake = _FakeBedrock([_retrieval_turn("x")])
    response = _run(monkeypatch, fake, deadline=499.0)

    assert fake.calls == 0, "an already-passed deadline must not start any model call"
    assert response.conversational_text
    assert response.statement_batches is None, "no cards may be invented on the way out"


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


def test_a_followup_click_sends_the_model_the_same_turn_as_typed_input(monkeypatch, no_retrieval):
    """The bug this pins: `followup: true` used to append "emit no cards unless they clearly
    changed topic" to the user message, so clicking Tell me more could not produce cards while
    typing the same words could. The flag stays on the wire contract; it must not change a
    single byte of what the model is sent. Compared as whole message lists, history included,
    because the suppression note lived inside the last one."""
    history = [
        {"role": "user", "text": "where do I get tutoring?"},
        {"role": "assistant", "text": "Peer Connections runs drop-in tutoring."},
    ]
    query = "How do I book a calculus tutor at Peer Connections?"

    sent = []
    for followup in (False, True):
        fake = _FakeBedrock([_text_turn("Here you go.")])
        monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
        orchestrator.run_chat(
            ChatRequest(query=query, followup=followup, history=history), _SETTINGS
        )
        sent.append(fake.kwargs["messages"])

    typed, clicked = sent
    assert clicked == typed
    assert "follow-up" not in str(clicked) and "no cards" not in str(clicked)


def test_the_response_serialises_to_camps_camelcase_wire_contract(monkeypatch, no_retrieval):
    """Camp's own frontend arrives at a later bullet and reads these exact keys, so a
    rename here is a silent break."""
    fake = _FakeBedrock([_text_turn("Here you go.")])
    response = _run(monkeypatch, fake)
    body = response.model_dump(by_alias=True)
    assert body["conversationalText"] == "Here you go."
    assert "trailingText" in body
    assert "statementBatches" in body
    assert "safetyHandoff" in body
    assert "talkToPersonAvailable" in body

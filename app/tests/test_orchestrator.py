"""The Converse loop's two caps, and the wire contract the frontend depends on."""

import copy
from datetime import datetime, timezone

import pytest

import orchestrator
from conftest import stored
from models import ChatRequest
from retrieve import RetrievedChunk
from settings import Settings
from usage import TurnUsage

_SETTINGS = Settings(
    knowledge_base_id="KB123",
    generation_model_id="us.anthropic.claude-sonnet-4-6",
    title_model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    bedrock_region="us-west-2",
    input_guardrail_id="gr-1",
    input_guardrail_version="3",
    chat_history_table_name="chat-history-test",
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
        # Deep-copied: the loop appends the assistant turn to the same list object.
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
    """There is no submit tool any more: `end_turn` is the only way a turn ends."""
    fake = _FakeBedrock([_text_turn("Try Peer Connections.")])
    response = _run(monkeypatch, fake)
    assert fake.calls == 1
    assert response.conversational_text == "Try Peer Connections."
    assert response.statement_batches is None


def test_submit_chat_response_is_no_longer_offered_as_a_tool(monkeypatch, no_retrieval):
    """Its removal is the substance of the change: under it the model typed its own URLs."""
    fake = _FakeBedrock([_text_turn("hi")])
    _run(monkeypatch, fake)

    tool_names = {tool["toolSpec"]["name"] for tool in fake.kwargs["toolConfig"]["tools"]}
    assert tool_names == {"retrieve_campus_resources"}


def test_a_retrieved_source_becomes_a_card_the_model_wrote(monkeypatch):
    """The end-to-end contract: retrieve, hand over numbered sources, take back tagged cards."""
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
    """Two fields, so the closing question renders under the answer rather than over it."""
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
    """Nothing promotes the prose back above the grid to fill the empty bubble."""
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
    """The panel's placement is a safety property, so a safety turn is one bubble above it."""
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
    """The output scanner reads the whole message, both sides of the split."""
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
    """No cards built out of retrieved page text: a broken reply is prose."""
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
    """A model that never submits must not loop forever, and the exit is logged."""
    settings = Settings(**{**_SETTINGS.__dict__, "max_converse_iterations": 3})
    fake = _FakeBedrock([_retrieval_turn()])

    with caplog.at_level("WARNING"):
        response = _run(monkeypatch, fake, settings=settings)

    assert fake.calls == 3, "the loop must stop at exactly max_converse_iterations"
    assert "3-iteration cap" in caplog.text
    assert response.conversational_text


def test_the_loop_stops_at_the_wall_clock_deadline(monkeypatch, no_retrieval, caplog):
    """The cap that actually bounds a request: six slow iterations outlast the function."""
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
    """A fresh retrieval on the way out is the exact overrun the deadline exists to prevent."""
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
    """run_chat is callable without a deadline; the budget then comes from Settings."""
    settings = Settings(**{**_SETTINGS.__dict__, "converse_deadline_seconds": 0})
    fake = _FakeBedrock([_tool_turn("retrieve_campus_resources", {"query": "x"})])
    response = _run(monkeypatch, fake, settings=settings)
    assert fake.calls == 0, "a zero-second budget is already spent when the loop starts"
    assert response.conversational_text


def test_inference_config_comes_from_settings_not_literals(monkeypatch, no_retrieval):
    """maxTokens and temperature are config knobs, not literals in the Converse call."""
    settings = Settings(
        **{**_SETTINGS.__dict__, "generation_max_tokens": 777, "generation_temperature": 0.9}
    )
    fake = _FakeBedrock([_text_turn("hi")])
    _run(monkeypatch, fake, settings=settings)
    assert fake.kwargs["inferenceConfig"] == {"maxTokens": 777, "temperature": 0.9}
    assert fake.kwargs["modelId"] == "us.anthropic.claude-sonnet-4-6"


def test_history_is_trimmed_to_the_configured_window(monkeypatch, no_retrieval):
    """The window is applied at the query's Limit too, so this makes the function total."""
    settings = Settings(**{**_SETTINGS.__dict__, "max_history_messages": 2})
    fake = _FakeBedrock([_text_turn("hi")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(
        ChatRequest(query="and financial aid?"),
        settings,
        history=[
            stored("user", "one"),
            stored("assistant", "two"),
            stored("user", "three"),
            stored("assistant", "four"),
        ],
    )

    # 2 trimmed history turns + the current user message + the primed search exchange.
    sent = str(fake.kwargs["messages"])
    assert len(fake.kwargs["messages"]) == 5
    assert "one" not in sent and "two" not in sent, "older turns must be dropped"
    assert "three" in sent and "four" in sent, "the window's turns must survive"


def test_the_model_is_never_handed_back_its_own_markup(monkeypatch, no_retrieval):
    """A copied <safety> would be a panel by imitation, so the tags come off first."""
    fake = _FakeBedrock([_text_turn("hi")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(
        ChatRequest(query="and financial aid?"),
        _SETTINGS,
        history=[
            stored("user", "where is tutoring?"),
            stored(
                "assistant",
                'Two places can help.\n\n<card ref="1"><title>Peer Connections</title>'
                "<desc>Drop-in tutoring.</desc></card>\n\n"
                "<safety></safety>Which one?",
            ),
        ],
    )

    sent = str(fake.kwargs["messages"])
    assert "<card" not in sent and "<safety" not in sent and "<title" not in sent
    # The words survive: only the markup goes, on both sides of the card group.
    assert "Two places can help." in sent
    assert "Which one?" in sent


def test_a_students_own_message_is_never_stripped_on_its_way_to_the_model(
    monkeypatch, no_retrieval
):
    """Only the assistant's side is markup this server wrote the contract for."""
    fake = _FakeBedrock([_text_turn("hi")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(
        ChatRequest(query="and now?"),
        _SETTINGS,
        history=[stored("user", "my prof said to write <card> on the form?")],
    )

    assert "<card>" in str(fake.kwargs["messages"])


def test_a_followup_click_sends_the_model_the_same_turn_as_typed_input(monkeypatch, no_retrieval):
    """A clicked question and the same question typed by hand reach the model identically."""
    moment = datetime(2026, 8, 12, 3, 14, tzinfo=timezone.utc)
    history = [
        stored("user", "where do I get tutoring?"),
        stored("assistant", "Peer Connections runs drop-in tutoring."),
    ]
    query = "How do I book a calculus tutor at Peer Connections?"

    sent = []
    for followup in (False, True):
        fake = _FakeBedrock([_text_turn("Here you go.")])
        monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
        orchestrator.run_chat(
            ChatRequest(query=query, followup=followup),
            _SETTINGS,
            history=history,
            now=moment,
        )
        sent.append(fake.kwargs["messages"])

    typed, clicked = sent
    assert clicked == typed
    assert "follow-up" not in str(clicked) and "no cards" not in str(clicked)


def _roles(messages):
    return [message["role"] for message in messages]


def test_a_history_ending_in_a_user_turn_does_not_break_alternation(monkeypatch, no_retrieval):
    """A failed turn leaves a user message with no reply, and Converse rejects two in a row."""
    fake = _FakeBedrock([_text_turn("Here's what I can tell you.")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(
        ChatRequest(query="are you there?"),
        _SETTINGS,
        history=[
            stored("user", "my roommate is threatening me"),
            stored("assistant", "That sounds frightening."),
            stored("user", "it happened again last night"),
        ],
    )

    messages = fake.kwargs["messages"]
    assert _roles(messages) == ["user", "assistant", "user", "assistant", "user"]
    sent = str(messages)
    assert "it happened again last night" in sent, "the unanswered message is not dropped"
    assert "are you there?" in sent


def test_a_window_that_opens_on_an_assistant_turn_drops_it(monkeypatch, no_retrieval):
    """Converse also requires the first message to be a user turn."""
    fake = _FakeBedrock([_text_turn("ok")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(
        ChatRequest(query="and the deadline?"),
        _SETTINGS,
        history=[stored("assistant", "half of an answer"), stored("user", "thanks")],
    )

    messages = fake.kwargs["messages"]
    assert _roles(messages) == ["user", "assistant", "user"]
    assert "half of an answer" not in str(messages)


def test_consecutive_stored_messages_of_one_role_are_merged(monkeypatch, no_retrieval):
    """Two failed turns in a row still have to compose into one alternating transcript."""
    fake = _FakeBedrock([_text_turn("ok")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(
        ChatRequest(query="hello?"),
        _SETTINGS,
        history=[
            stored("user", "first try"),
            stored("user", "second try"),
        ],
    )

    messages = fake.kwargs["messages"]
    assert _roles(messages) == ["user", "assistant", "user"]
    assert "first try" in messages[0]["content"][0]["text"]
    assert "second try" in messages[0]["content"][0]["text"]


def test_the_first_search_is_primed_before_the_model_speaks(monkeypatch):
    """It lands as a completed tool exchange, so the common case is one Converse call."""
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
    fake = _FakeBedrock(
        [
            _text_turn(
                'Tutoring is free.\n\n<card ref="1"><title>Free tutoring</title>'
                "<desc>Drop-in help, no appointment.</desc></card>"
            )
        ]
    )
    response = _run(monkeypatch, fake)

    assert fake.calls == 1, "primed results must let the model answer in one call"
    primed_use, primed_result = fake.kwargs["messages"][-2:]
    tool_use = primed_use["content"][0]["toolUse"]
    assert primed_use["role"] == "assistant"
    assert tool_use["name"] == "retrieve_campus_resources"
    assert tool_use["input"] == {"query": "where do I get tutoring?"}, (
        "priming searches the student's own words"
    )
    assert '"id": 1' in str(primed_result), "primed sources are numbered like any others"
    card = response.statement_batches[0].cards[0]
    assert card.source_url == "https://www.sjsu.edu/tutoring/index.php"


def test_an_empty_primed_search_is_still_shown_to_the_model(monkeypatch, no_retrieval):
    """Zero results are appended, not skipped: a search that found nothing is the signal."""
    fake = _FakeBedrock([_text_turn("I don't have a page for that.")])
    _run(monkeypatch, fake)

    primed_result = fake.kwargs["messages"][-1]
    assert '"resultCount": 0' in str(primed_result)


def test_a_priming_failure_degrades_to_the_model_searching_itself(monkeypatch, caplog):
    """Retrieval being down must not fail the turn; the model then searches itself."""

    def _broken(query, settings):
        raise RuntimeError("bedrock unavailable")

    monkeypatch.setattr(orchestrator, "retrieve_chunks", _broken)
    fake = _FakeBedrock([_text_turn("hi")])

    with caplog.at_level("WARNING"):
        response = _run(monkeypatch, fake)

    assert "Primed retrieval failed" in caplog.text
    assert response.conversational_text == "hi"
    assert not any("toolUse" in str(m) for m in fake.kwargs["messages"]), (
        "no synthetic exchange may claim a search that never ran"
    )


def test_the_response_serialises_to_camps_camelcase_wire_contract(monkeypatch, no_retrieval):
    """The frontend reads these exact keys, so a rename here is a break."""
    fake = _FakeBedrock([_text_turn("Here you go.")])
    response = _run(monkeypatch, fake)
    body = response.model_dump(by_alias=True)
    assert body["conversationalText"] == "Here you go."
    assert "trailingText" in body
    assert "statementBatches" in body
    assert "safetyHandoff" in body
    assert "talkToPersonAvailable" in body


def _billed(turn, input_tokens, output_tokens):
    """A Converse response carrying the `usage` block Bedrock reports on every call."""
    return {**turn, "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens}}


def test_the_tally_counts_every_model_call_and_sums_its_tokens(monkeypatch, no_retrieval):
    """Two calls, because the model searched again: exactly the case an average cannot price."""
    usage = TurnUsage()
    fake = _FakeBedrock(
        [
            _billed(_retrieval_turn("tutoring hours"), 6000, 40),
            _billed(_text_turn("Peer Connections has drop-in hours."), 6800, 210),
        ]
    )
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
    orchestrator.run_chat(
        ChatRequest(query="where do I get tutoring?"), _SETTINGS, usage=usage
    )

    assert usage.model_calls == 2
    assert usage.input_tokens == 12800
    assert usage.output_tokens == 250
    # The primed search plus the one the model ran itself.
    assert usage.retrievals == 2


def test_a_call_with_no_usage_block_is_still_a_billed_call(monkeypatch, no_retrieval):
    """Defensive on purpose: the invocation happened, so it counts as a call."""
    usage = TurnUsage()
    fake = _FakeBedrock([_text_turn("hi")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
    orchestrator.run_chat(ChatRequest(query="hi"), _SETTINGS, usage=usage)

    assert usage.model_calls == 1
    assert usage.input_tokens == 0


def test_the_deadline_exit_still_reports_what_it_billed(monkeypatch, no_retrieval):
    """Why the tally is an argument and not a return value: every exit is under a cap."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: clock["now"])

    def burn_time():
        clock["now"] += 10.0

    usage = TurnUsage()
    fake = _FakeBedrock([_billed(_retrieval_turn(), 5000, 30)], on_call=burn_time)
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
    orchestrator.run_chat(
        ChatRequest(query="tutoring?"), _SETTINGS, deadline=1015.0, usage=usage
    )

    assert fake.calls == 2
    assert usage.model_calls == 2
    assert usage.input_tokens == 10000


def test_a_retrieval_that_failed_is_not_counted(monkeypatch):
    """A call that raised may or may not have been billed, and a meter must not guess."""

    def _broken(query, settings):
        raise RuntimeError("bedrock unavailable")

    monkeypatch.setattr(orchestrator, "retrieve_chunks", _broken)
    usage = TurnUsage()
    fake = _FakeBedrock([_text_turn("hi")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
    orchestrator.run_chat(ChatRequest(query="hi"), _SETTINGS, usage=usage)

    assert usage.retrievals == 0
    assert usage.model_calls == 1


def test_the_loop_runs_unchanged_without_a_tally(monkeypatch, no_retrieval):
    """`usage` is optional and nothing reads it back, so the turn is identical without it."""
    fake = _FakeBedrock([_text_turn("Here you go.")])
    response = _run(monkeypatch, fake)
    assert response.conversational_text == "Here you go."
    assert response.usage is None, "the loop never attaches one; the handler does"


class _RecordingSink:
    """A StreamSink that remembers what it was told, in order."""

    def __init__(self, on_text=None):
        self.texts = []
        self.stages = []
        self._on_text = on_text

    def status(self, stage):
        self.stages.append(stage)

    def text(self, accumulated):
        self.texts.append(accumulated)
        if self._on_text is not None:
            self._on_text()


class _FakeStreamingBedrock:
    """A ConverseStream stand-in. `script` is one list of stream events per call."""

    def __init__(self, script):
        self.script = script
        self.calls = 0

    def converse_stream(self, **kwargs):
        self.calls += 1
        self.kwargs = copy.deepcopy(kwargs)
        index = min(self.calls - 1, len(self.script) - 1)
        return {"stream": iter(self.script[index])}

    def converse(self, **kwargs):  # pragma: no cover (a streaming test must not call this)
        raise AssertionError("a streaming turn must not call Converse")


def _text_events(text, *, chunks=3, usage=None):
    """A ConverseStream event sequence for a plain text reply, split into deltas."""
    size = max(1, -(-len(text) // chunks))
    pieces = [text[i : i + size] for i in range(0, len(text), size)] or [""]
    events = [{"messageStart": {"role": "assistant"}}]
    events += [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": piece}}}
        for piece in pieces
    ]
    events.append({"contentBlockStop": {"contentBlockIndex": 0}})
    events.append({"messageStop": {"stopReason": "end_turn"}})
    events.append({"metadata": {"usage": usage or {"inputTokens": 900, "outputTokens": 120}}})
    return events


def _streaming_loop(monkeypatch, script, sink=None, **kwargs):
    fake = _FakeStreamingBedrock(script)
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
    response = orchestrator.run_chat(
        ChatRequest(query="where is the writing center?"),
        _SETTINGS,
        stream=sink if sink is not None else _RecordingSink(),
        **kwargs,
    )
    return response, fake


def test_a_streamed_turn_and_a_buffered_turn_produce_the_same_response(monkeypatch):
    """Same model text, same finished response: both transports exit through one parser."""
    reply = (
        "Two places can help.\n\n"
        '<card ref="1"><title>Writing Center</title><desc>Drop-in help with any '
        "assignment.</desc><followup>What are the writing center hours?</followup></card>\n\n"
        "Which one sounds closer to what you need?"
    )
    chunks = [
        RetrievedChunk(
            text="The Writing Center supports every student.",
            score=0.9,
            source_url="https://www.sjsu.edu/writingcenter/",
            title="Writing Center",
            section="academic-support",
            s3_uri="s3://bucket/writingcenter",
        )
    ]

    fake_buffered = _FakeBedrock([_text_turn(reply)])
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: chunks)
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake_buffered)
    buffered = orchestrator.run_chat(ChatRequest(query="writing help"), _SETTINGS)

    fake_streamed = _FakeStreamingBedrock([_text_events(reply, chunks=7)])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake_streamed)
    streamed = orchestrator.run_chat(
        ChatRequest(query="writing help"), _SETTINGS, stream=_RecordingSink()
    )

    assert streamed.model_dump(by_alias=True, exclude={"statement_batches"}) == buffered.model_dump(
        by_alias=True, exclude={"statement_batches"}
    )
    # The batches carry a minted id and a timestamp, so they are compared on content.
    streamed_cards = [c.model_dump(by_alias=True) for b in streamed.statement_batches for c in b.cards]
    buffered_cards = [c.model_dump(by_alias=True) for b in buffered.statement_batches for c in b.cards]
    assert streamed_cards == buffered_cards
    assert streamed_cards[0]["sourceUrl"] == "https://www.sjsu.edu/writingcenter/"


def test_the_sink_sees_the_whole_reply_so_far_and_never_a_fragment(monkeypatch):
    """The sink is handed the accumulated text every time, so it owns what is safe to show."""
    sink = _RecordingSink()
    _streaming_loop(monkeypatch, [_text_events("abcdefghi", chunks=3)], sink=sink)

    assert sink.texts == ["abc", "abcdef", "abcdefghi"]
    for earlier, later in zip(sink.texts, sink.texts[1:]):
        assert later.startswith(earlier), "the accumulated text must only ever grow"


def test_token_usage_comes_from_the_streams_own_metadata(monkeypatch):
    """Taken from the metadata event rather than recomputed, so both paths price the same."""
    usage = TurnUsage()
    _streaming_loop(
        monkeypatch,
        [_text_events("hello", usage={"inputTokens": 1234, "outputTokens": 56})],
        usage=usage,
    )

    assert usage.model_calls == 1
    assert usage.input_tokens == 1234
    assert usage.output_tokens == 56


def test_a_streamed_tool_call_reassembles_its_partial_json_arguments(monkeypatch):
    """Bedrock streams tool arguments as JSON fragments, none of which is valid alone."""
    tool_events = [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "tu-1", "name": "retrieve_campus_resources"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"que'}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": 'ry": "cap'}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": 'lan hours"}'}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 2}}},
    ]
    searched = []

    fake = _FakeStreamingBedrock([tool_events, _text_events("Here you go.")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    def _record(query, settings):
        searched.append(query)
        return []

    monkeypatch.setattr(orchestrator, "retrieve_chunks", _record)
    orchestrator.run_chat(ChatRequest(query="caplan"), _SETTINGS, stream=_RecordingSink())

    # The primed search on the student's own words, then the model's own sharper one.
    assert searched == ["caplan", "caplan hours"]


def test_a_tool_calls_arguments_never_reach_the_preview(monkeypatch):
    """What streams is prose: a toolUse block's input is addressed to the server."""
    tool_events = [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "tu-1", "name": "retrieve_campus_resources"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"query": "x"}'}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]
    sink = _RecordingSink()
    fake = _FakeStreamingBedrock([tool_events, _text_events("Answer.")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])

    orchestrator.run_chat(ChatRequest(query="x"), _SETTINGS, stream=sink)

    assert all("query" not in text for text in sink.texts), sink.texts
    # Only the second call's text streamed; the first call was the tool call.
    assert sink.texts[-1] == "Answer."
    assert all("Answer.".startswith(text) for text in sink.texts), sink.texts


def test_unparseable_tool_arguments_still_answer_the_turn(monkeypatch):
    """A raise here would lose a reply the model had already written."""
    broken = [
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "tu-1", "name": "retrieve_campus_resources"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"que'}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]
    searched = []
    fake = _FakeStreamingBedrock([broken, _text_events("Recovered.")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)
    monkeypatch.setattr(
        orchestrator, "retrieve_chunks", lambda query, settings: searched.append(query) or []
    )

    response = orchestrator.run_chat(ChatRequest(query="pantry"), _SETTINGS, stream=_RecordingSink())

    assert searched == ["pantry", "pantry"]
    assert response.conversational_text == "Recovered."


def test_a_status_event_is_pushed_while_retrieval_runs(monkeypatch):
    """Retrieval produces no text, so without this the socket goes silent."""
    sink = _RecordingSink()
    _streaming_loop(monkeypatch, [_text_events("Here.")], sink=sink)

    assert sink.stages == ["retrieving"]


def test_a_broken_sink_never_costs_the_answer(monkeypatch):
    """The sink pushes to a socket the student may have closed, and the turn is paid for."""

    def _explode():
        raise RuntimeError("the socket is gone")

    sink = _RecordingSink(on_text=_explode)
    response, _ = _streaming_loop(monkeypatch, [_text_events("Still answered.")], sink=sink)

    assert response.conversational_text == "Still answered."


def test_the_buffered_path_never_opens_a_stream(monkeypatch):
    """POST /chat is under a hard "keeps working unchanged" constraint."""

    class _StreamIsForbidden(_FakeBedrock):
        def converse_stream(self, **kwargs):  # pragma: no cover (the assertion)
            raise AssertionError("the buffered path must not call ConverseStream")

    fake = _StreamIsForbidden([_text_turn("Buffered.")])
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    response = orchestrator.run_chat(ChatRequest(query="hi"), _SETTINGS)

    assert response.conversational_text == "Buffered."
    assert "guardrailConfig" not in fake.kwargs, "nothing is attached to the buffered call"


def test_the_guardrail_reaches_the_model_call_only_when_asked_and_only_in_sync_mode(monkeypatch):
    """`async` releases text before it has been scanned, so no configuration produces it."""
    sink = _RecordingSink()
    _, fake = _streaming_loop(
        monkeypatch,
        [_text_events("Screened.")],
        sink=sink,
        guardrail_config={
            "guardrailIdentifier": "gr-1",
            "guardrailVersion": "3",
            "streamProcessingMode": "sync",
        },
    )

    assert fake.kwargs["guardrailConfig"]["streamProcessingMode"] == "sync"

    _, unscreened = _streaming_loop(monkeypatch, [_text_events("Plain.")])
    assert "guardrailConfig" not in unscreened.kwargs


# 03:14 UTC on a Wednesday is 8:14pm the previous Tuesday in San Jose.
_LAMBDA_UTC_INSTANT = datetime(2026, 8, 12, 3, 14, tzinfo=timezone.utc)
_CAMPUS_READING = "Tuesday, August 11, 2026 at 8:14 PM PDT (America/Los_Angeles)"


def _user_turn(messages):
    """The last user message: the turn the model is being asked to answer."""
    texts = [
        message["content"][0]["text"]
        for message in messages
        if message["role"] == "user" and "text" in message["content"][0]
    ]
    return texts[-1]


def test_the_model_is_told_the_campus_local_time(monkeypatch, no_retrieval):
    """The whole feature in one assertion: server-derived, campus-local, on this turn only."""
    fake = _FakeBedrock([_text_turn("Here you go.")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(
        ChatRequest(query="is the pantry open?"), _SETTINGS, now=_LAMBDA_UTC_INSTANT
    )

    sent = _user_turn(fake.kwargs["messages"])
    assert sent.startswith(f"Current date and time on campus: {_CAMPUS_READING}.")
    assert "3:14 AM" not in sent, "the UTC clock must not reach the model"


def test_every_model_call_in_the_turn_carries_the_same_time(monkeypatch):
    """One clock reading serves both calls, so a long turn does not drift."""
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])
    fake = _FakeBedrock([_retrieval_turn(), _text_turn("Found it.")])
    seen = []
    fake._on_call = lambda: seen.append(_user_turn(fake.kwargs["messages"]))
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(
        ChatRequest(query="pantry hours?"), _SETTINGS, now=_LAMBDA_UTC_INSTANT
    )

    assert fake.calls == 2
    assert len(seen) == 2 and seen[0] == seen[1]
    assert all(_CAMPUS_READING in text for text in seen)


def test_the_students_own_words_are_not_touched_by_the_stamp(monkeypatch, no_retrieval):
    """The line is a projection, not an edit: what the student typed reaches the model whole."""
    fake = _FakeBedrock([_text_turn("ok")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(
        ChatRequest(query="where is the food pantry?"), _SETTINGS, now=_LAMBDA_UTC_INSTANT
    )

    assert "Student message:\nwhere is the food pantry?" in _user_turn(fake.kwargs["messages"])


def test_earlier_turns_are_never_backfilled_with_a_timestamp(monkeypatch, no_retrieval):
    """Stored history is copied through untouched: a message from Tuesday is not restamped."""
    fake = _FakeBedrock([_text_turn("ok")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(
        ChatRequest(query="and financial aid?"),
        _SETTINGS,
        history=[stored("user", "where do I get tutoring?"), stored("assistant", "Peer Connections.")],
        now=_LAMBDA_UTC_INSTANT,
    )

    messages = fake.kwargs["messages"]
    assert messages[0]["content"][0]["text"] == "where do I get tutoring?"
    assert messages[1]["content"][0]["text"] == "Peer Connections."
    assert str(messages).count("Current date and time on campus") == 1


def test_a_turn_with_no_clock_still_reaches_the_model(monkeypatch, no_retrieval):
    """A runtime with no tz database costs the line and nothing else."""
    monkeypatch.setattr(orchestrator, "campus_context_line", lambda moment: "")
    fake = _FakeBedrock([_text_turn("ok")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(ChatRequest(query="hi"), _SETTINGS)

    assert _user_turn(fake.kwargs["messages"]) == "Student message:\nhi\n\nWrite your reply."


def test_an_absent_moment_is_read_from_the_clock(monkeypatch, no_retrieval):
    """Every caller passes None, so the default has to read the clock."""
    fake = _FakeBedrock([_text_turn("ok")])
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: fake)

    orchestrator.run_chat(ChatRequest(query="hi"), _SETTINGS)

    assert "Current date and time on campus:" in _user_turn(fake.kwargs["messages"])
    assert "(America/Los_Angeles)" in _user_turn(fake.kwargs["messages"])


_ESCALATION_SETTINGS = Settings(
    **{
        **_SETTINGS.__dict__,
        "escalation_recipient": "sjsucares@sjsu.edu",
        "escalation_subject": "A student would like to talk with someone",
    }
)


def test_a_tagged_turn_carries_an_assembled_draft(monkeypatch, no_retrieval):
    fake = _FakeBedrock(
        [
            _text_turn(
                "That one really needs a person.\n\n"
                "<escalate_to_human>Hi, I have a hold I cannot clear.</escalate_to_human>"
            )
        ]
    )

    response = _run(monkeypatch, fake, settings=_ESCALATION_SETTINGS)

    assert response.escalation is not None
    assert response.escalation.to == "sjsucares@sjsu.edu"
    assert response.escalation.body.startswith("Hi, I have a hold I cannot clear.")
    # The draft is not the bubble. Its prose left the message the student reads.
    assert response.conversational_text == "That one really needs a person."


def test_an_untagged_turn_carries_no_draft(monkeypatch, no_retrieval):
    fake = _FakeBedrock([_text_turn("Peer Connections runs drop-in tutoring.")])

    response = _run(monkeypatch, fake, settings=_ESCALATION_SETTINGS)

    assert response.escalation is None


def test_a_safety_turn_never_carries_an_offer(monkeypatch, no_retrieval):
    """A draft under the panel puts homework between the student and a number that answers."""
    fake = _FakeBedrock(
        [
            _text_turn(
                "<safety>crisis-988</safety>\n\nPlease reach someone below.\n\n"
                "<escalate_to_human>Hi, I could use some help.</escalate_to_human>"
            )
        ]
    )

    response = _run(monkeypatch, fake, settings=_ESCALATION_SETTINGS)

    assert response.safety_handoff is not None
    assert response.escalation is None
    assert "I could use some help" not in response.conversational_text


def test_a_crisis_line_in_the_prose_drops_the_offer_too(monkeypatch, no_retrieval):
    """The other way into a safety turn: no tag, but prose naming a crisis line."""
    fake = _FakeBedrock(
        [
            _text_turn(
                "If it gets heavier than coursework, call 988.\n\n"
                "<escalate_to_human>Hi, I could use some help.</escalate_to_human>"
            )
        ]
    )

    response = _run(monkeypatch, fake, settings=_ESCALATION_SETTINGS)

    assert response.safety_handoff is not None
    assert response.escalation is None


def test_no_recipient_configured_means_no_offer_however_the_model_tags_it(
    monkeypatch, no_retrieval
):
    """The deployment gate at the loop's own exit: no recipient, no offer."""
    fake = _FakeBedrock(
        [_text_turn("<escalate_to_human>Hi, I could use some help.</escalate_to_human>")]
    )

    response = _run(monkeypatch, fake)

    assert response.escalation is None


def test_a_streamed_turn_and_a_buffered_turn_agree_about_the_draft(monkeypatch):
    """The same parity the cards have, and for the same structural reason."""
    reply = (
        "That one needs a person.\n\n"
        "<escalate_to_human>Hi, I have a hold I cannot clear.</escalate_to_human>"
    )
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])

    monkeypatch.setattr(
        orchestrator, "_bedrock_client", lambda region: _FakeBedrock([_text_turn(reply)])
    )
    buffered = orchestrator.run_chat(ChatRequest(query="who can help?"), _ESCALATION_SETTINGS)

    monkeypatch.setattr(
        orchestrator,
        "_bedrock_client",
        lambda region: _FakeStreamingBedrock([_text_events(reply)]),
    )
    streamed = orchestrator.run_chat(
        ChatRequest(query="who can help?"), _ESCALATION_SETTINGS, stream=_RecordingSink()
    )

    assert buffered.escalation == streamed.escalation
    assert buffered.escalation is not None


def test_the_preview_never_shows_the_draft(monkeypatch):
    """A draft is not an answer, and it must not type itself out in the preview."""
    sink = _RecordingSink()
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])
    monkeypatch.setattr(
        orchestrator,
        "_bedrock_client",
        lambda region: _FakeStreamingBedrock(
            [
                _text_events(
                    "That one needs a person.\n\n"
                    "<escalate_to_human>Hi, I have a hold I cannot clear.</escalate_to_human>"
                )
            ]
        ),
    )

    orchestrator.run_chat(
        ChatRequest(query="who can help?"), _ESCALATION_SETTINGS, stream=sink
    )

    from cards import preview_safe_prefix

    for accumulated in sink.texts:
        assert "escalate_to_human" not in preview_safe_prefix(accumulated)
        assert "I have a hold" not in preview_safe_prefix(accumulated)


def test_a_reply_that_is_only_an_offer_still_says_something(monkeypatch, no_retrieval):
    """A turn that is one block and nothing else still needs a sentence above the draft."""
    fake = _FakeBedrock(
        [_text_turn("<escalate_to_human>Hi, I have a hold I cannot clear.</escalate_to_human>")]
    )

    response = _run(monkeypatch, fake, settings=_ESCALATION_SETTINGS)

    assert response.escalation is not None
    assert response.conversational_text == orchestrator.ESCALATION_FALLBACK_TEXT
    assert orchestrator._NO_OUTPUT_TEXT not in response.conversational_text


def test_four_blocks_back_to_back_are_still_one_offer(monkeypatch, no_retrieval):
    """The first is the offer, the rest are logged and dropped: one turn makes one offer."""
    fake = _FakeBedrock(
        [
            _text_turn(
                "Here is one.\n\n"
                + "".join(
                    f"<escalate_to_human>Draft number {n}.</escalate_to_human>\n\n"
                    for n in range(1, 5)
                )
            )
        ]
    )

    response = _run(monkeypatch, fake, settings=_ESCALATION_SETTINGS)

    assert response.escalation.body.startswith("Draft number 1.")
    for n in range(1, 5):
        assert f"Draft number {n}" not in response.conversational_text


def test_a_named_place_reaches_the_response_as_a_resolved_card(monkeypatch, no_retrieval):
    fake = _FakeBedrock(
        [
            _text_turn(
                "The Career Center handles resumes.\n\n<place>career-center</place>"
            )
        ]
    )

    response = _run(monkeypatch, fake)

    assert response.place is not None
    assert response.place.name == "Career Center"
    assert response.place.address.startswith("Clark Hall")
    assert response.place.directions_url.startswith("https://www.google.com/maps/dir/")
    # The key was an instruction to the server; it never becomes bubble copy.
    assert response.conversational_text == "The Career Center handles resumes."


def test_an_unlisted_place_yields_no_card_at_all(monkeypatch, no_retrieval):
    """Not a guessed card and not a near-miss key."""
    fake = _FakeBedrock(
        [_text_turn("Try the bowling alley.\n\n<place>student-union-bowling-alley</place>")]
    )

    response = _run(monkeypatch, fake)

    assert response.place is None
    assert response.conversational_text == "Try the bowling alley."


def test_an_untagged_turn_carries_no_place(monkeypatch, no_retrieval):
    fake = _FakeBedrock([_text_turn("Peer Connections runs drop-in tutoring.")])
    assert _run(monkeypatch, fake).place is None


def test_a_safety_turn_never_carries_a_location(monkeypatch, no_retrieval):
    """Same rule as the offer above, in a taller box: this turn is a phone call."""
    fake = _FakeBedrock(
        [
            _text_turn(
                "<safety>crisis-988</safety>\n\nPlease reach someone below.\n\n"
                "<place>student-wellness-center</place>"
            )
        ]
    )

    response = _run(monkeypatch, fake)

    assert response.safety_handoff is not None
    assert response.place is None
    assert "student-wellness-center" not in response.conversational_text


def test_a_location_is_dropped_from_an_untagged_crisis_reply(monkeypatch, no_retrieval):
    """The other route into a safety turn: prose naming crisis lines without the tag."""
    fake = _FakeBedrock(
        [
            _text_turn(
                "Please call or text 988 right now.\n\n<place>student-wellness-center</place>"
            )
        ]
    )

    response = _run(monkeypatch, fake)

    assert response.safety_handoff is not None
    assert response.place is None


def test_the_place_rides_on_a_reply_that_also_has_cards(monkeypatch):
    """A location is not an alternative to a card, it is the other half of one."""
    monkeypatch.setattr(
        orchestrator,
        "retrieve_chunks",
        lambda query, settings: [
            RetrievedChunk(
                text="Clark Hall room 140.",
                score=0.9,
                source_url="https://careercenter.sjsu.edu/",
                title="Career Center",
                section="career",
                s3_uri=None,
            )
        ],
    )
    fake = _FakeBedrock(
        [
            _text_turn(
                "Here is the office.\n\n"
                '<card ref="1"><title>Resume help</title>'
                "<desc>Walk in for a review.</desc></card>\n\n"
                "<place>career-center</place>"
            )
        ]
    )

    response = _run(monkeypatch, fake)

    assert len(response.statement_batches[0].cards) == 1
    assert response.place.name == "Career Center"


def test_the_streamed_turn_resolves_the_same_place_as_the_buffered_one(monkeypatch):
    """One parser, one exit, so parity is a property of the structure."""
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])
    reply = "Head over there.\n\n<place>spartan-food-pantry</place>"

    monkeypatch.setattr(
        orchestrator, "_bedrock_client", lambda region: _FakeBedrock([_text_turn(reply)])
    )
    buffered = orchestrator.run_chat(ChatRequest(query="where is the pantry?"), _SETTINGS)

    monkeypatch.setattr(
        orchestrator,
        "_bedrock_client",
        lambda region: _FakeStreamingBedrock([_text_events(reply)]),
    )
    streamed = orchestrator.run_chat(
        ChatRequest(query="where is the pantry?"), _SETTINGS, stream=_RecordingSink()
    )

    assert buffered.place == streamed.place
    assert buffered.place is not None


def test_the_preview_never_shows_a_place_key(monkeypatch):
    """A catalogue key is machinery, and the preview stops at the tag."""
    sink = _RecordingSink()
    monkeypatch.setattr(orchestrator, "retrieve_chunks", lambda query, settings: [])
    monkeypatch.setattr(
        orchestrator,
        "_bedrock_client",
        lambda region: _FakeStreamingBedrock(
            [_text_events("Clark Hall it is.\n\n<place>career-center</place>")]
        ),
    )

    orchestrator.run_chat(ChatRequest(query="where?"), _SETTINGS, stream=sink)

    from cards import preview_safe_prefix

    for accumulated in sink.texts:
        assert "career-center" not in preview_safe_prefix(accumulated)

"""The generation worker: the same turn POST /chat runs, ending in one authoritative frame.

THE CENTRAL RULE, as tests. What streams is a preview; what the browser renders is the final
payload, and that payload is exactly what the buffered handler would have produced for the
same model text. The strongest assertion in this file is the one that builds the same turn
both ways and compares them.
"""

import copy
import json

import pytest

import orchestrator
import stream_worker
from conftest import FakeConversationStore, TEST_SUB, stored
from test_streaming import CONNECTION_ID, _FakeManagement


_CONVERSATION = "01J0000000000000000000000A"

_REPLY = (
    "Two places can help with that.\n\n"
    '<card ref="1"><title>Writing Center</title>'
    "<desc>Drop-in help with any assignment, at any stage.</desc>"
    "<followup>What are the Writing Center's hours?</followup></card>\n\n"
    "Which of those sounds closer to what you need?"
)


class _FakeContext:
    def get_remaining_time_in_millis(self):
        return 60_000


class _FakeStreamingBedrock:
    def __init__(self, text):
        size = max(1, len(text) // 8)
        self._events = (
            [{"messageStart": {"role": "assistant"}}]
            + [
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text[i : i + size]}}}
                for i in range(0, len(text), size)
            ]
            + [
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
                {"metadata": {"usage": {"inputTokens": 2100, "outputTokens": 190}}},
            ]
        )

    def converse_stream(self, **kwargs):
        self.kwargs = copy.deepcopy(kwargs)
        return {"stream": iter(self._events)}

    def converse(self, **kwargs):
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": _REPLY}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 2100, "outputTokens": 190},
        }


@pytest.fixture
def worker(monkeypatch):
    """The worker with its store, its socket and its model all stubbed."""
    from retrieve import RetrievedChunk

    management = _FakeManagement()
    store = FakeConversationStore()
    bedrock = _FakeStreamingBedrock(_REPLY)

    monkeypatch.setattr(stream_worker, "STORE", store)
    monkeypatch.setattr(stream_worker, "generate_title", lambda **kwargs: "Writing help")
    monkeypatch.setattr("streaming.management_client", lambda endpoint: management)
    monkeypatch.setattr(orchestrator, "_bedrock_client", lambda region: bedrock)
    monkeypatch.setattr(
        orchestrator,
        "retrieve_chunks",
        lambda query, settings: [
            RetrievedChunk(
                text="The Writing Center supports every student, at any stage of a draft.",
                score=0.91,
                source_url="https://www.sjsu.edu/writingcenter/",
                title="Writing Center",
                section="academic-support",
                s3_uri="s3://bucket/wc",
            )
        ],
    )

    management.store = store
    management.bedrock = bedrock
    return management


def _event(**overrides):
    event = {
        "connectionId": CONNECTION_ID,
        "turnId": "01J000000000000000000TURN",
        "userId": TEST_SUB,
        "conversationId": _CONVERSATION,
        "isNewConversation": False,
        "userSortKey": f"MSG#{_CONVERSATION}#SK0001",
        "query": "where can I get help with an essay?",
        "followup": False,
        "usage": {"guardrailContentUnits": 1},
    }
    event.update(overrides)
    return event


def test_the_final_payload_is_what_post_chat_would_have_returned(worker, monkeypatch):
    """THE ACCEPTANCE CRITERION. The same model text through both transports must render
    the same finished turn - cards, prose, the trailing split, all of it - because both
    exit through the same _response_from_text reading the same complete reply."""
    from models import ChatRequest

    stream_worker.lambda_handler(_event(), _FakeContext())

    final = worker.of_type("final")
    assert len(final) == 1
    payload = final[0]["payload"]

    buffered = orchestrator.run_chat(
        ChatRequest(query="where can I get help with an essay?"), stream_worker.SETTINGS
    ).model_dump(by_alias=True)

    assert payload["conversationalText"] == buffered["conversationalText"]
    assert payload["trailingText"] == buffered["trailingText"]
    assert payload["safetyHandoff"] == buffered["safetyHandoff"]
    streamed_cards = [c for b in payload["statementBatches"] for c in b["cards"]]
    buffered_cards = [c for b in buffered["statementBatches"] for c in b["cards"]]
    assert streamed_cards == buffered_cards
    # The card carries a URL the model never saw, resolved server-side from its ref.
    assert streamed_cards[0]["sourceUrl"] == "https://www.sjsu.edu/writingcenter/"


def test_the_preview_never_contained_the_card_markup(worker):
    """Prose only. The cards arrive in the final payload, parsed from the complete reply -
    the student never sees a half-typed `<card ref="1">`."""
    stream_worker.lambda_handler(_event(), _FakeContext())

    streamed = "".join(frame["text"] for frame in worker.of_type("delta"))
    assert "<card" not in streamed
    assert "<title>" not in streamed
    assert "Writing Center" not in streamed
    assert streamed == "Two places can help with that.\n\n"


def test_the_reply_is_persisted_as_the_model_wrote_it(worker):
    """The same record the buffered turn writes, because it is the same response object out
    of the same run_chat: the reply whole, tags and all, plus the pairs its cards resolved
    against. A streamed turn and a buffered one are indistinguishable on the table."""
    stream_worker.lambda_handler(_event(), _FakeContext())

    written = worker.store.appended[-1]
    assert written["role"] == "assistant"
    assert "<card" in written["text"], "the record keeps the card group's position"
    assert "Two places can help with that." in written["text"]
    assert "Which of those sounds closer" in written["text"]
    assert written["sources"], "and the URLs those cards resolved against"


def test_the_turn_reads_history_without_re_reading_the_message_just_written(worker):
    """The route function already wrote the student's message and passed its sort key. The
    loop appends this turn in memory, so reading it back would say it twice."""
    stream_worker.lambda_handler(_event(), _FakeContext())

    (_, kwargs) = next(call for call in worker.store.calls if call[0] == "read")
    assert kwargs["exclude_sort_key"] == f"MSG#{_CONVERSATION}#SK0001"
    assert kwargs["user_id"] == TEST_SUB


def test_usage_carries_the_guardrail_the_route_function_already_billed(worker):
    """ONE TALLY PER TURN, opened in the route function before the screen and finished here.
    A meter that only counted what the worker spent would read low by exactly the screen
    that every message pays for."""
    stream_worker.lambda_handler(_event(), _FakeContext())

    usage = worker.of_type("final")[0]["payload"]["usage"]
    assert usage["guardrailContentUnits"] == 1, "the route function's screen is still in it"
    assert usage["modelCalls"] == 1
    assert usage["inputTokens"] == 2100
    assert usage["outputTokens"] == 190
    assert usage["retrievals"] == 1


def test_a_new_conversation_is_named_and_the_name_rides_out_on_the_final_frame(worker):
    stream_worker.lambda_handler(_event(isNewConversation=True), _FakeContext())

    assert worker.of_type("final")[0]["payload"]["title"] == "Writing help"


def test_a_closed_tab_stops_the_pushing_but_the_turn_is_still_finished(monkeypatch, worker):
    """MY CHOICE, stated: a 410 stops the frames and nothing else. The model call is already
    paid for, and abandoning the turn would leave a user message with no assistant reply -
    the dangling turn docs/accounts-and-storage.md calls a reef, which the next turn would
    have to merge. Coming back to a coherent conversation is worth the writes."""
    worker.gone_after = 0  # every push 410s, from the first delta

    result = stream_worker.lambda_handler(_event(), _FakeContext())

    assert result == {"ok": True}
    assert worker.frames == [], "nothing was delivered"
    written = worker.store.appended[-1]
    assert written["role"] == "assistant", "the reply is on record for when they return"
    assert written["sources"], "with its sources, so a reopened conversation is complete"


def test_a_failed_loop_tells_the_student_rather_than_leaving_the_socket_silent(
    worker, monkeypatch
):
    """The alternative is a spinner that never resolves. The exception is logged, not sent:
    a botocore message can quote the request, and the request is the student's own words."""

    def _explode(*args, **kwargs):
        raise RuntimeError("bedrock is unavailable: 'where can I get help with an essay?'")

    monkeypatch.setattr(stream_worker, "run_chat", _explode)

    result = stream_worker.lambda_handler(_event(), _FakeContext())

    assert result == {"ok": False}
    errors = worker.of_type("error")
    assert len(errors) == 1
    assert "unavailable" in errors[0]["message"]
    assert "essay" not in json.dumps(errors[0]), "the student's words are not echoed back"
    assert worker.of_type("final") == []


def test_the_loop_gets_the_same_budget_the_buffered_path_gets(worker, monkeypatch):
    """The worker is not behind the gateway's 29-second ceiling and could be given more, but
    a longer budget would make a streamed turn answer questions a buffered turn gives up on
    - and identical rendering is the property this feature is held to."""
    seen = {}

    real = orchestrator.run_chat

    def _capture(request, settings, **kwargs):
        seen["deadline"] = kwargs.get("deadline")
        return real(request, settings, **kwargs)

    monkeypatch.setattr(stream_worker, "run_chat", _capture)
    stream_worker.lambda_handler(_event(), _FakeContext())

    import time

    budget = seen["deadline"] - time.monotonic()
    assert budget <= stream_worker.SETTINGS.converse_deadline_seconds


def test_nothing_is_attached_to_the_model_call_by_default(worker):
    """The output guardrail is off, so a streamed reply is the same text a buffered one is.
    Its only safe mode holds the response back to scan it in chunks, which spends most of
    this feature's benefit on a screen today's guardrail cannot fire."""
    stream_worker.lambda_handler(_event(), _FakeContext())

    assert "guardrailConfig" not in worker.bedrock.kwargs
    assert stream_worker._guardrail_config() is None


def test_the_output_guardrail_can_only_ever_be_synchronous(monkeypatch):
    """`async` releases text to the student before it has been scanned, which is not a
    screen at all. There is no configuration that produces it - the mode is a literal."""
    monkeypatch.setattr(stream_worker, "_OUTPUT_GUARDRAIL", True)

    config = stream_worker._guardrail_config()

    assert config["streamProcessingMode"] == "sync"
    assert config["guardrailIdentifier"] == stream_worker.SETTINGS.input_guardrail_id
    assert config["guardrailVersion"] == stream_worker.SETTINGS.input_guardrail_version


def test_a_streamed_turn_stores_its_draft_beside_its_cards(worker, monkeypatch):
    """The worker writes the same two records the buffered path does, and the draft is one
    of the things stored rather than reproduced."""
    import dataclasses

    from models import ChatResponse, EmailDraft

    monkeypatch.setattr(
        stream_worker,
        "run_chat",
        lambda request, settings, **kwargs: ChatResponse(
            conversationalText="That one needs a person.",
            escalation=EmailDraft(to="sjsucares@sjsu.edu", subject="S", body="B"),
        ),
    )
    # Nothing here reads Settings for the draft - the response already carries it - but the
    # worker is the deployed path, so it runs with the feature configured.
    monkeypatch.setattr(
        stream_worker,
        "SETTINGS",
        dataclasses.replace(
            stream_worker.SETTINGS, escalation_recipient="sjsucares@sjsu.edu"
        ),
    )

    stream_worker.lambda_handler(_event(), _FakeContext())

    written = worker.store.appended[0]
    assert written["escalation"] == {
        "to": "sjsucares@sjsu.edu",
        "subject": "S",
        "body": "B",
    }


def test_a_streamed_turn_stores_its_location_beside_its_cards(worker, monkeypatch):
    """Storage parity with the buffered path, which is what keeps a conversation the same
    conversation whichever transport answered it."""
    from models import ChatResponse, PlaceCard

    place = PlaceCard(
        key="career-center",
        name="Career Center",
        address="Clark Hall, 1st floor, room 140",
        directionsUrl="https://www.google.com/maps/dir/?api=1&destination=Clark+Hall",
    )
    monkeypatch.setattr(
        stream_worker,
        "run_chat",
        lambda request, settings, **kwargs: ChatResponse(
            conversationalText="Clark Hall, first floor.", place=place
        ),
    )

    stream_worker.lambda_handler(_event(), _FakeContext())

    assert worker.store.appended[0]["place"] == place.model_dump(by_alias=True)


def test_the_final_payload_carries_the_location(worker, monkeypatch):
    """The authoritative payload is a dump of the whole response, so a new field rides along
    by construction. Asserted once, because "by construction" is the claim being made."""
    from models import ChatResponse, PlaceCard

    monkeypatch.setattr(
        stream_worker,
        "run_chat",
        lambda request, settings, **kwargs: ChatResponse(
            conversationalText="Clark Hall, first floor.",
            place=PlaceCard(
                key="career-center",
                name="Career Center",
                address="Clark Hall, 1st floor, room 140",
                directionsUrl="https://www.google.com/maps/dir/?api=1&destination=Clark+Hall",
            ),
        ),
    )

    stream_worker.lambda_handler(_event(), _FakeContext())

    final = worker.of_type("final")
    assert final, worker.types
    assert final[-1]["payload"]["place"]["name"] == "Career Center"

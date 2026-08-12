"""The generation worker: the half of a streamed turn that takes twenty seconds.

WHY IT IS A SEPARATE FUNCTION. A WebSocket route integration has the same 29-second ceiling
every API Gateway integration has, and the agent loop can use most of it. So the route
function (app/streaming.py) does the fast, ordered part - validate, rate limit, guardrail,
write the student's message - and hands off here ASYNCHRONOUSLY, returning immediately. This
function is not behind the gateway at all: it talks back down the connection with
post_to_connection, which is why the timeout stops applying.

IT IS INVOKED WITH RETRIES SET TO ZERO (infra_stack.py). Lambda retries a failed async
invocation twice by default, and a retried worker answers the same question again - down the
same socket, billing the model each time. Nothing here is idempotent enough to survive that,
so the retry is off rather than defended against.

WHAT IT DOES NOT DO is decide anything about the reply. It reads history, calls the same
run_chat POST /chat calls, writes the same two records and sends the same ChatResponse. The
streaming is a sink handed to the loop; remove it and this is the buffered turn.
"""

from __future__ import annotations

import logging
import os
import time

from cards import join_prose
from history import ConversationStore
from models import ChatRequest
from orchestrator import run_chat
from settings import load_settings
from streaming import ConnectionSink, MANAGEMENT_ENDPOINT, DELTA_MIN_CHARS, DELTA_MAX_DELAY_MS
from titles import generate_title
from usage import TurnUsage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SETTINGS = load_settings()
STORE = ConversationStore(
    SETTINGS.chat_history_table_name, title_max_chars=SETTINGS.title_max_chars
)

# Seconds held back from Lambda's remaining time for the work AFTER the loop: the reply is
# written, the conversation may be titled, and the final payload still has to be pushed.
# Larger than the HTTP handler's reserve because that last push is a network call, and it is
# the one thing in the turn the student actually sees.
_POST_LOOP_RESERVE_SECONDS = 6

# Attach this stack's guardrail to ConverseStream, or not. OFF by default and measured
# (config.yaml `streaming.output_guardrail`): the only safe stream mode holds the reply back
# to scan it in chunks, which spends most of this feature's benefit on a screen that today's
# guardrail cannot fire.
_OUTPUT_GUARDRAIL = (os.environ.get("STREAM_OUTPUT_GUARDRAIL") or "").lower() == "true"


def _guardrail_config():
    """The guardrail block for ConverseStream, or None.

    `sync` IS THE ONLY MODE THIS CAN EMIT, and that is structural rather than a default:
    `async` releases text to the student before it has been scanned, which is not a screen
    at all, and it does not support PII masking. There is no configuration that produces it.
    """
    if not _OUTPUT_GUARDRAIL:
        return None
    return {
        "guardrailIdentifier": SETTINGS.input_guardrail_id,
        "guardrailVersion": SETTINGS.input_guardrail_version,
        "streamProcessingMode": "sync",
    }


def _deadline(context):
    """The loop's wall-clock budget, the same minimum-of-two shape app/handler.py uses.

    DELIBERATELY THE SAME NUMBER as the buffered path (chat.converse_deadline_seconds), even
    though this function is not behind the 29-second gateway ceiling and could be given
    more. A longer budget here would make a streamed turn answer questions a buffered turn
    gives up on, and "the finished turn renders identically to the buffered one for the same
    question" is the property this whole feature is held to.
    """
    budget = float(SETTINGS.converse_deadline_seconds)
    remaining_ms = getattr(context, "get_remaining_time_in_millis", None)
    if callable(remaining_ms):
        try:
            budget = min(budget, (remaining_ms() / 1000.0) - _POST_LOOP_RESERVE_SECONDS)
        except Exception:
            logger.exception("Could not read Lambda remaining time; using the configured budget")
    return time.monotonic() + budget


def _title_deadline(context):
    budget = float(SETTINGS.title_deadline_seconds)
    remaining_ms = getattr(context, "get_remaining_time_in_millis", None)
    if callable(remaining_ms):
        try:
            budget = min(budget, (remaining_ms() / 1000.0) - 2)
        except Exception:
            logger.exception("Could not read Lambda remaining time; using the title budget")
    return time.monotonic() + budget


def lambda_handler(event, context):
    """One streamed turn, from the message already on record to the final payload.

    THE STUDENT'S MESSAGE IS ALREADY WRITTEN when this starts - the route function did it
    before invoking, so a turn that dies in here still leaves the disclosure on record. That
    is the same ordering guarantee POST /chat makes, split across two functions.
    """
    connection_id = event["connectionId"]
    turn_id = event["turnId"]
    user_id = event["userId"]
    conversation_id = event["conversationId"]

    sink = ConnectionSink(
        endpoint=MANAGEMENT_ENDPOINT,
        connection_id=connection_id,
        turn_id=turn_id,
        min_chars=DELTA_MIN_CHARS,
        max_delay_ms=DELTA_MAX_DELAY_MS,
    )

    # The tally the route function opened. It already holds the guardrail units that screen
    # billed, so the cost panel sees one turn's whole spend rather than the part this
    # function paid for.
    usage = TurnUsage.model_validate(event.get("usage") or {})

    request = ChatRequest.model_validate(
        {
            "query": event["query"],
            "conversationId": conversation_id,
            "followup": bool(event.get("followup", False)),
        }
    )

    try:
        history = STORE.recent_messages(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=SETTINGS.max_history_messages,
            # The message the route function just wrote. Excluded because run_chat appends
            # this turn in memory, so reading it back would say it twice.
            exclude_sort_key=event.get("userSortKey"),
        )
    except Exception:
        logger.exception("Could not read conversation history; answering without it")
        history = []

    try:
        response = run_chat(
            request,
            SETTINGS,
            history=history,
            deadline=_deadline(context),
            usage=usage,
            # The preview. Nothing downstream reads it back.
            stream=sink,
            guardrail_config=_guardrail_config(),
        )
    except Exception:
        # The student gets a plain failure rather than a partial answer, and the exception
        # is logged rather than sent: a botocore message can quote the request, and the
        # request is the student's own words.
        logger.exception("Streamed chat orchestration failed")
        sink.error("The assistant is unavailable right now.")
        return {"ok": False}

    # The tail of the preview, so the prose on screen ends where the model's lead-in does
    # rather than a batch short of it. It flushes the sink's own accumulated RAW text - not
    # `response.conversational_text`, which is the parsed and normalised prose and a
    # different string, so slicing it with the sink's offset would send a fragment starting
    # mid-word. Harmless when the connection is already gone.
    sink.flush()

    try:
        STORE.append_message(
            user_id=user_id,
            conversation_id=conversation_id,
            role="assistant",
            text=join_prose(response.conversational_text, response.trailing_text),
            cards=[
                card.model_dump(by_alias=True)
                for batch in (response.statement_batches or [])
                for card in batch.cards
            ],
        )
    except Exception:
        logger.exception("Could not record the assistant's reply; sending it anyway")

    response.conversation_id = conversation_id
    if event.get("isNewConversation"):
        try:
            title = generate_title(
                question=request.query,
                answer=join_prose(response.conversational_text, response.trailing_text),
                settings=SETTINGS,
                deadline=_title_deadline(context),
                usage=usage,
            )
            if title is not None and STORE.set_generated_title(
                user_id=user_id, conversation_id=conversation_id, title=title
            ):
                response.title = title
        except Exception:
            logger.warning(
                "Could not name a new conversation; it keeps its first-message title.",
                exc_info=True,
            )
    response.usage = usage

    # THE AUTHORITATIVE PAYLOAD, and the whole reason the preview above is allowed to be
    # rough: this is byte-for-byte what POST /chat would have returned for this turn, cards,
    # caps, dashes, trailing split and safety panel included, because it came out of the
    # same run_chat. The browser throws the preview away and renders this.
    sink.final(response.model_dump(by_alias=True))

    logger.info(
        "ws turn cards=%s safety=%s calls=%s in=%s out=%s frames=%s gone=%s",
        sum(len(batch.cards) for batch in (response.statement_batches or [])),
        response.safety_handoff is not None,
        usage.model_calls,
        usage.input_tokens,
        usage.output_tokens,
        sink.frames,
        sink.gone,
    )
    return {"ok": True}

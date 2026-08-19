"""The generation worker: the half of a streamed turn that takes twenty seconds.

It decides nothing about the reply, and its async invoke has retries off; see
docs/chat-service.md, Streaming.
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

# Held back from Lambda's remaining time for the write, the title and the final push.
_POST_LOOP_RESERVE_SECONDS = 6

# Off by default and measured; see docs/chat-service.md, Streaming.
_OUTPUT_GUARDRAIL = (os.environ.get("STREAM_OUTPUT_GUARDRAIL") or "").lower() == "true"


def _guardrail_config():
    """The guardrail block for ConverseStream, or None. `sync` is the only mode it emits."""
    if not _OUTPUT_GUARDRAIL:
        return None
    return {
        "guardrailIdentifier": SETTINGS.input_guardrail_id,
        "guardrailVersion": SETTINGS.input_guardrail_version,
        "streamProcessingMode": "sync",
    }


def _deadline(context):
    """The loop's wall-clock budget, deliberately the same number as the buffered path."""
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
    """One streamed turn, from the message already on record to the final payload."""
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
            # run_chat appends this turn in memory, so reading it back would say it twice.
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
        logger.exception("Streamed chat orchestration failed")
        sink.error("The assistant is unavailable right now.")
        return {"ok": False}

    # The sink's own raw text: its offset indexes the string it was measured against.
    sink.flush()

    try:
        STORE.append_message(
            user_id=user_id,
            conversation_id=conversation_id,
            role="assistant",
            text=response.raw_text,
            sources=response.sources,
            escalation=(
                response.escalation.model_dump(by_alias=True)
                if response.escalation is not None
                else None
            ),
            place=(
                response.place.model_dump(by_alias=True)
                if response.place is not None
                else None
            ),
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

    # Byte-for-byte what POST /chat would have returned for this turn.
    sink.final(response.model_dump(by_alias=True))

    logger.info(
        "ws turn cards=%s safety=%s place=%s escalation=%s calls=%s in=%s out=%s "
        "frames=%s gone=%s",
        sum(len(batch.cards) for batch in (response.statement_batches or [])),
        response.safety_handoff is not None,
        response.place.key if response.place is not None else None,
        response.escalation is not None,
        usage.model_calls,
        usage.input_tokens,
        usage.output_tokens,
        sink.frames,
        sink.gone,
    )
    return {"ok": True}

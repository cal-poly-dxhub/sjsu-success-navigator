"""One turn, with no transport in it.

Cap, guardrail, write, read, model, write, title: the order is argued for, and every
dependency arrives as an argument.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from cards import join_prose
from history import ConversationStore, new_conversation_id
from models import ChatRequest, ChatResponse
from orchestrator import run_chat
from ratelimit import claim_turn
from settings import Settings
from titles import generate_title
from usage import TurnUsage

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class TurnRefused(Exception):
    """The daily cap is spent. Nothing was written and nothing was billed."""

    def __init__(self, refusal):
        super().__init__(refusal.message)
        self.refusal = refusal


def apply_input_guardrail(query: str, *, settings: Settings, bedrock, usage=None):
    """The query alone, because PROMPT_ATTACK is about what the student sent."""
    try:
        result = bedrock.apply_guardrail(
            guardrailIdentifier=settings.input_guardrail_id,
            guardrailVersion=settings.input_guardrail_version,
            source="INPUT",
            content=[{"text": {"text": query}}],
        )
    except Exception:
        logger.exception("ApplyGuardrail failed; continuing without the input screen")
        return None

    if usage is not None:
        usage.record_guardrail(result.get("usage"))

    if result.get("action") != "GUARDRAIL_INTERVENED":
        return None

    outputs = result.get("outputs") or []
    text = (outputs[0].get("text") if outputs else "") or ""
    logger.info("Input guardrail intervened on a query")
    return text


def stored_escalation(response: ChatResponse):
    """What the student was shown, so a recipient that changes cannot rewrite an old turn."""
    if response.escalation is None:
        return None
    return response.escalation.model_dump(by_alias=True)


def stored_place(response: ChatResponse):
    """What the student was shown, so an office that moves cannot rewrite an old turn."""
    if response.place is None:
        return None
    return response.place.model_dump(by_alias=True)


def name_new_conversation(
    *,
    user_id,
    conversation_id,
    question,
    answer,
    deadline,
    store: ConversationStore,
    settings: Settings,
    make_title: Callable[..., Any] = generate_title,
    usage=None,
):
    """The fallback title is already on the header, so every failure here is survivable."""
    try:
        title = make_title(
            question=question,
            answer=answer,
            settings=settings,
            deadline=deadline,
            usage=usage,
        )
        if title is None:
            return None
        if not store.set_generated_title(
            user_id=user_id, conversation_id=conversation_id, title=title
        ):
            return None
    except Exception:
        logger.warning(
            "Could not name a new conversation; it keeps its first-message title.",
            exc_info=True,
        )
        return None
    return title


def run_turn(
    request: ChatRequest,
    *,
    user_id: str,
    client_id: str | None,
    settings: Settings,
    store: ConversationStore,
    bedrock_client: Callable[[], Any],
    deadline: float,
    title_deadline_at: Callable[[], float],
    converse: Callable[..., ChatResponse] = run_chat,
    make_title: Callable[..., Any] = generate_title,
    stream: Any = None,
) -> ChatResponse:
    """The whole turn, in order. `stream` is the only difference from a buffered one."""
    # Step 1, the daily cap: ahead of the guardrail, so a refusal spends nothing billable.
    refusal = claim_turn(
        store=store,
        user_id=user_id,
        client_id=client_id,
        settings=settings,
    )
    if refusal is not None:
        raise TurnRefused(refusal)

    # Opened before the first thing that spends anything, and mutated in place from here down.
    usage = TurnUsage()

    # Step 2, the guardrail: no turn is recorded, but the screen was billed, so usage returns.
    blocked_text = apply_input_guardrail(
        request.query, settings=settings, bedrock=bedrock_client(), usage=usage
    )
    if blocked_text is not None:
        return ChatResponse(
            conversationId=request.conversation_id,
            conversationalText=blocked_text,
            usage=usage,
        )

    # An unnamed conversation did not exist a moment ago, so this is the turn that titles it.
    is_new_conversation = request.conversation_id is None
    conversation_id = request.conversation_id or new_conversation_id()

    # Step 3, the student's message: before the model call, so a timeout still leaves a record.
    user_sort_key = None
    try:
        user_sort_key = store.append_message(
            user_id=user_id,
            conversation_id=conversation_id,
            role="user",
            text=request.query.strip(),
        )
    except Exception:
        # No sort key to exclude below; at worst the read picks the message up and merges it.
        logger.exception("Could not record the student's message; answering anyway")

    # Step 3b, the id, ahead of every byte of the reply. A buffered turn has no sink.
    if stream is not None:
        stream.accepted(conversation_id)

    # Step 4, the context read.
    try:
        history = store.recent_messages(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=settings.max_history_messages,
            exclude_sort_key=user_sort_key,
        )
    except Exception:
        logger.exception("Could not read conversation history; answering without it")
        history = []

    # Step 5, the model. Spread rather than named: an injected stand-in may not accept it.
    streaming_kwargs = {} if stream is None else {"stream": stream}
    response = converse(
        request,
        settings,
        history=history,
        deadline=deadline,
        usage=usage,
        **streaming_kwargs,
    )

    # Step 6, the reply.
    try:
        store.append_message(
            user_id=user_id,
            conversation_id=conversation_id,
            role="assistant",
            # As the model wrote it, tags and all, so reopening re-parses rather than rebuilds.
            text=response.raw_text,
            sources=response.sources,
            # Recorded, not re-resolved: the key survives the text but against today's table.
            place=stored_place(response),
            escalation=stored_escalation(response),
        )
    except Exception:
        logger.exception("Could not record the assistant's reply; returning it anyway")

    response.conversation_id = conversation_id

    # Step 7, the title, on a new conversation only.
    if is_new_conversation:
        response.title = name_new_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            question=request.query,
            answer=join_prose(response.conversational_text, response.trailing_text),
            deadline=title_deadline_at(),
            store=store,
            settings=settings,
            make_title=make_title,
            usage=usage,
        )
    response.usage = usage

    logger.info(
        "chat cards=%s safety=%s place=%s escalation=%s calls=%s in=%s out=%s",
        sum(len(batch.cards) for batch in (response.statement_batches or [])),
        response.safety_handoff is not None,
        response.place.key if response.place is not None else None,
        response.escalation is not None,
        usage.model_calls,
        usage.input_tokens,
        usage.output_tokens,
    )
    return response

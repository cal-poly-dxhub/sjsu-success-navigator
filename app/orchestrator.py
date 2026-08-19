"""The Converse loop: retrieval primed on every turn, the tool as escape hatch, one exit.

The model's text reply is the answer on every path, under an iteration cap and a
wall-clock deadline; see docs/chat-service.md, The Converse loop.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Protocol, Sequence

import boto3
from botocore.config import Config

from settings import Settings
from campus_time import campus_context_line
from escalation import ESCALATION_FALLBACK_TEXT, build_email_draft
from history import StoredMessage
from models import ChatRequest, ChatResponse
from cards import (
    TurnSources,
    cards_from_parsed,
    cited_source_urls,
    create_statement_batch,
    join_prose,
    parse_model_response,
    source_options_for_tool,
    strip_card_tags,
)
from places import resolve_place
from prompts import build_system_prompt
from retrieve import retrieve_chunks
from safety import SAFETY_FALLBACK_TEXT, apply_safety_handoff_to_response
from tools import TOOL_CONFIG
from usage import TurnUsage

logger = logging.getLogger(__name__)

# The only hardcoded reply left on this path: the model produced no text at all before
# the loop ran out. An admission that there is no answer, not a substitute for one.
_NO_OUTPUT_TEXT = (
    "I ran out of time putting that together. Ask me again and I'll take another run at it."
)

# Server-authored and constant, so a primed turn stays byte-identical for a given query.
_PRIMED_TOOL_USE_ID = "tooluse_primed_first_search"

# Built once per container. read_timeout is under the function's own 29s budget.
_BEDROCK_CLIENT = None


def _bedrock_client(region: str):
    global _BEDROCK_CLIENT
    if _BEDROCK_CLIENT is None:
        _BEDROCK_CLIENT = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                retries={"max_attempts": 3, "mode": "adaptive"},
                read_timeout=25,
                connect_timeout=5,
            ),
        )
    return _BEDROCK_CLIENT


class StreamSink(Protocol):
    """Where a streaming turn's PREVIEW goes. Implemented by app/streaming.py."""

    def status(self, stage: str) -> None:
        """Something is happening that is not text arriving - a retrieval, say."""

    def text(self, accumulated: str) -> None:
        """The reply as far as the model has written it."""


def run_chat(
    request: ChatRequest,
    settings: Settings,
    history: Sequence[StoredMessage] = (),
    deadline: float | None = None,
    usage: TurnUsage | None = None,
    stream: "StreamSink | None" = None,
    guardrail_config: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ChatResponse:
    """Run the Converse tool-use loop under an iteration cap and a wall-clock deadline."""
    client = _bedrock_client(settings.bedrock_region)

    if deadline is None:
        deadline = time.monotonic() + settings.converse_deadline_seconds

    # Built here, never persisted, and the only thing that can put a URL on a card.
    sources = TurnSources()
    messages = _build_converse_messages(request, history, settings, now)
    system_prompt = build_system_prompt(settings)
    last_text = ""

    _prime_first_search(messages, sources=sources, request=request,
                        settings=settings, deadline=deadline, usage=usage, stream=stream)

    for iteration in range(settings.max_converse_iterations):
        # Checked BEFORE the call: never start a request that cannot finish in time.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "Converse loop hit its wall-clock deadline after %s iteration(s); "
                "answering from the text produced so far (%s chars). sources=%s query=%r",
                iteration,
                len(last_text),
                len(sources),
                request.query[:80],
            )
            return _response_from_text(last_text, sources, request.query, settings)

        logger.info("Converse iteration %s for query=%r", iteration + 1, request.query[:80])

        call = {
            "modelId": settings.generation_model_id,
            "system": [{"text": system_prompt}],
            "messages": messages,
            "toolConfig": TOOL_CONFIG,
            "inferenceConfig": {
                "maxTokens": settings.generation_max_tokens,
                "temperature": settings.generation_temperature,
            },
        }
        if guardrail_config is not None:
            call["guardrailConfig"] = guardrail_config

        # One shape out of both calls, so nothing below this line knows which transport ran.
        if stream is None:
            response = client.converse(**call)
        else:
            response = _converse_streaming(client, call, stream)

        if usage is not None:
            # Taken from the stream's own metadata rather than recounted.
            usage.record_model_call(response)

        assistant_message = response["output"]["message"]
        messages.append(assistant_message)

        text = _extract_text(assistant_message)
        if text:
            last_text = text

        tool_uses = [
            block["toolUse"]
            for block in assistant_message.get("content", [])
            if "toolUse" in block
        ]

        # `end_turn` with no tool call is the answer. A `tool_use` stop reason carrying no
        # toolUse block is malformed, and there is nothing further to do with it either.
        if response.get("stopReason") != "tool_use" or not tool_uses:
            return _response_from_text(text, sources, request.query, settings)

        tool_results: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            tool_results.append(
                _run_tool(
                    tool_use,
                    sources=sources,
                    request=request,
                    settings=settings,
                    usage=usage,
                    stream=stream,
                )
            )

        messages.append({"role": "user", "content": tool_results})

    # The cap was reached without the model ever ending its turn. Logged as the distinct
    # event it is: otherwise it looks like a model that answered on its first call.
    logger.warning(
        "Converse loop hit its %s-iteration cap without a final reply; answering from the "
        "text produced so far (%s chars). query=%r",
        settings.max_converse_iterations,
        len(last_text),
        request.query[:80],
    )
    return _response_from_text(last_text, sources, request.query, settings)


def _converse_streaming(
    client,
    call: dict[str, Any],
    stream: StreamSink,
) -> dict[str, Any]:
    """Run ConverseStream and hand back what Converse would have returned."""
    response = client.converse_stream(**call)

    content: list[dict[str, Any]] = []
    # contentBlockIndex -> the partial block. A dict: nothing promises they arrive in order.
    blocks: dict[int, dict[str, Any]] = {}
    accumulated = ""
    stop_reason = None
    reported_usage = None

    def push(text: str) -> None:
        try:
            stream.text(text)
        except Exception:
            logger.warning("Could not push a streamed delta; finishing the turn anyway",
                           exc_info=True)

    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"]
            index = start.get("contentBlockIndex", 0)
            tool_use = (start.get("start") or {}).get("toolUse")
            if tool_use:
                blocks[index] = {
                    "toolUse": {
                        "toolUseId": tool_use.get("toolUseId"),
                        "name": tool_use.get("name"),
                        # Accumulated as a STRING and parsed at the close: Bedrock streams
                        # tool arguments as partial JSON, valid only once complete.
                        "_input_json": "",
                    }
                }
            continue

        if "contentBlockDelta" in event:
            delta_event = event["contentBlockDelta"]
            index = delta_event.get("contentBlockIndex", 0)
            delta = delta_event.get("delta") or {}
            if "text" in delta:
                block = blocks.setdefault(index, {"text": ""})
                block["text"] = block.get("text", "") + delta["text"]
                accumulated += delta["text"]
                push(accumulated)
            elif "toolUse" in delta:
                block = blocks.get(index)
                if block and "toolUse" in block:
                    block["toolUse"]["_input_json"] += delta["toolUse"].get("input", "")
            continue

        if "contentBlockStop" in event:
            index = event["contentBlockStop"].get("contentBlockIndex", 0)
            block = blocks.pop(index, None)
            if block is not None:
                content.append(_finished_block(block))
            continue

        if "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason")
            continue

        if "metadata" in event:
            reported_usage = event["metadata"].get("usage")
            continue

    # A block the stream never closed is kept, the same instinct the zero-card fallback has.
    for index in sorted(blocks):
        content.append(_finished_block(blocks[index]))

    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop_reason,
        "usage": reported_usage,
    }


def _finished_block(block: dict[str, Any]) -> dict[str, Any]:
    """One assembled content block, in the shape Converse would have returned it."""
    if "toolUse" not in block:
        return {"text": block.get("text", "")}

    tool_use = dict(block["toolUse"])
    raw = tool_use.pop("_input_json", "") or ""
    try:
        tool_use["input"] = json.loads(raw) if raw.strip() else {}
    except ValueError:
        logger.warning("A streamed toolUse carried unparseable input; searching on the "
                       "student's own message instead")
        tool_use["input"] = {}
    return {"toolUse": tool_use}


def _prime_first_search(
    messages: list[dict[str, Any]],
    *,
    sources: TurnSources,
    request: ChatRequest,
    settings: Settings,
    deadline: float,
    usage: TurnUsage | None = None,
    stream: StreamSink | None = None,
) -> None:
    """Run the first retrieval server-side and append it as a completed tool exchange."""
    if time.monotonic() >= deadline:
        return
    query = request.query.strip()
    _tell(stream, "retrieving")
    try:
        chunks = retrieve_chunks(query, settings)
        # Counted only once it returned: a retrieval that raised may not have been billed.
        if usage is not None:
            usage.record_retrieval()
        options = sources.add_chunks(chunks, limit=settings.card_max_retrieval_results)
    except Exception:
        logger.warning(
            "Primed retrieval failed; the model will search itself. query=%r",
            query[:80],
            exc_info=True,
        )
        return
    messages.append(
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": _PRIMED_TOOL_USE_ID,
                        "name": "retrieve_campus_resources",
                        "input": {"query": query},
                    }
                }
            ],
        }
    )
    messages.append(
        {
            "role": "user",
            "content": [
                _tool_result_block(
                    _PRIMED_TOOL_USE_ID,
                    {
                        "resultCount": len(options),
                        "results": source_options_for_tool(options),
                    },
                )
            ],
        }
    )


def _tell(stream: StreamSink | None, stage: str) -> None:
    """Say what the turn is doing, when anybody is listening. Swallows its own failures."""
    if stream is None:
        return
    try:
        stream.status(stage)
    except Exception:
        logger.warning("Could not push a %r status event", stage, exc_info=True)


def _run_tool(
    tool_use: dict[str, Any],
    *,
    sources: TurnSources,
    request: ChatRequest,
    settings: Settings,
    usage: TurnUsage | None = None,
    stream: StreamSink | None = None,
) -> dict[str, Any]:
    tool_name = tool_use["name"]
    tool_use_id = tool_use["toolUseId"]
    tool_input = tool_use.get("input") or {}

    if tool_name != "retrieve_campus_resources":
        return _tool_result_block(
            tool_use_id, {"error": f"Unknown tool: {tool_name}"}, is_error=True
        )

    search_query = str(tool_input.get("query", request.query)).strip()
    # The model decided the primed results missed. Worth saying: this is the longest wait.
    _tell(stream, "retrieving")
    chunks = retrieve_chunks(search_query, settings)
    if usage is not None:
        usage.record_retrieval()
    options = sources.add_chunks(chunks, limit=settings.card_max_retrieval_results)

    return _tool_result_block(
        tool_use_id,
        {
            "resultCount": len(options),
            "results": source_options_for_tool(options),
        },
    )


def _build_converse_messages(
    request: ChatRequest,
    history: Sequence[StoredMessage],
    settings: Settings,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Stored history plus this turn's message, in a shape Converse will accept."""
    messages: list[dict[str, Any]] = []

    for item in list(history)[-settings.max_history_messages :]:
        text = item.text.strip()
        if item.role == "assistant":
            text = strip_card_tags(text)
        if not text:
            continue
        messages.append({"role": item.role, "content": [{"text": text}]})

    messages.append(
        {"role": "user", "content": [{"text": _build_user_message(request, now)}]}
    )

    messages = _merge_consecutive_roles(messages)

    # Converse also requires the FIRST message to be a user turn, and a window can open on
    # an assistant reply. The loop above always ends on a user message, so this cannot empty.
    while messages and messages[0]["role"] == "assistant":
        messages.pop(0)

    return messages


def _merge_consecutive_roles(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []

    for message in messages:
        if not merged:
            merged.append(message)
            continue

        if merged[-1]["role"] == message["role"]:
            prev_text = merged[-1]["content"][0]["text"]
            next_text = message["content"][0]["text"]
            merged[-1]["content"][0]["text"] = f"{prev_text}\n\n{next_text}"
            continue

        merged.append(message)

    return merged


def _build_user_message(request: ChatRequest, now: datetime | None = None) -> str:
    """The user turn handed to the model. It does NOT read `request.followup`."""
    time_line = campus_context_line(now)
    return (
        f"{time_line}\n\n" if time_line else ""
    ) + (
        f"Student message:\n{request.query.strip()}"
        "\n\n"
        "Write your reply."
    )


def _tool_result_block(
    tool_use_id: str,
    payload: dict[str, Any],
    *,
    is_error: bool = False,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "toolResult": {
            "toolUseId": tool_use_id,
            "content": [{"text": json.dumps(payload)}],
        }
    }
    if is_error:
        block["toolResult"]["status"] = "error"
    return block


def _response_from_text(
    text: str,
    sources: TurnSources,
    query: str,
    settings: Settings,
) -> ChatResponse:
    """Parse one LIVE model reply into the wire response. The only place cards come from."""
    parsed = parse_model_response(text)
    escalation = (
        None
        if parsed.needs_safety
        else build_email_draft(parsed.escalation_prose, settings=settings)
    )
    return _assemble_response(
        parsed,
        text=text,
        sources=sources,
        query=query,
        settings=settings,
        escalation=escalation,
    )


def replay_stored_reply(
    *,
    text: str,
    urls_by_ref: dict[int, str] | None,
    escalation: Any | None,
    query: str,
    settings: Settings,
) -> ChatResponse:
    """Render one STORED assistant reply through the code that rendered it live."""
    return _assemble_response(
        parse_model_response(text),
        text=text,
        sources=TurnSources.from_stored(urls_by_ref),
        query=query,
        settings=settings,
        escalation=escalation,
    )


def _assemble_response(
    parsed,
    *,
    text: str,
    sources: TurnSources,
    query: str,
    settings: Settings,
    escalation: Any | None,
) -> ChatResponse:
    """One parsed reply as the wire response, live or replayed. THE ONE EXIT."""
    # Resolved here rather than beside the offer, because the key is IN THE REPLY, so a
    # replayed turn needs no argument. The display read hands back its own recorded card.
    place = None if parsed.needs_safety else resolve_place(parsed.place_key)

    if parsed.needs_safety:
        if parsed.escalation_prose is not None:
            logger.info(
                "chat route=safety escalation=dropped (a safety turn carries no offer)"
            )
        if parsed.place_key is not None:
            logger.info(
                "chat route=safety place=dropped (a safety turn carries no location card)"
            )
        # A safety turn carries no cards by contract, so this must NOT take the zero-card
        # fallback below, which would fold the card text back into the bubble.
        cards = []
        prose = join_prose(parsed.prose, parsed.trailing_prose) or strip_card_tags(text)
        prose = prose or SAFETY_FALLBACK_TEXT
        trailing = ""
        logger.info("chat route=safety keys=%s", list(parsed.safety_keys or ()))
    else:
        cards = cards_from_parsed(parsed.cards, sources, settings)
        if cards:
            # The split survives only when there is a card group to split around.
            prose, trailing = parsed.prose, parsed.trailing_prose
        else:
            # The zero-card fallback rebuilds the bubble from the COMPLETE reply, so there
            # is no position left to preserve.
            prose, trailing = strip_card_tags(text), ""

    if not prose and not trailing:
        if escalation is not None:
            # A reply that was NOTHING BUT an escalation block: its content is an email and
            # is removed from the bubble, so there is no prose left to introduce the draft.
            logger.warning(
                "The model wrote an escalation block and no prose for query=%r; "
                "introducing the draft with the server's own line.",
                query[:80],
            )
            prose = ESCALATION_FALLBACK_TEXT
        else:
            logger.warning("Model produced no usable text for query=%r", query[:80])
            prose = _NO_OUTPUT_TEXT

    batches = [create_statement_batch(cards, query)] if cards else []
    response = ChatResponse(
        conversationalText=prose,
        trailingText=trailing or None,
        statementBatches=batches or None,
        place=place,
        escalation=escalation,
        talkToPersonAvailable=True,
        # The record is the model's own text. Empty only when the loop produced nothing,
        # where the assembled prose IS what the student was shown.
        raw_text=text or join_prose(prose, trailing),
        sources=cited_source_urls(cards, sources),
    )
    # The whole message is screened, both sides of the split.
    return apply_safety_handoff_to_response(
        response,
        conversational_text=join_prose(prose, trailing),
        safety_keys=parsed.safety_keys,
    )


def _extract_text(assistant_message: dict[str, Any] | None) -> str:
    if not assistant_message:
        return ""

    parts: list[str] = []
    for block in assistant_message.get("content", []):
        if "text" in block:
            parts.append(block["text"])

    return "\n".join(parts).strip()

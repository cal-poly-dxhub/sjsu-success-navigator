from __future__ import annotations

import json
import logging
import time
from typing import Any

import boto3
from botocore.config import Config

from settings import Settings
from models import ChatRequest, ChatResponse
from cards import (
    build_statement_cards,
    cards_from_submission,
    chunks_to_tool_results,
    create_statement_batch,
    retrieve_statement_cards,
)
from prompts import SYSTEM_PROMPT
from retrieve import RetrievedChunk, retrieve_chunks
from safety import apply_safety_handoff_to_response
from tools import TOOL_CONFIG

logger = logging.getLogger(__name__)

# Module scope: built once per container and reused, where camp built one per request
# (which on Lambda discards the warm container's connection pool every invocation).
# read_timeout is 25s, not camp's 120: the function's own budget is 29s, so a socket
# that outlives it can only turn a diagnosable timeout into a gateway 504.
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


def classify_response_mode(response: ChatResponse) -> str:
    if response.safety_handoff:
        return "safety"
    if response.statement_batches and response.statement_batches[0].cards:
        return "triage"
    return "talk"


def run_chat(
    request: ChatRequest,
    settings: Settings,
    deadline: float | None = None,
) -> ChatResponse:
    """Run the Converse tool-use loop under TWO independent caps.

    `max_converse_iterations` bounds how many model calls happen.
    `deadline` bounds how long they may take, as a `time.monotonic()` timestamp. The two
    are not interchangeable: six iterations of a slow model, or one retrieval that stalls,
    exceeds the function's budget without ever reaching the iteration cap. Passing None
    derives the deadline from settings.converse_deadline_seconds; the handler passes an
    explicit one so Lambda's real remaining time can narrow it.

    Both caps exit the same way - through _fallback_response, which returns the best
    answer available from whatever was retrieved so far. That is the point: the
    alternative is being killed mid-Converse, where the invocation is billed and the
    student gets a gateway 504 carrying no response at all.
    """
    client = _bedrock_client(settings.bedrock_region)

    if deadline is None:
        deadline = time.monotonic() + settings.converse_deadline_seconds

    known_chunks: list[RetrievedChunk] = []
    messages = _build_converse_messages(request, settings)

    for iteration in range(settings.max_converse_iterations):
        # Checked BEFORE the call, not after: the point is never to start a Converse
        # request that cannot finish inside the function's remaining time.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "Converse loop hit its wall-clock deadline after %s iteration(s); "
                "returning the best answer available. chunks=%s query=%r",
                iteration,
                len(known_chunks),
                request.query[:80],
            )
            return _fallback_response(
                request, settings, known_chunks, None, allow_retrieval=False
            )

        logger.info("Converse iteration %s for query=%r", iteration + 1, request.query[:80])

        response = client.converse(
            modelId=settings.generation_model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig=TOOL_CONFIG,
            inferenceConfig={
                "maxTokens": settings.generation_max_tokens,
                "temperature": settings.generation_temperature,
            },
        )

        assistant_message = response["output"]["message"]
        messages.append(assistant_message)
        stop_reason = response.get("stopReason")

        if stop_reason != "tool_use":
            return _fallback_response(request, settings, known_chunks, assistant_message)

        tool_uses = [
            block["toolUse"]
            for block in assistant_message.get("content", [])
            if "toolUse" in block
        ]
        if not tool_uses:
            return _fallback_response(request, settings, known_chunks, assistant_message)

        tool_results: list[dict[str, Any]] = []
        final_response: ChatResponse | None = None

        for tool_use in tool_uses:
            tool_name = tool_use["name"]
            tool_use_id = tool_use["toolUseId"]
            tool_input = tool_use.get("input") or {}

            if tool_name == "submit_chat_response":
                final_response = _response_from_submission(
                    tool_input,
                    query=request.query,
                    known_chunks=known_chunks,
                )
                tool_results.append(
                    _tool_result_block(
                        tool_use_id,
                        {"status": "submitted"},
                    )
                )
                continue

            if tool_name == "retrieve_campus_resources":
                search_query = str(tool_input.get("query", request.query)).strip()
                chunks = retrieve_chunks(search_query, settings)
                known_chunks = _merge_chunks(known_chunks, chunks)
                payload = chunks_to_tool_results(chunks)
                tool_results.append(
                    _tool_result_block(
                        tool_use_id,
                        {
                            "resultCount": len(payload),
                            "results": payload,
                        },
                    )
                )
                continue

            tool_results.append(
                _tool_result_block(
                    tool_use_id,
                    {"error": f"Unknown tool: {tool_name}"},
                    is_error=True,
                )
            )

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        if final_response is not None:
            return final_response

    # The cap was reached without the model calling submit_chat_response. Camp fell
    # through to the same fallback silently; the cap is configurable here
    # (MAX_CONVERSE_ITERATIONS, from config.yaml) so this is logged as the distinct
    # event it is - the alternative is a run that looks identical in the logs to a
    # model that answered in one turn.
    logger.warning(
        "Converse loop hit its %s-iteration cap without submit_chat_response; "
        "returning the retrieval fallback. query=%r",
        settings.max_converse_iterations,
        request.query[:80],
    )
    return _fallback_response(request, settings, known_chunks, None)


def _build_converse_messages(
    request: ChatRequest, settings: Settings
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    for item in (request.history or [])[-settings.max_history_messages :]:
        text = item.text.strip()
        if not text:
            continue
        messages.append({"role": item.role, "content": [{"text": text}]})

    messages = _merge_consecutive_roles(messages)

    while messages and messages[0]["role"] == "assistant":
        messages.pop(0)

    messages.append(
        {"role": "user", "content": [{"text": _build_user_message(request)}]}
    )
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


def _build_user_message(request: ChatRequest) -> str:
    parts = [f"Student message:\n{request.query.strip()}"]

    if request.followup:
        parts.append(
            "UI context: The student clicked a follow-up action on an existing resource card. "
            "Answer their question narrowly. Keep cards empty unless they clearly changed topic."
        )

    parts.append(
        "Decide whether to retrieve campus resources, then call submit_chat_response when ready."
    )
    return "\n\n".join(parts)


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


def _response_from_submission(
    tool_input: dict[str, Any],
    *,
    query: str,
    known_chunks: list[RetrievedChunk],
) -> ChatResponse:
    conversational_text = str(tool_input.get("conversationalText", "")).strip()
    submitted_cards = tool_input.get("cards") or []

    if not conversational_text:
        conversational_text = (
            "Here is what I found on campus — take a look at the resources below."
        )

    needs_safety = bool(tool_input.get("needsSafetyHandoff"))
    cards = cards_from_submission(submitted_cards, known_chunks=known_chunks)
    if needs_safety:
        cards = []
    batches = [create_statement_batch(cards, query)] if cards else []

    response = ChatResponse(
        conversationalText=conversational_text,
        statementBatches=batches or None,
        talkToPersonAvailable=True,
    )
    return apply_safety_handoff_to_response(
        response,
        conversational_text=conversational_text,
        requested=needs_safety,
    )


def _fallback_response(
    request: ChatRequest,
    settings: Settings,
    known_chunks: list[RetrievedChunk],
    assistant_message: dict[str, Any] | None,
    *,
    allow_retrieval: bool = True,
) -> ChatResponse:
    """Camp's non-agentic fallback: shape cards from whatever is known, retrieving once
    if the loop never did.

    `allow_retrieval=False` is the deadline path. That last-resort retrieval is a fresh
    network call, so running it after the wall-clock budget is already spent is the one
    thing the deadline exists to prevent - it would push the function past its timeout
    while trying to recover from having run out of time.
    """
    text = _extract_text(assistant_message)

    if known_chunks:
        cards = build_statement_cards(known_chunks, request.query)
    elif allow_retrieval:
        cards = retrieve_statement_cards(request.query, settings)
    else:
        cards = []

    if not cards and not text:
        text = (
            "I pulled together a few campus resources that may help — "
            "scroll the cards for offices, links, and next steps."
        )
    elif not text:
        text = (
            "I can help you find SJSU campus resources. "
            "Tell me a bit more about what you need and I'll point you in the right direction."
        )

    batches = [create_statement_batch(cards, request.query)] if cards else []
    response = ChatResponse(
        conversationalText=text,
        statementBatches=batches or None,
        talkToPersonAvailable=True,
    )
    return apply_safety_handoff_to_response(
        response,
        conversational_text=text,
        requested=False,
    )


def _extract_text(assistant_message: dict[str, Any] | None) -> str:
    if not assistant_message:
        return ""

    parts: list[str] = []
    for block in assistant_message.get("content", []):
        if "text" in block:
            parts.append(block["text"])

    return "\n".join(parts).strip()


def _merge_chunks(
    existing: list[RetrievedChunk],
    incoming: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    best: dict[str, RetrievedChunk] = {}

    for chunk in existing + incoming:
        key = chunk.source_url or chunk.s3_uri or chunk.text[:80]
        current = best.get(key)
        if current is None or chunk.score > current.score:
            best[key] = chunk

    merged = list(best.values())
    merged.sort(key=lambda chunk: chunk.score, reverse=True)
    return merged

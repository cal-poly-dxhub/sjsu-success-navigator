"""The Converse tool-use loop. One tool (retrieval), one exit (the model's text reply).

WHAT CHANGED FROM v1. The loop used to have two exits and two card builders. The model could
end by calling `submit_chat_response` with a JSON cards array, or by just talking, and every
abnormal path - deadline, iteration cap, a model that never submitted - fell through to
`_fallback_response`, which built cards MECHANICALLY out of retrieved page text. That is why
a timeout produced cards nobody had written: the fallback answered from the corpus rather
than from the conversation.

Now the model's text reply is the answer, on every path. When there is text, cards.py parses
it. When there is no text at all - the deadline landed before the model produced any - the
student gets one honest sentence rather than machine-assembled referrals. Retrieval no longer
has a second, card-shaped consumer, so a run that ends early degrades to less, never to
something invented.

The two caps and their ordering are unchanged, and both still exist for the reason the
docstring below gives.
"""

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
    TurnSources,
    cards_from_parsed,
    create_statement_batch,
    join_prose,
    parse_model_response,
    source_options_for_tool,
    strip_card_tags,
)
from prompts import build_system_prompt
from retrieve import retrieve_chunks
from safety import SAFETY_FALLBACK_TEXT, apply_safety_handoff_to_response
from tools import TOOL_CONFIG

logger = logging.getLogger(__name__)

# The ONLY hardcoded reply left on this path, and it is reachable in exactly one situation:
# the model produced no text whatsoever before the loop ran out of time or iterations. An
# empty bubble is the alternative, so this is not a substitute for an answer - it is an
# admission that there is not one.
_NO_OUTPUT_TEXT = (
    "I ran out of time putting that together. Ask me again and I'll take another run at it."
)

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

    Both caps exit through _response_from_text with whatever text the model had produced by
    then. That is the point: the alternative is being killed mid-Converse, where the
    invocation is billed and the student gets a gateway 504 carrying no response at all.
    """
    client = _bedrock_client(settings.bedrock_region)

    if deadline is None:
        deadline = time.monotonic() + settings.converse_deadline_seconds

    # The id-to-URL map for this turn. Built here, never persisted, and the only thing that
    # can put a URL on a card - which is what makes a model-invented URL unrepresentable.
    sources = TurnSources()
    messages = _build_converse_messages(request, settings)
    system_prompt = build_system_prompt(settings)
    last_text = ""

    for iteration in range(settings.max_converse_iterations):
        # Checked BEFORE the call, not after: the point is never to start a Converse
        # request that cannot finish inside the function's remaining time.
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

        response = client.converse(
            modelId=settings.generation_model_id,
            system=[{"text": system_prompt}],
            messages=messages,
            toolConfig=TOOL_CONFIG,
            inferenceConfig={
                "maxTokens": settings.generation_max_tokens,
                "temperature": settings.generation_temperature,
            },
        )

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
        # toolUse block is malformed rather than final, but there is nothing further to do
        # with it either, so it takes the same exit.
        if response.get("stopReason") != "tool_use" or not tool_uses:
            return _response_from_text(text, sources, request.query, settings)

        tool_results: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            tool_results.append(
                _run_tool(tool_use, sources=sources, request=request, settings=settings)
            )

        messages.append({"role": "user", "content": tool_results})

    # The cap was reached without the model ever ending its turn. Camp fell through to a
    # mechanical card builder here; now the student gets whatever the model actually said,
    # which may be nothing. Logged as the distinct event it is - the alternative is a run
    # that looks identical in the logs to a model that answered on its first call.
    logger.warning(
        "Converse loop hit its %s-iteration cap without a final reply; answering from the "
        "text produced so far (%s chars). query=%r",
        settings.max_converse_iterations,
        len(last_text),
        request.query[:80],
    )
    return _response_from_text(last_text, sources, request.query, settings)


def _run_tool(
    tool_use: dict[str, Any],
    *,
    sources: TurnSources,
    request: ChatRequest,
    settings: Settings,
) -> dict[str, Any]:
    tool_name = tool_use["name"]
    tool_use_id = tool_use["toolUseId"]
    tool_input = tool_use.get("input") or {}

    if tool_name != "retrieve_campus_resources":
        return _tool_result_block(
            tool_use_id, {"error": f"Unknown tool: {tool_name}"}, is_error=True
        )

    search_query = str(tool_input.get("query", request.query)).strip()
    chunks = retrieve_chunks(search_query, settings)
    options = sources.add_chunks(chunks, limit=settings.card_max_retrieval_results)

    return _tool_result_block(
        tool_use_id,
        {
            "resultCount": len(options),
            "results": source_options_for_tool(options),
        },
    )


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
    """The user turn handed to the model. It does NOT read `request.followup`.

    This used to append a "the student clicked a follow-up, emit no cards" note, which is
    why clicking Tell me more never produced cards while typing the same question did. A
    click sends text the model itself authored, down the same route as typed input - same
    intercept, same guardrail, same history - so the turn it produces has to be the same
    turn. What an answer needs (a destination, a source, neither) is a property of the
    question, not of the widget that sent it.

    `followup` stays on the wire contract (models.ChatRequest, and the frontend still sets
    it) but no longer reaches the prompt from here.
    """
    return (
        f"Student message:\n{request.query.strip()}"
        "\n\n"
        "Retrieve campus resources if you need them, then write your reply."
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
    """Parse one model reply into the wire response. The only place cards come from.

    The reply reaches the student in the order it was written: `conversationalText` is the
    prose above the card group and `trailingText` the prose below it, so a closing question
    lands under the cards it refers to. Both fall back to one bubble - the safety branch and
    the zero-card branch below each have a reason there is nothing left to split around.

    The zero-card branch is the contract's fallback, and it is why a parse failure cannot
    lose anything: when no card survives, the bubble is rebuilt from the COMPLETE reply with
    the tags scrubbed, rather than from the text that happened to sit outside the blocks. So
    a malformed card - an unclosed tag, a block with no title - reaches the student as the
    model's words instead of vanishing.
    """
    parsed = parse_model_response(text)

    if parsed.needs_safety:
        # A safety turn carries no cards by contract. Any the model emitted anyway are
        # dropped deliberately, so this must NOT take the zero-card fallback below - that
        # would fold the card text back into the bubble. An empty prose falls back to the
        # server's one authored sentence: the panel needs an introduction, not silence.
        #
        # The reply is also un-split here: with the cards gone there is nothing for trailing
        # prose to sit under, and the panel's placement - directly under the whole message,
        # never buried between two halves of it - is a safety property, not a layout one.
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
            # is no position left to preserve: with no grid on screen, prose that was
            # written under the cards is just the end of the message.
            prose, trailing = strip_card_tags(text), ""

    if not prose and not trailing:
        logger.warning("Model produced no usable text for query=%r", query[:80])
        prose = _NO_OUTPUT_TEXT

    batches = [create_statement_batch(cards, query)] if cards else []
    response = ChatResponse(
        conversationalText=prose,
        trailingText=trailing or None,
        statementBatches=batches or None,
        talkToPersonAvailable=True,
    )
    # The whole message is screened, both sides of the split: a hotline named under the
    # cards has to attach the panel exactly as one named above them.
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

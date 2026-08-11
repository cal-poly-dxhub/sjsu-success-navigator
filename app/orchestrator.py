"""The Converse loop: retrieval primed on every turn, the tool as escape hatch, one exit.

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
from typing import Any, Sequence

import boto3
from botocore.config import Config

from settings import Settings
from history import StoredMessage
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
from usage import TurnUsage

logger = logging.getLogger(__name__)

# The ONLY hardcoded reply left on this path, and it is reachable in exactly one situation:
# the model produced no text whatsoever before the loop ran out of time or iterations. An
# empty bubble is the alternative, so this is not a substitute for an answer - it is an
# admission that there is not one.
_NO_OUTPUT_TEXT = (
    "I ran out of time putting that together. Ask me again and I'll take another run at it."
)

# The toolUseId on the primed retrieval exchange. Server-authored and constant: it never
# reaches the student, and a fixed id keeps the primed turn byte-identical for a given
# query, which is what makes the followup-parity guarantee testable.
_PRIMED_TOOL_USE_ID = "tooluse_primed_first_search"

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
    history: Sequence[StoredMessage] = (),
    deadline: float | None = None,
    usage: TurnUsage | None = None,
) -> ChatResponse:
    """Run the Converse tool-use loop under TWO independent caps.

    `history` is the previous turns, READ FROM DYNAMODB BY THE CALLER (app/handler.py) and
    never taken from the request. It arrives as an argument rather than being fetched here
    so this function stays a pure function of what it is given - which is what lets the
    tests drive a dangling user turn, an empty conversation and a full window without a
    table. The turn's ORDER, including that the user message is written before this runs,
    is the handler's business.

    `max_converse_iterations` bounds how many model calls happen.
    `deadline` bounds how long they may take, as a `time.monotonic()` timestamp. The two
    are not interchangeable: six iterations of a slow model, or one retrieval that stalls,
    exceeds the function's budget without ever reaching the iteration cap. Passing None
    derives the deadline from settings.converse_deadline_seconds; the handler passes an
    explicit one so Lambda's real remaining time can narrow it.

    Both caps exit through _response_from_text with whatever text the model had produced by
    then. That is the point: the alternative is being killed mid-Converse, where the
    invocation is billed and the student gets a gateway 504 carrying no response at all.

    `usage` is the turn's billable tally (app/usage.py), MUTATED IN PLACE and never read.
    It is an argument rather than a return value because every exit here is an exit under a
    cap: a turn that hits the deadline on its third call still billed three, and a tally
    riding on the response would be lost on exactly the paths worth counting. Passing None
    counts nothing, which is what the loop's own tests do.
    """
    client = _bedrock_client(settings.bedrock_region)

    if deadline is None:
        deadline = time.monotonic() + settings.converse_deadline_seconds

    # The id-to-URL map for this turn. Built here, never persisted, and the only thing that
    # can put a URL on a card - which is what makes a model-invented URL unrepresentable.
    sources = TurnSources()
    messages = _build_converse_messages(request, history, settings)
    system_prompt = build_system_prompt(settings)
    last_text = ""

    _prime_first_search(messages, sources=sources, request=request,
                        settings=settings, deadline=deadline, usage=usage)

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

        if usage is not None:
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
        # toolUse block is malformed rather than final, but there is nothing further to do
        # with it either, so it takes the same exit.
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
                )
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


def _prime_first_search(
    messages: list[dict[str, Any]],
    *,
    sources: TurnSources,
    request: ChatRequest,
    settings: Settings,
    deadline: float,
    usage: TurnUsage | None = None,
) -> None:
    """Run the first retrieval server-side and append it as a completed tool exchange.

    Every substantive question needs retrieval exactly once (the 2026-08-10 eval: of the
    turns that logged, 20 of 30 made exactly one search, 2 made two, and the one wrong
    SKIP was a scored failure), so the first search is not the model's decision any more.
    It runs here, on the student's own words, and lands in the transcript as a synthetic
    assistant toolUse plus its toolResult - the exact wire shape a real call produces, so
    the model wakes up holding results and answers in ONE Converse call in the common
    case. The tool stays declared as the escape hatch: a vague follow-up or a missed
    phrasing is the model's cue to search again with a sharper query.

    Two deliberate degradations. Past the deadline nothing runs (the deadline exists so
    no network call starts that cannot finish), and a retrieval failure logs and returns
    rather than failing the turn - the model then simply searches itself, which is the
    pre-priming behaviour. An EMPTY result set is appended, not skipped: results that
    found nothing are the honest-gap signal the prompt teaches from.
    """
    if time.monotonic() >= deadline:
        return
    query = request.query.strip()
    try:
        chunks = retrieve_chunks(query, settings)
        # Counted only once it returned. A retrieval that raised may or may not have been
        # billed, and a meter that guesses in its own favour is not a meter.
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


def _run_tool(
    tool_use: dict[str, Any],
    *,
    sources: TurnSources,
    request: ChatRequest,
    settings: Settings,
    usage: TurnUsage | None = None,
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
) -> list[dict[str, Any]]:
    """Stored history plus this turn's message, in a shape Converse will accept.

    THE NEW MESSAGE IS APPENDED BEFORE THE MERGE, and that ordering is the fix for the reef
    the doc names (docs/accounts-and-storage.md, Reefs). A turn whose model call failed
    leaves a user message with no assistant reply, so the next turn reads a history ENDING
    IN A USER ROLE - and Converse rejects two user messages in a row outright, which would
    make one failed turn poison every turn after it. Merging afterwards folds the dangling
    message into this one, so the disclosure that never got an answer is still in front of
    the model rather than dropped on the floor.

    Trimming here as well as in the query is not a second opinion about the window: it is
    what makes this function total. A caller that hands over more than the configured window
    gets it trimmed rather than silently billed for it.
    """
    messages: list[dict[str, Any]] = []

    for item in list(history)[-settings.max_history_messages :]:
        text = item.text.strip()
        if not text:
            continue
        messages.append({"role": item.role, "content": [{"text": text}]})

    messages.append(
        {"role": "user", "content": [{"text": _build_user_message(request)}]}
    )

    messages = _merge_consecutive_roles(messages)

    # Converse also requires the FIRST message to be a user turn. A window that begins
    # mid-conversation can open on an assistant reply; the loop above always ends on a user
    # message, so this can never empty the list.
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


def _build_user_message(request: ChatRequest) -> str:
    """The user turn handed to the model. It does NOT read `request.followup`.

    This used to append a "the student clicked a follow-up, emit no cards" note, which is
    why clicking Tell me more never produced cards while typing the same question did. A
    click sends text the model itself authored, down the same route as typed input - same
    intercept, same guardrail, same history - so the turn it produces has to be the same
    turn. What an answer needs (a destination, a source, neither) is a property of the
    question, not of the widget that sent it.

    It also no longer says "retrieve if you need them": the first search is primed
    server-side after this message (see _prime_first_search), and on the rare priming
    failure that sentence would be a false promise. The system prompt owns the
    search-again rule; this turn stays instruction-light so it is true on both paths.

    `followup` stays on the wire contract (models.ChatRequest, and the frontend still sets
    it) but no longer reaches the prompt from here.
    """
    return (
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

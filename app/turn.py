"""ONE TURN, with no transport in it.

WHAT THIS IS. Every step a student's question goes through between "the caller has been
identified" and "there is a ChatResponse to send": the daily cap, the input guardrail, the
student's message written, the previous messages read back, the agent loop, the reply
written, and - on a conversation that did not exist a moment ago - the title. It was
app/handler.py's, in the same order, and it is here now because it is about to have a
SECOND caller (the FastAPI app under the Lambda Web Adapter) and this repo already knows
what a second copy of this sequence costs: app/streaming.py and app/stream_worker.py hold
one between them, and every ordering argument below had to be re-made in its docstring.

THE ORDER IS THE WHOLE FILE, and each position was argued for:

  1. rate limit - BEFORE the guardrail, so a refused turn spends one conditional DynamoDB
     write and nothing billable, not even a guardrail text unit.
  2. guardrail   - BEFORE the write, so a blocked message never becomes a turn. Storing it
     would smuggle the attack text into the history the model reads on the NEXT turn, past
     the screen that just caught it.
  3. write the student's message - BEFORE the model call, so a disclosure that then times
     out is still on record. That ordering is the whole reason this is not one write at the
     end.
  4. read the previous N back  - one descending, limited, strongly consistent query,
     excluding the message just written (the orchestrator appends the current turn in
     memory, so reading it back would say it twice).
  5. the model.
  6. write the reply.
  7. on a NEW conversation only, name it - last, after the answer exists, on its own short
     budget. A label can never be allowed to delay or fail a turn, and by this point the
     fallback title is already on the header (app/history.py).

WHY EVERY DEPENDENCY IS AN ARGUMENT. The store, the settings, the Bedrock client, the loop
and the title generator all arrive as parameters rather than module globals, and that is
not ceremony:

  - it is what makes this transport-free. A caller supplies its own clients; nothing here
    reads an environment variable or knows what an event looks like.
  - it is what keeps app/handler.py's 80 existing tests running UNCHANGED. That suite's
    seams are `handler.STORE`, `handler.SETTINGS`, `handler._bedrock_client`,
    `handler.run_chat` and `handler.generate_title`, and handler.py passes each of those
    module globals in by name at call time - so a monkeypatch on the handler still reaches
    the step it has always reached.

A STORAGE FAILURE DOES NOT DENY THE STUDENT AN ANSWER. Each step that touches DynamoDB is
guarded on its own and logs at ERROR: a failed write costs the record of one message and a
failed read costs the context, but refusing to answer would cost a student in front of a
screen the answer itself. The log line is the alarm.
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
    """The daily cap is spent. This turn will not happen, and it is not an error.

    AN EXCEPTION RATHER THAN A RETURN VALUE, because a refusal is the one exit from
    `run_turn` that is not a ChatResponse: it carries a limit, a reset instant and a
    retry-after, and each transport renders those its own way - POST /chat as a 429 with a
    Retry-After header, the socket as an `error` frame. Folding it into the response model
    would put a shape on the wire that no client asks for; returning a two-field result
    object would make every caller unpack a tuple whose second half is almost always None.

    NOTHING WAS WRITTEN AND NOTHING WAS BILLED when this is raised - not even a guardrail
    text unit - which is exactly what the rate limit's position in the order buys.
    """

    def __init__(self, refusal):
        super().__init__(refusal.message)
        self.refusal = refusal


def apply_input_guardrail(query: str, *, settings: Settings, bedrock, usage=None):
    """Screen the BARE student query with ApplyGuardrail(source=INPUT).

    Returns the guardrail's replacement text when it blocks, or None to continue. The
    query alone is screened - not the system prompt, not retrieved passages - because
    PROMPT_ATTACK is about what the student sent.

    A guardrail FAILURE is not a block: if the call itself errors, the request continues
    to the loop rather than refusing a legitimate question over an infrastructure fault.
    Bedrock is already the harder dependency behind it, and a student who hits a transient
    guardrail outage should not be told their question was rejected.

    `usage` is the turn's billable tally (app/usage.py). The text units are taken from the
    guardrail's OWN reported usage rather than counted off the query length, because the
    unit is 1,000 characters of whatever the service decided to screen - and a screen that
    blocked is billed exactly like one that passed, which is why this records before the
    intervention check below.
    """
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
    """This turn's email draft as it will be stored, or None.

    Stored beside the cards and for the same reason: it is what the student was shown. A
    reopened conversation re-renders these exact bytes rather than reassembling them from
    today's config, so a recipient that changes next month does not rewrite what an old
    turn offered.
    """
    if response.escalation is None:
        return None
    return response.escalation.model_dump(by_alias=True)


def stored_place(response: ChatResponse):
    """This turn's location card as it will be stored, or None.

    Stored rather than re-resolved from the key on the way out, for the reason the draft
    beside it is stored: an office that moves next month must not silently rewrite where an
    old turn said it was. What a reopened conversation shows is what the student was shown.
    """
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
    """Name a conversation the model just created. Returns the title, or None.

    THE FALLBACK IS ALREADY WRITTEN when this runs. The first user message put a truncated
    title on the header on its way past (app/history.py), so every path out of here that is
    not a good title - the deadline, a Bedrock error, an unusable reply, a failed write -
    leaves the conversation named rather than nameless. That is why this whole function can
    swallow its failures at INFO instead of failing the turn: there is no state in which
    doing nothing is worse than what was already there.

    Runs AFTER the assistant's reply is written and the answer is in hand, so the title can
    reflect what the conversation turned out to be about rather than only what was asked.
    """
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
) -> ChatResponse:
    """The whole turn, in the order this module's docstring fixes. Raises TurnRefused.

    `user_id` and `client_id` are the CALLER'S to establish - both come out of a validated
    token, never a request body, and nothing here re-checks them. This function trusts what
    it is handed about identity precisely because there is no shape it could be handed that
    would let it check.

    `bedrock_client` is a zero-argument FACTORY rather than a client, so a turn refused by
    the daily cap never builds one. That is the same instinct the rate limit's position has:
    a refused turn should cost as close to nothing as the code can arrange.

    `deadline` is a `time.monotonic()` timestamp the Converse loop must not start a call
    after. `title_deadline_at` is called for the titling budget AFTER the model returns, and
    it is a callable for that reason alone: both are `time.monotonic()` timestamps, so one
    computed up here would already be in the past by the time the title needed it, and every
    new conversation would silently keep its fallback name. A deadline means "from now", and
    for the title, now is twenty seconds later than here.

    `converse` and `make_title` are the agent loop and the titler. They are parameters so a
    caller can hand in its own - which is what app/handler.py does with its own module
    globals, keeping that suite's monkeypatches pointed at the steps they have always been
    pointed at.
    """
    # STEP 1 - the per-user daily cap, before the guardrail screen and before the loop.
    #
    # NOTHING IS WRITTEN AND NO USAGE IS PRODUCED. A refused turn is not a turn - it made no
    # model call, screened nothing, and left no message - so unlike a guardrail block, which
    # billed a screen and reports it, there is genuinely nothing to meter.
    refusal = claim_turn(
        store=store,
        user_id=user_id,
        client_id=client_id,
        settings=settings,
    )
    if refusal is not None:
        raise TurnRefused(refusal)

    # The turn's billable tally, opened before the first thing that spends anything and
    # mutated in place from here down (app/usage.py). It rides out on the response so the
    # cost panel can price the conversation in front of the student from what this
    # conversation actually used, rather than from the sample average in config.yaml.
    usage = TurnUsage()

    # STEP 2 - the guardrail screen. The conversation id is echoed unchanged, because no
    # turn was recorded under it. The usage IS returned: a blocked screen was billed like
    # any other, and a meter that only counts the turns that worked reads low under attack.
    blocked_text = apply_input_guardrail(
        request.query, settings=settings, bedrock=bedrock_client(), usage=usage
    )
    if blocked_text is not None:
        return ChatResponse(
            conversationId=request.conversation_id,
            conversationalText=blocked_text,
            usage=usage,
        )

    # A conversation the CLIENT could not name is one that did not exist a moment ago, and
    # that - not a lookup, not a message count - is what makes this the turn that titles it.
    is_new_conversation = request.conversation_id is None
    conversation_id = request.conversation_id or new_conversation_id()

    # STEP 3 - the student's message, before the model call.
    user_sort_key = None
    try:
        user_sort_key = store.append_message(
            user_id=user_id,
            conversation_id=conversation_id,
            role="user",
            text=request.query.strip(),
        )
    except Exception:
        # No sort key to exclude below. If this was the ambiguous kind of failure - the
        # write landed and the response did not - the read picks the message up and the
        # orchestrator's consecutive-role merge folds it into the copy it appends, so the
        # worst case is one sentence said twice rather than a rejected Converse call.
        logger.exception("Could not record the student's message; answering anyway")

    # STEP 4 - the context read.
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

    # STEP 5 - the model.
    response = converse(request, settings, history=history, deadline=deadline, usage=usage)

    # STEP 6 - the reply.
    try:
        store.append_message(
            user_id=user_id,
            conversation_id=conversation_id,
            role="assistant",
            # THE REPLY AS THE MODEL WROTE IT, tags and all, plus the pairs its cards
            # resolved against. Between them they are the turn, so reopening this
            # conversation re-parses it rather than reassembling it from halves - which is
            # what used to lose the prose the model wrote UNDER its cards.
            text=response.raw_text,
            sources=response.sources,
            # The location card is the exception the re-parse does not cover, and it is
            # recorded for the reason the draft below it is: the `<place>` key survives in
            # the text and would resolve again, but against TODAY'S catalogue. What the
            # student was sent to is what comes back.
            place=stored_place(response),
            escalation=stored_escalation(response),
        )
    except Exception:
        logger.exception("Could not record the assistant's reply; returning it anyway")

    response.conversation_id = conversation_id

    # STEP 7 - the title, on a new conversation only.
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

    # Replaces classify_response_mode, which collapsed a turn into one of three words by
    # reading only the FIRST statement batch. The counts say strictly more and cannot go
    # stale against the response shape.
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

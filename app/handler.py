"""Chat Lambda entrypoint - bare handler, HTTP API payload 2.0. No FastAPI, no Mangum.

Camp's main.py and its routers are replaced by this file; the service modules alongside it
(settings, models, prompts, tools, retrieve, cards, safety, orchestrator) move in as files.

FIVE ROUTES, one function (see lambda_handler):

    POST   /chat                              one turn
    GET    /conversations                     the caller's own conversations
    GET    /conversations/{conversationId}    one conversation, for display
    PATCH  /conversations/{conversationId}    rename it
    DELETE /conversations/{conversationId}    delete it, and every message under it

Every route is the same identity story as the write and shares it literally: `sub` comes
out of the JWT the authorizer validated, the DynamoDB partition is built from it, and a
conversation id belonging to somebody else addresses nothing inside the caller's partition.
That is what makes the two WRITE routes safe on the same terms as the reads: a forged
conversation id renames nothing and deletes nothing, because the only partition either can
reach is the caller's own. Each user managing their OWN history is the whole feature; there
is no staff view here, and no request field that could ask for one.

Request order on POST /chat:

  1. validate    - parse the body, reject a missing or oversized query as a clean 400 before
                   anything is billed.
  2. identity    - the Cognito `sub` from the JWT the API Gateway authorizer already
                   validated. Never from the body.
  3. rate limit  - one atomic conditional write against this user's daily allowance
                   (app/ratelimit.py). BEFORE the guardrail, so a refused turn spends one
                   DynamoDB write and nothing billable - not even a guardrail text unit.
  4. guardrail   - ApplyGuardrail(source=INPUT) on the BARE query, PROMPT_ATTACK only. A block
                   returns the configured message with no retrieval, no generation and
                   nothing written.
  5. the turn    - write the student's message, read the previous N back, call the model,
                   write the reply. HISTORY IS SERVER-AUTHORITATIVE (docs/accounts-and-storage.md,
                   Turn lifecycle): the client sends a conversation id and a message, and
                   nothing it sends can put words in a previous turn's mouth.

Step 5 is the Bedrock Converse tool-use loop under Sammy's system prompt. Safety is the
model's triage call (decision, 2026-08-10): the prompt carries the emergency instruction and
a keyed resource roster, the model emits a <safety> block, and app/safety.py resolves the
keys into the fixed contact panel. There is no pre-model phrase gate.

Wiring comes from env vars set by the CDK stack (see settings.py). The response body is the
camelCase wire contract the frontend expects, produced by the pydantic aliases in models.py.
"""

import base64
import json
import logging
import re
import time

import boto3
from botocore.config import Config

from cards import join_prose, normalise_dashes
from history import ConversationStore, new_conversation_id
from models import (
    CONVERSATION_ID_PATTERN,
    ChatRequest,
    ChatResponse,
    ConversationDeleteResponse,
    ConversationListResponse,
    ConversationMessage,
    ConversationRenameRequest,
    ConversationResponse,
    ConversationSummary,
    ConversationTitleResponse,
    StatementCard,
)
from orchestrator import run_chat
from ratelimit import claim_turn
from settings import load_settings
from titles import generate_title
from usage import TurnUsage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Settings and clients at module scope: resolved once per container, not per request.
# A missing environment variable raises here, on the first invocation, naming the
# variable - rather than surfacing later as a 502 on a student's question.
SETTINGS = load_settings()

# The conversation store. Named here, connected lazily: a cold start that never reaches a
# turn should not pay for a client it does not use. The title cap travels with it so the
# fallback title and the model's are held to one number.
STORE = ConversationStore(
    SETTINGS.chat_history_table_name, title_max_chars=SETTINGS.title_max_chars
)

_BEDROCK_CLIENT = None


def _bedrock_client():
    """The bedrock-runtime client used for ApplyGuardrail. Same client family the agent
    loop uses for Converse, but built here so the guardrail screen does not depend on the
    loop module (which arrives at bullet 6)."""
    global _BEDROCK_CLIENT
    if _BEDROCK_CLIENT is None:
        _BEDROCK_CLIENT = boto3.client(
            "bedrock-runtime",
            region_name=SETTINGS.bedrock_region,
            config=Config(
                retries={"max_attempts": 3, "mode": "adaptive"},
                read_timeout=10,
                connect_timeout=5,
            ),
        )
    return _BEDROCK_CLIENT


# Seconds held back from Lambda's remaining time when deriving the loop's deadline: the
# response still has to be shaped and serialised after the loop returns.
_POST_LOOP_RESERVE_SECONDS = 3


def loop_deadline(context):
    """A `time.monotonic()` timestamp the Converse loop must not start a call after.

    The MINIMUM of two budgets, because each catches what the other misses:

      - the configured one (chat.converse_deadline_seconds) is the intended budget, and
        is what applies in a test or a local run where there is no Lambda context.
      - Lambda's own `get_remaining_time_in_millis()` is the ground truth. It already
        accounts for time this invocation has spent - a slow cold start, a long guardrail
        call - which the static budget cannot see. Documented method, verified against the
        Python context-object reference (2026-08-05).

    Taking the smaller means a slow start SHORTENS the loop's budget rather than letting
    it overrun the function.
    """
    budget = float(SETTINGS.converse_deadline_seconds)

    remaining_ms = getattr(context, "get_remaining_time_in_millis", None)
    if callable(remaining_ms):
        try:
            lambda_budget = (remaining_ms() / 1000.0) - _POST_LOOP_RESERVE_SECONDS
            budget = min(budget, lambda_budget)
        except Exception:
            logger.exception(
                "Could not read Lambda remaining time; using the configured budget"
            )

    return time.monotonic() + budget


# Seconds held back from Lambda's remaining time when deriving the TITLING deadline: the
# response still has to be serialised after the title call returns. Smaller than the loop's
# reserve because by this point the only work left is json.dumps.
_POST_TITLE_RESERVE_SECONDS = 1


def title_deadline(context):
    """A `time.monotonic()` timestamp the titling call must not start after.

    The same minimum-of-two-budgets shape as loop_deadline, and it exists for a stronger
    reason: the student's answer is already written and about to be returned, so a title
    that overran would turn a finished turn into a gateway 504. Taking Lambda's real
    remaining time means a slow answer simply costs the title, which is what the fallback
    is for. A deadline already in the past is not an error - titles.generate_title checks
    it first and does nothing.
    """
    budget = float(SETTINGS.title_deadline_seconds)

    remaining_ms = getattr(context, "get_remaining_time_in_millis", None)
    if callable(remaining_ms):
        try:
            budget = min(
                budget, (remaining_ms() / 1000.0) - _POST_TITLE_RESERVE_SECONDS
            )
        except Exception:
            logger.exception(
                "Could not read Lambda remaining time; using the configured title budget"
            )

    return time.monotonic() + budget


def _parse_body(event):
    """The JSON object body of an HTTP API (payload format 2.0) event, or None if the body is
    absent, not valid JSON, or not a JSON object."""
    body = event.get("body")
    if body is None:
        return None
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _response(status_code, payload, headers=None):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", **(headers or {})},
        "body": json.dumps(payload),
    }


def user_id_from(event):
    """The Cognito `sub`, out of the claims the JWT authorizer validated. Or None.

    THE ONLY PLACE A USER IS IDENTIFIED, and it does not read the body. `sub` is immutable
    and API Gateway has already checked the token's signature, issuer, audience and expiry
    by the time this runs (HTTP API payload 2.0 puts the claims at
    requestContext.authorizer.jwt.claims), so this is a read rather than a decision.

    A body field would be the same value with none of that behind it - a client could put
    anybody's id in it, and every partition key in DynamoDB is built from this. That is
    exactly why ChatRequest has no user field: the convenience and the vulnerability are the
    same line of code.
    """
    return _claim(event, "sub")


def client_id_from(event):
    """The `client_id` claim - WHICH APP CLIENT the caller signed in through. Or None.

    Read from the same validated claim set as `sub` above, and it is there for the same
    reason that set is trustworthy at all: a Cognito ACCESS token carries `client_id` rather
    than `aud`, and API Gateway validates it against the authorizer's audience allowlist
    before this function runs. That list is exactly two entries - the browser's client and
    the eval harness's - so this claim is a validated statement about which of the two
    callers this is, not a self-description.

    Its ONE use is the rate limit's exemption list (app/ratelimit.py). It is never an
    identity: `sub` is the identity, and a client id is shared by everybody who signs in
    through that client. Nothing keys storage on this.

    An ID token would carry `aud` and no `client_id`, so this reads None and the caller falls
    under the limit. That is the safe direction.
    """
    return _claim(event, "client_id")


def _claim(event, name):
    """One claim from the JWT the authorizer validated, or None if it is missing or blank.

    HTTP API payload 2.0 puts them at requestContext.authorizer.jwt.claims. Reading them
    through one function keeps the "this came from the validated token, not the body" story
    in a single place rather than repeated per claim.
    """
    request_context = (event or {}).get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    claims = (authorizer.get("jwt") or {}).get("claims") or {}
    value = claims.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def apply_input_guardrail(query, usage=None):
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
        result = _bedrock_client().apply_guardrail(
            guardrailIdentifier=SETTINGS.input_guardrail_id,
            guardrailVersion=SETTINGS.input_guardrail_version,
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


def _chat_response(response):
    """Serialise a ChatResponse through its pydantic aliases - the camelCase wire
    contract camp's frontend reads."""
    return _response(200, response.model_dump(by_alias=True))


def _stored_cards(response):
    """This turn's cards as they will be stored: resolved URLs, one flat list.

    A turn makes exactly one card group, so flattening the batches loses nothing. These are
    for a display read that does not exist yet - the model is never shown them again.
    """
    return [
        card.model_dump(by_alias=True)
        for batch in (response.statement_batches or [])
        for card in batch.cards
    ]


def name_new_conversation(
    *, user_id, conversation_id, question, answer, deadline, usage=None
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
        title = generate_title(
            question=question,
            answer=answer,
            settings=SETTINGS,
            deadline=deadline,
            usage=usage,
        )
        if title is None:
            return None
        if not STORE.set_generated_title(
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


def run_turn(request, user_id, deadline, context=None, usage=None):
    """One turn against the store, in the order docs/accounts-and-storage.md fixes.

      1. write the student's message. BEFORE the model call, so a disclosure that then
         times out is still on record. That ordering is the whole reason this is not one
         write at the end.
      2. read the previous N back - one descending, limited, strongly consistent query,
         excluding the message just written (the orchestrator appends the current turn in
         memory, so reading it back would say it twice).
      3. call the model.
      4. write the reply.
      5. on a NEW conversation only, name it (app/titles.py). Last, after the answer
         exists, and on its own short budget: a label can never be allowed to delay or
         fail a turn, and by this point the fallback title is already on the header.

    A STORAGE FAILURE DOES NOT DENY THE STUDENT AN ANSWER. Each step is guarded on its own
    and logs at ERROR: a write that fails costs the record of one message, and a read that
    fails costs the context, but refusing to answer would cost a student in front of a
    screen the answer itself. Same posture as the guardrail outage above, for the same
    reason - and like that one, the log line is the alarm.

    `usage` is the tally the caller opened before the guardrail screen. It is attached to
    the response HERE, after the titling call, so the model calls this turn actually made -
    the loop's, plus the small one that named a new conversation - are all in it.
    """
    # A conversation the CLIENT could not name is one that did not exist a moment ago, and
    # that - not a lookup, not a message count - is what makes this the turn that titles it.
    is_new_conversation = request.conversation_id is None
    conversation_id = request.conversation_id or new_conversation_id()

    user_sort_key = None
    try:
        user_sort_key = STORE.append_message(
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

    try:
        history = STORE.recent_messages(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=SETTINGS.max_history_messages,
            exclude_sort_key=user_sort_key,
        )
    except Exception:
        logger.exception("Could not read conversation history; answering without it")
        history = []

    response = run_chat(
        request, SETTINGS, history=history, deadline=deadline, usage=usage
    )

    try:
        STORE.append_message(
            user_id=user_id,
            conversation_id=conversation_id,
            role="assistant",
            # The prose the model wrote, both sides of the card group, with the card tags
            # already resolved out of it. Cards ride alongside as their own attribute; what
            # goes back to the model on the next turn is this text and nothing else.
            text=join_prose(response.conversational_text, response.trailing_text),
            cards=_stored_cards(response),
        )
    except Exception:
        logger.exception("Could not record the assistant's reply; returning it anyway")

    response.conversation_id = conversation_id
    # THE TITLE DEADLINE IS DERIVED HERE, not alongside the loop's. Both are
    # `time.monotonic()` timestamps, so one computed before a twenty-second model call would
    # already be in the past by the time the title needed it, and every new conversation
    # would silently keep its fallback name. A deadline means "from now", and now is here.
    if is_new_conversation:
        response.title = name_new_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            question=request.query,
            answer=join_prose(response.conversational_text, response.trailing_text),
            deadline=title_deadline(context),
            usage=usage,
        )
    response.usage = usage
    return response


def post_chat(event, context):
    """POST /chat, in the order the module docstring fixes: validate, identity, rate limit,
    guardrail, turn. Safety handoffs come out of the loop - the model triages and emits keys,
    the server resolves them into the fixed panel (app/safety.py)."""
    data = _parse_body(event)
    query = (data or {}).get("query")
    if not isinstance(query, str) or not query.strip():
        return _response(400, {"error": "Missing 'query' in request body."})
    if len(query) > SETTINGS.max_query_chars:
        return _response(
            400,
            {"error": f"Query exceeds {SETTINGS.max_query_chars} characters."},
        )

    # The body, reduced to the two things a client is allowed to say. Anything else it sent
    # - a `history` array, a `messages` array, a user id - is an unknown key and pydantic
    # drops it here, which is the last point at which it exists.
    try:
        request = ChatRequest.model_validate(data)
    except Exception:
        logger.exception("Invalid chat request body")
        return _response(400, {"error": "Invalid request body."})

    # STEP 2 - identity. The route is authorizer-gated, so a request arriving without a
    # `sub` is a misconfigured stack or a direct invoke, not a student. Failing closed
    # rather than answering anonymously: every partition key is built from this claim, so
    # there is nowhere to put the turn and no one to attribute it to.
    user_id = user_id_from(event)
    if user_id is None:
        logger.error("A /chat request carried no JWT sub claim; refusing it")
        return _response(401, {"error": "Unauthenticated."})

    # STEP 3 - the per-user daily cap, BEFORE the guardrail screen and before the loop. That
    # ordering is what makes this a spend guard: a refused turn costs one conditional
    # DynamoDB write and nothing else, where a check after the guardrail would still bill a
    # content-filter text unit for every message an over-limit account sent.
    #
    # NOTHING IS WRITTEN AND NO USAGE IS RETURNED. A refused turn is not a turn - it made no
    # model call, screened nothing, and left no message - so unlike a guardrail block, which
    # billed a screen and reports it, there is genuinely nothing to meter. The conversation
    # id is not echoed either: no turn was recorded under it.
    refusal = claim_turn(
        store=STORE,
        user_id=user_id,
        # The app client the token was issued to, from the same validated claims as `sub`.
        # The eval harness's machine client is exempt; a browser cannot claim that.
        client_id=client_id_from(event),
        settings=SETTINGS,
    )
    if refusal is not None:
        return _response(
            429,
            {
                "error": refusal.message,
                "limit": refusal.limit,
                # The reset INSTANT, not a duration and not a local time. The browser turns
                # this into the student's own clock (frontend/src/lib/chatApi.ts), which is
                # the only place that knows what timezone they are in.
                "resetAt": refusal.reset_at_iso,
                "retryAfterSeconds": refusal.retry_after_seconds,
            },
            # The standard header, for the clients that are not the browser: eval/run_eval.py
            # and anything else driving this endpoint programmatically already understand it,
            # and it costs nothing to be correct for them.
            headers={"Retry-After": str(refusal.retry_after_seconds)},
        )

    # The turn's billable tally, opened before the first thing that spends anything and
    # mutated in place from here down (app/usage.py). It rides out on the response so the
    # cost panel can price the conversation in front of the student from what this
    # conversation actually used, rather than from the sample average in config.yaml.
    usage = TurnUsage()

    # STEP 4 - the guardrail screen. NOTHING IS WRITTEN ON A BLOCK, and that is deliberate:
    # a blocked message never became a turn, and storing it would smuggle the attack text
    # into the history the model reads on the NEXT turn - past the screen that just caught
    # it. The conversation id is echoed unchanged, because no turn was recorded under it.
    # The usage IS returned: a blocked screen was billed like any other, and a meter that
    # only counts the turns that worked is a meter that reads low under attack.
    blocked_text = apply_input_guardrail(query, usage=usage)
    if blocked_text is not None:
        return _chat_response(
            ChatResponse(
                conversationId=request.conversation_id,
                conversationalText=blocked_text,
                usage=usage,
            )
        )

    # STEP 5 - the turn: write, read, model, write. Under both loop caps (iterations and
    # wall clock).
    try:
        response = run_turn(
            request,
            user_id,
            deadline=loop_deadline(context),
            context=context,
            usage=usage,
        )
    except Exception:
        # The student gets a plain failure, never a partial or invented answer. The
        # exception itself is logged, not returned: a botocore message can quote the
        # request, and the request here is the student's own words.
        logger.exception("Chat orchestration failed")
        return _response(502, {"error": "The assistant is unavailable right now."})

    # Replaces classify_response_mode, which collapsed a turn into one of three words by
    # reading only the FIRST statement batch. The counts say strictly more and cannot go
    # stale against the response shape.
    logger.info(
        "chat cards=%s safety=%s calls=%s in=%s out=%s",
        sum(len(batch.cards) for batch in (response.statement_batches or [])),
        response.safety_handoff is not None,
        usage.model_calls,
        usage.input_tokens,
        usage.output_tokens,
    )
    return _chat_response(response)


def _display_cards(stored):
    """Stored cards, re-validated through the live card contract.

    A card that no longer matches is DROPPED rather than failing the read, and the reason is
    the same as history.py's unreadable-item skip: the only way one gets here is a shape a
    previous version of this code wrote, and refusing to open a conversation because one old
    card lost a field would be a worse outcome than opening it without that card. The
    WARNING is the alarm.
    """
    cards = []
    for raw in stored or []:
        try:
            cards.append(StatementCard.model_validate(raw))
        except Exception:
            logger.warning("Skipping a stored card that no longer fits the card contract")
    return cards


def get_conversations(event):
    """GET /conversations - the caller's own conversations, most recently active first.

    NO REQUEST BODY AND NO PARAMETERS, deliberately: there is nothing to ask for. The only
    input is the JWT `sub`, so this route cannot be pointed at another student even by a
    caller who wants to.
    """
    user_id = user_id_from(event)
    if user_id is None:
        logger.error("A /conversations request carried no JWT sub claim; refusing it")
        return _response(401, {"error": "Unauthenticated."})

    try:
        summaries = STORE.list_conversations(
            user_id=user_id, limit=SETTINGS.max_conversations_listed
        )
    except Exception:
        # A read failure IS the whole response here - unlike a turn, where the answer
        # matters more than the record - so it is a 502 rather than a silent empty list. An
        # empty list would say "you have no conversations", which is a different and worse
        # lie than "this did not load".
        logger.exception("Could not list conversations")
        return _response(502, {"error": "Could not load your conversations."})

    payload = ConversationListResponse(
        conversations=[
            ConversationSummary(
                conversationId=summary.conversation_id,
                title=summary.title,
                createdAt=summary.created_at,
                lastActivityAt=summary.last_activity_at,
                messageCount=summary.message_count,
            )
            for summary in summaries
        ]
    )
    return _response(200, payload.model_dump(by_alias=True))


def get_conversation(event):
    """GET /conversations/{conversationId} - one conversation, in the DISPLAY projection.

    The id is validated against the same pattern POST /chat validates, for the same reason:
    it goes into a sort-key prefix, so an id carrying a `#` would compose a key prefix the
    server did not intend. That is a 400 rather than a security boundary - the boundary is
    the partition key, which comes from the JWT.

    A WELL-FORMED ID THAT IS NOT THE CALLER'S RETURNS 200 WITH AN EMPTY LIST. Not a 404,
    which would confirm to a prober which ids exist somewhere, and not a 403, which would
    imply this server checked an owner and could have got that check wrong. It reads empty
    because the only partition it can address is the caller's own.
    """
    user_id = user_id_from(event)
    if user_id is None:
        logger.error("A /conversations request carried no JWT sub claim; refusing it")
        return _response(401, {"error": "Unauthenticated."})

    conversation_id = _conversation_id_from(event)
    if conversation_id is None:
        return _response(400, {"error": "Malformed conversation id."})

    try:
        messages = STORE.conversation_messages(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=SETTINGS.max_conversation_messages,
        )
    except Exception:
        logger.exception("Could not read a conversation")
        return _response(502, {"error": "Could not load that conversation."})

    payload = ConversationResponse(
        conversationId=conversation_id,
        messages=[
            ConversationMessage(
                role=message.role,
                text=message.text,
                cards=_display_cards(message.cards),
                createdAt=message.created_at,
            )
            for message in messages
        ],
    )
    return _response(200, payload.model_dump(by_alias=True))


def _conversation_id_from(event):
    """The validated `conversationId` path parameter, or None.

    Shared by the three routes that take one, because the validation is the same on all of
    them and the reason is the same: the value composes a DynamoDB sort-key prefix, so an id
    carrying a `#` would address keys the server did not intend. A 400 rather than a
    security boundary - the boundary is the partition key, which comes from the JWT.
    """
    conversation_id = ((event or {}).get("pathParameters") or {}).get("conversationId")
    if not isinstance(conversation_id, str) or not re.match(
        CONVERSATION_ID_PATTERN, conversation_id
    ):
        return None
    return conversation_id


def patch_conversation(event):
    """PATCH /conversations/{conversationId} - the student renames their own chat.

    THE RENAME IS THE ONE PLACE A TITLE IS NOT THE SERVER'S IDEA, and the store records that
    with `userTitled`, which model titling is forbidden from writing over. A name a student
    chose is theirs.

    Dashes are normalised on the way in, through the same function the card path uses. Not
    censorship of what a student may type: it is the one display invariant this app holds
    everywhere (docs/cards-v2.md), and a sidebar row is display.

    A 404 here is not an existence oracle. The only header this can address is one inside
    the caller's own partition, so "no such conversation" means no such conversation OF
    YOURS - which the caller already knew, since they were looking at their own list.
    """
    user_id = user_id_from(event)
    if user_id is None:
        logger.error("A conversation rename carried no JWT sub claim; refusing it")
        return _response(401, {"error": "Unauthenticated."})

    conversation_id = _conversation_id_from(event)
    if conversation_id is None:
        return _response(400, {"error": "Malformed conversation id."})

    data = _parse_body(event)
    try:
        rename = ConversationRenameRequest.model_validate(data or {})
    except Exception:
        return _response(400, {"error": "Missing 'title' in request body."})

    title = " ".join(normalise_dashes(rename.title).split())
    if not title:
        return _response(400, {"error": "A conversation title cannot be blank."})
    if len(title) > SETTINGS.title_max_chars:
        # Rejected rather than truncated: these are the student's own words, and a name
        # silently shortened is a name they did not choose.
        return _response(
            400,
            {"error": f"A title must be {SETTINGS.title_max_chars} characters or fewer."},
        )

    try:
        renamed = STORE.rename_conversation(
            user_id=user_id, conversation_id=conversation_id, title=title
        )
    except Exception:
        logger.exception("Could not rename a conversation")
        return _response(502, {"error": "Could not rename that conversation."})

    if not renamed:
        return _response(404, {"error": "No such conversation."})

    payload = ConversationTitleResponse(conversationId=conversation_id, title=title)
    return _response(200, payload.model_dump(by_alias=True))


def delete_conversation(event):
    """DELETE /conversations/{conversationId} - hard delete, messages first.

    IDEMPOTENT, and deliberately not a 404 on a conversation that is not there. A delete
    says what the caller wants the world to look like afterwards, and afterwards is the same
    either way; a second click, a retried request, or a forged id all leave nothing to
    delete and nothing to report. It also means this route cannot be used to ask which ids
    exist.

    A FAILURE PARTWAY THROUGH IS A 502 WITH THE HEADER STILL PRESENT. The store deletes
    messages before the header exactly so the recoverable half is the one that survives: the
    conversation is still in the student's list, still deletable, and the retry finishes it.
    """
    user_id = user_id_from(event)
    if user_id is None:
        logger.error("A conversation delete carried no JWT sub claim; refusing it")
        return _response(401, {"error": "Unauthenticated."})

    conversation_id = _conversation_id_from(event)
    if conversation_id is None:
        return _response(400, {"error": "Malformed conversation id."})

    try:
        deleted = STORE.delete_conversation(
            user_id=user_id, conversation_id=conversation_id
        )
    except Exception:
        # The header is still there, because it is deleted last. What the student sees is
        # the conversation they tried to remove, which is the recoverable failure.
        logger.exception("Could not delete a conversation")
        return _response(502, {"error": "Could not delete that conversation."})

    payload = ConversationDeleteResponse(
        conversationId=conversation_id, deletedMessages=deleted
    )
    return _response(200, payload.model_dump(by_alias=True))


# The routes this function serves, spelled as HTTP API payload-2.0 route keys. The stack
# creates exactly these five (infra/infra_stack.py, section 5) and points all of them at
# this function.
_CHAT_ROUTE = "POST /chat"
_CONVERSATIONS_ROUTE = "GET /conversations"
_CONVERSATION_ROUTE = "GET /conversations/{conversationId}"
_CONVERSATION_RENAME_ROUTE = "PATCH /conversations/{conversationId}"
_CONVERSATION_DELETE_ROUTE = "DELETE /conversations/{conversationId}"


def lambda_handler(event, context):
    """Dispatch on the route key API Gateway puts in the event.

    An UNKNOWN route key is a 404 rather than falling through to the chat turn. The stack
    only creates the three routes above, so an unknown one means somebody added a fourth and
    pointed it here - and having that quietly run a billable Bedrock turn on, say, a GET is
    the kind of default that is discovered from an invoice.

    A MISSING route key runs the chat turn: that is a direct invoke (the console, a test
    harness), which is what this function did before it had more than one route.
    """
    route = (event or {}).get("routeKey")
    if not isinstance(route, str) or not route.strip():
        return post_chat(event, context)

    route = route.strip()
    if route == _CHAT_ROUTE:
        return post_chat(event, context)
    if route == _CONVERSATIONS_ROUTE:
        return get_conversations(event)
    if route == _CONVERSATION_ROUTE:
        return get_conversation(event)
    if route == _CONVERSATION_RENAME_ROUTE:
        return patch_conversation(event)
    if route == _CONVERSATION_DELETE_ROUTE:
        return delete_conversation(event)

    logger.error("No handler for route %r", route)
    return _response(404, {"error": "Not found."})

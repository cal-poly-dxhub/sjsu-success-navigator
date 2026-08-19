"""Chat Lambda entrypoint: bare handler, HTTP API payload 2.0. No FastAPI, no Mangum.

Five routes on one function, and on POST /chat the step order is load-bearing; see
docs/chat-service.md, The request path.
"""

import base64
import json
import logging
import re
import time

import boto3
from botocore.config import Config

from cards import normalise_dashes
# `new_conversation_id` is not called in this file any more - the turn that mints one moved
# to app/turn.py. It stays imported because it is part of this module's surface: the id
# format is the handler's contract with the client (models.CONVERSATION_ID_PATTERN validates
# what comes back in), and the suite mints ids through it.
from history import ConversationStore, new_conversation_id
from models import (
    CONVERSATION_ID_PATTERN,
    ChatRequest,
    ConversationDeleteResponse,
    ConversationListResponse,
    ConversationMessage,
    ConversationRenameRequest,
    ConversationResponse,
    ConversationSummary,
    ConversationTitleResponse,
    EmailDraft,
    PlaceCard,
    StatementCard,
)
from orchestrator import replay_stored_reply, run_chat
from settings import load_settings
from titles import generate_title
# The turn, lifted out of this file: everything between an identified caller and a
# ChatResponse. `run_chat` and `generate_title` above are imported for the same reason
# they always were - they are handed to it, so this module stays the place they are
# named and therefore the place the test suite patches them.
from turn import TurnRefused, run_turn

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Resolved once per container. A missing environment variable raises here, naming it.
SETTINGS = load_settings()

# Named here, connected lazily. The title cap travels with it so one number names a chat.
STORE = ConversationStore(
    SETTINGS.chat_history_table_name, title_max_chars=SETTINGS.title_max_chars
)

_BEDROCK_CLIENT = None


def _bedrock_client():
    """The bedrock-runtime client used for ApplyGuardrail, built once per container."""
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


# Held back from Lambda's remaining time: the response still has to be serialised.
_POST_LOOP_RESERVE_SECONDS = 3


def loop_deadline(context):
    """A `time.monotonic()` timestamp the Converse loop must not start a call after."""
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


# Held back from Lambda's remaining time. Smaller: only json.dumps is left after this.
_POST_TITLE_RESERVE_SECONDS = 1


def title_deadline(context):
    """A `time.monotonic()` timestamp the titling call must not start after."""
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
    """The JSON object body of an HTTP API event, or None if it is absent or not an object."""
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
    """The Cognito `sub`, out of the claims the JWT authorizer validated. Or None."""
    return _claim(event, "sub")


def client_id_from(event):
    """The `client_id` claim: which app client the caller signed in through, or None."""
    return _claim(event, "client_id")


def _claim(event, name):
    """One claim from the JWT the authorizer validated, or None if missing or blank."""
    request_context = (event or {}).get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    claims = (authorizer.get("jwt") or {}).get("claims") or {}
    value = claims.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _chat_response(response):
    """Serialise a ChatResponse through its aliases: the camelCase wire contract."""
    return _response(200, response.model_dump(by_alias=True))


def _display_cards_from(response):
    """A rendered turn's cards as one flat list. A turn makes exactly one card group."""
    return [card for batch in (response.statement_batches or []) for card in batch.cards]


def post_chat(event, context):
    """POST /chat: validate, identity, rate limit, guardrail, turn."""
    data = _parse_body(event)
    query = (data or {}).get("query")
    if not isinstance(query, str) or not query.strip():
        return _response(400, {"error": "Missing 'query' in request body."})
    if len(query) > SETTINGS.max_query_chars:
        return _response(
            400,
            {"error": f"Query exceeds {SETTINGS.max_query_chars} characters."},
        )

    try:
        request = ChatRequest.model_validate(data)
    except Exception:
        logger.exception("Invalid chat request body")
        return _response(400, {"error": "Invalid request body."})

    user_id = user_id_from(event)
    if user_id is None:
        logger.error("A /chat request carried no JWT sub claim; refusing it")
        return _response(401, {"error": "Unauthenticated."})

    # STEPS 3 TO 5 - THE TURN ITSELF, and it is no longer in this file. Rate limit,
    # guardrail, write the student's message, read the previous ones back, call the model,
    # write the reply, name a new conversation: all of it moved to app/turn.py, in the same
    # order, doing the same things and logging the same line. What stays here is the part
    # that is about HTTP - the shapes the two non-answer exits take on the wire.
    #
    # IT MOVED BECAUSE IT IS ABOUT TO HAVE A SECOND CALLER, and this repo already knows
    # what a second copy costs: app/streaming.py and app/stream_worker.py hold one between
    # them, and every ordering argument had to be made twice.
    #
    # EVERY DEPENDENCY IS PASSED BY NAME OUT OF THIS MODULE'S GLOBALS, which is deliberate
    # rather than ceremonial. SETTINGS, STORE, _bedrock_client, run_chat and generate_title
    # are the seams this function's own test suite patches; resolving them HERE, at call
    # time, is what keeps a monkeypatch on this module reaching the step it always reached.
    try:
        response = run_turn(
            request,
            user_id=user_id,
            # The app client the token was issued to, from the same validated claims as
            # `sub`. The eval harness's machine client is exempt from the daily cap; a
            # browser cannot claim that.
            client_id=client_id_from(event),
            settings=SETTINGS,
            store=STORE,
            # The FACTORY, not a client: a turn refused by the daily cap must not build one.
            bedrock_client=_bedrock_client,
            deadline=loop_deadline(context),
            # A CALLABLE, evaluated after the model returns. Both are time.monotonic()
            # stamps, so a title deadline computed here would already be in the past by the
            # time the title needed it, and every new conversation would keep its fallback.
            title_deadline_at=lambda: title_deadline(context),
            converse=run_chat,
            make_title=generate_title,
        )
    except TurnRefused as refused:
        # THE DAILY CAP, spelled as HTTP. Nothing was written, nothing was billed and no
        # usage is returned - a refused turn is not a turn. The conversation id is not
        # echoed either: no turn was recorded under it.
        refusal = refused.refusal
        return _response(
            429,
            {
                "error": refusal.message,
                "limit": refusal.limit,
                # The reset INSTANT; the browser renders it in the student's own clock.
                "resetAt": refusal.reset_at_iso,
                "retryAfterSeconds": refusal.retry_after_seconds,
            },
            headers={"Retry-After": str(refusal.retry_after_seconds)},
        )
    except Exception:
        # Logged, not returned: a botocore message can quote the request, and the request
        # here is the student's own words.
        logger.exception("Chat orchestration failed")
        return _response(502, {"error": "The assistant is unavailable right now."})

    # A GUARDRAIL BLOCK ARRIVES HERE TOO, as an ordinary ChatResponse carrying the
    # guardrail's replacement text and the usage that screen billed. It is a 200: the
    # server answered, and what it answered with is the refusal.
    return _chat_response(response)


def _display_cards(stored):
    """Stored cards, re-validated through the live contract. One that no longer fits is dropped."""
    cards = []
    for raw in stored or []:
        try:
            cards.append(StatementCard.model_validate(raw))
        except Exception:
            logger.warning("Skipping a stored card that no longer fits the card contract")
    return cards


def _display_escalation(stored):
    """A stored email draft, re-validated. None if it no longer fits the live contract."""
    if not stored:
        return None
    try:
        return EmailDraft.model_validate(stored)
    except Exception:
        logger.warning("Skipping a stored escalation draft that no longer fits its contract")
        return None


def _display_place(stored):
    """A stored location card, re-validated. None if it no longer fits the live contract."""
    if not stored:
        return None
    try:
        return PlaceCard.model_validate(stored)
    except Exception:
        logger.warning("Skipping a stored location card that no longer fits its contract")
        return None


def get_conversations(event):
    """GET /conversations: the caller's own, most recently active first. No parameters."""
    user_id = user_id_from(event)
    if user_id is None:
        logger.error("A /conversations request carried no JWT sub claim; refusing it")
        return _response(401, {"error": "Unauthenticated."})

    try:
        summaries = STORE.list_conversations(
            user_id=user_id, limit=SETTINGS.max_conversations_listed
        )
    except Exception:
        # A read failure IS the whole response here, so a 502 rather than an empty list:
        # "you have no conversations" is a worse lie than "this did not load".
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
    """GET /conversations/{conversationId}: one conversation, in the DISPLAY projection."""
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

    return _response(
        200,
        ConversationResponse(
            conversationId=conversation_id,
            messages=_rendered_messages(messages),
        ).model_dump(by_alias=True),
    )


def _rendered_messages(messages):
    """Stored rows as the browser renders them, oldest first. The question travels with
    the answer, because that is what a card group is labelled with."""
    rendered = []
    question = ""

    for message in messages:
        if message.role == "user":
            question = message.text
            rendered.append(
                ConversationMessage(
                    role="user",
                    # The student's own words, untouched: this server never wrote a
                    # contract for what a student may type.
                    text=message.text,
                    createdAt=message.created_at,
                )
            )
            continue

        rendered.append(_rendered_reply(message, question))
        question = ""

    return rendered


def _rendered_reply(message, question):
    """One stored assistant message, rendered. A row carrying `cards` is the legacy shape
    and is handed back as it always was; everything else is re-parsed."""
    if message.cards:
        return ConversationMessage(
            role="assistant",
            text=message.text,
            cards=_display_cards(message.cards),
            place=_display_place(message.place),
            escalation=_display_escalation(message.escalation),
            createdAt=message.created_at,
        )

    replayed = replay_stored_reply(
        text=message.text,
        urls_by_ref=message.sources,
        # The draft as it was addressed then, not as config would address one today.
        escalation=_display_escalation(message.escalation),
        query=question,
        settings=SETTINGS,
    )
    return ConversationMessage(
        role="assistant",
        text=replayed.conversational_text,
        trailingText=replayed.trailing_text,
        cards=_display_cards_from(replayed),
        safetyHandoff=replayed.safety_handoff,
        # The RECORDED card, not `replayed.place`: a reopened turn must say where the
        # student was sent. The panel above goes the other way deliberately.
        place=_display_place(message.place),
        escalation=replayed.escalation,
        createdAt=message.created_at,
    )


def _conversation_id_from(event):
    """The validated `conversationId` path parameter, or None. It composes a sort-key prefix."""
    conversation_id = ((event or {}).get("pathParameters") or {}).get("conversationId")
    if not isinstance(conversation_id, str) or not re.match(
        CONVERSATION_ID_PATTERN, conversation_id
    ):
        return None
    return conversation_id


def patch_conversation(event):
    """PATCH /conversations/{conversationId}: the student renames their own chat."""
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
        # Rejected rather than truncated: a name silently shortened is one they did not choose.
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
    """DELETE /conversations/{conversationId}: hard delete, messages first."""
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
        # The header is still there, because it is deleted last: the recoverable failure.
        logger.exception("Could not delete a conversation")
        return _response(502, {"error": "Could not delete that conversation."})

    payload = ConversationDeleteResponse(
        conversationId=conversation_id, deletedMessages=deleted
    )
    return _response(200, payload.model_dump(by_alias=True))


# The routes this function serves. The stack creates exactly these five
# (infra/infra/infra_stack.py, section 5) and points all of them at this function.
_CHAT_ROUTE = "POST /chat"
_CONVERSATIONS_ROUTE = "GET /conversations"
_CONVERSATION_ROUTE = "GET /conversations/{conversationId}"
_CONVERSATION_RENAME_ROUTE = "PATCH /conversations/{conversationId}"
_CONVERSATION_DELETE_ROUTE = "DELETE /conversations/{conversationId}"


def lambda_handler(event, context):
    """Dispatch on the route key API Gateway puts in the event."""
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

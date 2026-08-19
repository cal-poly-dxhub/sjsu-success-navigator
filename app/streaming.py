"""The WebSocket streaming path: routes, and the connection record behind them.

Additive and not the default, and what streams is a preview the final payload replaces;
see docs/chat-service.md, Streaming.
"""

from __future__ import annotations

import json
import logging
import os
import time

import boto3
from botocore.config import Config

from history import ConversationStore, new_conversation_id, new_ulid
from models import ChatRequest, ChatResponse
from preview import CARDS_STAGE as PREVIEW_CARDS_STAGE, PreviewSink
from ratelimit import claim_turn
from settings import load_settings
from usage import TurnUsage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SETTINGS = load_settings()

# From the stack, never from the event: assembling a reply's destination out of request
# data is how a request chooses where it is sent. Defaulted, because they exist only when
# the streaming gate is on and settings.py is imported by the chat function too.
MANAGEMENT_ENDPOINT = os.environ.get("STREAM_CALLBACK_URL", "")
WORKER_FUNCTION_NAME = os.environ.get("STREAM_WORKER_FUNCTION_NAME", "")

# Batching, from config.yaml `streaming`. A dropped variable batches rather than per token.
DELTA_MIN_CHARS = int(os.environ.get("STREAM_DELTA_MIN_CHARS") or 160)
DELTA_MAX_DELAY_MS = int(os.environ.get("STREAM_DELTA_MAX_DELAY_MS") or 250)

# The stage a `status` frame carries once the model has started writing card blocks. It is
# DEFINED in app/preview.py, beside the code that decides when to send it, and re-exported
# here because this module is where the socket's wire values are read from.
CARDS_STAGE = PREVIEW_CARDS_STAGE

# A separate instance rather than an import from handler.py, which would pull the whole
# HTTP request path into a function that serves neither.
STORE = ConversationStore(
    SETTINGS.chat_history_table_name, title_max_chars=SETTINGS.title_max_chars
)

# Three hours: API Gateway's own hard cap plus slack, so TTL only collects rows
# $disconnect failed to remove.
CONNECTION_TTL_SECONDS = 3 * 60 * 60


def connection_expiry(now: float | None = None) -> int:
    """The `expiresAt` epoch-second stamp for a connection opened now. Wall clock, not
    monotonic: TTL compares against DynamoDB's own clock."""
    return int((now if now is not None else time.time()) + CONNECTION_TTL_SECONDS)


_MANAGEMENT_CLIENT = None
_LAMBDA_CLIENT = None
_BEDROCK_CLIENT = None


def _lambda_client():
    """The client that starts the generation worker. Built once per container."""
    global _LAMBDA_CLIENT
    if _LAMBDA_CLIENT is None:
        _LAMBDA_CLIENT = boto3.client(
            "lambda",
            config=Config(
                # ONE ATTEMPT. An async invoke retried after the first call was delivered
                # would start a SECOND generation worker, both billed, both on one socket.
                retries={"max_attempts": 1, "mode": "standard"},
                connect_timeout=2,
                read_timeout=5,
            ),
        )
    return _LAMBDA_CLIENT


def _bedrock_client():
    """bedrock-runtime, for the input guardrail screen. The same shape as handler.py's."""
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


def apply_input_guardrail(query, usage=None):
    """ApplyGuardrail(source=INPUT) on the bare query. INPUT SCREENING IS UNCHANGED."""
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
    logger.info("Input guardrail intervened on a streamed query")
    return text


def management_client(endpoint: str):
    """The apigatewaymanagementapi client that pushes frames down one connection."""
    global _MANAGEMENT_CLIENT
    if _MANAGEMENT_CLIENT is None:
        _MANAGEMENT_CLIENT = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=endpoint,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=2,
                read_timeout=5,
            ),
        )
    return _MANAGEMENT_CLIENT


def identity_from(event):
    """The Cognito `sub` for this connection, out of the $connect authorizer's context."""
    request_context = (event or {}).get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    value = authorizer.get("sub")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def client_id_from(event):
    """The `client_id` claim, from the same authorizer context. Never an identity."""
    request_context = (event or {}).get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    value = authorizer.get("clientId")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def connection_id_from(event):
    request_context = (event or {}).get("requestContext") or {}
    value = request_context.get("connectionId")
    return value if isinstance(value, str) and value else None


def is_gone(exc) -> bool:
    """Is this the 410 API Gateway raises when the connection has closed? Read off the code."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        if code in ("GoneException", "410"):
            return True
    return type(exc).__name__ == "GoneException"


class ConnectionSink(PreviewSink):
    """Where one turn's preview goes: batched frames down one WebSocket connection.

    THE PREVIEW LOGIC IS NOT HERE ANY MORE. The offset, the accumulated raw text, the
    batching thresholds, the safe prefix and the card announcement all live in
    app/preview.py, unchanged, because the FastAPI app under the Lambda Web Adapter needs
    exactly the same answers to exactly the same questions and this file's own docstring
    records what having two copies of that bookkeeping cost. What is left here is the wire:
    one method, `_post`.

    BATCHED, NOT PER TOKEN, and that is a cost control rather than a nicety. Every push is a
    billable API Gateway message, so a frame per token would multiply the message count by
    the token count to no visible end - the browser reveals text at ~108 characters a second
    and the model outruns that, so the deltas queue on the client either way. The thresholds
    arrive as arguments from config.yaml (see DELTA_MIN_CHARS above).

    A 410 IS THE STUDENT CLOSING THE TAB, and it stops the pushing and nothing else. The
    turn finishes and is persisted (see stream_worker), because the model call is already
    paid for and because a user message with no assistant reply is the dangling turn
    docs/accounts-and-storage.md calls a reef - the next turn would have to merge it. Coming
    back to a coherent conversation is worth the writes.
    """

    def __init__(self, *, endpoint, connection_id, turn_id, min_chars, max_delay_ms):
        super().__init__(min_chars=min_chars, max_delay_ms=max_delay_ms)
        self._client = management_client(endpoint)
        self._connection_id = connection_id
        self._turn_id = turn_id

    def _post(self, payload) -> bool:
        if self.gone:
            return False
        try:
            self._client.post_to_connection(
                ConnectionId=self._connection_id,
                Data=json.dumps({"turnId": self._turn_id, **payload}).encode("utf-8"),
            )
        except Exception as exc:
            if is_gone(exc):
                logger.info("The student closed the connection; finishing the turn anyway")
                self.gone = True
                return False
            # A real fault, but still not worth the answer: the next frame may land, and
            # the final payload is the one that matters.
            logger.warning("Could not push a streamed frame", exc_info=True)
            return False
        self.frames += 1
        return True


def _ack(status_code=200, message=None):
    """What a WebSocket route integration returns. The body reaches no client on $connect
    or $disconnect, so it exists for the logs and for a direct invoke."""
    return {"statusCode": status_code, "body": json.dumps({"message": message or "ok"})}


def handle_connect(event, store):
    """$connect: the authorizer has already said yes, so this only records the connection."""
    user_id = identity_from(event)
    connection_id = connection_id_from(event)
    if user_id is None or connection_id is None:
        logger.error("A $connect carried no authorizer sub claim; refusing it")
        return _ack(401, "Unauthenticated.")

    store.open_connection(
        user_id=user_id,
        connection_id=connection_id,
        expires_at=connection_expiry(),
    )
    logger.info("ws connect")
    return _ack()


def handle_disconnect(event, store):
    """$disconnect: forget the connection record. Best effort, which is why the record
    carries a TTL rather than relying on this."""
    user_id = identity_from(event)
    connection_id = connection_id_from(event)
    if user_id is None or connection_id is None:
        # Nothing addressable, so nothing to delete. The TTL collects the row either way.
        logger.info("A $disconnect carried no identity; leaving the record to its TTL")
        return _ack()

    store.close_connection(user_id=user_id, connection_id=connection_id)
    logger.info("ws disconnect")
    return _ack()


def _sink_for(event, turn_id):
    return ConnectionSink(
        endpoint=MANAGEMENT_ENDPOINT,
        connection_id=connection_id_from(event),
        turn_id=turn_id,
        min_chars=DELTA_MIN_CHARS,
        max_delay_ms=DELTA_MAX_DELAY_MS,
    )


def handle_message(event, store):
    """The message route: the HTTP handler's order in full, then a hand-off."""
    connection_id = connection_id_from(event)
    user_id = identity_from(event)
    if user_id is None or connection_id is None:
        logger.error("A message frame carried no authorizer sub claim; refusing it")
        return _ack(401, "Unauthenticated.")

    # Carried on every frame of this turn, so a reply that arrives late, or two turns racing
    # on one connection, lands on the right bubble or is discarded.
    turn_id = new_ulid()
    sink = _sink_for(event, turn_id)

    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        body = None
    if not isinstance(body, dict):
        sink.error("That message could not be read.")
        return _ack(400, "Malformed frame.")

    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        sink.error("Ask me a question and I'll take a look.")
        return _ack(400, "Missing query.")
    if len(query) > SETTINGS.max_query_chars:
        sink.error(f"That message is longer than {SETTINGS.max_query_chars} characters.")
        return _ack(400, "Query too long.")

    # The same model the HTTP route validates through, so a `history` array or a user id is
    # an unknown key and is dropped rather than sanitised.
    try:
        request = ChatRequest.model_validate(
            {
                "query": query,
                "conversationId": body.get("conversationId"),
                "followup": bool(body.get("followup", False)),
            }
        )
    except Exception:
        logger.exception("Invalid streaming chat request")
        sink.error("That message could not be read.")
        return _ack(400, "Invalid request.")

    refusal = claim_turn(
        store=store,
        user_id=user_id,
        client_id=client_id_from(event),
        settings=SETTINGS,
    )
    if refusal is not None:
        # A definite answer, not a socket failure: the client renders it rather than retrying
        # over POST /chat and being refused again.
        sink.error(
            refusal.message,
            limit=refusal.limit,
            resetAt=refusal.reset_at_iso,
            retryAfterSeconds=refusal.retry_after_seconds,
        )
        return _ack(429, "Daily limit reached.")

    usage = TurnUsage()

    blocked_text = apply_input_guardrail(query, usage=usage)
    if blocked_text is not None:
        # Nothing written and no worker started. The usage rides along: the screen was billed.
        sink.final(
            ChatResponse(
                conversationId=request.conversation_id,
                conversationalText=blocked_text,
                usage=usage,
            ).model_dump(by_alias=True)
        )
        return _ack()

    is_new_conversation = request.conversation_id is None
    conversation_id = request.conversation_id or new_conversation_id()

    user_sort_key = None
    try:
        user_sort_key = store.append_message(
            user_id=user_id,
            conversation_id=conversation_id,
            role="user",
            text=request.query.strip(),
        )
    except Exception:
        logger.exception("Could not record the student's message; answering anyway")

    # Told BEFORE the worker is invoked: the student's message is already stored under this
    # id, and a client that never learned it would orphan the conversation.
    sink._post(
        {
            "type": "accepted",
            "conversationId": conversation_id,
        }
    )

    _invoke_worker(
        {
            "connectionId": connection_id,
            "turnId": turn_id,
            "userId": user_id,
            "conversationId": conversation_id,
            "isNewConversation": is_new_conversation,
            "userSortKey": user_sort_key,
            "query": request.query,
            "followup": request.followup,
            # The guardrail screen already billed; its units travel with the turn.
            "usage": usage.model_dump(by_alias=True),
        }
    )
    return _ack()


def _invoke_worker(payload):
    """Start the generation worker and do not wait for it. Asynchronous, retries off."""
    _lambda_client().invoke(
        FunctionName=WORKER_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps(payload).encode("utf-8"),
    )


# The route keys this function serves. The stack creates exactly these
# (infra/infra/infra_stack.py, section 7) and points all of them here.
_CONNECT_ROUTE = "$connect"
_DISCONNECT_ROUTE = "$disconnect"
# `$default` catches a frame whose `action` names no route, which is a malformed client
# rather than a turn, so it is refused: a billable path is not a fall-through.
_MESSAGE_ROUTE = "sendMessage"


def lambda_handler(event, context):
    """Dispatch on the route key API Gateway puts in `requestContext`."""
    route = ((event or {}).get("requestContext") or {}).get("routeKey")

    if route == _CONNECT_ROUTE:
        return handle_connect(event, STORE)
    if route == _DISCONNECT_ROUTE:
        return handle_disconnect(event, STORE)
    if route == _MESSAGE_ROUTE:
        return handle_message(event, STORE)

    logger.error("No handler for WebSocket route %r", route)
    return _ack(404, "Not found.")

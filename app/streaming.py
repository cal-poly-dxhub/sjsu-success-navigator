"""The WebSocket streaming path: routes, and the connection record behind them.

WHY A WEBSOCKET AND NOT RESPONSE STREAMING. Two constraints meet here and leave exactly
one door. API Gateway response streaming is REST-API only and this is an HTTP API; Lambda
response streaming supports Node.js and custom runtimes only and this agent loop is Python.
In-band streaming therefore means rewriting the loop in another language. A WebSocket API
keeps the loop exactly where it is and moves the streaming OUT OF BAND: the model's tokens
go out through ConverseStream and post_to_connection while the HTTP request that started
the turn has already returned.

THIS PATH IS ADDITIVE AND IT IS NOT THE DEFAULT. POST /chat is untouched - same handler,
same order, same response - and it stays the fallback for any student whose socket does not
open or does not survive (frontend/src/lib/chatStream.ts falls back on ANY failure). The
whole surface is gated on one config key; with it absent, nothing here is reachable because
none of the resources exist.

WHAT STREAMS IS A PREVIEW. The deltas pushed to the browser are PROSE ONLY and are never
parsed, capped, or trusted. Cards, the length caps, dash normalisation, the trailing-text
split and the safety-card decision all derive from the COMPLETE reply, in exactly the code
POST /chat already runs - orchestrator._response_from_text, reached through the same
run_chat. At the end of the turn one final message carries the authoritative payload: the
identical ChatResponse POST /chat would have returned for that turn. The browser throws the
preview away and renders that. There is no second card parser here, and there is nothing in
this file that reads a partial reply.
"""

from __future__ import annotations

import json
import logging
import os
import time

import boto3
from botocore.config import Config

from cards import card_block_started, preview_safe_prefix
from history import ConversationStore, new_conversation_id, new_ulid
from models import ChatRequest, ChatResponse
from ratelimit import claim_turn
from settings import load_settings
from usage import TurnUsage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SETTINGS = load_settings()

# The stage's callback endpoint (https://..., not wss://) that post_to_connection writes to,
# and the worker's function name. Both come from the stack rather than from the event: the
# event carries `domainName` and `stage`, and assembling the URL a reply is sent to out of
# request data is how a request ends up choosing its own destination.
#
# Read with a default of "" rather than through settings.py's `_required`, because these two
# variables exist ONLY when the streaming gate is on - and settings.py is imported by the
# chat function too, which has neither.
MANAGEMENT_ENDPOINT = os.environ.get("STREAM_CALLBACK_URL", "")
WORKER_FUNCTION_NAME = os.environ.get("STREAM_WORKER_FUNCTION_NAME", "")

# Batching, from config.yaml `streaming` (see resolve_streaming for why these are a cost
# control). The defaults are the config file's, so a dropped variable batches rather than
# pushing per token.
DELTA_MIN_CHARS = int(os.environ.get("STREAM_DELTA_MIN_CHARS") or 160)
DELTA_MAX_DELAY_MS = int(os.environ.get("STREAM_DELTA_MAX_DELAY_MS") or 250)

# The stage a `status` frame carries once the model has started writing card blocks. A WIRE
# VALUE, never a sentence: what the student reads about it is a string in the frontend's
# catalogue, in whichever language they chose. Alongside "retrieving", which the orchestrator
# sends before any text exists; this one marks the other end of the reply.
CARDS_STAGE = "composing_cards"

# The same store the HTTP handler uses, built the same way and connected lazily. A separate
# instance rather than an import from handler.py: importing that module would pull the whole
# HTTP request path - and its module-scope Bedrock client - into a function that serves
# neither.
STORE = ConversationStore(
    SETTINGS.chat_history_table_name, title_max_chars=SETTINGS.title_max_chars
)

# How long a connection record outlives the connection it describes, in seconds. API
# Gateway's own quotas end every connection well inside this: 10 minutes idle, 2 hours
# hard. Three hours is that hard cap plus an hour of slack, so the only rows TTL ever
# collects are the ones $disconnect failed to remove.
CONNECTION_TTL_SECONDS = 3 * 60 * 60


def connection_expiry(now: float | None = None) -> int:
    """The `expiresAt` epoch-second stamp for a connection opened now.

    Wall clock, not `time.monotonic()`: TTL is an absolute epoch second that DynamoDB
    compares against its own clock, so a process-relative timestamp would be meaningless
    to it (and, being small, would expire the row immediately).
    """
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
                # ONE ATTEMPT. This is an asynchronous invoke, so a retry that raced a
                # response the first call already delivered would start a SECOND generation
                # worker - two answers to one question, both billed, both pushed down the
                # same socket. The same reasoning that puts the function's own async retry
                # count at zero applies to the call that starts it.
                retries={"max_attempts": 1, "mode": "standard"},
                connect_timeout=2,
                read_timeout=5,
            ),
        )
    return _LAMBDA_CLIENT


def _bedrock_client():
    """bedrock-runtime, for the input guardrail screen on the message route. Deliberately
    the same shape and the same timeouts as app/handler.py's - it is the same screen."""
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
    """ApplyGuardrail(source=INPUT) on the bare query. INPUT SCREENING IS UNCHANGED.

    Same call, same guardrail, same version, same failure posture as app/handler.py: a
    guardrail FAILURE is not a block, because a student who hits a transient outage should
    not be told their question was rejected. Returns the replacement text on a block, or
    None to continue.

    It is repeated here rather than imported from handler.py for one reason: importing that
    module would pull the whole HTTP request path and its module-scope store into a function
    that serves neither. The thing that must not drift is the guardrail IDENTITY, and that
    comes from Settings, which both read from the same environment.
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
    logger.info("Input guardrail intervened on a streamed query")
    return text


def management_client(endpoint: str):
    """The apigatewaymanagementapi client that pushes frames down one connection.

    Built once per container against the stage's callback endpoint, which the stack passes
    in the environment rather than this module reconstructing it from the event - the event
    carries `domainName` and `stage`, and assembling a URL from request data is how a
    request ends up choosing where a reply is sent.

    Short timeouts on purpose. Every push sits inside the turn's own budget, so a stalled
    socket has to fail fast enough to leave the rest of the answer time to arrive.
    """
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
    """The Cognito `sub` for this connection, out of the $connect authorizer's context.

    THE ONLY PLACE A WEBSOCKET CALLER IS IDENTIFIED, and like app/handler.user_id_from it
    does not read anything the client sent. API Gateway attaches the $connect authorizer's
    `context` map to every route invocation on the connection - $default and $disconnect
    included, verified against a deployed probe - so this claim came out of a token
    app/ws_authorizer.py verified against the pool's JWKS at handshake time.

    A frame cannot influence it: the body is not read here, and the authorizer ran once,
    before the connection existed.
    """
    request_context = (event or {}).get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    value = authorizer.get("sub")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def client_id_from(event):
    """The `client_id` claim, from the same authorizer context as `sub`.

    Used for exactly one thing, the same thing it is used for on POST /chat: the rate
    limit's exemption list (app/ratelimit.py). Never an identity.
    """
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
    """Is this the 410 API Gateway raises when the connection has closed?

    Read off the error CODE rather than caught as the generated GoneException class, for the
    reason app/history.py gives about ConditionalCheckFailedException: the code is the
    contract, botocore's exception classes are generated, and matching a narrow named
    condition means every OTHER failure stays an error instead of being quietly read as
    "the student left".
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        if code in ("GoneException", "410"):
            return True
    return type(exc).__name__ == "GoneException"


class ConnectionSink:
    """Where one turn's preview goes: batched frames down one WebSocket connection.

    BATCHED, NOT PER TOKEN, and that is a cost control rather than a nicety. Every push is a
    billable API Gateway message, so a frame per token would multiply the message count by
    the token count to no visible end - the browser reveals text at ~108 characters a second
    and the model outruns that, so the deltas queue on the client either way.

    PROSE ONLY, AND ONLY THE PART THAT IS SAFE TO SHOW. `text` is handed the whole reply so
    far and pushes `cards.preview_safe_prefix` of it, which stops at the first tag of the
    card contract - so the preview is the lead-in the model wrote and never a half-typed
    `<card ref="2">`. It is a PREVIEW: nothing here caps it, normalises it or reads it for
    meaning, and the final payload replaces it wholesale.

    A 410 IS THE STUDENT CLOSING THE TAB, and it stops the pushing and nothing else. The
    turn finishes and is persisted (see stream_worker), because the model call is already
    paid for and because a user message with no assistant reply is the dangling turn
    docs/accounts-and-storage.md calls a reef - the next turn would have to merge it. Coming
    back to a coherent conversation is worth the writes.
    """

    def __init__(self, *, endpoint, connection_id, turn_id, min_chars, max_delay_ms):
        self._client = management_client(endpoint)
        self._connection_id = connection_id
        self._turn_id = turn_id
        self._min_chars = min_chars
        self._max_delay = max_delay_ms / 1000.0
        # True once the connection is known to be gone. Every push checks it, so one 410
        # stops the rest of the turn's frames rather than raising once per delta.
        self.gone = False
        # How much of the preview has been sent, and the RAW accumulated reply it indexes
        # into. The two belong together: `_sent` is an offset into `_accumulated`'s safe
        # prefix and is meaningless against any other string - which is exactly the mistake
        # `flush` used to make, slicing the PARSED prose with an index taken from the raw
        # stream and sending a fragment that began mid-word.
        self._sent = 0
        self._accumulated = ""
        self._last_push = time.monotonic()
        # Whether the "the model has started writing cards" frame has gone out. Sent at
        # most once per turn - see _announce_cards.
        self._cards_announced = False
        self.frames = 0

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
            # Anything else is a real fault and worth a log line, but it is still not worth
            # the answer: the next frame may well land, and the final payload is the one
            # that matters.
            logger.warning("Could not push a streamed frame", exc_info=True)
            return False
        self.frames += 1
        return True

    def status(self, stage):
        """Something is happening that produces no text. The UI can say a true thing."""
        self._post({"type": "status", "stage": stage})

    def text(self, accumulated):
        """The reply so far. Pushes whatever is newly safe to show, if enough has built up."""
        self._accumulated = accumulated
        safe = preview_safe_prefix(accumulated)
        pending = len(safe) - self._sent
        if pending > 0:
            now = time.monotonic()
            if pending >= self._min_chars or (now - self._last_push) >= self._max_delay:
                self._flush_to(safe, now)
        self._announce_cards(accumulated)

    def _announce_cards(self, accumulated):
        """Say ONCE that the model has begun writing cards, and finish the prose first.

        THE SIGNAL IS THE MODEL'S OWN OUTPUT, not a timer and not a guess: `<card` in the
        stream is the same event that stops the preview, so this frame marks the exact
        instant the prose ended and the part the student cannot see began. A reply that
        never writes a card never sends it, which is what stops the browser promising
        resources that are not coming.

        The tail of the preview is flushed FIRST, and that ordering is load-bearing twice
        over. The safe prefix cannot grow past this point, so there is nothing left to wait
        for and the last words of the lead-in should not sit in the batcher behind a
        min_chars threshold they may never reach. And the browser clears the indicator on
        any arriving prose, so a delta landing after this frame would take it back off.
        """
        if self._cards_announced or not card_block_started(accumulated):
            return
        self._cards_announced = True
        self.flush()
        self.status(CARDS_STAGE)

    def flush(self):
        """Push the tail of the preview, once the model has stopped.

        Takes no argument on purpose. It works from the RAW text the sink accumulated, so
        the offset it slices with indexes the string it was measured against - the tail is
        whatever is left of this turn's own preview, never a slice of some other string
        that happens to start the same way.
        """
        safe = preview_safe_prefix(self._accumulated)
        if len(safe) > self._sent:
            self._flush_to(safe, time.monotonic())

    def _flush_to(self, safe, now):
        if self._post({"type": "delta", "text": safe[self._sent :]}):
            self._sent = len(safe)
            self._last_push = now

    def final(self, payload):
        """THE AUTHORITATIVE MESSAGE, and the only one the browser renders as the answer.

        `payload` is exactly what POST /chat returns for this turn - the same ChatResponse,
        serialised through the same aliases - so the finished turn is identical whichever
        transport carried it. The preview above was never anything else's input.
        """
        self._post({"type": "final", "payload": payload})

    def error(self, message, **extra):
        """A turn that will not produce an answer, described well enough to render.

        Distinct from a socket failure on purpose: this is the server saying something
        definite (a rate-limit refusal, a failed loop), so the client shows it rather than
        falling back to POST /chat and asking the same question twice.
        """
        self._post({"type": "error", "message": message, **extra})


def _ack(status_code=200, message=None):
    """What a WebSocket route integration returns. The body is not delivered to the client
    on $connect or $disconnect - only the status code decides whether the handshake
    completes - so it exists for the logs and for a direct invoke."""
    return {"statusCode": status_code, "body": json.dumps({"message": message or "ok"})}


def handle_connect(event, store):
    """$connect: the authorizer has already said yes, so this only records the connection.

    A MISSING `sub` IS A REFUSAL, not an anonymous connection. The route is authorizer-gated,
    so arriving here without one means a misconfigured stack rather than a student - and
    every key this connection will touch is built from that claim, so there is nowhere to
    put the turn and nobody to attribute it to. Same posture as POST /chat's 401.
    """
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
    """$disconnect: forget the connection record.

    Best effort by nature. API Gateway does not guarantee this route fires at all - a
    dropped client, a region event, an idle timeout on a connection nobody was watching -
    which is exactly why the record carries a TTL rather than relying on this.
    """
    user_id = identity_from(event)
    connection_id = connection_id_from(event)
    if user_id is None or connection_id is None:
        # Nothing addressable, so nothing to delete. Not an error worth an ERROR line: the
        # TTL collects the row either way.
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
    """The message route: the same order POST /chat runs, then a hand-off.

    THE ORDER IS THE HTTP HANDLER'S, DELIBERATELY AND IN FULL - validate, identity, rate
    limit, guardrail, persist - because each step's POSITION is a property somebody argued
    for, and a second path that reordered them would quietly undo those arguments:

      - the rate limit is BEFORE the guardrail, so a refused turn spends one conditional
        DynamoDB write and nothing billable.
      - the guardrail is BEFORE the write, so a blocked message never becomes a turn. That
        one matters more here than anywhere: storing it would smuggle the attack text into
        the history the model reads on the NEXT turn, past the screen that just caught it.
      - the student's message is written BEFORE the model call, so a disclosure that then
        fails is still on record.

    THEN IT RETURNS, WITHOUT AN ANSWER. A WebSocket route integration has the same 29-second
    ceiling every API Gateway integration has, and the agent loop can use most of it - so
    the generation runs in a SEPARATE function, invoked asynchronously, which pushes its
    reply down the connection this request arrived on. What this returns is an ack.
    """
    connection_id = connection_id_from(event)
    user_id = identity_from(event)
    if user_id is None or connection_id is None:
        logger.error("A message frame carried no authorizer sub claim; refusing it")
        return _ack(401, "Unauthenticated.")

    # A turn id, minted here and carried on every frame of this turn. The client binds its
    # pending exchange to it, so a reply that arrives after the student has moved on - or
    # two turns racing on one connection - lands on the right bubble or is discarded.
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

    # The same model the HTTP route validates through, so the same fields are accepted and
    # the same unknown ones - a `history` array, a user id - are dropped rather than
    # sanitised. There is nothing here a client can say that names a user.
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
        # A definite answer, not a socket failure - the client renders it rather than
        # retrying over POST /chat, which would ask the same question twice and be refused
        # again. The reset INSTANT travels, and the browser renders it in the student's own
        # clock exactly as it does for the 429.
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
        # Nothing written and no worker started. The whole turn is this frame, and it is the
        # same ChatResponse POST /chat would have returned - including the usage, because a
        # blocked screen was billed like any other.
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

    # Told BEFORE the worker is invoked, so the client holds the conversation id even if the
    # generation then fails: the student's message is already stored under it, and a client
    # that never learned it would open a fresh conversation on the next turn and orphan this
    # one.
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
            # The guardrail screen already billed; its units travel so the cost panel sees
            # one turn's whole spend rather than only the part the worker paid for.
            "usage": usage.model_dump(by_alias=True),
        }
    )
    return _ack()


def _invoke_worker(payload):
    """Start the generation worker and do not wait for it.

    ASYNCHRONOUS ('Event'), which is the entire reason this route can return inside the
    integration timeout while a twenty-second agent loop runs.

    THE RETRY COUNT IS ZERO, AND IT IS SET ON THE FUNCTION (infra_stack.py), not here.
    Lambda retries a failed asynchronous invocation twice by default, and a retried
    generation worker answers the same question a second and third time - down the same
    socket, billing the model each time. There is no idempotency key that would make that
    safe, so the retry is turned off instead.
    """
    _lambda_client().invoke(
        FunctionName=WORKER_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps(payload).encode("utf-8"),
    )


# The route keys this function serves. The stack creates exactly these (infra_stack.py,
# section 7) and points all of them here.
_CONNECT_ROUTE = "$connect"
_DISCONNECT_ROUTE = "$disconnect"
# The named message route. `$default` catches a frame whose `action` names no route, which
# is a malformed client rather than a turn - so it is refused rather than answered, for the
# reason the HTTP handler refuses an unknown routeKey: a billable path should not be the
# thing an unrecognised request falls into.
_MESSAGE_ROUTE = "sendMessage"


def lambda_handler(event, context):
    """Dispatch on the route key API Gateway puts in `requestContext`.

    NOT the top-level `routeKey` app/handler.py reads - that is where an HTTP API payload
    2.0 event carries it, and a WebSocket event carries it one level down. The two handlers
    are separate functions partly for this reason: a shared one would have to guess which
    protocol it was serving, and guessing wrong on a WebSocket frame would run a billable
    chat turn.

    An UNKNOWN route key is refused rather than falling through, for the reason the HTTP
    handler gives: the stack creates a fixed set, so an unknown one means somebody added a
    route and pointed it here.
    """
    route = ((event or {}).get("requestContext") or {}).get("routeKey")

    if route == _CONNECT_ROUTE:
        return handle_connect(event, STORE)
    if route == _DISCONNECT_ROUTE:
        return handle_disconnect(event, STORE)
    if route == _MESSAGE_ROUTE:
        return handle_message(event, STORE)

    logger.error("No handler for WebSocket route %r", route)
    return _ack(404, "Not found.")

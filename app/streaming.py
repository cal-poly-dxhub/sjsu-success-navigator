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
import time

import boto3
from botocore.config import Config

from history import ConversationStore
from settings import load_settings

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SETTINGS = load_settings()

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


# The route keys this function serves. The stack creates exactly these (infra_stack.py,
# section 7) and points all of them here.
_CONNECT_ROUTE = "$connect"
_DISCONNECT_ROUTE = "$disconnect"


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

    logger.error("No handler for WebSocket route %r", route)
    return _ack(404, "Not found.")

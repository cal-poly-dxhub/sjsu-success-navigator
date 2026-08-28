"""The streaming chat app: a FastAPI app run by the Lambda Web Adapter.

POST /api/chat is the real turn, streamed. /api/stream and /api/model are probes,
and keeping the transport-only one is what tells a transport fault from a Bedrock one.
"""

import asyncio
import json
import logging
import queue
import threading
import time

import boto3
from botocore.config import Config
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from history import ConversationStore
from models import ChatRequest
from preview import PreviewSink
from settings import SettingsError, load_settings
from token_auth import Identity, Unauthorized, verifier, verifier_error
from turn import TurnRefused, run_turn

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Five seconds of wall clock: spread out and all at once cannot be confused in a curl trace.
CHUNK_COUNT = 10
CHUNK_INTERVAL_SECONDS = 0.5

# The probe's own cap: a throwaway question does not need the turn's token budget.
PROBE_MAX_TOKENS = 512

# Not batched: a response-streamed HTTP body has no per-frame charge to save.
STREAM_MIN_CHARS = 1
STREAM_MAX_DELAY_MS = 0

app = FastAPI()

# Equal to the CloudFront behaviour's path pattern letter for letter; an infra test compares them.
EDGE_PATH_PREFIX = "/api"

# Everything the outside world calls hangs off this; the readiness route does not.
router = APIRouter(prefix=EDGE_PATH_PREFIX)

_SETTINGS = None
_SETTINGS_ERROR = None
_BEDROCK_CLIENT = None
_STORE = None


def settings():
    """Lazy, and the error is held: a module-scope raise would take the probes down too."""
    global _SETTINGS, _SETTINGS_ERROR
    if _SETTINGS is None and _SETTINGS_ERROR is None:
        try:
            _SETTINGS = load_settings()
        except SettingsError as exc:
            _SETTINGS_ERROR = str(exc)
    return _SETTINGS


def _bedrock_client(region):
    """Its own client: importing orchestrator's would pull the agent loop in with it."""
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


@app.get("/")
async def health() -> dict:
    """The adapter's readiness target, polled at every cold start. Cheap on purpose."""
    return {"ok": True, "probe": "lwa-response-stream"}


async def _timed_chunks():
    """The stamp is written at yield time, so it is the evidence; the line count is not."""
    started = time.monotonic()
    for index in range(1, CHUNK_COUNT + 1):
        yield f"chunk {index}/{CHUNK_COUNT} t=+{time.monotonic() - started:.3f}s\n"
        await asyncio.sleep(CHUNK_INTERVAL_SECONDS)


@router.get("/stream")
async def stream() -> StreamingResponse:
    """The transport control. Plain lines, because `curl -N` renders them as they land."""
    return StreamingResponse(_timed_chunks(), media_type="text/plain")


def _model_deltas(question: str):
    """Synchronous on purpose: botocore's event stream blocks, so Starlette threadpools it."""
    resolved = settings()
    if resolved is None:
        yield f"[error] {_SETTINGS_ERROR}\n"
        return

    started = time.monotonic()
    try:
        response = _bedrock_client(resolved.bedrock_region).converse_stream(
            modelId=resolved.generation_model_id,
            messages=[{"role": "user", "content": [{"text": question}]}],
            inferenceConfig={
                "maxTokens": PROBE_MAX_TOKENS,
                "temperature": resolved.generation_temperature,
            },
        )
    except Exception as exc:
        logger.exception("ConverseStream failed to start")
        yield f"[error] {type(exc).__name__}\n"
        return

    deltas = 0
    try:
        for event in response["stream"]:
            delta = (event.get("contentBlockDelta") or {}).get("delta") or {}
            text = delta.get("text")
            if text:
                deltas += 1
                yield text
    except Exception as exc:
        # Whatever arrived is already on the wire and is still evidence about the transport.
        logger.exception("ConverseStream ended early")
        yield f"\n[error] {type(exc).__name__}\n"
        return

    elapsed = time.monotonic() - started
    logger.info("probe model deltas=%s elapsed=%.3fs", deltas, elapsed)
    # A trailing line, not a header: a buffered response puts this and the first token together.
    yield f"\n[deltas={deltas} elapsed={elapsed:.3f}s]\n"


@router.get("/model")
async def model(q: str = "Say hello and name one thing SJSU students ask about.") -> StreamingResponse:
    """The default question exists so the route can be curled with no arguments."""
    return StreamingResponse(_model_deltas(q), media_type="text/plain")


def store():
    """Built lazily, and its own instance: importing handler.py drags its transport along."""
    global _STORE
    if _STORE is None:
        resolved = settings()
        _STORE = ConversationStore(
            resolved.chat_history_table_name, title_max_chars=resolved.title_max_chars
        )
    return _STORE


def identity_from(request: Request) -> Identity | None:
    """No authorizer sits in front of this route, so the token is verified here or nowhere."""
    resolved = verifier()
    if resolved is None:
        # A half-configured deploy, but the same 401, logged because somebody has to fix it.
        logger.error("The streaming chat route cannot verify tokens: %s", verifier_error())
        return None

    try:
        return resolved.identity_from_headers(request.headers)
    except Unauthorized as refused:
        # The exception's own message and nothing else: no chain, no header, no token.
        logger.warning("Refusing a streamed chat request: %s", refused)
        return None


class _ResponseSink(PreviewSink):
    """The turn runs on another thread and pushes frames here; the queue is the whole coupling."""

    def __init__(self, frames: "queue.Queue"):
        super().__init__(min_chars=STREAM_MIN_CHARS, max_delay_ms=STREAM_MAX_DELAY_MS)
        self._frames = frames

    def _post(self, payload) -> bool:
        self._frames.put(payload)
        self.frames += 1
        return True


# An object(), so nothing a turn could legitimately emit can be mistaken for the end of it.
_DONE = object()


def _turn_frames(chat_request: ChatRequest, *, user_id: str, client_id: str | None):
    """NDJSON, because a turn carries a body and `EventSource` can only issue a GET."""
    resolved = settings()
    frames: "queue.Queue" = queue.Queue()
    sink = _ResponseSink(frames)

    def produce():
        try:
            response = run_turn(
                chat_request,
                user_id=user_id,
                client_id=client_id,
                settings=resolved,
                store=store(),
                bedrock_client=lambda: _bedrock_client(resolved.bedrock_region),
                deadline=time.monotonic() + resolved.converse_deadline_seconds,
                # Evaluated after the model returns, so the title's budget starts then.
                title_deadline_at=lambda: time.monotonic()
                + resolved.title_deadline_seconds,
                stream=sink,
            )
        except TurnRefused as refused:
            # A definite answer: the client renders it rather than retrying and being refused.
            refusal = refused.refusal
            sink.error(
                refusal.message,
                limit=refusal.limit,
                resetAt=refusal.reset_at_iso,
                retryAfterSeconds=refusal.retry_after_seconds,
            )
            return
        except Exception:
            # Logged, not sent: a botocore message can quote the student's own words.
            logger.exception("Streamed chat orchestration failed")
            sink.error("The assistant is unavailable right now.")
            return

        # The tail of the preview, from the sink's own raw text, not the parsed prose.
        sink.flush()
        # Byte for byte what POST /chat returns. A client throws the preview away for this.
        sink.final(response.model_dump(by_alias=True))

    def run():
        try:
            produce()
        finally:
            # Always, or the generator below blocks forever on a queue nobody will feed.
            frames.put(_DONE)

    worker = threading.Thread(target=run, name="chat-turn", daemon=True)
    worker.start()

    while True:
        frame = frames.get()
        if frame is _DONE:
            break
        yield json.dumps(frame) + "\n"


@router.post("/chat")
async def chat(request: Request):
    """Validate and identify first: past the first byte, the only way to say no is a frame."""
    resolved = settings()
    if resolved is None:
        logger.error("The streaming chat route has no settings: %s", _SETTINGS_ERROR)
        return JSONResponse(
            {"error": "The assistant is unavailable right now."}, status_code=503
        )

    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "Invalid request body."}, status_code=400)

    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        return JSONResponse(
            {"error": "Missing 'query' in request body."}, status_code=400
        )
    if len(query) > resolved.max_query_chars:
        return JSONResponse(
            {"error": f"Query exceeds {resolved.max_query_chars} characters."},
            status_code=400,
        )

    # The same model POST /chat validates through; anything else the client sent is dropped.
    try:
        chat_request = ChatRequest.model_validate(body)
    except Exception:
        logger.exception("Invalid streaming chat request body")
        return JSONResponse({"error": "Invalid request body."}, status_code=400)

    # Failing closed: one status for every way a token can fail, and no detail on the wire.
    identity = identity_from(request)
    if identity is None:
        return JSONResponse({"error": "Unauthenticated."}, status_code=401)

    return StreamingResponse(
        _turn_frames(
            chat_request, user_id=identity.sub, client_id=identity.client_id
        ),
        media_type="application/x-ndjson",
    )


# Last, because include_router copies the routes it finds at call time.
app.include_router(router)

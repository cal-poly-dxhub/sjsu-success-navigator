"""The response-streaming probe: a FastAPI app run by the Lambda Web Adapter.

WHY IT EXISTS. The WebSocket section of infra_stack.py says, correctly, that "Lambda
response streaming is Node.js/custom-runtime only and the agent loop is Python" - so the
stream was moved out of band, onto a socket and three functions. That sentence is true of
the PYTHON MANAGED RUNTIME and false of Lambda: the AWS Lambda Web Adapter runs an
ordinary ASGI app as an execution wrapper and streams its response body out through a
Function URL, which is the supported way to get in-band streaming out of Python
(aws/aws-lambda-web-adapter, examples/fastapi-response-streaming-zip). This module is that
example morphed onto this repo's zip-from-source pipeline, and it exists so the commit
that moves real logic onto the mechanism is a move rather than a bring-up.

TWO ROUTES THAT STREAM, AND KEEPING BOTH IS THE POINT.

    GET /api/stream   ten timed chunks, no AWS call at all - pure transport
    GET /api/model    one ConverseStream turn against the configured generation model

They are a DIFFERENTIAL. When a stream arrives in one lump, the question is always
"transport or Bedrock?", and the only way to answer it without a deploy-and-guess cycle is
to have one route that cannot possibly be Bedrock's fault. /stream is that route, and it
stays here for every later stage - it is the control, not scaffolding.

WHAT /model IS NOT. No tools, no retrieval, no card parsing, no safety triage, no titling
and no storage: one message in, text deltas out. Those all belong to the real turn
(app/orchestrator.py, reached through app/handler.py), and pulling them in here would mean
a second copy of a loop this repo already has exactly one of. What is being proven at this
stage is narrower than a turn - that OUR model, under OUR settings and OUR IAM, emits text
incrementally through this transport.

AND THE ROUTE THAT IS NOT A PROBE.

    POST /api/chat    the real turn - guardrail, retrieval, tools, cards, safety, storage,
                      titling - streamed as it is written, the conversation id announced
                      first

It is the SAME turn POST /chat runs, because it is literally the same function: app/turn.py
holds the sequence and this route hands it a sink. There is no second loop here, no second
card parser and no second exit; what streams is a preview and the last frame carries the
identical ChatResponse the API Gateway handler would have returned. That property is
structural rather than tested-for, and the acceptance check is still to send one question
down both paths and diff the payloads.

THE MODULE NAME IS NOW A LIE and is left alone deliberately. run.sh names `stream_probe:app`
and the stack's bundle names the file; renaming is a rename commit, and deleting or renaming
anything is a later step and the captain's call.

TWO ROUTES THAT DO NOT STREAM. `/` is the adapter's readiness target, cheap on purpose:
the adapter polls AWS_LWA_READINESS_CHECK_PATH (default "/") before forwarding the first
request, and the upstream example puts its stream there, paying for the whole stream at
every cold start.

WHY EVERY REAL ROUTE SITS UNDER A PREFIX AND THE READINESS ROUTE DOES NOT. This app is
reached two ways: directly on its Function URL, and through the CloudFront distribution
that already serves the site, on a path-pattern behaviour (infra_stack.py section 6).
CloudFront matches a behaviour on the viewer's path and forwards that path to the origin
UNCHANGED - there is no prefix-stripping short of a rewrite function - so the pattern at
the edge and the paths in here have to be the same string, and EDGE_PATH_PREFIX below is
this side of it. `/` stays outside the router because the two things that reach it are the
adapter's own readiness poll on 127.0.0.1 and a direct curl at the Function URL's root;
moving it under the prefix would mean a readiness check answering 404 at every cold start
and a function that never starts. It is also the reason the site keeps `/`: the edge
behaviour claims one prefix and nothing else, so the Astro app still owns the root.

NOTHING HERE PROVES THE FUNCTION URL. Uvicorn plus curl proves the app emits chunks
incrementally, which is the half that can be checked without an account. Whether Lambda
forwards them incrementally is a property of the deployed InvokeMode and the adapter, and
only a deploy can show it.
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
from turn import TurnRefused, run_turn

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Ten chunks half a second apart: five seconds of wall clock, which is long enough that
# "arrived spread out" and "arrived all at once" cannot be confused for each other in a
# curl trace, and short enough to sit far inside the function's timeout.
CHUNK_COUNT = 10
CHUNK_INTERVAL_SECONDS = 0.5

# The probe's own generation cap, deliberately NOT settings.generation_max_tokens. This
# route answers one throwaway question with no system prompt and no retrieved passages; a
# 1200-token budget would only buy a longer bill for a check whose result is visible in the
# first two seconds.
PROBE_MAX_TOKENS = 512

# THE PREVIEW IS NOT BATCHED HERE, and that is a measured difference from the socket rather
# than an oversight. app/streaming.py batches because every push down a WebSocket is a
# billable API Gateway message; a response-streamed HTTP body has no per-frame charge, so
# the same thresholds would buy nothing and cost up to STREAM_DELTA_MAX_DELAY_MS of latency
# on the last words of a sentence. `min_chars=1` pushes every delta the model produces.
STREAM_MIN_CHARS = 1
STREAM_MAX_DELAY_MS = 0

app = FastAPI()

# THE PREFIX THE EDGE MATCHES ON, and the reason it is a constant rather than typed into
# three decorators: it has to equal the CloudFront behaviour's path pattern
# (infra_stack.py's _STREAM_EDGE_PATH_PREFIX) letter for letter, and the infra suite's
# test_the_edge_path_pattern_and_the_apps_own_routes_are_one_string reads BOTH off disk
# and compares them. A mismatch is not a synth error or a test failure anywhere else - it
# is a deployed 404 from FastAPI, served through a distribution working as configured.
#
# "/api" rather than something this repo made up: it is the prefix the upstream example
# this app is morphed from uses (aws/aws-lambda-web-adapter,
# examples/fastapi-backend-only-response-streaming, GET /api/stream), and the site it now
# shares a domain with has no page under it.
EDGE_PATH_PREFIX = "/api"

# Every route the outside world calls hangs off this; `/` does not (see the module
# docstring). include_router is at the BOTTOM of the file, after the last route is defined.
router = APIRouter(prefix=EDGE_PATH_PREFIX)

_SETTINGS = None
_SETTINGS_ERROR = None
_BEDROCK_CLIENT = None
_STORE = None


def settings():
    """The chat function's Settings, loaded once and LAZILY - which is the one place this
    module deliberately departs from app/handler.py.

    handler.py loads at import so a missing variable fails the cold start naming itself,
    and for that function it is right: every route it serves needs the whole identity set.
    Here it would be exactly backwards. A probe's job is to tell two failures apart, and a
    module-scope raise would take the transport route down with the model route - the
    adapter's readiness check would fail, the Function URL would return an opaque 502, and
    the one route that could have proven the transport still works would never answer.

    So the error is CAUGHT AND HELD rather than swallowed: /stream keeps working, and
    /model reports the missing variable by name.
    """
    global _SETTINGS, _SETTINGS_ERROR
    if _SETTINGS is None and _SETTINGS_ERROR is None:
        try:
            _SETTINGS = load_settings()
        except SettingsError as exc:
            _SETTINGS_ERROR = str(exc)
    return _SETTINGS


def _bedrock_client(region):
    """The bedrock-runtime client, built once per container.

    The same shape app/orchestrator.py's uses and for the same reasons - adaptive retries,
    a read timeout long enough for a model call - rather than boto3's defaults. It is a
    separate client rather than an import from that module because importing it would pull
    the whole agent loop into a function that at this stage runs none of it.
    """
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
    """The adapter's readiness target. Cheap on purpose - see the module docstring."""
    return {"ok": True, "probe": "lwa-response-stream"}


async def _timed_chunks():
    """One line per chunk, each carrying the elapsed seconds it was yielded at.

    The TIMESTAMP IS THE EVIDENCE. A reader that buffered the whole body still sees ten
    lines, so line count proves nothing; the stamps are written by the generator at yield
    time, so a buffered response shows ten stamps spread across five seconds arriving in
    one lump, and a streamed one shows each stamp arriving when it says it was made.
    """
    started = time.monotonic()
    for index in range(1, CHUNK_COUNT + 1):
        yield f"chunk {index}/{CHUNK_COUNT} t=+{time.monotonic() - started:.3f}s\n"
        await asyncio.sleep(CHUNK_INTERVAL_SECONDS)


@router.get("/stream")
async def stream() -> StreamingResponse:
    """The transport control.

    text/plain rather than text/event-stream: SSE framing is a contract with a browser
    client, and there is no client here. Plain lines are what `curl -N` renders as they
    land, which is the whole point of the route.
    """
    return StreamingResponse(_timed_chunks(), media_type="text/plain")


def _model_deltas(question: str):
    """Text deltas from one ConverseStream call, yielded as Bedrock produces them.

    A SYNCHRONOUS generator on purpose. botocore's event stream is a blocking iterator, and
    Starlette runs a non-async body iterator in a threadpool, so the event loop is never
    parked on a socket read. Writing this as `async def` around the same blocking iterator
    is the version that looks more correct and stalls the server.

    CONVERSESTREAM, NOT INVOKEMODELWITHRESPONSESTREAM, even though the IAM action is spelled
    the second way. Converse is what app/orchestrator.py calls, so this route exercises the
    API the real turn will arrive on rather than a neighbouring one that happens to share a
    permission.

    AN ERROR AFTER THE FIRST BYTE CANNOT BE A STATUS CODE. Starlette has already sent 200 by
    the time this runs, so a failure is reported IN BAND as a final line and logged. That is
    a probe's answer, not a product's: a student never sees this route.
    """
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
        # Mid-stream faults end the reply rather than failing it. Whatever arrived is
        # already on the wire and is still evidence about the transport.
        logger.exception("ConverseStream ended early")
        yield f"\n[error] {type(exc).__name__}\n"
        return

    elapsed = time.monotonic() - started
    logger.info("probe model deltas=%s elapsed=%.3fs", deltas, elapsed)
    # A trailing line rather than a header, because headers are gone by now. It is what
    # makes "did it stream" answerable from the transcript alone: a buffered response puts
    # this line and the first token in the same packet.
    yield f"\n[deltas={deltas} elapsed={elapsed:.3f}s]\n"


@router.get("/model")
async def model(q: str = "Say hello and name one thing SJSU students ask about.") -> StreamingResponse:
    """One real Bedrock turn, streamed. The default question exists so the route can be
    curled with no arguments at all - the first thing anyone does to a probe."""
    return StreamingResponse(_model_deltas(q), media_type="text/plain")


# --- the real turn, streamed -------------------------------------------------------------


def store():
    """The conversation store, built once and lazily, for the same reason settings are.

    A separate instance rather than an import from app/handler.py: that module is the API
    Gateway transport, and importing it here would pull its request pipeline and its
    module-scope clients into a function that serves none of them.
    """
    global _STORE
    if _STORE is None:
        resolved = settings()
        _STORE = ConversationStore(
            resolved.chat_history_table_name, title_max_chars=resolved.title_max_chars
        )
    return _STORE


def identity_from(request: Request):
    """The caller's `sub` and `client_id`, out of the Lambda request context. Or (None, None).

    THE HEADER IS THE ADAPTER'S, NOT THE CLIENT'S, and that is the whole reason this is
    allowed to be an identity at all. The Lambda Web Adapter serialises the invocation's
    request context into `x-amzn-request-context` with an `insert`, which REPLACES whatever
    the caller sent (aws-lambda-web-adapter src/lib.rs, and its changelog entry "Override
    user-set x-amzn-{lambda,request}-context headers to prevent spoofing"). So a browser
    cannot put a `sub` in here any more than it can put one in an API Gateway event.

    IT READS THE SAME CLAIMS APP/HANDLER.PY READS, from the same place: an HTTP API payload
    2.0 request context puts them at `authorizer.jwt.claims`, and that is what arrives here
    when this function sits behind the JWT authorizer POST /chat already uses.

    IT FAILS CLOSED TODAY, ON PURPOSE. Behind the IAM-authenticated Function URL this stack
    creates, the request context carries `authorizer.iam` - the signing principal - and no
    `jwt` block at all, so this returns None and the route answers 401 to everybody. That is
    the honest state of things: WHICH front door this endpoint gets, and what authenticates
    a browser to it, is undecided and out of scope. Inventing a header to trust in the
    meantime would be deciding it by accident, in the direction nobody would choose.
    """
    raw = request.headers.get("x-amzn-request-context")
    if not raw:
        return None, None
    try:
        context = json.loads(raw)
    except ValueError:
        logger.warning("x-amzn-request-context was not JSON; refusing the request")
        return None, None
    if not isinstance(context, dict):
        return None, None
    claims = (((context.get("authorizer") or {}).get("jwt") or {}).get("claims")) or {}

    def claim(name):
        value = claims.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else None

    return claim("sub"), claim("client_id")


class _ResponseSink(PreviewSink):
    """A PreviewSink whose wire is a queue drained by the response body generator.

    THE TURN RUNS ON ANOTHER THREAD. `run_turn` is blocking all the way down - botocore's
    event stream is a blocking iterator - so it cannot drive an HTTP response body directly.
    The producer pushes frames here and the generator below pops them; the queue is the
    whole of the coupling, and it is unbounded because the only producer is one turn and the
    only consumer is already waiting on it.

    `_post` ALWAYS SUCCEEDS. There is no 410 to detect: a client that hangs up is noticed by
    the ASGI server when the generator's next chunk cannot be written, and the turn behind
    it finishes and is persisted regardless - the same posture app/streaming.py takes for
    the same reason (the model call is already paid for, and a user message with no
    assistant reply is a dangling turn the next one would have to merge).
    """

    def __init__(self, frames: "queue.Queue"):
        super().__init__(min_chars=STREAM_MIN_CHARS, max_delay_ms=STREAM_MAX_DELAY_MS)
        self._frames = frames

    def _post(self, payload) -> bool:
        self._frames.put(payload)
        self.frames += 1
        return True


# The sentinel the producer thread puts on the queue when the turn is over, however it
# ended. An object() rather than None or a frame type, so nothing a turn could legitimately
# emit can be mistaken for the end of it.
_DONE = object()


def _turn_frames(chat_request: ChatRequest, *, user_id: str, client_id: str | None):
    """NDJSON frames for one streamed turn: accepted, status, delta, then one final or error.

    NDJSON RATHER THAN SSE, and it is not a front-door decision - it is the smallest framing
    that carries the socket's existing frame types unchanged (`accepted`, `status`, `delta`,
    `final`, `error`), so whatever ends up in front of this can translate one line into one
    message. SSE's `event:`/`data:` framing would be a second spelling of the same thing,
    chosen for a browser API nobody has committed to.

    `accepted` COMES FIRST AND CARRIES THE CONVERSATION ID, and on this transport it is
    app/turn.py that sends it, the instant the student's message is on record under that id
    and before the loop can produce a byte. Nothing in this file arranges that or could:
    the id is minted inside the turn, and a frame assembled out here would either precede
    the write it claims or trail the first delta. What the queue below guarantees is only
    that the order the sink posted in is the order the body is written in.

    THE DEADLINE IS THE CONFIGURED ONE, unchanged: chat.converse_deadline_seconds, 22
    seconds, the same number app/handler.py and app/stream_worker.py both use. It is not
    narrowed against Lambda's remaining time the way the handler narrows it, because there
    is no context object here - the adapter does forward one in `x-amzn-lambda-context`, and
    reading it is a real improvement that belongs with the function timeout it would be
    measured against, not in the commit that first serves a turn.
    """
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
            # A definite answer, not a transport failure - the client renders it rather than
            # retrying somewhere else and being refused again. The same four fields POST
            # /chat puts in its 429 body.
            refusal = refused.refusal
            sink.error(
                refusal.message,
                limit=refusal.limit,
                resetAt=refusal.reset_at_iso,
                retryAfterSeconds=refusal.retry_after_seconds,
            )
            return
        except Exception:
            # The student gets a plain failure rather than a partial answer, and the
            # exception is logged rather than sent: a botocore message can quote the
            # request, and the request is the student's own words.
            logger.exception("Streamed chat orchestration failed")
            sink.error("The assistant is unavailable right now.")
            return

        # The tail of the preview, so the prose on screen ends where the model's lead-in
        # does rather than a batch short of it. It flushes the sink's own accumulated RAW
        # text - not response.conversational_text, which is the parsed and normalised prose
        # and a different string.
        sink.flush()
        # THE AUTHORITATIVE PAYLOAD, and the whole reason the preview is allowed to be
        # rough: byte-for-byte what POST /chat returns for this turn, because it came out of
        # the same app/turn.py. A client throws the preview away and renders this.
        sink.final(response.model_dump(by_alias=True))

    def run():
        try:
            produce()
        finally:
            # ALWAYS, or the generator below blocks forever on a queue nobody will feed.
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
    """One turn, streamed. The same order, the same loop and the same payload as POST /chat.

    VALIDATE, IDENTIFY, THEN STREAM. Everything that can be refused with a status code is
    refused before the first byte of the body leaves, because after that Starlette has
    already sent 200 and the only way to say no is a frame. What is left inside the stream
    is the daily cap, which lives in app/turn.py where its position in the order is argued
    for - the socket refuses it the same way, as an `error` frame.
    """
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

    # The body, reduced to the two things a client is allowed to say. Anything else it sent
    # - a `history` array, a user id - is an unknown key and pydantic drops it here, which
    # is the last point at which it exists. The SAME model POST /chat validates through.
    try:
        chat_request = ChatRequest.model_validate(body)
    except Exception:
        logger.exception("Invalid streaming chat request body")
        return JSONResponse({"error": "Invalid request body."}, status_code=400)

    # Identity, and failing closed. Every partition key is built from this claim, so a
    # request without one has nowhere to put the turn and nobody to attribute it to - the
    # same 401 POST /chat gives, for the same reason.
    user_id, client_id = identity_from(request)
    if user_id is None:
        logger.error("A streamed chat request carried no JWT sub claim; refusing it")
        return JSONResponse({"error": "Unauthenticated."}, status_code=401)

    return StreamingResponse(
        _turn_frames(chat_request, user_id=user_id, client_id=client_id),
        media_type="application/x-ndjson",
    )


# The routes above are declared on the router, so nothing is served until this line runs.
# It is last because include_router copies the routes it finds AT CALL TIME - called any
# earlier and the ones defined below it would be silently absent from the app.
app.include_router(router)

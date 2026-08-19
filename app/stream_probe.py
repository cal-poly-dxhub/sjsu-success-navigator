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

    GET /stream   ten timed chunks, no AWS call at all - pure transport
    GET /model    one ConverseStream turn against the configured generation model

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

TWO ROUTES THAT DO NOT STREAM. `/` is the adapter's readiness target, cheap on purpose:
the adapter polls AWS_LWA_READINESS_CHECK_PATH (default "/") before forwarding the first
request, and the upstream example puts its stream there, paying for the whole stream at
every cold start.

NOTHING HERE PROVES THE FUNCTION URL. Uvicorn plus curl proves the app emits chunks
incrementally, which is the half that can be checked without an account. Whether Lambda
forwards them incrementally is a property of the deployed InvokeMode and the adapter, and
only a deploy can show it.
"""

import asyncio
import logging
import time

import boto3
from botocore.config import Config
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from settings import SettingsError, load_settings

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

app = FastAPI()

_SETTINGS = None
_SETTINGS_ERROR = None
_BEDROCK_CLIENT = None


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


@app.get("/stream")
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


@app.get("/model")
async def model(q: str = "Say hello and name one thing SJSU students ask about.") -> StreamingResponse:
    """One real Bedrock turn, streamed. The default question exists so the route can be
    curled with no arguments at all - the first thing anyone does to a probe."""
    return StreamingResponse(_model_deltas(q), media_type="text/plain")

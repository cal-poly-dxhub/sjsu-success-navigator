"""A response-streaming PROBE. It proves a mechanism and carries no product logic.

WHY IT EXISTS. The WebSocket section of infra_stack.py says, correctly, that "Lambda
response streaming is Node.js/custom-runtime only and the agent loop is Python" - so the
stream was moved out of band, onto a socket and three functions. That sentence is true of
the PYTHON MANAGED RUNTIME and false of Lambda: the AWS Lambda Web Adapter runs an
ordinary ASGI app as an execution wrapper and streams its response body out through a
Function URL, which is the supported way to get in-band streaming out of Python
(aws/aws-lambda-web-adapter, examples/fastapi-response-streaming-zip). This module is that
example morphed onto this repo's zip-from-source pipeline, and it exists so the commit
that moves real logic onto the mechanism is a move rather than a bring-up.

IT IS DELIBERATELY TRIVIAL. No Bedrock, no store, no settings, no imports from anywhere
else in app/ - a timer and a counter. The question a probe has to answer is "did the bytes
leave in pieces, spread over time", and a real agent loop in the way only adds ways for
the answer to be yes-but. When the answer is proven, the generator is what gets replaced.

TWO ROUTES, NOT ONE, AND THE SPLIT IS LOAD-BEARING. The adapter polls the app for
readiness before it forwards the first request, at AWS_LWA_READINESS_CHECK_PATH - which
defaults to "/". The upstream example puts its stream on "/" and therefore pays for the
whole stream once at every cold start. Here "/" is a cheap 200 and the stream lives at
/stream, so the readiness check costs a dict.

NOTHING HERE PROVES THE FUNCTION URL. Uvicorn plus curl proves that the app emits chunks
incrementally, which is the half that can be checked without an account. Whether Lambda
forwards them incrementally is a property of the deployed InvokeMode and the adapter, and
only a deploy can show it.
"""

import asyncio
import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

# Ten chunks half a second apart: five seconds of wall clock, which is long enough that
# "arrived spread out" and "arrived all at once" cannot be confused for each other in a
# curl trace, and short enough to sit far inside the function's timeout.
CHUNK_COUNT = 10
CHUNK_INTERVAL_SECONDS = 0.5

app = FastAPI()


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
    """The probe itself.

    text/plain rather than text/event-stream: SSE framing is a contract with a browser
    client, and there is no client here. Plain lines are what `curl -N` renders as they
    land, which is the whole point of the route.
    """
    return StreamingResponse(_timed_chunks(), media_type="text/plain")

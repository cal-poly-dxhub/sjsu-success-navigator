"""Chat Lambda entrypoint - bare handler, HTTP API payload 2.0. No FastAPI, no Mangum.

Camp's main.py and its routers are replaced by this file; the service modules alongside it
(settings, models, prompts, tools, retrieve, cards, safety, orchestrator) move in as files.

Request order:

  1. validate    - parse the body, reject a missing or oversized query as a clean 400 before
                   anything is billed.
  2. guardrail   - ApplyGuardrail(source=INPUT) on the BARE query, PROMPT_ATTACK only. A block
                   returns the configured message with no retrieval and no generation.
  3. agent loop  - the Bedrock Converse tool-use loop under Sammy's system prompt. Safety is
                   the model's triage call (decision, 2026-08-10): the prompt carries the
                   emergency instruction and a keyed resource roster, the model emits a
                   <safety> block, and app/safety.py resolves the keys into the fixed contact
                   panel. There is no pre-model phrase gate.

Wiring comes from env vars set by the CDK stack (see settings.py). The response body is the
camelCase wire contract the frontend expects, produced by the pydantic aliases in models.py.
"""

import base64
import json
import logging
import time

import boto3
from botocore.config import Config

from models import ChatRequest
from orchestrator import run_chat
from settings import load_settings

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Settings and clients at module scope: resolved once per container, not per request.
# A missing environment variable raises here, on the first invocation, naming the
# variable - rather than surfacing later as a 502 on a student's question.
SETTINGS = load_settings()

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


def _response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def apply_input_guardrail(query):
    """Screen the BARE student query with ApplyGuardrail(source=INPUT).

    Returns the guardrail's replacement text when it blocks, or None to continue. The
    query alone is screened - not the system prompt, not retrieved passages - because
    PROMPT_ATTACK is about what the student sent.

    A guardrail FAILURE is not a block: if the call itself errors, the request continues
    to the loop rather than refusing a legitimate question over an infrastructure fault.
    Bedrock is already the harder dependency behind it, and a student who hits a transient
    guardrail outage should not be told their question was rejected.
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


def lambda_handler(event, context):
    """POST /chat, in the order the module docstring fixes: validate, guardrail, loop.
    Safety handoffs come out of the loop - the model triages and emits keys, the server
    resolves them into the fixed panel (app/safety.py)."""
    data = _parse_body(event)
    query = (data or {}).get("query")
    if not isinstance(query, str) or not query.strip():
        return _response(400, {"error": "Missing 'query' in request body."})
    if len(query) > SETTINGS.max_query_chars:
        return _response(
            400,
            {"error": f"Query exceeds {SETTINGS.max_query_chars} characters."},
        )

    # STEP 2 - the guardrail screen.
    blocked_text = apply_input_guardrail(query)
    if blocked_text is not None:
        return _response(
            200,
            {
                "conversationalText": blocked_text,
                "statementBatches": None,
                "safetyHandoff": None,
                "talkToPersonAvailable": True,
            },
        )

    # STEP 3 - the agent loop, under both caps (iterations and wall clock).
    try:
        request = ChatRequest(
            query=query,
            followup=bool((data or {}).get("followup", False)),
            sessionId=(data or {}).get("sessionId"),
            history=(data or {}).get("history"),
        )
    except Exception:
        logger.exception("Invalid chat request body")
        return _response(400, {"error": "Invalid request body."})

    try:
        response = run_chat(request, SETTINGS, deadline=loop_deadline(context))
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
        "chat cards=%s safety=%s",
        sum(len(batch.cards) for batch in (response.statement_batches or [])),
        response.safety_handoff is not None,
    )
    return _chat_response(response)

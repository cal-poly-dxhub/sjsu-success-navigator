"""Chat Lambda entrypoint - bare handler, HTTP API payload 2.0. No FastAPI, no Mangum.

Camp's main.py and its routers are replaced by this file; the service modules alongside it
(settings, models, prompts, tools, retrieve, cards, safety, orchestrator) move in as files.

Request order is LOAD-BEARING and is enforced here rather than anywhere downstream
(docs/synthesis.md, docs/architecture-v1.py:146):

  1. validate    - parse the body, reject a missing or oversized query as a clean 400 before
                   anything is billed.
  2. safety      - the deterministic in-Lambda intercept, BEFORE any AWS call. A crisis
                   message returns the fixed handoff panel and never reaches the guardrail or
                   the model. This runs first because a guardrail block returns a fixed
                   refusal string: screening first would answer a student in crisis with
                   "I can't help with that" whenever their message also tripped the screen.
  3. guardrail   - ApplyGuardrail(source=INPUT) on the BARE query, PROMPT_ATTACK only. A block
                   returns the configured message with no retrieval and no generation.
  4. agent loop  - the Bedrock Converse tool-use loop under Sammy's system prompt.

Steps 3 and 4 land at build-plan bullets 5 and 6. This file currently wires 1 only, and
returns 501 rather than pretending to have answered.

Wiring comes from env vars set by the CDK stack (see settings.py). The response body is the
camelCase wire contract the frontend expects, produced by the pydantic aliases in models.py.
"""

import base64
import json


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


def lambda_handler(event, context):
    """POST /chat. Bullets 5 and 6 fill in the safety intercept, the guardrail screen and the
    agent loop; until then this validates the request and returns 501 rather than an answer it
    did not generate."""
    data = _parse_body(event)
    query = (data or {}).get("query")
    if not isinstance(query, str) or not query.strip():
        return _response(400, {"error": "Missing 'query' in request body."})

    return _response(
        501,
        {"error": "The chat agent loop is not wired yet (docs/build-plan.md bullets 5-6)."},
    )

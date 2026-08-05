"""Chat Lambda entrypoint - bare handler, HTTP API payload 2.0. No FastAPI, no Mangum.

Destination for build-plan bullets "pull camp agent loop..." and "pull camp card
parsing and the pre-model safety intercept": camp's main.py and routers are replaced
by this file; the service modules alongside it move in as files. Order inside a
request is load-bearing: safety intercept first, then ApplyGuardrail(source=INPUT),
then the Converse loop (docs/synthesis.md).
"""


def lambda_handler(event, context):
    raise NotImplementedError(
        "scaffold: the camp agent loop and routing land here (docs/build-plan.md)"
    )

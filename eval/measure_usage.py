#!/usr/bin/env python3
"""Measure what one real question actually COSTS, against the deployed stack.

Runs the real loop in this process against the deployed stack's own wiring, so every
call is real and billed. On-demand, never CI; see docs/eval-harness.md.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from pathlib import Path

import boto3
import yaml

DEFAULT_STACK = "SjsuNavigatorStack"
DEFAULT_PROFILE = "gavilan"
DEFAULT_REGION = "us-west-2"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _REPO_ROOT / "app"
_GROUND_TRUTH = Path(__file__).resolve().parent / "ground-truth.yaml"


def discover_chat_function(session, stack_name: str) -> str:
    """The chat function's real name, from the stack's own resources. Its physical name
    carries CloudFormation's hash, so a literal would measure a different account."""
    cfn = session.client("cloudformation")
    paginator = cfn.get_paginator("list_stack_resources")
    for page in paginator.paginate(StackName=stack_name):
        for resource in page["StackResourceSummaries"]:
            if resource["ResourceType"] != "AWS::Lambda::Function":
                continue
            if resource["LogicalResourceId"].startswith("ChatFunction"):
                return resource["PhysicalResourceId"]
    raise SystemExit(f"No ChatFunction* resource in stack {stack_name}.")


def load_deployed_settings(session, function_name: str):
    """Build app.settings.Settings from the DEPLOYED function's environment, which is what
    makes the measurement attributable to this stack rather than to config.yaml."""
    config = session.client("lambda").get_function_configuration(
        FunctionName=function_name
    )
    environment = config.get("Environment", {}).get("Variables", {})

    sys.path.insert(0, str(_APP_DIR))
    from settings import load_settings  # noqa: E402 - path set immediately above

    preserved = dict(os.environ)
    os.environ.update(environment)
    try:
        settings = load_settings()
    finally:
        os.environ.clear()
        os.environ.update(preserved)

    memory_mb = config["MemorySize"]
    return settings, environment, memory_mb


def lambda_billed_seconds(session, stack_name: str) -> tuple[float, int] | tuple[None, None]:
    """Mean BILLED duration of real /chat invocations, from the function's REPORT lines."""
    import time

    logs = session.client("logs")
    groups = logs.describe_log_groups(
        logGroupNamePrefix=f"{stack_name}-ChatFunctionLogGroup"
    )["logGroups"]
    if not groups:
        return None, None

    query_id = logs.start_query(
        logGroupName=groups[0]["logGroupName"],
        startTime=int(time.time()) - 30 * 86400,
        endTime=int(time.time()),
        queryString=(
            'filter @type = "REPORT" '
            "| stats count(*) as invocations, avg(@billedDuration) as avg_billed_ms"
        ),
    )["queryId"]

    for _ in range(30):
        time.sleep(1)
        result = logs.get_query_results(queryId=query_id)
        if result["status"] == "Complete":
            if not result["results"]:
                return None, None
            row = {f["field"]: f["value"] for f in result["results"][0]}
            return float(row["avg_billed_ms"]) / 1000.0, int(float(row["invocations"]))
    return None, None


class UsageRecorder:
    """Wrap the bedrock-runtime client so every Converse response's `usage` is captured,
    leaving the loop under measurement byte-identical to the deployed one."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[dict] = []

    def converse(self, **kwargs):
        response = self._inner.converse(**kwargs)
        usage = response.get("usage") or {}
        self.calls.append(
            {
                "input_tokens": usage.get("inputTokens", 0),
                "output_tokens": usage.get("outputTokens", 0),
            }
        )
        return response

    def __getattr__(self, name):
        return getattr(self._inner, name)


def measure_one(orchestrator, retrieve_module, settings, query: str, history):
    """One turn through the real loop. Returns its billable units."""
    from models import ChatRequest

    recorder = UsageRecorder(boto3.client("bedrock-runtime", region_name=settings.bedrock_region))
    orchestrator._BEDROCK_CLIENT = recorder

    retrievals = {"count": 0}
    original_retrieve = retrieve_module.retrieve_chunks

    def counting_retrieve(*args, **kwargs):
        retrievals["count"] += 1
        return original_retrieve(*args, **kwargs)

    orchestrator.retrieve_chunks = counting_retrieve
    try:
        response = orchestrator.run_chat(
            ChatRequest(query=query), settings, history=history
        )
    finally:
        orchestrator.retrieve_chunks = original_retrieve
        orchestrator._BEDROCK_CLIENT = None

    # A turn that retrieved nothing is a BROKEN measurement, not a cheap question:
    # the input tokens are missing the passages that dominate them.
    if retrievals["count"] == 0:
        raise SystemExit(
            "A turn completed with zero retrievals: the primed search failed, so these "
            "token counts do not include the retrieved passages. Refusing to report a "
            "measurement taken against an unreachable knowledge base."
        )

    return {
        "model_calls": len(recorder.calls),
        "input_tokens": sum(c["input_tokens"] for c in recorder.calls),
        "output_tokens": sum(c["output_tokens"] for c in recorder.calls),
        "retrievals": retrievals["count"],
        "answer": response.conversational_text or "",
    }


def measure_guardrail(session, settings, query: str) -> int:
    """The input screen, exactly as the handler runs it, for its billed text units."""
    client = session.client("bedrock-runtime", region_name=settings.bedrock_region)
    result = client.apply_guardrail(
        guardrailIdentifier=settings.input_guardrail_id,
        guardrailVersion=settings.input_guardrail_version,
        source="INPUT",
        content=[{"text": {"text": query}}],
    )
    usage = result.get("usage") or {}
    return int(usage.get("contentPolicyUnits", 0))


def load_questions(limit: int) -> list[str]:
    """Real questions from the ground-truth set, spread across its behaviours by stride."""
    data = yaml.safe_load(_GROUND_TRUTH.read_text())
    items = data.get("pairs") or []
    questions = [item["question"] for item in items if item.get("question")]
    if not questions:
        raise SystemExit("No questions found in ground-truth.yaml.")
    if limit >= len(questions):
        return questions
    stride = len(questions) / limit
    return [questions[int(i * stride)] for i in range(limit)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--stack-name", default=DEFAULT_STACK)
    parser.add_argument(
        "--questions",
        type=int,
        default=24,
        help="Single-turn questions to measure. Each is a real, billed Bedrock call.",
    )
    parser.add_argument(
        "--depth-probes",
        type=int,
        default=3,
        help="Probe questions re-asked at each depth, for the history slope.",
    )
    parser.add_argument(
        "--depth-turns",
        type=int,
        default=5,
        help="Depths to measure each probe at (0..n-1 prior turns).",
    )
    args = parser.parse_args()

    # PROCESS-WIDE, before anything under app/ is imported: those modules build clients
    # from the DEFAULT session, so without this they can quietly measure another account.
    os.environ["AWS_PROFILE"] = args.profile
    os.environ["AWS_DEFAULT_REGION"] = args.region
    os.environ.pop("AWS_ACCESS_KEY_ID", None)
    os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
    os.environ.pop("AWS_SESSION_TOKEN", None)

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    function_name = discover_chat_function(session, args.stack_name)
    settings, environment, memory_mb = load_deployed_settings(session, function_name)

    print(f"Measuring against {function_name}")
    print(f"  model            {settings.generation_model_id}")
    print(f"  knowledge base   {settings.knowledge_base_id}")
    print(f"  guardrail        {settings.input_guardrail_id} v{settings.input_guardrail_version}")
    print(f"  top-k / min score {settings.number_of_results} / {settings.retrieve_min_score}")
    print(f"  memory           {memory_mb} MB")
    print()

    sys.path.insert(0, str(_APP_DIR))
    import orchestrator
    import retrieve as retrieve_module
    from history import StoredMessage

    questions = load_questions(args.questions)

    singles = []
    for index, question in enumerate(questions, start=1):
        units = measure_one(orchestrator, retrieve_module, settings, question, history=())
        units["guardrail_units"] = measure_guardrail(session, settings, question)
        singles.append(units)
        print(
            f"  [{index}/{len(questions)}] {units['model_calls']} call(s), "
            f"{units['input_tokens']} in, {units['output_tokens']} out, "
            f"{units['retrievals']} retrieval(s)  {question[:52]!r}"
        )

    # ---- the depth slope, a CONTROLLED experiment; see docs/eval-harness.md -------------
    print("\nDepth series (same question, growing history):")
    transcript: list[StoredMessage] = []
    for index, (question, units) in enumerate(zip(questions, singles)):
        transcript.append(StoredMessage(role="user", text=question, sort_key=f"{index}u"))
        transcript.append(
            StoredMessage(role="assistant", text=units["answer"], sort_key=f"{index}a")
        )

    by_prior_turns: dict[int, list[float]] = {}
    probes = questions[: args.depth_probes]
    for probe_index, probe in enumerate(probes, start=1):
        for prior_turns in range(args.depth_turns):
            # Trimmed to the window the server shows the model, so the series flattens
            # past the trim exactly as production does.
            history = tuple(transcript[-(prior_turns * 2) :] if prior_turns else ())
            units = measure_one(
                orchestrator, retrieve_module, settings, probe, history=history
            )
            per_call = units["input_tokens"] / max(1, units["model_calls"])
            by_prior_turns.setdefault(prior_turns, []).append(per_call)
            print(
                f"  [probe {probe_index}, {prior_turns} prior turn(s)] "
                f"{units['model_calls']} call(s), {per_call:.0f} in/call"
            )

    billed_seconds, invocations = lambda_billed_seconds(session, args.stack_name)

    calls = [u["model_calls"] for u in singles]
    per_call_input = [u["input_tokens"] / max(1, u["model_calls"]) for u in singles]
    outputs = [u["output_tokens"] for u in singles]
    retrievals = [u["retrievals"] for u in singles]
    guardrails = [u["guardrail_units"] for u in singles]

    # Fitted on tokens PER CALL, or the loop-length effect contaminates it.
    slope = 0.0
    if len(by_prior_turns) > 1:
        points = [(k, statistics.mean(v)) for k, v in sorted(by_prior_turns.items())]
        mean_x = statistics.mean(p[0] for p in points)
        mean_y = statistics.mean(p[1] for p in points)
        denominator = sum((p[0] - mean_x) ** 2 for p in points)
        if denominator:
            slope = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points) / denominator

    print("\n" + "=" * 72)
    print("Paste into config.yaml under cost_model.measured:")
    print("=" * 72)
    block = {
        "sample_questions": len(singles),
        "model_calls_avg": round(statistics.mean(calls), 2),
        "context_tokens_per_call_base": round(statistics.mean(per_call_input)),
        "context_tokens_per_call_per_prior_turn": round(slope),
        "output_tokens_avg": round(statistics.mean(outputs)),
        "retrievals_avg": round(statistics.mean(retrievals), 2),
        "guardrail_content_units_avg": round(statistics.mean(guardrails), 2),
    }
    if billed_seconds is not None:
        block["chat_lambda_gb_seconds"] = round(billed_seconds * memory_mb / 1024.0, 2)
    for key, value in block.items():
        print(f"    {key}: {value}")
    if billed_seconds is not None:
        print(
            f"\n  (chat_lambda_gb_seconds from {invocations} real invocations: "
            f"{billed_seconds:.3f}s mean billed x {memory_mb} MB)"
        )
    print(f"\n  spread: model_calls {min(calls)}-{max(calls)}, "
          f"input/call {min(per_call_input):.0f}-{max(per_call_input):.0f}, "
          f"output {min(outputs)}-{max(outputs)}")
    print("  depth series, mean input tokens per call by prior turns: "
          + ", ".join(f"{k}:{statistics.mean(v):.0f}" for k, v in sorted(by_prior_turns.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

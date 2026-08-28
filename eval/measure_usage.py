#!/usr/bin/env python3
"""Measure what one real question actually costs, against the deployed stack.

Every call here is real and billed. On demand, never CI.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

import boto3
import yaml

DEFAULT_STACK = "SjsuNavigatorStack"
DEFAULT_PROFILE = "sjsu"
DEFAULT_REGION = "us-west-2"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _REPO_ROOT / "app"
_GROUND_TRUTH = Path(__file__).resolve().parent / "ground-truth.yaml"


def discover_chat_function(session, stack_name: str) -> str:
    """The physical name carries CloudFormation's hash, so a literal measures another account."""
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
    """From the deployed function's environment, so the measurement belongs to this stack."""
    config = session.client("lambda").get_function_configuration(
        FunctionName=function_name
    )
    environment = config.get("Environment", {}).get("Variables", {})

    sys.path.insert(0, str(_APP_DIR))
    from settings import load_settings  # noqa: E402 (path set immediately above)

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
    """Mean billed duration of real /chat invocations, from the function's REPORT lines."""
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
    """Captures every response's `usage` and its `modelId`, leaving the loop untouched."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[dict] = []
        self.guardrails: list[dict] = []

    def converse(self, **kwargs):
        response = self._inner.converse(**kwargs)
        usage = response.get("usage") or {}
        self.calls.append(
            {
                "model_id": kwargs.get("modelId"),
                "input_tokens": usage.get("inputTokens", 0),
                "output_tokens": usage.get("outputTokens", 0),
            }
        )
        return response

    def apply_guardrail(self, **kwargs):
        result = self._inner.apply_guardrail(**kwargs)
        self.guardrails.append(dict(result.get("usage") or {}))
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


def measure_one(orchestrator, retrieve_module, settings, query: str, history):
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

    # A turn that retrieved nothing is broken, not cheap: the passages dominate the input.
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


def audit_one(orchestrator, retrieve_module, titles, turn, settings, query: str) -> dict:
    """The instrument checked before it is read: app/usage.py against the service itself."""
    from models import ChatRequest
    from usage import TurnUsage

    recorder = UsageRecorder(boto3.client("bedrock-runtime", region_name=settings.bedrock_region))
    orchestrator._BEDROCK_CLIENT = recorder
    titles._BEDROCK_CLIENT = recorder

    usage = TurnUsage()
    retrievals = {"count": 0}
    original_retrieve = retrieve_module.retrieve_chunks

    def counting_retrieve(*args, **kwargs):
        retrievals["count"] += 1
        return original_retrieve(*args, **kwargs)

    orchestrator.retrieve_chunks = counting_retrieve
    try:
        turn.apply_input_guardrail(query, settings=settings, bedrock=recorder, usage=usage)
        response = orchestrator.run_chat(
            ChatRequest(query=query), settings, history=(), usage=usage
        )
        titles.generate_title(
            question=query,
            answer=response.conversational_text or "",
            settings=settings,
            deadline=time.monotonic() + settings.title_deadline_seconds,
            usage=usage,
        )
    finally:
        orchestrator.retrieve_chunks = original_retrieve
        orchestrator._BEDROCK_CLIENT = None
        titles._BEDROCK_CLIENT = None

    generation = [c for c in recorder.calls if c["model_id"] == settings.generation_model_id]
    title_calls = [c for c in recorder.calls if c["model_id"] == settings.title_model_id]
    return {
        "query": query,
        "bedrock_calls": len(recorder.calls),
        "bedrock_input": sum(c["input_tokens"] for c in recorder.calls),
        "bedrock_output": sum(c["output_tokens"] for c in recorder.calls),
        "bedrock_guardrail_units": sum(
            int(g.get("contentPolicyUnits", 0)) for g in recorder.guardrails
        ),
        "generation_calls": len(generation),
        "generation_input": sum(c["input_tokens"] for c in generation),
        "generation_output": sum(c["output_tokens"] for c in generation),
        "title_calls": len(title_calls),
        "title_input": sum(c["input_tokens"] for c in title_calls),
        "title_output": sum(c["output_tokens"] for c in title_calls),
        "usage": usage,
        "retrievals": retrievals["count"],
    }


def run_audit(orchestrator, retrieve_module, titles, turn, settings, questions) -> int:
    """Both shapes, because a tally that agrees on one call proves nothing about the loop."""
    print("Auditing app/usage.py against Bedrock's own usage blocks.")
    print(f"  generation  {settings.generation_model_id}")
    print(f"  title       {settings.title_model_id}\n")

    audited: list[dict] = []
    for query in questions:
        row = audit_one(orchestrator, retrieve_module, titles, turn, settings, query)
        audited.append(row)
        usage = row["usage"]
        agree = not _differences(row)
        print(
            f"  {'AGREE' if agree else 'DIFFER'}  "
            f"calls {row['bedrock_calls']}/{usage.model_calls}  "
            f"in {row['bedrock_input']}/{usage.input_tokens + usage.title_input_tokens}  "
            f"out {row['bedrock_output']}/{usage.output_tokens + usage.title_output_tokens}  "
            f"guardrail {row['bedrock_guardrail_units']}/{usage.guardrail_content_units}  "
            f"(generation {row['generation_calls']}, title {row['title_calls']})  "
            f"{query[:44]!r}"
        )
        shapes = {r["generation_calls"] for r in audited}
        if len(shapes) > 1 and max(shapes) > 1:
            break

    print("\n" + "=" * 72)
    print("Bedrock (raw) vs app/usage.py (the tally the panel prices), summed")
    print("=" * 72)
    rows = _comparison(audited)
    for label, raw, recorded in rows:
        print(f"  {label:<24} {raw:>8} {recorded:>8}   {'ok' if raw == recorded else 'MISMATCH'}")
    retrievals = sum(r["usage"].retrievals for r in audited)
    print(f"  {'retrievals':<24} {'-':>8} {retrievals:>8}   (no service counter to check)")

    print(
        f"\n  The split is the point: {sum(r['title_input'] for r in audited)} in / "
        f"{sum(r['title_output'] for r in audited)} out came from\n  "
        f"{settings.title_model_id}, not {settings.generation_model_id},\n"
        "  and the panel prices the generation fields at the generation rate."
    )
    mismatched = [label for label, raw, recorded in rows if raw != recorded]
    if mismatched:
        print(f"\n  MISMATCH on: {', '.join(mismatched)}")
        return 1
    return 0


def _comparison(audited: list[dict]) -> list[tuple[str, int, int]]:
    """Per model, because one total would pass whether or not the buckets were right."""
    def raw(key: str) -> int:
        return sum(r[key] for r in audited)

    def tally(field: str) -> int:
        return sum(getattr(r["usage"], field) for r in audited)

    return [
        ("model calls", raw("bedrock_calls"), tally("model_calls")),
        ("generation in", raw("generation_input"), tally("input_tokens")),
        ("generation out", raw("generation_output"), tally("output_tokens")),
        ("title in", raw("title_input"), tally("title_input_tokens")),
        ("title out", raw("title_output"), tally("title_output_tokens")),
        ("guardrail units", raw("bedrock_guardrail_units"), tally("guardrail_content_units")),
    ]


def _differences(row: dict) -> list[str]:
    return [label for label, raw, recorded in _comparison([row]) if raw != recorded]


def measure_guardrail(session, settings, query: str) -> int:
    """Exactly as the handler runs it, for its billed text units."""
    client = session.client("bedrock-runtime", region_name=settings.bedrock_region)
    result = client.apply_guardrail(
        guardrailIdentifier=settings.input_guardrail_id,
        guardrailVersion=settings.input_guardrail_version,
        source="INPUT",
        content=[{"text": {"text": query}}],
    )
    usage = result.get("usage") or {}
    return int(usage.get("contentPolicyUnits", 0))


def embedding_model_arn(session, settings) -> str:
    """The dimension AWS/Bedrock publishes the retriever's token counts under."""
    kb = session.client("bedrock-agent").get_knowledge_base(
        knowledgeBaseId=settings.knowledge_base_id
    )["knowledgeBase"]
    return kb["knowledgeBaseConfiguration"]["vectorKnowledgeBaseConfiguration"][
        "embeddingModelArn"
    ]


def print_before_and_after(block: dict) -> None:
    """The only place config.yaml is read: a measurement that consulted its own answer is not one."""
    config_path = _REPO_ROOT / "config.yaml"
    try:
        cost_model = yaml.safe_load(config_path.read_text())["cost_model"]
    except Exception as error:
        print(f"\n  (could not read {config_path.name} for a comparison: {error})")
        return

    old, rates = dict(cost_model["measured"]), cost_model["rates"]
    new = {**old, **block}
    print("\n  against the committed block:")
    for key, value in block.items():
        was = old.get(key)
        flag = "" if was == value else "   <-"
        print(f"    {key:<40} {str(was):>10} -> {str(value):>10}{flag}")
    print(
        f"\n    {'cost per message (costModel.ts, perMessage)':<40} "
        f"{per_message_cost(rates, old):>10.5f} -> {per_message_cost(rates, new):>10.5f}"
    )


def bedrock_by_model(session, started, finished, model_ids: dict) -> dict:
    """The split a deployed function's wire `usage` cannot be asked for, taken apart by model."""
    import datetime

    cloudwatch = session.client("cloudwatch")
    start = datetime.datetime.fromisoformat(started) - datetime.timedelta(seconds=15)
    end = datetime.datetime.fromisoformat(finished) + datetime.timedelta(seconds=15)

    measured = {}
    for role, model_id in model_ids.items():
        row = {}
        for metric in ("Invocations", "InputTokenCount", "OutputTokenCount"):
            points = cloudwatch.get_metric_statistics(
                Namespace="AWS/Bedrock",
                MetricName=metric,
                Dimensions=[{"Name": "ModelId", "Value": model_id}],
                StartTime=start,
                EndTime=end,
                # 60s is the finest AWS/Bedrock publishes; the reconciliation below makes it safe.
                Period=60,
                Statistics=["Sum"],
            )["Datapoints"]
            row[metric] = int(sum(point["Sum"] for point in points))
        measured[role] = row
    return measured


def reconcile(per_model: dict, totals: dict) -> None:
    """It has to add back up, because a window off by a minute produces plausible numbers."""
    generation, title = per_model["generation"], per_model["title"]
    checks = [
        ("model calls",
         generation["Invocations"] + title["Invocations"], totals["model_calls"]),
        ("input tokens",
         generation["InputTokenCount"] + title["InputTokenCount"], totals["input_tokens"]),
        ("output tokens",
         generation["OutputTokenCount"] + title["OutputTokenCount"], totals["output_tokens"]),
        ("retrievals", per_model["embedding"]["Invocations"], totals["retrievals"]),
    ]
    print("  reconciling CloudWatch against the run's own wire totals:")
    for label, from_metrics, from_wire in checks:
        mark = "ok" if from_metrics == from_wire else "MISMATCH"
        print(f"    {label:<14} {from_metrics:>8} {from_wire:>8}   {mark}")
    bad = [label for label, a, b in checks if a != b]
    if bad:
        raise SystemExit(
            f"CloudWatch and the transcript disagree on: {', '.join(bad)}. The per-model "
            "split would describe a different set of calls, so these are not this run's "
            "numbers. Re-run when the metrics have settled, or against a quiet account."
        )


def per_message_cost(rates: dict, measured: dict) -> float:
    """Mirrors `perMessage` in frontend/src/lib/costModel.ts and must move with it."""
    million = 1_000_000
    model = (
        measured["model_calls_avg"] * measured["context_tokens_per_call_base"] / million
        * rates["generation_input_per_1m"]
        + measured["output_tokens_avg"] / million * rates["generation_output_per_1m"]
    )
    guardrail = measured["guardrail_content_units_avg"] / 1000 * rates[
        "guardrail_content_per_1k_units"
    ]
    retrieval = measured["retrievals_avg"] * (
        rates["vector_query_per_1m"] / million
        + measured["retrieval_query_tokens"] / million * rates["embedding_per_1m"]
    )
    plumbing = (
        rates["api_requests_per_1m"] / million
        + rates["cloudfront_per_1m_requests"] / million
        + rates["lambda_per_1m_requests"] / million
        + measured["chat_lambda_gb_seconds"] * rates["lambda_per_gb_second"]
        + measured["chat_dynamodb_writes"] / million * rates["dynamodb_write_per_1m"]
        + measured["chat_dynamodb_reads"] / million * rates["dynamodb_read_per_1m"]
    )
    return model + guardrail + retrieval + plumbing


def load_eval_run(path: Path) -> tuple[list[dict], dict, str, str]:
    """Turns through the real front door. Nothing here is re-asked or re-billed."""
    import json

    transcript = json.loads(path.read_text())
    run = transcript.get("run") or {}
    turns = []
    for result in transcript.get("results") or []:
        if result.get("status") != 200:
            continue
        response = result.get("response") or {}
        usage = response.get("usage")
        if not usage:
            continue
        turns.append(
            {
                "question": result["question"],
                "answer": response.get("conversationalText") or "",
                "model_calls": int(usage.get("modelCalls", 0)),
                "input_tokens": int(usage.get("inputTokens", 0)),
                "output_tokens": int(usage.get("outputTokens", 0)),
                "retrievals": int(usage.get("retrievals", 0)),
                "guardrail_units": int(usage.get("guardrailContentUnits", 0)),
            }
        )
    if not turns:
        raise SystemExit(f"{path} carries no successful turn with a usage block.")

    started, finished = run.get("started_utc"), run.get("finished_utc")
    if not (started and finished):
        raise SystemExit(
            f"{path} predates the run window being recorded, so the per-model split cannot "
            "be taken. Re-run eval/run_eval.py to produce a transcript that carries "
            "started_utc and finished_utc."
        )
    return turns, run, started, finished


def load_questions(limit: int) -> list[str]:
    """Spread across the ground-truth set's behaviours by stride."""
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
    parser.add_argument(
        "--from-eval",
        default=None,
        help="Take the single-turn half from an eval/results transcript instead of asking "
             "again here. Those turns went through API Gateway and the chat Lambda under "
             "the eval account, which this process cannot do; the depth series below still "
             "runs locally, because it needs to control the history exactly.",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Check app/usage.py against Bedrock's own usage blocks and stop. Measures "
             "nothing; it is what has to hold before the numbers below mean anything.",
    )
    args = parser.parse_args()

    # Before anything under app/ is imported: those modules build clients from the default session.
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
    import titles
    import turn
    from history import StoredMessage

    questions = load_questions(args.questions)

    if args.audit:
        return run_audit(orchestrator, retrieve_module, titles, turn, settings, questions)

    per_model = None
    if args.from_eval:
        # Turns that already happened through the front door: nothing is re-asked or re-billed.
        singles, run, started, finished = load_eval_run(Path(args.from_eval))
        questions = [turn_["question"] for turn_ in singles]
        print(f"Reading {len(singles)} deployed turn(s) from {Path(args.from_eval).name}")
        print(f"  asked at {run.get('api_url')}")
        print(f"  window   {started} -> {finished}")
        per_model = bedrock_by_model(
            session,
            started,
            finished,
            {
                "generation": settings.generation_model_id,
                "title": settings.title_model_id,
                "embedding": embedding_model_arn(session, settings),
            },
        )
        reconcile(
            per_model,
            {
                key: sum(turn_[key] for turn_ in singles)
                for key in ("model_calls", "input_tokens", "output_tokens", "retrievals")
            },
        )
        generation, title = per_model["generation"], per_model["title"]
        print(
            f"\n  generation  {generation['Invocations']:>4} call(s), "
            f"{generation['InputTokenCount']:>7} in, {generation['OutputTokenCount']:>6} out"
        )
        print(
            f"  title       {title['Invocations']:>4} call(s), "
            f"{title['InputTokenCount']:>7} in, {title['OutputTokenCount']:>6} out"
        )
    else:
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

    # The depth slope, a controlled experiment.
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
            # Trimmed to the window the server shows the model, so the series flattens as it does.
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

    # Generation only: the panel multiplies these by the generation rate, so a folded-in
    # title token would be priced at the wrong model's price.
    query_tokens = None
    if per_model is not None:
        generation = per_model["generation"]
        embedding = per_model["embedding"]
        turns = len(singles)
        calls_avg = generation["Invocations"] / turns
        base_tokens = generation["InputTokenCount"] / max(1, generation["Invocations"])
        output_avg = generation["OutputTokenCount"] / turns
        if embedding["Invocations"]:
            query_tokens = embedding["InputTokenCount"] / embedding["Invocations"]
    else:
        calls_avg = statistics.mean(calls)
        base_tokens = statistics.mean(per_call_input)
        output_avg = statistics.mean(outputs)

    # Fitted on tokens per call, or the loop-length effect contaminates it.
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
        "model_calls_avg": round(calls_avg, 2),
        "context_tokens_per_call_base": round(base_tokens),
        "context_tokens_per_call_per_prior_turn": round(slope),
        "output_tokens_avg": round(output_avg),
        "retrievals_avg": round(statistics.mean(retrievals), 2),
        "guardrail_content_units_avg": round(statistics.mean(guardrails), 2),
    }
    if query_tokens is not None:
        block["retrieval_query_tokens"] = round(query_tokens)
    if billed_seconds is not None:
        block["chat_lambda_gb_seconds"] = round(billed_seconds * memory_mb / 1024.0, 2)
    for key, value in block.items():
        print(f"    {key}: {value}")

    print_before_and_after(block)
    if billed_seconds is not None:
        print(
            f"\n  (chat_lambda_gb_seconds from {invocations} real invocations: "
            f"{billed_seconds:.3f}s mean billed x {memory_mb} MB)"
        )
    wire = " (as the wire reported it, titling call included)" if per_model else ""
    print(f"\n  spread per turn{wire}: model_calls {min(calls)}-{max(calls)}, "
          f"input/call {min(per_call_input):.0f}-{max(per_call_input):.0f}, "
          f"output {min(outputs)}-{max(outputs)}")
    print("  depth series, mean input tokens per call by prior turns: "
          + ", ".join(f"{k}:{statistics.mean(v):.0f}" for k, v in sorted(by_prior_turns.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

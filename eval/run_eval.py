#!/usr/bin/env python3
"""Collect the deployed system's answers to the ground-truth questions. No scoring.

Headless, so it signs in as the pool's machine client. Needs boto3, httpx and PyYAML.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import fnmatch
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import boto3
import httpx
import yaml

DEFAULT_STACK = "SjsuNavigatorStack"
DEFAULT_PROFILE = "sjsu"
DEFAULT_REGION = "us-west-2"
DEFAULT_USERNAME = "eval-runner"
# The HTTP API integration cap is 30s, so past ~32s it is the gateway, not the answer.
REQUEST_TIMEOUT_S = 35.0


def discover_endpoint(profile: str, region: str, stack_name: str) -> dict:
    """The eval client, not the web one, which has no password flow."""
    session = boto3.Session(profile_name=profile, region_name=region)
    stacks = session.client("cloudformation").describe_stacks(StackName=stack_name)
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stacks["Stacks"][0]["Outputs"]}
    missing = [k for k in ("ChatApiUrl", "ChatEvalClientId") if k not in outputs]
    if missing:
        raise SystemExit(f"stack {stack_name} is missing output(s): {', '.join(missing)}")
    return {"api_url": outputs["ChatApiUrl"], "client_id": outputs["ChatEvalClientId"]}


def sign_in(client_id: str, region: str, username: str, password: str) -> str:
    """One unsigned InitiateAuth, access token back."""
    resp = httpx.post(
        f"https://cognito-idp.{region}.amazonaws.com/",
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        },
        json={
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": client_id,
            "AuthParameters": {"USERNAME": username, "PASSWORD": password},
        },
        timeout=20.0,
    )
    body = resp.json()
    if resp.status_code != 200:
        raise SystemExit(
            f"sign-in failed ({resp.status_code}): {body.get('message') or body.get('__type')}"
        )
    token = (body.get("AuthenticationResult") or {}).get("AccessToken")
    if not token:
            # A challenge here means the pool user has no permanent password.
        raise SystemExit(f"sign-in returned no access token: {json.dumps(body)[:300]}")
    return token


def classify(status: int | None, response: dict | None) -> str:
    """Response shape for the page badge. Not a judgment."""
    if status != 200 or response is None:
        return "error"
    if response.get("safetyHandoff"):
        return "safety"
    batches = response.get("statementBatches") or []
    if any(batch.get("cards") for batch in batches):
        return "cards"
    return "prose-only"


def ask_one(client: httpx.Client, api_url: str, token: str, pair: dict) -> dict:
    """A fresh single-turn conversation, with one retry on a throttle."""
    payload = {"query": pair["question"], "followup": False}
    headers = {"Authorization": f"Bearer {token}"}
    attempts = 0
    while True:
        attempts += 1
        started = time.monotonic()
        status, response, error = None, None, None
        try:
            r = client.post(api_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_S)
            status = r.status_code
            try:
                response = r.json()
            except ValueError:
                error = f"non-JSON body: {r.text[:500]}"
        except httpx.HTTPError as exc:
            error = f"{exc.__class__.__name__}: {exc}"
        latency = round(time.monotonic() - started, 2)
        if status == 429 and attempts == 1:
            time.sleep(2.0)
            continue
        if status is not None and status != 200 and response is not None:
            error = json.dumps(response)[:500]
            response = None
        return {
            "id": pair["id"],
            "question": pair["question"],
            "status": status,
            "latency_s": latency,
            "attempts": attempts,
            "behavior_fired": classify(status, response),
            "response": response,
            "error": error,
        }


def git_commit(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ground-truth", default=str(here / "ground-truth.yaml"))
    parser.add_argument("--ids", action="append", default=[],
                        help="glob over pair ids, repeatable (e.g. --ids 'safety-*')")
    parser.add_argument("--sample", type=int, default=0,
                        help="ask only N pairs, taken by an EVEN STRIDE through the file. The "
                             "file is grouped by behaviour, so a stride keeps the run's mix of "
                             "routing, factual, process, safety and out-of-scope questions "
                             "close to the set's - which a head or a random draw does not. "
                             "Same rule eval/measure_usage.py samples by.")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="parallel questions; stay well under the 10 rps throttle")
    parser.add_argument("--stack-name", default=DEFAULT_STACK)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--api-url", default=None, help="skip discovery, use this POST /chat URL")
    parser.add_argument("--client-id", default=None,
                        help="skip discovery, use this app client id (the MACHINE client)")
    parser.add_argument("--username", default=os.environ.get("EVAL_USERNAME", DEFAULT_USERNAME))
    parser.add_argument("--password-file", default=None,
                        help="file holding the eval account password (keep it OUTSIDE the repo); "
                             "default is the EVAL_PASSWORD env var")
    parser.add_argument("--out-dir", default=str(here / "results"))
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()

    password = None
    if args.password_file:
        password = Path(args.password_file).read_text().strip()
    else:
        password = os.environ.get("EVAL_PASSWORD")
    if not password:
        raise SystemExit("no password: set EVAL_PASSWORD or pass --password-file")

    doc = yaml.safe_load(Path(args.ground_truth).read_text())
    pairs = doc["pairs"]
    if args.ids:
        pairs = [p for p in pairs if any(fnmatch.fnmatch(p["id"], g) for g in args.ids)]
    if not pairs:
        raise SystemExit("no pairs matched --ids")
    if args.sample and args.sample < len(pairs):
        stride = len(pairs) / args.sample
        pairs = [pairs[int(i * stride)] for i in range(args.sample)]

    if args.api_url and args.client_id:
        endpoint = {"api_url": args.api_url, "client_id": args.client_id}
    else:
        endpoint = discover_endpoint(args.profile, args.region, args.stack_name)
        if args.api_url:
            endpoint["api_url"] = args.api_url

    token = sign_in(endpoint["client_id"], args.region, args.username, password)
    print(f"signed in as {args.username}; asking {len(pairs)} question(s) "
          f"at {endpoint['api_url']} (concurrency {args.concurrency})")

    with httpx.Client() as client:
        # Best-effort warm: the ungated GET /warm route exists for exactly this.
        try:
            client.get(endpoint["api_url"].replace("/chat", "/warm"), timeout=15.0)
        except httpx.HTTPError:
            pass

        started = time.monotonic()
        started_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        results: list[dict | None] = [None] * len(pairs)
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {
                pool.submit(ask_one, client, endpoint["api_url"], token, pair): i
                for i, pair in enumerate(pairs)
            }
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                result = future.result()
                results[i] = result
                done += 1
                print(f"  {done}/{len(pairs)}  {result['id']:<28} "
                      f"{result['status'] or 'ERR'!s:>4}  {result['latency_s']:>5}s  "
                      f"{result['behavior_fired']}")
        duration = round(time.monotonic() - started, 1)

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    latencies = [r["latency_s"] for r in results if r["status"] == 200]
    transcript = {
        "run": {
            "timestamp_utc": stamp,
            # The window, because measure_usage.py --from-eval reads CloudWatch over it.
            "started_utc": started_utc,
            "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "api_url": endpoint["api_url"],
            "ground_truth": str(Path(args.ground_truth).name),
            "git_commit": git_commit(here.parent),
            "question_count": len(pairs),
            "duration_s": duration,
            "median_latency_s": round(statistics.median(latencies), 2) if latencies else None,
        },
        "results": results,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / f"eval-{stamp}.json"
    transcript_path.write_text(json.dumps(transcript, indent=1))

    fired = {}
    for r in results:
        fired[r["behavior_fired"]] = fired.get(r["behavior_fired"], 0) + 1
    print(f"\ndone in {duration}s; behaviors: {fired}")
    print(f"transcript: {transcript_path}")

    if not args.no_render:
        sys.path.insert(0, str(here))
        import render_results
        html_path = transcript_path.with_suffix(".html")
        render_results.render(transcript_path, Path(args.ground_truth), None, html_path)
        print(f"page:       {html_path}")


if __name__ == "__main__":
    main()

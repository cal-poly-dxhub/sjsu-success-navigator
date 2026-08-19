"""Scraper Lambda handler: crawl list, scrape_pages, gated S3 upload, gated ingestion job.

A missing or malformed crawl list is fatal on purpose, and both change gates matter; see
docs/scraper.md. Runtime wiring comes from stack-set environment variables.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

import boto3

from scraper import (
    SeedListError,
    load_seed_pages,
    scrape_pages,
    seed_urls,
    slugify_url,
)

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

# boto3 lowercases user-metadata keys out of head_object, so keep this spelling lowercase.
CONTENT_HASH_METADATA_KEY = "content-sha256"

# Statuses that mean the data source is still occupied. The full enum is STARTING,
# IN_PROGRESS, COMPLETE, FAILED, STOPPING, STOPPED.
ACTIVE_INGESTION_STATUSES = ("STARTING", "IN_PROGRESS", "STOPPING")

DEFAULT_URL_LIST_FILE = "urls.csv"

# Where the bundled list is looked for, in order: /opt is where Lambda extracts a layer, and
# the layer stages repo-root data/ itself, so URL_LIST_FILE stays a bare filename. The
# function's own directory is second, which keeps a local run working.
SEED_LIST_DIRS = (Path("/opt"), Path(__file__).resolve().parent)


def _s3_client():
    return boto3.client("s3")


def _bedrock_agent_client():
    return boto3.client("bedrock-agent")


def seed_list_path(filename=None) -> Path:
    """Absolute path to the bundled crawl list, or a SeedListError naming everywhere we looked."""
    filename = filename or os.environ.get("URL_LIST_FILE") or DEFAULT_URL_LIST_FILE
    candidates = [directory / filename for directory in SEED_LIST_DIRS]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SeedListError(
        f"bundled crawl list {filename!r} not found. Looked in: "
        + ", ".join(str(c) for c in candidates)
        + ". It ships as a Lambda layer extracted to /opt, so an absent file means the layer is "
        "missing from the function or the filename in URL_LIST_FILE does not match the asset."
    )


# --- Change detection ----------------------------------------------------------------------


def content_fingerprint(markdown, metadata) -> str:
    """Stable sha256 over everything a run would upload for one page, timestamp excluded."""
    metadata = metadata or {}
    payload = "\x00".join(
        [
            markdown or "",
            metadata.get("source_url") or "",
            metadata.get("title") or "",
            metadata.get("section") or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stored_content_fingerprint(s3, bucket, key):
    """The fingerprint on the markdown object already in the bucket, or None for changed."""
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 - absent object is the normal case, not an error
        LOG.debug("no stored fingerprint for %s (%s)", key, exc)
        return None
    value = (head.get("Metadata") or {}).get(CONTENT_HASH_METADATA_KEY)
    return value if isinstance(value, str) else None


def latest_object_modified(s3, bucket):
    """The newest LastModified across the source bucket, or None if unreadable or empty."""
    latest = None
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents") or []:
                modified = obj.get("LastModified")
                if modified is not None and (latest is None or modified > latest):
                    latest = modified
    except Exception as exc:  # noqa: BLE001 - falls back to the this-run-changed-something rule
        LOG.warning("could not read bucket modification times (%s); ignoring", exc)
        return None
    return latest


def _metadata_body(metadata: dict) -> bytes:
    """Bedrock S3 metadata sidecar body. The `.md.metadata.json` name is load-bearing;
    see docs/scraper.md, The metadata sidecar."""
    attributes = {
        "source_url": metadata.get("source_url", ""),
        "title": metadata.get("title") or "",
        "section": metadata.get("section") or "",
        "scrape_timestamp": metadata.get("scrape_timestamp", ""),
    }
    return json.dumps({"metadataAttributes": attributes}).encode("utf-8")


def expected_kb_keys(urls):
    """Every object key the KB source bucket SHOULD hold after a healthy run."""
    keys = set()
    for url in urls:
        slug = slugify_url(url)
        keys.add(f"{slug}.md")
        keys.add(f"{slug}.md.metadata.json")
    return keys


def prune_stale_objects(s3, bucket, expected_keys):
    """Delete KB source objects the crawl list no longer calls for. Returns the deleted
    keys."""
    deleted: list[str] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents") or []:
                key = obj["Key"]
                if key in expected_keys:
                    continue
                s3.delete_object(Bucket=bucket, Key=key)
                deleted.append(key)
                LOG.info("pruned stale KB object: %s", key)
    except Exception as exc:  # noqa: BLE001 - pruning is housekeeping, never fatal
        LOG.exception("prune failed (ignored): %s", exc)
    return deleted


# --- Ingestion: one job at a time, and never lose a change ---------------------------------


def _ingestion_job_summaries(bedrock_agent, kb_id, data_source_id, max_results=20):
    """Recent ingestion jobs for this data source, newest first. [] if unreadable."""
    try:
        response = bedrock_agent.list_ingestion_jobs(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
            maxResults=max_results,
        )
        summaries = response.get("ingestionJobSummaries")
        return list(summaries) if summaries else []
    except Exception as exc:  # noqa: BLE001
        LOG.warning("could not list ingestion jobs (%s); proceeding without job history", exc)
        return []


def active_ingestion_job(summaries):
    """The id of a job currently STARTING/IN_PROGRESS/STOPPING, or None."""
    for job in summaries:
        if job.get("status") in ACTIVE_INGESTION_STATUSES:
            return job.get("ingestionJobId")
    return None


def last_ingestion_started_at(summaries):
    """When the most recent job STARTED, or None. Started-at can only over-trigger."""
    started = None
    for job in summaries:
        at = job.get("startedAt")
        if at is not None and (started is None or at > started):
            started = at
    return started


def should_start_ingestion(changed_this_run, bucket_latest, last_started):
    """Whether to start an ingestion job, and the human-readable reason. Never raises."""
    if changed_this_run:
        return True, "content changed this run"
    if bucket_latest is not None and last_started is not None and bucket_latest > last_started:
        # Something got into the bucket no ingestion job has covered: almost always a
        # previous run that deferred because a job was already running.
        return True, "bucket holds objects newer than the last ingestion job"
    return False, "nothing changed"


def start_ingestion(
    bedrock_agent, kb_id, data_source_id, summaries, changed_this_run, bucket_latest
):
    """Start an ingestion job if one is warranted and none is running. Never raises."""
    running = active_ingestion_job(summaries)
    if running:
        LOG.info("ingestion job %s already running; deferring to the next scheduled run", running)
        return None, f"deferred (job {running} in progress)"

    wanted, reason = should_start_ingestion(
        changed_this_run, bucket_latest, last_ingestion_started_at(summaries)
    )
    if not wanted:
        LOG.info("no ingestion needed: %s", reason)
        return None, f"skipped ({reason})"

    try:
        response = bedrock_agent.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            description="Automated scraper re-sync",
        )
        job_id = response["ingestionJob"]["ingestionJobId"]
        LOG.info("started ingestion job %s (%s)", job_id, reason)
        return job_id, f"started ({reason})"
    except Exception as exc:  # noqa: BLE001
        # A job that raced us, or a throttle on the one-per-ten-seconds limit. Both are the
        # overlap case and both self-heal next run.
        LOG.warning("could not start ingestion job (%s: %s); deferring", type(exc).__name__, exc)
        return None, f"deferred ({type(exc).__name__})"


def handler(event, context):
    # Loaded first and deliberately UNGUARDED: a SeedListError fails the invocation before
    # anything is deleted.
    started_at = time.monotonic()
    pages = load_seed_pages(seed_list_path())
    all_urls = seed_urls(pages)
    timeout = float(os.environ.get("SCRAPE_TIMEOUT_SECONDS", "20"))
    user_agent = os.environ.get("SCRAPER_USER_AGENT") or None
    bucket = os.environ["SOURCE_BUCKET"]
    kb_id = os.environ["KNOWLEDGE_BASE_ID"]
    data_source_id = os.environ["DATA_SOURCE_ID"]

    LOG.info("scraping %d configured page(s)", len(pages))
    kwargs = {"timeout": timeout}
    if user_agent:
        kwargs["user_agent"] = user_agent
    results = scrape_pages(pages, **kwargs)

    s3 = _s3_client()
    uploaded_keys: list[str] = []
    # Every key this run confirmed is LIVE, re-uploaded or found unchanged. The prune unions
    # these in, so a run can never delete a page it just looked at.
    live_object_keys: list[str] = []
    unchanged_keys: list[str] = []
    failures: list[dict] = []
    for result in results:
        if not result.ok:
            LOG.warning("scrape failed: %s (%s)", result.url, result.error)
            failures.append({"url": result.url, "error": result.error})
            continue
        md_key = f"{result.slug}.md"
        meta_key = f"{result.slug}.md.metadata.json"

        # GATE 1: upload only what changed. The sidecar rides with the markdown, because its
        # timestamp moves every run.
        fingerprint = content_fingerprint(result.markdown, result.metadata)
        if stored_content_fingerprint(s3, bucket, md_key) == fingerprint:
            unchanged_keys.append(md_key)
            live_object_keys.extend((md_key, meta_key))
            LOG.info("unchanged, not uploaded: %s (<- %s)", md_key, result.url)
            continue

        s3.put_object(
            Bucket=bucket,
            Key=md_key,
            Body=result.markdown.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
            Metadata={CONTENT_HASH_METADATA_KEY: fingerprint},
        )
        s3.put_object(
            Bucket=bucket,
            Key=meta_key,
            Body=_metadata_body(result.metadata),
            ContentType="application/json",
        )
        uploaded_keys.append(md_key)
        live_object_keys.extend((md_key, meta_key))
        LOG.info("uploaded %s (<- %s)", md_key, result.url)

    # Prune BEFORE ingestion, so one job indexes the new content and retires the removed.
    expected = expected_kb_keys(all_urls) | set(live_object_keys)
    pruned_keys = prune_stale_objects(s3, bucket, expected)

    LOG.info(
        "scrape complete: %d uploaded, %d unchanged, %d failed, %d pruned",
        len(uploaded_keys),
        len(unchanged_keys),
        len(failures),
        len(pruned_keys),
    )

    # GATE 2: ingest only when the bucket actually moved (or still holds unindexed content).
    bedrock_agent = _bedrock_agent_client()
    summaries = _ingestion_job_summaries(bedrock_agent, kb_id, data_source_id)
    changed_this_run = bool(uploaded_keys or pruned_keys)
    # Only worth asking when this run changed nothing.
    bucket_latest = None if changed_this_run else latest_object_modified(s3, bucket)
    ingestion_job_id, ingestion_status = start_ingestion(
        bedrock_agent,
        kb_id,
        data_source_id,
        summaries,
        changed_this_run=changed_this_run,
        bucket_latest=bucket_latest,
    )

    # One structured line per run. duration_seconds is here for the 15-minute timeout
    # question in docs/scraper.md, One daily run, no tiers.
    summary = {
        "pages_configured": len(pages),
        "pages_fetched": len(results),
        "pages_changed": len(uploaded_keys),
        "pages_unchanged": len(unchanged_keys),
        "pages_failed": len(failures),
        "objects_pruned": len(pruned_keys),
        "ingestion": ingestion_status,
        "ingestion_job_id": ingestion_job_id,
        "duration_seconds": round(time.monotonic() - started_at, 1),
    }
    LOG.info("scrape run summary: %s", json.dumps(summary, sort_keys=True, default=str))

    return {
        "uploaded": len(uploaded_keys),
        "unchanged": len(unchanged_keys),
        "pruned": pruned_keys,
        "failed": failures,
        "ingestionJobId": ingestion_job_id,
        "ingestion": ingestion_status,
        "summary": summary,
    }

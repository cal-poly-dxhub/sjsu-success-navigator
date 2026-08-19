"""Scraper Lambda handler: crawl list -> scrape_pages -> gated S3 upload -> gated ingestion job.

Thin wrapper over scraper.py (the fetch + extract logic is NOT reimplemented here). On each
invocation it scrapes the WHOLE curated crawl list, uploads the markdown + a metadata sidecar for
every page whose content actually CHANGED, and triggers a Bedrock ingestion job only when the
source bucket actually moved. Partial failures are tolerated: failed pages are logged and skipped,
and the run continues.

ONE DAILY RUN, NO TIERS. Every invocation is the complete sweep, so there is no tier to pass and
nothing in the event is read. 203 curated pages sweep in a few minutes and change gating means an
unchanged day pays only the Lambda run, so the freshness/complexity trade gav made with tiers is
not worth making here (docs/build-plan.md, "Resolved").

THE CRAWL LIST ARRIVES AS A BUNDLED ASSET, NOT AS AN ENV VAR. Lambda caps all environment
variables at 4 KB in AGGREGATE and the limit cannot be raised; the 203-page list is 19 KB as
compact JSON of url/section pairs and 2.9 KB even gzipped and base64'd, so gav's approach of
shipping the seed URLs inside SCRAPER_TIERS does not survive the scale-up. The list ships as a
Lambda layer instead, extracted to /opt, and the only thing in the environment is its FILENAME -
the same value the stack feeds to the asset, so the two cannot drift.

A MISSING OR MALFORMED LIST IS FATAL, DELIBERATELY. load_seed_pages raises, this handler does not
catch it, and the invocation fails. That is the safe direction: continuing with zero pages would
hand prune_stale_objects an empty expected set, delete every document in the knowledge base, and
then start an ingestion job over the wreckage. A Lambda that errors out has pruned nothing.

CHANGE GATING, two gates, because the site changes far less often than we look at it:
  1. Upload only what changed. Each markdown object carries a `content-sha256` of exactly what
     this scraper produced for that page; a page whose fingerprint matches what is already in the
     bucket is not re-uploaded. (Bedrock's sync is incremental on its own, but re-PUTting an
     object still moves its LastModified and re-parses it, so gating here is what makes an
     unchanged run genuinely free.)
  2. Ingest only when something moved. No uploads and no prunes -> no ingestion job.

It also PRUNES: objects the crawl list no longer calls for are deleted from the source bucket
before ingestion, so removing a page from the list actually removes it from the knowledge base
instead of leaving it indexed forever (see prune_stale_objects).

NO NEW STORE for any of this. Change detection reads S3 object metadata (the per-object
fingerprint), S3 object LastModified, and Bedrock's own ingestion-job history. There is no
DynamoDB table, no state file, and nothing to back up or clean up.

Runtime wiring (all from stack-set env vars; boto3 from the runtime, deps from the layer):
  URL_LIST_FILE           filename of the bundled crawl list (resolved under /opt)
  SCRAPE_TIMEOUT_SECONDS  per-request HTTP timeout
  SCRAPER_USER_AGENT      identifying User-Agent
  SOURCE_BUCKET           KB S3 source bucket to upload into
  KNOWLEDGE_BASE_ID       KB to start ingestion on
  DATA_SOURCE_ID          the KB's S3 data source id
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

# S3 user-metadata key holding the fingerprint of what this scraper produced for a page. Written
# on every markdown PUT, read back to decide whether the next run needs to PUT at all. boto3
# lowercases user-metadata keys on the way out of head_object, so keep this spelling lowercase.
CONTENT_HASH_METADATA_KEY = "content-sha256"

# Ingestion-job statuses that mean the data source is still occupied. Bedrock allows exactly one
# job at a time, so seeing any of these means we defer rather than call and fail. STOPPING counts:
# the job has not released the data source yet. (The full enum is STARTING, IN_PROGRESS, COMPLETE,
# FAILED, STOPPING, STOPPED.)
ACTIVE_INGESTION_STATUSES = ("STARTING", "IN_PROGRESS", "STOPPING")

DEFAULT_URL_LIST_FILE = "urls.csv"

# Where the bundled crawl list is looked for, in order. /opt is where Lambda extracts layer
# content, which is how the list gets in (see the module docstring) - the layer stages the
# repo-root data/ directory itself, so its files land at /opt directly and URL_LIST_FILE stays a
# bare filename. The function's own directory is second so a local test run - or a future commit
# that moves the list into the function bundle - keeps working without touching this file.
SEED_LIST_DIRS = (Path("/opt"), Path(__file__).resolve().parent)


def _s3_client():
    return boto3.client("s3")


def _bedrock_agent_client():
    return boto3.client("bedrock-agent")


def seed_list_path(filename=None) -> Path:
    """Absolute path to the bundled crawl list, or a SeedListError naming everywhere we looked.

    The env var carries a FILENAME, never a path and never the list's contents (Lambda's 4 KB
    environment cap - see the module docstring). Raising here rather than returning a default
    keeps the fatal-on-missing-list guarantee: the run dies before the prune.
    """
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
#
# The daily schedule rests on one property: a page whose content has not changed must produce the
# SAME fingerprint run after run. That held on gav's corpus - all 19 pages byte-identical across
# two back-to-back scrapes, with the sidecar's scrape_timestamp the only field that moved - which
# is why the fingerprint below covers the document body and the sidecar fields Bedrock actually
# consumes, and deliberately NOT the timestamp. Hashing the timestamp would mark every page
# changed on every run and turn the gating into an expensive no-op.
#
# NOT YET MEASURED ON THIS CORPUS: sjsu.edu pages have not been scraped twice and compared (no
# account, and the scraper has never run). A page carrying a per-render nonce, a rotating
# announcement, or a "last updated" line inside the extracted body would defeat gate 1 for that
# page. The cost of being wrong is re-uploads and re-ingestion, not incorrect answers, and the run
# summary's pages_changed count is where it would show up.


def content_fingerprint(markdown, metadata) -> str:
    """Stable sha256 over everything a run would upload for one page.

    Covers the markdown body plus the sidecar fields that reach the knowledge base (source_url,
    title and section - the first two drive citation attribution and the third drives card ranking
    and follow-ups), so a re-sectioned or retitled page re-uploads even when the body is untouched.
    Excludes scrape_timestamp, which changes on every run by definition.
    """
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
    """The fingerprint recorded on the markdown object already in the bucket, or None.

    None means "treat this page as changed": the object is absent (first run), or it predates
    change gating and carries no fingerprint, or S3 could not be read. Every one of those wants
    an upload, so the failure direction is a redundant PUT rather than a missed update.
    """
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 - absent object is the normal case, not an error
        LOG.debug("no stored fingerprint for %s (%s)", key, exc)
        return None
    value = (head.get("Metadata") or {}).get(CONTENT_HASH_METADATA_KEY)
    return value if isinstance(value, str) else None


def latest_object_modified(s3, bucket):
    """The newest LastModified across the source bucket, or None if unreadable/empty.

    Paired with the last ingestion job's start time this answers "does the bucket hold content
    the knowledge base has not indexed yet?" without storing anything of our own - which is what
    makes a deferred ingestion self-healing. See should_start_ingestion.
    """
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
    """Bedrock S3 metadata sidecar body.

    Uploaded as `<slug>.md.metadata.json`, NOT `<slug>.json`: Bedrock treats `*.metadata.json` as
    METADATA for the sibling document and does NOT ingest it as its own document. A plain
    `<slug>.json` (the local scraper's inspection filename) WOULD be ingested as a JSON document,
    polluting the KB. The wrapper is Bedrock's documented `metadataAttributes` shape.

    `section` rides along with source_url and title because app/cards.py reads it back off every
    retrieved chunk: it deprioritizes noisy sections and picks each card's follow-up action from
    it. Drop it here and both degrade silently - the cards still render, just ranked wrong and
    with a generic button, and nothing in the logs says why.
    """
    attributes = {
        "source_url": metadata.get("source_url", ""),
        "title": metadata.get("title") or "",
        "section": metadata.get("section") or "",
        "scrape_timestamp": metadata.get("scrape_timestamp", ""),
    }
    return json.dumps({"metadataAttributes": attributes}).encode("utf-8")


def expected_kb_keys(urls):
    """Every object key the KB source bucket SHOULD hold after a healthy run.

    Derived from the crawl list, NOT from what this run happened to upload - see
    prune_stale_objects for why that distinction is load-bearing."""
    keys = set()
    for url in urls:
        slug = slugify_url(url)
        keys.add(f"{slug}.md")
        keys.add(f"{slug}.md.metadata.json")
    return keys


def prune_stale_objects(s3, bucket, expected_keys):
    """Delete KB source objects the crawl list no longer calls for. Returns the deleted keys.

    WHY THIS EXISTS: the uploader only ever put_objects, so without a prune a page removed from
    the crawl list simply stops being refreshed while its document stays in the bucket and stays
    indexed forever. De-listing would be a silent no-op.

    WHY IT PRUNES AGAINST THE LIST, NOT AGAINST THIS RUN'S UPLOADS: pruning by what succeeded
    would make one transient fetch failure delete that page from the knowledge base - a 404 on the
    financial-aid index for a single day would drop every deadline answer the bot has until
    someone noticed. Keying on the crawl list means a failed fetch leaves the last-good document
    in place.

    Never raises: a prune failure must not break a scrape that has already succeeded."""
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
#
# Bedrock allows exactly one ingestion job per data source (StartIngestionJob is also rate-limited
# to one per ten seconds), so an overlap - a manual invoke landing on top of the schedule, or the
# deploy trigger firing while a scheduled run is mid-flight - has to SKIP cleanly rather than
# throw.
#
# Skipping is only safe if the skipped change is picked up later, and "this run uploaded
# something" cannot provide that: the next run finds the page unchanged, uploads nothing, and
# would start no job - leaving content sitting in the bucket unindexed indefinitely. The fix is a
# second, store-free signal: compare the newest object in the bucket against the start time of the
# last ingestion job Bedrock ran. Anything newer is unindexed, whoever put it there and whenever.
# A deferred run therefore heals itself on the next daily run.


def _ingestion_job_summaries(bedrock_agent, kb_id, data_source_id, max_results=20):
    """Recent ingestion jobs for this data source, newest first. [] if unreadable.

    Soft-fails on purpose: losing visibility of job history must degrade the decision below to
    "start when this run changed something" (the un-gated behaviour), never block ingestion.
    """
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
    """When the most recent job STARTED, or None if there is no history.

    Uses started-at rather than completed-at because a job indexes the bucket as of roughly when
    it began; comparing against the start time can only over-trigger (a harmless extra incremental
    sync), while comparing against completion could skip an object written mid-job.
    """
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
        # Something got into the bucket that no ingestion job has covered - almost always a
        # previous run that deferred because a job was already running.
        return True, "bucket holds objects newer than the last ingestion job"
    return False, "nothing changed"


def start_ingestion(
    bedrock_agent, kb_id, data_source_id, summaries, changed_this_run, bucket_latest
):
    """Start an ingestion job if one is warranted and none is running. Returns (job_id, status).

    Never raises: an ingestion problem must not fail a scrape whose uploads already landed, and
    the bucket-newer-than-last-job rule above means the next run retries on its own.
    """
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
        # A job that raced us between the list and the start, or a throttle on the one-per-ten-
        # seconds limit, lands here. Both are the overlap case and both self-heal next run.
        LOG.warning("could not start ingestion job (%s: %s); deferring", type(exc).__name__, exc)
        return None, f"deferred ({type(exc).__name__})"


def handler(event, context):
    # The crawl list, which is the corpus: what this run fetches AND what the prune keeps. Loaded
    # first and deliberately UNGUARDED - a SeedListError here fails the invocation before
    # anything is deleted (see the module docstring).
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
    # Every object key this run confirmed is LIVE (markdown AND sidecars), whether it was
    # re-uploaded or found unchanged. The prune guard below unions these in, so a run can never
    # delete a page it just looked at. Change gating is what makes the "found unchanged" half
    # necessary: without gating every successful page is re-uploaded every run, so the uploaded
    # set happens to cover the whole corpus and the distinction does not exist.
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

        # GATE 1: upload only what changed. The sidecar rides with the markdown rather than being
        # gated separately - it carries a scrape_timestamp that moves every run, so gating it on
        # its own bytes would re-upload all of them forever and re-trigger ingestion each time.
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

    # Drop anything the crawl list no longer calls for, BEFORE ingestion, so the same job that
    # indexes the new content also retires the removed content.
    #
    # The live keys are unioned in as a belt-and-braces guard: expected_kb_keys derives slugs from
    # the crawl-list URL while the uploader uses result.slug, and although both slugify the same
    # input today, any future divergence (a redirect used for slugging, a slug scheme change)
    # would otherwise make the prune delete the very objects this run just confirmed. Whatever
    # this run saw as a live page is by definition wanted - including the ones it left alone
    # because they had not changed.
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
    # Only worth asking when this run changed nothing: that is the case where the answer decides
    # between "genuinely nothing to do" and "a previous run deferred and left content unindexed".
    bucket_latest = None if changed_this_run else latest_object_modified(s3, bucket)
    ingestion_job_id, ingestion_status = start_ingestion(
        bedrock_agent,
        kb_id,
        data_source_id,
        summaries,
        changed_this_run=changed_this_run,
        bucket_latest=bucket_latest,
    )

    # One structured line per run, so "how fresh is the bot?" is a log query rather than a guess.
    #
    # duration_seconds is here for a specific open question: the corpus was sized by scaling gav's
    # measured 19-pages-in-25-67s to 203 pages, which lands at ~4.5-12 minutes against a 15-minute
    # timeout. The top of that range is close enough that the first live runs need to be read
    # rather than assumed (docs/build-plan.md, "Resolved").
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

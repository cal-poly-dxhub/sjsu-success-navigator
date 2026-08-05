"""Tests for the scraper Lambda handler. No live network/AWS: scrape_pages and the S3 /
bedrock-agent clients are monkeypatched, and boto3 is stubbed (it's provided by the Lambda runtime,
not installed in the scraper venv).

Two properties carry most of the weight, because both fail SILENTLY and both destroy the corpus:

  1. A bad crawl list must fail the invocation BEFORE the prune. A run that fetches nothing hands
     prune_stale_objects an empty expected set, which deletes every document in the bucket.
  2. The prune keys off the crawl LIST, never off what a run managed to fetch. Otherwise one 404
     on a page deletes its document and quietly drops those answers from the bot.
"""

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Stub boto3 BEFORE importing the handler (the handler does `import boto3` at module load). The
# tests replace the client getters, so boto3 itself is never called.
sys.modules.setdefault("boto3", types.ModuleType("boto3"))

import lambda_function as lf  # noqa: E402
from scraper import ScrapeResult, SeedListError  # noqa: E402

# The crawl list no longer travels in the environment - it ships as a bundled layer asset and only
# its FILENAME is an env var (Lambda's 4 KB aggregate env cap; see the handler docstring). So the
# fixture below stubs load_seed_pages/seed_list_path rather than setting a URLs variable.
BASE_ENV = {
    "URL_LIST_FILE": "url-list.csv",
    "SCRAPE_TIMEOUT_SECONDS": "15",
    "SCRAPER_USER_AGENT": "TestAgent/1.0",
    "SOURCE_BUCKET": "kb-bucket",
    "KNOWLEDGE_BASE_ID": "kb-123",
    "DATA_SOURCE_ID": "ds-456",
}

SEED_PAGES = [
    {"url": "https://x/a", "section": "academic-advising", "title": "Page A"},
    {"url": "https://x/b", "section": "financial-aid", "title": "Page B"},
]


def _ok(slug="x-a-hash"):
    return ScrapeResult(
        url="https://x/a",
        slug=slug,
        section="academic-advising",
        ok=True,
        title="Page A",
        markdown="# Page A\n\nbody text",
        metadata={
            "source_url": "https://x/a",
            "fetched_url": "https://x/a",
            "title": "Page A",
            "section": "academic-advising",
            "scrape_timestamp": "2026-08-05T00:00:00Z",
            "content_chars": 11,
            "scraper_version": "1",
        },
    )


def _fail():
    return ScrapeResult(url="https://x/b", slug="x-b", section="financial-aid", ok=False, error="HTTP 404")


def _wire(monkeypatch, results, pages=None):
    for k, v in BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(lf, "seed_list_path", lambda *a, **kw: Path("/opt/url-list.csv"))
    monkeypatch.setattr(lf, "load_seed_pages", lambda path: list(SEED_PAGES if pages is None else pages))
    monkeypatch.setattr(lf, "scrape_pages", lambda pages, **kw: results)
    s3, bedrock_agent = MagicMock(), MagicMock()
    # Empty head = no stored fingerprint = every page reads as changed, which is the state of a
    # fresh bucket. Tests that exercise the gating set a fingerprint explicitly.
    s3.head_object.return_value = {}
    bedrock_agent.start_ingestion_job.return_value = {
        "ingestionJob": {"ingestionJobId": "job-1", "status": "STARTING"}
    }
    # No job history by default: nothing running to defer behind, nothing already ingested.
    bedrock_agent.list_ingestion_jobs.return_value = {"ingestionJobSummaries": []}
    monkeypatch.setattr(lf, "_s3_client", lambda: s3)
    monkeypatch.setattr(lf, "_bedrock_agent_client", lambda: bedrock_agent)
    return s3, bedrock_agent


def _paginated(keys):
    """A MagicMock paginator whose paginate() yields one page of the given object keys."""
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": [{"Key": k} for k in keys]}]
    return paginator


def _job(job_id, status, started_at):
    return {"ingestionJobId": job_id, "status": status, "startedAt": started_at}


# --- The happy path ----------------------------------------------------------------------------


def test_uploads_markdown_and_metadata_and_triggers_ingestion(monkeypatch):
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])
    out = lf.handler({}, None)

    puts = {c.kwargs["Key"]: c.kwargs for c in s3.put_object.call_args_list}
    # Two objects per page: the markdown and a Bedrock metadata sidecar (NOT `<slug>.json`).
    assert set(puts) == {"x-a-hash.md", "x-a-hash.md.metadata.json"}
    assert puts["x-a-hash.md"]["Body"] == b"# Page A\n\nbody text"
    assert all(p["Bucket"] == "kb-bucket" for p in puts.values())
    meta = json.loads(puts["x-a-hash.md.metadata.json"]["Body"])
    assert meta["metadataAttributes"]["source_url"] == "https://x/a"
    assert meta["metadataAttributes"]["title"] == "Page A"

    bedrock_agent.start_ingestion_job.assert_called_once()
    ck = bedrock_agent.start_ingestion_job.call_args.kwargs
    assert ck["knowledgeBaseId"] == "kb-123"
    assert ck["dataSourceId"] == "ds-456"

    assert out["uploaded"] == 1
    assert out["failed"] == []
    assert out["ingestionJobId"] == "job-1"


def test_the_sidecar_carries_section_alongside_source_url_and_title(monkeypatch):
    # Not cosmetic and not covered by the assertions above. cards.py reads `section` off every
    # retrieved chunk to deprioritize noisy sections and to choose a card's follow-up button. Drop
    # it and the cards still render - ranked wrong, with a generic button, and nothing in the logs
    # explains why. It is the one metadata field whose absence is invisible at runtime.
    s3, _ = _wire(monkeypatch, [_ok()])
    lf.handler({}, None)

    sidecar = next(
        c.kwargs for c in s3.put_object.call_args_list if c.kwargs["Key"].endswith(".metadata.json")
    )
    attributes = json.loads(sidecar["Body"])["metadataAttributes"]
    assert attributes["section"] == "academic-advising"
    assert set(attributes) == {"source_url", "title", "section", "scrape_timestamp"}
    assert sidecar["ContentType"] == "application/json"


def test_the_sidecar_key_is_metadata_json_not_a_plain_json_document(monkeypatch):
    # Bedrock treats `*.metadata.json` as METADATA for the sibling document. A plain `<slug>.json`
    # (the local scraper's inspection filename) would be INGESTED as its own JSON document.
    s3, _ = _wire(monkeypatch, [_ok()])
    lf.handler({}, None)
    keys = {c.kwargs["Key"] for c in s3.put_object.call_args_list}
    assert "x-a-hash.md.metadata.json" in keys
    assert "x-a-hash.json" not in keys


def test_partial_failure_uploads_survivors_and_still_ingests(monkeypatch):
    s3, bedrock_agent = _wire(monkeypatch, [_ok(), _fail()])
    out = lf.handler({}, None)
    # Only the successful page (md + metadata = 2 puts); the failure is reported; ingestion runs.
    assert s3.put_object.call_count == 2
    assert out["uploaded"] == 1
    assert out["failed"] == [{"url": "https://x/b", "error": "HTTP 404"}]
    bedrock_agent.start_ingestion_job.assert_called_once()


def test_no_successes_skips_ingestion(monkeypatch):
    s3, bedrock_agent = _wire(monkeypatch, [_fail()])
    s3.get_paginator.return_value = _paginated([])
    out = lf.handler({}, None)
    s3.put_object.assert_not_called()
    bedrock_agent.start_ingestion_job.assert_not_called()
    assert out["uploaded"] == 0
    assert out["ingestionJobId"] is None


def test_passes_the_whole_crawl_list_and_the_config_to_scrape_pages(monkeypatch):
    # One daily run, no tiers: every invocation is the complete sweep, so the pages handed to
    # scrape_pages are the crawl list in full - including the sections, which ride into the sidecar.
    captured = {}
    for k, v in BASE_ENV.items():
        monkeypatch.setenv(k, v)

    def fake_scrape(pages, **kw):
        captured["pages"] = pages
        captured["kw"] = kw
        return []

    monkeypatch.setattr(lf, "seed_list_path", lambda *a, **kw: Path("/opt/url-list.csv"))
    monkeypatch.setattr(lf, "load_seed_pages", lambda path: list(SEED_PAGES))
    monkeypatch.setattr(lf, "scrape_pages", fake_scrape)
    monkeypatch.setattr(lf, "_s3_client", lambda: MagicMock())
    monkeypatch.setattr(lf, "_bedrock_agent_client", lambda: MagicMock())

    lf.handler({}, None)
    assert captured["pages"] == SEED_PAGES
    assert captured["kw"]["timeout"] == 15.0
    assert captured["kw"]["user_agent"] == "TestAgent/1.0"


def test_the_event_is_ignored(monkeypatch):
    # No tiers means nothing in the event is read. A scheduled invocation, a manual invoke and the
    # deploy trigger's payload must all do the identical complete sweep.
    s3_a, _ = _wire(monkeypatch, [_ok()])
    scheduled = lf.handler({"source": "aws.events"}, None)
    s3_b, _ = _wire(monkeypatch, [_ok()])
    triggered = lf.handler({"tier": "fast", "RequestType": "Create"}, None)

    assert scheduled["summary"]["pages_configured"] == triggered["summary"]["pages_configured"]
    assert scheduled["uploaded"] == triggered["uploaded"] == 1


def test_the_run_summary_reports_configured_pages_and_duration(monkeypatch):
    # pages_configured vs pages_fetched is how a silently shrinking crawl list shows up in the
    # logs; duration_seconds is the instrument for the 15-minute-timeout question (the corpus was
    # sized by scaling gav's measurements, and the top of that range is ~12 minutes).
    _wire(monkeypatch, [_ok(), _fail()])
    out = lf.handler({}, None)
    summary = out["summary"]
    assert summary["pages_configured"] == len(SEED_PAGES)
    assert summary["pages_fetched"] == 2
    assert summary["pages_changed"] == 1
    assert summary["pages_failed"] == 1
    assert isinstance(summary["duration_seconds"], float)
    # Serializable, because it is emitted as one structured log line per run.
    json.loads(json.dumps(summary, default=str))


# --- The crawl list arrives as a bundled asset, and a bad one is fatal --------------------------


def test_seed_list_path_prefers_the_layer_mount(monkeypatch, tmp_path):
    # /opt is where Lambda extracts layer content. Both candidate dirs are redirected into tmp so
    # the test does not depend on the machine's real /opt.
    opt, local = tmp_path / "opt", tmp_path / "local"
    opt.mkdir()
    local.mkdir()
    (opt / "url-list.csv").write_text("x", encoding="utf-8")
    (local / "url-list.csv").write_text("y", encoding="utf-8")
    monkeypatch.setattr(lf, "SEED_LIST_DIRS", (opt, local))

    assert lf.seed_list_path("url-list.csv") == opt / "url-list.csv"


def test_seed_list_path_falls_back_to_the_function_bundle(monkeypatch, tmp_path):
    opt, local = tmp_path / "opt", tmp_path / "local"
    opt.mkdir()
    local.mkdir()
    (local / "url-list.csv").write_text("y", encoding="utf-8")
    monkeypatch.setattr(lf, "SEED_LIST_DIRS", (opt, local))

    assert lf.seed_list_path("url-list.csv") == local / "url-list.csv"


def test_seed_list_path_reads_the_filename_from_the_environment(monkeypatch, tmp_path):
    # The env var carries a FILENAME, never the list itself: 203 pages is 19 KB as compact JSON
    # against Lambda's 4 KB aggregate environment cap, which cannot be raised.
    opt = tmp_path / "opt"
    opt.mkdir()
    (opt / "custom.csv").write_text("x", encoding="utf-8")
    monkeypatch.setattr(lf, "SEED_LIST_DIRS", (opt,))
    monkeypatch.setenv("URL_LIST_FILE", "custom.csv")

    assert lf.seed_list_path() == opt / "custom.csv"


def test_a_missing_bundled_list_raises_naming_where_it_looked(monkeypatch, tmp_path):
    monkeypatch.setattr(lf, "SEED_LIST_DIRS", (tmp_path,))
    with pytest.raises(SeedListError) as exc:
        lf.seed_list_path("url-list.csv")
    assert str(tmp_path) in str(exc.value)


def test_a_bad_crawl_list_fails_the_invocation_before_anything_is_deleted(monkeypatch):
    # THE guarantee. An unusable list must abort the run, not produce a zero-page sweep: zero pages
    # means an empty expected set, and the prune would then delete every document in the knowledge
    # base and start an ingestion job over the wreckage. A Lambda that raises has pruned nothing.
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])

    def boom(path):
        raise SeedListError("url-list.csv has a valid header but no pages")

    monkeypatch.setattr(lf, "load_seed_pages", boom)

    with pytest.raises(SeedListError):
        lf.handler({}, None)

    s3.delete_object.assert_not_called()
    s3.put_object.assert_not_called()
    bedrock_agent.start_ingestion_job.assert_not_called()


def test_a_missing_layer_asset_fails_the_invocation_too(monkeypatch):
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])

    def boom(*a, **kw):
        raise SeedListError("bundled crawl list 'url-list.csv' not found")

    monkeypatch.setattr(lf, "seed_list_path", boom)

    with pytest.raises(SeedListError):
        lf.handler({}, None)

    s3.delete_object.assert_not_called()
    bedrock_agent.start_ingestion_job.assert_not_called()


# --- Pruning: keyed off the crawl list, never off what a run fetched ---------------------------


def test_expected_kb_keys_covers_every_page_on_the_crawl_list():
    keys = lf.expected_kb_keys(["https://x/a", "https://x/b"])
    a, b = lf.slugify_url("https://x/a"), lf.slugify_url("https://x/b")
    assert keys == {
        f"{a}.md",
        f"{a}.md.metadata.json",
        f"{b}.md",
        f"{b}.md.metadata.json",
    }


def test_prune_deletes_only_what_the_crawl_list_no_longer_wants(monkeypatch):
    s3, _ = _wire(monkeypatch, [_ok()])
    s3.get_paginator.return_value = _paginated(
        ["keep.md", "keep.md.metadata.json", "stale.md", "stale.md.metadata.json"]
    )

    deleted = lf.prune_stale_objects(s3, "kb-bucket", {"keep.md", "keep.md.metadata.json"})

    assert sorted(deleted) == ["stale.md", "stale.md.metadata.json"]
    assert {c.kwargs["Key"] for c in s3.delete_object.call_args_list} == {
        "stale.md",
        "stale.md.metadata.json",
    }


def test_prune_never_raises_when_s3_refuses(monkeypatch):
    # Housekeeping must not break a scrape that already succeeded - e.g. if the role is missing
    # s3:ListBucket, the run should still upload and ingest.
    s3, _ = _wire(monkeypatch, [_ok()])
    s3.get_paginator.side_effect = RuntimeError("AccessDenied")

    assert lf.prune_stale_objects(s3, "kb-bucket", {"keep.md"}) == []


def test_a_failed_fetch_does_not_delete_that_page_from_the_knowledge_base(monkeypatch):
    # THE point of keying on the crawl list. Both pages are listed; /b 404s this run. Pruning by
    # what uploaded successfully would delete /b's document and silently drop its answers from the
    # bot until someone noticed. Its last-good document must survive a transient failure.
    s3, _ = _wire(monkeypatch, [_ok(), _fail()])
    b_slug = lf.slugify_url("https://x/b")
    s3.get_paginator.return_value = _paginated(
        ["x-a-hash.md", "x-a-hash.md.metadata.json", f"{b_slug}.md", f"{b_slug}.md.metadata.json"]
    )

    out = lf.handler({}, None)

    deleted = {c.kwargs["Key"] for c in s3.delete_object.call_args_list}
    assert deleted == set()  # nothing pruned, despite /b producing no upload this run
    assert out["pruned"] == []


def test_a_page_removed_from_the_crawl_list_is_pruned(monkeypatch):
    # The other half: de-listing a page has to actually retire its document, or removal is a silent
    # no-op and the KB answers forever from a page nobody maintains.
    s3, bedrock_agent = _wire(monkeypatch, [_ok()], pages=[SEED_PAGES[0]])
    b_slug = lf.slugify_url("https://x/b")
    s3.get_paginator.return_value = _paginated(
        ["x-a-hash.md", "x-a-hash.md.metadata.json", f"{b_slug}.md", f"{b_slug}.md.metadata.json"]
    )

    out = lf.handler({}, None)

    assert sorted(out["pruned"]) == [f"{b_slug}.md", f"{b_slug}.md.metadata.json"]
    # A prune-only change still has to re-ingest, or the deleted document keeps its vectors.
    bedrock_agent.start_ingestion_job.assert_called_once()


def test_ingestion_runs_for_a_prune_only_change(monkeypatch):
    s3, bedrock_agent = _wire(monkeypatch, [_fail()])
    s3.get_paginator.return_value = _paginated(["gone.md"])

    lf.handler({}, None)

    bedrock_agent.start_ingestion_job.assert_called_once()


# --- Gate 1: upload only what changed ---------------------------------------------------------


def test_content_fingerprint_ignores_the_scrape_timestamp():
    # Without this the gate is an expensive no-op: the timestamp moves on every run, so every page
    # would read as changed, re-upload, and re-trigger ingestion daily.
    md = "# Page\n\nbody"
    base = {
        "source_url": "https://x/a",
        "title": "Page",
        "section": "academic-advising",
        "scrape_timestamp": "2026-08-05T00:00:00Z",
    }
    later = dict(base, scrape_timestamp="2026-08-06T21:16:07Z")
    assert lf.content_fingerprint(md, base) == lf.content_fingerprint(md, later)


def test_content_fingerprint_moves_on_body_title_url_or_section_change():
    md = "# Page\n\nbody"
    meta = {"source_url": "https://x/a", "title": "Page", "section": "academic-advising"}
    baseline = lf.content_fingerprint(md, meta)
    assert lf.content_fingerprint(md + " more", meta) != baseline
    assert lf.content_fingerprint(md, dict(meta, title="Renamed")) != baseline
    assert lf.content_fingerprint(md, dict(meta, source_url="https://x/moved")) != baseline
    # section is in the fingerprint so RE-SECTIONING a page re-uploads it. The body is unchanged,
    # but the sidecar is not, and cards.py ranks and routes off that sidecar value.
    assert lf.content_fingerprint(md, dict(meta, section="financial-aid")) != baseline


def test_changed_page_is_uploaded_and_stamped_with_its_fingerprint(monkeypatch):
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])
    out = lf.handler({}, None)

    md_put = next(c.kwargs for c in s3.put_object.call_args_list if c.kwargs["Key"] == "x-a-hash.md")
    expected = lf.content_fingerprint(_ok().markdown, _ok().metadata)
    assert md_put["Metadata"] == {lf.CONTENT_HASH_METADATA_KEY: expected}
    assert out["uploaded"] == 1 and out["unchanged"] == 0
    bedrock_agent.start_ingestion_job.assert_called_once()


def test_unchanged_page_uploads_nothing_and_starts_no_ingestion(monkeypatch):
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])
    stored = lf.content_fingerprint(_ok().markdown, _ok().metadata)
    s3.head_object.return_value = {"Metadata": {lf.CONTENT_HASH_METADATA_KEY: stored}}

    out = lf.handler({}, None)

    s3.put_object.assert_not_called()
    bedrock_agent.start_ingestion_job.assert_not_called()
    assert out["uploaded"] == 0
    assert out["unchanged"] == 1
    assert out["summary"]["pages_changed"] == 0
    assert out["ingestion"].startswith("skipped")


def test_a_second_consecutive_run_over_unchanged_content_reports_zero_changes(monkeypatch):
    # The acceptance property, end to end: run once against an empty bucket, feed run one's own
    # stamped fingerprint back as what S3 now holds, and run again.
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])

    first = lf.handler({}, None)
    assert first["uploaded"] == 1

    md_put = next(c.kwargs for c in s3.put_object.call_args_list if c.kwargs["Key"] == "x-a-hash.md")
    s3.reset_mock()
    s3.head_object.return_value = {"Metadata": dict(md_put["Metadata"])}
    s3.get_paginator.return_value = _paginated(["x-a-hash.md", "x-a-hash.md.metadata.json"])
    bedrock_agent.reset_mock()
    bedrock_agent.list_ingestion_jobs.return_value = {"ingestionJobSummaries": []}

    second = lf.handler({}, None)

    assert second["summary"]["pages_changed"] == 0
    assert second["summary"]["pages_unchanged"] == 1
    assert second["pruned"] == []
    s3.put_object.assert_not_called()
    bedrock_agent.start_ingestion_job.assert_not_called()


def test_a_missing_fingerprint_is_treated_as_changed(monkeypatch):
    # Objects written before change gating existed carry no fingerprint; so does an object S3
    # refuses to HEAD. Both must re-upload (and thereby stamp themselves), never silently skip.
    s3, _ = _wire(monkeypatch, [_ok()])
    s3.head_object.side_effect = RuntimeError("AccessDenied")

    out = lf.handler({}, None)
    assert out["uploaded"] == 1


def test_an_unchanged_page_is_never_pruned_as_stale(monkeypatch):
    # The regression change gating introduces if you are not careful. The prune's belt-and-braces
    # union used to cover every live page for free, because every successful page was re-uploaded
    # on every run. Once unchanged pages stop uploading, a page can be live, correct, and absent
    # from that union - and a slug-derivation mismatch would then delete it. Pages found unchanged
    # must count as live.
    s3, _ = _wire(monkeypatch, [_ok()])
    stored = lf.content_fingerprint(_ok().markdown, _ok().metadata)
    s3.head_object.return_value = {"Metadata": {lf.CONTENT_HASH_METADATA_KEY: stored}}
    s3.get_paginator.return_value = _paginated(
        ["x-a-hash.md", "x-a-hash.md.metadata.json", "really-stale.md"]
    )

    out = lf.handler({}, None)

    assert out["unchanged"] == 1
    assert out["pruned"] == ["really-stale.md"]


# --- Gate 2 + concurrency: one ingestion job at a time, and never lose a change ----------------


def test_overlapping_run_defers_instead_of_failing(monkeypatch):
    # Bedrock allows one ingestion job per data source. An overlap - a manual invoke landing on the
    # schedule, or the deploy trigger firing mid-run - must skip cleanly.
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])
    bedrock_agent.list_ingestion_jobs.return_value = {
        "ingestionJobSummaries": [_job("running-job", "IN_PROGRESS", 100)]
    }

    out = lf.handler({}, None)

    bedrock_agent.start_ingestion_job.assert_not_called()
    assert out["ingestionJobId"] is None
    assert out["ingestion"] == "deferred (job running-job in progress)"
    # The upload still happened - only the indexing was deferred.
    assert out["uploaded"] == 1


def test_a_deferred_change_is_picked_up_by_the_next_run(monkeypatch):
    # THE reason deferring is safe. This run changes nothing, so "did I upload?" says no ingestion
    # is needed - but the bucket holds an object newer than the last job, which is exactly what a
    # previously deferred run leaves behind. Without this the deferred change would sit unindexed
    # forever, because no later run would ever see it as changed either.
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])
    stored = lf.content_fingerprint(_ok().markdown, _ok().metadata)
    s3.head_object.return_value = {"Metadata": {lf.CONTENT_HASH_METADATA_KEY: stored}}
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "x-a-hash.md", "LastModified": 200}]}
    ]
    bedrock_agent.list_ingestion_jobs.return_value = {
        "ingestionJobSummaries": [_job("older-job", "COMPLETE", 100)]
    }

    out = lf.handler({}, None)

    assert out["uploaded"] == 0
    bedrock_agent.start_ingestion_job.assert_called_once()
    assert "newer than the last ingestion job" in out["ingestion"]


def test_a_fully_indexed_unchanged_bucket_starts_nothing(monkeypatch):
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])
    stored = lf.content_fingerprint(_ok().markdown, _ok().metadata)
    s3.head_object.return_value = {"Metadata": {lf.CONTENT_HASH_METADATA_KEY: stored}}
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "x-a-hash.md", "LastModified": 100}]}
    ]
    bedrock_agent.list_ingestion_jobs.return_value = {
        "ingestionJobSummaries": [_job("recent-job", "COMPLETE", 200)]
    }

    out = lf.handler({}, None)
    bedrock_agent.start_ingestion_job.assert_not_called()
    assert out["ingestion"] == "skipped (nothing changed)"


def test_a_race_on_start_ingestion_defers_rather_than_raising(monkeypatch):
    # Another run can win between our list and our start, and StartIngestionJob is rate-limited to
    # one per ten seconds. Either way the scrape has already succeeded and must not fail.
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])

    class ConflictException(Exception):
        pass

    bedrock_agent.start_ingestion_job.side_effect = ConflictException("ongoing ingestion job")

    out = lf.handler({}, None)

    assert out["ingestionJobId"] is None
    assert out["ingestion"] == "deferred (ConflictException)"
    assert out["uploaded"] == 1  # the upload is still reported as done


def test_losing_job_history_falls_back_to_the_old_rule(monkeypatch):
    # If ListIngestionJobs is denied or throttled, we must still ingest what we just changed.
    s3, bedrock_agent = _wire(monkeypatch, [_ok()])
    bedrock_agent.list_ingestion_jobs.side_effect = RuntimeError("AccessDenied")

    out = lf.handler({}, None)

    bedrock_agent.start_ingestion_job.assert_called_once()
    assert out["ingestion"] == "started (content changed this run)"


def test_active_status_helpers():
    summaries = [_job("a", "COMPLETE", 1), _job("b", "STOPPING", 2), _job("c", "IN_PROGRESS", 3)]
    # STOPPING counts as occupied: the job has not released the data source yet.
    assert lf.active_ingestion_job(summaries) == "b"
    assert lf.active_ingestion_job([_job("a", "COMPLETE", 1)]) is None
    assert lf.last_ingestion_started_at(summaries) == 3
    assert lf.last_ingestion_started_at([]) is None

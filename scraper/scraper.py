"""Static-HTML scraper for the curated SJSU student-services page list.

Fetches the pages named in the curated crawl list over plain HTTP (every page on the list was
confirmed server-rendered HTML - no SPA, no browser automation), extracts main-content markdown
with trafilatura, and - via the CLI - writes a markdown file plus a JSON metadata sidecar per page
for manual inspection.

Split of concerns, so the Lambda wrapper needs none of the local-only surface:
  - `scrape_pages(pages)` does fetch + extract only and returns `ScrapeResult` objects. It
    performs NO file or AWS I/O, so lambda_function.py calls it and uploads each result's
    markdown + metadata to the KB S3 source bucket instead of writing local files.
  - `write_result()` and `main()` are the local-only concerns (filesystem + CLI + config).

Scope discipline: only the listed URLs are fetched. There is deliberately NO link-following /
recursive crawling - the corpus is a curated list of pages, and a crawler over sjsu.edu would
pull in the whole university.

THE CRAWL LIST IS THE CORPUS. `load_seed_pages` is the only reader of it, and it raises rather
than returning a short or empty list: a run that fetches nothing would hand
`lambda_function.prune_stale_objects` an empty expected set, and the prune would delete every
document in the knowledge base. So a missing, empty, or malformed list has to be loud.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import trafilatura

LOG = logging.getLogger("scraper")

SCRAPER_VERSION = "1"
DEFAULT_TIMEOUT = 20.0
DEFAULT_USER_AGENT = "SJSUNavigatorScraper/1.0 (+https://www.sjsu.edu/)"
DEFAULT_OUTPUT_DIR = "./scraper_output"

# The crawl list's required columns. The file carries two more (static_html, body_text_chars)
# that are page-selection evidence, not scraper input, and are ignored here.
#
# `section` is required, not optional: it rides into the metadata sidecar and app/cards.py uses
# it to deprioritize noisy sections and to pick a page's follow-up button. A page with no section
# still answers questions, so that failure would be invisible - hence a hard error.
SEED_COLUMNS = ("url", "section", "title")

# "ï¿½" (U+00EF U+00BF U+00BD) - the Latin-1 view of a UTF-8-encoded U+FFFD, which some CMSes
# bake into their own source as the entities &#239;&#191;&#189;. See _scrub_replacement_chars.
_REPLACEMENT_SEQ = "ï¿½"

# Readable slug: everything that is not a lowercase letter or digit becomes a hyphen.
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Keep the readable portion bounded so filenames stay sane; uniqueness is guaranteed by the hash.
_SLUG_MAX_READABLE = 80
_HASH_LEN = 8


class SeedListError(Exception):
    """The crawl list is missing, empty, or malformed. Fatal by design - see load_seed_pages."""


@dataclass
class ScrapeResult:
    """Outcome of scraping one page. `ok` gates whether markdown/metadata are populated.

    `section` is carried from the crawl list rather than derived from the URL: it is a curated
    grouping, and app/cards.py keys its ranking and follow-up presets off the exact value."""

    url: str
    slug: str
    section: str
    ok: bool
    title: Optional[str] = None
    markdown: Optional[str] = None
    metadata: Optional[dict] = None
    error: Optional[str] = None


# --- The crawl list ------------------------------------------------------------------------
#
# Every check below is duplicated in infra/config.py:resolve_seed_pages, which runs the same
# validation at SYNTH so a broken list fails `cdk synth` instead of deploying. The duplication is
# deliberate: this module ships in the Lambda bundle and infra/ does not, so they cannot share
# code - and the runtime check is the one that matters, because the list travels as a bundled
# asset and an asset can be stale, truncated, or absent regardless of what synth saw.


def load_seed_pages(path) -> List[Dict[str, str]]:
    """The crawl list as `[{"url", "section", "title"}]`, in file order.

    Raises SeedListError on a missing file, a missing required column, a blank cell, a non-http
    URL, a duplicate URL, or a header with no rows. Never returns an empty list.

    RAISING IS THE POINT. The alternative - log a warning and continue with zero pages - reaches
    prune_stale_objects with an empty expected set, deletes every document in the knowledge base,
    and then starts an ingestion job over the wreckage. A Lambda that errors out prunes nothing,
    so the corpus survives a bad deploy of this file.
    """
    path = Path(path)
    if not path.exists():
        raise SeedListError(
            f"crawl list not found at {path}. It is the scraper's only source of URLs, so a "
            "missing file means a run that fetches nothing - and prunes everything."
        )

    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            columns = reader.fieldnames or []
            missing = [c for c in SEED_COLUMNS if c not in columns]
            if missing:
                raise SeedListError(
                    f"{path.name} is missing required column(s): {', '.join(missing)}. "
                    f"Required: {', '.join(SEED_COLUMNS)}."
                )
            rows = list(reader)
    except SeedListError:
        raise
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise SeedListError(f"{path.name} could not be read as CSV: {exc}") from exc

    pages: List[Dict[str, str]] = []
    seen: set = set()
    for line_number, row in enumerate(rows, start=2):  # start=2: line 1 is the header
        page = {}
        for column in SEED_COLUMNS:
            value = (row.get(column) or "").strip()
            if not value:
                raise SeedListError(
                    f"{path.name} line {line_number}: `{column}` is empty. Every column in "
                    f"{', '.join(SEED_COLUMNS)} is required for every page."
                )
            page[column] = value
        if not page["url"].startswith(("http://", "https://")):
            raise SeedListError(
                f"{path.name} line {line_number}: {page['url']!r} is not an http(s) URL."
            )
        if page["url"] in seen:
            raise SeedListError(
                f"{path.name} line {line_number}: {page['url']} is listed more than once."
            )
        seen.add(page["url"])
        pages.append(page)

    if not pages:
        raise SeedListError(
            f"{path.name} has a valid header but no pages. An empty crawl list means the "
            "scraper fetches nothing and its prune deletes the whole knowledge base."
        )
    return pages


def seed_urls(pages) -> List[str]:
    """Just the URLs, in list order. The corpus as CONFIGURATION defines it, which is what the
    stale-object prune must key off - never the subset a run happened to fetch successfully."""
    return [page["url"] for page in pages]


def slugify_url(url: str) -> str:
    """Map a URL to a deterministic, filesystem-safe filename stem.

    Readable part is `<host><path>` with non-alphanumerics collapsed to hyphens; a short sha256
    prefix of the FULL url (including any query string) is appended so distinct URLs that would
    otherwise slugify identically never collide. Same URL in -> same slug out, always.

    Load-bearing beyond filenames: the prune derives expected object keys from the crawl list
    through this function while the uploader derives them from the fetched result, so any
    instability here would let a run delete the documents it just wrote.
    """
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.netloc}{parts.path}".lower()
    readable = _SLUG_RE.sub("-", base).strip("-")[:_SLUG_MAX_READABLE].strip("-")
    if not readable:
        readable = "index"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:_HASH_LEN]
    return f"{readable}-{digest}"


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 'Z' string (second precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_metadata(
    url: str,
    fetched_url: str,
    title: Optional[str],
    section: str,
    markdown: str,
    *,
    timestamp: Optional[str] = None,
) -> dict:
    """Metadata sidecar for one page. `timestamp` is injectable so tests stay deterministic.

    `source_url` is the requested URL (used for KB source attribution); `fetched_url` is the
    final URL after any redirects. `section` is the curated grouping from the crawl list, and it
    has to survive all the way into the sidecar: retrieval reads it back out and app/cards.py
    uses it to rank chunks and to choose a card's follow-up action.
    """
    return {
        "source_url": url,
        "fetched_url": fetched_url,
        "title": title,
        "section": section,
        "scrape_timestamp": timestamp or _now_iso(),
        "content_chars": len(markdown),
        "scraper_version": SCRAPER_VERSION,
    }


def _scrub_replacement_chars(text: Optional[str]) -> Optional[str]:
    """Remove U+FFFD replacement-char garbage baked into a page's own source.

    Not a defect in our fetch: it is what an upstream cp1252->UTF-8 lossy mis-decode at
    authoring/CMS time leaves behind, stored as the HTML entities `&#239;&#191;&#189;`, which
    decode to U+00EF U+00BF U+00BD ("ï¿½") - the Latin-1 view of a UTF-8-encoded U+FFFD. The
    original characters (a curly apostrophe, an accented name) were replaced by U+FFFD at the
    source and are UNRECOVERABLE, since U+FFFD carries no information about what it replaced.
    All we can do is strip the garbage so it does not pollute the knowledge base. Both the
    3-char sequence and any bare U+FFFD go; neither occurs in legitimate English content.
    """
    if not text:
        return text
    return text.replace(_REPLACEMENT_SEQ, "").replace("�", "")


def _flatten_markdown_tables(markdown: Optional[str]) -> Optional[str]:
    """Turn markdown-table rows into plain prose lines. We do NOT want tables in the knowledge
    base - university pages use tables for LAYOUT as well as for data, so trafilatura emits
    mangled "| cell | |" rows with empty cells. This flattens that markup to flat text.

    Deliberately DUMB and robust (string ops on the OUTPUT markdown, never HTML table parsing, no
    column/header semantics): a line is treated as a table row only if it starts with "|". Its
    cells are split on "|"; empty cells and pure table-drawing cells (---, :, spaces - i.e. the
    |---|---| separator row) are dropped; each remaining cell becomes its own prose line.
    Non-table lines (including headings) pass through untouched.

    (include_tables stays True in extract_markdown on purpose: setting it False makes trafilatura
    drop heading markup and jam adjacent cells together with no separator - worse than this.)
    """
    if not markdown:
        return markdown
    out = []
    for line in markdown.split("\n"):
        if not line.strip().startswith("|"):
            out.append(line)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # Keep content cells only: non-empty and not composed solely of table-drawing chars.
        out.extend(c for c in cells if c and (set(c) - set("-: ")))
    return "\n".join(out)


def _extract_title(html: str) -> Optional[str]:
    """Best-effort page title via trafilatura metadata; None if unavailable."""
    try:
        meta = trafilatura.extract_metadata(html)
    except Exception:  # trafilatura metadata extraction is best-effort; never fatal
        return None
    title = getattr(meta, "title", None) if meta is not None else None
    return title or None


def extract_markdown(html: str, url: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Extract (title, main-content markdown) from a page's HTML.

    Uses trafilatura, which strips nav/header/footer/sidebar boilerplate. Tables are kept (office
    hours and eligibility criteria are often tabular); comments are dropped. Returns (title, None)
    when no main content is found (e.g. a redirect stub or an empty page).

    Both the title and the markdown are scrubbed of replacement-char garbage baked into the page's
    own source (see `_scrub_replacement_chars`), and layout tables are flattened to prose (see
    `_flatten_markdown_tables`) because the KB wants flat text.
    """
    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_comments=False,
    )
    markdown = _flatten_markdown_tables(_scrub_replacement_chars(markdown))
    if markdown is not None:
        markdown = markdown.strip() or None
    return _scrub_replacement_chars(_extract_title(html)), markdown


def scrape_page(page: Dict[str, str], client: httpx.Client) -> ScrapeResult:
    """Fetch + extract one crawl-list page. Never raises: failures become ok=False results."""
    url = page["url"]
    section = page["section"]
    slug = slugify_url(url)
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        LOG.warning("fetch failed (HTTP %s): %s", code, url)
        return ScrapeResult(url=url, slug=slug, section=section, ok=False, error=f"HTTP {code}")
    except httpx.RequestError as exc:
        LOG.warning("fetch error (%s): %s", exc.__class__.__name__, url)
        return ScrapeResult(
            url=url,
            slug=slug,
            section=section,
            ok=False,
            error=f"{exc.__class__.__name__}: {exc}",
        )

    # response.text honors the page's declared charset; extract_markdown scrubs any
    # replacement-char garbage baked into the source content.
    title, markdown = extract_markdown(response.text, url=url)
    if not markdown:
        LOG.warning("no main content extracted: %s", url)
        return ScrapeResult(
            url=url,
            slug=slug,
            section=section,
            ok=False,
            title=title,
            error="no content extracted",
        )

    # The extracted title wins, with the crawl list's curated title as the fallback. Extraction
    # tracks the live page (a renamed office shows up on the next run); the list is the safety
    # net, so a page whose <title> trafilatura cannot read still cites as something a student
    # recognizes instead of as a null.
    title = title or page.get("title") or None
    metadata = build_metadata(url, str(response.url), title, section, markdown)
    return ScrapeResult(
        url=url,
        slug=slug,
        section=section,
        ok=True,
        title=title,
        markdown=markdown,
        metadata=metadata,
    )


def scrape_pages(
    pages: List[Dict[str, str]],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_USER_AGENT,
    client: Optional[httpx.Client] = None,
) -> List[ScrapeResult]:
    """Scrape each page in order, continuing past failures. One result per input page.

    Importable core with no filesystem/AWS coupling - the Lambda wrapper calls this directly.
    Pass `client` to inject a preconfigured/mock httpx.Client (used in tests); otherwise one is
    created with the given timeout + User-Agent and closed on exit. Redirects are followed.
    """
    owns_client = client is None
    if owns_client:
        client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
    try:
        return [scrape_page(page, client) for page in pages]
    finally:
        if owns_client:
            client.close()


def write_result(result: ScrapeResult, output_dir) -> Optional[Path]:
    """Write `<slug>.md` + `<slug>.json` for a successful result. Returns the markdown path.

    No-op (returns None) for failed results. Local-only; the Lambda path uploads to S3 instead.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not result.ok:
        return None
    md_path = output_dir / f"{result.slug}.md"
    json_path = output_dir / f"{result.slug}.json"
    md_path.write_text(result.markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(result.metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return md_path


def load_scraper_config(config_path=None) -> dict:
    """Read the `scraper:` block from the repo-root config.yaml (single source of truth).

    CLI-only. The Lambda gets these values as env vars set by the stack, because config.yaml is
    not in the function's asset bundle.
    """
    import yaml

    if config_path is None:
        config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg.get("scraper", {}) or {}


def main(argv=None) -> int:
    """CLI: scrape the crawl list (or a `--section` slice of it) to `output_dir`.

    Exit codes: 0 all pages succeeded; 1 the run completed but some pages failed; 2 the crawl
    list could not be loaded - the same fatal condition the Lambda treats it as.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Scrape the curated SJSU page list to clean markdown + metadata sidecars."
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml.")
    parser.add_argument(
        "--url-list", type=Path, default=None, help="Override scraper.url_list_file."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path(DEFAULT_OUTPUT_DIR), help="Where to write."
    )
    parser.add_argument("--section", default=None, help="Scrape only this crawl-list section.")
    parser.add_argument(
        "--limit", type=int, default=None, help="Scrape at most N pages (spot checks)."
    )
    args = parser.parse_args(argv)

    cfg = load_scraper_config(args.config)
    list_path = args.url_list or (
        Path(__file__).resolve().parents[1] / cfg.get("url_list_file", "url-list.csv")
    )
    timeout = float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT))
    user_agent = cfg.get("user_agent", DEFAULT_USER_AGENT)

    try:
        pages = load_seed_pages(list_path)
    except SeedListError as exc:
        LOG.error("%s", exc)
        return 2

    if args.section:
        pages = [p for p in pages if p["section"] == args.section]
        if not pages:
            LOG.error("no pages in section %r", args.section)
            return 2
    if args.limit is not None:
        pages = pages[: args.limit]

    LOG.info("scraping %d page(s) -> %s", len(pages), args.output_dir)
    results = scrape_pages(pages, timeout=timeout, user_agent=user_agent)

    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    for result in ok:
        path = write_result(result, args.output_dir)
        LOG.info("wrote %s (%d chars) <- %s", path, result.metadata["content_chars"], result.url)
    for result in failed:
        LOG.warning("FAILED %s: %s", result.url, result.error)

    LOG.info("done: %d ok, %d failed", len(ok), len(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

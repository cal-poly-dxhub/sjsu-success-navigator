"""Static-source scraper for the curated SJSU student-services page list.

One extraction entry point over HTML and PDF, no link-following, and a crawl list that
raises rather than returning a short one; see docs/scraper.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
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
import pypdf
import trafilatura
from lxml import html as lxml_html

LOG = logging.getLogger("scraper")

SCRAPER_VERSION = "2"
DEFAULT_TIMEOUT = 20.0
DEFAULT_USER_AGENT = "SJSUNavigatorScraper/1.0 (+https://www.sjsu.edu/)"
DEFAULT_OUTPUT_DIR = "./scraper_output"

# The crawl list's required columns. The file carries two more (static_html,
# body_text_chars) that are page-selection evidence rather than scraper input.
SEED_COLUMNS = ("url", "section", "title")

# The Latin-1 view of a UTF-8-encoded U+FFFD; see _scrub_replacement_chars.
_REPLACEMENT_SEQ = "ï¿½"

# Readable slug: everything that is not a lowercase letter or digit becomes a hyphen.
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Bounded for readability; uniqueness comes from the hash.
_SLUG_MAX_READABLE = 80
_HASH_LEN = 8


class SeedListError(Exception):
    """The crawl list is missing, empty, or malformed. Fatal by design - see load_seed_pages."""


class ExtractionError(Exception):
    """A fetched document could not be turned into usable text. Raised only by the PDF path."""


@dataclass
class ScrapeResult:
    """Outcome of scraping one page. `ok` gates whether markdown and metadata are populated."""

    url: str
    slug: str
    section: str
    ok: bool
    title: Optional[str] = None
    markdown: Optional[str] = None
    metadata: Optional[dict] = None
    error: Optional[str] = None


# --- The crawl list. Every check is duplicated at synth in infra/infra/config.py. ----------


def load_seed_pages(path) -> List[Dict[str, str]]:
    """The crawl list as `[{"url", "section", "title"}]`, in file order. Never empty."""
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
    """Just the URLs, in list order. What the prune keys off, never a run's successes."""
    return [page["url"] for page in pages]


def slugify_url(url: str) -> str:
    """Map a URL to a deterministic, filesystem-safe filename stem."""
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
    """Metadata sidecar for one page. `timestamp` is injectable so tests stay deterministic."""
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
    """Remove U+FFFD replacement-char garbage baked into a page's own source."""
    if not text:
        return text
    return text.replace(_REPLACEMENT_SEQ, "").replace("�", "")


def _flatten_markdown_tables(markdown: Optional[str]) -> Optional[str]:
    """Turn markdown-table rows into plain prose lines, because the KB wants flat text."""
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


# --- Template-aware supplement pass; see docs/scraper.md, The HTML supplement pass. --------

_CONTACT_BAND_CLASS = "o-region--contact"

# Block-level tags whose text stands alone as one line. Collected whole, never revisited.
_BLOCK_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "dt", "dd",
    "blockquote", "figcaption", "caption", "address", "summary", "pre",
}
# Never collected and never descended into: machinery, form controls, and real chrome.
_SKIP_TAGS = {
    "script", "style", "noscript", "template", "iframe", "svg", "form", "button",
    "select", "option", "label", "nav", "header", "footer", "aside",
}
# ARIA equivalents, because the LibGuides template marks its navbar role="banner" on a div.
_SKIP_ROLES = {"banner", "navigation", "contentinfo", "search", "complementary"}

# Below this many characters a block is a widget label, not content.
_MIN_BLOCK_CHARS = 3

_WS_RE = re.compile(r"\s+")
_DEDUP_RE = re.compile(r"[^a-z0-9]+")


def _parse_tree(html: str):
    """lxml tree for the supplement pass, or None. Best effort: a page it cannot parse still
    gets its trafilatura extraction. <br> tails gain a space, or facts glue together."""
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return None
    for br in tree.iter("br"):
        br.tail = " " + (br.tail or "")
    return tree


def _block_text(el) -> str:
    """One element's visible text as a single scrubbed line."""
    return _scrub_replacement_chars(_WS_RE.sub(" ", el.text_content()).strip())


def _dedup_key(text: str) -> str:
    """Letters and digits only, lowercased - the identity of a block for dedup purposes."""
    return _DEDUP_RE.sub("", text.lower())


def _is_leaf_container(el) -> bool:
    """A div/section/article with no block or container descendants: one link-grid tile."""
    if el.tag not in ("div", "section", "article"):
        return False
    return not any(
        isinstance(d.tag, str)
        and (d.tag in _BLOCK_TAGS or d.tag in ("div", "section", "article", "ul", "ol", "table"))
        for d in el.iterdescendants()
    )


def _collect_blocks(el, blocks: List[str]) -> None:
    """Walk one subtree in document order, appending each block-level element's text once."""
    tag = el.tag if isinstance(el.tag, str) else None
    if tag is None or tag in _SKIP_TAGS:
        return
    if (el.get("role") or "").strip().lower() in _SKIP_ROLES:
        return
    if tag in _BLOCK_TAGS or _is_leaf_container(el):
        text = _block_text(el)
        if len(text) >= _MIN_BLOCK_CHARS:
            blocks.append(text)
        return  # collected whole - do not revisit descendants
    for child in el:
        _collect_blocks(child, blocks)


def _content_region(tree):
    """The page's content container across the corpus's templates, with <body> as fallback."""
    for xpath in ("//main", '//*[@role="main"]', '//*[@id="main-content"]'):
        found = tree.xpath(xpath)
        if found:
            return found[0]
    body = tree.find("body")
    return body if body is not None else tree


def _contact_band_blocks(tree) -> List[str]:
    """Text blocks of the www.sjsu.edu contact band(s), outermost containers only. Descends
    from the band's children, because its root carries a role _collect_blocks skips."""
    nodes = tree.xpath(f'//*[contains(@class, "{_CONTACT_BAND_CLASS}")]')
    node_set = set(nodes)
    blocks: List[str] = []
    for node in nodes:
        if any(ancestor in node_set for ancestor in node.iterancestors()):
            continue  # an inner __item/__detail element; its outermost band collects it
        for child in node:
            _collect_blocks(child, blocks)
    return blocks


def _merge_new_blocks(seen: str, blocks: List[str]) -> tuple[List[str], str]:
    """The blocks whose normalised text is not already in `seen`. Returns (kept, seen)."""
    added: List[str] = []
    for block in blocks:
        key = _dedup_key(block)
        if not key or key in seen:
            continue
        added.append(block)
        seen += key
    return added, seen


# --- PDF extraction; see docs/scraper.md, PDF extraction. ----------------------------------

# Page numbers are folded away, or no running footer carrying one is ever seen twice.
_PDF_DIGITS_RE = re.compile(r"\d+")
# Furniture is short by nature; the cap stops a repeated real sentence being mistaken for it.
_PDF_FURNITURE_MAX_CHARS = 120
# A line must repeat on this share of pages AND on two pages absolutely.
_PDF_FURNITURE_MIN_SHARE = 0.6
_PDF_FURNITURE_MIN_PAGES = 2
# The floor for "this document is actually text".
_PDF_MIN_LETTERS = 200
# ...and it has to be prose, not symbol soup.
_PDF_MIN_LETTER_RATIO = 0.5


def _pdf_page_lines(data: bytes) -> List[List[str]]:
    """Each page's text as a list of whitespace-normalised, non-empty lines."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        raw_pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - encrypted, truncated, or malformed: all the same here
        raise ExtractionError(f"PDF could not be parsed ({type(exc).__name__}: {exc})") from exc

    pages: List[List[str]] = []
    for raw in raw_pages:
        lines = []
        for line in raw.splitlines():
            line = _WS_RE.sub(" ", line).strip()
            if line:
                lines.append(line)
        pages.append(lines)
    return pages


def _furniture_keys(pages: List[List[str]]) -> set:
    """The digit-normalised lines that repeat across pages often enough to be running furniture."""
    if len(pages) < _PDF_FURNITURE_MIN_PAGES:
        return set()
    page_counts: Dict[str, int] = {}
    for lines in pages:
        for key in {
            _PDF_DIGITS_RE.sub("#", line)
            for line in lines
            if len(line) <= _PDF_FURNITURE_MAX_CHARS
        }:
            page_counts[key] = page_counts.get(key, 0) + 1
    threshold = max(_PDF_FURNITURE_MIN_PAGES, len(pages) * _PDF_FURNITURE_MIN_SHARE)
    return {key for key, count in page_counts.items() if count >= threshold}


def _has_usable_text(text: str) -> bool:
    """Whether extracted text is prose a knowledge base can use, rather than scan residue."""
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < _PDF_MIN_LETTERS:
        return False
    non_space = sum(1 for ch in text if not ch.isspace())
    return bool(non_space) and letters / non_space >= _PDF_MIN_LETTER_RATIO


def extract_pdf(data: bytes, url: Optional[str] = None) -> tuple[Optional[str], str]:
    """Extract (title, text) from a PDF. Raises ExtractionError if it holds no usable text."""
    pages = _pdf_page_lines(data)
    furniture = _furniture_keys(pages)
    kept = [
        line
        for lines in pages
        for line in lines
        if _PDF_DIGITS_RE.sub("#", line) not in furniture
    ]
    text = _scrub_replacement_chars("\n".join(kept).strip()) or ""

    if not _has_usable_text(text):
        raise ExtractionError(
            f"PDF holds no usable text ({sum(ch.isalpha() for ch in text)} letters across "
            f"{len(pages)} page(s)) - an image-only scan or a slide deck of pictures. It needs "
            "OCR, which this scraper deliberately does not do; drop it from the crawl list."
        )
    if url:
        LOG.info(
            "pdf extracted: %d page(s), %d furniture line(s) stripped: %s",
            len(pages),
            len(furniture),
            url,
        )
    return None, text


def _is_pdf(content_type: str, body: bytes) -> bool:
    """PDF by magic bytes first, declared content type second: the bytes are the document."""
    head = body[:1024].lstrip()
    if head.startswith(b"%PDF-"):
        return True
    if head[:1] == b"<":  # an HTML or XML document, whatever the header says it is
        return False
    return "application/pdf" in content_type.lower()


def extract_document(
    body: bytes,
    text: str,
    content_type: str,
    url: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """THE extraction entry point: (title, content) for one document, whatever its format."""
    if _is_pdf(content_type, body):
        return extract_pdf(body, url=url)
    return extract_markdown(text, url=url)


def extract_markdown(html: str, url: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Extract (title, content markdown) from a page's HTML."""
    body = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_comments=False,
    )
    body = (_flatten_markdown_tables(_scrub_replacement_chars(body)) or "").strip()

    recovered: List[str] = []
    band: List[str] = []
    tree = _parse_tree(html)
    if tree is not None:
        region_blocks: List[str] = []
        region = _content_region(tree)
        if region is not None:
            _collect_blocks(region, region_blocks)
        seen = _dedup_key(body)
        recovered, seen = _merge_new_blocks(seen, region_blocks)
        band, seen = _merge_new_blocks(seen, _contact_band_blocks(tree))

    if url and (recovered or band):
        LOG.info(
            "supplement pass recovered %d block(s)%s: %s",
            len(recovered) + len(band),
            " incl. contact band" if band else "",
            url,
        )

    sections = [part for part in ("\n".join(band), body, "\n".join(recovered)) if part]
    markdown = "\n\n".join(sections).strip() or None
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

    # One extraction path for both formats. response.text honours the declared charset;
    # the PDF branch reads response.content, which must stay bytes.
    try:
        title, markdown = extract_document(
            response.content,
            response.text,
            response.headers.get("content-type", ""),
            url=url,
        )
    except ExtractionError as exc:
        # ERROR, not WARNING: this does not resolve itself on the next run. It is a
        # curation bug and should read as one.
        LOG.error("extraction failed: %s (%s)", url, exc)
        return ScrapeResult(url=url, slug=slug, section=section, ok=False, error=str(exc))

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

    # The extracted title wins, with the crawl list's curated title as the fallback.
    title = title or page.get("title") or None
    # The page introduces itself: Bedrock embeds only chunk text, never the sidecar.
    if title:
        markdown = f"# {title}\n\n{markdown}"
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
    """Scrape each page in order, continuing past failures. One result per input page."""
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
    """Write `<slug>.md` and `<slug>.json` for a successful result. Local-only, no-op on
    a failure. Returns the markdown path."""
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
    """Read the `scraper:` block from the repo-root config.yaml. CLI-only: the Lambda gets
    these values as environment variables from the stack."""
    import yaml

    if config_path is None:
        config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg.get("scraper", {}) or {}


def main(argv=None) -> int:
    """CLI: scrape the crawl list, or a `--section` slice of it, to `output_dir`."""
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
    # Resolved inside repo-root data/, the directory Lambda extracts to /opt, so a local run
    # and a deployed run read the same file.
    list_path = args.url_list or (
        Path(__file__).resolve().parents[1] / "data" / cfg.get("url_list_file", "urls.csv")
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

"""Static-source scraper for the curated SJSU student-services page list.

Fetches the documents named in the curated crawl list over plain HTTP (no SPA, no browser
automation), extracts text, and - via the CLI - writes a markdown file plus a JSON metadata
sidecar per document for manual inspection.

TWO SOURCE FORMATS, ONE PATH. `extract_document` is the only extraction entry point and it
dispatches on what the server actually sent: server-rendered HTML through trafilatura plus a
template-aware supplement pass (extract_markdown), and PDF through pypdf plus a repeated-furniture
strip (extract_pdf). Callers - scrape_page, and through it the Lambda - never branch on format.
PDF is not an afterthought on this corpus: SJSU publishes most of its academic-coaching material
(how to email a professor, how to use office hours) only as Writing Center and Peer Connections
handout PDFs, so an HTML-only extractor cannot answer the questions the sponsors asked for.

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

# The crawl list's required columns. The file carries two more (static_html, body_text_chars)
# that are page-selection evidence, not scraper input, and are ignored here - including for the
# PDF rows, which record "pdf" and the extracted-text length rather than anything about HTML.
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


class ExtractionError(Exception):
    """A fetched document could not be turned into usable text.

    Raised only by the PDF path, and deliberately: a PDF is opaque in a way HTML is not. An
    HTML page that extracts to nothing is visibly empty in the browser too, but a PDF that
    extracts to nothing looks perfectly readable to the human who added it to the crawl list -
    it is a scan, or text drawn as vector art. Silently ingesting that empty (or near-empty)
    document would put a titled, cited, contentless page in the knowledge base, which retrieval
    can rank and the model can cite. So the PDF path raises, scrape_page turns it into
    `ok=False` with the reason, and the page is counted in the run summary's failures. The
    corpus keeps the last-good version of that URL and a human sees the error in the log.
    """


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


# --- Template-aware supplement pass ---------------------------------------------------------
#
# trafilatura models a page as an article: prose in the middle, boilerplate around it. Two layouts
# in this corpus break that model, measured against the live KB (2026-08-10 audit: 39 of 203
# ingested docs carried any phone number; EOP's and AEC's contact info appeared in none):
#
#   1. The www.sjsu.edu CMS stamps each office's phone/email/hours into a styled band OUTSIDE
#      <main> (class `o-region--contact`, role="complementary"). Template chrome to an article
#      extractor; per-office data to us - for a routing assistant, the most valuable text on the
#      page. Even the offices' dedicated contact-us pages keep their facts in this band.
#   2. Landing pages carry their content as link-tile grids (heading + link + one-line
#      description, repeated). Link-dense and paragraph-poor, so the boilerplate heuristics prune
#      it - and trafilatura's own favor_recall/include_links options measurably do not bring it
#      back (bursar index: 341 of 1,491 in-main chars survive, identical with favor_recall).
#
# So trafilatura stays - it is right about real chrome and good at prose - and a second lxml pass
# recovers what its model wrongly drops: the contact band, plus any content-region text block
# missing from its output. Dedup is on letters-and-digits-only normalization, so markdown escaping
# and whitespace reflow cannot make the same sentence look new.

_CONTACT_BAND_CLASS = "o-region--contact"

# Block-level tags whose text stands alone as one supplement line. Collected whole; descendants
# are not revisited, so an <a> inside a collected <li> is never double-counted.
_BLOCK_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "dt", "dd",
    "blockquote", "figcaption", "caption", "address", "summary", "pre",
}
# Never collected and never descended into: invisible machinery, form controls, and the
# nav/header/footer chrome trafilatura is already right to drop.
_SKIP_TAGS = {
    "script", "style", "noscript", "template", "iframe", "svg", "form", "button",
    "select", "option", "label", "nav", "header", "footer", "aside",
}
# ARIA equivalents of the chrome tags - the library's LibGuides template marks its navbar with
# role="banner" on a plain <div>, so tag names alone do not exclude it.
_SKIP_ROLES = {"banner", "navigation", "contentinfo", "search", "complementary"}

# Below this many characters a block is a bare widget label ("Go", "FAQ" links), not content.
_MIN_BLOCK_CHARS = 3

_WS_RE = re.compile(r"\s+")
_DEDUP_RE = re.compile(r"[^a-z0-9]+")


def _parse_tree(html: str):
    """lxml tree for the supplement pass, or None. Best-effort by design: a page lxml cannot
    parse still gets its trafilatura extraction, exactly as before the supplement pass existed.

    <br> tails get a leading space because text_content() otherwise glues the surrounding lines
    together - the contact band separates "Phone: 408-924-1601" from "Monday - Friday" with
    nothing but <br>, and "408-924-1601Monday" in the KB is a corrupted fact, not a phone number.
    """
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
    """A div/section/article with no block-level or container descendants: one tile of a link
    grid, whose heading-less label + description text would otherwise be skipped entirely."""
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
    """The page's content container across the corpus's templates: <main> on www.sjsu.edu and
    careercenter (WordPress), role="main" on library.sjsu.edu (LibGuides), with the whole <body>
    as the fallback - _collect_blocks skips chrome by tag and role, so the fallback stays sane."""
    for xpath in ("//main", '//*[@role="main"]', '//*[@id="main-content"]'):
        found = tree.xpath(xpath)
        if found:
            return found[0]
    body = tree.find("body")
    return body if body is not None else tree


def _contact_band_blocks(tree) -> List[str]:
    """Text blocks of the www.sjsu.edu contact band(s), outermost containers only. The band root
    carries role="complementary", which _collect_blocks rightly skips everywhere else - so this
    pass descends from the band's children instead of its root. Empty on other templates."""
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
    """The blocks whose normalized text is not already a substring of `seen` (the normalized
    concatenation of everything kept so far). Returns (kept blocks, updated accumulator)."""
    added: List[str] = []
    for block in blocks:
        key = _dedup_key(block)
        if not key or key in seen:
            continue
        added.append(block)
        seen += key
    return added, seen


# --- PDF extraction ---------------------------------------------------------------------------
#
# pypdf, because it is pure Python (no poppler, no system binary, no OCR) and it ships a
# py3-none-any wheel, which is what the manylinux layer bundler in infra/ needs - it runs pip with
# --only-binary=:all:, so a dep that has to compile would fail the build. pdfminer.six was the
# other pure-Python candidate and it was measured against this corpus, not assumed: on the Writing
# Center's "Email Etiquette" handout it emits one character per line down the page ("S\na\nn\n
# J\no\ns\ne") while pypdf returns clean prose. Same file, same call. That decided it.
#
# Two things then stand between pypdf's output and a usable document:
#
#   1. REPEATED FURNITURE. Every page of a handout carries the same running header or footer -
#      "Email Etiquette for Students, Fall 2013. Rev. Summer 2014  2 of 3". Text is chunked at
#      FIXED_SIZE for retrieval, with no idea where pages ended, so that line lands in the middle
#      of chunks as noise, several times per document. _furniture_keys finds the lines that repeat
#      across pages and drops them. Page numbers are normalised away first, or "1 of 3" and "2 of
#      3" would look like different lines and neither would ever be caught.
#   2. DOCUMENTS THAT ARE NOT TEXT. See ExtractionError.

# A page number differs on every page; without folding digits away, no running footer that carries
# one is ever seen twice.
_PDF_DIGITS_RE = re.compile(r"\d+")
# Furniture is short by nature - a header, a footer, a page number. The cap is what stops a
# repeated-by-coincidence sentence of real content from being mistaken for it.
_PDF_FURNITURE_MAX_CHARS = 120
# A line has to repeat on at least this SHARE of the pages to count as furniture, and on at least
# two pages in absolute terms. Both, so a two-page handout still gets its running header stripped
# while a line appearing on 2 of 16 slides does not.
_PDF_FURNITURE_MIN_SHARE = 0.6
_PDF_FURNITURE_MIN_PAGES = 2
# The floor for "this document is actually text". A real handout runs to thousands of letters; the
# fragments a scan or an image-only slide deck yields (a stray caption, a page number) run to tens.
_PDF_MIN_LETTERS = 200
# ...and it has to be prose, not symbol soup: a vector-art or badly-encoded PDF can emit plenty of
# characters that are almost all punctuation and stray glyphs.
_PDF_MIN_LETTER_RATIO = 0.5


def _pdf_page_lines(data: bytes) -> List[List[str]]:
    """Each page's text as a list of whitespace-normalised, non-empty lines.

    Structure is kept per page rather than flattened because the furniture pass needs to know
    which page a line came from - a line repeated ACROSS pages is furniture, the same line twice
    on one page is just the document.
    """
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
    """Extract (title, text) from a PDF. Raises ExtractionError if it holds no usable text.

    THE TITLE IS ALWAYS None, so scrape_page falls back to the crawl list's curated title. That
    is a decision about what a student sees, not an omission: the title is rendered as the source
    attribution on an answer card, and this corpus's PDF metadata titles do not survive that bar.
    Measured across the handouts on the list, seven of nine carry an empty /Title, one carries the
    unedited template placeholder "Title of Handout", and the useful remainder is a single file.
    The common failure mode elsewhere is worse still - "Microsoft Word - handout_v2final.docx" -
    and a filename on a card is exactly what the crawl list's curated title exists to prevent.
    """
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
    """PDF by magic bytes first, declared content type second.

    The bytes lead because they are the document; a Content-Type header is only a claim about it,
    and this corpus has seen both claims go wrong. A handout served as `application/octet-stream`
    still has to be read as a PDF, so magic bytes alone are enough to say yes. A soft-404 HTML
    page served as `application/pdf` must NOT be, so an opening angle bracket is enough to say no
    before the header is consulted at all - otherwise that page reaches pypdf, which raises, and a
    loud extraction failure is the wrong story to tell about a URL whose real problem is that it
    now serves an error page. The header decides only what the bytes leave open.
    """
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
    """THE extraction entry point: (title, content) for one fetched document, whatever its format.

    `body` is the raw bytes and `text` is the same bytes decoded by the HTTP client using the
    charset the server declared. Both are passed rather than one derived from the other because
    each format needs its own: PDF is binary and must never be decoded, HTML is text and its
    charset handling is not ours to reimplement.

    Dispatching here rather than in the caller is the point - scrape_page, and through it the
    Lambda, does not know or care which formats the corpus contains.
    """
    if _is_pdf(content_type, body):
        return extract_pdf(body, url=url)
    return extract_markdown(text, url=url)


def extract_markdown(html: str, url: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Extract (title, content markdown) from a page's HTML.

    trafilatura extracts the prose body: nav/header/footer boilerplate stripped, tables kept
    (office hours and eligibility criteria are often tabular) then flattened to prose because the
    KB wants flat text. The supplement pass (see the comment block above) recovers what its
    article model wrongly drops on this corpus - the www.sjsu.edu contact band and the link-tile
    text of landing pages - deduplicated against the body so nothing appears twice.

    Assembly order is band, body, recovered tiles: the page introduces itself. The band used to
    be APPENDED, which put every office's phone and email in the document's tail chunk under
    FIXED_SIZE chunking - a chunk with contact digits but often no office name, exactly the
    shape a "what is X's phone number" query has to embed-match (2026-08-10 eval: retrieval
    found the right pages while the band chunk for AEC never ranked). Leading with the band
    puts identity and contacts in chunk 1 next to the title scrape_page prepends. Dedup
    precedence is unchanged - a band block the body already carries is dropped, so the fact
    stays in the body where it already was.

    Both passes scrub replacement-char garbage baked into the page's own source (see
    `_scrub_replacement_chars`). Returns (title, None) only when BOTH passes find nothing
    (a redirect stub or an empty page).
    """
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

    # One extraction path for both formats - see extract_document. response.text honors the
    # page's declared charset for the HTML branch; the PDF branch reads response.content, which
    # must stay bytes. Both branches scrub replacement-char garbage baked into the source.
    try:
        title, markdown = extract_document(
            response.content,
            response.text,
            response.headers.get("content-type", ""),
            url=url,
        )
    except ExtractionError as exc:
        # ERROR, not WARNING: unlike a 404 or a timeout this does not resolve itself on the next
        # run. The document will keep extracting to nothing every day until someone removes it
        # from the crawl list, so it should read as a curation bug, which is what it is.
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

    # The extracted title wins, with the crawl list's curated title as the fallback. Extraction
    # tracks the live page (a renamed office shows up on the next run); the list is the safety
    # net, so a page whose <title> trafilatura cannot read still cites as something a student
    # recognizes instead of as a null.
    title = title or page.get("title") or None
    # The page introduces itself: the title leads the document as a heading. Bedrock embeds
    # only the chunk text, never the metadata sidecar, so without this a FIXED_SIZE chunk can
    # carry an office's facts with nothing naming the office. Prepended here rather than in
    # extract_markdown because only this frame knows the curated-title fallback. A body that
    # repeats the title in its own first heading costs a duplicate line, not a wrong fact.
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

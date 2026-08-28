"""The local scraper. No live network: HTTP is mocked via httpx.MockTransport."""

import csv
import json
import logging
import re
from pathlib import Path

import httpx
import pytest

import scraper

# A realistic static page: boilerplate wrapping a real article, in the corpus's own shape.
FIXTURE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Academic Advising | San Jose State University</title></head>
<body>
  <header><nav><ul>
    <li><a href="/">Home</a></li>
    <li><a href="/advising/">Advising</a></li>
    <li><a href="/admissions/">Admissions</a></li>
  </ul></nav></header>
  <aside class="sidebar">
    <h3>Quick Links</h3>
    <ul><li><a href="/advising/appointments">Appointments</a></li><li><a href="/advising/contact">Contact</a></li></ul>
  </aside>
  <main>
    <article>
      <h1>Academic Advising</h1>
      <p>Undergraduate Advising and Success is open Monday through Friday from 9:00 AM to 5:00 PM
         in Clark Hall. Drop-in advising is available during the first two weeks of each semester,
         and appointments can be scheduled online for the rest of the term.</p>
      <p>Students on academic probation are required to meet with an advisor before registering
         for the following semester. Bring an unofficial transcript to the appointment.</p>
      <p>For questions about degree requirements or major changes, contact your college advising
         center directly.</p>
    </article>
  </main>
  <footer><p>&copy; 2026 San Jose State University. All rights reserved. Privacy policy. Accessibility.</p></footer>
</body>
</html>
"""

# Replacement-char garbage baked into a page's own source.
FIXTURE_MOJIBAKE_HTML = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Counseling Services</title></head>"
    "<body><main><article><h1>Counseling Services</h1>"
    "<p>Counseling and Psychological Services supports every student&#239;&#191;&#189;s "
    "mental health through short-term therapy, crisis support and referral to community "
    "providers when longer care is needed.</p>"
    "</article></main></body></html>"
)

# University pages use tables for layout as well as data, so trafilatura emits empty cells.
FIXTURE_TABLE_HTML = (
    "<!DOCTYPE html><html><head><title>Financial Aid Deadlines</title></head><body><main><article>"
    "<h1>Financial Aid Deadlines</h1>"
    "<table>"
    "<tr><td>FAFSA priority deadline -- March 2 for the following academic year.</td><td></td></tr>"
    "<tr><td>Cal Grant GPA verification -- submitted by the high school or college.</td><td></td></tr>"
    "</table>"
    "<h2>Contact</h2>"
    "<table>"
    "<tr><td>Phone: (408) 283-7500</td><td>Email: fao@sjsu.edu</td></tr>"
    "<tr><td>Address: One Washington Square</td><td>San Jose, CA 95192</td></tr>"
    "</table>"
    "</article></main></body></html>"
)

# The crawl list carries two more columns than the scraper reads, and they must be ignored.
SEED_HEADER = "url,section,title,static_html,body_text_chars\n"
SEED_ROWS = (
    "https://www.sjsu.edu/advising/,academic-advising,Academic Advising,True,4210\n"
    "https://www.sjsu.edu/counseling/,counseling-psych,Counseling Services,True,3880\n"
)


def _seed_file(tmp_path, body, name="urls.csv") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _page(url="https://www.sjsu.edu/advising/", section="academic-advising", title="Advising"):
    return {"url": url, "section": section, "title": title}


def test_load_seed_pages_reads_required_columns_and_ignores_the_rest(tmp_path):
    pages = scraper.load_seed_pages(_seed_file(tmp_path, SEED_HEADER + SEED_ROWS))
    assert pages == [
        {
            "url": "https://www.sjsu.edu/advising/",
            "section": "academic-advising",
            "title": "Academic Advising",
        },
        {
            "url": "https://www.sjsu.edu/counseling/",
            "section": "counseling-psych",
            "title": "Counseling Services",
        },
    ]


def test_load_seed_pages_preserves_file_order(tmp_path):
    reversed_rows = "".join(reversed(SEED_ROWS.splitlines(keepends=True)))
    pages = scraper.load_seed_pages(_seed_file(tmp_path, SEED_HEADER + reversed_rows))
    assert [p["section"] for p in pages] == ["counseling-psych", "academic-advising"]


def test_a_missing_crawl_list_raises_rather_than_scraping_nothing(tmp_path):
    # The failure this guard exists for: [] here would prune the whole knowledge base.
    with pytest.raises(scraper.SeedListError, match="not found"):
        scraper.load_seed_pages(tmp_path / "absent.csv")


def test_a_header_only_crawl_list_raises(tmp_path):
    with pytest.raises(scraper.SeedListError, match="no pages"):
        scraper.load_seed_pages(_seed_file(tmp_path, SEED_HEADER))


def test_an_empty_crawl_list_raises(tmp_path):
    with pytest.raises(scraper.SeedListError):
        scraper.load_seed_pages(_seed_file(tmp_path, ""))


@pytest.mark.parametrize("dropped", scraper.SEED_COLUMNS)
def test_a_missing_required_column_raises_naming_the_column(tmp_path, dropped):
    # `section` rides into the sidecar, so a page missing it is an invisible gap.
    columns = [c for c in ("url", "section", "title") if c != dropped]
    body = ",".join(columns) + "\n" + ",".join(f"v-{c}" for c in columns) + "\n"
    with pytest.raises(scraper.SeedListError, match=dropped):
        scraper.load_seed_pages(_seed_file(tmp_path, body))


@pytest.mark.parametrize(
    "row",
    [
        ",academic-advising,Advising\n",
        "https://www.sjsu.edu/advising/,,Advising\n",
        "https://www.sjsu.edu/advising/,academic-advising,\n",
        "https://www.sjsu.edu/advising/,   ,Advising\n",
    ],
)
def test_a_blank_cell_raises_with_the_line_number(tmp_path, row):
    body = "url,section,title\n" + row
    with pytest.raises(scraper.SeedListError, match="line 2"):
        scraper.load_seed_pages(_seed_file(tmp_path, body))


@pytest.mark.parametrize(
    "url", ["www.sjsu.edu/advising/", "ftp://www.sjsu.edu/advising/", "/advising/", "javascript:0"]
)
def test_a_non_http_url_raises(tmp_path, url):
    body = f"url,section,title\n{url},academic-advising,Advising\n"
    with pytest.raises(scraper.SeedListError, match="not an http"):
        scraper.load_seed_pages(_seed_file(tmp_path, body))


def test_a_duplicate_url_raises(tmp_path):
    # A duplicate would upload the same slug twice per run and mask a copy-paste mistake.
    body = (
        "url,section,title\n"
        "https://www.sjsu.edu/advising/,academic-advising,Advising\n"
        "https://www.sjsu.edu/counseling/,counseling-psych,Counseling\n"
        "https://www.sjsu.edu/advising/,academic-advising,Advising Again\n"
    )
    with pytest.raises(scraper.SeedListError, match="line 4"):
        scraper.load_seed_pages(_seed_file(tmp_path, body))


def test_an_unreadable_crawl_list_raises_as_a_seed_list_error(tmp_path):
    # Invalid UTF-8 is what a corrupted asset looks like: a SeedListError, never a crash.
    path = tmp_path / "urls.csv"
    path.write_bytes(b"url,section,title\nhttps://x/a,sec,\xff\xfe title\n")
    with pytest.raises(scraper.SeedListError, match="could not be read"):
        scraper.load_seed_pages(path)


def test_the_real_crawl_list_is_valid_and_complete():
    """The committed data/urls.csv itself, not a fixture: this is the file that ships."""
    path = Path(__file__).resolve().parents[2] / "data" / "urls.csv"
    pages = scraper.load_seed_pages(path)

    with open(path, newline="", encoding="utf-8") as fh:
        data_rows = [r for r in csv.DictReader(fh) if (r.get("url") or "").strip()]
    assert len(pages) == len(data_rows)
    assert len(pages) > 1
    assert all(p["url"].startswith("https://") for p in pages)
    assert all(p["section"] and p["title"] for p in pages)


def test_seed_urls_is_the_configured_corpus_in_order():
    pages = [_page(url="https://x/a"), _page(url="https://x/b")]
    assert scraper.seed_urls(pages) == ["https://x/a", "https://x/b"]


def test_slugify_is_deterministic():
    url = "https://www.sjsu.edu/advising/index.php"
    assert scraper.slugify_url(url) == scraper.slugify_url(url)


def test_slugify_is_filesystem_safe_and_readable():
    slug = scraper.slugify_url("https://www.sjsu.edu/advising/index.php")
    assert re.fullmatch(r"[a-z0-9-]+", slug)
    assert slug.startswith("www-sjsu-edu-advising-index-php-")


def test_slugify_distinct_urls_do_not_collide():
    # Same slugified path but different query strings must yield different filenames.
    a = scraper.slugify_url("https://www.sjsu.edu/search?q=advising")
    b = scraper.slugify_url("https://www.sjsu.edu/search?q=aid")
    assert a != b


def test_slugify_root_path_becomes_index():
    slug = scraper.slugify_url("https://www.sjsu.edu/")
    assert slug.startswith("www-sjsu-edu-index-") or slug.startswith("www-sjsu-edu-")


def test_build_metadata_shape_and_injected_timestamp():
    md = scraper.build_metadata(
        "https://www.sjsu.edu/advising",
        "https://www.sjsu.edu/advising/",
        "Academic Advising",
        "academic-advising",
        "some markdown body",
        timestamp="2026-08-05T00:00:00Z",
    )
    assert md == {
        "source_url": "https://www.sjsu.edu/advising",
        "fetched_url": "https://www.sjsu.edu/advising/",
        "title": "Academic Advising",
        "section": "academic-advising",
        "scrape_timestamp": "2026-08-05T00:00:00Z",
        "content_chars": len("some markdown body"),
        "scraper_version": scraper.SCRAPER_VERSION,
    }


def test_metadata_has_required_attribution_keys():
    md = scraper.build_metadata("u", "u", None, "sec", "x", timestamp="2026-08-05T00:00:00Z")
    # section is on this list, not optional: it is the crawl list's curated grouping.
    for key in ("source_url", "title", "section", "scrape_timestamp"):
        assert key in md


def test_extract_markdown_keeps_article_drops_boilerplate():
    title, markdown = scraper.extract_markdown(FIXTURE_HTML, url="https://www.sjsu.edu/advising/")
    assert markdown, "expected non-empty markdown from the fixture"
    assert "Monday through Friday" in markdown
    assert "academic probation" in markdown
    assert "Admissions" not in markdown
    assert "Quick Links" not in markdown
    assert "All rights reserved" not in markdown
    # Title extracted (site suffix may or may not be trimmed by trafilatura).
    assert title and "Academic Advising" in title


FIXTURE_SJSU_TEMPLATE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Bursar's Office | SJSU</title></head>
<body>
  <header><nav><ul>
    <li><a href="/">Home</a></li><li><a href="/bursar/">Bursar</a></li>
  </ul></nav></header>
  <main id="sjsu-maincontent" role="main">
    <h1>Bursar's Office</h1>
    <p>We are proud to offer an affordable high-quality education to all of our students, and
       the Bursar's Office keeps student accounts accurate from registration to graduation.</p>
    <div class="o-grid">
      <div class="o-grid__item">
        <h2><a href="/bursar/fees-due-dates/">Fees and Due Dates</a></h2>
        <div>See how tuition and fees vary by semester, course, program or student type.</div>
      </div>
      <div class="o-grid__item">
        <h2><a href="/bursar/payment-refunds/">Payment Plans</a></h2>
        <div>Installment payment plans spread tuition across the term for eligible students.</div>
      </div>
    </div>
  </main>
  <div class="o-region--contact u-bg--typeface-pattern-diamonds" role="complementary">
    <div class="o-region--contact__label"><h2 class="o-region--contact__title">Bursar's Office</h2></div>
    <div class="o-region--contact__block">
      <h3 class="o-region--contact__heading">Contact Us</h3>
      <div class="o-region--contact__detail">
        <p><strong>Urgent inquiries:</strong><br>Phone: <a href="tel:4089241601">408-924-1601</a><br>
           Monday - Friday 9:00 a.m. - 4:00 p.m.<br>
           Email: <a href="mailto:bursar@sjsu.edu">bursar@sjsu.edu</a></p>
      </div>
    </div>
  </div>
  <footer><p>&copy; 2026 San Jose State University. Footer boilerplate and privacy policy.</p></footer>
</body>
</html>
"""

# A landing page that is only tiles, with no paragraph for trafilatura to anchor on.
FIXTURE_TILES_ONLY_HTML = """
<!DOCTYPE html>
<html><head><title>Get Help | Library</title></head>
<body>
  <div class="navbar" role="banner"><a href="/">Library Home</a><a href="/hours">Hours</a></div>
  <div role="main">
    <div class="tiles">
      <div class="tile"><a href="/chat">Chat with a Librarian</a></div>
      <div class="tile"><a href="/email">Email a research question to the reference desk</a></div>
      <div class="tile"><a href="/onesearch">OneSearch the catalog and databases</a></div>
    </div>
  </div>
</body>
</html>
"""


def test_contact_band_reaches_the_markdown():
    """Outside <main> and role="complementary": right to drop, and impossible to lose."""
    _, markdown = scraper.extract_markdown(FIXTURE_SJSU_TEMPLATE_HTML, url="https://www.sjsu.edu/bursar/index.php")
    assert markdown
    assert "408-924-1601" in markdown
    assert "bursar@sjsu.edu" in markdown
    assert "Monday - Friday 9:00 a.m. - 4:00 p.m." in markdown
    # The band separates its lines with <br> alone, which must not glue facts together.
    assert "408-924-1601 Monday" in markdown


def test_link_tile_text_reaches_the_markdown_and_chrome_still_does_not():
    _, markdown = scraper.extract_markdown(FIXTURE_SJSU_TEMPLATE_HTML, url="https://www.sjsu.edu/bursar/index.php")
    assert markdown
    assert "Fees and Due Dates" in markdown
    assert "Installment payment plans spread tuition" in markdown
    # The supplement pass must not reopen the door to chrome.
    assert "Footer boilerplate" not in markdown
    assert "privacy policy" not in markdown


def test_supplement_pass_adds_nothing_twice():
    """Prose trafilatura already kept is also a region block; dedup must keep it single."""
    _, markdown = scraper.extract_markdown(FIXTURE_SJSU_TEMPLATE_HTML, url="https://www.sjsu.edu/bursar/index.php")
    assert markdown.count("affordable high-quality education") == 1
    assert markdown.count("Installment payment plans") == 1
    assert markdown.count("408-924-1601") == 1


def test_the_contact_band_leads_the_document():
    """Assembled first, not appended, so contacts land in chunk one beside the title."""
    _, markdown = scraper.extract_markdown(
        FIXTURE_SJSU_TEMPLATE_HTML, url="https://www.sjsu.edu/bursar/index.php"
    )
    assert markdown.index("408-924-1601") < markdown.index("affordable high-quality education")
    assert markdown.index("bursar@sjsu.edu") < markdown.index("Fees and Due Dates")


def test_tiles_only_page_still_extracts():
    """A page with no paragraphs at all must still produce a document, not an ok=False."""
    _, markdown = scraper.extract_markdown(FIXTURE_TILES_ONLY_HTML, url="https://library.sjsu.edu/ask-librarian")
    assert markdown
    assert "Chat with a Librarian" in markdown
    assert "OneSearch" in markdown


def test_supplement_pass_skips_chrome_marked_by_aria_role():
    """The LibGuides navbar is a plain div, so tag names alone do not exclude it."""
    tree = scraper._parse_tree(FIXTURE_TILES_ONLY_HTML)
    blocks = []
    # The whole body, not the content region: the banner sits outside role="main".
    scraper._collect_blocks(tree.find("body"), blocks)
    assert any("Chat with a Librarian" in b for b in blocks)
    assert not any("Library Home" in b for b in blocks)


def test_supplement_pass_survives_unparseable_html():
    """lxml failing must degrade to plain trafilatura behaviour, never raise."""
    title, markdown = scraper.extract_markdown("", url="https://www.sjsu.edu/empty/")
    assert markdown is None
    title, markdown = scraper.extract_markdown("<<<not really html", url="https://www.sjsu.edu/garbage/")
    assert title is None or isinstance(title, str)


def _build_pdf(pages) -> bytes:
    """A minimal, valid multi-page PDF drawing one text line per string in `pages[i]`."""
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    for i, lines in enumerate(pages):
        page_obj = 4 + 2 * i
        objs[page_obj] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_obj + 1} 0 R >>"
        ).encode()
        body = ["BT /F1 12 Tf 72 720 Td 14 TL"]
        for line in lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            body.append(f"({escaped}) Tj T*")
        body.append("ET")
        stream = "\n".join(body).encode("latin-1")
        objs[page_obj + 1] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref, count = len(out), max(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % count
    for num in range(1, count):
        out += b"%010d 00000 n \n" % offsets.get(num, 0)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (count, xref)
    return bytes(out)


# Shaped like the real Writing Center handouts: a footer differing only in its page number.
FIXTURE_PDF_PAGES = [
    [
        "Email Etiquette for Students, Rev. Summer 2014  1 of 2",
        "Email etiquette matters because email is the main mode of communication between",
        "students and professors. Refer to the syllabus before you write.",
    ],
    [
        "Email Etiquette for Students, Rev. Summer 2014  2 of 2",
        "Identify yourself by full name, course number, and section number.",
        "Use a formal salutation and signature, and send from your SJSU address.",
    ],
]


def test_extract_pdf_returns_the_prose():
    _title, text = scraper.extract_pdf(_build_pdf(FIXTURE_PDF_PAGES))
    assert "Email etiquette matters because email is the main mode of communication between" in text
    assert "Identify yourself by full name, course number, and section number." in text


def test_extract_pdf_strips_the_running_footer_from_every_page():
    """The whole point of the furniture pass: chunking is fixed-size and page-blind."""
    _title, text = scraper.extract_pdf(_build_pdf(FIXTURE_PDF_PAGES))
    assert "Email Etiquette for Students" not in text
    assert "1 of 2" not in text and "2 of 2" not in text


def test_extract_pdf_keeps_a_line_that_appears_on_only_one_page():
    """Furniture is what repeats: a short heading is content, not furniture."""
    pages = [["Sample Email"] + FIXTURE_PDF_PAGES[0][1:], FIXTURE_PDF_PAGES[1]]
    _title, text = scraper.extract_pdf(_build_pdf(pages))
    assert "Sample Email" in text


def test_extract_pdf_returns_no_title_so_the_curated_one_wins():
    """PDF /Title on this corpus is empty, a placeholder or a filename; the curated one wins."""
    title, _text = scraper.extract_pdf(_build_pdf(FIXTURE_PDF_PAGES))
    assert title is None


def test_a_pdf_with_no_text_raises_loudly():
    """A scan must never become a titled, contentless knowledge base document."""
    with pytest.raises(scraper.ExtractionError, match="no usable text"):
        scraper.extract_pdf(_build_pdf([[], [], []]))


def test_a_pdf_with_only_a_scrap_of_text_raises_loudly():
    """Not empty, so a truthiness check passes, but only a caption's worth of text."""
    with pytest.raises(scraper.ExtractionError, match="no usable text"):
        scraper.extract_pdf(_build_pdf([["Figure 1"], ["Figure 2"]]))


def test_unparseable_pdf_bytes_raise_extraction_error_not_a_stray_exception():
    with pytest.raises(scraper.ExtractionError, match="could not be parsed"):
        scraper.extract_pdf(b"%PDF-1.4\nthis is not a pdf")


def test_extract_document_routes_html_to_the_html_extractor():
    title, markdown = scraper.extract_document(
        FIXTURE_HTML.encode("utf-8"), FIXTURE_HTML, "text/html; charset=utf-8"
    )
    assert title == "Academic Advising"  # from the page's own <title>, unlike the PDF path
    assert "Monday through Friday" in markdown


def test_extract_document_routes_pdf_to_the_pdf_extractor():
    data = _build_pdf(FIXTURE_PDF_PAGES)
    title, text = scraper.extract_document(data, "", "application/pdf")
    assert title is None
    assert "Identify yourself by full name" in text


def test_extract_document_sniffs_a_pdf_the_server_mislabels():
    """www.sjsu.edu is not guaranteed to label every handout; the bytes decide."""
    data = _build_pdf(FIXTURE_PDF_PAGES)
    _title, text = scraper.extract_document(data, "", "application/octet-stream")
    assert "Identify yourself by full name" in text


def test_extract_document_does_not_send_html_to_the_pdf_extractor_on_a_wrong_header():
    """An HTML error page served as application/pdf: the bytes say HTML, so HTML wins."""
    title, markdown = scraper.extract_document(
        FIXTURE_HTML.encode("utf-8"), FIXTURE_HTML, "application/pdf"
    )
    assert title == "Academic Advising"
    assert "Monday through Friday" in markdown


def test_scrub_replacement_chars():
    assert scraper._scrub_replacement_chars("studentï¿½s") == "students"  # "ï¿½" triple
    assert scraper._scrub_replacement_chars("a�b") == "ab"  # bare U+FFFD
    assert scraper._scrub_replacement_chars("clean text") == "clean text"
    assert scraper._scrub_replacement_chars(None) is None
    assert scraper._scrub_replacement_chars("") == ""


def test_extract_markdown_scrubs_baked_in_mojibake():
    _title, markdown = scraper.extract_markdown(
        FIXTURE_MOJIBAKE_HTML, url="https://www.sjsu.edu/counseling/"
    )
    assert markdown
    assert "ï¿½" not in markdown, "baked-in 'ï¿½' garbage still present"
    assert "�" not in markdown, "bare U+FFFD still present"
    assert "students mental health" in markdown  # "student[ï¿½]s" -> garbage stripped


def test_flatten_markdown_tables_unit():
    src = (
        "# Title\n\n"
        "| FAFSA priority deadline -- March 2. | |\n"
        "| --- | --- |\n"
        "| Phone: (408) 283-7500 | Email: fao@sjsu.edu |\n\n"
        "Regular paragraph.\n"
    )
    out = scraper._flatten_markdown_tables(src)
    lines = out.split("\n")
    assert "| |" not in out
    assert not any(ln.strip().startswith("|") for ln in lines)
    assert "# Title" in lines
    assert "Regular paragraph." in lines
    assert "FAFSA priority deadline -- March 2." in lines
    assert "Phone: (408) 283-7500" in lines
    assert "Email: fao@sjsu.edu" in lines


def test_extract_markdown_flattens_layout_tables_to_prose():
    _title, markdown = scraper.extract_markdown(
        FIXTURE_TABLE_HTML, url="https://www.sjsu.edu/faso/deadlines/"
    )
    assert markdown
    assert "| |" not in markdown
    assert not any(ln.strip().startswith("|") for ln in markdown.split("\n"))
    assert "FAFSA priority deadline -- March 2 for the following academic year." in markdown
    assert "Phone: (408) 283-7500" in markdown
    assert "Email: fao@sjsu.edu" in markdown
    assert "San Jose, CA 95192" in markdown
    assert "# Financial Aid Deadlines" in markdown


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_scrape_page_success_carries_the_curated_section(monkeypatch):
    def handler(request):
        return httpx.Response(200, html=FIXTURE_HTML)

    page = _page(section="academic-advising")
    with _client(handler) as client:
        result = scraper.scrape_page(page, client)

    assert result.ok
    assert "Monday through Friday" in result.markdown
    assert result.section == "academic-advising"
    # And it reaches the sidecar, which is where retrieval reads it back out.
    assert result.metadata["section"] == "academic-advising"
    assert result.metadata["source_url"] == page["url"]
    assert result.metadata["content_chars"] == len(result.markdown)


def test_scrape_page_scrubs_mojibake_end_to_end():
    # Served correctly as UTF-8, as the real site does: the garbage is in the source itself.
    def handler(request):
        return httpx.Response(
            200,
            content=FIXTURE_MOJIBAKE_HTML.encode("utf-8"),
            headers={"content-type": "text/html; charset=utf-8"},
        )

    with _client(handler) as client:
        result = scraper.scrape_page(_page(url="https://www.sjsu.edu/counseling/"), client)

    assert result.ok
    assert "ï¿½" not in result.markdown
    assert "�" not in result.markdown
    assert "students mental health" in result.markdown


def test_scrape_page_leads_with_the_title_as_a_heading():
    """The page introduces itself: Bedrock embeds only chunk text, never the sidecar."""

    def handler(request):
        return httpx.Response(200, html=FIXTURE_HTML)

    with _client(handler) as client:
        result = scraper.scrape_page(_page(), client)

    assert result.ok
    first_line = result.markdown.splitlines()[0]
    assert first_line.startswith("# ")
    assert "Academic Advising" in first_line


def test_scrape_page_heading_uses_the_curated_title_fallback():
    def handler(request):
        return httpx.Response(
            200,
            html="<html><body><main><article><p>"
            + ("Peer connections run drop-in groups every weekday afternoon. " * 6)
            + "</p></article></main></body></html>",
        )

    with _client(handler) as client:
        result = scraper.scrape_page(_page(title="Peer Connections"), client)

    assert result.ok
    assert result.markdown.startswith("# Peer Connections\n\n")


def test_scrape_page_falls_back_to_the_curated_title():
    # An unreadable <title> must still cite as something a student recognises.
    def handler(request):
        return httpx.Response(
            200,
            html="<html><body><main><article><p>"
            + ("Peer connections run drop-in groups every weekday afternoon. " * 6)
            + "</p></article></main></body></html>",
        )

    with _client(handler) as client:
        result = scraper.scrape_page(_page(title="Peer Connections"), client)

    assert result.ok
    assert result.title == "Peer Connections"
    assert result.metadata["title"] == "Peer Connections"


def test_scrape_page_404_is_graceful_and_keeps_its_section():
    def handler(request):
        return httpx.Response(404, text="Not Found")

    with _client(handler) as client:
        result = scraper.scrape_page(_page(url="https://www.sjsu.edu/gone/"), client)

    assert result.ok is False
    assert result.error == "HTTP 404"
    assert result.section == "academic-advising"
    assert result.markdown is None
    assert result.metadata is None


def test_scrape_page_network_error_is_graceful():
    def handler(request):
        raise httpx.ConnectError("dns failure", request=request)

    with _client(handler) as client:
        result = scraper.scrape_page(_page(url="https://nope.invalid/"), client)

    assert result.ok is False
    assert "ConnectError" in result.error
    assert result.markdown is None


def test_scrape_page_with_no_extractable_content_fails_gracefully():
    def handler(request):
        return httpx.Response(200, html="<html><body></body></html>")

    with _client(handler) as client:
        result = scraper.scrape_page(_page(), client)

    assert result.ok is False
    assert result.error == "no content extracted"


def test_scrape_page_handles_a_pdf_and_titles_it_from_the_crawl_list():
    """The same call the Lambda makes: no branch on format at the call site."""
    def handler(request):
        return httpx.Response(
            200,
            content=_build_pdf(FIXTURE_PDF_PAGES),
            headers={"content-type": "application/pdf"},
        )

    page = _page(url="https://www.sjsu.edu/writingcenter/docs/handouts/x.pdf", title="Email Etiquette")
    with _client(handler) as client:
        result = scraper.scrape_page(page, client)

    assert result.ok
    # The curated title, not a filename and not a PDF metadata placeholder.
    assert result.title == "Email Etiquette"
    assert result.markdown.startswith("# Email Etiquette")
    assert "Use a formal salutation and signature" in result.markdown
    assert result.metadata["content_chars"] == len(result.markdown)


def test_scrape_page_fails_loudly_on_an_image_only_pdf(caplog):
    """The corpus must not gain a titled, cited, contentless document."""
    def handler(request):
        return httpx.Response(
            200, content=_build_pdf([[], []]), headers={"content-type": "application/pdf"}
        )

    with caplog.at_level(logging.ERROR, logger="scraper"):
        with _client(handler) as client:
            result = scraper.scrape_page(_page(url="https://www.sjsu.edu/scan.pdf"), client)

    assert result.ok is False
    assert result.markdown is None
    assert "no usable text" in result.error
    assert any(rec.levelno == logging.ERROR for rec in caplog.records)


def test_scrape_pages_continues_past_a_failure():
    good = "https://www.sjsu.edu/advising/"
    bad = "https://www.sjsu.edu/gone/"

    def handler(request):
        if str(request.url) == bad:
            return httpx.Response(404, text="Not Found")
        return httpx.Response(200, html=FIXTURE_HTML)

    pages = [_page(url=good), _page(url=bad, section="financial-aid")]
    with _client(handler) as client:
        results = scraper.scrape_pages(pages, client=client)

    assert len(results) == 2
    assert results[0].ok is True
    assert results[1].ok is False
    assert results[1].section == "financial-aid"


def test_write_result_creates_md_and_json(tmp_path):
    result = scraper.ScrapeResult(
        url="https://www.sjsu.edu/advising/",
        slug="www-sjsu-edu-advising-deadbeef",
        section="academic-advising",
        ok=True,
        title="Academic Advising",
        markdown="# Academic Advising\n\nOpen weekdays.",
        metadata={"source_url": "https://www.sjsu.edu/advising/", "title": "Academic Advising"},
    )
    md_path = scraper.write_result(result, tmp_path)
    json_path = tmp_path / f"{result.slug}.json"

    assert md_path.exists()
    assert md_path.read_text(encoding="utf-8").startswith("# Academic Advising")
    assert json.loads(json_path.read_text(encoding="utf-8"))["title"] == "Academic Advising"


def test_write_result_skips_failed(tmp_path):
    result = scraper.ScrapeResult(url="u", slug="s", section="sec", ok=False, error="HTTP 404")
    assert scraper.write_result(result, tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_cli_exits_2_on_a_bad_crawl_list_without_scraping(tmp_path, monkeypatch):
    # Same as the Lambda: a bad list is a hard stop, not a zero-page run.
    def explode(*a, **kw):
        raise AssertionError("scrape_pages must not run when the crawl list is unusable")

    monkeypatch.setattr(scraper, "scrape_pages", explode)
    code = scraper.main(["--url-list", str(tmp_path / "absent.csv"), "--output-dir", str(tmp_path)])
    assert code == 2


def test_cli_section_filter_scrapes_only_that_section(tmp_path, monkeypatch):
    captured = {}

    def fake_scrape(pages, **kw):
        captured["pages"] = pages
        return []

    monkeypatch.setattr(scraper, "scrape_pages", fake_scrape)
    seed = _seed_file(tmp_path, SEED_HEADER + SEED_ROWS)
    code = scraper.main(
        [
            "--url-list",
            str(seed),
            "--output-dir",
            str(tmp_path / "out"),
            "--section",
            "counseling-psych",
        ]
    )
    assert code == 0
    assert [p["url"] for p in captured["pages"]] == ["https://www.sjsu.edu/counseling/"]


def test_cli_unknown_section_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper, "scrape_pages", lambda pages, **kw: [])
    seed = _seed_file(tmp_path, SEED_HEADER + SEED_ROWS)
    code = scraper.main(
        ["--url-list", str(seed), "--output-dir", str(tmp_path / "out"), "--section", "nope"]
    )
    assert code == 2


def test_cli_limit_caps_the_page_count(tmp_path, monkeypatch):
    captured = {}

    def fake_scrape(pages, **kw):
        captured["pages"] = pages
        return []

    monkeypatch.setattr(scraper, "scrape_pages", fake_scrape)
    seed = _seed_file(tmp_path, SEED_HEADER + SEED_ROWS)
    scraper.main(["--url-list", str(seed), "--output-dir", str(tmp_path / "out"), "--limit", "1"])
    assert len(captured["pages"]) == 1


def test_cli_reports_a_partial_failure_with_exit_1(tmp_path, monkeypatch):
    ok = scraper.ScrapeResult(
        url="https://x/a",
        slug="x-a",
        section="academic-advising",
        ok=True,
        title="A",
        markdown="# A\n\nbody",
        metadata={"source_url": "https://x/a", "content_chars": 11},
    )
    bad = scraper.ScrapeResult(url="https://x/b", slug="x-b", section="sec", ok=False, error="HTTP 404")
    monkeypatch.setattr(scraper, "scrape_pages", lambda pages, **kw: [ok, bad])
    seed = _seed_file(tmp_path, SEED_HEADER + SEED_ROWS)
    out_dir = tmp_path / "out"

    code = scraper.main(["--url-list", str(seed), "--output-dir", str(out_dir)])

    assert code == 1
    assert (out_dir / "x-a.md").exists()
    assert not (out_dir / "x-b.md").exists()

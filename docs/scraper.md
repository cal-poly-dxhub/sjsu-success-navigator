# The scraper (scraper/)

What the code in `scraper/` does that reading it will not tell you: the AWS behaviours it is
shaped around, the measured numbers behind its constants, and the one failure it is built to
make loud.

`scraper/scraper.py` is the fetch and extract core, with no filesystem or AWS coupling, plus
a local CLI. `scraper/lambda_function.py` is the thin wrapper that uploads and triggers
ingestion. The code carries one-line pointers into the sections below.

## The crawl list is the corpus

`load_seed_pages` is the only reader of `data/urls.csv`, and **it raises rather than returning
a short or empty list.** That is the single most important behaviour in this tree.

A run that fetches nothing hands `prune_stale_objects` an empty expected set, which deletes
every document in the knowledge base, and then starts an ingestion job over the wreckage. A
Lambda that errors out has pruned nothing, so the corpus survives a bad deploy. The handler
therefore loads the list **first and unguarded**: a `SeedListError` fails the invocation
before anything is deleted.

It raises on a missing file, a missing required column, a blank cell, a non-http URL, a
duplicate URL, or a header with no rows. `section` is required rather than optional: it rides
into the metadata sidecar, comes back on every retrieved chunk, and reaches `SourceOption` in
`app/cards.py`. No app code switches on its value today (the section-to-preset table was
deleted when the card tag contract landed), but it is the curated grouping the crawl list
exists to carry, and a page with no section would be an invisible gap rather than a loud one.

**Every check is duplicated in `infra/infra/config.py:resolve_seed_pages`,** which runs the
same validation at synth so a broken list fails `cdk synth` instead of deploying. The
duplication is deliberate: this module ships in the Lambda bundle and `infra/` does not, so
they cannot share code. The runtime check is the one that matters, because the list travels as
a bundled asset and an asset can be stale, truncated or absent regardless of what synth saw.

**The list arrives as a bundled asset, not as an environment variable.** Lambda caps all
environment variables at **4 KB in aggregate** and the limit is not raisable; the list is 19 KB
as compact url/section JSON and 2.9 KB even gzipped and base64'd, so the source project's
approach of shipping seed URLs inside an env var does not survive the scale-up. It ships as a
Lambda layer extracted to `/opt`, and the only thing in the environment is its **filename**,
which is the same value the stack feeds to the asset, so the two cannot drift. The function's
own directory is searched second so a local run keeps working.

**Only the listed URLs are fetched.** There is deliberately no link-following: the corpus is a
curated list, and a crawler over sjsu.edu would pull in the whole university.

## One daily run, no tiers

Every invocation is the complete sweep, so there is no tier to pass and nothing in the event
is read. Cheap by calculation: 203 pages scale the source project's measured 19-pages-in-25-67s
to roughly 4.5 to 12 minutes at 512 MB (about $0.12/month of Lambda), a full corpus re-embed is
about 190K Titan tokens (under $0.01), and change gating means an unchanged day pays only the
Lambda run. It fits the 15-minute timeout, but the top of that range is close enough that the
run summary logs `duration_seconds` for the first live runs to be read rather than assumed.

## Change gating

Two gates, because the site changes far less often than we look at it.

**Gate 1: upload only what changed.** Each markdown object carries a `content-sha256` of
exactly what this scraper produced for that page, in S3 user metadata; a page whose fingerprint
matches what is already in the bucket is not re-uploaded. Bedrock's sync is incremental on its
own, but re-PUTting an object still moves its `LastModified` and re-parses it, so gating here is
what makes an unchanged run genuinely free. boto3 lowercases user-metadata keys on the way out
of `head_object`, so the key is spelled lowercase.

**Gate 2: ingest only when the bucket moved.** No uploads and no prunes means no ingestion job.

**The fingerprint covers the body and the sidecar fields Bedrock consumes, and deliberately
not the timestamp.** Hashing `scrape_timestamp` would mark every page changed on every run and
turn the gating into an expensive no-op. It does cover `source_url`, `title` and `section` (the
first two drive citation attribution and the third is the crawl list's curated grouping), so a
re-sectioned or retitled page re-uploads even when the body is untouched.

**No fingerprint means "treat this page as changed".** The object is absent (first run), or it
predates change gating, or S3 could not be read. Every one of those wants an upload, so the
failure direction is a redundant PUT rather than a missed update.

**Content-hash stability is unmeasured on sjsu.edu.** The property the daily schedule rests on
is that an unchanged page produces the same fingerprint run after run. That held on the source
project's corpus (all 19 pages byte-identical across two back-to-back scrapes, with
`scrape_timestamp` the only field that moved). Our pages have never been scraped twice and
compared, because that needs an account. A page carrying a per-render nonce, a rotating
announcement, or a "last updated" line inside the extracted body would defeat gate 1 for that
page. The cost of being wrong is re-uploads and re-ingestion, not incorrect answers, and the
run summary's `pages_changed` count is where it would show up: a count that never drops to
zero.

**The sidecar is gated with its markdown rather than separately,** because it carries a
`scrape_timestamp` that moves every run, so gating it on its own bytes would re-upload all of
them forever and re-trigger ingestion each time.

## Pruning

Objects the crawl list no longer calls for are deleted from the source bucket **before**
ingestion, so the same job that indexes new content also retires removed content.

**Why it exists:** the uploader only ever put objects, so without a prune a page removed from
the crawl list simply stops being refreshed while its document stays in the bucket and stays
indexed forever. De-listing would be a silent no-op.

**Why it prunes against the list and not against this run's uploads:** pruning by what
succeeded would make one transient fetch failure delete that page from the knowledge base. A
404 on the financial-aid index for a single day would drop every deadline answer the assistant
has until someone noticed. Keying on the crawl list means a failed fetch leaves the last-good
document in place.

**The live keys are unioned in as a belt-and-braces guard.** `expected_kb_keys` derives slugs
from the crawl-list URL while the uploader derives them from the fetched result. Both slugify
the same input today, but any future divergence (a redirect used for slugging, a slug scheme
change) would otherwise make the prune delete the objects this run just confirmed. Whatever
this run saw as a live page is by definition wanted, including pages it left alone because they
had not changed. Change gating is what makes that half necessary: without gating, every
successful page is re-uploaded every run, so the uploaded set happens to cover the whole corpus
and the distinction does not exist.

**A prune failure is never fatal:** it is housekeeping, and it must not break a scrape whose
uploads already landed.

## Ingestion

**Bedrock allows exactly one ingestion job per data source,** and `StartIngestionJob` is also
rate-limited to one per ten seconds. So an overlap (a manual invoke landing on top of the
schedule, or the deploy trigger firing while a scheduled run is mid-flight) has to skip cleanly
rather than throw. `STARTING`, `IN_PROGRESS` and `STOPPING` all mean the data source is still
occupied; `STOPPING` counts because the job has not released it yet. The full enum is
`STARTING, IN_PROGRESS, COMPLETE, FAILED, STOPPING, STOPPED`.

**Skipping is only safe if the skipped change is picked up later, and "this run uploaded
something" cannot provide that.** The next run finds the page unchanged, uploads nothing, and
would start no job, leaving content sitting in the bucket unindexed indefinitely. The fix is a
second, store-free signal: compare the newest object in the bucket against the **start time**
of the last ingestion job Bedrock ran. Anything newer is unindexed, whoever put it there and
whenever, so a deferred run heals itself on the next daily run.

**Started-at rather than completed-at,** because a job indexes the bucket as of roughly when it
began. Comparing against the start time can only over-trigger, which costs a harmless
incremental sync; comparing against completion could skip an object written mid-job.

**The bucket-latest lookup only runs when this run changed nothing,** because that is the only
case where the answer decides anything.

**Job history is soft-fail.** Losing visibility of it degrades the decision to "start when this
run changed something", which is the un-gated behaviour, and never blocks ingestion. A start
that raises (a job that raced us between the list and the start, or a throttle on the
one-per-ten-seconds limit) is the overlap case again and self-heals next run.

## No new store

Change detection reads S3 object metadata, S3 `LastModified`, and Bedrock's own ingestion-job
history. There is no DynamoDB table, no state file, and nothing to back up or clean up.

## The metadata sidecar

Uploaded as `<slug>.md.metadata.json`, **not** `<slug>.json`. Bedrock treats `*.metadata.json`
as metadata for the sibling document and does not ingest it as its own document; a plain
`<slug>.json` (which is the local CLI's inspection filename) **would** be ingested as a JSON
document, polluting the knowledge base. The wrapper is Bedrock's documented
`metadataAttributes` shape.

`section` rides along with `source_url` and `title` because retrieval reads it back off every
chunk and it reaches `SourceOption` in `app/cards.py`. Nothing switches on it today; dropping
it here would remove the curated grouping from the corpus silently, with nothing in the logs to
say so.

## Slugs

`<host><path>` with non-alphanumerics collapsed to hyphens, bounded for readability, plus a
short sha256 prefix of the **full** URL including any query string, so distinct URLs that would
otherwise slugify identically never collide. Same URL in, same slug out, always.

This is load-bearing beyond filenames: the prune derives expected object keys from the crawl
list through this function while the uploader derives them from the fetched result, so any
instability here would let a run delete the documents it just wrote.

## Extraction

`extract_document` is the only extraction entry point and it dispatches on what the server
actually sent, so callers never branch on format.

**PDF is not an afterthought on this corpus.** SJSU publishes most of its academic-coaching
material (how to email a professor, how to use office hours) only as Writing Center and Peer
Connections handout PDFs, so an HTML-only extractor cannot answer the questions the sponsors
asked for.

**Format is decided by magic bytes first, declared content type second.** The bytes lead
because they are the document and a `Content-Type` header is only a claim about it, and this
corpus has seen both claims go wrong. A handout served as `application/octet-stream` still has
to be read as a PDF, so `%PDF-` alone is enough to say yes. A soft-404 HTML page served as
`application/pdf` must not be, so an opening angle bracket is enough to say no before the
header is consulted at all: otherwise that page reaches pypdf, which raises, and a loud
extraction failure is the wrong story to tell about a URL whose real problem is that it now
serves an error page.

### The HTML supplement pass

trafilatura models a page as an article: prose in the middle, boilerplate around it. Two
layouts in this corpus break that model, measured against the live knowledge base in the
**2026-08-10 audit: 39 of 203 ingested documents carried any phone number, 52 carried any
email, and EOP's and AEC's contact information appeared in none.** Both causes are static HTML;
the JS hypothesis was tested and is false, so no browser automation.

1. **The www.sjsu.edu CMS puts each office's phone, email and hours in a styled band outside
   `<main>`** (class `o-region--contact`, `role="complementary"`). That is template chrome to an
   article extractor and per-office data to us, and for a routing assistant it is the most
   valuable text on the page. Even the offices' dedicated contact-us pages keep their facts
   there.
2. **Landing pages carry their content as link-tile grids** (heading, link, one-line
   description, repeated). Link-dense and paragraph-poor, so the boilerplate heuristics prune
   it, and trafilatura's own `favor_recall` and `include_links` options measurably do not bring
   it back: on the bursar index, 341 of 1,491 in-main characters survive, identical with
   `favor_recall`.

So trafilatura stays, because it is right about real chrome and good at prose, and a second
lxml pass recovers what its model wrongly drops: the contact band, plus any content-region
block missing from its output. Dedup is on letters-and-digits-only normalisation, so markdown
escaping and whitespace reflow cannot make the same sentence look new.

**Verified against all 228 live pages: 0 failures, phones 39/203 to 192/228, emails 52/203 to
192/228.**

**`<br>` tails get a leading space** because `text_content()` otherwise glues the surrounding
lines together: the contact band separates `Phone: 408-924-1601` from `Monday - Friday` with
nothing but a `<br>`, and `408-924-1601Monday` in the knowledge base is a corrupted fact rather
than a phone number.

**The contact band is descended from its children, not its root,** because the band root
carries `role="complementary"`, which the block walker rightly skips everywhere else. Nested
bands are handled by collecting from the outermost only.

**Chrome is skipped by tag and by ARIA role.** The library's LibGuides template marks its
navbar with `role="banner"` on a plain `<div>`, so tag names alone do not exclude it. Blocks
under 3 characters are bare widget labels ("Go", "FAQ" links) rather than content. A
`div`/`section`/`article` with no block-level or container descendants is treated as one tile
of a link grid, whose heading-less label and description text would otherwise be skipped
entirely. A collected block is not revisited, so an `<a>` inside a collected `<li>` is never
double-counted.

**lxml parsing is best effort:** a page lxml cannot parse still gets its trafilatura
extraction, exactly as before this pass existed.

**Tables are extracted and then flattened to prose.** University pages use tables for layout as
well as for data, so trafilatura emits mangled `| cell | |` rows with empty cells; the
flattener is deliberately dumb and robust, working on the output markdown rather than parsing
HTML tables, with no column or header semantics. `include_tables` stays True on purpose:
setting it False makes trafilatura drop heading markup and jam adjacent cells together with no
separator, which is worse.

**Assembly order is band, body, recovered tiles, so the page introduces itself.** The band used
to be **appended**, which put every office's phone and email in the document's tail chunk under
FIXED_SIZE chunking: a chunk with contact digits but often nothing naming the office, which is
exactly the shape a "what is X's phone number" query has to embed-match. The AEC probe found its
contact chunk unrankable. Bedrock embeds only chunk text, never the metadata sidecar. Leading
with the band puts identity and contacts in chunk 1, next to the title. Dedup precedence is
unchanged, so a band block the body already carries stays in the body.

**The title leads the document as an H1,** for the same reason: without it a FIXED_SIZE chunk
can carry an office's facts with nothing naming the office. It is prepended in `scrape_page`
rather than in `extract_markdown` because only that frame knows the curated-title fallback. A
body that repeats the title in its own first heading costs a duplicate line, not a wrong fact.

**The extracted title wins, with the crawl list's curated title as the fallback.** Extraction
tracks the live page, so a renamed office shows up on the next run; the list is the safety net,
so a page whose `<title>` trafilatura cannot read still cites as something a student recognises
rather than as a null.

**Both extraction changes rewrite every fingerprint,** so the next deploy re-uploads and
re-ingests the whole corpus. That is deliberate: the corpus was the bug.

**Replacement-character garbage is scrubbed from both passes.** It is not a defect in our
fetch: it is what an upstream cp1252-to-UTF-8 lossy mis-decode at authoring or CMS time leaves
behind, stored as the HTML entities `&#239;&#191;&#189;`, which decode to U+00EF U+00BF U+00BD,
the Latin-1 view of a UTF-8-encoded U+FFFD. The original characters (a curly apostrophe, an
accented name) were replaced by U+FFFD at the source and are unrecoverable, since U+FFFD carries
no information about what it replaced. All that can be done is strip it so it does not pollute
the knowledge base. Both the 3-character sequence and any bare U+FFFD go; neither occurs in
legitimate English content.

### PDF extraction

**pypdf, because it is pure Python** (no poppler, no system binary, no OCR) and it ships a
`py3-none-any` wheel, which is what the manylinux layer bundler in `infra/` needs: it runs pip
with `--only-binary=:all:`, so a dependency that has to compile would fail the build.
**pdfminer.six was the other pure-Python candidate and it was measured against this corpus
rather than assumed:** on the Writing Center's "Email Etiquette" handout it emits one character
per line down the page (`S\na\nn\n J\no\ns\ne`) while pypdf returns clean prose. Same file, same
call. That decided it.

Two things then stand between pypdf's output and a usable document.

**1. Repeated furniture.** Every page of a handout carries the same running header or footer
("Email Etiquette for Students, Fall 2013. Rev. Summer 2014  2 of 3"). Text is chunked at
FIXED_SIZE for retrieval with no idea where pages ended, so that line lands in the middle of
chunks as noise, several times per document. Lines that repeat across pages are dropped. Page
numbers are normalised away first, or "1 of 3" and "2 of 3" would look like different lines and
neither would ever be caught. The thresholds:

- **120 characters max.** Furniture is short by nature, and the cap is what stops a
  repeated-by-coincidence sentence of real content from being mistaken for it.
- **On at least 60% of the pages, and on at least two pages absolutely.** Both, so a two-page
  handout still gets its running header stripped while a line appearing on 2 of 16 slides does
  not.

Page structure is kept per page rather than flattened, because a line repeated **across** pages
is furniture while the same line twice on one page is just the document.

**2. Documents that are not text.** `extract_pdf` raises `ExtractionError` when the result is
not prose, and the PDF path is the only one that raises. A PDF is opaque in a way HTML is not:
an HTML page that extracts to nothing is visibly empty in the browser too, but a PDF that
extracts to nothing looks perfectly readable to the human who added it to the crawl list, being
a scan or text drawn as vector art. Silently ingesting that would put a titled, cited,
contentless page in the knowledge base, which retrieval can rank and the model can cite. So the
raise becomes an `ok=False` result counted in the run summary's failures, the corpus keeps the
last-good version of that URL, and a human sees the error. The thresholds are **200 letters
minimum** (a real handout runs to thousands; the fragments a scan yields run to tens) and **at
least 50% letters among non-space characters**, because a vector-art or badly-encoded PDF can
emit plenty of characters that are almost all punctuation and stray glyphs.

**A PDF's title is always None,** so `scrape_page` falls back to the crawl list's curated
title. That is a decision about what a student sees rather than an omission: the title is
rendered as the source attribution on an answer card, and this corpus's PDF metadata titles do
not survive that bar. Measured across the handouts on the list, seven of nine carry an empty
`/Title`, one carries the unedited template placeholder "Title of Handout", and the useful
remainder is a single file. The common failure mode elsewhere is worse still ("Microsoft Word -
handout_v2final.docx"), and a filename on a card is exactly what the curated title exists to
prevent.

## Failure handling

`scrape_page` never raises: failures become `ok=False` results and the run continues.

**An extraction failure logs at ERROR where a fetch failure logs at WARNING.** Unlike a 404 or
a timeout, an extraction failure does not resolve itself on the next run: the document will keep
extracting to nothing every day until someone removes it from the crawl list, so it should read
as a curation bug, which is what it is.

`response.text` honours the page's declared charset for the HTML branch; the PDF branch reads
`response.content`, which must stay bytes. Both are passed to `extract_document` rather than one
derived from the other, because each format needs its own: PDF is binary and must never be
decoded, and HTML's charset handling is not ours to reimplement.

## The run summary

One structured log line per run, so "how fresh is the assistant?" is a log query rather than a
guess. `duration_seconds` is there for the open question about the 15-minute timeout above.

## The CLI

`main()` and `write_result()` are the local-only concerns. Exit codes: 0 all pages succeeded,
1 the run completed but some pages failed, 2 the crawl list could not be loaded, which is the
same fatal condition the Lambda treats it as. `load_scraper_config` reads the `scraper:` block
from the repo-root `config.yaml`; the Lambda gets those values as environment variables from the
stack, because `config.yaml` is not in the function's asset bundle.

## What the suite pins

`scraper/tests/`. No live network and no AWS: `scrape_pages` and the S3 and Bedrock clients
are stubbed, and `boto3` is stubbed **before** `lambda_function` is imported, because the
handler imports it at module load.

The tests named for the two gates are the acceptance properties, and a few assert things the
test name cannot carry on its own:

- **`test_a_bad_crawl_list_fails_the_invocation_before_anything_is_deleted`** is the guarantee
  the whole tree is built around. A zero-page sweep would prune the entire knowledge base, so
  an unusable list has to abort the run rather than degrade it.
- **`test_a_failed_fetch_does_not_delete_that_page_from_the_knowledge_base`** is the point of
  keying the prune on the crawl list rather than on what the run fetched. Both pages are
  listed and one 404s; pruning by uploads would drop that page's document over one bad day.
  Its mirror, `test_a_page_removed_from_the_crawl_list_is_pruned`, is why the prune exists at
  all.
- **`test_an_unchanged_page_is_never_pruned_as_stale`** is the regression change gating
  introduces if you are not careful: an unchanged page uploads nothing, so a prune keyed on
  this run's uploads would delete the document it just confirmed. The live-key union is what
  stops it.
- **`test_content_fingerprint_ignores_the_scrape_timestamp`** is what keeps gate 1 from being
  an expensive no-op, and `test_a_second_consecutive_run_over_unchanged_content_reports_zero_changes`
  is the same property end to end.
- **`test_a_deferred_change_is_picked_up_by_the_next_run`** is the reason deferring behind a
  running job is safe: the run that defers changes nothing, so "did I upload?" would say no
  ingestion is needed and the content would sit unindexed. The bucket-newer-than-last-job rule
  is what catches it.
- **`test_the_sidecar_key_is_metadata_json_not_a_plain_json_document`** pins the naming that
  keeps Bedrock from ingesting the sidecar as its own document.
- **`test_the_run_summary_reports_configured_pages_and_duration`** is how a silently shrinking
  crawl list becomes visible, and the summary has to stay JSON-serialisable because it is
  emitted as one structured log line.

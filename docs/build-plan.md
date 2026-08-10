# Build plan (v1)

Each bullet is one commit. Gate before it lands: `cdk synth` clean, plus a few
`Template.from_stack` assertions on that section's key wiring. Synth alone is a
weak gate on an L1-heavy stack; the assertions catch wiring drift, not invalid
L1 property values. Nothing pre-flights those without an account.

## Planning

- [ ] pick the sjsu pages to crawl, confirming each scrapes as static html so the gav scraper works on them
- [ ] draft aws arch diagram: diagram-as-code plus generated png, committed in repo

## Copy over and morph

- [x] pull gav config skeleton and synth validators, renamed with no hardcoded global names
- [x] pull gav kb section, repointed at our source bucket and chunking values
- [x] pull gav scraper shell, crawling the curated url list from config on a single daily schedule (no tiers); sidecars carry `section` alongside source_url and title, or cards.py deprioritization and follow-ups degrade silently
- [x] pull gav lambda section: bare handler, not fastapi/mangum; keep pydantic in the deps layer, the camelCase wire contract lives in its aliases (the guardrail rides with it; handler is a validate-only stub until the next two bullets)
- [x] pull camp agent loop, tool schemas, system prompt and services as files; main.py and the routers are replaced (the loop also carries a wall-clock deadline, not just the iteration cap)
- [x] pull camp card parsing and the pre-model safety intercept as-is
- [x] ~~map crawl-list sections to card presets (app/section_presets.py, explicit entry per section, no fallthrough); an unretrieved sourceUrl loses its link~~
      SUPERSEDED by the card tag contract below. section_presets.py is deleted: the
      follow-up prompt is authored per card by the model, so a table keyed on
      `section` no longer has anything to answer. `section` still reaches the
      metadata sidecars and is still required of the crawl list - it is read at
      retrieval time - it just no longer picks button text.
- [x] pull gav api gateway: cognito gate on the billable route, our origins, our throttle numbers, route to /chat, Authorization in allow_headers (plus reserved concurrency: rate bounds invocations started, not how many run at once)
- [x] pull gav frontend s3 + cloudfront, stamping config.json with the api url; site domain joins the api cors allowlist as a deploy token (site content is a placeholder until bullet 10; camp's app is MULTI-PAGE, so directory-index rewriting, not an SPA fallback)
- [x] bundle astro in a container at synth (minimal placeholder app; dist/ never committed)
- [x] pull camp frontend ui as astro dist/, mock sidebar fixtures deleted; gav-style InitiateAuth with the token in memory, expiry checked before the fetch because an authorizer 401 carries no cors headers (/login and /auth/callback removed: both existed only for the Hosted UI OAuth redirect)
- [x] strip gav-specific surface: primo tools, catalog bucket, feedback path, dual
      hosting. Nothing to remove - none of the four were ever ported, because each
      section was pulled by hand rather than copied wholesale. Now pinned by
      test_no_gav_specific_resources_were_inherited so a later pull cannot
      reintroduce them, with the deliberately-inherited pieces (PROMPT_ATTACK
      guardrail, Cognito gate, config validators, seed-list layer) pinned alongside.

The api gateway section reopens when the frontend lands, to take the
distribution domain into its cors allowlist. Not frozen after its commit.

## Build ourselves

- [x] cards are parsed from model-emitted tags, not cut out of prose (docs/cards-v2.md).
      The model writes one text reply - prose plus `<card ref="N">` blocks - and cites
      sources by a per-turn integer id it was handed; the server resolves the id to a
      URL. `submit_chat_response` is gone, and so is the mechanical chunk-to-card
      builder that ran on every timeout. Caps live in config.yaml `cards` and reach both
      the parser and the prompt. BREAKING: the wire contract is unchanged, but every
      card's text now comes from the model, so a deploy of this without the matching
      prompt produces zero cards rather than wrong ones.
- [x] the cards carry the answer and the prose introduces them (docs/cards-v2.md).
      cards.desc_max_chars 140 -> 300, and the system prompt says where the substance
      goes: anything that sends a student somewhere, or tells them about a source we
      ingested, is a card with a real description, and the prose is a two-or-three-line
      intro pointing below. The cap stopped being a layout measurement when the
      four-line clamp went - it is an editorial budget now, still one config value
      reaching both the parser and the prompt. Whether the model actually shifts its
      weight is a question for real answers, not the unit suite.
- [x] card presentation: variable-height cards in a responsive grid under the prose,
      dealt in off a deck. The reveal button, the one-at-a-time stack and the 4-line
      clamp are gone, and the prose is never replaced (docs/cards-v2.md, Presentation).
      This is the rework the tag-contract commit deliberately deferred, and the clamp
      it removes is the box the entry above stopped deriving desc_max_chars from.
- [x] one reading rhythm across the turn: the deal is slower (0.1s -> 0.34s between
      cards, so four land in ~1.46s rather than 0.74s), card body text is 1rem rather
      than 0.875rem, and cards.desc_max_chars 300 -> 180 with the prompt re-steered to
      two sentences - the destination plus the one specific that matters. The three move
      together: a larger body at the old cap is a paragraph in a box, and dropping the
      cap without rewriting the prompt's examples truncates cards instead of shortening
      them. Still no clamp, no fixed height, no minimum height, and reduced motion still
      presents the grid directly.
- [x] a follow-up click is an ordinary user turn, so it can carry cards (docs/cards-v2.md,
      Tell me more). The card-suppression note is out of the user message and the matching
      guidance is out of the system prompt, including "do not repeat cards the student
      already has" - unenforceable, since history carries prose only, and it degraded into
      blanket avoidance. Retrieval guidance now turns on whether the answer needs a source
      rather than on the turn's position. `followup` stays on the wire contract with no
      backend reader.
- [x] cards size to their content instead of truncating (docs/cards-v2.md, Length caps).
      Real-model testing put an ellipsis on nearly every card: output length is a
      distribution, and a cap sized AT the two-sentence steer (180) turned ordinary
      variance into daily truncation. The per-field caps become runaway guards far above
      normal output - desc 180 -> 600, title 60 -> 90, follow-up 120 unchanged - each hit
      logged at WARNING, so an ellipsis now means a bug. Length steering stays in the
      prompt. An over-cap follow-up keeps its button and is sent whole (it displays as
      the student's turn and wraps there). A card past ~280 body chars spans its whole
      grid row so a short neighbour never floats on its dead space.
- [x] crawl list covers every office in the sponsor reference sheets (2026-08-10 coverage
      audit): +25 pages to 228. Campus safety (UPD, BIT, Alert SJSU, the Title IX landing page),
      five identity centers, ASPIRE/McNair/Office of Research, study abroad, supported
      instruction, IT, parking, the AS services, wellbeing, off-campus housing, the H&A success
      center (the advising hub lists 7 college centers, not the sheets' 9), and the
      enrollment-management directory. Four new sections: campus-safety, research, study-abroad,
      campus-services; nothing in app code switches on section values. Deliberately excluded:
      spartanrecreation.com, events.sjsu.edu, sjsuspartans.com, one.sjsu.edu (likely JS apps,
      off the three-host set) and the library CAT page (404 at every candidate URL). The sheets
      themselves carry errors - a dead CAPS intake URL, a dead /pridecenter link, conflicting
      CAPS phone / SJSU Cares location / pantry hours between sheets - which matters when they
      become eval ground truth.
- [x] scraper extraction recovers what trafilatura's article model drops (2026-08-10 audit of
      the LIVE corpus: 39 of 203 ingested docs carried any phone number, 52 any email; EOP's and
      AEC's contact info appeared in none). Both causes are static HTML - the JS hypothesis was
      tested and is false, so no browser automation. (1) The www.sjsu.edu CMS puts each office's
      phone/email/hours in a role="complementary" band OUTSIDE main, which an article extractor
      rightly reads as chrome; even contact-us.php pages keep their facts there. (2) Landing-page
      link-tile grids are pruned as link-dense boilerplate, and trafilatura's favor_recall /
      include_links measurably do not bring them back. Fix: a second lxml pass appends the band
      and any content-region block missing from the body, deduplicated on letters-and-digits
      normalization; <br> tails become spaces so "Phone: X<br>Monday" cannot glue into a
      corrupted fact. Verified against all 228 live pages: 0 failures, phones 39/203 -> 192/228,
      emails 52/203 -> 192/228. Every fingerprint changes, so the next deploy re-uploads and
      re-ingests the whole corpus - deliberate; the corpus was the bug.
- [x] ground-truth QA baseline (eval/ground-truth.yaml): 82 pairs known correct
      independently of the program - sponsor sheets first, every load-bearing fact verified
      against the live public pages on 2026-08-10, sheet-vs-sheet conflicts resolved by the
      live site and the sheets' errors recorded in the file header. Covers routing, factual,
      process and disambiguation questions plus safety-intercept cases (the safety panel
      must appear; a fluent reply without it is a FAIL) and five honest-gap probes for
      content deliberately outside the corpus. This is the fixture set the harness below
      consumes.
- [x] safety is the model's triage call, resolved server-side (2026-08-10). The pre-model
      phrase gate is gone: the system prompt carries the emergency instruction and a keyed
      resource roster (built from app/safety.py's table, the same table the resolver reads,
      so a taught key always resolves), the model emits <safety>key, key</safety>, and the
      server turns keys into the fixed contact panel - label, number and link are
      table-authored, never model-authored, the card-ref construction applied to crisis
      contacts. Unknown keys drop at WARNING; nothing valid resolves to the default crisis
      set; prose citing a hotline without the tag still gets the panel. Emergencies only:
      SJSU Cares and other office processes are ordinary routing answers, and the four
      ground-truth pairs that assumed intercepts for housing/family/intake are reclassified
      rag-answer. The roster adds SAS and UPD, so a survivor disclosure can resolve to the
      confidential advocate instead of a generic crisis set.
- [ ] adapt an eval harness from camp's 9-question cli and gav's harness (needs account)
- [x] ~~measure the real average character advance for Nunito Sans at 0.9375rem (the card
      TITLE size - the only text the estimate still bears on) in a
      browser and re-derive cards.title_max_chars; 60 comes from a 0.5em estimate
      (config.yaml carries the arithmetic), which is standard for a humanist sans but
      is not a measurement of this font. No longer bears on desc_max_chars, which is
      no longer derived from the box.~~
      SUPERSEDED by the card-sizing entry above: title_max_chars is a runaway guard now
      (90), not a one-line derivation, so there is no arithmetic left for a measured
      advance to feed.
- [ ] cap-violation rate as an eval metric: if the model overruns often, either the
      prompt or the cap is wrong, and the fixture run says which
- [ ] retune retrieve_min_score; 0.35 was tuned against a differently shaped corpus (needs account)
- [ ] update the team repo's audit docs, which still say mangum and fan-out scraper

## Open

- [ ] the chat lambda's 29s timeout is a ceiling, not a choice: an HTTP API
      integration's `timeoutInMillis` maxes at 30,000 ms and is not raisable by a
      quota request, so the synthesis item "raise timeout/memory above their
      30s/256MB" is half done on purpose (memory rose to 1024MB, the timeout
      cannot rise). If the agent loop does not fit in 29s the fix is
      architectural - streaming, or an async job with a poll - not a bigger
      number. Unmeasured without an account.
- [ ] content-hash stability is unmeasured on sjsu.edu: gav's corpus scraped
      byte-identical twice, ours has never been scraped at all (needs account).
      A per-render nonce or a rotating banner inside an extracted body would
      defeat upload gating for that page - visible as a `pages_changed` count
      that never drops to zero.

## Resolved (2026-08-05)

- crawl list transport: a SECOND Lambda layer carrying `url-list.csv`, with only
  the filename in `URL_LIST_FILE`. Forced, not preferred: Lambda caps all
  environment variables at 4 KB in aggregate (hard, not raisable), and the list
  is 19 KB as compact url/section JSON - 2.9 KB even gzipped and base64'd. Gav's
  `SCRAPER_TIERS` env transport does not survive the scale-up. A layer rather
  than the function bundle because the list sits at the repo root and
  `Code.from_asset` takes one directory.
- scraper cadence: DAILY, single schedule, no tiers. Cheap by calculation: 203
  pages scale gav's measured 19-pages-in-25-67s to ~4.5-12 min at 512MB
  (~$0.12/mo of Lambda), a full corpus re-embed is ~190K Titan tokens (<$0.01),
  and change gating means unchanged days pay only the Lambda run. Fits the
  15-min timeout, but the top of the range is close: log run duration from the
  first live run.
- scraper ceiling: 203 pages fits one Lambda (see above); revisit only if runs
  approach the cap.
- billing alarm: deferred to v2 (see aws-port-draft.md).

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
- [ ] adapt an eval harness from camp's 9-question cli and gav's harness (needs account)
- [ ] measure the real average character advance for Nunito Sans at 0.9375rem (the card
      TITLE size - the only text the estimate still bears on) in a
      browser and re-derive cards.title_max_chars; 60 comes from a 0.5em estimate
      (config.yaml carries the arithmetic), which is standard for a humanist sans but
      is not a measurement of this font. No longer bears on desc_max_chars, which is
      no longer derived from the box.
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

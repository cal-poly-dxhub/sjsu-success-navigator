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
- [ ] pull gav lambda section: bare handler, not fastapi/mangum; keep pydantic in the deps layer, the camelCase wire contract lives in its aliases
- [ ] pull camp agent loop, tool schemas, system prompt and services as files; main.py and the routers are replaced
- [ ] pull camp card parsing and the pre-model safety intercept as-is
- [ ] pull gav api gateway: cognito gate on the billable route, our origins, our throttle numbers, route to /chat, Authorization in allow_headers
- [ ] pull gav frontend s3 + cloudfront, stamping config.json with the api url; site domain joins the api cors allowlist as a deploy token
- [ ] pull camp frontend ui as astro dist/, spa fallback, mock sidebar fixtures deleted; gav-style InitiateAuth with the token in memory, expiry checked before the fetch because an authorizer 401 carries no cors headers
- [ ] strip gav-specific surface as each section lands: primo tools, catalog bucket, feedback path, dual hosting

The api gateway section reopens when the frontend lands, to take the
distribution domain into its cors allowlist. Not frozen after its commit.

## Build ourselves

- [ ] adapt an eval harness from camp's 9-question cli and gav's harness (needs account)
- [ ] retune retrieve_min_score; 0.35 was tuned against a differently shaped corpus (needs account)
- [ ] update the team repo's audit docs, which still say mangum and fan-out scraper

## Open

- [ ] astro build: bundle at deploy, or commit dist/
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

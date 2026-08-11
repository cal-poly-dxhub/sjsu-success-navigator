# Synthesis

## Make ourselves in new project

- curated crawl URL list (which SJSU pages matter; content decision, goes in config)
- billing alarm (gav lib has none)

## Pull from camp project

- converse python agent loop
    - tool schemas
    - parsing
        - response → cards
        - hardcoded matches → safety card
- frontend ui
- system prompt

## Pull from gav lib

All five infra items pull and rename. Sections are banner-delimited in one
~1,500-line stack class; lifting one means the section plus its config block
plus its IAM, plus renames. Pinned aws-cdk-lib==2.260.0, Bedrock L1 surface in
flux: validate against our pinned version.

- lambda hosting agent loop — `infra_stack.py:929-987`, IAM `:850-901`
    - bare handler, not FastAPI+Mangum: the camp services are framework-free
      Python and move as files; only main.py and the routers are replaced
    - keep pydantic in the deps layer (manylinux bundler `:105-134`); the
      camelCase wire contract with the frontend lives in its aliases
    - raise timeout/memory above their 30s/256MB
- kb: s3 vectors + embedding model — `infra_stack.py:210-414`
    - strongest match; metadata-key and index_arn traps already solved
    - names, chunking values, source bucket
- scraper + lambda — deps layer `:422-445`, schedules `:594-618`, install
  trigger `:648-660`, change-gating/prune logic in `scraper/lambda_function.py`
    - pulls whole on a curated URL list; recursion deferred to v2
- frontend s3 + cloudfront — `infra_stack.py:1260-1320`, token stamping `:1329-1389`
    - deploy Astro dist/, index.html root object, SPA fallback, stamp API URL
      into config.json
- api gateway backend — `infra_stack.py:1090-1157`
    - HttpApi, CORS allowlist rejecting wildcard at synth, CfnStage throttling
      escape hatch, native JWT authorizer
    - no WAF, deliberately: cannot attach to HTTP API v2
    - route rename to /chat, our origins, our throttle numbers
    - Cognito JWT gate on the billable route: ADOPT. Satisfies the requirement
      for auth to campus-affiliated users, and gates Bedrock spend. This
      replaces google oauth rather than removing it
- also pull: `GET /warm` pre-warm route; install trigger that makes first
  ingestion happen during `cdk deploy`

## Decisions (2026-08-05, resolving the diagram gaps)

Gav lib is the reference where the plan was silent; its values verified against
its config.yaml and stack.

- generation model: `us.anthropic.claude-sonnet-4-6` (gav's; camp used the
  same). Cross-region inference profile, so IAM needs profile ARN + underlying
  foundation-model ARNs.
- embedding model: `amazon.titan-embed-text-v2:0`, 1024-dim, cosine, float32
  (gav's exact vector-store shape).
- chunking: gav's FIXED_SIZE 600 tokens / 20% overlap as the starting baseline;
  retune with the eval once an account exists. Chunking is immutable, so a
  change is a data-source replacement (gav folds chunk config into the data
  source name; keep that trick).
- guardrail: ADOPT gav's single PROMPT_ATTACK input screen
  (ApplyGuardrail source=INPUT). Ordering is load-bearing: our deterministic
  safety intercept runs FIRST, then the guardrail, then the loop, so crisis
  handling can never be pre-empted by a guardrail block.
- scraper cadence: daily, single schedule, no tiers. Cost-checked in
  build-plan.md (~$0.15/mo all-in).
- billing alarm: v2.
- cognito: PER-USER accounts, signed in by redirect to managed login
  (authorization code + PKCE). SUPERSEDES the original "v1 ships gav's shared
  username/password pilot login; campus-affiliated accounts are v2" - brought
  forward because SJSU's IdP federates into this same pool, and a federated user
  cannot authenticate through InitiateAuth at all, so any form written for the
  shared login would be thrown away rather than extended. Okta is then
  config-only. Local accounts are admin-created scaffolding; self-signup stays
  off. A second, machine-only app client carries the eval harness.
- crawl list: url-list.csv is authoritative over the brief; three hosts
  (www / careercenter / library .sjsu.edu), 203 pages.

## Open

- Bedrock model-access opt-in state of the target AWS account. Check before the
  first deploy attempt, not during.
- Human-handoff destination for sensitive inquiries. Pending on Student
  Affairs' side; not answerable from any repo. The safety card points somewhere
  unconfirmed until then.

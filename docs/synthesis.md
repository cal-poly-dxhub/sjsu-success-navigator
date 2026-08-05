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

## Open

- Bedrock model-access opt-in state of the target AWS account. Check before the
  first deploy attempt, not during.
- Human-handoff destination for sensitive inquiries. Pending on Student
  Affairs' side; not answerable from any repo. The safety card points somewhere
  unconfirmed until then.

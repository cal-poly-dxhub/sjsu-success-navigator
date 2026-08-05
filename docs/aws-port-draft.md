# AWS port draft

**Quick AWS Port (v1):**

**Keep:**

- converse python agent loop
    - tool schemas
    - parsing
        - response → cards
        - hardcoded matches → safety card
- frontend ui
- system prompt

**Changes**:

- the cdk
    - lamnda on the agent loop
    - kb
        - s3 vectors
        - embedding model
    - custom scraper + lamda
    - s3 frontend + cloudfront
    - api gateway backend
- remove google oauth

**Improvements (v2):**

- moving the card system to parse bare output tags
    - normal cards only
    - NOT safety: safety stays a deterministic pre-model intercept. Requirements
      forbid AI-generated guidance on crisis, mental health, accessibility
      intake, housing instability, family emergency
- persistnace, logins
- eval harness against Student Affairs' 5-10 query test set (needs an account)
- recursive crawl (v1 ships a curated URL list)
- response streaming (HTTP API cannot stream; needs REST API or Function URL)
- billing alarm (v1 relies on stage throttling + the Cognito gate as cost caps)
- campus-affiliated Cognito accounts (v1 ships gav's single shared
  username/password pilot login)

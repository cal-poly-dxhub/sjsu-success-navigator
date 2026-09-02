# SJSU Student Success Navigator

## Index

| Section | Purpose |
|---------|---------|
| [Overview](#overview) | What this is and who it is for |
| [Description](#description) | Technology stack and repository structure |
| [Architecture](#architecture) | Diagram and the paths through a turn |
| [Deployment](#deployment) | Prerequisites, install steps, and issuing accounts |
| [Configuration Reference](#configuration-reference) | `config.yaml` settings |
| [Usage](#usage) | Asking a question, the eval harness, refreshing the corpus |
| [License](#license) | Project licensing details |
| [Collaboration](#collaboration) | Contact information for the team |
| [Disclaimers](#disclaimers) | Legal and usage disclaimers |

## Overview

A chat assistant that points enrolled San Jose State students at the campus office that can
actually help them. It answers from 238 curated sjsu.edu pages and academic-coaching handouts,
and every answer that sends a student somewhere arrives as a card carrying the destination, the
specifics that make it usable, and the page it came from. Built with Cal Poly DxHub.

It routes, it states facts it can cite, and it says plainly when the corpus has no page for
something rather than filling the gap. Three paths exist for when routing is not enough: a
student describing an emergency gets a fixed panel of crisis contacts the model cannot author,
a location question gets a map the project renders itself, and anything past that gets an email
draft addressed to SJSU Cares that the student sends from their own mail client.

## Description

**Tech stack overview** (AWS-native):

- **RAG:** Amazon Bedrock Knowledge Base (S3 data source) over an Amazon S3 Vectors store
  (Titan Text Embeddings v2, 1024-dim, cosine)
- **Generation:** Claude Sonnet 4.6 on Bedrock, in an agentic Converse tool-use loop with a
  wall-clock deadline and an iteration cap. Retrieval is primed server-side on every turn, so
  the common case is one model call and the `retrieve_campus_resources` tool is the escape
  hatch for a sharper second search
- **Cards:** the model writes its whole turn as text, citing sources by a per-turn integer id
  it was handed. It is never shown a URL, so a model-authored link is unrepresentable rather
  than validated and rejected
- **Safety:** the model triages and emits keys; the server resolves them into a fixed contact
  panel from `data/contacts.csv`. Label, number and link are never model-authored
- **Location:** a `<place>` key resolves to an address and a map picture rendered from
  OpenStreetMap tiles and served from our own distribution. No Maps API, no key, no
  third-party request
- **Streaming:** a FastAPI app under the Lambda Web Adapter streams the turn as NDJSON off a
  Function URL, served to the browser on a `/api/*` behaviour of the site's own distribution
- **Ingestion:** a scraper Lambda sweeps the curated URL list daily, HTML and PDF alike, with
  every upload and every ingestion job gated on a content fingerprint
- **Backend:** Lambda plus API Gateway HTTP API v2 (Python 3.13)
- **Auth:** one Cognito user pool, authorization code with PKCE through managed login. Okta
  federates into it behind a single config key
- **Storage:** one DynamoDB table, partitioned on the Cognito `sub`, so a request cannot name
  another student's partition
- **Guardrails:** one Bedrock Guardrail screening the input for prompt injection only
- **Infrastructure:** AWS CDK (Python)
- **Frontend:** Astro plus React, fifteen languages of chrome, and replies that follow the
  language the student wrote in

**Repository structure:**

- `infra/` CDK app (Knowledge Base, S3 Vectors store, scraper, Lambdas, HTTP API, Cognito,
  DynamoDB, CloudFront, IAM)
- `app/` the chat Lambda, the streaming FastAPI app, and the turn they share
- `scraper/` the ingestion Lambda and its fetch and extract core
- `eval/` the ground-truth baseline, the judgment-free runner, and the cost meter
- `frontend/` the Astro site and the chat UI
- `data/` every SJSU fact this repo states, as CSVs both languages read
- `scripts/` the OpenStreetMap map renderer
- `config.yaml` model, chunking, retrieval, card, rate-limit and guardrail settings
- `docs/` the install guide, the architecture diagram, the build plan, the eval harness and
  the system prompt

## Architecture

[`docs/architecture.drawio`](docs/architecture.drawio) is the source of truth and is edited by
hand. Two pages: a system overview, and the query path through one turn. Open it at
[draw.io](https://app.diagrams.net) or with the VS Code draw.io extension.

## Deployment

**[`docs/install.md`](docs/install.md) is the install guide**: model access, `cdk deploy`,
issuing the first account, signing in to check it, and connecting Okta afterwards.

**Prerequisites:** AWS credentials for a bootstrapped account and region, Bedrock model access
for Claude Sonnet 4.6, Claude Haiku 4.5 and Titan Text Embeddings v2, Python 3.13 (the Lambda
runtime), Node.js 22, the CDK CLI, and a running Docker daemon. Synth needs Docker: it bundles
the scraper's manylinux dependency layer and builds the Astro site in a container.

**Steps:**

```bash
cd infra
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt

cdk bootstrap
cdk synth          # offline apart from Docker, no AWS credentials needed
python -m pytest   # unit tests, no live AWS
cdk deploy         # needs credentials and a bootstrapped account
```

`cdk deploy` provisions everything: the knowledge base and vector store, the scraper and its
daily schedule, the chat and streaming functions, the HTTP API, the Cognito pool, the history
table, the guardrail, and the CloudFront-fronted site. The install trigger runs the first
ingestion during the deploy, so the corpus is indexed by the time it finishes. CloudFront takes
15 to 30 minutes to create and the same to destroy, so budget for it. Tear down with
`cdk destroy`; the history table is retained deliberately, because it holds the only copy of
what students said, so a destroy then redeploy collides on its name until the leftover table is
renamed or removed by hand.

**Issue accounts.** Self-signup is off and no identity provider ships, so every account is
created by an administrator: one command to create it, one to set its password. The stack
prints both as outputs and the install guide walks through them with worked examples.

**Other outputs worth keeping:** `SiteUrl` is the app. `ChatApiUrl` and `ConversationsApiUrl`
are the gated REST routes. `ChatWebClientId` and `ChatEvalClientId` are the pool's two app
clients. `StreamEdgeUrl` and `StreamProbeFunctionUrl` are the streaming endpoint reachable two
ways, which is what makes the edge latency measurable.

## Configuration Reference

Everything changeable lives in `config.yaml` at the repo root. The CDK app reads it at synth and
validates it there, so a bad value fails `cdk synth` rather than the deploy. Key sections:

- **`knowledge_base` / `vector_store` / `chunking`** embedding model, index dimensions and
  distance metric, and how documents are split. Chunking is immutable in Bedrock, so changing
  it replaces the data source
- **`scraper`** the daily cron, the crawl list filename, and the fetch timeout
- **`retrieval` / `generation`** passages per retrieval, the answering and titling models, and
  the token caps
- **`cards`** the card ceiling and the three per-field length guards, which reach both the
  parser and the system prompt from this one place
- **`escalation`** which `data/contacts.csv` row the email draft is addressed to, its subject,
  and the cap that drops the offer rather than truncating it. Blank the contact to take the
  feature off
- **`rate_limit`** the per-user daily message cap, the first control that bounds what one
  account can spend. 0 turns it off
- **`chat`** the loop's iteration cap and deadlines, the history window the model is shown,
  and the two browser read caps
- **`chat_history`** the table name, which is global within an account and region
- **`okta`** the SAML metadata URL and the email attribute name
- **`http_api` / `cors`** throttling, reserved concurrency, and the exact-match origin
  allowlist that rejects a wildcard at synth
- **`guardrail`** the prompt-injection input screen and its blocked-input message
- **`cost_model`** published AWS rates and the measured per-question constants behind the cost
  panel

The SJSU facts themselves are not in here. Offices, buildings, contacts, abbreviations and the
crawl list are CSVs in `data/`, written for somebody with a spreadsheet and no Python; see
[`data/README.md`](data/README.md).

## Usage

**Asking a question.** Open `SiteUrl`, sign in through managed login, and type. Replies stream
as they are written. Conversations are listed in the sidebar, can be renamed or deleted, and
reopen re-rendered under today's rules rather than as a stored snapshot.

**Refreshing the corpus.** The scraper runs itself daily. To add or remove pages, edit
`data/urls.csv` and deploy: the list is validated at synth, and a page removed from it is
pruned from the knowledge base on the next run. A run that cannot read the list fails before
it deletes anything.

**Evaluation.** `eval/` holds 84 ground-truth pairs verified against the live SJSU pages, a
runner that collects the deployed system's answers without scoring them, a renderer that puts
the golden expectation beside the real answer for a human to judge, and the cost meter that
produces `config.yaml`'s measured block. All of it runs against the deployed stack and costs
real money per run; none of it is in CI. See
[`docs/eval-harness.md`](docs/eval-harness.md) and [`eval/README.md`](eval/README.md).

**Editing the system prompt.** `app/prompts.py` is the prompt and
[`docs/system-prompt.md`](docs/system-prompt.md) is why each section reads the way it does,
including which wordings were chosen by measurement. Every numeral in it is interpolated from
settings, so the cap the model is told is the cap the server applies.

## License

MIT. See [LICENSE](LICENSE).

## Collaboration

Thanks for your interest in our solution. Having specific examples of replication and cloning
allows us to continue to grow and scale our work. If you clone or download this repository, kindly
shoot us a quick email to let us know you are interested in this work!

[wwps-cic@amazon.com]

## Disclaimers

Customers are responsible for making their own independent assessment of the information in this document.

This document:

(a) is for informational purposes only,

(b) references AWS product offerings and practices, which are subject to change without notice,

(c) does not create any commitments or assurances from AWS and its affiliates, suppliers or licensors. AWS products or services are provided "as is" without warranties, representations, or conditions of any kind, whether express or implied. The responsibilities and liabilities of AWS to its customers are controlled by AWS agreements, and this document is not part of, nor does it modify, any agreement between AWS and its customers, and

(d) is not to be considered a recommendation or viewpoint of AWS.

Additionally, you are solely responsible for testing, security and optimizing all code and assets on GitHub repo, and all such code and assets should be considered:

(a) as-is and without warranties or representations of any kind,

(b) not suitable for production environments, or on production or other critical data, and

(c) to include shortcuts in order to support rapid prototyping such as, but not limited to, relaxed authentication and authorization and a lack of strict adherence to security best practices.

All work produced is open source. More information can be found in the GitHub repo.

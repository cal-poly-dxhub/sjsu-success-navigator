"""SJSU Student Success Navigator - v1 AWS architecture diagram (plan).

Source of truth: this .py file. The PNG next to it (architecture-v1.png) is the
generated artifact - regenerate it after editing this file:

    python3 docs/architecture-v1.py

Requires Graphviz (`brew install graphviz`) and the `diagrams` library
(`pip install diagrams`).

This diagram is drawn BEFORE the stack code exists. Every node and edge traces to
the plan documents (docs/build-plan.md, docs/synthesis.md, docs/aws-port-draft.md,
url-list.csv at the repo root); nothing here is inferred from code, because there
is no code yet. It exists to make the cross-project wiring reviewable: v1 is
assembled by pulling sections out of the gav lib CDK project and the camp
application, so the risk sits in the seams, not the components.

Decisions reflected:
  - v1 is authenticated. Cognito with a single InitiateAuth call replaces Google
    OAuth; a native JWT authorizer guards POST /chat, the one billable route.
    GET /warm stays ungated (it spends no Bedrock tokens).
  - The chat Lambda is a bare handler - no FastAPI, no Mangum. The camp services
    (safety intercept, Converse agent loop, card parsing) move in as files;
    pydantic aliases carry the camelCase wire contract.
  - Ingestion scrapes a curated URL list from config (203 pages in url-list.csv,
    hosts www/careercenter/library.sjsu.edu), no recursive crawl. Metadata
    sidecars carry section alongside source_url and title.
  - No response streaming in v1: one JSON response per question.
  - Frontend is static Astro on S3 behind CloudFront (OAC). CDK bundles it in a
    container at synth; config.json is stamped with the API URL at deploy time,
    and the distribution domain joins the API's CORS allowlist.

GAPS - ambiguities the plan documents do not resolve. Where one of these blocks
an edge or a label, the diagram carries "undecided" rather than a guess:
  1. Generation model ID is pinned nowhere in this repo's docs (camp defaulted to
     us.anthropic.claude-sonnet-4-6; no decision is recorded here). Drawn generic.
  2. Embedding model ID and vector dimension are unnamed ("kb: s3 vectors +
     embedding model"), and chunking values are "ours" but unchosen. Drawn generic.
  3. Guardrail: the plan is silent on whether gav lib's PROMPT_ATTACK input
     guardrail comes along with the pulled lambda section or is stripped. Not
     drawn; needs a decision (gav's lambda IAM carries ApplyGuardrail).
  4. Scraper schedule shape: gav has fast/full tiers; our docs pull "schedules"
     with no cadence or tier decision for the 203-URL list. One schedule drawn,
     cadence unlabeled.
  5. Billing alarm wiring: mechanism (AWS Budgets vs CloudWatch) and threshold
     undecided. Node drawn with no edges.
  6. Cognito account model: synthesis says "auth to campus-affiliated users";
     gav's template is one shared demo login. Provisioning undecided; the pool is
     drawn, the account model is not.
  7. Domain count: the task brief says two domains; url-list.csv contains three
     hosts (www.sjsu.edu 177, careercenter.sjsu.edu 21, library.sjsu.edu 5).
     Drawn from the CSV.
"""

import os

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.cost import Budgets
from diagrams.aws.general import InternetAlt1, Users
from diagrams.aws.integration import Eventbridge
from diagrams.aws.ml import Bedrock
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.security import Cognito
from diagrams.aws.storage import S3

# Write the PNG next to this file (docs/) regardless of the current working directory.
_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "architecture-v1")

graph_attr = {"fontsize": "20", "labelloc": "t", "pad": "0.5", "nodesep": "0.8", "ranksep": "1.1"}

with Diagram(
    "SJSU Student Success Navigator - v1 AWS Architecture (plan)",
    filename=_OUTPUT,
    outformat="png",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
):
    # External SJSU pages the scraper pulls from - the curated list in url-list.csv.
    sjsu_sites = InternetAlt1(
        "SJSU websites\n(curated URL list, 203 pages:\nwww / careercenter / library\n.sjsu.edu)"
    )

    with Cluster("Ingestion  (scheduled + on-deploy install trigger)"):
        schedule = Eventbridge("EventBridge schedule\n(cadence undecided)")
        scraper = Lambda("Scraper Lambda\ncurated list from config\nchange gating + prune")
        source_bucket = S3(
            "KB source bucket\nmarkdown + metadata sidecars\n(section, source_url, title)"
        )

    # The KB + vector store are the hub shared by both flows.
    with Cluster("Shared RAG core  (Bedrock KB + S3 Vectors)"):
        kb = Bedrock("Bedrock Knowledge Base\nS3 data source\n(chunking values undecided)")
        embed = Bedrock("Embedding model\n(ID undecided - see gaps)")
        vectors = S3("S3 Vectors\nbucket + index")

    with Cluster("Auth  (pilot gate on the billable route)"):
        cognito = Cognito("Cognito user pool\n+ public app client\n(account model undecided)")

    with Cluster("Query path  (per request, runtime)"):
        student = Users("Student\n(browser, Astro UI)")
        api = APIGateway(
            "HTTP API v2\nPOST /chat + GET /warm\nCORS allowlist, stage throttling"
        )
        chat_fn = Lambda(
            "Chat Lambda (bare handler)\nsafety intercept -> Converse\nagent loop -> cards\npydantic camelCase contract"
        )
        model = Bedrock("Bedrock Converse\ngeneration model\n(ID undecided - see gaps)")

    with Cluster("Site delivery  (S3 + CloudFront, OAC origin)"):
        cdn = CloudFront(
            "CloudFront distribution\n(domain joins the API\nCORS allowlist at deploy)"
        )
        site_bucket = S3(
            "Private site bucket\nAstro dist/ (container-bundled\nat synth) + config.json\n(API URL stamped at deploy)"
        )

    with Cluster(
        "Build ourselves  (wiring undecided)",
        graph_attr={"style": "dashed", "bgcolor": "gray95"},
    ):
        billing = Budgets("Billing alarm\n(mechanism + threshold\nundecided - no edges drawn)")

    # --- Ingestion flow: schedule -> scrape -> S3 markdown -> KB ingest -> embed -> vectors ---
    schedule >> Edge(color="darkorange", label="scheduled invoke\n(+ install trigger on deploy)") >> scraper
    sjsu_sites >> Edge(color="darkorange", label="fetch pages\n(static HTML)") >> scraper
    scraper >> Edge(color="darkorange", label="upload markdown\n+ sidecars (change-gated)") >> source_bucket
    scraper >> Edge(color="darkorange", style="dashed", label="StartIngestionJob\n(only when content changed)") >> kb
    source_bucket >> Edge(color="darkorange", label="S3 data source\ningest") >> kb
    kb >> Edge(style="dashed", label="embed chunks") >> embed
    kb >> Edge(color="darkorange", label="write vectors") >> vectors

    # --- Site delivery: how the Astro app reaches the browser ---
    student >> Edge(color="purple", label="GET site (HTTPS)") >> cdn
    cdn >> Edge(color="purple", style="dashed", label="origin fetch\n(OAC, private)") >> site_bucket
    cdn >> Edge(color="purple", style="dotted", label="serves Astro app\n+ config.json (API URL)") >> student

    # --- Auth: one InitiateAuth call, token held in memory ---
    student >> Edge(
        color="crimson", label="InitiateAuth (one call)\n-> JWT, held in memory,\nexpiry checked before fetch"
    ) >> cognito

    # --- Query flow. The crimson hop is the ONLY one that requires a valid JWT. ---
    student >> Edge(
        color="crimson", penwidth="2.0", label="POST /chat\nAuthorization: Bearer JWT\nREQUIRED (401 without it)"
    ) >> api
    student >> Edge(color="darkblue", style="dotted", label="GET /warm\n(ungated, no JWT)") >> api
    api >> Edge(color="gray", style="dashed", label="JWT authorizer\nvalidates via pool JWKS") >> cognito
    api >> Edge(color="darkblue", label="proxy\n(payload 2.0)") >> chat_fn
    chat_fn >> Edge(color="darkblue", label="retrieve_campus_resources\n(KB Retrieve)") >> kb
    kb >> Edge(color="darkblue", style="dashed", label="read vectors") >> vectors
    chat_fn >> Edge(style="dashed", dir="both", label="Converse\ntool-use loop") >> model

    # --- Response: one JSON body, no streaming in v1 ---
    chat_fn >> Edge(
        color="darkgreen", style="dotted",
        label="{conversationalText, cards | safety}\nsingle JSON response - no streaming (v1)",
    ) >> student

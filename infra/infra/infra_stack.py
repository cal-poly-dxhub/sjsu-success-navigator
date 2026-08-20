"""SJSU Student Success Navigator infrastructure stack.

Each "Copy over and morph" bullet in docs/build-plan.md lands its gav section into the
banner below that names it, in this order (mirroring gav's own section order so a pull is
a copy + rename, not a re-architecture):

  1. Vector store + Knowledge Base + S3 data source   ("pull gav kb section")      DONE
  2. Scraper Lambda + deps layer + daily schedule
     + on-deploy install trigger                      ("pull gav scraper shell")   DONE
  3. Bedrock Guardrail (PROMPT_ATTACK input screen)   (docs/synthesis.md decision)
  4. Chat Lambda + role + deps layer                  ("pull gav lambda section")
  5. Cognito pool + managed login domain + two app
     clients + JWT authorizer
     + HTTP API + routes + throttling                 ("pull gav api gateway")
  6. Site bucket + CloudFront (OAC) + Astro deploy
     + config.json stamping                           ("pull gav frontend s3 + cloudfront")

One section is NOT in that list and is deliberately unnumbered: the chat history table,
which sits just before section 4 because the chat Lambda's role and environment reference
it. It is not a gav pull - it is the first slice of per-user accounts and chat history
(docs/accounts-and-storage.md) - so numbering it would put it in a sequence that means
"gav's section order", which it has no place in.

All changeable knobs come from the repo-root config.yaml (see infra/config.py), which
also validates the file at synth - the L1 Cfn* constructs used below do not check any
property constraints themselves.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import jsii
from aws_cdk import (
    AssetHashType,
    BundlingOptions,
    CfnOutput,
    DockerImage,
    DockerVolume,
    Duration,
    Fn,
    ILocalBundling,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_authorizers as apigwv2_authorizers,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_bedrock as bedrock,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cognito as cognito,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_s3vectors as s3vectors,
    triggers,
)
from constructs import Construct

from infra.config import (
    CHAT_LAMBDA_TIMEOUT_SECONDS,
    resolve_cards,
    resolve_chat,
    resolve_chat_history,
    resolve_chunking,
    resolve_cost_model,
    resolve_data_source_name,
    resolve_escalation,
    resolve_generation,
    resolve_cors_allow_origins,
    resolve_guardrail,
    resolve_http_api,
    resolve_knowledge_base,
    resolve_okta,
    resolve_rate_limit,
    resolve_request,
    resolve_retrieval,
    resolve_scraper,
    resolve_seed_pages,
    resolve_streaming,
    resolve_vector_store,
    validate_config,
)

# Repo root. infra_stack.py is <repo>/infra/infra/infra_stack.py, so parents[2] is the root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
# The scraper Lambda's source (scraper.py + lambda_function.py) and its requirements.txt (the
# deps built into the layer below).
_SCRAPER_DIR = _REPO_ROOT / "scraper"
# The chat Lambda's source: the bare handler plus camp's service modules, moved in as files.
# No FastAPI and no Mangum (docs/build-plan.md) - camp's main.py and routers are replaced by
# handler.py, and everything else in this directory is framework-free Python already.
_APP_DIR = _REPO_ROOT / "app"
# The Astro app. Built in a container at synth (see _astro_bundling); dist/ is gitignored
# and produced by the build, never committed.
_FRONTEND_DIR = _REPO_ROOT / "frontend"
# EVERY SJSU FACT THE APP STATES, as CSV a non-engineer can open in Excel: the crawl list, the
# campus places and their buildings, the safety/cares/escalation contacts, and the campus
# shorthand glossary. It sits OUTSIDE app/ and outside frontend/ on purpose - both read it, and
# a fact spelled once in Python and again in TypeScript has no test that can see both copies.
#
# It therefore reaches the deployment TWICE, by two different routes, because `Code.from_asset`
# and `Source.asset` each take exactly one directory: as the Lambda layer built below (extracted
# to /opt, where app/campus_data.py and scraper/lambda_function.py look), and as a read-only
# mount into the Astro build container (see _astro_bundling). data/README.md is the file
# somebody editing a phone number is meant to open.
_DATA_DIR = _REPO_ROOT / "data"

# The Node image the site is built in. Pinned to a MAJOR version rather than `latest` so a
# synth six months from now builds the same way, and matched to the Node this repo develops
# against. Alpine keeps the pull small; Astro needs no native toolchain.
_NODE_BUILD_IMAGE = "public.ecr.aws/docker/library/node:22-alpine"

# Both Lambdas' architecture and the MATCHING manylinux wheel tag. trafilatura pulls in lxml
# and regex, pydantic pulls in pydantic-core - all compiled extensions - so the layers must
# contain Linux (x86_64) wheels, not the macOS wheels a plain `pip install` produces on a dev
# Mac. Keep these two in lockstep.
_LAMBDA_PYTHON = _lambda.Runtime.PYTHON_3_13
_LAMBDA_ARCH = _lambda.Architecture.X86_64
_MANYLINUX_TAG = "manylinux2014_x86_64"
_LAMBDA_PY_TAG = "3.13"


# THE LAMBDA WEB ADAPTER LAYER, published by AWS, and the whole reason a python3.13
# function can stream a response body at all (app/stream_probe.py, and the probe section
# near the end of this file). The layer holds two things: the adapter binary, registered as
# a Lambda extension, and /opt/bootstrap, the wrapper script that starts the app.
#
# THE PUBLISHER ACCOUNT IS AWS'S AND IS THE SAME IN EVERY COMMERCIAL REGION - it is a
# published constant from the project's README, not a name this deployment owns, so it does
# not belong in config.yaml with the names that are ours.
_LWA_LAYER_ACCOUNT = "753240598075"

# THE LAYER NAME CARRIES THE ARCHITECTURE, so it is DERIVED from _LAMBDA_ARCH rather than
# typed beside it. AWS publishes one layer per architecture under two different names, and
# attaching the x86_64 adapter to an arm64 function is a deploy that succeeds and an
# invocation that fails on an Exec format error. Flipping _LAMBDA_ARCH above now flips this
# with it, and an architecture nobody has published a layer for raises KeyError at synth
# instead of shipping a mismatch.
_LWA_LAYER_NAME = {
    _lambda.Architecture.X86_64.name: "LambdaAdapterLayerX86",
    _lambda.Architecture.ARM_64.name: "LambdaAdapterLayerArm64",
}[_LAMBDA_ARCH.name]

# PINNED TO 28, which is adapter 1.0.1 - the version the project's README and its
# fastapi-response-streaming-zip example both name, and the newest one with a GitHub
# release and a CHANGELOG entry behind it. Layer 29 exists and calls itself 1.1.0, but
# there is no 1.1.0 release, no changelog entry and no doc pointing at it (probed
# 2026-08-19 with lambda:GetLayerVersion, which is the only call the layer's public policy
# allows; version 30 does not exist). An undocumented build is a bad thing to be running
# when a probe fails and the question is whether the mechanism or the wiring is at fault.
_LWA_LAYER_VERSION = 28

# The port the probe's uvicorn binds and the adapter forwards to. Spelled once and handed
# to both through a single PORT environment variable - two numbers is how a function ends
# up timing out against a readiness check on a port nothing is listening to.
_LWA_PROBE_PORT = 8000

# The eval harness's MACHINE account username, spelled into the setup commands the stack
# prints so they are copy-paste runnable. It is a NAME, not a credential - the password is
# chosen by the deployer and never appears here or anywhere in the repo.
#
# This is not the retired shared login wearing a new name. That one was the single
# credential every human used; humans now each get their own account and reach it through
# the managed-login redirect, which cannot issue a token to this client at all. This
# account exists because eval/run_eval.py runs headless with no browser to redirect, so it
# needs the one identity that the password-auth client will serve. Keep it to the harness.
_EVAL_USERNAME = "eval-runner"

# Placeholder standing in for a real person's account in the printed admin-create-user
# command. Deliberately not a valid username shape anyone would leave in place.
_HUMAN_USERNAME_PLACEHOLDER = "USERNAME-HERE"

# The WebSocket streaming API's stage name. It is part of the URL the browser connects to
# (wss://<apiId>.execute-api.<region>.amazonaws.com/<stage>), so it is spelled once here
# rather than in config.yaml: it names nothing globally - it is scoped to an API that does
# not exist until this stack creates it - and there is no deployment that wants a different
# one. A config knob would be a value nobody has a reason to change and a second place the
# frontend's URL could come from.
_STREAMING_STAGE_NAME = "stream"


def _astro_bundling() -> BundlingOptions:
    """Build frontend/ into static files INSIDE A CONTAINER, at synth.

    A container rather than the local toolchain, deliberately: the build then depends on
    the pinned image and the committed lockfile instead of whatever Node happens to be on
    the machine running `cdk deploy`. There is no local-bundling fallback here (unlike the
    Lambda layers, where one exists because pip can cross-build with --platform): a
    silently-different local build is exactly what pinning the image is for, and a missing
    Docker should fail loudly rather than produce a different site.

    `npm ci`, not `npm install`: ci installs exactly the lockfile and fails if package.json
    and the lockfile disagree, so the site cannot drift between deploys.

    node_modules and dist are excluded from what is copied INTO the container - both are
    build products of the host, and copying them would both slow the asset staging and
    risk a stale dist/ shadowing the fresh build.
    """
    return BundlingOptions(
        image=DockerImage.from_registry(_NODE_BUILD_IMAGE),
        # THE REPO-ROOT data/ DIRECTORY, MOUNTED READ-ONLY. The site's facts - the SJSU Cares
        # address, its phone number, its mailbox - are CSV rows there, and the frontend build
        # compiles them into the bundle (frontend/scripts/generate-campus-data.mjs). They
        # cannot come in through /asset-input: bundling bind-mounts ONE directory, this
        # asset's is frontend/, and data/ is deliberately outside it because Python reads the
        # same rows. So it arrives as a second mount.
        #
        # READ-ONLY is not decoration. CDK mounts /asset-input read-WRITE and that has already
        # cost this repo a broken local node_modules (see the copy below); data/ is the one
        # copy of these facts, so nothing in a container gets to write to it.
        #
        # The asset hash is unaffected by this mount and does not need to be: bundling hashes
        # the OUTPUT, so a changed CSV changes the built site and therefore the hash.
        volumes=[
            DockerVolume(
                host_path=str(_DATA_DIR), container_path="/asset-data"
            )
        ],
        # PINNED, like the Lambda layers: without it the image resolves to the host's
        # architecture, so an arm64 Mac and an x64 CI runner build the site with different
        # native toolchains. The committed lockfile carries every platform's rolldown
        # binding (npm records them all when the lock is generated with
        # --package-lock-only), so `npm ci` resolves on either - but the OUTPUT should not
        # depend on who ran the deploy.
        platform="linux/amd64",
        # OUTPUT hashing: the asset hash tracks the BUILT site, so an edit that does not
        # change the output (a comment in a source file) does not churn the deployment.
        command=[
            "sh",
            "-c",
            # THREE env vars, each fixing a real failure rather than being defensive:
            #   HOME=/tmp  - CDK runs the container as the HOST uid, which has no home
            #                inside the image, so anything writing under $HOME fails.
            #   ASTRO_TELEMETRY_DISABLED - Astro's telemetry writes to $HOME/.config on
            #                first run; it died with EACCES on /.config/astro before HOME
            #                was set. Off is also simply correct for a build container:
            #                no phoning home from a deploy machine.
            #   npm_config_cache - same writability problem, npm's cache defaults to
            #                $HOME/.npm.
            "export HOME=/tmp ASTRO_TELEMETRY_DISABLED=1 npm_config_cache=/tmp/.npm && "
            # BUILD IN A CONTAINER-LOCAL COPY, never in /asset-input. CDK bind-mounts the
            # real frontend/ directory READ-WRITE, so an `npm ci` run here replaces the
            # developer's node_modules with the container's linux/amd64 binaries and
            # breaks their next local build (observed, not theoretical). Copying the
            # inputs out first also keeps the host's node_modules and dist/ from leaking
            # into the build, so what is produced depends only on the committed sources
            # and the lockfile.
            "mkdir -p /tmp/build && cd /asset-input && "
            "cp -R package.json package-lock.json astro.config.mjs tsconfig.json "
            "scripts src public /tmp/build/ && "
            # THE FACTS, from the READ-ONLY MOUNT below, into the place the generator looks
            # for them: `npm run build` runs frontend/scripts/generate-campus-data.mjs first,
            # and that script resolves data/ as a sibling of the frontend directory - which is
            # true in a checkout, in CI, and here. Copied rather than read from the mount so
            # the build depends on nothing outside /tmp/build once it starts.
            "cp -R /asset-data /tmp/data && "
            # DROP THE DEVELOPER'S LOCAL config.json. To run `astro dev` against a deployed
            # API you hand-write frontend/public/config.json with the four runtime keys, and
            # public/ ships verbatim - so without this line that file is built into dist/ and
            # staged at the SAME bucket key the SiteConfigDeployment stamps at deploy. Today
            # the site deployment's `exclude=["config.json"]` happens to keep it from being
            # uploaded, but that exclude exists to scope the PRUNE; the moment it is narrowed
            # or the two deployments are merged, a developer's private endpoint is published
            # over the real one and the site silently points students at the wrong API.
            #
            # It is removed from the container-local COPY, not from the mounted input: CDK
            # bind-mounts frontend/ read-write, so deleting under /asset-input would delete
            # the developer's own file (the same trap the copy above exists for).
            #
            # NOT expressible as a Source.asset `exclude` entry, which is the obvious-looking
            # place for it: that list filters the asset FINGERPRINT, not what the container
            # sees, because bundling bind-mounts the whole source directory. Verified - with
            # "public/config.json" added to the exclude below, the file still lands in the
            # staged asset; only the asset hash changes.
            #
            # `rm -f` never fails on an absent file, so a developer without one synths and
            # deploys exactly as before.
            "rm -f /tmp/build/public/config.json && cd /tmp/build && "
            "npm ci --no-audit --no-fund && "
            "npm run build && "
            "cp -R dist/. /asset-output/",
        ],
    )


def _requirements_hash(layer: str, *requirements: Path) -> str:
    """A stable asset hash for a deps layer, keyed on EVERY requirements file it installs.

    Exists to keep the deps layers from colliding in CDK's asset cache - see the long note
    on the chat layer. The layer name is folded in as well as the file contents, so two
    layers would stay distinct even if their requirements were byte-identical.

    VARIADIC because one of them installs two files: the streaming app's layer pulls in
    app/requirements.txt through a `-r` line so the app's own runtime pin is not spelled a
    second time, and a hash that only saw the outer file would leave a changed pydantic pin
    staged as the previous build. Hashing the concatenation means a single-file layer's
    digest is byte-identical to what this returned before, so no existing asset churns.
    """
    digest = hashlib.sha256(
        b"".join(path.read_bytes() for path in requirements)
    ).hexdigest()[:32]
    return f"{layer}-{digest}"


def _config_hash(payload: Dict[str, Any]) -> str:
    """A short, stable content hash of a resolved config block.

    Used where a CloudFormation resource has no property that changes when config.yaml does -
    specifically CfnGuardrailVersion, which would otherwise never publish a new version after
    a guardrail edit. sort_keys makes it order-independent, so re-arranging config.yaml without
    changing a value does not churn the resource.
    """
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]


@jsii.implements(ILocalBundling)
class _PipManylinuxLayerBundler:
    """Builds one requirements.txt into a Lambda layer using prebuilt manylinux wheels via
    `pip --platform ... --only-binary=:all:` - NO Docker, NO compiler. This is AWS's documented
    method for compiled deps: --platform + --only-binary forces pip to download the Linux wheel
    matching the Lambda architecture instead of building a macOS binary that would fail at
    runtime with an ELF/Mach-O error.

    Both deps layers use this. The scraper's trafilatura pulls in lxml and regex; the chat
    Lambda's pydantic pulls in pydantic-core, a compiled Rust extension - same trap, same fix
    (verified: the wheel lands as _pydantic_core.cpython-313-x86_64-linux-gnu.so).

    `requirements` is a constructor argument rather than a module constant because the two
    layers build different files; a shared hardcoded path would have quietly built the
    scraper's deps into the chat layer.

    Returns False on any failure so CDK falls back to the BundlingOptions Docker `image`/`command`
    (the required fallback if a transitive dep ever lacks a manylinux wheel).
    """

    def __init__(self, requirements: Path) -> None:
        self._requirements = requirements

    def try_bundle(self, output_dir: str, *, image=None, **_kwargs) -> bool:
        try:
            subprocess.run(
                [
                    sys.executable, "-m", "pip", "install",
                    "-r", str(self._requirements),
                    "--platform", _MANYLINUX_TAG,
                    "--python-version", _LAMBDA_PY_TAG,
                    "--implementation", "cp",
                    "--only-binary=:all:",
                    "--target", str(Path(output_dir) / "python"),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            return True
        except Exception:  # no local pip / missing wheel -> let CDK try Docker bundling
            return False


class NavigatorStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Dict[str, Any],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Validate the WHOLE config before creating anything, including blocks whose
        # sections are not built yet. L1 Cfn* constructs do not check property constraints
        # at synth, so without this the first sign of a bad value is a failed deploy - or,
        # for an immutable property, a replacement of a resource that already holds data.
        validate_config(config)

        # Held for the sections below.
        self._config = config

        # Resolved knobs. Every name below comes out of these - no global name is spelled
        # in this file (see the naming convention in infra/config.py).
        kb_cfg = resolve_knowledge_base(config)
        vs_cfg = resolve_vector_store(config)
        chunking_cfg = resolve_chunking(config)
        guardrail_cfg = resolve_guardrail(config)
        generation_cfg = resolve_generation(config)
        retrieval_cfg = resolve_retrieval(config)
        request_cfg = resolve_request(config)
        chat_cfg = resolve_chat(config)
        chat_history_cfg = resolve_chat_history(config)
        cards_cfg = resolve_cards(config)
        # None when no escalation recipient is configured, and the absence then reaches
        # THREE places from this one value: no ESCALATION_* variables on the chat function,
        # no escalationRecipient in config.json, and - because the function reads the same
        # absence - no mention of the tag in the system prompt.
        escalation_cfg = resolve_escalation(config)
        # None when the per-user daily cap is off. The env var below is then omitted rather
        # than set to zero, which is the same gate the cost panel uses on config.json.
        daily_message_limit = resolve_rate_limit(config)
        cors_allow_origins = resolve_cors_allow_origins(config)

        # The embedding model, region-scoped. Titan v2 at 1024 dimensions is inherited from
        # gav, not chosen against this corpus (docs/synthesis.md). Region and partition are
        # Stack tokens, so nothing here pins an account or a region.
        embedding_model_arn = (
            f"arn:{self.partition}:bedrock:{self.region}"
            f"::foundation-model/{kb_cfg['embedding_model_id']}"
        )

        # --- 1. Vector store + Knowledge Base + S3 data source -------------------
        #
        # Amazon S3 Vectors as the KB vector store rather than OpenSearch Serverless:
        # near-zero cost, no cluster / VPC / FGAC / security policies. SEMANTIC-SEARCH-ONLY
        # (no hybrid) - the accepted cost/simplicity tradeoff. Encryption defaults to SSE-S3
        # (AWS-managed keys); revisit if a customer-managed KMS key is ever required.
        vector_bucket = s3vectors.CfnVectorBucket(
            self,
            "VectorBucket",
            vector_bucket_name=vs_cfg["vector_bucket_name"],
        )

        # The vector index.
        #   - dimension MUST equal the embedding model's output (Titan v2 = 1024) or every
        #     ingestion fails; sourced from knowledge_base.vector_dimension, and cross-checked
        #     against the model's real output sizes in infra/config.py.
        #   - data_type float32 is the only type S3 Vectors supports.
        #   - distance_metric cosine: Titan v2 embeddings are normalized.
        #   - non_filterable_metadata_keys is a KNOWN TRAP and is IMMUTABLE after creation:
        #     Bedrock's internal metadata keys are filterable by default and blow S3 Vectors'
        #     filterable-metadata limit, failing every ingestion with ValidationException.
        #     Marking them non-filterable is the documented fix, and retrieval never filters
        #     on them so it costs nothing. (Max 10 keys; 5 used - enforced at synth.)
        vector_index = s3vectors.CfnIndex(
            self,
            "VectorIndex",
            vector_bucket_name=vs_cfg["vector_bucket_name"],
            index_name=vs_cfg["index_name"],
            data_type=vs_cfg["data_type"],
            dimension=kb_cfg["vector_dimension"],
            distance_metric=vs_cfg["distance_metric"],
            metadata_configuration=s3vectors.CfnIndex.MetadataConfigurationProperty(
                non_filterable_metadata_keys=vs_cfg["non_filterable_metadata_keys"],
            ),
        )
        # The index lives inside the bucket, so the bucket must exist first. vector_bucket_name
        # is a literal from config (not a Ref), so this edge is declared explicitly rather
        # than inferred from a token reference.
        vector_index.add_dependency(vector_bucket)

        # The bucket the KB ingests from: scraped markdown plus one metadata sidecar per page.
        # Filled by the scraper Lambda (section 2). Private posture throughout - no public
        # access, ACLs disabled, encrypted at rest, SSL-only - and torn down with the stack so
        # `cdk destroy` leaves nothing behind to pay for.
        source_bucket = s3.Bucket(
            self,
            "KnowledgeBaseSourceBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # KB execution role, assumable by the Bedrock service. Other resources reference this
        # object directly, so no ARN is ever copy-pasted.
        kb_role = iam.Role(
            self,
            "KnowledgeBaseRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Execution role for the SJSU Navigator Bedrock Knowledge Base.",
        )
        # Invoke the embeddings model - used for both ingested chunks and incoming queries.
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[embedding_model_arn],
            )
        )
        # Data-plane access to the S3 Vectors index: read/write vectors + read the index.
        # These are the actions AWS documents for a Bedrock KB on an S3 Vectors store. Scoped
        # to the one index ARN, which already nests the bucket name
        # (arn:...:bucket/<bucket>/index/<index>), so no wildcard is needed.
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3vectors:PutVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:DeleteVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:GetIndex",
                ],
                resources=[vector_index.attr_index_arn],
            )
        )
        # Read the source content to ingest: ListBucket on the bucket, GetObject on its
        # objects. Wired through the bucket object, not a hardcoded ARN.
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[source_bucket.bucket_arn, source_bucket.arn_for_objects("*")],
            )
        )

        knowledge_base = bedrock.CfnKnowledgeBase(
            self,
            "KnowledgeBase",
            name=kb_cfg["name"],
            role_arn=kb_role.role_arn,
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=embedding_model_arn,
                ),
            ),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type="S3_VECTORS",
                # S3VectorsConfiguration is a oneOf: EITHER index_arn alone, OR index_name +
                # vector_bucket_arn - never all three. All three matches BOTH subschemas and
                # CloudFormation rejects it as ambiguous at validation, before anything is
                # created. index_arn alone it is: the ARN already nests bucket + index name, so
                # it fully identifies the store, and the GetAtt keeps the dependency on the
                # in-stack index (which itself depends on the bucket).
                s3_vectors_configuration=bedrock.CfnKnowledgeBase.S3VectorsConfigurationProperty(
                    index_arn=vector_index.attr_index_arn,
                ),
            ),
        )
        # The KB must not be created before the index exists, and needs its role plus the
        # role's inline policy in place. The index already depends on the vector bucket, so
        # that ordering comes along transitively.
        knowledge_base.add_dependency(vector_index)
        knowledge_base.node.add_dependency(kb_role)

        # The S3 data source. Chunking comes from config; the strategy and its values are
        # gav's baseline, to be retuned with the eval once an account exists.
        #
        # THE NAME CARRIES THE CHUNKING CONFIG ON PURPOSE (resolve_data_source_name builds it).
        # Chunking is immutable in Bedrock, so changing it makes CloudFormation REPLACE this
        # resource - and CloudFormation replaces by creating the new one BEFORE deleting the
        # old. With a fixed name that collides inside the knowledge base and the deploy dies
        # mid-update on "DataSource with name ... already exists (409 AlreadyExists)". Folding
        # the chunk config into the name keeps the replacement name distinct, so a chunking
        # change is a config.yaml edit plus `cdk deploy` instead of manual AWS surgery.
        #
        # dataDeletionPolicy is left at Bedrock's DELETE default, so the old chunks leave the
        # vector index with the old data source rather than lingering beside the new ones.
        # The replacement starts EMPTY (the delete drops its vectors and the new one has
        # ingested nothing) and `cdk deploy` does NOT refill it - the install trigger in
        # section 2 only re-fires on scraper change, so re-ingestion needs a manual kick.
        s3_data_source = bedrock.CfnDataSource(
            self,
            "S3DataSource",
            name=resolve_data_source_name(config),
            knowledge_base_id=knowledge_base.attr_knowledge_base_id,
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type="S3",
                s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                    bucket_arn=source_bucket.bucket_arn,
                ),
            ),
            vector_ingestion_configuration=bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                chunking_configuration=bedrock.CfnDataSource.ChunkingConfigurationProperty(
                    chunking_strategy=chunking_cfg["strategy"],
                    fixed_size_chunking_configuration=bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                        max_tokens=chunking_cfg["max_tokens"],
                        overlap_percentage=chunking_cfg["overlap_percentage"],
                    ),
                ),
            ),
        )
        # The data source cannot be created before the KB exists.
        s3_data_source.add_dependency(knowledge_base)

        # Held for the sections below: the scraper writes to the bucket and starts ingestion
        # jobs on the KB + data source; the chat Lambda retrieves from the KB.
        self._source_bucket = source_bucket
        self._knowledge_base = knowledge_base
        self._s3_data_source = s3_data_source

        # --- 2. Scraper: Lambda + deps layer + daily schedule + install trigger --

        scraper_cfg = resolve_scraper(config)
        seed_pages = resolve_seed_pages(config)

        # Dependency LAYER: trafilatura + lxml + regex + httpx as manylinux x86_64 wheels. Built
        # locally with pip --platform (no Docker); Docker bundling is the fallback if a wheel is
        # ever missing. The asset hash is keyed on this layer's own requirements.txt (see
        # _requirements_hash and the note on the chat layer). See _PipManylinuxLayerBundler.
        scraper_deps_layer = _lambda.LayerVersion(
            self,
            "ScraperDepsLayer",
            description="trafilatura/lxml/regex/httpx/pypdf (manylinux x86_64) for the scraper Lambda.",
            compatible_runtimes=[_LAMBDA_PYTHON],
            compatible_architectures=[_LAMBDA_ARCH],
            code=_lambda.Code.from_asset(
                str(_SCRAPER_DIR),
                # DISTINCT PER LAYER, and load-bearing - see the note on the chat layer.
                asset_hash=_requirements_hash("scraper", _SCRAPER_DIR / "requirements.txt"),
                asset_hash_type=AssetHashType.CUSTOM,
                exclude=["*", ".*", "!requirements.txt"],
                bundling=BundlingOptions(
                    image=_LAMBDA_PYTHON.bundling_image,
                    local=_PipManylinuxLayerBundler(_SCRAPER_DIR / "requirements.txt"),
                    # Docker fallback: inside the Linux bundling image, a plain install yields
                    # correct Linux wheels natively. platform pins x86_64 even on an ARM Mac.
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt --target /asset-output/python",
                    ],
                    platform="linux/amd64",
                ),
            ),
        )

        # THE FACTS AS A LAYER, and the one place this stack diverges from gav.
        #
        # Gav ships its 19 seed URLs to the Lambda inside the SCRAPER_TIERS environment variable.
        # That does not survive the scale-up: Lambda caps all environment variables at 4 KB in
        # AGGREGATE (a hard limit, not a soft default, and not raisable by a quota request), and
        # the crawl list alone is 19 KB as compact JSON of url/section pairs - 2.9 KB even gzipped
        # and base64'd, which is most of the budget for one variable and leaves the list
        # unreadable.
        #
        # So the files travel as content and only a FILENAME goes in the environment. A layer
        # rather than a function bundle because data/ lives at the REPO ROOT (a decision: it is
        # content, and BOTH languages read it) while each function's asset is its own source
        # directory - from_asset takes one directory, and pointing it at the root would bundle
        # the entire repo.
        #
        # ONE LAYER FOR THE WHOLE DIRECTORY, carried by the scraper AND by the two functions that
        # run the agent loop: the crawl list is what the scraper opens, and places.csv,
        # buildings.csv, contacts.csv and abbreviations.csv are what app/campus_data.py opens at
        # import to build the location cards, the crisis panel and the prompt's rosters. Two
        # layers would be two things to remember to attach.
        #
        # THE ASSET ROOT IS data/ ITSELF, so its contents extract to /opt directly and
        # URL_LIST_FILE stays a bare filename, exactly as before the move. Both the asset include
        # and the URL_LIST_FILE env below read the SAME config value, so the file the layer
        # carries and the file the handler opens cannot drift apart.
        url_list_file = scraper_cfg["url_list_file"]
        campus_data_layer = _lambda.LayerVersion(
            self,
            "CampusDataLayer",
            description=(
                f"The repo-root data/ directory - the curated crawl list ({len(seed_pages)} "
                "pages), campus places and buildings, contacts, and the shorthand glossary - as "
                "a bundled asset, too large for Lambda's 4 KB environment-variable budget."
            ),
            compatible_runtimes=[_LAMBDA_PYTHON],
            compatible_architectures=[_LAMBDA_ARCH],
            # Layer content extracts to /opt at runtime, which is where seed_list_path and
            # app/campus_data.py both look first.
            #
            # LISTED FILE BY FILE, like the function bundles below, so README.md stays out of a
            # deployed layer and a new file in data/ is a deliberate addition here rather than
            # something that rides along.
            #
            # ".*" is NOT redundant with "*", and leaving it out is a real leak. CDK's exclude
            # globbing does not match hidden entries with a leading-wildcard pattern, so "*" alone
            # ships .DS_Store from the directory inside a deployed layer. Verified by listing the
            # staged asset; test_campus_data_layer_ships_only_the_data_files pins it, and the
            # function assets below need the same treatment.
            code=_lambda.Code.from_asset(
                str(_DATA_DIR),
                exclude=[
                    "*",
                    ".*",
                    f"!{url_list_file}",
                    "!places.csv",
                    "!buildings.csv",
                    "!contacts.csv",
                    "!abbreviations.csv",
                ],
            ),
        )

        # Its OWN execution role: basic logs + narrow S3 write + narrow StartIngestionJob.
        scraper_lambda_role = iam.Role(
            self,
            "ScraperFunctionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Execution role for the scraper Lambda (scrape -> S3 -> start ingestion).",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        # Upload markdown + metadata sidecars into the KB source bucket, and DELETE the ones the
        # crawl list no longer calls for. Without the delete the uploader could only ever add: a
        # page removed from the list would keep its document in the bucket and stay indexed
        # forever, so de-listing would be a silent no-op. See lambda_function.prune_stale_objects.
        scraper_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:DeleteObject"],
                resources=[source_bucket.arn_for_objects("*")],
            )
        )
        # READ the source objects, which is what change gating runs on: the scraper HEADs each
        # markdown object to read back the `content-sha256` it stamped there last time, and
        # uploads only when the fresh content hashes differently. HeadObject is authorized as
        # s3:GetObject, so this grant is what makes an unchanged page cost nothing.
        scraper_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[source_bucket.arn_for_objects("*")],
            )
        )
        # ListBucket is granted on the BUCKET arn, not the object arn - the prune has to enumerate
        # what is actually there before it can tell what is stale, and the ingestion decision reads
        # the same listing's LastModified times.
        scraper_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[source_bucket.bucket_arn],
            )
        )
        # Trigger ingestion of the fresh content on the specific KB (StartIngestionJob is scoped
        # to the knowledge-base ARN; it covers that KB's data sources). ListIngestionJobs comes
        # with it because the scraper checks the job history before starting: Bedrock allows one
        # job per data source at a time, so an overlap has to be detected and skipped, and the
        # last job's start time is how a skipped change is found again on the next run without
        # storing anything.
        scraper_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:StartIngestionJob", "bedrock:ListIngestionJobs"],
                resources=[knowledge_base.attr_knowledge_base_arn],
            )
        )

        scraper_log_group = logs.LogGroup(
            self,
            "ScraperFunctionLogGroup",
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        scraper_lambda = _lambda.Function(
            self,
            "ScraperFunction",
            runtime=_LAMBDA_PYTHON,
            architecture=_LAMBDA_ARCH,
            handler="lambda_function.handler",
            # Ship only the two source files; deps come from the layer, boto3 from the runtime.
            # ".*" for the same reason as the seed-list layer: without it, scraper/.venv (70 MB of
            # local test deps) and .pytest_cache ride along into the deployed function.
            code=_lambda.Code.from_asset(
                str(_SCRAPER_DIR),
                exclude=["*", ".*", "!scraper.py", "!lambda_function.py"],
            ),
            layers=[scraper_deps_layer, campus_data_layer],
            role=scraper_lambda_role,
            # 15 minutes, the Lambda maximum, against a run estimated at 4.5-12 minutes: scaling
            # gav's measured 19-pages-in-25-67s to this corpus. Gav's 5 minutes would time out at
            # the slow end. The estimate's top is close enough to the cap that the first live run
            # has to be read rather than assumed, which is what the summary's duration_seconds is
            # for (docs/build-plan.md, "Resolved"). A timeout is not silent damage - the prune runs
            # before ingestion and a killed run simply leaves the corpus as it was.
            timeout=Duration.minutes(15),
            memory_size=512,
            log_group=scraper_log_group,
            environment={
                # The crawl list's FILENAME, not its contents (Lambda's 4 KB env cap - see the
                # seed-list layer above). Same config value that selects the bundled asset.
                "URL_LIST_FILE": url_list_file,
                "SCRAPE_TIMEOUT_SECONDS": str(scraper_cfg["timeout_seconds"]),
                "SCRAPER_USER_AGENT": scraper_cfg["user_agent"],
                "SOURCE_BUCKET": source_bucket.bucket_name,
                "KNOWLEDGE_BASE_ID": knowledge_base.attr_knowledge_base_id,
                "DATA_SOURCE_ID": s3_data_source.attr_data_source_id,
            },
        )
        # Needs the KB + data source (it calls StartIngestionJob on them at runtime).
        scraper_lambda.node.add_dependency(s3_data_source)

        # ONE SCHEDULE, no tiers. Gav splits its corpus into a daily fast tier and a slower full
        # sweep to answer "we changed our hours, when does the bot know?" cheaply. Here the whole
        # corpus is swept daily instead: 203 pages fit one Lambda run, and change gating means an
        # unchanged day pays only the Lambda time (docs/build-plan.md, "Resolved"). That makes the
        # tier machinery - a tier map, one rule per tier, a tier name in the event payload, and a
        # prune that must never key off one tier's slice - cost without a benefit.
        #
        # No event payload for the same reason: every invocation is the complete sweep, so the
        # handler reads nothing from the event and a hand-added rule or a console test invoke
        # behaves identically to the schedule.
        events.Rule(
            self,
            "ScraperDailySchedule",
            description=(
                f"Daily re-scrape of the {len(seed_pages)} curated SJSU pages to refresh "
                "knowledge base content."
            ),
            schedule=events.Schedule.expression(scraper_cfg["schedule_cron"]),
            targets=[events_targets.LambdaFunction(scraper_lambda)],
        )

        # One-click install: invoke the scraper ONCE during `cdk deploy` so the KB is populated the
        # moment the stack comes up - no manual invoke, no waiting a day for the schedule. Uses the
        # stable aws-cdk-lib `triggers.Trigger` (a CDK-managed invoker), NOT a hand-rolled custom
        # resource.
        #   - invocation_type=EVENT: fire-and-forget. The trigger succeeds once the function is
        #     invoked, REGARDLESS of the scrape's result - a flaky site, a partial-page failure, or
        #     the async ingestion never fails or blocks the deploy. (REQUEST_RESPONSE, the default,
        #     would make the deploy wait on the scraper and fail with it - and this run can take 12
        #     minutes.) The daily schedule retries, so an install hiccup is self-healing.
        #   - execute_after: run only after the scraper AND its targets exist - it writes to the
        #     source bucket and calls StartIngestionJob on the data source.
        #   - execute_on_handler_change (default True): fires on install (create) and whenever the
        #     scraper changes; a no-op redeploy does not re-fire. Re-firing is harmless anyway -
        #     change gating means it re-uploads nothing and starts no ingestion job.
        #
        # WHAT "WHENEVER THE SCRAPER CHANGES" ACTUALLY MEANS, since it is easy to assume every
        # deploy re-scrapes and it does not. execute_on_handler_change ties this trigger to the
        # function's currentVersion, so the synthesized HandlerArn is a Ref to a
        # `ScraperFunctionCurrentVersion<hash>` resource whose LOGICAL ID hashes the function's
        # code asset plus its configuration (env vars, layers, memory, timeout, role). Deploy
        # something that leaves all of those alone and the logical id is identical, this custom
        # resource's properties are unchanged, CloudFormation does not re-run it, and NO SCRAPE
        # HAPPENS. The corollary is the trap: editing the crawl list DOES change the seed-list
        # layer, which changes the function configuration, so a content-only edit re-fires the
        # trigger - which is what you want here, and is not free.
        triggers.Trigger(
            self,
            "ScraperInstallTrigger",
            handler=scraper_lambda,
            invocation_type=triggers.InvocationType.EVENT,
            execute_after=[
                scraper_lambda,
                source_bucket,
                knowledge_base,
                s3_data_source,
            ],
            execute_on_handler_change=True,
        )

        # --- 3. Guardrail: PROMPT_ATTACK input screen ---
        #
        # ONE guardrail, screening PROMPT_ATTACK on the input side. It is applied via
        # ApplyGuardrail(source=INPUT) on the bare query in the handler; nothing is attached to
        # Converse, so there is no output guardrail here to find.
        #
        # WHERE IT SITS IN THE TURN (docs/synthesis.md, docs/architecture.drawio): the screen runs
        # on the bare query after the daily cap and before the student's message is written
        # (app/turn.py, STEP 2), so a blocked message never becomes a turn. There is NO pre-model
        # safety intercept. The deterministic phrase gate was removed on 2026-08-10; safety is now
        # a model-emitted <safety> tag the server resolves against data/contacts.csv
        # (docs/chat-service.md, Safety).
        #
        # Everything else was left out deliberately. The screen runs ahead of the system prompt,
        # so a content filter or a silent PII rewrite would pre-empt the prompt's crisis handling.
        # The prompt owns safety; PROMPT_ATTACK stays because it is an attack on the prompt itself.

        # Canonical definition of the guardrail. It drives BOTH the CfnGuardrail props and the
        # version-description hash, so the hash covers exactly what is deployed and any config
        # change forces a new published version.
        guardrail_def = {
            "name": guardrail_cfg["name"],
            "contentFilters": [
                {
                    "type": f["type"],
                    "inputStrength": f["input_strength"],
                    # Forced NONE by resolve_guardrail, not read from config: this guardrail is
                    # only ever applied to input, and Bedrock requires NONE for PROMPT_ATTACK.
                    "outputStrength": f["output_strength"],
                }
                for f in guardrail_cfg["content_filters"]
            ],
            "blockedInputMessaging": guardrail_cfg["blocked_input_messaging"],
        }

        input_guardrail = bedrock.CfnGuardrail(
            self,
            "InputGuardrail",
            name=guardrail_def["name"],
            # Bedrock caps this at 200 characters and rejects the change set at deploy if it is
            # longer (the L1 does not validate it at synth). The reasoning that does not fit
            # lives in the comment block above, not here.
            description=(
                "Input screen for the SJSU Student Success Navigator: "
                "ApplyGuardrail(source=INPUT) on the bare student query, PROMPT_ATTACK only. "
                "No other filter and no PII policy - the system prompt owns safety."
            ),
            blocked_input_messaging=guardrail_def["blockedInputMessaging"],
            # Bedrock requires a blocked-outputs message on every guardrail. This one is
            # unreachable by construction - the guardrail is never applied to output - so it
            # reuses the input message rather than carrying a second string to maintain.
            blocked_outputs_messaging=guardrail_def["blockedInputMessaging"],
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type=f["type"],
                        input_strength=f["inputStrength"],
                        output_strength=f["outputStrength"],
                    )
                    for f in guardrail_def["contentFilters"]
                ],
            ),
            # NO sensitive_information_policy_config: PII anonymization would rewrite the
            # student's message before the model ever read it, replacing the details that make
            # an urgent message legible with {NAME} and {ADDRESS}.
        )

        # Numbered, immutable version the Lambda pins to. The description carries a content hash
        # of the resolved guardrail config, because CfnGuardrailVersion has no other property
        # that changes when config.yaml does: without it, a guardrail edit updates the DRAFT but
        # never publishes a new version, and the Lambda goes on using the stale one. Pinning to
        # DRAFT instead would be mutable, with no rollback and no reproducibility.
        input_guardrail_version = bedrock.CfnGuardrailVersion(
            self,
            "InputGuardrailVersion",
            guardrail_identifier=input_guardrail.attr_guardrail_id,
            description=f"input config-{_config_hash(guardrail_def)}",
        )

        # --- Chat history: ONE DynamoDB table, partitioned on the user -----------
        #
        # The first slice of per-user accounts and chat history (docs/accounts-and-storage.md,
        # Storage). It landed BEFORE any code read or wrote it, precisely so the slice that
        # does - app/history.py, now shipped - was pure application code with no CDK diff
        # tangled into it, and so the shape below was argued once, here, rather than being
        # decided in passing by whoever wrote the first PutItem.
        #
        # ONE TABLE for both item kinds, distinguished by a sort-key prefix:
        #   conversation header   pk=USER#<sub>   sk=CONV#<convId>
        #   message               pk=USER#<sub>   sk=MSG#<convId>#<ulid>
        # so listing a user's conversations is begins_with('CONV#') and loading one in order
        # is begins_with('MSG#<convId>#'). Both are a single Query on one partition.
        #
        # PARTITIONING ON THE USER IS A SECURITY PROPERTY, not a modelling convenience. The
        # partition key is derived from the JWT `sub` in the Lambda, so a request cannot
        # address another student's data even with a forged convId - there is no tenant
        # filter that can be forgotten, because there is no filter.
        #
        # The attribute names `pk`/`sk` are generic ON PURPOSE. Both item kinds share them
        # and neither is "a user id" or "a conversation id" - the sort key is a compound
        # prefix in both cases - so naming them after either would be wrong for the other.
        # They are IMMUTABLE: changing a key attribute replaces the table, taking the
        # history with it, which is why they are spelled here beside this comment rather
        # than exposed in config.yaml as if they were tunable.
        #
        # NO SECONDARY INDEX. Every access pattern in the doc is served by the primary key;
        # a GSI for cross-user time queries is purely additive and nobody has asked for one.
        # Adding one later costs a backfill, not a migration.
        chat_history_table = dynamodb.Table(
            self,
            "ChatHistoryTable",
            table_name=chat_history_cfg["table_name"],
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            # ON-DEMAND. Pilot traffic is unknown and bursty - a demo to the sponsors is a
            # spike against an otherwise idle table - and provisioned capacity would mean
            # either paying for a peak that mostly does not happen or throttling the one
            # session anybody is watching. Billing mode is one of the few properties here
            # that CAN be changed on a live table, so this is a starting point rather than
            # a commitment.
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            # PITR: 35 days of second-granularity restore. Cheap insurance on the only copy
            # of a student's transcript, and the failure it covers is not hardware - it is a
            # bad deploy of the application slice that comes next writing or deleting the
            # wrong items under the right keys, which no backup-on-a-schedule catches in
            # time.
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            # TTL ENABLED NOW, ON AN ATTRIBUTE NOTHING WRITES. The retention window for
            # identifiable transcripts - some of which will carry crisis disclosures - is an
            # open policy question with the university (docs/accounts-and-storage.md, Open),
            # and this stack does not get to answer it. Items with no `expiresAt` NEVER
            # expire, so enabling it costs exactly nothing today; enabling it later is a
            # table-level change with a disable/enable cycle behind it. `expiresAt` is epoch
            # SECONDS when it is eventually written, because that is the only format TTL
            # reads - every other timestamp in these items is an ISO 8601 UTC string, so the
            # one that is not is the one worth naming here.
            time_to_live_attribute="expiresAt",
            # RETAIN, and the one place this stack breaks its own "one-click install implies
            # one-click uninstall" rule. Everything else here is reproducible from source: a
            # destroyed bucket re-fills from the next scrape, a destroyed KB re-ingests. This
            # table is the ONLY copy of what students actually said, so a `cdk destroy` that
            # silently took it would be unrecoverable in a way nothing else in the stack is.
            #
            # THE COST, stated rather than discovered: the table name is a fixed global name,
            # so destroy-then-redeploy fails on CreateTable until the leftover is renamed in
            # config.yaml, imported, or deleted by hand. A loud collision is the better half
            # of this trade.
            removal_policy=RemovalPolicy.RETAIN,
        )

        # --- 4. Chat Lambda: bare handler + role + deps layer --------------------
        #
        # The query path: one Lambda running the deterministic safety intercept, the guardrail
        # screen, and camp's Converse agent loop. A BARE handler - no FastAPI, no Mangum
        # (docs/build-plan.md): camp's service modules are framework-free Python and move in as
        # files, so only main.py and the routers are replaced.
        #
        # The HTTP API that fronts this arrives at bullet 5. Until then the function is
        # deployable and directly invocable but has no route.

        generation_model_id = generation_cfg["model_id"]

        # Its OWN execution role, distinct from the KB role and the scraper role. Basic
        # execution (CloudWatch Logs) via the managed policy; Bedrock grants added narrowly.
        chat_lambda_role = iam.Role(
            self,
            "ChatFunctionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Execution role for the chat Lambda (retrieve + guardrail + converse).",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        # THE CHAT TURN'S GRANTS, collected as they are added. The WebSocket generation
        # worker runs the SAME turn - retrieve, invoke, screen, read and write the table -
        # so it is given this exact list rather than a second hand-written copy that could
        # come to differ. A streamed turn that could reach less than a buffered one would
        # not fail loudly; it would fail somewhere inside an answer.
        _chat_turn_statements = []

        def _grant_chat_turn(statement):
            _chat_turn_statements.append(statement)
            chat_lambda_role.add_to_policy(statement)

        # Retrieve chunks from the KB. Scoped to the one knowledge base.
        _grant_chat_turn(
            iam.PolicyStatement(
                actions=["bedrock:Retrieve"],
                resources=[knowledge_base.attr_knowledge_base_arn],
            )
        )
        # Invoke the generation model (Converse maps to bedrock:InvokeModel*). Modern Claude
        # models are invoked through a CROSS-REGION INFERENCE PROFILE - a geographic-prefixed id
        # like "us.anthropic..." - which needs a different IAM shape than a bare on-demand
        # foundation-model id. resolve_generation decides which form this is, so the branch is
        # config-driven rather than a string test inline here.
        if generation_cfg["is_inference_profile"]:
            base_model_id = generation_cfg["base_model_id"]
            _grant_chat_turn(
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel*"],
                    resources=[
                        # The account+region-scoped profile itself.
                        f"arn:{self.partition}:bedrock:{self.region}:{self.account}"
                        f":inference-profile/{generation_model_id}",
                        # The underlying foundation model in the SOURCE region (this stack's).
                        f"arn:{self.partition}:bedrock:{self.region}"
                        f"::foundation-model/{base_model_id}",
                        # ...and in every destination region the profile may route to. Those
                        # cannot be enumerated without hardcoding a region list, so this is a
                        # region wildcard on the SAME single model id - the AWS-recommended
                        # grant for cross-region inference.
                        f"arn:{self.partition}:bedrock:*::foundation-model/{base_model_id}",
                    ],
                )
            )
            # Resolve the profile's metadata/routing at runtime. ListInferenceProfiles has no
            # resource-level scoping (must be "*"); GetInferenceProfile is read-only metadata.
            _grant_chat_turn(
                iam.PolicyStatement(
                    actions=[
                        "bedrock:GetInferenceProfile",
                        "bedrock:ListInferenceProfiles",
                    ],
                    resources=["*"],
                )
            )
        else:
            _grant_chat_turn(
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel"],
                    resources=[
                        f"arn:{self.partition}:bedrock:{self.region}"
                        f"::foundation-model/{generation_model_id}"
                    ],
                )
            )
        # Invoke the TITLING model (app/titles.py). A separate grant because it is a separate
        # model id: the generation grant above names one model, so a titling call against
        # another would be AccessDenied - which, unlike a denied generation, fails silently
        # by design (every conversation keeps its first-message title) and would therefore
        # be discovered from a sidebar that never improves rather than from an error.
        #
        # Skipped entirely when the two ids are the same, because the statement above
        # already covers it and a duplicate grant reads like a second, different permission.
        title_model_id = generation_cfg["title_model_id"]
        if title_model_id != generation_model_id:
            if generation_cfg["title_is_inference_profile"]:
                title_base_model_id = generation_cfg["title_base_model_id"]
                title_resources = [
                    f"arn:{self.partition}:bedrock:{self.region}:{self.account}"
                    f":inference-profile/{title_model_id}",
                    f"arn:{self.partition}:bedrock:{self.region}"
                    f"::foundation-model/{title_base_model_id}",
                    f"arn:{self.partition}:bedrock:*::foundation-model/{title_base_model_id}",
                ]
            else:
                title_resources = [
                    f"arn:{self.partition}:bedrock:{self.region}"
                    f"::foundation-model/{title_model_id}"
                ]
            _grant_chat_turn(
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel*"],
                    resources=title_resources,
                )
            )

        # ApplyGuardrail on the ONE guardrail: the standalone input screen (source=INPUT).
        # Nothing is attached to Converse, so there is no second ARN to grant.
        _grant_chat_turn(
            iam.PolicyStatement(
                actions=["bedrock:ApplyGuardrail"],
                resources=[input_guardrail.attr_guardrail_arn],
            )
        )
        # Read and write the conversation history (app/history.py).
        #
        # Read AND write, because an ordinary turn is both: the server writes the student's
        # message before the model call, queries the last N messages back for context, then
        # writes the assistant's reply. The counter on the header is an atomic ADD, which is
        # an UpdateItem, so read-only plus PutItem would not cover it either.
        #
        # SPELLED OUT RATHER THAN grant_read_write_data, for ONE action: that helper's read
        # set includes dynamodb:Scan, and Scan is the single operation that does not take a
        # partition key. The entire isolation story for this table is that the Lambda derives
        # the partition key from the JWT `sub`, so a request cannot name another student's
        # data - and a Scan grant is precisely the hole in that, reading every partition at
        # once. Nothing in the access patterns (docs/accounts-and-storage.md) needs it, so a
        # handler bug that reaches for it should fail with AccessDenied rather than return
        # somebody else's transcript. The helper's stream actions (GetRecords,
        # GetShardIterator) go for a duller reason: this table has no stream.
        #
        # The list below IS the doc's access-pattern table, plus DescribeTable (metadata
        # only, no item data - boto3's resource API fetches it lazily). Adding one later is a
        # one-line change here, which is the cost of not granting a category by default.
        #
        # Scoped to THIS table's ARN. No `/index/*` companion because there is no secondary
        # index in v1; adding a GSI means adding its ARN here, and that being a visible edit
        # is the point.
        _grant_chat_turn(
            iam.PolicyStatement(
                actions=[
                    # List a user's conversations; load one conversation in order.
                    "dynamodb:Query",
                    # Read one conversation header.
                    "dynamodb:GetItem",
                    # Append a turn; mint a conversation header.
                    "dynamodb:PutItem",
                    # The atomic ADD on the header's messageCount.
                    "dynamodb:UpdateItem",
                    # Delete everything for one user, item by item and in batches.
                    "dynamodb:DeleteItem",
                    "dynamodb:BatchWriteItem",
                    # Mint-if-absent, so two concurrent first turns cannot both create the
                    # same header.
                    "dynamodb:ConditionCheckItem",
                    "dynamodb:DescribeTable",
                ],
                resources=[chat_history_table.table_arn],
            )
        )

        # Dependency LAYER: pydantic as manylinux x86_64 wheels. pydantic_core is a compiled
        # Rust extension, so this needs the same --platform treatment as the scraper's lxml
        # (verified: the wheel lands as _pydantic_core.cpython-313-x86_64-linux-gnu.so, an ELF
        # binary, where a plain local install would produce a macOS .so that fails at import).
        # boto3 comes from the runtime; there is no FastAPI and no Mangum to carry.
        chat_deps_layer = _lambda.LayerVersion(
            self,
            "ChatDepsLayer",
            description="pydantic (manylinux x86_64) for the chat Lambda's wire contract.",
            compatible_runtimes=[_LAMBDA_PYTHON],
            compatible_architectures=[_LAMBDA_ARCH],
            code=_lambda.Code.from_asset(
                str(_APP_DIR),
                asset_hash=_requirements_hash("chat", _APP_DIR / "requirements.txt"),
                asset_hash_type=AssetHashType.CUSTOM,
                # WITHOUT THIS, THIS LAYER SHIPS THE SCRAPER'S PACKAGES. Verified against
                # aws-cdk-lib 2.260.0's asset-staging.js: an asset's cacheKey is a sha256
                # over its staging props, the staged result comes from
                # `assetCache.obtain(cacheKey, ...)`, and under AssetHashType.OUTPUT the
                # bundle directory is `bundling-temp-${cacheKey}` - which `bundle()`
                # SKIPS ENTIRELY if it already exists. Both deps layers hashed to the same
                # key (same bundling image, same command, same platform, same one-file
                # `exclude`), so the second one silently reused the first one's bundle.
                #
                # The failure mode is as bad as it gets: synth is clean, both layers
                # publish, the deploy succeeds, and the chat Lambda dies at cold start on
                # `No module named 'pydantic'` - which is only visible in CloudWatch,
                # because API Gateway turns it into a 502 and the UI into a blank bubble.
                #
                # A distinct asset_hash is the lever `Code.from_asset` actually exposes
                # (extra_hash is not), and customFingerprint is one of those cache-key
                # props - so it separates the cache keys AND the bundle directories.
                #
                # The tradeoff of CUSTOM over OUTPUT, accepted deliberately: the hash now
                # tracks the requirements FILE rather than the built tree, so a floating
                # dependency resolving to a newer patch does not by itself publish a new
                # layer. Editing requirements.txt does. That is the more reproducible of
                # the two, and it is the only one that keeps the layers apart.
                exclude=["*", ".*", "!requirements.txt"],
                bundling=BundlingOptions(
                    image=_LAMBDA_PYTHON.bundling_image,
                    local=_PipManylinuxLayerBundler(_APP_DIR / "requirements.txt"),
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt --target /asset-output/python",
                    ],
                    platform="linux/amd64",
                ),
            ),
        )

        # Explicit log group so retention is bounded and it is torn down with the stack, rather
        # than the implicit never-expiring group Lambda would create on first invoke and leave
        # orphaned on destroy.
        chat_log_group = logs.LogGroup(
            self,
            "ChatFunctionLogGroup",
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # The chat function's runtime wiring, hoisted out of the constructor because the
        # WebSocket streaming functions in section 7 run the SAME application modules and
        # therefore need the same values. Spelled once: two copies of this dict would drift,
        # and the drift would be a streaming turn answering under a different cap or against
        # a different table than the buffered one.
        chat_environment = {
            "KNOWLEDGE_BASE_ID": knowledge_base.attr_knowledge_base_id,
            "GENERATION_MODEL_ID": generation_model_id,
            # The model that names a conversation. Identity like the one above, and
            # granted its own InvokeModel statement above when it differs.
            "TITLE_MODEL_ID": title_model_id,
            # Lambda auto-sets AWS_REGION and it is RESERVED (it cannot be set in a
            # function's configuration), so the region is passed under our own key.
            "BEDROCK_REGION": self.region,
            "NUMBER_OF_RESULTS": str(retrieval_cfg["number_of_results"]),
            "RETRIEVE_MIN_SCORE": str(retrieval_cfg["min_score"]),
            "GENERATION_MAX_TOKENS": str(generation_cfg["max_tokens"]),
            "GENERATION_TEMPERATURE": str(generation_cfg["temperature"]),
            "MAX_QUERY_CHARS": str(request_cfg["max_query_chars"]),
            # The agent loop's caps. MAX_CONVERSE_ITERATIONS is camp's 6, but from config
            # and logged when reached (see resolve_chat).
            "MAX_CONVERSE_ITERATIONS": str(chat_cfg["max_converse_iterations"]),
            "MAX_HISTORY_MESSAGES": str(chat_cfg["max_history_messages"]),
            # The read endpoints' caps (GET /conversations and one conversation). Not
            # the same number as MAX_HISTORY_MESSAGES on purpose: that one is billed in
            # tokens on every turn, these bound one DynamoDB query of stored items.
            "MAX_CONVERSATIONS_LISTED": str(chat_cfg["max_conversations_listed"]),
            "MAX_CONVERSATION_MESSAGES": str(chat_cfg["max_conversation_messages"]),
            # The loop's WALL-CLOCK budget. The iteration cap bounds how many model
            # calls happen, not how long they take, so without this a slow run is
            # killed mid-Converse - billed, with no response reaching the student.
            # Validated at synth to sit under the function timeout above.
            "CONVERSE_DEADLINE_SECONDS": str(chat_cfg["converse_deadline_seconds"]),
            # The conversation title's cap and the titling call's own budget. The cap
            # reaches TWO places inside the function, like the card caps do: the model
            # title's validator and the first-message truncation that is its fallback,
            # so the two can never disagree about how long a sidebar row may be.
            "TITLE_MAX_CHARS": str(chat_cfg["title_max_chars"]),
            "TITLE_DEADLINE_SECONDS": str(chat_cfg["title_deadline_seconds"]),
            # The card contract's caps. Each one reaches TWO places inside the function -
            # the parser that enforces it and the system prompt that states it - and both
            # read it from here, so the number the model is told is by construction the
            # number the server applies.
            "CARD_MAX_CARDS": str(cards_cfg["max_cards"]),
            "CARD_MAX_RETRIEVAL_RESULTS": str(cards_cfg["max_retrieval_results"]),
            "CARD_TITLE_MAX_CHARS": str(cards_cfg["title_max_chars"]),
            "CARD_DESC_MAX_CHARS": str(cards_cfg["desc_max_chars"]),
            "CARD_FOLLOWUP_MAX_CHARS": str(cards_cfg["followup_max_chars"]),
            # The input screen, pinned to its published numbered version. There is no
            # OUTPUT_GUARDRAIL_* pair and no GUARDRAIL_TRACE: nothing is attached to
            # Converse, so there is no trace to configure.
            "INPUT_GUARDRAIL_ID": input_guardrail.attr_guardrail_id,
            "INPUT_GUARDRAIL_VERSION": input_guardrail_version.attr_version,
            # The conversation history table, read and written on every turn. By
            # REFERENCE, not as the literal from config.yaml: a copy here would be a
            # second place the name is spelled, and the two would drift the moment
            # either changed.
            "CHAT_HISTORY_TABLE_NAME": chat_history_table.table_name,
            # The PER-USER daily message cap, counted in that same table under the
            # caller's own partition (app/ratelimit.py). Nothing else in this stack
            # bounds what ONE account spends: the stage throttle and the reserved
            # concurrency below both bound the service as a whole and cannot tell two
            # students apart.
            #
            # PRESENT ONLY WHEN THE CAP IS ON. A disabled cap omits the variable rather
            # than setting it to "0", so there is one spelling of off and the function
            # reads an unset value as disabled without a second default to keep in step.
            **(
                {"DAILY_MESSAGE_LIMIT": str(daily_message_limit)}
                if daily_message_limit is not None
                else {}
            ),
            # THE ESCALATE-TO-HUMAN PATH, present only when config.yaml names a recipient.
            # Absent is off, in one spelling, and the function reads it the same way: with
            # no address app/prompts.py leaves the tag out of the system prompt entirely
            # and app/escalation.py builds no draft. The recipient is spelled here AND in
            # config.json below because the two consumers need different halves of it - the
            # server assembles the message, the browser decides whether the component
            # exists - and both read this one resolved value.
            **(
                {
                    "ESCALATION_RECIPIENT": escalation_cfg["recipient"],
                    "ESCALATION_SUBJECT": escalation_cfg["subject"],
                    "ESCALATION_MAX_CHARS": str(escalation_cfg["max_chars"]),
                }
                if escalation_cfg is not None
                else {}
            ),
        }

        chat_lambda = _lambda.Function(
            self,
            "ChatFunction",
            runtime=_LAMBDA_PYTHON,
            architecture=_LAMBDA_ARCH,
            handler="handler.lambda_handler",
            # The handler plus the service modules it imports. Listed file by file rather than
            # by a directory glob so a stray script in app/ cannot ride along, and ".*" for the
            # dotfile trap the scraper section documents: without it app/.venv and
            # .pytest_cache would end up inside the deployed function.
            code=_lambda.Code.from_asset(
                str(_APP_DIR),
                exclude=[
                    "*",
                    ".*",
                    "!handler.py",
                    # The turn itself, lifted out of handler.py: rate limit, guardrail,
                    # write, read, model, write, title. handler.py imports it at module
                    # scope, so an omission here is an ImportError at cold start.
                    "!turn.py",
                    "!settings.py",
                    "!models.py",
                    "!prompts.py",
                    "!tools.py",
                    "!retrieve.py",
                    "!cards.py",
                    "!safety.py",
                    "!escalation.py",
                    "!places.py",
                    # The reader of the repo-root data/ directory. places.py, safety.py and
                    # prompts.py all import it at module scope, so a bundle without it is a
                    # cold start that dies on the import line.
                    "!campus_data.py",
                    "!orchestrator.py",
                    "!campus_time.py",
                    "!history.py",
                    "!titles.py",
                    "!usage.py",
                    "!ratelimit.py",
                ],
            ),
            # The data layer as well as the deps: places.py, safety.py and prompts.py read
            # the repo-root data/ CSVs at import, and Lambda extracts this layer to /opt.
            layers=[chat_deps_layer, campus_data_layer],
            role=chat_lambda_role,
            # 29 seconds against camp's 30, and this is a CEILING rather than a choice: an HTTP
            # API integration's timeoutInMillis maxes out at 30,000 ms (verified against the
            # apigatewayv2 CreateIntegration reference, 2026-08-05) and cannot be raised by a
            # quota request. A longer Lambda timeout would just let the function keep running
            # and billing after API Gateway had already returned 504 to the student. One second
            # under, so the function's own timeout wins and the failure is diagnosable in ITS
            # logs rather than only as a gateway 504.
            #
            # This is the "raise timeout/memory above their 30s/256MB" item in
            # docs/synthesis.md, and it is HALF DONE deliberately: memory rises, the timeout
            # cannot. If the agent loop turns out not to fit in 29s, the fix is architectural
            # (streaming, or an async job) rather than a bigger number - recorded in
            # docs/build-plan.md under Open.
            #
            # The constant lives in infra/config.py because the validator for
            # chat.converse_deadline_seconds checks the deadline against it: the loop's
            # wall-clock budget has to sit under this number, and a copy here would let the
            # two drift.
            timeout=Duration.seconds(CHAT_LAMBDA_TIMEOUT_SECONDS),
            # 1024 MB, up from camp's 256. Lambda scales CPU with memory, and this function
            # imports pydantic and boto3 and then makes several sequential Bedrock calls inside
            # a 29-second budget, so cold-start import time comes directly out of the time the
            # loop has to work in. Untuned against a real invocation (no account), so it is a
            # deliberate over-provision rather than a measured value.
            memory_size=1024,
            log_group=chat_log_group,
            environment=chat_environment,
        )
        # The function retrieves from the KB at runtime, so it must not exist before the KB.
        chat_lambda.node.add_dependency(knowledge_base)
        # Same for the history table it reads and writes. The env var above is already a Ref,
        # so CloudFormation would order these anyway; stated explicitly to match the KB edge
        # rather than relying on a reference that a later refactor could turn into a literal.
        chat_lambda.node.add_dependency(chat_history_table)
        # Held for bullet 5, which routes the HTTP API at it.
        self._chat_lambda = chat_lambda

        # --- 5. Auth + API: Cognito pool/client + JWT authorizer + HTTP API ------
        #
        # HTTP API (v2), DECIDED - not a REST API. A REST API would buy WAF attachment and
        # response streaming, and neither changes the outcome here: the 30-second
        # integration ceiling is the same on both, and if the loop ever needs longer the
        # answer is streaming, which leaves API Gateway entirely (Function URL). So the
        # cheaper, simpler v2 it is, and no WAF follows from that rather than being an
        # oversight (docs/synthesis.md).
        #
        # THE COST FENCE, in full, because there is no billing alarm until v2:
        #   1. the Cognito gate on POST /chat  - the only billable route
        #   2. route throttling                - bounds invocations STARTED per second
        #   3. reserved concurrency            - bounds invocations running AT ONCE
        #   4. the loop's deadline + iteration cap (app/orchestrator.py) - bounds ONE run
        # Numbers 2 and 3 are not redundant: at 10 rps against a 29-second budget, ~290
        # invocations can be in flight, all of them spending Bedrock tokens. And neither
        # bounds a single runaway invocation, which is what number 4 is for.

        http_api_cfg = resolve_http_api(config)

        # Where Cognito is allowed to send the browser back to, derived from the SAME
        # config list the CORS allowlist uses rather than typed a second time - both
        # answer "which origins is this app served from", and two literals would drift.
        #
        # THE TRAILING SLASH IS LOAD-BEARING. Cognito matches a redirect_uri against these
        # by exact string, and the frontend sends `window.location.origin + "/"`, so an
        # entry without it fails with redirect_mismatch - an error rendered by Cognito's
        # own page, before the app is ever reached.
        local_redirect_urls = [f"{origin.rstrip('/')}/" for origin in cors_allow_origins]

        # A Cognito user pool holding ONE ACCOUNT PER PERSON, each created administratively
        # after deploy. The single shared pilot credential this replaces is gone: it could
        # not tell two students apart, so nothing downstream could ever be per-user, and
        # every rotation was a message to everybody who had it.
        #
        # THIS POOL IS ALSO WHERE SJSU'S OWN IDP LANDS. Federating Okta in later is a
        # config-only change - an identity provider on this pool and its name in the web
        # client's supported list - and that is the reason sign-in below is the hosted
        # redirect today rather than a form. A federated user CANNOT authenticate through
        # InitiateAuth or any SDK call; only the managed-login endpoints can complete that
        # exchange. A custom form built now would be thrown away on the day Okta arrives.
        #
        # Local accounts are therefore SCAFFOLDING, expected to be replaced by federated
        # ones rather than to accumulate.
        #
        # No credential appears here, in CDK, or in the template - a password in a template
        # is a password in the console, the change set and the stack events. The stack
        # prints the CLI commands instead and a human runs them per account.
        auth_pool = cognito.UserPool(
            self,
            "ChatUserPool",
            # SELF-SIGNUP OFF, and now for two reasons rather than one. It still keeps
            # strangers out of a paid endpoint, and it is also what "accounts are issued,
            # not claimed" means mechanically: with Okta arriving, a self-enrolled local
            # account would be a second identity for a person who already has one.
            self_sign_up_enabled=False,
            # PLAIN USERNAME, not email. `username=True` alone emits neither
            # UsernameAttributes nor AliasAttributes, which is what lets an administrator
            # create whatever name the campus uses - an email-only pool rejects a name that
            # is not an address, and an alias pool rejects one that looks like one.
            #
            # SIGN-IN OPTIONS ARE IMMUTABLE AFTER CREATION. Changing this later REPLACES
            # the pool, so it is left exactly as it was: this change swaps who holds
            # accounts, not how the pool is keyed, and a replacement would throw away every
            # account for no gain. Federated users are keyed by the provider anyway.
            sign_in_aliases=cognito.SignInAliases(username=True),
            # No self-service recovery: local accounts carry no verified email or phone to
            # send a code to, and while they are scaffolding an administrator moving a
            # password is the whole recovery story. NONE synthesizes admin_only.
            account_recovery=cognito.AccountRecovery.NONE,
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            # NO CUSTOM ATTRIBUTES, deliberately, and this is a decision rather than an
            # omission. A federated profile is rebuilt from the provider's claims on every
            # sign-in, so anything this application writes into a Cognito attribute is
            # liable to be silently overwritten by Okta later. Application data belongs in
            # DynamoDB keyed by `sub`; the pool holds identity only.
            #
            # One-click install implies one-click uninstall.
            removal_policy=RemovalPolicy.DESTROY,
        )

        # THE MANAGED LOGIN DOMAIN - the hosted endpoints the browser is redirected to.
        # This is the piece that makes the whole flow federation-ready: /oauth2/authorize
        # is the only place an Okta round trip can happen, so having it now means adding
        # SJSU's IdP later touches no application code.
        #
        # The prefix must be globally unique across all AWS accounts, and nothing in this
        # repo may hardcode a global name - so it is derived from the stack's own id. The
        # last group of the CloudFormation stack GUID is lowercase hex, which is exactly
        # the character set a domain prefix allows, and a fresh install in another account
        # gets its own without anyone choosing one.
        stack_unique_suffix = Fn.select(
            4, Fn.split("-", Fn.select(2, Fn.split("/", self.stack_id)))
        )
        login_domain = auth_pool.add_domain(
            "ChatLoginDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"sjsu-navigator-{stack_unique_suffix}"
            ),
        )

        # THE OKTA SAML IDENTITY PROVIDER, created ONLY when config.yaml carries a metadata
        # URL. resolve_okta returns None otherwise and everything below falls back to
        # COGNITO alone, so the local-accounts deployment that exists today synthesizes an
        # identical pool. Same gating shape as the cost panel: the absence of a value is the
        # switch, not a flag beside it.
        #
        # THE PROVIDER NAME IS `Okta` AND MUST NEVER CHANGE (infra/config.py,
        # OKTA_PROVIDER_NAME). A federated user's Cognito username is
        # `<providerName>_<nameid>`, so renaming it mints a new user with a new `sub` - the
        # DynamoDB partition key - and orphans every conversation the old identity wrote.
        # ProviderName is also the resource's physical id, so CloudFormation REPLACES rather
        # than updates it. It is named for the provider's role, never for whose org is
        # behind it, which is what lets the rehearsal tenant and SJSU's share it.
        #
        # SP-INITIATED ONLY. `idp_initiated=False` is passed explicitly rather than left to
        # the default: IdP-initiated SAML has no request for the response to be bound to, so
        # an unsolicited assertion is accepted on its own, which is a login-CSRF primitive.
        # Every sign-in here starts at /oauth2/authorize, so nothing needs it.
        okta_cfg = resolve_okta(config)
        okta_provider = None
        if okta_cfg is not None:
            okta_provider = cognito.UserPoolIdentityProviderSaml(
                self,
                "OktaIdentityProvider",
                user_pool=auth_pool,
                name=okta_cfg["provider_name"],
                # A URL, not a file: Cognito re-fetches it, so the IdP's signing certificate
                # rotating on the Okta side is not an outage waiting on a manual re-upload.
                metadata=cognito.UserPoolIdentityProviderSamlMetadata.url(
                    okta_cfg["metadata_url"]
                ),
                idp_initiated=False,
                # ONE MAPPING, and the two halves come from different places on purpose: the
                # Okta-side attribute name is config (orgs spell it differently), and what it
                # maps to is this pool's own standard `email`.
                #
                # NO USERNAME MAPPING, which is not an omission - Cognito derives the
                # username from the SAML NameID and does not accept a mapping for it. An
                # attempt to map one is rejected at CreateIdentityProvider.
                attribute_mapping=cognito.AttributeMapping(
                    email=cognito.ProviderAttribute.other(okta_cfg["email_attribute"]),
                ),
            )

        # TWO APP CLIENTS ON ONE POOL, because the two callers cannot share one.
        #
        # THE WEB CLIENT: authorization code flow with PKCE, through the hosted endpoints
        # above. Public, no secret - the frontend is JavaScript in a browser, so a secret
        # would be readable by anyone who views source. PKCE is what makes that safe and it
        # is NOT configured here because it cannot be: Cognito requires a code_challenge
        # from any client that has no secret, so the protection is a property of the client
        # being public plus the frontend actually sending one (frontend/src/lib/auth.ts).
        # There is no CloudFormation property to turn it on or off.
        #
        # No password flow on this client AT ALL. That is the load-bearing half of the
        # split: leaving one enabled would let a form quietly reappear that federated users
        # could never sign in through, which is the exact dead end this change exists to
        # avoid.
        #
        # THIS is the client Okta attaches to, and the only one - see the eval client below.
        web_client = auth_pool.add_client(
            "ChatWebClient",
            generate_secret=False,
            # Pinned to refresh-only through the L1 below rather than set here - see the
            # note after this construct.
            auth_flows=cognito.AuthFlow(),
            # SPELLED OUT IN BOTH DIRECTIONS, never left to the default, and this is the
            # same class of finding as ExplicitAuthFlows below. CDK fills an omitted
            # SupportedIdentityProviders from every provider REGISTERED ON THE POOL, so
            # leaving it out would attach Okta to whichever clients happened not to name a
            # list - a property that changes when an unrelated construct is added elsewhere.
            #
            # COGNITO stays in the list beside Okta rather than being replaced by it: local
            # accounts are how this stack is administered and evaluated, and dropping them
            # the moment federation lands would strand every account created so far behind
            # an IdP that does not know them.
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO,
                *(
                    [cognito.UserPoolClientIdentityProvider.custom(
                        okta_cfg["provider_name"]
                    )]
                    if okta_cfg is not None
                    else []
                ),
            ],
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=True,
                    # IMPLICIT OFF. It returns tokens in the URL fragment, where they land
                    # in history and in any Referer - the reason the code flow exists.
                    implicit_code_grant=False,
                    client_credentials=False,
                ),
                # openid is what makes this an OIDC exchange at all. email and profile are
                # here so the sidebar can show who is signed in; NEITHER is an identity -
                # the server reads `sub` (see the authorizer note below).
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                # Local dev only at this point. The deployed CloudFront origin is appended
                # in section 6, as a deploy-time token - see the note there.
                callback_urls=list(local_redirect_urls),
                logout_urls=list(local_redirect_urls),
            ),
            # One day, matching "one sign-in covers the session", and Cognito's maximum for
            # an access token. The frontend holds the token in memory only, so a reload
            # signs in again regardless.
            access_token_validity=Duration.days(1),
            # The frontend never uses the refresh token it is handed; pinning its validity
            # to the access token's means the copy it discards cannot outlive the session
            # by the 30-day default.
            refresh_token_validity=Duration.days(1),
            id_token_validity=Duration.days(1),
            prevent_user_existence_errors=True,
        )

        # EXPLICIT AUTH FLOWS, SPELLED OUT, and this is a finding rather than tidiness.
        # An empty AuthFlow() makes CDK omit ExplicitAuthFlows entirely, and an ABSENT
        # ExplicitAuthFlows does not mean "no direct sign-in" - Cognito falls back to its
        # legacy defaults, which include SRP. The web client would then quietly accept a
        # local-password sign-in after all, which is the dead end this split exists to
        # close: SRP is a path no federated user can ever use.
        #
        # ALLOW_REFRESH_TOKEN_AUTH is the only entry, and it is not a sign-in path - it is
        # what lets the token endpoint honour a refresh grant for a session that already
        # authenticated through the redirect.
        web_client.node.default_child.explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]

        # ORDERING, DECLARED RATHER THAN INFERRED. The provider is named in the list above
        # as the LITERAL string "Okta", not as a Ref, so CloudFormation sees no reference
        # between these two resources and is free to create the client first - which fails
        # the update with "identity provider Okta does not exist". A literal is worth that
        # explicit edge: it keeps the one name that can never change spelled where a reader
        # (and a test) can see it, instead of behind a token.
        if okta_provider is not None:
            web_client.node.add_dependency(okta_provider)

        # THE EVAL CLIENT: password auth, no OAuth, no callback URLs. eval/run_eval.py runs
        # headless - there is no browser to redirect and no human to click - so the one
        # thing the web client deliberately cannot do is the only thing this one does.
        #
        # Separate rather than one permissive client because a client is the unit Cognito
        # applies flows to: a single client serving both would carry an enabled password
        # flow that any browser could call with the client id from config.json. Splitting
        # them means the harness's path is not reachable from the web app's credentials.
        eval_client = auth_pool.add_client(
            "ChatEvalClient",
            generate_secret=False,
            # No secret, so the harness's one unsigned InitiateAuth needs no SECRET_HASH.
            auth_flows=cognito.AuthFlow(user_password=True),
            # COGNITO ALONE, PINNED, and this line is load-bearing rather than decorative.
            # CDK defaults an omitted SupportedIdentityProviders to every provider REGISTERED
            # ON THE POOL, so creating the Okta provider above silently added it to this
            # client (verified against a synthesized template, not assumed). This is the
            # machine account's client: it authenticates a password against a local user and
            # has no browser to redirect, so a federated provider on it is reachable by
            # nobody and widens the client for nothing.
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
            access_token_validity=Duration.days(1),
            refresh_token_validity=Duration.days(1),
            id_token_validity=Duration.days(1),
            prevent_user_existence_errors=True,
            disable_o_auth=True,
        )

        # THE EVAL CLIENT IS EXEMPT FROM THE PER-USER DAILY MESSAGE CAP, and this is the
        # wiring that says so. eval/run_eval.py fires all 82 ground-truth questions as ONE
        # account at concurrency 3, so any per-user cap worth having is a cap the harness
        # trips - and a tripped harness does not fail loudly, it records refusals as answers
        # and the eval reads as a regression in the model.
        #
        # KEYED ON THE APP CLIENT, NOT THE USERNAME. A Cognito ACCESS token carries
        # `client_id` (see the authorizer note below - it is why the audience list works at
        # all), API Gateway has already validated that claim against the audience allowlist
        # by the time the function runs, and the two clients are exactly the two callers.
        # So the exemption rides on the same validated claim set as `sub` rather than on a
        # name, and a browser cannot claim it: the web client is not in this list and the
        # harness's client id is useless without the password flow only it enables.
        #
        # EXEMPT rather than "a far higher limit", deliberately. A number here would have to
        # be kept above whatever eval/ground-truth.yaml grows to, and the failure of getting
        # that wrong is silent in the same way. This is a machine account a human starts by
        # hand with a password nobody else has; it is not the thing the cap exists to bound.
        #
        # Set AFTER the fact rather than in the environment block above, because the client
        # is created here in section 5 and the function in section 4. add_environment keeps
        # this a token reference to the real client id, so nothing is spelled twice.
        #
        # PLURAL, so a second machine client is a config edit rather than a code change.
        chat_lambda.add_environment(
            "RATE_LIMIT_EXEMPT_CLIENT_IDS", eval_client.user_pool_client_id
        )

        # CORS is locked to the origins in config.yaml, never "*" (rejected at synth).
        # Note what this is and is not: CORS is enforced by BROWSERS ONLY, so it is not a
        # security boundary - curl ignores it entirely, and the throttle plus the JWT gate
        # remain the real controls. What it does buy is stopping a third-party page from
        # driving this billable endpoint from its visitors' browsers.
        #
        # The CloudFront distribution's own origin is appended at bullet 9, as a deploy-time
        # token rather than a hardcoded domain (docs/build-plan.md: this section is not
        # frozen at its commit).
        #
        # `Authorization` is in allow_headers because POST /chat is gated: the frontend sets
        # that header from JavaScript, which makes every /chat call PREFLIGHTED. Leave it
        # out and the browser fails at the OPTIONS before the POST is ever sent - and the
        # symptom is a CORS error, which reads like a configuration problem rather than the
        # auth problem it is.
        # Spelled ONCE. Section 6 re-emits the whole CORS block through the L1 escape
        # hatch to append the CloudFront origin, and a second literal here would be a
        # silent way for the two to drift - dropping Authorization on the amended block
        # would break every /chat call at the preflight.
        # PATCH and DELETE join the list for the two conversation-management routes. Both
        # are cross-origin calls from the site to the API and both carry Authorization, so
        # both are preflighted: leave either out and the browser fails at the OPTIONS, which
        # surfaces as a CORS error rather than as the missing method it is.
        cors_allow_methods = ["POST", "GET", "PATCH", "DELETE", "OPTIONS"]
        cors_allow_headers = ["Content-Type", "Authorization"]
        self._cors_allow_origins = list(cors_allow_origins)
        self._cors_allow_methods = cors_allow_methods
        self._cors_allow_headers = cors_allow_headers

        http_api = apigwv2.HttpApi(
            self,
            "ChatHttpApi",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=self._cors_allow_origins,
                allow_methods=[
                    apigwv2.CorsHttpMethod[m] for m in cors_allow_methods
                ],
                allow_headers=cors_allow_headers,
            ),
        )

        chat_integration = apigwv2_integrations.HttpLambdaIntegration(
            "ChatIntegration", chat_lambda
        )

        # NATIVE JWT authorizer - no authorizer Lambda, so nothing to cold-start, nothing
        # extra to pay for, and none of our code in the auth decision. API Gateway fetches
        # the pool's JWKS and validates signature, issuer, audience and expiry itself.
        #
        # THE AUDIENCE IS THE APP CLIENT ID, which works because of a documented quirk: a
        # Cognito ACCESS token carries no `aud` claim, it carries `client_id`, and API
        # Gateway validates client_id only when aud is absent. The frontend deliberately
        # sends the access token, not the ID token (which carries account attributes for no
        # benefit here). Do not "fix" this to an ID token.
        #
        # BOTH CLIENTS ARE LISTED, and omitting either breaks a caller completely: the
        # audience is an allowlist of client_id values, so an eval token presented against
        # a web-client-only audience is rejected at the gateway with a 401 that carries no
        # CORS headers and no explanation. Splitting the clients above is only safe because
        # this list was widened in the same change.
        #
        # Widening the audience does NOT widen who gets in. Every token here is still
        # signed by this pool for a user in it; what the second entry admits is the eval
        # account, which is a user in the pool by construction.
        jwt_authorizer = apigwv2_authorizers.HttpJwtAuthorizer(
            "ChatJwtAuthorizer",
            f"https://cognito-idp.{self.region}.amazonaws.com/{auth_pool.user_pool_id}",
            jwt_audience=[
                web_client.user_pool_client_id,
                eval_client.user_pool_client_id,
            ],
            identity_source=["$request.header.Authorization"],
        )

        # POST /chat is THE billable route: every Bedrock call, every guardrail unit and
        # every retrieval hangs off it, so it is the one that is gated.
        http_api.add_routes(
            path="/chat",
            methods=[apigwv2.HttpMethod.POST],
            integration=chat_integration,
            authorizer=jwt_authorizer,
        )

        # THE TWO READ ROUTES: a student's own conversation list, and one conversation for
        # display (docs/accounts-and-storage.md, Storage access patterns). Same function,
        # same authorizer, and the authorizer is not optional here even though these spend
        # no Bedrock tokens: the `sub` claim IS the partition key, so an ungated read would
        # not be a cheap route, it would be a route with nobody to attribute - and the
        # handler's only alternative would be trusting a user id off the wire.
        #
        # Every route on this API is gated, which is what makes
        # test_every_route_is_jwt_gated a one-line assertion over all of them rather than
        # an allowlist somebody has to remember to extend.
        http_api.add_routes(
            path="/conversations",
            methods=[apigwv2.HttpMethod.GET],
            integration=chat_integration,
            authorizer=jwt_authorizer,
        )
        http_api.add_routes(
            # The braces are API Gateway's path-parameter syntax, and the name inside them
            # is what arrives at the Lambda as pathParameters.conversationId - so this
            # string and app/handler.get_conversation have to agree letter for letter.
            path="/conversations/{conversationId}",
            methods=[apigwv2.HttpMethod.GET],
            integration=chat_integration,
            authorizer=jwt_authorizer,
        )

        # THE TWO WRITE ROUTES on one conversation: rename it, and delete it with every
        # message under it. Same function, same authorizer, same path, and the authorizer
        # matters MORE here than on the reads it sits beside: these change stored data, and
        # the only thing that decides whose data is the `sub` claim the partition key is
        # built from. An ungated delete would be a delete with nobody to attribute.
        #
        # A separate add_routes call rather than more methods on the GET above, because the
        # GET is a read of a projection and these are writes; keeping them apart is what
        # makes each route's comment true of every method under it.
        http_api.add_routes(
            path="/conversations/{conversationId}",
            methods=[apigwv2.HttpMethod.PATCH, apigwv2.HttpMethod.DELETE],
            integration=chat_integration,
            authorizer=jwt_authorizer,
        )

        # Throttling via the stage's DEFAULT route settings, which apply to every route on
        # the stage - /chat today and anything added later, which is the safer default for
        # a paid endpoint than an allowlist of routes somebody has to remember to extend.
        #
        # NOT the per-route `RouteSettings` map, and that is a finding rather than a
        # preference: passing a RouteSettingsProperty as a map VALUE renders its keys in
        # camelCase (`throttlingRateLimit`) instead of CloudFormation's PascalCase, so the
        # throttle would deploy silently unapplied. Verified in the synthesized template
        # against aws-cdk-lib 2.260.0; DefaultRouteSettings renders correctly.
        # test_the_stage_throttle_renders_cloudformation_property_names pins it.
        default_stage = http_api.default_stage.node.default_child
        default_stage.default_route_settings = apigwv2.CfnStage.RouteSettingsProperty(
            throttling_rate_limit=http_api_cfg["throttling_rate_limit"],
            throttling_burst_limit=http_api_cfg["throttling_burst_limit"],
        )

        # RESERVED CONCURRENCY on the chat function. This is the control the request-rate
        # throttle cannot provide: rate bounds how many invocations START, and with a
        # 29-second budget a sustained 10 rps leaves hundreds running at once. It also
        # reserves capacity from the account pool, so the scraper cannot starve chat and
        # chat cannot starve the scraper.
        cfn_chat = chat_lambda.node.default_child
        cfn_chat.reserved_concurrent_executions = http_api_cfg[
            "chat_reserved_concurrency"
        ]

        self._http_api = http_api
        chat_url = f"{http_api.api_endpoint}/chat"
        self._chat_url = chat_url
        # The conversation READ endpoints' base. Stamped into config.json beside the chat
        # URL rather than derived in the browser by string-surgery on it: the frontend
        # would have to strip "/chat" and re-append, which is a rule about this stack's
        # route names living in a file this stack does not build.
        conversations_url = f"{http_api.api_endpoint}/conversations"

        CfnOutput(
            self,
            "ChatApiUrl",
            value=chat_url,
            description="HTTP API POST /chat endpoint (requires a Cognito access token).",
        )
        CfnOutput(
            self,
            "ConversationsApiUrl",
            value=conversations_url,
            description=(
                "HTTP API GET /conversations, and /conversations/{id} under it. Lists only "
                "the caller's own (requires a Cognito access token)."
            ),
        )
        CfnOutput(
            self,
            "ChatUserPoolId",
            value=auth_pool.user_pool_id,
            description="Cognito user pool gating POST /chat.",
        )
        CfnOutput(
            self,
            "ChatWebClientId",
            value=web_client.user_pool_client_id,
            description=(
                "Cognito app client id the browser redirects with (public, no secret, "
                "authorization code + PKCE). The frontend reads this from config.json."
            ),
        )
        CfnOutput(
            self,
            "ChatEvalClientId",
            value=eval_client.user_pool_client_id,
            description=(
                "Cognito app client id for eval/run_eval.py (password auth, no OAuth). "
                "NOT the browser's client - see ChatWebClientId."
            ),
        )
        CfnOutput(
            self,
            "ChatLoginDomain",
            value=login_domain.base_url(),
            description="Cognito managed login domain the frontend redirects to.",
        )
        # Accounts are created by CLI, with the deployer's own credentials, because CDK
        # cannot do it without putting a password in the template. Printed spelled out so
        # the commands run as-is.
        #
        # Two commands, for two different kinds of account. The human one is a template to
        # run per person; the eval one is run once for the harness.
        CfnOutput(
            self,
            "ChatCreateUserCommand",
            value=(
                "aws cognito-idp admin-create-user"
                f" --region {self.region}"
                f" --user-pool-id {auth_pool.user_pool_id}"
                f" --username {_HUMAN_USERNAME_PLACEHOLDER}"
                " --message-action SUPPRESS"
            ),
            description=(
                "Run once PER PERSON - self-signup is off, so every account is issued. "
                "Then set a password with ChatSetPasswordCommand. Replaced wholesale when "
                "SJSU's IdP is federated into this pool."
            ),
        )
        CfnOutput(
            self,
            "ChatSetPasswordCommand",
            value=(
                "aws cognito-idp admin-set-user-password"
                f" --region {self.region}"
                f" --user-pool-id {auth_pool.user_pool_id}"
                f" --username {_HUMAN_USERNAME_PLACEHOLDER}"
                " --password 'CHOOSE-A-PASSWORD' --permanent"
            ),
            description=(
                "--permanent is REQUIRED: without it the account stays in "
                "FORCE_CHANGE_PASSWORD, and managed login answers the redirect with a "
                "forced password change instead of returning a code."
            ),
        )
        CfnOutput(
            self,
            "ChatCreateEvalUserCommand",
            value=(
                "aws cognito-idp admin-create-user"
                f" --region {self.region}"
                f" --user-pool-id {auth_pool.user_pool_id}"
                f" --username {_EVAL_USERNAME}"
                " --message-action SUPPRESS"
                " && aws cognito-idp admin-set-user-password"
                f" --region {self.region}"
                f" --user-pool-id {auth_pool.user_pool_id}"
                f" --username {_EVAL_USERNAME}"
                " --password 'CHOOSE-A-PASSWORD' --permanent"
            ),
            description=(
                "Run ONCE for the eval harness. Its password goes in EVAL_PASSWORD (or "
                "--password-file), never in this repo."
            ),
        )

        # --- WebSocket streaming API (OPTIONAL, and off by default) --------------
        #
        # DELIBERATELY UNNUMBERED, like the chat history table above: it is not a gav pull,
        # so putting it in a sequence that means "gav's section order" would be a lie. It
        # sits HERE, between 5 and 6, for a mechanical reason - section 6 stamps config.json,
        # and the browser's choice of transport is the presence of this API's URL in it.
        #
        # THE GATE IS THE WHOLE FEATURE (config.yaml `streaming`). resolve_streaming returns
        # None when the block is absent or disabled, and then NOTHING below is synthesized:
        # no API, no authorizer, no functions, no layer, and no `streamingApiUrl` in
        # config.json - so the frontend has nothing to open and every student stays on
        # POST /chat, which none of this touches.
        #
        # WHY A WEBSOCKET. API Gateway response streaming is REST-API only and this is an
        # HTTP API; Lambda response streaming is Node.js/custom-runtime only and the agent
        # loop is Python. In-band streaming means rewriting the loop in another language.
        # This keeps the Python and moves the stream out of band: ConverseStream on the way
        # in, post_to_connection on the way out.
        streaming_cfg = resolve_streaming(config)
        streaming_api_url = None

        if streaming_cfg is not None:
            # THE AUTHORIZER'S OWN DEPS LAYER, separate from the chat function's rather
            # than a line added to it. Two reasons: PyJWT's `crypto` extra pulls in
            # `cryptography`, a large compiled wheel the chat function has no use for, and
            # POST /chat is under a hard "keeps working unchanged" constraint - changing
            # its layer changes its deployed artifact for a feature it does not run.
            #
            # `_requirements_hash` folds the layer NAME into the asset hash, which is what
            # keeps this out of the collision the chat layer's long note describes: three
            # layers with the same image, command and platform would otherwise share a
            # cache key and silently reuse each other's bundle.
            connect_authorizer_layer = _lambda.LayerVersion(
                self,
                "ConnectAuthorizerDepsLayer",
                description=(
                    "PyJWT + cryptography (manylinux x86_64) for the WebSocket $connect "
                    "authorizer's RS256 verification."
                ),
                compatible_runtimes=[_LAMBDA_PYTHON],
                compatible_architectures=[_LAMBDA_ARCH],
                code=_lambda.Code.from_asset(
                    str(_APP_DIR),
                    asset_hash=_requirements_hash(
                        "connect-authorizer", _APP_DIR / "requirements-authorizer.txt"
                    ),
                    asset_hash_type=AssetHashType.CUSTOM,
                    exclude=["*", ".*", "!requirements-authorizer.txt"],
                    bundling=BundlingOptions(
                        image=_LAMBDA_PYTHON.bundling_image,
                        local=_PipManylinuxLayerBundler(
                            _APP_DIR / "requirements-authorizer.txt"
                        ),
                        command=[
                            "bash",
                            "-c",
                            "pip install -r requirements-authorizer.txt "
                            "--target /asset-output/python",
                        ],
                        platform="linux/amd64",
                    ),
                ),
            )

            connect_authorizer_log_group = logs.LogGroup(
                self,
                "ConnectAuthorizerLogGroup",
                retention=logs.RetentionDays.THREE_MONTHS,
                removal_policy=RemovalPolicy.DESTROY,
            )

            # THE ONE PIECE OF OUR CODE IN AN AUTH DECISION ANYWHERE IN THIS STACK, and it
            # exists only because a WebSocket API cannot use the native JWT authorizer the
            # HTTP API uses: REQUEST (Lambda) is the only authorizer type it accepts, and
            # `$connect` is the only route one can be attached to.
            #
            # NO IAM GRANTS BEYOND LOGGING. It reaches exactly one thing - the pool's public
            # JWKS document over https - which needs no credentials. An authorizer that
            # could read the history table would be an authorizer that could be made to.
            connect_authorizer_lambda = _lambda.Function(
                self,
                "ConnectAuthorizerFunction",
                runtime=_LAMBDA_PYTHON,
                architecture=_LAMBDA_ARCH,
                handler="ws_authorizer.lambda_handler",
                # ONE FILE. It imports nothing from the rest of app/, which is the point:
                # the module that decides who a caller is should not be able to reach the
                # store, the model or the guardrail.
                code=_lambda.Code.from_asset(
                    str(_APP_DIR), exclude=["*", ".*", "!ws_authorizer.py"]
                ),
                layers=[connect_authorizer_layer],
                # Short: it fetches a cached JWKS and verifies one signature. A slow
                # authorizer is a slow handshake, in front of a student.
                timeout=Duration.seconds(10),
                memory_size=512,
                log_group=connect_authorizer_log_group,
                environment={
                    "COGNITO_REGION": self.region,
                    "USER_POOL_ID": auth_pool.user_pool_id,
                    # THE SAME ALLOWLIST THE HTTP API'S AUTHORIZER CARRIES as its audience,
                    # and deliberately the same rather than narrowed to the web client. Two
                    # doors into one pool with two different answers to "which clients?" is
                    # how one of them ends up wrong; the eval harness cannot reach a browser
                    # socket anyway, and its token is a token from this pool either way.
                    "ALLOWED_CLIENT_IDS": Fn.join(
                        ",",
                        [
                            web_client.user_pool_client_id,
                            eval_client.user_pool_client_id,
                        ],
                    ),
                },
            )

            # THE TOKEN TRAVELS IN THE QUERY STRING, and that is measured rather than
            # preferred. The better option is `Sec-WebSocket-Protocol`, which keeps the
            # token out of URLs entirely and which API Gateway DOES accept as an identity
            # source - but it never echoes the subprotocol back in its 101 response, and RFC
            # 6455 says a client whose requested subprotocol is not echoed must fail the
            # connection. Probed against a deployed WebSocket API on 2026-08-12: the 101
            # came back with no `Sec-WebSocket-Protocol` header, and Chrome 151 and Node's
            # WHATWG WebSocket both fired `error` without opening. Query string it is.
            #
            # THE IDENTITY SOURCE IS LOAD-BEARING IN A WAY THAT BITES: if it is ABSENT from
            # the request, API Gateway does not invoke the authorizer at all. The handshake
            # is still refused (probed: HTTP 401), but a browser cannot read the status of a
            # failed upgrade, so in JS it surfaces as an opaque error and a 1006 close.
            connect_authorizer = apigwv2_authorizers.WebSocketLambdaAuthorizer(
                "ChatConnectAuthorizer",
                connect_authorizer_lambda,
                identity_source=["route.request.querystring.token"],
            )

            # The routes' own function: $connect and $disconnect record and clear the
            # connection. It runs the SAME application modules as the chat function and
            # therefore takes the same environment (see chat_environment above).
            streaming_route_log_group = logs.LogGroup(
                self,
                "ChatStreamRouteLogGroup",
                retention=logs.RetentionDays.THREE_MONTHS,
                removal_policy=RemovalPolicy.DESTROY,
            )

            streaming_route_role = iam.Role(
                self,
                "ChatStreamRouteRole",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                description=(
                    "Execution role for the WebSocket route function (connect/disconnect)."
                ),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AWSLambdaBasicExecutionRole"
                    )
                ],
            )
            # Exactly the two actions the connection record needs, scoped to the one table.
            # Not the chat role's list: this function does not read a conversation, does not
            # query, and must not be able to. Spelled out for the reason the chat role's
            # note gives - `grant_read_write_data` would bring dynamodb:Scan, the one
            # operation that takes no partition key.
            streaming_route_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["dynamodb:PutItem", "dynamodb:DeleteItem"],
                    resources=[chat_history_table.table_arn],
                )
            )

            # THE GENERATION WORKER: the half of a streamed turn that takes twenty seconds.
            # It is a separate function because the route function is behind API Gateway and
            # inherits the same 29-second integration ceiling POST /chat has, while the agent
            # loop can use most of it. This one is invoked asynchronously and talks back down
            # the connection, so the ceiling stops applying to it.
            streaming_worker_log_group = logs.LogGroup(
                self,
                "ChatStreamWorkerLogGroup",
                retention=logs.RetentionDays.THREE_MONTHS,
                removal_policy=RemovalPolicy.DESTROY,
            )

            # It runs the same turn the chat function runs, so it holds the same grants -
            # taken from the same role rather than a second copy, because a streamed turn
            # that could reach less than a buffered one would fail somewhere subtle.
            streaming_worker_role = iam.Role(
                self,
                "ChatStreamWorkerRole",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                description="Execution role for the WebSocket generation worker.",
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AWSLambdaBasicExecutionRole"
                    )
                ],
            )
            for statement in _chat_turn_statements:
                streaming_worker_role.add_to_policy(statement)

            streaming_worker_lambda = _lambda.Function(
                self,
                "ChatStreamWorkerFunction",
                runtime=_LAMBDA_PYTHON,
                architecture=_LAMBDA_ARCH,
                handler="stream_worker.lambda_handler",
                code=_lambda.Code.from_asset(
                    str(_APP_DIR),
                    exclude=[
                        "*",
                        ".*",
                        "!stream_worker.py",
                        "!streaming.py",
                        # The preview's own bookkeeping - the offset, the batching and the
                        # safe prefix - lifted out of streaming.py so the FastAPI app can
                        # use the same one. streaming.py imports it at module scope.
                        "!preview.py",
                        "!settings.py",
                        "!models.py",
                        "!prompts.py",
                        "!tools.py",
                        "!retrieve.py",
                        "!cards.py",
                        "!safety.py",
                        "!escalation.py",
                        "!places.py",
                        "!campus_data.py",
                        "!orchestrator.py",
                        "!campus_time.py",
                        "!history.py",
                        "!titles.py",
                        "!usage.py",
                        "!ratelimit.py",
                    ],
                ),
                # Same pair as the chat function, and for the same reason: this one runs
                # the agent loop too, so it imports places.py, safety.py and prompts.py.
                layers=[chat_deps_layer, campus_data_layer],
                role=streaming_worker_role,
                # Longer than the chat function's 29 seconds because nothing is waiting on
                # an HTTP response here - but the LOOP's budget is unchanged
                # (chat.converse_deadline_seconds, read by stream_worker), so this is
                # headroom for the writes, the title and the final push, not a longer answer.
                timeout=Duration.seconds(60),
                memory_size=1024,
                log_group=streaming_worker_log_group,
                environment=dict(chat_environment),
            )
            streaming_worker_lambda.node.add_dependency(chat_history_table)
            streaming_worker_lambda.node.add_dependency(knowledge_base)

            # ZERO RETRIES ON THE ASYNCHRONOUS INVOKE, and this is the single most important
            # property of this resource. Lambda retries a failed async invocation TWICE by
            # default, and a retried generation worker answers the same question again -
            # down the same socket, billing a full agent loop each time, with the student
            # watching a second reply type itself over the first. Nothing in the turn is
            # idempotent enough to survive that.
            streaming_worker_lambda.configure_async_invoke(retry_attempts=0)

            streaming_route_lambda = _lambda.Function(
                self,
                "ChatStreamRouteFunction",
                runtime=_LAMBDA_PYTHON,
                architecture=_LAMBDA_ARCH,
                handler="streaming.lambda_handler",
                code=_lambda.Code.from_asset(
                    str(_APP_DIR),
                    exclude=[
                        "*",
                        ".*",
                        "!streaming.py",
                        # Same reason as the worker's: streaming.py's ConnectionSink is a
                        # PreviewSink now, and it imports it at module scope.
                        "!preview.py",
                        "!settings.py",
                        "!history.py",
                        "!models.py",
                        "!usage.py",
                        "!ratelimit.py",
                        "!cards.py",
                        # cards.py imports retrieve.py at module scope. cards is here for
                        # preview_safe_prefix ALONE - the stop rule that keeps markup off a
                        # student's screen - and nothing on this function parses a card.
                        "!retrieve.py",
                    ],
                ),
                layers=[chat_deps_layer],
                role=streaming_route_role,
                # It writes one or two DynamoDB items and makes one guardrail call. The model
                # is the worker's problem.
                timeout=Duration.seconds(15),
                memory_size=512,
                log_group=streaming_route_log_group,
                environment={
                    **chat_environment,
                    "STREAM_WORKER_FUNCTION_NAME": streaming_worker_lambda.function_name,
                },
            )
            streaming_route_lambda.node.add_dependency(chat_history_table)

            # The route function screens input with the same guardrail POST /chat uses, and
            # starts the worker. Both grants are narrow and named.
            streaming_route_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["bedrock:ApplyGuardrail"],
                    resources=[input_guardrail.attr_guardrail_arn],
                )
            )
            streaming_route_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["dynamodb:UpdateItem"],
                    resources=[chat_history_table.table_arn],
                )
            )
            streaming_worker_lambda.grant_invoke(streaming_route_lambda)

            streaming_api = apigwv2.WebSocketApi(
                self,
                "ChatStreamingApi",
                # The frame field that picks a route. `$request.body.action` is the
                # convention API Gateway documents, and it is what makes a message route a
                # named thing rather than everything falling into $default.
                route_selection_expression="$request.body.action",
                connect_route_options=apigwv2.WebSocketRouteOptions(
                    integration=apigwv2_integrations.WebSocketLambdaIntegration(
                        "ChatStreamConnectIntegration", streaming_route_lambda
                    ),
                    # $connect IS THE ONLY ROUTE THAT CAN CARRY AN AUTHORIZER, which is not
                    # a gap: a WebSocket connection is authorized once, at the handshake,
                    # and API Gateway carries this authorizer's `context` onto every later
                    # route on that connection (verified against a deployed probe), so the
                    # message route reads `sub` from a validated token rather than a frame.
                    authorizer=connect_authorizer,
                ),
                disconnect_route_options=apigwv2.WebSocketRouteOptions(
                    integration=apigwv2_integrations.WebSocketLambdaIntegration(
                        "ChatStreamDisconnectIntegration", streaming_route_lambda
                    ),
                ),
            )

            # THE MESSAGE ROUTE, named rather than left to $default. The API's route
            # selection expression is `$request.body.action`, so a frame saying
            # {"action": "sendMessage", ...} lands here and a frame naming nothing lands on
            # $default - which this API deliberately does not define, so an unrecognised
            # frame is refused instead of falling into the billable path. That is the same
            # instinct app/handler.py's unknown-routeKey 404 has.
            #
            # NO AUTHORIZER, because a WebSocket API cannot put one here - and does not need
            # to. The connection was authorized at $connect and API Gateway carries that
            # authorizer's context onto this route, so every frame arrives with a `sub` that
            # came out of a verified token.
            streaming_api.add_route(
                "sendMessage",
                integration=apigwv2_integrations.WebSocketLambdaIntegration(
                    "ChatStreamMessageIntegration", streaming_route_lambda
                ),
            )

            # NO ACCESS LOGGING, and with a token in the query string that is a decision
            # rather than an omission. API Gateway access logs are built from an explicit
            # `$context.*` format string, so a token could only appear in one by being put
            # there - but the surest way for that not to happen is for there to be no log.
            # The HTTP API alongside this one configures none either.
            streaming_stage = apigwv2.WebSocketStage(
                self,
                "ChatStreamingStage",
                web_socket_api=streaming_api,
                stage_name=_STREAMING_STAGE_NAME,
                auto_deploy=True,
                # The same fence the HTTP stage carries, from the same config numbers: this
                # endpoint reaches the same paid model calls, so it gets the same throttle
                # rather than a second set of numbers to keep in step.
                throttle=apigwv2.ThrottleSettings(
                    rate_limit=http_api_cfg["throttling_rate_limit"],
                    burst_limit=http_api_cfg["throttling_burst_limit"],
                ),
            )

            # THERE IS NO AUTHORIZER RESULT CACHE TO CONFIGURE HERE, and the absence of the
            # property IS the setting. `AuthorizerResultTtlInSeconds` is documented as
            # "Supported only for HTTP API Lambda authorizers"
            # (AWS::ApiGatewayV2::Authorizer, checked 2026-08-12), and API Gateway does not
            # merely ignore it on this protocol - it refuses the resource:
            #
            #   AuthorizerResultTtlInSeconds cannot be set for WEBSOCKET protocol Apis.
            #   (Service: AmazonApiGatewayV2; Status Code: 400; BadRequestException)
            #
            # That is a real CREATE_FAILED from a real deploy (2026-08-12), and it happens
            # for the value 0 as loudly as for 300, so this cannot be a smaller number - it
            # has to be no property at all. An earlier revision walked the construct tree
            # and set it to 0 to disable caching; that is the line that failed to deploy.
            # CDK itself never emits it here: WebSocketLambdaAuthorizer takes no TTL
            # argument, only HttpLambdaAuthorizer does, so nothing is being suppressed by
            # leaving it out. Whether API Gateway caches a WebSocket authorizer's answer at
            # all is not documented either way; what is certain is that there is no knob,
            # so the token in the identity source is not protected by one. What bounds the
            # exposure instead is the connection model: the authorizer runs at the
            # handshake, and app/ws_authorizer.py verifies the signature and the expiry on
            # every one of them.

            # PUSHING BACK DOWN THE CONNECTION. Both functions send frames - the route one
            # for a guardrail block or a rate-limit refusal, the worker for the whole
            # streamed reply - so both need ManageConnections on this API and both need the
            # stage's callback endpoint.
            #
            # THE ENDPOINT COMES FROM THE STACK, never from the event. A WebSocket event
            # carries `domainName` and `stage`, and assembling the URL a reply is sent to out
            # of request data is how a request ends up choosing its own destination.
            for function in (streaming_route_lambda, streaming_worker_lambda):
                streaming_api.grant_manage_connections(function)
                function.add_environment("STREAM_CALLBACK_URL", streaming_stage.callback_url)
                function.add_environment(
                    "STREAM_DELTA_MIN_CHARS", str(streaming_cfg["delta_min_chars"])
                )
                function.add_environment(
                    "STREAM_DELTA_MAX_DELAY_MS", str(streaming_cfg["delta_max_delay_ms"])
                )

            # The route function applies the per-user daily cap, so it needs the same
            # exemption list the chat function carries - and by reference to the same client,
            # so the harness cannot be exempt on one transport and capped on the other.
            streaming_route_lambda.add_environment(
                "RATE_LIMIT_EXEMPT_CLIENT_IDS", eval_client.user_pool_client_id
            )

            # PRESENT ONLY WHEN IT IS ON, the same spelling of off the rest of this stack
            # uses. When it is on, app/stream_worker.py can only build a `sync` guardrail
            # config - `async` is not representable there - because async releases text to
            # the student before it has been scanned.
            if streaming_cfg["output_guardrail"]:
                streaming_worker_lambda.add_environment("STREAM_OUTPUT_GUARDRAIL", "true")

            streaming_api_url = streaming_stage.url

            CfnOutput(
                self,
                "ChatStreamingApiUrl",
                value=streaming_api_url,
                description=(
                    "WebSocket endpoint for progressive replies (requires a Cognito access "
                    "token as ?token=). The frontend reads this from config.json; when the "
                    "key is absent it uses POST /chat."
                ),
            )

        # --- The streaming app: FastAPI + Lambda Web Adapter + Function URL -----
        #
        # UNNUMBERED AND DELIBERATELY ALONE, like the history table above: it is not a gav
        # pull, so numbering it would put it in a sequence that means "gav's section
        # order". It sits HERE, straight after the WebSocket section, because it is the
        # mechanism that section exists to work around.
        #
        # WHAT IT IS FOR. The banner above says "Lambda response streaming is
        # Node.js/custom-runtime only and the agent loop is Python". That is true of the
        # python3.13 MANAGED RUNTIME and false of Lambda. The Lambda Web Adapter is an
        # execution wrapper: AWS_LAMBDA_EXEC_WRAPPER points the runtime at /opt/bootstrap
        # from the layer, that script execs run.sh out of the bundle, run.sh starts
        # uvicorn, and the adapter - registered as an extension by the same layer - proxies
        # each invocation to it over HTTP. In response_stream mode it writes the response
        # body out as the app produces it. That is the supported way to stream in band from
        # Python (aws/aws-lambda-web-adapter, examples/fastapi-response-streaming-zip).
        #
        # IT NOW SERVES THE REAL TURN. POST /chat on this app runs app/turn.py - the same
        # rate limit, guardrail, write, read, loop, write and title the API Gateway handler
        # runs, out of the same module - and streams the reply as it is written, with the
        # last frame carrying the identical ChatResponse. The two probe routes stay beside
        # it on purpose: when a stream arrives in one lump the question is always
        # "transport, Bedrock, or the turn?", and /stream and /model answer the first two
        # without a deploy-and-guess cycle.
        #
        # NOTHING ABOVE CHANGES, WHICH IS THE POINT OF THIS BEING A SEPARATE FUNCTION. The
        # socket keeps its three functions, its authorizer, its connection table, its grants
        # and its config.json key, and stays the live path the frontend opens. Removing any
        # of it is a later step and not this one: until a front door is chosen, this
        # endpoint answers 401 to everybody (see app/stream_probe.py, identity_from), so it
        # cannot be what a student reaches.
        #
        # ZIP PLUS THE LAYER, NEVER A CONTAINER IMAGE, and that is where the two upstream
        # examples get spliced. fastapi-backend-only-response-streaming is the one that
        # streams from a backend behind a signed URL, and it is PackageType: Image;
        # fastapi-response-streaming-zip is the one packaged as a zip with the adapter as a
        # layer. This takes the packaging from the second and the posture from the first,
        # because every bundle in this stack builds from source at synth and an image would
        # add an ECR repository to the account and an image build to CI for a binary AWS
        # already publishes as a layer.
        #
        # UNGATED, unlike the socket, which is a judgement rather than an oversight. A gate
        # would be a config.yaml key, a resolver, a validator, a second synth direction and
        # a case in test_config.py - all guarding a function that costs nothing until it is
        # invoked and that nobody can invoke without credentials. It is removed by deleting
        # this block, which is the same edit turning its key off would have been.

        # ITS OWN DEPS LAYER, for the reason the authorizer's has one: the chat function
        # and the stream worker are under a hard "keeps working unchanged" constraint, and
        # FastAPI in their layer would change their deployed artifact for a function they
        # do not run. `_requirements_hash` folds the layer NAME into the asset hash, which
        # is what keeps this fourth pip-built layer out of the CDK asset-cache collision
        # the chat layer's long note describes.
        stream_probe_deps_layer = _lambda.LayerVersion(
            self,
            "StreamProbeDepsLayer",
            description=(
                "FastAPI + uvicorn (manylinux x86_64) for the Lambda Web Adapter "
                "response-streaming probe."
            ),
            compatible_runtimes=[_LAMBDA_PYTHON],
            compatible_architectures=[_LAMBDA_ARCH],
            code=_lambda.Code.from_asset(
                str(_APP_DIR),
                # BOTH FILES, because the outer one pulls the inner one in with `-r`. A
                # hash over only the outer file would leave a changed pydantic pin staged
                # as the previous build.
                asset_hash=_requirements_hash(
                    "stream-probe",
                    _APP_DIR / "requirements-stream-probe.txt",
                    _APP_DIR / "requirements.txt",
                ),
                asset_hash_type=AssetHashType.CUSTOM,
                exclude=[
                    "*",
                    ".*",
                    "!requirements-stream-probe.txt",
                    # Named because the file above reads it, not because the exclude is
                    # what feeds the build: bundling bind-mounts the whole directory, so
                    # this list shapes the declared inputs rather than the container's view.
                    "!requirements.txt",
                ],
                bundling=BundlingOptions(
                    image=_LAMBDA_PYTHON.bundling_image,
                    local=_PipManylinuxLayerBundler(
                        _APP_DIR / "requirements-stream-probe.txt"
                    ),
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements-stream-probe.txt "
                        "--target /asset-output/python",
                    ],
                    platform="linux/amd64",
                ),
            ),
        )

        # THE ADAPTER ITSELF, referenced by ARN rather than built: it is a Rust binary AWS
        # publishes per region and per architecture, and its layer policy grants
        # lambda:GetLayerVersion to everyone. The account, the name and the version are
        # module constants (see _LWA_LAYER_NAME, which is derived from _LAMBDA_ARCH so the
        # two cannot drift); the region and partition come from the stack, so a fresh
        # install in another region attaches its own region's copy.
        lambda_web_adapter_layer = _lambda.LayerVersion.from_layer_version_arn(
            self,
            "LambdaWebAdapterLayer",
            f"arn:{self.partition}:lambda:{self.region}:{_LWA_LAYER_ACCOUNT}"
            f":layer:{_LWA_LAYER_NAME}:{_LWA_LAYER_VERSION}",
        )

        stream_probe_log_group = logs.LogGroup(
            self,
            "StreamProbeLogGroup",
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        stream_probe_lambda = _lambda.Function(
            self,
            "StreamProbeFunction",
            runtime=_LAMBDA_PYTHON,
            architecture=_LAMBDA_ARCH,
            # NOT a dotted module.function, and this is the one property of this resource
            # that looks like a mistake and is not. /opt/bootstrap is two lines:
            #
            #     exec -- "${LAMBDA_TASK_ROOT}/${_HANDLER}"
            #
            # so the handler is a PATH under the bundle root, and the file it names has to
            # be there and has to be executable. app/run.sh carries mode 100755 in git;
            # CDK's asset staging copies it with fs.copyFileSync (which preserves the mode)
            # and the CDK CLI zips it with the file's st_mode in the entry's external
            # attributes, so the bit survives to /var/task. A dropped execute bit is a
            # clean deploy and a Permission denied on every invocation, which is why
            # test_the_stream_probe_ships_an_executable_run_sh reads the staged mode.
            handler="run.sh",
            # THE WHOLE TURN'S MODULES NOW, because POST /chat on this app runs the same
            # app/turn.py the API Gateway handler runs. Spelled file by file like every
            # other bundle here, with ".*" for the dotfile trap the scraper section
            # documents - and pinned by
            # test_the_stream_probe_ships_every_module_it_imports, which walks
            # stream_probe.py's import graph rather than trusting this list to be re-read.
            #
            # It is the chat function's list plus three: run.sh (the adapter's entry point),
            # stream_probe.py (the app) and preview.py (the sink's bookkeeping, which the
            # buffered handler has no use for). handler.py is deliberately NOT here: it is
            # the API Gateway transport, and this function is a different one.
            code=_lambda.Code.from_asset(
                str(_APP_DIR),
                exclude=[
                    "*",
                    ".*",
                    "!run.sh",
                    "!stream_probe.py",
                    "!preview.py",
                    "!turn.py",
                    "!settings.py",
                    "!models.py",
                    "!prompts.py",
                    "!tools.py",
                    "!retrieve.py",
                    "!cards.py",
                    "!safety.py",
                    "!escalation.py",
                    "!places.py",
                    # The reader of the repo-root data/ directory, for the same reason it is
                    # in the chat function's list: places.py, safety.py and prompts.py all
                    # import it at module scope, so a bundle without it is a cold start that
                    # dies on the import line.
                    "!campus_data.py",
                    "!orchestrator.py",
                    "!campus_time.py",
                    "!history.py",
                    "!titles.py",
                    "!usage.py",
                    "!ratelimit.py",
                ],
            ),
            # Order does not matter: the adapter layer owns /opt/bootstrap and
            # /opt/extensions, the deps layer owns /opt/python, and nothing overlaps.
            #
            # THE DATA LAYER RIDES ALONG for the reason the chat function carries it: this
            # app runs the same turn, so places.py, safety.py and prompts.py read the
            # repo-root data/ CSVs at import, and Lambda extracts that layer to /opt where
            # campus_data.py looks first.
            layers=[
                stream_probe_deps_layer,
                lambda_web_adapter_layer,
                campus_data_layer,
            ],
            # NO ROLE ARGUMENT, so CDK builds one with AWSLambdaBasicExecutionRole and
            # nothing else - the same shape the $connect authorizer has, and for the same
            # reason. The probe reaches no model, no store and no guardrail, so it must not
            # be able to.
            #
            # NO RESERVED CONCURRENCY either: that takes capacity out of the account pool,
            # and the chat function is the only thing here entitled to fence some off.
            timeout=Duration.seconds(60),
            # 1024 MB, RAISED from 512 when this stopped being a probe and started serving
            # the turn: it is the chat function's number, for the chat function's reason.
            # Lambda scales CPU with memory, this now imports FastAPI, uvicorn, pydantic
            # AND the whole agent loop at cold start, and every second of that comes out of
            # the 22 seconds the loop has to work in.
            memory_size=1024,
            log_group=stream_probe_log_group,
            environment={
                # THE CHAT FUNCTION'S OWN ENVIRONMENT, whole, because app/settings.py's
                # identity variables have no defaults: load_settings() raises unless all
                # seven are present, and the probe reads its model id and region out of
                # exactly that object. Taking a subset would mean a second, smaller idea of
                # what Settings needs, which is the thing that goes stale.
                #
                # AN ENVIRONMENT VARIABLE IS NOT A GRANT. This block names the history
                # table and the knowledge base; the role below can reach neither, and the
                # probe imports nothing that would try. What it buys is that the model id
                # the probe streams from is the model id this deployment configures.
                **chat_environment,
                # The wrapper the layer ships. Without it the runtime looks for a Python
                # handler called "run.sh" and the function never starts.
                "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
                # HALF OF THE STREAMING SWITCH, and the half that lives in the adapter.
                # Lower case: this is the adapter's own enum ("buffered" or
                # "response_stream"), not the Function URL's InvokeMode below, which is
                # SCREAMING_SNAKE. Set only one of the two and the function still works -
                # it just buffers - which is exactly the failure a probe has to be able to
                # tell apart from a broken app, so both are asserted.
                "AWS_LWA_INVOKE_MODE": "response_stream",
                # Read twice: by uvicorn in run.sh, and by the adapter as the traffic port
                # it forwards to and readiness-checks.
                "PORT": str(_LWA_PROBE_PORT),
            },
        )
        # THE SAME EXEMPTION LIST THE OTHER TWO TRANSPORTS CARRY, and by reference to the
        # same client. This function applies the per-user daily cap now (app/turn.py step
        # 1), so the eval harness has to be exempt here exactly as it is on POST /chat and
        # on the socket - a harness that is exempt on one transport and capped on another
        # is a run that fails halfway for a reason nobody would look for.
        stream_probe_lambda.add_environment(
            "RATE_LIMIT_EXEMPT_CLIENT_IDS", eval_client.user_pool_client_id
        )
        # It retrieves from the KB and reads and writes the history table on every turn, so
        # neither may be created after it. Both env values are already references, so
        # CloudFormation would order these anyway; stated for the reason the chat function
        # states them, rather than relying on a reference a later refactor could flatten.
        stream_probe_lambda.node.add_dependency(knowledge_base)
        stream_probe_lambda.node.add_dependency(chat_history_table)

        # THE CHAT TURN'S GRANTS, THE SAME LIST AND FROM THE SAME PLACE the WebSocket
        # worker takes them (`_chat_turn_statements`): retrieve from the knowledge base,
        # invoke the generation and titling models, apply the input guardrail, and read and
        # write the one conversation table. It runs the same turn, so it holds the same
        # grants - a streamed turn that could reach less than a buffered one would fail
        # somewhere subtle and only for some questions.
        #
        # THIS REPLACED A DELIBERATELY NARROWER STATEMENT, and the widening is the whole
        # content of this stage. When this function only streamed a bare ConverseStream it
        # held one action (`bedrock:InvokeModelWithResponseStream`) on the one configured
        # model; that grant is SUBSUMED here, because the list below carries
        # `bedrock:InvokeModel*` on the same model ARNs. Keeping both would read like two
        # different permissions, which is the reason the titling grant is skipped when its
        # model id matches the generation one.
        #
        # WHAT IT STILL DOES NOT HOLD, and must not: `lambda:InvokeFunction` (there is no
        # generation worker to start - the turn runs in this process) and
        # `execute-api:ManageConnections` (nothing here pushes down a socket). Those are the
        # two grants the WebSocket path needs and this one exists to make unnecessary.
        for statement in _chat_turn_statements:
            stream_probe_lambda.add_to_role_policy(statement)

        # THE OTHER HALF OF THE STREAMING SWITCH. InvokeMode is a property of the URL, not
        # of the function, and Lambda buffers the whole body without it no matter what the
        # adapter is configured to do.
        #
        # AWS_IAM, AND AuthType: NONE IS FORBIDDEN HERE. The upstream examples use NONE
        # because they are demos; on this account an unauthenticated Function URL is an
        # endpoint any stranger can bill us through, with none of the four fences POST
        # /chat sits behind. IAM means a caller needs SigV4 over credentials that carry
        # lambda:InvokeFunctionUrl for this ARN. It also decides what is NOT in the
        # template: CDK attaches the anonymous lambda:InvokeFunctionUrl permission only
        # when AuthType is NONE, so no AWS::Lambda::Permission is emitted for this URL at
        # all, and test_the_stream_probe_url_is_iam_signed_and_open_to_nobody pins that.
        #
        # WHAT THE BROWSER EVENTUALLY TALKS TO IS UNDECIDED and out of scope. A student's
        # browser holds a Cognito token, not SigV4 credentials, so this URL is not yet a
        # front door - it is a thing the deployer and the eval harness can sign a request
        # to, which is all a probe needs.
        stream_probe_url = stream_probe_lambda.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.AWS_IAM,
            invoke_mode=_lambda.InvokeMode.RESPONSE_STREAM,
        )

        CfnOutput(
            self,
            "StreamProbeFunctionUrl",
            value=stream_probe_url.url,
            description=(
                "IAM-signed Function URL for the streaming chat app. POST /chat streams a "
                "full turn as NDJSON frames; GET /stream and GET /model are the transport "
                "and Bedrock probes. Every request must be SigV4-signed for service "
                "'lambda', and POST /chat additionally needs a validated JWT sub in the "
                "request context - which an IAM Function URL does not carry, so it answers "
                "401 until a front door is chosen. Nothing else in this stack references it."
            ),
        )

        # --- 6. Site delivery: S3 + CloudFront (OAC) + Astro + config.json -------
        #
        # THE BUILD: the Astro app in frontend/ is compiled IN A CONTAINER at synth (see
        # _astro_bundling) and its dist/ is what lands in the bucket. dist/ is gitignored
        # and never committed - the build is reproducible from source and a lockfile, so a
        # checked-in dist/ could only ever be a second source of truth that goes stale.
        #
        # Camp's chat UI replaces frontend/src at bullet 11; the pipeline that carries it
        # is established here, against a minimal placeholder page, so that commit is a
        # source swap rather than a build-system bring-up.
        #
        # ROUTING: camp's app is MULTI-PAGE, not a single shell. It has three Astro pages
        # (index, login, auth/callback), and Astro's default build format emits them as
        # directories - /index.html, /login/index.html, /auth/callback/index.html. So a
        # blanket SPA fallback would be WRONG here twice over: it is not a single shell,
        # and rewriting every miss to index.html with a 200 would mask real 404s as a
        # blank-looking home page. What a REST (OAC) origin actually needs is directory
        # -index resolution, because unlike an S3 website endpoint it does not resolve
        # /login/ to /login/index.html on its own - it returns 403. That is what the
        # CloudFront Function below does, and only that.

        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            # OAC requires ACLs disabled (bucket-owner-enforced). Default for new buckets;
            # set explicitly so the OAC requirement is legible.
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Directory-index resolution at the edge. A REST origin (which OAC requires) has no
        # index-document behaviour, so without this every path but "/" 403s.
        #   "/"            -> handled by default_root_object, not here
        #   "/login/"      -> "/login/index.html"
        #   "/login"       -> "/login/index.html"   (extensionless, so not a real file)
        #   "/_astro/x.js" -> untouched             (has an extension)
        # A path that still does not exist 403s, and the error responses below turn that
        # into an honest 404 rather than a 200 carrying the home page.
        directory_index_function = cloudfront.Function(
            self,
            "SiteDirectoryIndexFunction",
            comment="Resolve directory paths to index.html for the S3 REST (OAC) origin.",
            code=cloudfront.FunctionCode.from_inline(
                """
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.endsWith('/')) {
    request.uri = uri + 'index.html';
  } else if (!uri.includes('.')) {
    request.uri = uri + '/index.html';
  }
  return request;
}
"""
            ),
        )

        site_distribution = cloudfront.Distribution(
            self,
            "SiteDistribution",
            comment="SJSU Student Success Navigator web app.",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=directory_index_function,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            # A missing key on a REST origin comes back as 403 (the bucket policy grants
            # GetObject only, so S3 cannot distinguish "absent" from "forbidden" without
            # ListBucket). Both are mapped to a real 404 STATUS - deliberately NOT to
            # index.html with a 200, which is the blanket-fallback anti-pattern: it would
            # make every typo look like a working page that failed to render.
            # BOTH response_http_status AND response_page_path, always. CloudFront
            # rejects a status without a page outright - "Both or neither of ResponseCode
            # and ResponsePagePath must be specified" - and it rejects it at CREATE time,
            # not at synth, so this cost a failed deploy and a rollback before it was
            # caught. The L1 does not validate it and neither did the first version of
            # test_a_missing_page_is_a_404_and_not_a_blanket_spa_fallback, which asserted
            # ResponsePagePath was ABSENT and so pinned the broken shape in place.
            #
            # /404.html is a real page Astro builds (frontend/src/pages/404.astro), served
            # WITH a 404 status. That is still not the blanket fallback: the distinction
            # that matters is the status code, not whether a page is named. Serving
            # index.html with a 200 would tell the browser the typo'd URL is a real page.
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=404,
                    response_page_path="/404.html",
                    ttl=Duration.minutes(5),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=404,
                    response_page_path="/404.html",
                    ttl=Duration.minutes(5),
                ),
            ],
        )

        site_url = f"https://{site_distribution.distribution_domain_name}"

        # TWO DEPLOYMENTS INTO ONE BUCKET, and the split is load-bearing rather than tidy.
        #
        # BucketDeployment defaults to prune=True, which is `aws s3 sync --delete`: each
        # deployment removes destination objects its own source does not contain. Two
        # unscoped deployments therefore fight, and whichever CloudFormation runs last
        # wins - so the site deployment excludes config.json from its prune, and the
        # config deployment does not prune at all.
        #
        # They also need DIFFERENT cache-control, which is the actual reason they cannot be
        # one deployment: config.json carries the API URL, so a cached copy pins a stale
        # endpoint after a redeploy - exactly the failure that makes one-click install a
        # lie. It is no-store; the site content is revalidated.
        s3deploy.BucketDeployment(
            self,
            "SiteContentDeployment",
            destination_bucket=site_bucket,
            sources=[
                s3deploy.Source.asset(
                    str(_FRONTEND_DIR),
                    # Host build products stay out of the container: node_modules is
                    # reinstalled from the lockfile inside it, and a copied dist/ could
                    # shadow the fresh build.
                    exclude=["node_modules", "dist", ".astro", "README.md"],
                    bundling=_astro_bundling(),
                )
            ],
            distribution=site_distribution,
            distribution_paths=["/*"],
            # Scopes the prune so this deployment cannot delete the config.json the other
            # one owns.
            exclude=["config.json"],
            # no-cache means REVALIDATE, not "do not store": the browser may keep the copy
            # but must check it is current. HTML is cheap to revalidate and must never be
            # served stale after a deploy. (Immutable caching for Astro's hashed /_astro/
            # assets is a v2 performance item, not a correctness one.)
            cache_control=[s3deploy.CacheControl.no_cache()],
        )

        # config.json - the ONLY thing that tells the frontend where its API is, stamped at
        # DEPLOY time from stack tokens. Source.jsonData resolves those tokens during
        # deployment, so nothing in the committed frontend names an account, a region, an
        # API id or a pool id, and a fresh install in another account discovers its own.
        s3deploy.BucketDeployment(
            self,
            "SiteConfigDeployment",
            destination_bucket=site_bucket,
            sources=[
                s3deploy.Source.json_data(
                    "config.json",
                    {
                        "chatApiUrl": chat_url,
                        # The history reads. One base URL, not two: the single-conversation
                        # route is this plus "/<id>", which is the one bit of URL assembly
                        # the frontend does and the only alternative to a second key that
                        # could drift from this one.
                        "conversationsApiUrl": conversations_url,
                        # The frontend redirects with these. The WEB client, never the
                        # eval one: this file is world-readable by design, and the eval
                        # client id in it would publish a password-auth endpoint.
                        "userPoolId": auth_pool.user_pool_id,
                        "userPoolClientId": web_client.user_pool_client_id,
                        # Where the browser is sent for /oauth2/authorize and /logout.
                        # There is no redirect URI here on purpose - the frontend derives
                        # it from window.location.origin, so the same config.json is
                        # correct on localhost and on the distribution, and the two can
                        # never disagree about the exact string Cognito matches.
                        "loginDomain": login_domain.base_url(),
                        "region": self.region,
                        # The cost breakdown's whole model - published rates, measured
                        # usage, measured baseline - or the key omitted entirely when
                        # cost_model.enabled is false. THE OMISSION IS THE GATE: the
                        # frontend renders the cost SECTION inside its settings panel only
                        # when this key is present, so the breakdown comes off with a
                        # config edit and a deploy rather than a code change. (Settings
                        # itself is always there - it holds the student's language choice -
                        # and with no key it simply has one section fewer.) That is what
                        # has to be true before Okta federation starts provisioning SJSU
                        # students into this pool just in time - a student must not be
                        # shown what the system costs to run.
                        #
                        # Nothing here is account spend. Every figure is a published list
                        # rate times usage measured against THIS stack, so the number
                        # cannot silently blend in another project sharing the account.
                        **(
                            {"costModel": cost_model}
                            if (cost_model := resolve_cost_model(config)) is not None
                            else {}
                        ),
                        # The WebSocket endpoint for progressive replies, or the key
                        # omitted entirely when streaming is off. THE OMISSION IS THE GATE
                        # again, and here it does something stronger than hide a control:
                        # the frontend picks its TRANSPORT from whether this key is present
                        # (frontend/src/lib/chatStream.ts), so a build that never receives
                        # it cannot open a socket at all - there is no URL for it to guess.
                        # Turning streaming off is a config edit, and what it turns off is
                        # the whole path rather than a branch inside it.
                        **(
                            {"streamingApiUrl": streaming_api_url}
                            if streaming_api_url is not None
                            else {}
                        ),
                        # Where an escalation draft is addressed, or the key omitted
                        # entirely when no recipient is configured. THE OMISSION IS THE
                        # GATE a third time, and what it gates here is whether the
                        # component exists in the browser at all: with no recipient the
                        # frontend renders no escalation UI even if a response somehow
                        # carried a draft (frontend/src/components/ConversationTurnView.tsx).
                        #
                        # The address is not secret - it is a published campus mailbox, and
                        # every draft shows it to the student before they send anything - so
                        # a world-readable config.json is the right place for it.
                        **(
                            {"escalationRecipient": escalation_cfg["recipient"]}
                            if escalation_cfg is not None
                            else {}
                        ),
                    },
                )
            ],
            distribution=site_distribution,
            distribution_paths=["/config.json"],
            # PRUNE OFF: this deployment's source is one file, so pruning would delete the
            # entire site the other deployment just wrote.
            prune=False,
            # no-store, not no-cache: this file pins the API endpoint, and a stale copy
            # points the app at an endpoint that may no longer exist.
            cache_control=[s3deploy.CacheControl.no_store()],
        )

        # THE API SECTION REOPENS HERE (docs/build-plan.md: it is not frozen at its
        # commit). The app is served from the CloudFront domain, so the browser sends that
        # origin on every /chat call - without it in the allowlist the API is unreachable
        # from the only place it is meant to be reached from.
        #
        # It is a DEPLOY-TIME TOKEN, never a hardcoded domain, so a fresh install in
        # another account allowlists its own distribution. The L1 escape hatch replaces the
        # whole CorsConfiguration block, which is why the methods and headers above are
        # spelled once and reused rather than re-typed here.
        cfn_api = http_api.node.default_child
        cfn_api.cors_configuration = apigwv2.CfnApi.CorsProperty(
            allow_origins=[*self._cors_allow_origins, site_url],
            allow_methods=self._cors_allow_methods,
            allow_headers=self._cors_allow_headers,
        )

        # THE AUTH SECTION REOPENS HERE TOO, for the same reason and by the same mechanism.
        # Cognito will only send the browser back to a REGISTERED callback URL, and the
        # deployed one is the CloudFront domain, which does not exist until section 6
        # creates the distribution. So the web client is built above with the local dev
        # entries and the deployed origin is appended here as a deploy-time token - never a
        # hardcoded domain, so a fresh install redirects to its own site.
        #
        # This is an ORDERING constraint, not a dependency cycle: the distribution does not
        # reference the client, so appending here adds no edge CloudFormation has to
        # resolve backwards.
        #
        # Sign-out is in the same list because it is the same kind of redirect. Leave the
        # logout URL out and Cognito refuses the /logout call, which strands the browser on
        # an error page with its pool session still live - so the next sign-in returns a
        # code without ever asking who the student is, and "sign out" was a lie.
        cfn_web_client = web_client.node.default_child
        deployed_redirect_urls = [*local_redirect_urls, f"{site_url}/"]
        cfn_web_client.callback_ur_ls = deployed_redirect_urls
        cfn_web_client.logout_ur_ls = deployed_redirect_urls

        CfnOutput(
            self,
            "SiteUrl",
            value=site_url,
            description="CloudFront URL for the web app.",
        )

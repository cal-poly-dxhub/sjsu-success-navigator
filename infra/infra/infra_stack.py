"""SJSU Student Success Navigator infrastructure stack.

Each "Copy over and morph" bullet in docs/build-plan.md lands its gav section into the
banner below that names it, in this order (mirroring gav's own section order so a pull is
a copy + rename, not a re-architecture):

  1. Vector store + Knowledge Base + S3 data source   ("pull gav kb section")      DONE
  2. Scraper Lambda + deps layer + daily schedule
     + on-deploy install trigger                      ("pull gav scraper shell")   DONE
  3. Bedrock Guardrail (PROMPT_ATTACK input screen)   (docs/synthesis.md decision)
  4. Chat Lambda + role + deps layer                  ("pull gav lambda section")
  5. Cognito pool + client + JWT authorizer
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
    Duration,
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
    resolve_data_source_name,
    resolve_generation,
    resolve_cors_allow_origins,
    resolve_guardrail,
    resolve_http_api,
    resolve_knowledge_base,
    resolve_request,
    resolve_retrieval,
    resolve_scraper,
    resolve_seed_pages,
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

# The ONE shared pilot login's username, spelled into the two setup commands the stack
# prints so they are copy-paste runnable. It is a NAME, not a credential - the password is
# chosen by the deployer at step 2 and never appears here or anywhere in the repo. The pool
# signs in by plain username, so this needs no @ and carries no email attribute.
_PILOT_USERNAME = "sjsupilot" 


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
            "src public /tmp/build/ && "
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


def _requirements_hash(layer: str, requirements: Path) -> str:
    """A stable asset hash for a deps layer, keyed on its OWN requirements file.

    Exists to keep the two deps layers from colliding in CDK's asset cache - see the long
    note on the chat layer. The layer name is folded in as well as the file contents, so
    two layers would stay distinct even if their requirements were byte-identical.
    """
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()[:32]
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
            description="trafilatura/lxml/regex/httpx (manylinux x86_64) for the scraper Lambda.",
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

        # THE CRAWL LIST AS A SECOND LAYER, and the one place this stack diverges from gav.
        #
        # Gav ships its 19 seed URLs to the Lambda inside the SCRAPER_TIERS environment variable.
        # That does not survive the scale-up: Lambda caps all environment variables at 4 KB in
        # AGGREGATE (a hard limit, not a soft default, and not raisable by a quota request), and
        # this crawl list is 19 KB as compact JSON of url/section pairs - 2.9 KB even gzipped and
        # base64'd, which is most of the budget for one variable and leaves the list unreadable.
        #
        # So the file travels as content and only its FILENAME goes in the environment. A layer
        # rather than the function bundle because the list lives at the REPO ROOT (a decision: it
        # is content, not infra) while the function's asset is scraper/ - from_asset takes one
        # directory, and pointing it at the root would bundle the entire repo.
        #
        # Both the asset include and the URL_LIST_FILE env below read the SAME config value, so the
        # file the layer carries and the file the handler opens cannot drift apart.
        url_list_file = scraper_cfg["url_list_file"]
        seed_list_layer = _lambda.LayerVersion(
            self,
            "ScraperSeedListLayer",
            description=(
                f"The curated crawl list ({len(seed_pages)} pages) as a bundled asset - too "
                "large for Lambda's 4 KB environment-variable budget."
            ),
            compatible_runtimes=[_LAMBDA_PYTHON],
            compatible_architectures=[_LAMBDA_ARCH],
            # Layer content extracts to /opt at runtime, which is where seed_list_path looks.
            #
            # ".*" is NOT redundant with "*", and leaving it out is a real leak. CDK's exclude
            # globbing does not match hidden entries with a leading-wildcard pattern, so "*" alone
            # ships .git/, .gitignore and .DS_Store from the repo root inside a deployed layer.
            # Verified by listing the staged asset; test_seed_list_layer_ships_only_the_crawl_list
            # pins it, and the function asset below needs the same treatment.
            code=_lambda.Code.from_asset(
                str(_REPO_ROOT),
                exclude=["*", ".*", f"!{url_list_file}"],
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
            layers=[scraper_deps_layer, seed_list_layer],
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

        # --- 3. Guardrail: PROMPT_ATTACK input screen (after safety intercept) ---
        #
        # ONE guardrail, screening PROMPT_ATTACK on the input side. It is applied via
        # ApplyGuardrail(source=INPUT) on the bare query in the handler; nothing is attached to
        # Converse, so there is no output guardrail here to find.
        #
        # THE ORDERING IS LOAD-BEARING (docs/synthesis.md, docs/architecture-v1.py:146): the
        # deterministic safety intercept runs FIRST, in-Lambda, before this screen and before any
        # model call. A guardrail block returns a fixed refusal string - so if it ran first, a
        # student in crisis whose message also tripped the screen would get the refusal instead of
        # the crisis panel. app/handler.py enforces the order and
        # test_safety_intercept_runs_before_the_guardrail pins it.
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
        # Storage). INFRASTRUCTURE ONLY: nothing reads or writes this table yet. It lands
        # first precisely so the slice that does is pure application code, with no CDK diff
        # tangled into it - and so the shape below is argued once, here, rather than being
        # decided in passing by whoever writes the first PutItem.
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
        # Retrieve chunks from the KB. Scoped to the one knowledge base.
        chat_lambda_role.add_to_policy(
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
            chat_lambda_role.add_to_policy(
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
            chat_lambda_role.add_to_policy(
                iam.PolicyStatement(
                    actions=[
                        "bedrock:GetInferenceProfile",
                        "bedrock:ListInferenceProfiles",
                    ],
                    resources=["*"],
                )
            )
        else:
            chat_lambda_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel"],
                    resources=[
                        f"arn:{self.partition}:bedrock:{self.region}"
                        f"::foundation-model/{generation_model_id}"
                    ],
                )
            )
        # ApplyGuardrail on the ONE guardrail: the standalone input screen (source=INPUT).
        # Nothing is attached to Converse, so there is no second ARN to grant.
        chat_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:ApplyGuardrail"],
                resources=[input_guardrail.attr_guardrail_arn],
            )
        )
        # Read and write the conversation history. NOTHING USES THIS YET - the table and its
        # grant land ahead of the application slice on purpose (see the section above).
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
        chat_lambda_role.add_to_policy(
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
                    "!settings.py",
                    "!models.py",
                    "!prompts.py",
                    "!tools.py",
                    "!retrieve.py",
                    "!cards.py",
                    "!safety.py",
                    "!orchestrator.py",
                ],
            ),
            layers=[chat_deps_layer],
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
            environment={
                "KNOWLEDGE_BASE_ID": knowledge_base.attr_knowledge_base_id,
                "GENERATION_MODEL_ID": generation_model_id,
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
                # The loop's WALL-CLOCK budget. The iteration cap bounds how many model
                # calls happen, not how long they take, so without this a slow run is
                # killed mid-Converse - billed, with no response reaching the student.
                # Validated at synth to sit under the function timeout above.
                "CONVERSE_DEADLINE_SECONDS": str(chat_cfg["converse_deadline_seconds"]),
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
                # The conversation history table. Read by no code yet - it arrives with the
                # table so the application slice is pure application code. By REFERENCE, not
                # as the literal from config.yaml: a copy here would be a second place the
                # name is spelled, and the two would drift the moment either changed.
                "CHAT_HISTORY_TABLE_NAME": chat_history_table.table_name,
            },
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

        # A Cognito user pool holding ONE shared pilot login, created by hand after deploy.
        # This is gav's pattern carried over deliberately: campus-affiliated accounts are a
        # v2 item (docs/synthesis.md), and a shared login is the cheapest thing that keeps
        # a public URL from spending Bedrock tokens for anyone who finds it.
        #
        # No credential appears here, in CDK, or in the template - a password in a template
        # is a password in the console, the change set and the stack events. The stack
        # prints the two CLI commands instead and a human runs them once.
        auth_pool = cognito.UserPool(
            self,
            "ChatUserPool",
            # SELF-SIGNUP OFF. The pool exists to keep strangers OUT of a paid endpoint;
            # letting them enrol themselves would defeat the entire gate.
            self_sign_up_enabled=False,
            # PLAIN USERNAME, not email. `username=True` alone emits neither
            # UsernameAttributes nor AliasAttributes, which is what lets a plain name like
            # "sjsupilot" be created: an email-only pool rejects a name that is not an
            # address. This is a shared pilot login handed over in a sentence, not a
            # person's account, so a fake address would be one more thing to explain.
            #
            # SIGN-IN OPTIONS ARE IMMUTABLE AFTER CREATION. Changing this later REPLACES
            # the pool, which changes the pool id the frontend is built with - so it is
            # settled here, before the first deploy, or not cheaply at all.
            sign_in_aliases=cognito.SignInAliases(username=True),
            # No self-service recovery: there is no verified email or phone on this account
            # to send a code to, and nobody but the deployer should be able to move its
            # password. NONE synthesizes admin_only.
            account_recovery=cognito.AccountRecovery.NONE,
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            # One-click install implies one-click uninstall.
            removal_policy=RemovalPolicy.DESTROY,
        )

        # PUBLIC client, no secret: the frontend is JavaScript in a browser, so a client
        # secret would be readable by anyone who views source - theatre, and Cognito
        # rejects the unsigned browser call unless the client is public anyway.
        #
        # USER_PASSWORD_AUTH rather than SRP: the frontend does ONE unsigned fetch to
        # cognito-idp with no SDK and no build step. SRP needs big-integer crypto no
        # dependency-free client is going to carry. The tradeoff is that the password
        # crosses the wire inside TLS instead of never leaving the browser; for a shared
        # pilot login behind a link that is the right trade, and it is NOT a pattern for
        # real student accounts (which is the v2 item).
        auth_client = auth_pool.add_client(
            "ChatUserPoolClient",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_password=True),
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
            disable_o_auth=True,
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
        cors_allow_methods = ["POST", "GET", "OPTIONS"]
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
        jwt_authorizer = apigwv2_authorizers.HttpJwtAuthorizer(
            "ChatJwtAuthorizer",
            f"https://cognito-idp.{self.region}.amazonaws.com/{auth_pool.user_pool_id}",
            jwt_audience=[auth_client.user_pool_client_id],
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

        CfnOutput(
            self,
            "ChatApiUrl",
            value=chat_url,
            description="HTTP API POST /chat endpoint (requires a Cognito access token).",
        )
        CfnOutput(
            self,
            "ChatUserPoolId",
            value=auth_pool.user_pool_id,
            description="Cognito user pool gating POST /chat.",
        )
        CfnOutput(
            self,
            "ChatUserPoolClientId",
            value=auth_client.user_pool_client_id,
            description="Cognito app client id the frontend signs in with (public, no secret).",
        )
        # The pilot account is created by CLI, with the deployer's own credentials, because
        # CDK cannot do it without putting a password in the template. Printed spelled out
        # so the commands run as-is.
        CfnOutput(
            self,
            "ChatCreateUserCommand",
            value=(
                "aws cognito-idp admin-create-user"
                f" --region {self.region}"
                f" --user-pool-id {auth_pool.user_pool_id}"
                f" --username {_PILOT_USERNAME}"
                " --message-action SUPPRESS"
            ),
            description="Step 1 of 2, run once after deploy with your own AWS credentials.",
        )
        CfnOutput(
            self,
            "ChatSetPasswordCommand",
            value=(
                "aws cognito-idp admin-set-user-password"
                f" --region {self.region}"
                f" --user-pool-id {auth_pool.user_pool_id}"
                f" --username {_PILOT_USERNAME}"
                " --password 'CHOOSE-A-PASSWORD' --permanent"
            ),
            description=(
                "Step 2 of 2. --permanent is REQUIRED: without it the account stays in "
                "FORCE_CHANGE_PASSWORD and sign-in returns a challenge instead of a token."
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
                        # The frontend signs in with these (bullet 11's InitiateAuth).
                        "userPoolId": auth_pool.user_pool_id,
                        "userPoolClientId": auth_client.user_pool_client_id,
                        "region": self.region,
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

        CfnOutput(
            self,
            "SiteUrl",
            value=site_url,
            description="CloudFront URL for the web app.",
        )

"""SJSU Student Success Navigator infrastructure stack.

Every changeable knob comes from the repo-root config.yaml, which infra/config.py also
validates at synth, because the L1 Cfn* constructs below check nothing themselves.
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
    aws_ssm as ssm,
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
    resolve_vector_store,
    validate_config,
)

# Repo root. infra_stack.py is <repo>/infra/infra/infra_stack.py, so parents[2] is the root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
# The scraper Lambda's source and the requirements.txt built into the layer below.
_SCRAPER_DIR = _REPO_ROOT / "scraper"
# The chat Lambda's source: a bare handler, no FastAPI and no Mangum.
_APP_DIR = _REPO_ROOT / "app"
# The Astro app, built in a container at synth. dist/ is produced there, never committed.
_FRONTEND_DIR = _REPO_ROOT / "frontend"
# Every SJSU fact the app states, as CSV a non-engineer can open. Outside both app/ and
# frontend/ because both read it, so it reaches the deployment twice: as the layer built
# below, and as a read-only mount into the Astro build container.
_DATA_DIR = _REPO_ROOT / "data"

# Pinned to a major version, so a synth six months from now builds the same way.
_NODE_BUILD_IMAGE = "public.ecr.aws/docker/library/node:22-alpine"

# Keep these in lockstep: the layers hold compiled extensions, so the wheel tag has to match.
_LAMBDA_PYTHON = _lambda.Runtime.PYTHON_3_13
_LAMBDA_ARCH = _lambda.Architecture.X86_64
_MANYLINUX_TAG = "manylinux2014_x86_64"
_LAMBDA_PY_TAG = "3.13"


# AWS's own account, the same in every commercial region, so it is not a config.yaml knob.
_LWA_LAYER_ACCOUNT = "753240598075"

# Derived from _LAMBDA_ARCH: a mismatched adapter deploys clean and fails on Exec format.
_LWA_LAYER_NAME = {
    _lambda.Architecture.X86_64.name: "LambdaAdapterLayerX86",
    _lambda.Architecture.ARM_64.name: "LambdaAdapterLayerArm64",
}[_LAMBDA_ARCH.name]

# Adapter 1.0.1, the newest version with a release and a changelog behind it.
_LWA_LAYER_VERSION = 28

# Spelled once and handed to uvicorn and the adapter through one PORT variable.
_LWA_PROBE_PORT = 8000

# One string in three files: EDGE_PATH_PREFIX in app/streaming_app.py, STREAM_PATH_PREFIX
# in frontend/src/lib/chatStream.ts, and here. The infra suite reads all three off disk.
_STREAM_EDGE_PATH_PREFIX = "/api"
_STREAM_EDGE_PATH_PATTERN = f"{_STREAM_EDGE_PATH_PREFIX}/*"

# A name, not a credential. It exists because eval/run_eval.py runs headless with no
# browser to redirect, so it needs the identity the password-auth client will serve.
_EVAL_USERNAME = "eval-runner"

# Deliberately not a valid username shape anyone would leave in place.
_HUMAN_USERNAME_PLACEHOLDER = "USERNAME-HERE"

def _astro_bundling() -> BundlingOptions:
    """Build frontend/ into static files inside a container, at synth.

    The build then depends on the pinned image and the committed lockfile rather than on
    whatever Node is on the machine running `cdk deploy`, and there is deliberately no
    local fallback: a missing Docker should fail loudly, not produce a different site.
    """
    return BundlingOptions(
        image=DockerImage.from_registry(_NODE_BUILD_IMAGE),
        # A second mount, because bundling bind-mounts one directory and data/ is outside
        # frontend/. Read-only: /asset-input is mounted read-write and has cost this repo
        # a broken local node_modules already.
        volumes=[
            DockerVolume(
                host_path=str(_DATA_DIR), container_path="/asset-data"
            )
        ],
        # Pinned, so the built site does not depend on who ran the deploy.
        platform="linux/amd64",
        # Output hashing: an edit that does not change the built site does not churn a deploy.
        command=[
            "sh",
            "-c",
            # The container runs as the host uid, which has no home in the image, so
            # everything that writes under $HOME needs telling where to write.
            "export HOME=/tmp ASTRO_TELEMETRY_DISABLED=1 npm_config_cache=/tmp/.npm && "
            # Never build in /asset-input: it is the developer's own frontend/, mounted
            # read-write, and `npm ci` there replaces their node_modules with linux binaries.
            "mkdir -p /tmp/build && cd /asset-input && "
            "cp -R package.json package-lock.json astro.config.mjs tsconfig.json "
            "scripts src public /tmp/build/ && "
            # generate-campus-data.mjs resolves data/ as a sibling of the frontend directory.
            "cp -R /asset-data /tmp/data && "
            # A developer's hand-written config.json would ship at the same bucket key the
            # deployment stamps, pointing students at their private endpoint. Removed from
            # the copy, not the mount, and not expressible as a Source.asset exclude.
            "rm -f /tmp/build/public/config.json && cd /tmp/build && "
            "npm ci --no-audit --no-fund && "
            "npm run build && "
            "cp -R dist/. /asset-output/",
        ],
    )


def _requirements_hash(layer: str, *requirements: Path) -> str:
    """A stable asset hash for a deps layer, keyed on every requirements file it installs.

    The layer name is folded in too, so two layers stay distinct in CDK's asset cache even
    when their requirements are byte-identical.
    """
    digest = hashlib.sha256(
        b"".join(path.read_bytes() for path in requirements)
    ).hexdigest()[:32]
    return f"{layer}-{digest}"


def _config_hash(payload: Dict[str, Any]) -> str:
    """For a resource with no property of its own that changes when config.yaml does."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]


@jsii.implements(ILocalBundling)
class _PipManylinuxLayerBundler:
    """One requirements.txt into a layer of prebuilt manylinux wheels: no Docker, no compiler.

    --platform with --only-binary forces pip to download the Linux wheel matching the Lambda
    architecture instead of building a macOS binary that fails at runtime. Returns False on
    any failure, so CDK falls back to the Docker bundling beside it.
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
        except Exception:  # no local pip or a missing wheel: let CDK try Docker
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

        # Before creating anything: otherwise a bad value first shows up as a failed deploy.
        validate_config(config)

        self._config = config

        # Every name below comes out of these: no global name is spelled in this file.
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
        # None when no recipient is configured, which reaches three places from this value.
        escalation_cfg = resolve_escalation(config)
        # None when the cap is off: the variable below is omitted rather than set to zero.
        daily_message_limit = resolve_rate_limit(config)
        cors_allow_origins = resolve_cors_allow_origins(config)

        # Region and partition are Stack tokens, so nothing here pins an account or a region.
        embedding_model_arn = (
            f"arn:{self.partition}:bedrock:{self.region}"
            f"::foundation-model/{kb_cfg['embedding_model_id']}"
        )

        # S3 Vectors rather than OpenSearch Serverless: near-zero cost, and semantic search
        # only, which is the accepted tradeoff.
        vector_bucket = s3vectors.CfnVectorBucket(
            self,
            "VectorBucket",
            vector_bucket_name=vs_cfg["vector_bucket_name"],
        )

        # `dimension` must equal the embedding model's output or every ingestion fails, and
        # `non_filterable_metadata_keys` is immutable after creation: Bedrock's own metadata
        # keys are filterable by default and blow S3 Vectors' limit, which fails every
        # ingestion with a ValidationException.
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
        # A literal name rather than a Ref, so the edge is declared rather than inferred.
        vector_index.add_dependency(vector_bucket)

        # The bucket the KB ingests from: scraped markdown plus one sidecar per page.
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

        # Referenced as an object everywhere below, so no ARN is ever copy-pasted.
        kb_role = iam.Role(
            self,
            "KnowledgeBaseRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Execution role for the SJSU Navigator Bedrock Knowledge Base.",
        )
        # The embeddings model, for both ingested chunks and incoming queries.
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[embedding_model_arn],
            )
        )
        # Scoped to the one index ARN, which already nests the bucket name.
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
        # ListBucket on the bucket, GetObject on its objects, both through the bucket object.
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
                # A oneOf: index_arn alone, or index_name with vector_bucket_arn, never all three.
                s3_vectors_configuration=bedrock.CfnKnowledgeBase.S3VectorsConfigurationProperty(
                    index_arn=vector_index.attr_index_arn,
                ),
            ),
        )
        # The index and the role first. The bucket comes along transitively through the index.
        knowledge_base.add_dependency(vector_index)
        knowledge_base.node.add_dependency(kb_role)

        # Chunking is immutable, so a change replaces this resource, and CloudFormation creates
        # the replacement before deleting the old one. The name carries the chunk config so the
        # two do not collide. The replacement starts empty and needs a manual re-ingestion.
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
        s3_data_source.add_dependency(knowledge_base)

        # Held for the sections below.
        self._source_bucket = source_bucket
        self._knowledge_base = knowledge_base
        self._s3_data_source = s3_data_source


        scraper_cfg = resolve_scraper(config)
        seed_pages = resolve_seed_pages(config)

        # manylinux wheels, built locally with pip --platform, with Docker as the fallback.
        scraper_deps_layer = _lambda.LayerVersion(
            self,
            "ScraperDepsLayer",
            description="trafilatura/lxml/regex/httpx/pypdf (manylinux x86_64) for the scraper Lambda.",
            compatible_runtimes=[_LAMBDA_PYTHON],
            compatible_architectures=[_LAMBDA_ARCH],
            code=_lambda.Code.from_asset(
                str(_SCRAPER_DIR),
                # Distinct per layer, or two layers share one cached bundle.
                asset_hash=_requirements_hash("scraper", _SCRAPER_DIR / "requirements.txt"),
                asset_hash_type=AssetHashType.CUSTOM,
                exclude=["*", ".*", "!requirements.txt"],
                bundling=BundlingOptions(
                    image=_LAMBDA_PYTHON.bundling_image,
                    local=_PipManylinuxLayerBundler(_SCRAPER_DIR / "requirements.txt"),
                    # In the Linux image a plain install already yields Linux wheels.
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt --target /asset-output/python",
                    ],
                    platform="linux/amd64",
                ),
            ),
        )

        # The facts travel as content and only a filename goes in the environment: Lambda caps
        # all environment variables at 4 KB together, and the crawl list alone is 19 KB. One
        # layer for the whole directory, with data/ itself as the asset root, so the contents
        # extract straight to /opt and every filename stays bare.
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
            # Listed file by file, so a new file in data/ is a deliberate addition here. ".*" is
            # not redundant with "*": CDK's exclude globbing misses hidden entries, so without
            # it a .DS_Store ships inside the layer.
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

        # Its own role: basic logs, a narrow S3 write, and a narrow StartIngestionJob.
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
        # The delete is what makes de-listing a page more than a silent no-op.
        scraper_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:DeleteObject"],
                resources=[source_bucket.arn_for_objects("*")],
            )
        )
        # Change gating HEADs each object for its stored hash, and HeadObject needs GetObject.
        scraper_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[source_bucket.arn_for_objects("*")],
            )
        )
        # On the bucket ARN, not the object one: the prune has to enumerate what is there.
        scraper_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[source_bucket.bucket_arn],
            )
        )
        # ListIngestionJobs rides along because Bedrock allows one job per data source at a time.
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
            # Two source files only. ".*" for the same reason as the data layer: .venv rides along.
            code=_lambda.Code.from_asset(
                str(_SCRAPER_DIR),
                exclude=["*", ".*", "!scraper.py", "!lambda_function.py"],
            ),
            layers=[scraper_deps_layer, campus_data_layer],
            role=scraper_lambda_role,
            # The Lambda maximum, against an estimated run close enough to it to be worth reading.
            timeout=Duration.minutes(15),
            memory_size=512,
            log_group=scraper_log_group,
            environment={
                # The filename, not the contents, and the same config value the asset reads.
                "URL_LIST_FILE": url_list_file,
                "SCRAPE_TIMEOUT_SECONDS": str(scraper_cfg["timeout_seconds"]),
                "SCRAPER_USER_AGENT": scraper_cfg["user_agent"],
                "SOURCE_BUCKET": source_bucket.bucket_name,
                "KNOWLEDGE_BASE_ID": knowledge_base.attr_knowledge_base_id,
                "DATA_SOURCE_ID": s3_data_source.attr_data_source_id,
            },
        )
        # It calls StartIngestionJob on them at runtime.
        scraper_lambda.node.add_dependency(s3_data_source)

        # One schedule and no tiers: the whole corpus fits one run, and change gating means an
        # unchanged day pays only the Lambda time. No event payload, so every invocation is
        # the complete sweep and a console test invoke behaves exactly like the schedule.
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

        # One-click install: invoke the scraper once during `cdk deploy` so the KB is populated
        # when the stack comes up. EVENT rather than REQUEST_RESPONSE, or the deploy waits on a
        # run that can take twelve minutes and fails with it. It re-fires only when the
        # function's code or configuration changes, which a crawl-list edit does.
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

        # One guardrail, screening PROMPT_ATTACK on the input side and nothing else: the screen
        # runs ahead of the system prompt, so a content filter here would pre-empt the prompt's
        # own crisis handling. The prompt owns safety.

        # Drives both the CfnGuardrail props and the version-description hash, so a config
        # change forces a new published version.
        guardrail_def = {
            "name": guardrail_cfg["name"],
            "contentFilters": [
                {
                    "type": f["type"],
                    "inputStrength": f["input_strength"],
                    # Forced NONE by resolve_guardrail: Bedrock requires it for PROMPT_ATTACK.
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
            # Bedrock caps this at 200 characters and rejects the change set if it is longer.
            description=(
                "Input screen for the SJSU Student Success Navigator: "
                "ApplyGuardrail(source=INPUT) on the bare student query, PROMPT_ATTACK only. "
                "No other filter and no PII policy - the system prompt owns safety."
            ),
            blocked_input_messaging=guardrail_def["blockedInputMessaging"],
            # Required by Bedrock and unreachable here, so it reuses the input message.
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
            # No PII policy: anonymization would replace the details that make a message urgent.
        )

        # The description carries a config hash because nothing else on this resource changes
        # when config.yaml does, and an edit would then leave the Lambda on a stale version.
        input_guardrail_version = bedrock.CfnGuardrailVersion(
            self,
            "InputGuardrailVersion",
            guardrail_identifier=input_guardrail.attr_guardrail_id,
            description=f"input config-{_config_hash(guardrail_def)}",
        )

        # One table, two item kinds, told apart by a sort-key prefix:
        #   conversation header   pk=USER#<sub>   sk=CONV#<convId>
        #   message               pk=USER#<sub>   sk=MSG#<convId>#<ulid>
        # Partitioning on the user is the security property: the key comes from the JWT
        # `sub`, so there is no tenant filter that can be forgotten. `pk` and `sk` are
        # generic because both kinds share them, and immutable, because changing a key
        # attribute replaces the table and takes the history with it.
        chat_history_table = dynamodb.Table(
            self,
            "ChatHistoryTable",
            table_name=chat_history_cfg["table_name"],
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            # Pilot traffic is bursty against an idle table, and this is one of the few
            # properties that can be changed later on a live table.
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            # The failure it covers is a bad deploy writing the wrong items under the right
            # keys, which no backup on a schedule catches in time.
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            # Enabled now on an attribute nothing writes: retention is an open question with
            # the university, an item without `expiresAt` never expires, and turning TTL on
            # later is a table-level change. It is epoch seconds, unlike every other stamp.
            time_to_live_attribute="expiresAt",
            # The only copy of what students said, so `cdk destroy` must not take it. The cost
            # is that destroy-then-redeploy fails on CreateTable until the leftover is dealt
            # with by hand, and a loud collision is the better half of that trade.
            removal_policy=RemovalPolicy.RETAIN,
        )


        generation_model_id = generation_cfg["model_id"]

        # Its own role: the managed basic-execution policy, with Bedrock grants added narrowly.
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
        # Collected as they are added, because the streaming app runs the same turn and gets
        # this exact list rather than a second copy that could come to differ.
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
        # A cross-region inference profile needs a different IAM shape than a bare model id,
        # and resolve_generation is what decides which of the two this is.
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
                        # And every region the profile may route to, which cannot be enumerated.
                        f"arn:{self.partition}:bedrock:*::foundation-model/{base_model_id}",
                    ],
                )
            )
            # ListInferenceProfiles takes no resource scoping; GetInferenceProfile is metadata.
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
        # A separate model id, and a denial here fails silently by design, so it would be
        # found in a sidebar that never improves. Skipped when the two ids are the same.
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

        # Nothing is attached to Converse, so there is no second ARN to grant.
        _grant_chat_turn(
            iam.PolicyStatement(
                actions=["bedrock:ApplyGuardrail"],
                resources=[input_guardrail.attr_guardrail_arn],
            )
        )
        # Spelled out rather than grant_read_write_data for one action: that helper grants
        # Scan, the single operation that takes no partition key, and the partition key is
        # this table's whole isolation story.
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
                    # Mint-if-absent, so two concurrent first turns cannot both create a header.
                    "dynamodb:ConditionCheckItem",
                    "dynamodb:DescribeTable",
                ],
                resources=[chat_history_table.table_arn],
            )
        )

        # pydantic_core is a compiled extension, so this needs the same --platform treatment
        # as the scraper's layer. boto3 comes from the runtime.
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
                # Without this, this layer ships the scraper's packages: two layers with the
                # same bundling props hash to one cache key, and CDK skips the second bundle
                # entirely. Synth, publish and deploy are all clean, and the function dies at
                # cold start on `No module named 'pydantic'`. The cost of a custom hash is
                # that it tracks the requirements file rather than the built tree.
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
        # streaming app below runs the same application modules and therefore needs the same
        # values. Spelled once: two copies of this dict would drift, and the drift would be a
        # streamed turn answering under a different cap or against a different table than the
        # buffered one.
        chat_environment = {
            "KNOWLEDGE_BASE_ID": knowledge_base.attr_knowledge_base_id,
            "GENERATION_MODEL_ID": generation_model_id,
            # Granted its own InvokeModel statement above when it differs from the one above.
            "TITLE_MODEL_ID": title_model_id,
            # AWS_REGION is reserved and cannot be set here, so the region gets its own key.
            "BEDROCK_REGION": self.region,
            "NUMBER_OF_RESULTS": str(retrieval_cfg["number_of_results"]),
            "RETRIEVE_MIN_SCORE": str(retrieval_cfg["min_score"]),
            "GENERATION_MAX_TOKENS": str(generation_cfg["max_tokens"]),
            "GENERATION_TEMPERATURE": str(generation_cfg["temperature"]),
            "MAX_QUERY_CHARS": str(request_cfg["max_query_chars"]),
            # The agent loop's caps, from config and logged when reached.
            "MAX_CONVERSE_ITERATIONS": str(chat_cfg["max_converse_iterations"]),
            "MAX_HISTORY_MESSAGES": str(chat_cfg["max_history_messages"]),
            # Not MAX_HISTORY_MESSAGES: that one is billed in tokens, these bound one query.
            "MAX_CONVERSATIONS_LISTED": str(chat_cfg["max_conversations_listed"]),
            "MAX_CONVERSATION_MESSAGES": str(chat_cfg["max_conversation_messages"]),
            # The iteration cap bounds how many calls happen, not how long they take.
            "CONVERSE_DEADLINE_SECONDS": str(chat_cfg["converse_deadline_seconds"]),
            # The cap reaches the title validator and the fallback truncation, so one number
            # decides how long a sidebar row may be.
            "TITLE_MAX_CHARS": str(chat_cfg["title_max_chars"]),
            "TITLE_DEADLINE_SECONDS": str(chat_cfg["title_deadline_seconds"]),
            # Each reaches the parser that enforces it and the prompt that states it, so the
            # number the model is told is the number the server applies.
            "CARD_MAX_CARDS": str(cards_cfg["max_cards"]),
            "CARD_MAX_RETRIEVAL_RESULTS": str(cards_cfg["max_retrieval_results"]),
            "CARD_TITLE_MAX_CHARS": str(cards_cfg["title_max_chars"]),
            "CARD_DESC_MAX_CHARS": str(cards_cfg["desc_max_chars"]),
            "CARD_FOLLOWUP_MAX_CHARS": str(cards_cfg["followup_max_chars"]),
            # Pinned to a published version. Nothing is attached to Converse, so there is no
            # output pair and no trace to configure.
            "INPUT_GUARDRAIL_ID": input_guardrail.attr_guardrail_id,
            "INPUT_GUARDRAIL_VERSION": input_guardrail_version.attr_version,
            # By reference, not the literal from config.yaml, or the name is spelled twice.
            "CHAT_HISTORY_TABLE_NAME": chat_history_table.table_name,
            # The only control that bounds what one account spends: the throttle and the
            # reserved concurrency below cannot tell two students apart. Omitted rather than
            # set to "0" when the cap is off, so there is one spelling of off.
            **(
                {"DAILY_MESSAGE_LIMIT": str(daily_message_limit)}
                if daily_message_limit is not None
                else {}
            ),
            # Present only when config.yaml names a recipient: absent is off, in one spelling.
            # It also reaches config.json below, because the server assembles the message and
            # the browser decides whether the component exists at all.
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
            # File by file, so a stray script in app/ cannot ride along, and ".*" so .venv
            # and .pytest_cache do not end up inside the deployed function.
            code=_lambda.Code.from_asset(
                str(_APP_DIR),
                exclude=[
                    "*",
                    ".*",
                    "!handler.py",
                    # Imported at module scope, so an omission here is an ImportError.
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
                    # Imported at module scope by places.py, safety.py and prompts.py.
                    "!campus_data.py",
                    "!orchestrator.py",
                    "!campus_time.py",
                    "!history.py",
                    "!titles.py",
                    "!usage.py",
                    "!ratelimit.py",
                ],
            ),
            # The data layer too: the CSVs are read at import, from /opt.
            layers=[chat_deps_layer, campus_data_layer],
            role=chat_lambda_role,
            # A ceiling, not a choice: an HTTP API integration tops out at 30,000 ms and
            # cannot be raised. One second under, so the function's own timeout wins and the
            # failure is diagnosable in its logs rather than only as a gateway 504.
            timeout=Duration.seconds(CHAT_LAMBDA_TIMEOUT_SECONDS),
            # Lambda scales CPU with memory, and cold-start import time comes out of the
            # loop's own budget. An over-provision rather than a measured value.
            memory_size=1024,
            log_group=chat_log_group,
            environment=chat_environment,
        )
        # It retrieves from the KB at runtime, so it must not exist before the KB.
        chat_lambda.node.add_dependency(knowledge_base)
        # Stated rather than left to the Ref above, which a refactor could flatten.
        chat_lambda.node.add_dependency(chat_history_table)
        # Held for the HTTP API below.
        self._chat_lambda = chat_lambda

        # HTTP API v2 rather than REST: the 30-second integration ceiling is the same on
        # both, and streaming leaves API Gateway entirely. The cost fence is four things:
        # the Cognito gate, route throttling (invocations started per second), reserved
        # concurrency (invocations running at once), and the loop's own deadline. The last
        # is the only one that bounds a single runaway invocation.

        http_api_cfg = resolve_http_api(config)

        # The trailing slash is load-bearing: Cognito matches a redirect_uri by exact string
        # and the frontend sends `window.location.origin + "/"`.
        local_redirect_urls = [f"{origin.rstrip('/')}/" for origin in cors_allow_origins]

        # One account per person, created administratively after deploy. Sign-in is the
        # hosted redirect rather than a form because a federated user cannot authenticate
        # through InitiateAuth at all, so a form built now is thrown away when Okta arrives.
        # No credential appears here or in the template: the stack prints the CLI commands.
        auth_pool = cognito.UserPool(
            self,
            "ChatUserPool",
            # Accounts are issued, not claimed: a self-enrolled one is a second identity.
            self_sign_up_enabled=False,
            # Plain username, so an administrator can create whatever name the campus uses.
            # Immutable after creation: changing it replaces the pool and every account in it.
            sign_in_aliases=cognito.SignInAliases(username=True),
            # No verified email or phone to send a code to, so recovery is administrative.
            account_recovery=cognito.AccountRecovery.NONE,
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            # No custom attributes: a federated profile is rebuilt from the provider's claims
            # on every sign-in, so anything written here is liable to be overwritten.
            removal_policy=RemovalPolicy.DESTROY,
        )

        # /oauth2/authorize is the only place an Okta round trip can happen, so having the
        # domain now means adding SJSU's IdP later touches no application code. The prefix
        # is globally unique, so it comes from the stack's own GUID rather than a literal.
        stack_unique_suffix = Fn.select(
            4, Fn.split("-", Fn.select(2, Fn.split("/", self.stack_id)))
        )
        login_domain = auth_pool.add_domain(
            "ChatLoginDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"sjsu-navigator-{stack_unique_suffix}"
            ),
        )

        # Created only when config.yaml carries a metadata URL: the absence of a value is
        # the switch. The provider name must never change, because a federated user's
        # username is `<providerName>_<nameid>` and renaming it mints a new `sub`, which is
        # the DynamoDB partition key. SP-initiated only, or an unsolicited assertion is
        # accepted on its own, which is a login-CSRF primitive.
        okta_cfg = resolve_okta(config)
        okta_provider = None
        if okta_cfg is not None:
            okta_provider = cognito.UserPoolIdentityProviderSaml(
                self,
                "OktaIdentityProvider",
                user_pool=auth_pool,
                name=okta_cfg["provider_name"],
                # A URL, so a certificate rotation is not an outage waiting on a re-upload.
                metadata=cognito.UserPoolIdentityProviderSamlMetadata.url(
                    okta_cfg["metadata_url"]
                ),
                idp_initiated=False,
                # No username mapping: Cognito derives it from the SAML NameID and rejects one.
                attribute_mapping=cognito.AttributeMapping(
                    email=cognito.ProviderAttribute.other(okta_cfg["email_attribute"]),
                ),
            )

        # Two clients on one pool, because the two callers cannot share one.
        #
        # The web client is public and has no secret, because the frontend is JavaScript in
        # a browser. PKCE is what makes that safe, and there is no property here to turn it
        # on: Cognito requires a code_challenge from any client with no secret.
        #
        # No password flow on it at all: leaving one enabled would let a form reappear that
        # a federated user could never sign in through. This is the client Okta attaches to.
        web_client = auth_pool.add_client(
            "ChatWebClient",
            generate_secret=False,
            # Pinned to refresh-only through the L1 below, for the reason stated there.
            auth_flows=cognito.AuthFlow(),
            # Never left to the default: CDK fills an omitted list from every provider
            # registered on the pool, so an unrelated construct could widen this one.
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
                    # Implicit returns tokens in the URL fragment, where history keeps them.
                    implicit_code_grant=False,
                    client_credentials=False,
                ),
                # email and profile are for the sidebar; neither is an identity, `sub` is.
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                # Local dev only here: the CloudFront origin is appended below as a token.
                callback_urls=list(local_redirect_urls),
                logout_urls=list(local_redirect_urls),
            ),
            # Cognito's maximum. The frontend holds the token in memory, so a reload signs in.
            access_token_validity=Duration.days(1),
            # The frontend discards its refresh token, so it must not outlive the session.
            refresh_token_validity=Duration.days(1),
            id_token_validity=Duration.days(1),
            prevent_user_existence_errors=True,
        )

        # An absent ExplicitAuthFlows does not mean no direct sign-in: Cognito falls back to
        # legacy defaults that include SRP, which is a path no federated user can ever use.
        web_client.node.default_child.explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]

        # The provider is named above as a literal, not a Ref, so nothing orders these two.
        if okta_provider is not None:
            web_client.node.add_dependency(okta_provider)

        # Password auth, no OAuth, no callbacks, because eval/run_eval.py runs headless. A
        # client is the unit Cognito applies flows to, so one client serving both would put
        # a password flow behind the client id in config.json.
        eval_client = auth_pool.add_client(
            "ChatEvalClient",
            generate_secret=False,
            # No secret, so the harness's one unsigned InitiateAuth needs no SECRET_HASH.
            auth_flows=cognito.AuthFlow(user_password=True),
            # Pinned, or creating the Okta provider above silently adds it to this client,
            # which has no browser to redirect and so could never use it.
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
            access_token_validity=Duration.days(1),
            refresh_token_validity=Duration.days(1),
            id_token_validity=Duration.days(1),
            prevent_user_existence_errors=True,
            disable_o_auth=True,
        )

        # The eval client is exempt from the daily cap: the harness fires its whole set as
        # one account, and a tripped harness records refusals as answers rather than failing.
        # Keyed on the app client, which is a claim API Gateway has already validated, so a
        # browser cannot claim it. Set after the fact because the client is created here and
        # the function above, and add_environment keeps it a reference.
        chat_lambda.add_environment(
            "RATE_LIMIT_EXEMPT_CLIENT_IDS", eval_client.user_pool_client_id
        )

        # Locked to config.yaml's origins, never "*". CORS is enforced by browsers only, so
        # it is not a boundary; what it buys is stopping a third-party page driving this
        # billable endpoint from its visitors' browsers. Every method here carries
        # Authorization and is therefore preflighted, and a missing entry surfaces as a CORS
        # error rather than as the missing method it is. Spelled once, because the CloudFront
        # origin is appended to this same block below.
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

        # Native, so no authorizer Lambda and none of this repo's code in the auth decision.
        # The audience is the app client id because a Cognito access token carries no `aud`,
        # it carries `client_id`, and API Gateway validates that only when `aud` is absent.
        # Do not "fix" this to an ID token. Both clients are listed, and omitting either is
        # a 401 at the gateway with no CORS headers and no explanation.
        jwt_authorizer = apigwv2_authorizers.HttpJwtAuthorizer(
            "ChatJwtAuthorizer",
            f"https://cognito-idp.{self.region}.amazonaws.com/{auth_pool.user_pool_id}",
            jwt_audience=[
                web_client.user_pool_client_id,
                eval_client.user_pool_client_id,
            ],
            identity_source=["$request.header.Authorization"],
        )

        # The billable route: every Bedrock call and every retrieval hangs off it.
        http_api.add_routes(
            path="/chat",
            methods=[apigwv2.HttpMethod.POST],
            integration=chat_integration,
            authorizer=jwt_authorizer,
        )

        # Gated even though they spend no Bedrock tokens: the `sub` claim is the partition
        # key, so an ungated read is a read with nobody to attribute.
        http_api.add_routes(
            path="/conversations",
            methods=[apigwv2.HttpMethod.GET],
            integration=chat_integration,
            authorizer=jwt_authorizer,
        )
        http_api.add_routes(
            # The name inside the braces arrives as pathParameters.conversationId.
            path="/conversations/{conversationId}",
            methods=[apigwv2.HttpMethod.GET],
            integration=chat_integration,
            authorizer=jwt_authorizer,
        )

        # Separate from the GET above because these are writes, and the only thing that
        # decides whose data they change is the `sub` claim.
        http_api.add_routes(
            path="/conversations/{conversationId}",
            methods=[apigwv2.HttpMethod.PATCH, apigwv2.HttpMethod.DELETE],
            integration=chat_integration,
            authorizer=jwt_authorizer,
        )

        # The stage default, so it covers a route added later. Not the per-route map: a
        # RouteSettingsProperty as a map value renders camelCase keys and deploys unapplied.
        default_stage = http_api.default_stage.node.default_child
        default_stage.default_route_settings = apigwv2.CfnStage.RouteSettingsProperty(
            throttling_rate_limit=http_api_cfg["throttling_rate_limit"],
            throttling_burst_limit=http_api_cfg["throttling_burst_limit"],
        )

        # The throttle bounds how many invocations start; this bounds how many run at once.
        cfn_chat = chat_lambda.node.default_child
        cfn_chat.reserved_concurrent_executions = http_api_cfg[
            "chat_reserved_concurrency"
        ]

        self._http_api = http_api
        chat_url = f"{http_api.api_endpoint}/chat"
        self._chat_url = chat_url
        # Stamped beside the chat URL rather than derived from it, which would put a rule
        # about this stack's route names in a file this stack does not build.
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
        # By CLI, because CDK cannot create an account without putting a password in the
        # template. Printed whole, so the commands run as they are.
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

        # The Lambda Web Adapter is an execution wrapper: AWS_LAMBDA_EXEC_WRAPPER points the
        # runtime at /opt/bootstrap from the layer, that execs run.sh out of the bundle, run.sh
        # starts uvicorn, and the adapter proxies each invocation to it over HTTP. In
        # response_stream mode it writes the body out as the app produces it, which is the
        # supported way to stream in band from Python.
        #
        # POST /api/chat on this app runs the same app/turn.py the API Gateway handler runs and
        # streams the reply as it is written; /stream and /model stay beside it because when a
        # stream arrives in one lump the question is always transport, Bedrock, or the turn.
        #
        # It verifies the Cognito access token itself (app/token_auth.py), because a Function URL
        # behind IAM auth carries the signer's identity and no student's claims, and takes no
        # authorizer. Zip plus the published layer rather than a container image, so nothing here
        # adds an ECR repository for a binary AWS already publishes.
        #
        # Ungated, which is a judgement: it costs nothing until it is invoked and nobody can
        # invoke it without credentials. config.yaml's `streaming` block is now read by nothing.

        # Its own layer: FastAPI in the chat function's would change that function's deployed
        # artifact for something it never runs.
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
                # Both files, because the outer one pulls the inner one in with `-r`.
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
                    # This list shapes the declared inputs, not what the container sees.
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

        # By ARN rather than built: AWS publishes it per region and per architecture, and the
        # region comes from the stack, so a fresh install attaches its own region's copy.
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

        # A parameter rather than an environment variable, because the web client's id in this
        # function's environment is a real CloudFormation cycle: the client's callback URL is
        # the distribution's domain, the distribution's /api/* origin is this function's URL,
        # and the URL belongs to the function. The ids are the only value in that loop read by
        # code rather than by CloudFormation, so they are the only one that can be deferred:
        # the function carries the parameter's name, which is a string and not a reference.
        # The name is the stack's own, so two installs in one account do not share one.
        streaming_client_allowlist_name = (
            f"/{self.stack_name}/streaming/allowed-client-ids"
        )
        ssm.StringParameter(
            self,
            "StreamingAllowedClientIds",
            parameter_name=streaming_client_allowlist_name,
            # Both clients: students arrive on one and the eval harness on the other, and this
            # is the same pair the HTTP API's authorizer carries as its audience.
            string_value=Fn.join(
                ",",
                [
                    web_client.user_pool_client_id,
                    eval_client.user_pool_client_id,
                ],
            ),
            description=(
                "App clients whose Cognito access tokens the streaming chat app accepts. "
                "Read once per container by app/token_auth.py; it is a parameter rather "
                "than a Lambda environment variable because the direct reference is a "
                "CloudFormation dependency cycle through the site distribution."
            ),
        )

        stream_probe_lambda = _lambda.Function(
            self,
            "StreamProbeFunction",
            runtime=_LAMBDA_PYTHON,
            architecture=_LAMBDA_ARCH,
            # A path under the bundle root, not a dotted module: /opt/bootstrap execs it. The
            # file has to be there and executable, and a dropped bit deploys clean and then
            # answers Permission denied on every invocation.
            handler="run.sh",
            # The chat function's list plus run.sh, streaming_app.py, preview.py and
            # token_auth.py. handler.py is deliberately absent: that is the other transport.
            # The infra suite walks streaming_app.py's imports rather than re-reading this.
            code=_lambda.Code.from_asset(
                str(_APP_DIR),
                exclude=[
                    "*",
                    ".*",
                    "!run.sh",
                    "!streaming_app.py",
                    "!preview.py",
                    # No authorizer in front, so the module deciding who a caller is rides along.
                    "!token_auth.py",
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
                    # Imported at module scope by places.py, safety.py and prompts.py.
                    "!campus_data.py",
                    "!orchestrator.py",
                    "!campus_time.py",
                    "!history.py",
                    "!titles.py",
                    "!usage.py",
                    "!ratelimit.py",
                ],
            ),
            # Order does not matter: nothing these three own inside /opt overlaps.
            layers=[
                stream_probe_deps_layer,
                lambda_web_adapter_layer,
                campus_data_layer,
            ],
            # No role argument, so CDK builds one with AWSLambdaBasicExecutionRole and the
            # turn's grants are added below. No reserved concurrency: that would take capacity
            # out of the account pool, and the chat function is the one entitled to fence it.
            timeout=Duration.seconds(60),
            # The chat function's number, for its reason: Lambda scales CPU with memory, and
            # cold-start imports come out of the time the loop has to work in.
            memory_size=1024,
            log_group=stream_probe_log_group,
            environment={
                # Whole, because load_settings() raises unless every identity variable is
                # present, and a subset here is a second idea of what Settings needs.
                **chat_environment,
                # Without it the runtime looks for a Python handler called "run.sh".
                "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
                # The adapter's half of the streaming switch, and its own lower-case enum,
                # not the Function URL's InvokeMode below. Set one and it silently buffers.
                "AWS_LWA_INVOKE_MODE": "response_stream",
                # Read by uvicorn in run.sh and by the adapter as the port it forwards to.
                "PORT": str(_LWA_PROBE_PORT),
                # Which pool app/token_auth.py trusts, since nothing in front of this endpoint
                # validates a student's token. References rather than literals, so a fresh
                # install trusts its own pool without anybody editing a file.
                "COGNITO_REGION": self.region,
                "USER_POOL_ID": auth_pool.user_pool_id,
                # By name, not by value: the client id itself here is the cycle above.
                "ALLOWED_CLIENT_IDS_PARAMETER": streaming_client_allowlist_name,
            },
        )
        # The same list the buffered transport carries: a harness exempt on one transport and
        # capped on the other is a run that fails halfway for a reason nobody would look for.
        stream_probe_lambda.add_environment(
            "RATE_LIMIT_EXEMPT_CLIENT_IDS", eval_client.user_pool_client_id
        )
        # Stated rather than left to the Refs above, which a refactor could flatten.
        stream_probe_lambda.node.add_dependency(knowledge_base)
        stream_probe_lambda.node.add_dependency(chat_history_table)

        # The same list the chat function takes, because it runs the same turn: a streamed
        # turn that could reach less than a buffered one would fail somewhere subtle and
        # only for some questions. It holds no `lambda:InvokeFunction` and no
        # `execute-api:ManageConnections`, because the turn runs in this process.
        for statement in _chat_turn_statements:
            stream_probe_lambda.add_to_role_policy(statement)

        # The door rather than the turn, so it is written here and not folded into the shared
        # list. The ARN is assembled by hand because `parameter.grant_read` would put a Ref in
        # this policy and bring the cycle back through it.
        stream_probe_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:{self.partition}:ssm:{self.region}:{self.account}"
                    f":parameter{streaming_client_allowlist_name}"
                ],
            )
        )

        # InvokeMode is a property of the URL, not the function, and Lambda buffers the whole
        # body without it whatever the adapter is set to.
        #
        # AWS_IAM, never NONE: an unauthenticated Function URL is an endpoint any stranger can
        # bill us through. It also decides what is not in the template, because CDK attaches
        # the anonymous invoke permission only when AuthType is NONE.
        #
        # Two things authenticate a caller, one per layer. SigV4 says the request may reach
        # this function, and the edge holds those credentials where a browser never does. The
        # Cognito token on the app's own header says who it is for. Neither substitutes for
        # the other. This URL stays open as the control: whether a body that streams out of
        # here still streams after the edge is the whole question, and a measurement with
        # nothing to compare against answers nothing.
        stream_probe_url = stream_probe_lambda.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.AWS_IAM,
            invoke_mode=_lambda.InvokeMode.RESPONSE_STREAM,
        )

        CfnOutput(
            self,
            "StreamProbeFunctionUrl",
            value=stream_probe_url.url,
            description=(
                "IAM-signed Function URL for the streaming chat app, and the CONTROL for "
                "the edge measurement - curl this and StreamEdgeUrl on the same route and "
                "compare time_starttransfer against time_total. POST /api/chat streams a "
                "full turn as NDJSON frames; GET /api/stream and GET /api/model are the "
                "transport and Bedrock probes. Every request must be SigV4-signed for "
                "service 'lambda', and POST /api/chat additionally needs a Cognito ACCESS "
                "token from this pool on the app's own auth header - the one name in "
                "app/token_auth.py's AUTH_HEADER_NAME, which is the only place it is "
                "spelled - and the app verifies it in process; without one it answers 401. "
                "The only other thing in this stack that references it is the site "
                "distribution's /api/* behaviour."
            ),
        )

        # The site and the stream come off one distribution: one origin for the browser, and
        # the streaming Function URL behind a path pattern on it.
        #
        # The Astro app is compiled in a container at synth and its dist/ is what lands in
        # the bucket, never a committed copy. It is multi-page, so a blanket SPA fallback
        # would be wrong twice over: rewriting every miss to index.html with a 200 masks real
        # 404s. What a REST origin needs is directory-index resolution, because unlike a
        # website endpoint it does not resolve /login/ to /login/index.html on its own.

        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            # OAC requires ACLs disabled. The default, spelled out so the requirement is legible.
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # A REST origin has no index-document behaviour, so without this every path but "/"
        # 403s. Extensionless paths gain /index.html; anything with an extension is untouched.
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

        # This distribution rather than one of the stream's own, so the browser gets one
        # origin for the whole app: no CORS story, no second allowlist to keep in step, and
        # no preflight in front of a request whose entire value is time to first byte.
        stream_edge_oac = cloudfront.FunctionUrlOriginAccessControl(
            self,
            "StreamEdgeOriginAccessControl",
            # One half of a contract whose other half is the Function URL's AuthType, and CDK
            # checks the pair at synth.
            signing=cloudfront.Signing.SIGV4_ALWAYS,
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
            additional_behaviors={
                # This pattern does not claim the root, so "/" is the site's index.html and the
                # adapter's readiness route stays on 127.0.0.1. The directory-index function is
                # on the default behaviour only: here it would 404 every route.
                _STREAM_EDGE_PATH_PATTERN: cloudfront.BehaviorOptions(
                    origin=origins.FunctionUrlOrigin.with_origin_access_control(
                        stream_probe_url, origin_access_control=stream_edge_oac
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    # A cached turn is one student's answer served to another.
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    # The Host exception is the point: OAC signs over the origin's host, so
                    # forwarding the viewer's makes every signature fail to validate. The rest
                    # is needed, including the body hash a client sending a POST has to compute.
                    origin_request_policy=(
                        cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER
                    ),
                    # The only value carrying POST: CloudFront has no POST-without-DELETE
                    # option, and the app answers 405 for the rest.
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    # Compressing is holding bytes in order to compress them, and "probably
                    # not, for a chunked body" is the wrong basis for a buffering measurement.
                    compress=False,
                ),
            },
            # CustomErrorResponses lives on DistributionConfig and a CacheBehavior has no
            # error-response property at all, so every entry here claims a status across both
            # origins. The two therefore divide the statuses.
            #
            # 403 is the site's, and it is the S3 REST origin's own "no such page": without
            # s3:ListBucket S3 will not distinguish absent from forbidden, so 403 is the only
            # status a page miss can arrive on. Grant ListBucket and the two halves of this
            # list swap meanings with nothing failing.
            #
            # 404 is the API's and passes through untouched, because it is the clearest signal
            # that the streaming front door is dead. Mapped to /404.html it came back as the
            # site's error page, so a curl got HTML and the browser fell back, and a broken
            # deploy read as a working product on both instruments at once. ttl=0 is the other
            # half: a cached front-door failure outlives the fix that repaired it.
            #
            # Both a status and a page, or neither. CloudFront rejects a status without a page
            # at create time rather than at synth, which cost a failed deploy and a rollback.
            #
            # One failure stays masked: a 403 raised on /api/* is covered by the site's entry.
            # Separating it needs a second distribution, which is a second domain, and the
            # instrument for it is the direct Function URL instead.
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=404,
                    response_page_path="/404.html",
                    ttl=Duration.minutes(5),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    ttl=Duration.seconds(0),
                ),
            ],
        )

        site_url = f"https://{site_distribution.distribution_domain_name}"

        # The half of the grant CDK does not write. Invoking a Function URL has needed both
        # `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` since October 2025, and the
        # construct above emits only the first. With one of them the edge answers 403
        # AccessDenied, which reads like a signing mistake and is invisible until a deploy.
        _lambda.CfnPermission(
            self,
            "StreamEdgeInvokeFunctionPermission",
            action="lambda:InvokeFunction",
            function_name=stream_probe_lambda.function_arn,
            principal="cloudfront.amazonaws.com",
            # The principal is a service every AWS customer shares, so without this condition
            # the grant reads "any distribution in any account may invoke this function".
            source_arn=(
                f"arn:{self.partition}:cloudfront::{self.account}"
                f":distribution/{site_distribution.distribution_id}"
            ),
        )

        # Two deployments into one bucket, because they need different cache-control:
        # config.json carries the API URL, so a cached copy pins a stale endpoint after a
        # redeploy. Both prune by default, so the site's excludes config.json and the
        # config's does not prune at all.
        s3deploy.BucketDeployment(
            self,
            "SiteContentDeployment",
            destination_bucket=site_bucket,
            sources=[
                s3deploy.Source.asset(
                    str(_FRONTEND_DIR),
                    # Host build products stay out: a copied dist/ could shadow the fresh build.
                    exclude=["node_modules", "dist", ".astro", "README.md"],
                    bundling=_astro_bundling(),
                )
            ],
            distribution=site_distribution,
            distribution_paths=["/*"],
            # Scopes the prune, so this cannot delete the config.json the other one owns.
            exclude=["config.json"],
            # no-cache means revalidate, not do not store, and HTML is cheap to revalidate.
            cache_control=[s3deploy.CacheControl.no_cache()],
        )

        # Stamped at deploy from stack tokens, so nothing in the committed frontend names an
        # account, a region, an API id or a pool id.
        s3deploy.BucketDeployment(
            self,
            "SiteConfigDeployment",
            destination_bucket=site_bucket,
            sources=[
                s3deploy.Source.json_data(
                    "config.json",
                    {
                        "chatApiUrl": chat_url,
                        # One base URL: the single-conversation route is this plus "/<id>".
                        "conversationsApiUrl": conversations_url,
                        # The web client, never the eval one: this file is world-readable,
                        # and the eval id in it would publish a password-auth endpoint.
                        "userPoolId": auth_pool.user_pool_id,
                        "userPoolClientId": web_client.user_pool_client_id,
                        # No redirect URI here: the frontend derives it from
                        # window.location.origin, so one config.json is right in both places.
                        "loginDomain": login_domain.base_url(),
                        "region": self.region,
                        # The omission is the gate: the settings panel renders the cost
                        # section only when this key is present, so the breakdown comes off
                        # with a config edit rather than a code change. Nothing here is
                        # account spend, only published rates times measured usage.
                        **(
                            {"costModel": cost_model}
                            if (cost_model := resolve_cost_model(config)) is not None
                            else {}
                        ),
                        # There is no streaming endpoint key: the stream is `/api/*` on this
                        # same distribution, so the browser sends a relative path.
                        #
                        # The omission is the gate again: with no recipient the frontend
                        # renders no escalation UI at all. The address is a published campus
                        # mailbox that every draft shows the student anyway.
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
            # One file, so pruning would delete the entire site the other deployment wrote.
            prune=False,
            # no-store, because a stale copy points the app at an endpoint that may be gone.
            cache_control=[s3deploy.CacheControl.no_store()],
        )

        # The browser sends the CloudFront origin on every /chat call, so the API is
        # unreachable from the one place it is meant to be reached from without this. A
        # deploy-time token, and the L1 replaces the whole CORS block, which is why the
        # methods and headers above are spelled once and reused.
        cfn_api = http_api.node.default_child
        cfn_api.cors_configuration = apigwv2.CfnApi.CorsProperty(
            allow_origins=[*self._cors_allow_origins, site_url],
            allow_methods=self._cors_allow_methods,
            allow_headers=self._cors_allow_headers,
        )

        # Cognito only redirects to a registered callback URL, and the deployed one is the
        # distribution's domain, which does not exist until it is created above. An ordering
        # constraint, not a cycle: the distribution does not reference the client. Sign-out
        # is in the same list, or Cognito refuses /logout and the pool session outlives it.
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

        CfnOutput(
            self,
            "StreamEdgeUrl",
            value=f"{site_url}{_STREAM_EDGE_PATH_PREFIX}",
            description=(
                "The streaming app through the edge: same routes as StreamProbeFunctionUrl "
                "hung off this prefix (/stream, /model, /chat). NO SIGNATURE IS NEEDED - "
                "origin access control signs the origin request, so this is the same "
                "endpoint reachable two ways, which is what makes the pair a measurement. "
                "A client sending a body must add x-amz-content-sha256 for it: Lambda does "
                "not accept an unsigned payload from an OAC-signed origin request."
            ),
        )

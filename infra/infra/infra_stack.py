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
    Duration,
    ILocalBundling,
    RemovalPolicy,
    Stack,
    aws_bedrock as bedrock,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_s3 as s3,
    aws_s3vectors as s3vectors,
    triggers,
)
from constructs import Construct

from infra.config import (
    resolve_chat,
    resolve_chunking,
    resolve_data_source_name,
    resolve_generation,
    resolve_guardrail,
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

# Both Lambdas' architecture and the MATCHING manylinux wheel tag. trafilatura pulls in lxml
# and regex, pydantic pulls in pydantic-core - all compiled extensions - so the layers must
# contain Linux (x86_64) wheels, not the macOS wheels a plain `pip install` produces on a dev
# Mac. Keep these two in lockstep.
_LAMBDA_PYTHON = _lambda.Runtime.PYTHON_3_13
_LAMBDA_ARCH = _lambda.Architecture.X86_64
_MANYLINUX_TAG = "manylinux2014_x86_64"
_LAMBDA_PY_TAG = "3.13"


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
        # ever missing. asset_hash_type=OUTPUT so the hash tracks the built layer, not the source
        # dir (which carries tests and a .venv). See _PipManylinuxLayerBundler.
        scraper_deps_layer = _lambda.LayerVersion(
            self,
            "ScraperDepsLayer",
            description="trafilatura/lxml/regex/httpx (manylinux x86_64) for the scraper Lambda.",
            compatible_runtimes=[_LAMBDA_PYTHON],
            compatible_architectures=[_LAMBDA_ARCH],
            code=_lambda.Code.from_asset(
                str(_SCRAPER_DIR),
                asset_hash_type=AssetHashType.OUTPUT,
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
                asset_hash_type=AssetHashType.OUTPUT,
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
            timeout=Duration.seconds(29),
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
                # The input screen, pinned to its published numbered version. There is no
                # OUTPUT_GUARDRAIL_* pair and no GUARDRAIL_TRACE: nothing is attached to
                # Converse, so there is no trace to configure.
                "INPUT_GUARDRAIL_ID": input_guardrail.attr_guardrail_id,
                "INPUT_GUARDRAIL_VERSION": input_guardrail_version.attr_version,
            },
        )
        # The function retrieves from the KB at runtime, so it must not exist before the KB.
        chat_lambda.node.add_dependency(knowledge_base)
        # Held for bullet 5, which routes the HTTP API at it.
        self._chat_lambda = chat_lambda

        # --- 5. Auth + API: Cognito pool/client + JWT authorizer + HTTP API ------

        # --- 6. Site delivery: S3 + CloudFront (OAC) + Astro + config.json -------

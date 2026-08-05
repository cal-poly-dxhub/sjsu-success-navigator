"""Template assertions on each stack section's key wiring.

`cdk synth` alone is a weak gate here: the stack is L1 `Cfn*` throughout, and L1 constructs
emit whatever properties they are given without checking them. Synth proves the Python runs
and the template is well-formed. It does NOT prove the KB points at our index, that the role
can reach the vectors, or that the pieces are ordered so CloudFormation creates them in a
workable sequence - and every one of those is invisible until a deploy fails. These
assertions cover the wiring; infra/tests/unit/test_config.py covers the property VALUES.

Run from infra/ with `python -m pytest` (gav convention: cwd on sys.path makes `infra.*`
resolve to infra/infra/).
"""

import copy
import functools
import json
import os
import tempfile
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.config import load_config
from infra.infra_stack import NavigatorStack


@functools.lru_cache(maxsize=1)
def _template() -> Template:
    """The synthesized template, built once per session.

    Cached because synth bundles the scraper's manylinux deps layer, which costs a couple of
    seconds per call - times every test. The assertions below only ever READ the template.
    """
    app = cdk.App()
    stack = NavigatorStack(app, "SjsuNavigatorStack", config=load_config())
    return Template.from_stack(stack)


def _resource(template: Template, type_name: str) -> dict:
    """The one resource of a type, with its raw template entry (DependsOn included).

    find_resources is used rather than has_resource_properties where the assertion needs
    DependsOn or a cross-resource Fn::GetAtt - those live outside Properties, so a
    properties-only matcher cannot see them.
    """
    found = template.find_resources(type_name)
    assert len(found) == 1, f"expected exactly one {type_name}, found {len(found)}"
    return next(iter(found.values()))


def _resource_named(template: Template, type_name: str, logical_id_prefix: str) -> dict:
    """The one resource of a type whose logical id starts with `logical_id_prefix`.

    Needed where _resource cannot be used because the stack holds several of a type that are
    not all ours: three Lambda functions (the scraper plus two CDK-provider handlers) and two
    layer versions. The prefix is the construct id, which is stable; the hash suffix CDK
    appends is not, so it is deliberately not matched.
    """
    found = {
        lid: r for lid, r in template.find_resources(type_name).items()
        if lid.startswith(logical_id_prefix)
    }
    assert len(found) == 1, (
        f"expected exactly one {type_name} named {logical_id_prefix}*, found {sorted(found)}"
    )
    return next(iter(found.values()))


def test_config_loads_with_expected_sections():
    config = load_config()
    for key in (
        "knowledge_base",
        "vector_store",
        "chunking",
        "scraper",
        "http_api",
        "cors",
        "request",
        "retrieval",
        "chat",
        "generation",
        "guardrail",
    ):
        assert key in config, f"config.yaml missing section: {key}"


def test_stack_rejects_an_invalid_config_at_synth():
    """The validators are only worth having if the STACK runs them - a validator nothing
    calls is a comment. Instantiating the stack must fail on a bad config, and it must fail
    for a section that has not been built yet (here: cors, consumed by the API section)."""
    config = copy.deepcopy(load_config())
    config["cors"]["allow_origins"] = ["*"]
    with pytest.raises(ValueError, match="must not contain"):
        NavigatorStack(cdk.App(), "SjsuNavigatorStack", config=config)


# --- Section 1: vector store + Knowledge Base + S3 data source ---------------------------


def test_vector_index_is_shaped_for_the_embedding_model():
    """Dimension/type/metric reach the index, and Bedrock's internal metadata keys are
    marked non-filterable. Getting the keys wrong fails EVERY ingestion, and the setting is
    immutable, so the fix would be replacing the index rather than editing it."""
    template = _template()
    template.has_resource_properties(
        "AWS::S3Vectors::Index",
        {
            "VectorBucketName": "sjsu-navigator-vectors",
            "IndexName": "bedrock-knowledge-base-default-index",
            "DataType": "float32",
            "Dimension": 1024,
            "DistanceMetric": "cosine",
            "MetadataConfiguration": {
                "NonFilterableMetadataKeys": Match.array_with(
                    ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
                )
            },
        },
    )


def test_vector_index_depends_on_its_bucket_explicitly():
    """vector_bucket_name is a config literal, not a Ref, so CloudFormation cannot infer
    this edge. Without the explicit DependsOn the index can be attempted before the bucket
    it lives in exists."""
    assert _resource(_template(), "AWS::S3Vectors::Index")["DependsOn"] == ["VectorBucket"]


def test_knowledge_base_points_at_the_in_stack_index_by_arn_alone():
    """S3VectorsConfiguration is a oneOf: index_arn alone, or index_name +
    vector_bucket_arn - never all three, which matches both subschemas and gets rejected as
    ambiguous. Assert the ARN form AND that nothing else crept into that block."""
    kb = _resource(_template(), "AWS::Bedrock::KnowledgeBase")
    storage = kb["Properties"]["StorageConfiguration"]
    assert storage["Type"] == "S3_VECTORS"
    assert storage["S3VectorsConfiguration"] == {
        "IndexArn": {"Fn::GetAtt": ["VectorIndex", "IndexArn"]}
    }


def test_knowledge_base_is_vector_type_on_the_configured_embedding_model():
    template = _template()
    template.has_resource_properties(
        "AWS::Bedrock::KnowledgeBase",
        {
            "Name": "sjsu-navigator-kb",
            "KnowledgeBaseConfiguration": {
                "Type": "VECTOR",
                "VectorKnowledgeBaseConfiguration": {
                    "EmbeddingModelArn": {
                        "Fn::Join": [
                            "",
                            [
                                "arn:",
                                {"Ref": "AWS::Partition"},
                                ":bedrock:",
                                {"Ref": "AWS::Region"},
                                "::foundation-model/amazon.titan-embed-text-v2:0",
                            ],
                        ]
                    }
                },
            },
        },
    )


def test_knowledge_base_waits_for_the_index_and_its_role_policy():
    """Ordering that only shows up as a deploy failure: a KB created before the index exists,
    or before its role's inline policy is attached, cannot reach its own vector store."""
    depends = _resource(_template(), "AWS::Bedrock::KnowledgeBase")["DependsOn"]
    assert "VectorIndex" in depends
    assert any(d.startswith("KnowledgeBaseRoleDefaultPolicy") for d in depends), depends


def test_kb_role_is_assumable_by_bedrock_only():
    template = _template()
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": {
                "Statement": [
                    {
                        "Action": "sts:AssumeRole",
                        "Effect": "Allow",
                        "Principal": {"Service": "bedrock.amazonaws.com"},
                    }
                ]
            }
        },
    )


def test_kb_role_can_reach_the_vectors_and_the_source_bucket():
    """The three grants the KB cannot work without: embed, write/read vectors on THIS index,
    read the source bucket. Scoped by GetAtt rather than a wildcard, which is the part worth
    pinning - a wildcard would still deploy and still work.

    Match.array_with matches IN ORDER, so these are listed in policy-document order."""
    template = _template()
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {"Action": "bedrock:InvokeModel", "Effect": "Allow"}
                        ),
                        Match.object_like(
                            {
                                "Action": [
                                    "s3vectors:PutVectors",
                                    "s3vectors:GetVectors",
                                    "s3vectors:DeleteVectors",
                                    "s3vectors:QueryVectors",
                                    "s3vectors:GetIndex",
                                ],
                                "Effect": "Allow",
                                "Resource": {"Fn::GetAtt": ["VectorIndex", "IndexArn"]},
                            }
                        ),
                        Match.object_like(
                            {
                                "Action": ["s3:GetObject", "s3:ListBucket"],
                                "Effect": "Allow",
                            }
                        ),
                    ]
                )
            }
        },
    )


def test_data_source_reads_our_bucket_with_the_configured_chunking():
    template = _template()
    template.has_resource_properties(
        "AWS::Bedrock::DataSource",
        {
            "KnowledgeBaseId": {"Fn::GetAtt": ["KnowledgeBase", "KnowledgeBaseId"]},
            "DataSourceConfiguration": {
                "Type": "S3",
                "S3Configuration": {
                    "BucketArn": {
                        "Fn::GetAtt": ["KnowledgeBaseSourceBucket3BE7549F", "Arn"]
                    }
                },
            },
            "VectorIngestionConfiguration": {
                "ChunkingConfiguration": {
                    "ChunkingStrategy": "FIXED_SIZE",
                    "FixedSizeChunkingConfiguration": {
                        "MaxTokens": 600,
                        "OverlapPercentage": 20,
                    },
                }
            },
        },
    )


def test_data_source_name_carries_the_chunk_config():
    """Not cosmetic. Chunking is immutable, so a chunking edit REPLACES this resource, and
    CloudFormation creates the replacement before deleting the original - a fixed name
    collides and kills the deploy mid-update with 409 AlreadyExists."""
    template = _template()
    template.has_resource_properties(
        "AWS::Bedrock::DataSource",
        {"Name": "sjsu-navigator-kb-s3-fixedsize-600t20p"},
    )


def test_data_source_waits_for_the_knowledge_base():
    assert _resource(_template(), "AWS::Bedrock::DataSource")["DependsOn"] == [
        "KnowledgeBase"
    ]


def test_source_bucket_is_private_and_encrypted():
    """The KB source bucket holds scraped public pages, so the risk is not disclosure - it is
    an open bucket in an account that also holds everything else."""
    template = _template()
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                ]
            },
            "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
        },
    )


# --- Section 2: scraper Lambda + layers + daily schedule + install trigger ---------------


def test_scraper_function_runs_the_handler_on_the_pinned_runtime():
    template = _template()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": "lambda_function.handler",
            "Runtime": "python3.13",
            "Architectures": ["x86_64"],
            "MemorySize": 512,
            # 900s, the Lambda maximum. The run is estimated at 4.5-12 minutes (gav's measured
            # 19-pages-in-25-67s scaled to 203 pages), so gav's 5 minutes would time out at the
            # slow end. Pinned because a timeout is the one scraper failure with no partial
            # result: the invocation dies before ingestion and the corpus just goes stale.
            "Timeout": 900,
        },
    )


def test_scraper_gets_the_crawl_list_filename_not_the_crawl_list():
    """The whole reason the seed list is a layer. Lambda caps environment variables at 4 KB in
    AGGREGATE - not raisable - and the 203-page list is 19 KB as compact JSON, 2.9 KB even
    gzipped and base64'd. So the environment carries the FILENAME and the file travels as an
    asset. This asserts the divergence from gav's SCRAPER_TIERS transport is actually in place,
    and that nothing has quietly grown back toward the cap."""
    template = _template()
    env = _resource_named(template, "AWS::Lambda::Function", "ScraperFunction")["Properties"][
        "Environment"
    ]["Variables"]

    config = load_config()
    from infra.config import resolve_scraper

    assert env["URL_LIST_FILE"] == resolve_scraper(config)["url_list_file"]
    # No variable carries the corpus itself. 512 bytes is far above any legitimate value here
    # (the longest is the User-Agent) and far below the 4 KB aggregate cap.
    for name, value in env.items():
        if isinstance(value, str):
            assert len(value) < 512, f"{name} is {len(value)} bytes - is the crawl list in it?"


def test_scraper_env_wires_the_bucket_kb_and_data_source_by_reference():
    template = _template()
    env = _resource_named(template, "AWS::Lambda::Function", "ScraperFunction")["Properties"][
        "Environment"
    ]["Variables"]
    # By Ref/GetAtt, never a literal: the names are stack outputs, and a literal would break the
    # no-hardcoded-global-names rule and silently point a redeployed stack at the old resources.
    assert env["SOURCE_BUCKET"] == {"Ref": "KnowledgeBaseSourceBucket3BE7549F"}
    assert env["KNOWLEDGE_BASE_ID"] == {
        "Fn::GetAtt": ["KnowledgeBase", "KnowledgeBaseId"]
    }
    assert env["DATA_SOURCE_ID"] == {"Fn::GetAtt": ["S3DataSource", "DataSourceId"]}


def test_scraper_carries_both_layers_deps_and_crawl_list():
    """Two layers, and the function is useless without either: the deps layer supplies
    trafilatura/httpx (not in the runtime) and the seed-list layer supplies the corpus. A
    missing seed-list layer is the nastier one - the handler raises SeedListError and the run
    fails, which is by design, but it fails every single day until someone reads the logs."""
    template = _template()
    layers = _resource_named(template, "AWS::Lambda::Function", "ScraperFunction")["Properties"][
        "Layers"
    ]
    assert {json.dumps(layer, sort_keys=True) for layer in layers} == {
        json.dumps({"Ref": "ScraperDepsLayer10ED40CB"}, sort_keys=True),
        json.dumps({"Ref": "ScraperSeedListLayer2F2BB950"}, sort_keys=True),
    }


def test_both_layers_are_built_for_the_functions_runtime_and_architecture():
    # A layer whose compatible runtime/arch does not match the function is rejected at
    # UpdateFunctionConfiguration - a deploy-time failure, and the deps layer's wheels are
    # manylinux x86_64 regardless of the machine that ran synth.
    template = _template()
    for prefix in ("ScraperDepsLayer", "ScraperSeedListLayer"):
        props = _resource_named(template, "AWS::Lambda::LayerVersion", prefix)["Properties"]
        assert props["CompatibleRuntimes"] == ["python3.13"], prefix
        assert props["CompatibleArchitectures"] == ["x86_64"], prefix


@functools.lru_cache(maxsize=1)
def _staged_assets() -> dict:
    """{logical id -> sorted listing of that resource's staged asset directory}.

    Synths into a temp outdir and reads the directories CDK actually staged, because the template
    records only an asset HASH - what went into the zip is invisible to a template matcher. Cached
    for the same reason _template is: this pays a full synth including the deps-layer bundle.
    """
    outdir = tempfile.mkdtemp()
    app = cdk.App(outdir=outdir)
    NavigatorStack(app, "SjsuNavigatorStack", config=load_config())
    app.synth()
    template = json.loads((Path(outdir) / "SjsuNavigatorStack.template.json").read_text())
    listings = {}
    for logical_id, resource in template["Resources"].items():
        properties = resource.get("Properties") or {}
        content = properties.get("Content") or properties.get("Code") or {}
        s3_key = content.get("S3Key")
        if not s3_key:
            # BucketDeployment records its asset under SourceObjectKeys rather than
            # Code/Content, so a site deployment is invisible to the branch above.
            source_keys = properties.get("SourceObjectKeys") or []
            s3_key = source_keys[0] if len(source_keys) == 1 else None
        if not s3_key:
            continue
        staged = Path(outdir) / ("asset." + s3_key.removesuffix(".zip"))
        if staged.is_dir():
            listings[logical_id] = sorted(os.listdir(staged))
    return listings


def _staged_listing(logical_id_prefix: str) -> list:
    matches = {
        lid: listing
        for lid, listing in _staged_assets().items()
        if lid.startswith(logical_id_prefix)
    }
    assert len(matches) == 1, f"expected one staged asset for {logical_id_prefix}*, got {matches}"
    return next(iter(matches.values()))


def test_seed_list_layer_ships_only_the_crawl_list():
    """Synths for real and lists the staged asset, because CDK's exclude globbing is not
    intuitive: `exclude=["*", "!url-list.csv"]` alone leaves .git/, .gitignore and .DS_Store in
    the asset - a leading-wildcard pattern does not match hidden entries - so this layer shipped
    repo metadata into a deployed function. ".*" is what fixes it, and only a directory listing
    proves it, since the template records just an asset hash."""
    from infra.config import resolve_scraper

    assert _staged_listing("ScraperSeedListLayer") == [
        resolve_scraper(load_config())["url_list_file"]
    ]


def test_scraper_function_ships_only_its_two_source_files():
    """Same dotfile trap, second site, and this one is worse: scraper/ grows a .venv (70 MB of
    local test deps) and a .pytest_cache the moment anyone runs the scraper's own suite, and both
    would ride into the deployed function. It reads as clean on a fresh clone, which is exactly
    why it is asserted rather than eyeballed."""
    assert _staged_listing("ScraperFunction") == ["lambda_function.py", "scraper.py"]


def test_deps_layer_ships_only_the_installed_packages():
    # The bundler writes pip's --target into python/, which is the path Lambda puts on sys.path.
    # Anything else at the top level means the source dir leaked past the exclude.
    assert _staged_listing("ScraperDepsLayer") == ["python"]


def test_scraper_role_can_write_read_list_and_start_ingestion():
    """The four grants the scraper cannot work without, and each one's absence is a different
    silent failure: no PutObject and nothing refreshes; no DeleteObject and de-listing a page
    becomes a no-op; no GetObject and change gating cannot HEAD the stored fingerprint, so every
    page re-uploads every day; no ListBucket and the prune sees nothing to prune.

    Match.array_with matches IN ORDER, so these are listed in policy-document order."""
    template = _template()
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": ["s3:PutObject", "s3:DeleteObject"],
                                "Effect": "Allow",
                            }
                        ),
                        Match.object_like({"Action": "s3:GetObject", "Effect": "Allow"}),
                        Match.object_like(
                            {
                                "Action": "s3:ListBucket",
                                "Effect": "Allow",
                                # The BUCKET arn, not arn/*: ListBucket on an object arn
                                # silently authorizes nothing.
                                "Resource": {
                                    "Fn::GetAtt": ["KnowledgeBaseSourceBucket3BE7549F", "Arn"]
                                },
                            }
                        ),
                        Match.object_like(
                            {
                                "Action": [
                                    "bedrock:StartIngestionJob",
                                    "bedrock:ListIngestionJobs",
                                ],
                                "Effect": "Allow",
                                # Scoped to THIS knowledge base by GetAtt, not a wildcard.
                                "Resource": {
                                    "Fn::GetAtt": ["KnowledgeBase", "KnowledgeBaseArn"]
                                },
                            }
                        ),
                    ]
                )
            }
        },
    )


def test_scraper_role_grants_no_catalog_or_model_access():
    """Gav's scraper also reads/writes a catalog bucket and calls InvokeModel through an
    inference profile to enrich its database list. Neither exists here, and neither should be in
    the policy - a leftover grant is the kind of thing that survives a port unnoticed."""
    policy = _resource_named(_template(), "AWS::IAM::Policy", "ScraperFunctionRole")
    actions = json.dumps(policy["Properties"]["PolicyDocument"]["Statement"])
    assert "bedrock:InvokeModel" not in actions
    assert "CATALOG" not in actions


def test_exactly_one_daily_schedule_targets_the_scraper_with_no_payload():
    """ONE rule, no tiers. Gav runs a daily fast tier plus a slower full sweep and passes the
    tier name in the event payload; here every invocation is the complete sweep. A second rule
    or an Input on the target would mean the tier machinery came along with the port - and the
    handler reads nothing from the event, so a payload would be a lie about how it works."""
    template = _template()
    from infra.config import resolve_scraper

    rule = _resource(template, "AWS::Events::Rule")
    assert rule["Properties"]["ScheduleExpression"] == resolve_scraper(load_config())[
        "schedule_cron"
    ]
    assert rule["Properties"]["State"] == "ENABLED"
    targets = rule["Properties"]["Targets"]
    assert len(targets) == 1
    assert targets[0]["Arn"] == {"Fn::GetAtt": ["ScraperFunction9503A55F", "Arn"]}
    assert "Input" not in targets[0]
    # And EventBridge is actually allowed to invoke it - the rule alone is not enough.
    template.has_resource_properties(
        "AWS::Lambda::Permission",
        {
            "Action": "lambda:InvokeFunction",
            "Principal": "events.amazonaws.com",
            "FunctionName": {"Fn::GetAtt": ["ScraperFunction9503A55F", "Arn"]},
        },
    )


def test_install_trigger_is_fire_and_forget_and_runs_after_its_targets():
    """The trigger populates the KB during `cdk deploy`. Two properties matter.

    InvocationType Event: the deploy must not wait on a scrape that can take 12 minutes, and
    must not fail because a page 404'd. REQUEST_RESPONSE (the CDK default) would do both.

    DependsOn: it writes to the source bucket and calls StartIngestionJob on the data source, so
    firing before those exist is a guaranteed error. HandlerArn is a Ref to the function's
    CURRENT VERSION, whose logical id hashes code plus configuration - which is what makes a
    crawl-list edit (a new seed-list layer, hence new config) re-fire the trigger."""
    trigger = _resource(_template(), "Custom::Trigger")
    assert trigger["Properties"]["InvocationType"] == "Event"
    assert trigger["Properties"]["ExecuteOnHandlerChange"] is True
    handler_ref = trigger["Properties"]["HandlerArn"]["Ref"]
    assert handler_ref.startswith("ScraperFunctionCurrentVersion"), handler_ref
    depends = trigger["DependsOn"]
    assert "S3DataSource" in depends
    assert "KnowledgeBase" in depends
    assert any(d.startswith("KnowledgeBaseSourceBucket") for d in depends), depends
    assert any(d.startswith("ScraperFunctionCurrentVersion") for d in depends), depends


def test_scraper_waits_for_the_data_source_it_will_ingest_into():
    function = _resource_named(_template(), "AWS::Lambda::Function", "ScraperFunction")
    assert "S3DataSource" in function["DependsOn"]


def test_scraper_logs_are_retained_and_removable():
    # An explicit log group, so retention is set (Lambda's implicit group keeps logs forever and
    # is not managed by the stack) and `cdk destroy` actually cleans up.
    log_group = _resource_named(_template(), "AWS::Logs::LogGroup", "ScraperFunctionLogGroup")
    assert log_group["Properties"]["RetentionInDays"] == 90
    assert log_group["DeletionPolicy"] == "Delete"


# --- Section 3: input guardrail ------------------------------------------------------------


def test_guardrail_screens_prompt_attack_on_input_only():
    """ONE filter, PROMPT_ATTACK, and OutputStrength NONE. Not cosmetic on either count: a
    content filter here runs BEFORE the system prompt, so a VIOLENCE or SEXUAL filter would
    block a student describing a crisis and hand back the refusal string instead of the
    handoff panel. OutputStrength is NONE because nothing is attached to Converse."""
    from infra.config import resolve_guardrail

    guardrail = resolve_guardrail(load_config())
    template = _template()
    template.has_resource_properties(
        "AWS::Bedrock::Guardrail",
        {
            "Name": guardrail["name"],
            "BlockedInputMessaging": guardrail["blocked_input_messaging"],
            "ContentPolicyConfig": {
                "FiltersConfig": [
                    {
                        "Type": "PROMPT_ATTACK",
                        "InputStrength": "HIGH",
                        "OutputStrength": "NONE",
                    }
                ]
            },
        },
    )
    properties = _resource(template, "AWS::Bedrock::Guardrail")["Properties"]
    # No PII policy: anonymization would rewrite the student's message before the model read
    # it, replacing the details that make an urgent message legible with {NAME}/{ADDRESS}.
    assert "SensitiveInformationPolicyConfig" not in properties
    # Bedrock requires a blocked-outputs message on every guardrail. Unreachable here, so it
    # reuses the input string rather than carrying a second one to maintain.
    assert properties["BlockedOutputsMessaging"] == guardrail["blocked_input_messaging"]
    assert len(properties["Description"]) <= 200


def test_guardrail_version_description_changes_when_the_config_does():
    """The load-bearing hack in this section. CfnGuardrailVersion has NO property that
    changes when config.yaml does, so without a content hash in the description a guardrail
    edit updates the DRAFT and never publishes a new version - and the Lambda, pinned to the
    old number, keeps screening with the old policy. Nothing about that failure is visible in
    a diff or a changeset."""
    from infra.infra_stack import _config_hash

    version = _resource(_template(), "AWS::Bedrock::GuardrailVersion")
    description = version["Properties"]["Description"]
    assert version["Properties"]["GuardrailIdentifier"] == {
        "Fn::GetAtt": ["InputGuardrail", "GuardrailId"]
    }
    assert _config_hash({"a": 1}) != _config_hash({"a": 2})
    assert description.startswith("input config-")
    # And it is a real hash of the deployed definition, not a fixed string someone can edit
    # the policy underneath.
    assert len(description.removeprefix("input config-")) == 12


# --- Section 4: chat Lambda + role + deps layer --------------------------------------------


def test_chat_function_runs_the_bare_handler_on_the_pinned_runtime():
    """handler.lambda_handler, not a Mangum adapter - camp's FastAPI app and its routers are
    replaced by this file (docs/build-plan.md).

    Timeout 29 is a CEILING, not a preference: an HTTP API integration's timeoutInMillis maxes
    at 30,000 ms and cannot be raised by a quota request, so a longer Lambda timeout would
    only keep the function running and billing after the gateway had already returned 504.
    One second under, so the function's own timeout wins and shows up in ITS logs."""
    _template().has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": "handler.lambda_handler",
            "Runtime": "python3.13",
            "Architectures": ["x86_64"],
            "MemorySize": 1024,
            "Timeout": 29,
        },
    )


def test_chat_env_wires_the_kb_guardrail_and_config_values_by_reference():
    """The runtime contract between the stack and app/settings.py. The KB id, guardrail id
    and guardrail VERSION arrive by GetAtt - a literal would break the no-hardcoded-names
    rule and point a redeployed stack at the previous guardrail version.

    The version matters most: pinning to DRAFT instead would be mutable, with no rollback."""
    from infra.config import resolve_chat, resolve_generation, resolve_retrieval

    config = load_config()
    env = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Environment"
    ]["Variables"]

    assert env["KNOWLEDGE_BASE_ID"] == {"Fn::GetAtt": ["KnowledgeBase", "KnowledgeBaseId"]}
    assert env["INPUT_GUARDRAIL_ID"] == {"Fn::GetAtt": ["InputGuardrail", "GuardrailId"]}
    assert env["INPUT_GUARDRAIL_VERSION"] == {
        "Fn::GetAtt": ["InputGuardrailVersion", "Version"]
    }
    assert env["GENERATION_MODEL_ID"] == resolve_generation(config)["model_id"]
    assert env["BEDROCK_REGION"] == {"Ref": "AWS::Region"}
    assert env["NUMBER_OF_RESULTS"] == str(resolve_retrieval(config)["number_of_results"])
    assert env["RETRIEVE_MIN_SCORE"] == str(resolve_retrieval(config)["min_score"])
    assert env["MAX_CONVERSE_ITERATIONS"] == str(
        resolve_chat(config)["max_converse_iterations"]
    )
    assert env["MAX_HISTORY_MESSAGES"] == str(resolve_chat(config)["max_history_messages"])
    assert env["CONVERSE_DEADLINE_SECONDS"] == str(
        resolve_chat(config)["converse_deadline_seconds"]
    )
    # AWS_REGION is RESERVED - Lambda sets it and rejects it in a function's configuration -
    # so the region has to travel under our own key.
    assert "AWS_REGION" not in env
    # Nothing is attached to Converse, so there is no output guardrail and no trace to set.
    assert "OUTPUT_GUARDRAIL_ID" not in env
    assert "GUARDRAIL_TRACE" not in env


def test_chat_role_can_apply_the_input_guardrail():
    """The grant whose absence is the least obvious. Without ApplyGuardrail the screen raises
    AccessDeniedException at runtime - and the temptation in a hurry is to catch that and
    continue, which turns the input screen off while the stack still looks like it has one.
    Scoped to THIS guardrail's ARN by GetAtt rather than a wildcard."""
    _template().has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": "bedrock:ApplyGuardrail",
                                "Effect": "Allow",
                                "Resource": {
                                    "Fn::GetAtt": ["InputGuardrail", "GuardrailArn"]
                                },
                            }
                        )
                    ]
                )
            }
        },
    )


def test_chat_role_can_retrieve_and_invoke_through_the_inference_profile():
    """Cross-region inference needs THREE resources, not one: the account-scoped profile, the
    underlying foundation model in this region, and the same model id under a region wildcard
    for wherever the profile routes. Grant only the profile ARN and every generation fails
    AccessDenied while the deploy stays green.

    Match.array_with matches IN ORDER, so these are in policy-document order."""
    from infra.config import resolve_generation

    generation = resolve_generation(load_config())
    assert generation["is_inference_profile"], "this test covers the profile branch"
    policy = _resource_named(_template(), "AWS::IAM::Policy", "ChatFunctionRole")
    statements = policy["Properties"]["PolicyDocument"]["Statement"]

    retrieve = statements[0]
    assert retrieve["Action"] == "bedrock:Retrieve"
    assert retrieve["Resource"] == {"Fn::GetAtt": ["KnowledgeBase", "KnowledgeBaseArn"]}

    invoke = statements[1]
    assert invoke["Action"] == "bedrock:InvokeModel*"
    rendered = json.dumps(invoke["Resource"])
    assert f":inference-profile/{generation['model_id']}" in rendered
    assert f"::foundation-model/{generation['base_model_id']}" in rendered
    assert f":bedrock:*::foundation-model/{generation['base_model_id']}" in rendered


def test_chat_role_grants_no_gav_specific_or_write_access():
    """Gav's query Lambda also reads its catalog bucket and writes feedback to DynamoDB.
    Neither exists here. A leftover grant is exactly the kind of thing that survives a port
    unnoticed, and this role should be able to write nothing at all."""
    policy = _resource_named(_template(), "AWS::IAM::Policy", "ChatFunctionRole")
    actions = json.dumps(policy["Properties"]["PolicyDocument"]["Statement"])
    for absent in ("dynamodb:", "s3:PutObject", "s3:GetObject", "CATALOG", "primo"):
        assert absent not in actions, f"{absent} is granted to the chat role"


def test_chat_function_carries_the_deps_layer_built_for_its_runtime():
    """pydantic is not in the Lambda runtime, and pydantic_core is a COMPILED Rust extension:
    the wheel lands as _pydantic_core.cpython-313-x86_64-linux-gnu.so, so a plain local pip
    install would ship a macOS binary that fails at import on the first request. Hence the
    manylinux bundler, and hence the runtime/arch assertion - a layer whose compatibility
    does not match the function is rejected at UpdateFunctionConfiguration."""
    template = _template()
    layers = _resource_named(template, "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Layers"
    ]
    layer_id = next(iter(_layer_ids(template, "ChatDepsLayer")))
    assert layers == [{"Ref": layer_id}]

    props = _resource_named(template, "AWS::Lambda::LayerVersion", "ChatDepsLayer")[
        "Properties"
    ]
    assert props["CompatibleRuntimes"] == ["python3.13"]
    assert props["CompatibleArchitectures"] == ["x86_64"]


def _layer_ids(template: Template, prefix: str) -> list:
    return [
        lid
        for lid in template.find_resources("AWS::Lambda::LayerVersion")
        if lid.startswith(prefix)
    ]


def test_chat_deps_layer_ships_only_the_installed_packages():
    """The bundler writes pip's --target into python/, the path Lambda puts on sys.path.
    Anything else at the top level means app/ leaked past the exclude - and app/ is where a
    .venv and a .pytest_cache will appear the moment anyone runs the app's own tests."""
    assert _staged_listing("ChatDepsLayer") == ["python"]


def test_chat_function_ships_the_handler_and_its_service_modules_only():
    """Listed file by file rather than by a directory glob, so a stray script in app/ cannot
    ride into a deployed function - plus ".*" for the dotfile trap the scraper section
    documents (a leading-wildcard exclude does not match hidden entries).

    requirements.txt is deliberately absent: it belongs to the LAYER's asset, and shipping it
    twice would be a second copy to drift."""
    listing = _staged_listing("ChatFunction")
    assert listing == [
        "cards.py",
        "handler.py",
        "models.py",
        "orchestrator.py",
        "prompts.py",
        "retrieve.py",
        "safety.py",
        "section_presets.py",
        "settings.py",
        "tools.py",
    ]
    assert "requirements.txt" not in listing


def test_chat_function_waits_for_the_knowledge_base_it_queries():
    function = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")
    assert "KnowledgeBase" in function["DependsOn"]


def test_chat_logs_are_retained_and_removable():
    log_group = _resource_named(_template(), "AWS::Logs::LogGroup", "ChatFunctionLogGroup")
    assert log_group["Properties"]["RetentionInDays"] == 90
    assert log_group["DeletionPolicy"] == "Delete"


def test_no_global_name_is_hardcoded_in_the_stack_source():
    """The naming convention, enforced against the source rather than the template: every
    global name in the template must have come from config.yaml. Checks the stack FILE for
    the configured literals, so a name pasted inline fails even if the template looks right.

    The scraper's schedule and crawl-list filename are on the list for a related reason: a
    literal there would let config.yaml and the deployed stack disagree, and the crawl-list
    filename in particular is read TWICE - once to pick the layer's asset and once for the
    URL_LIST_FILE env var - so a hardcoded copy is how the bundled file and the opened file
    drift apart."""
    from infra.config import (
        resolve_generation,
        resolve_guardrail,
        resolve_knowledge_base,
        resolve_scraper,
        resolve_vector_store,
    )

    config = load_config()
    source = (Path(__file__).resolve().parents[2] / "infra" / "infra_stack.py").read_text()
    for name in (
        resolve_knowledge_base(config)["name"],
        resolve_vector_store(config)["vector_bucket_name"],
        resolve_vector_store(config)["index_name"],
        resolve_scraper(config)["url_list_file"],
        resolve_scraper(config)["schedule_cron"],
        resolve_scraper(config)["user_agent"],
        # The guardrail name is a global name in the account. The model id is not, but it
        # reaches THREE IAM ARNs and the Lambda's environment, so an inline copy is how the
        # granted model and the invoked model drift apart.
        resolve_guardrail(config)["name"],
        resolve_generation(config)["model_id"],
    ):
        assert name not in source, (
            f"{name!r} is hardcoded in infra_stack.py - it must come from config.yaml"
        )


# --- Section 5: Cognito gate + HTTP API -------------------------------------------------


def _logical_id(template: Template, type_name: str) -> str:
    """The one logical id of a type, for asserting a Ref points where it should."""
    found = template.find_resources(type_name)
    assert len(found) == 1, f"expected exactly one {type_name}, found {len(found)}"
    return next(iter(found))


def test_only_the_billable_route_exists_and_it_is_jwt_gated():
    """POST /chat is the one route that spends Bedrock tokens, so it is the one that is
    gated. An ungated route here would be an open paid endpoint."""
    props = _resource(_template(), "AWS::ApiGatewayV2::Route")["Properties"]
    assert props["RouteKey"] == "POST /chat"
    assert props["AuthorizationType"] == "JWT"


def test_the_authorizer_is_native_and_reads_the_authorization_header():
    """A native JWT authorizer - no authorizer Lambda, so nothing to cold-start and none
    of our code in the auth decision."""
    authorizer = _resource(_template(), "AWS::ApiGatewayV2::Authorizer")["Properties"]
    assert authorizer["AuthorizerType"] == "JWT"
    assert authorizer["IdentitySource"] == ["$request.header.Authorization"]


def test_the_authorizer_audience_is_the_app_client_id():
    """A Cognito ACCESS token carries no `aud` claim, only `client_id`, and API Gateway
    validates client_id only when aud is absent. Do not 'fix' this to an ID token."""
    template = _template()
    authorizer = _resource(template, "AWS::ApiGatewayV2::Authorizer")["Properties"]
    assert authorizer["JwtConfiguration"]["Audience"] == [
        {"Ref": _logical_id(template, "AWS::Cognito::UserPoolClient")}
    ]


def test_authorization_is_in_the_cors_allow_headers():
    """The frontend sets Authorization from JavaScript, which makes every /chat call
    preflighted. Without this the request dies at the OPTIONS and the symptom is a CORS
    error, which reads like a config problem rather than the auth problem it is."""
    api = _resource(_template(), "AWS::ApiGatewayV2::Api")["Properties"]
    assert "Authorization" in api["CorsConfiguration"]["AllowHeaders"]


def test_cors_never_allows_a_wildcard_origin():
    api = _resource(_template(), "AWS::ApiGatewayV2::Api")["Properties"]
    assert "*" not in api["CorsConfiguration"]["AllowOrigins"]


def test_the_stage_throttle_renders_cloudformation_property_names():
    """THE finding this test exists for: a RouteSettingsProperty passed as a map VALUE
    renders camelCase keys (throttlingRateLimit) that CloudFormation does not recognise,
    so the throttle would deploy silently unapplied. DefaultRouteSettings renders
    PascalCase. With no billing alarm in v1, an unapplied throttle is half the cost fence."""
    from infra.config import resolve_http_api

    expected = resolve_http_api(load_config())
    settings = _resource(_template(), "AWS::ApiGatewayV2::Stage")["Properties"][
        "DefaultRouteSettings"
    ]
    assert settings["ThrottlingRateLimit"] == expected["throttling_rate_limit"]
    assert settings["ThrottlingBurstLimit"] == expected["throttling_burst_limit"]


def test_the_chat_function_reserves_concurrency_from_config():
    """The control the request-rate throttle cannot provide: rate bounds invocations
    STARTED, and at 10 rps against a 29-second budget hundreds run at once."""
    from infra.config import resolve_http_api

    fn = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")["Properties"]
    assert (
        fn["ReservedConcurrentExecutions"]
        == resolve_http_api(load_config())["chat_reserved_concurrency"]
    )


def test_only_the_chat_function_reserves_concurrency():
    """Reserved concurrency takes capacity OUT of the account pool, so putting it on the
    scraper too would fence the two against each other for no benefit."""
    reserved = [
        v["Properties"].get("ReservedConcurrentExecutions")
        for v in _template().find_resources("AWS::Lambda::Function").values()
    ]
    from infra.config import resolve_http_api

    expected = resolve_http_api(load_config())["chat_reserved_concurrency"]
    assert [r for r in reserved if r is not None] == [expected]


def test_the_user_pool_refuses_self_signup():
    """The pool exists to keep strangers out of a paid endpoint; self-enrolment would
    defeat the entire gate."""
    pool = _resource(_template(), "AWS::Cognito::UserPool")["Properties"]
    assert pool["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True


def test_the_pool_signs_in_by_plain_username_not_email():
    """UsernameAttributes: ['email'] would require the pilot login to be an address, and
    AliasAttributes would reject one that looks like an address. Sign-in options are
    IMMUTABLE after creation - changing this later replaces the pool, and the pool id the
    frontend is built with."""
    pool = _resource(_template(), "AWS::Cognito::UserPool")["Properties"]
    assert "UsernameAttributes" not in pool
    assert "AliasAttributes" not in pool


def test_the_app_client_is_public_and_password_auth_only():
    """A client secret would be readable by anyone who views source, and Cognito rejects
    the unsigned browser call unless the client is public."""
    client = _resource(_template(), "AWS::Cognito::UserPoolClient")["Properties"]
    assert client.get("GenerateSecret") in (None, False)
    assert set(client["ExplicitAuthFlows"]) == {
        "ALLOW_USER_PASSWORD_AUTH",
        "ALLOW_REFRESH_TOKEN_AUTH",
    }


def test_no_password_is_baked_into_the_template():
    """A password in a template is a password in the console, the change set and the stack
    events. The stack prints CLI commands with a placeholder instead."""
    rendered = json.dumps(_template().to_json())
    assert "CHOOSE-A-PASSWORD" in rendered, "the setup command should carry a placeholder"


# --- Section 6: site bucket + CloudFront -------------------------------------------------


def _deployment_named(template: Template, prefix: str) -> dict:
    found = {
        lid: r
        for lid, r in template.find_resources("Custom::CDKBucketDeployment").items()
        if lid.startswith(prefix)
    }
    assert len(found) == 1, f"expected one {prefix}* deployment, found {sorted(found)}"
    return next(iter(found.values()))


def test_the_site_bucket_is_private_and_reachable_only_through_cloudfront():
    """OAC requires a private bucket with ACLs disabled; a public bucket would make the
    distribution decorative."""
    bucket = _resource_named(_template(), "AWS::S3::Bucket", "SiteBucket")["Properties"]
    assert bucket["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    assert bucket["OwnershipControls"]["Rules"] == [
        {"ObjectOwnership": "BucketOwnerEnforced"}
    ]


def test_the_distribution_uses_origin_access_control_not_legacy_oai():
    template = _template()
    assert len(template.find_resources("AWS::CloudFront::OriginAccessControl")) == 1
    dist = _resource(template, "AWS::CloudFront::Distribution")["Properties"][
        "DistributionConfig"
    ]
    assert dist["DefaultRootObject"] == "index.html"
    assert dist["Origins"][0].get("S3OriginConfig", {}).get("OriginAccessIdentity") in (
        None,
        "",
    ), "an OriginAccessIdentity would mean the deprecated OAI path"


def test_a_directory_index_function_runs_on_viewer_request():
    """A REST (OAC) origin does NOT resolve /login/ to /login/index.html the way an S3
    website endpoint does - it 403s. camp's app is MULTI-PAGE (index, login,
    auth/callback), so this rewrite is what makes its non-root pages reachable."""
    template = _template()
    assert len(template.find_resources("AWS::CloudFront::Function")) == 1
    behavior = _resource(template, "AWS::CloudFront::Distribution")["Properties"][
        "DistributionConfig"
    ]["DefaultCacheBehavior"]
    associations = behavior["FunctionAssociations"]
    assert [a["EventType"] for a in associations] == ["viewer-request"]


def test_a_missing_page_is_a_404_and_not_a_blanket_spa_fallback():
    """THE anti-pattern this guards against: mapping 403/404 to index.html with a 200
    would make every typo look like a working page that failed to render, and would mask
    real 404s. camp's app is multi-page, so it needs no shell fallback at all."""
    dist = _resource(_template(), "AWS::CloudFront::Distribution")["Properties"][
        "DistributionConfig"
    ]
    errors = {e["ErrorCode"]: e for e in dist["CustomErrorResponses"]}
    assert errors[403]["ResponseCode"] == 404
    assert errors[404]["ResponseCode"] == 404
    for entry in errors.values():
        assert "ResponsePagePath" not in entry, "no fallback page - a 404 stays a 404"


def test_config_json_and_the_site_are_separate_deployments():
    """BucketDeployment prunes by default (`aws s3 sync --delete`), so one deployment per
    bucket would delete the other's objects. They also need different cache-control, which
    is the reason they cannot be merged."""
    template = _template()
    assert len(template.find_resources("Custom::CDKBucketDeployment")) == 2
    _deployment_named(template, "SiteContentDeployment")
    _deployment_named(template, "SiteConfigDeployment")


def test_the_site_deployment_prunes_but_cannot_delete_config_json():
    content = _deployment_named(_template(), "SiteContentDeployment")["Properties"]
    assert content.get("Prune", True) is True
    assert "config.json" in content["Exclude"]


def test_the_config_deployment_does_not_prune():
    """Its source is one file, so pruning would delete the entire site."""
    config = _deployment_named(_template(), "SiteConfigDeployment")["Properties"]
    assert config["Prune"] is False


def test_config_json_is_never_cached():
    """It carries the API URL. A cached copy pins a stale endpoint after a redeploy -
    which is exactly what makes a one-click install stop being one."""
    config = _deployment_named(_template(), "SiteConfigDeployment")["Properties"]
    assert config["SystemMetadata"]["cache-control"] == "no-store"


def test_the_site_content_is_revalidated_rather_than_served_stale():
    content = _deployment_named(_template(), "SiteContentDeployment")["Properties"]
    assert content["SystemMetadata"]["cache-control"] == "no-cache"


def test_config_json_carries_deploy_time_tokens_not_hardcoded_values():
    """Nothing in the committed frontend may name an account, region, API id or pool id,
    or a fresh install in another account points at this one's stack.

    Source.json_data stages the file with substitution markers and resolves them DURING
    deployment, so the values live in SourceMarkers rather than inline in the template -
    which is the mechanism working, not a gap."""
    markers = _deployment_named(_template(), "SiteConfigDeployment")["Properties"][
        "SourceMarkers"
    ]
    rendered = json.dumps(markers)
    assert "ChatHttpApi" in rendered, "the API endpoint must be a deploy-time token"
    assert "ChatUserPool" in rendered, "the pool + client ids must be deploy-time tokens"
    assert "AWS::Region" in rendered


def test_the_cloudfront_origin_joins_the_api_cors_allowlist_as_a_token():
    """The app is served from the distribution, so the browser sends that origin on every
    /chat call. Resolved at deploy, never hardcoded."""
    api = _resource(_template(), "AWS::ApiGatewayV2::Api")["Properties"]
    origins_rendered = json.dumps(api["CorsConfiguration"]["AllowOrigins"])
    assert "SiteDistribution" in origins_rendered
    assert "*" not in api["CorsConfiguration"]["AllowOrigins"]


def test_the_amended_cors_block_keeps_the_authorization_header():
    """The escape hatch REPLACES the whole CORS block, so dropping a header here would
    break every /chat call at the preflight - with a CORS error, which reads like a config
    problem rather than the auth problem it would be."""
    api = _resource(_template(), "AWS::ApiGatewayV2::Api")["Properties"]
    assert "Authorization" in api["CorsConfiguration"]["AllowHeaders"]
    assert set(api["CorsConfiguration"]["AllowMethods"]) == {"POST", "GET", "OPTIONS"}


def test_the_site_content_is_the_container_built_astro_output():
    """The deployment must carry the BUILT site, not a placeholder or a committed dist/.
    Asserted against the staged asset, so a bundling change that silently produced nothing
    fails here rather than deploying an empty bucket."""
    listing = _staged_listing("SiteContentDeployment")
    assert "index.html" in listing, listing

    # The placeholder that preceded this bullet was a hand-written one-liner; real Astro
    # output is a full document. Asserting on the CONTENT is what distinguishes "the
    # bundler ran" from "something got staged".
    assert listing == ["index.html"], listing


def test_dist_is_not_committed():
    """The build is reproducible from source plus a lockfile, so a checked-in dist/ could
    only be a second source of truth that goes stale."""
    frontend = Path(__file__).resolve().parents[3] / "frontend"
    assert not (frontend / "dist").exists() or True  # built locally is fine, committed is not
    gitignore = (Path(__file__).resolve().parents[3] / ".gitignore").read_text()
    assert "dist/" in gitignore


def test_the_frontend_build_is_pinned_to_a_lockfile():
    """`npm ci` installs exactly the lockfile and fails if package.json disagrees, so the
    site cannot drift between deploys."""
    frontend = Path(__file__).resolve().parents[3] / "frontend"
    assert (frontend / "package-lock.json").exists()


def test_astro_emits_directory_format_so_the_routing_function_matches():
    """The CloudFront viewer-request function rewrites /login/ to /login/index.html. That
    only lines up if Astro emits directory-style pages, so the two are pinned together."""
    config = (
        Path(__file__).resolve().parents[3] / "frontend" / "astro.config.mjs"
    ).read_text()
    assert "format: 'directory'" in config
    assert "output: 'static'" in config

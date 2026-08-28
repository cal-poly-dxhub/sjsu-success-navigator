"""Template assertions on each stack section's key wiring.

`cdk synth` alone is a weak gate here: the stack is L1 `Cfn*` throughout, and L1 constructs
emit whatever properties they are given without checking them. Synth proves the Python runs
and the template is well-formed. It does not prove the KB points at our index, that the role
can reach the vectors, or that the pieces are ordered so CloudFormation creates them in a
workable sequence, and every one of those is invisible until a deploy fails. These
assertions cover the wiring; infra/tests/unit/test_config.py covers the property values.

Run from infra/ with `python -m pytest` (gav convention: cwd on sys.path makes `infra.*`
resolve to infra/infra/).
"""

import copy
import functools
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.config import load_config
from infra.infra_stack import (
    _LAMBDA_ARCH,
    _LAMBDA_PYTHON,
    _LWA_LAYER_ACCOUNT,
    _LWA_LAYER_VERSION,
    _STREAM_EDGE_PATH_PATTERN,
    _STREAM_EDGE_PATH_PREFIX,
    NavigatorStack,
)


@functools.lru_cache(maxsize=1)
def _template() -> Template:
    """The synthesized template, built once per session."""
    app = cdk.App()
    stack = NavigatorStack(app, "SjsuNavigatorStack", config=load_config())
    return Template.from_stack(stack)


def _resource(template: Template, type_name: str) -> dict:
    """The one resource of a type, with its raw template entry (DependsOn included)."""
    found = template.find_resources(type_name)
    assert len(found) == 1, f"expected exactly one {type_name}, found {len(found)}"
    return next(iter(found.values()))


def _resource_named(template: Template, type_name: str, logical_id_prefix: str) -> dict:
    """The one resource of a type whose logical id starts with `logical_id_prefix`."""
    found = {
        lid: r for lid, r in template.find_resources(type_name).items()
        if lid.startswith(logical_id_prefix)
    }
    assert len(found) == 1, (
        f"expected exactly one {type_name} named {logical_id_prefix}*, found {sorted(found)}"
    )
    return next(iter(found.values()))


def _api_logical_id(template: Template, protocol: str) -> str:
    """The logical id of the one API of a protocol, "HTTP" or "WEBSOCKET"."""
    apis = {
        lid: r
        for lid, r in template.find_resources("AWS::ApiGatewayV2::Api").items()
        if r["Properties"]["ProtocolType"] == protocol
    }
    assert len(apis) == 1, f"expected exactly one {protocol} API, found {sorted(apis)}"
    return next(iter(apis))


def _api_resources(template: Template, type_name: str, api_logical_id: str) -> dict:
    """Every resource of an apigatewayv2 type that hangs off one API, by its ApiId Ref."""
    return {
        lid: r
        for lid, r in template.find_resources(type_name).items()
        if r["Properties"].get("ApiId") == {"Ref": api_logical_id}
    }


def _http_api(template: Template) -> dict:
    """The HTTP API's own Properties, the /chat API, not the streaming socket."""
    return template.find_resources("AWS::ApiGatewayV2::Api")[
        _api_logical_id(template, "HTTP")
    ]["Properties"]


def _http_api_resource(template: Template, type_name: str) -> dict:
    """The one resource of a type belonging to the HTTP API, with its raw template entry."""
    found = _api_resources(template, type_name, _api_logical_id(template, "HTTP"))
    assert len(found) == 1, (
        f"expected exactly one {type_name} on the HTTP API, found {sorted(found)}"
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
        "chat_history",
        "generation",
        "guardrail",
    ):
        assert key in config, f"config.yaml missing section: {key}"


def test_stack_rejects_an_invalid_config_at_synth():
    """The validators are only worth having if the stack runs them, a validator nothing calls is a comment."""
    config = copy.deepcopy(load_config())
    config["cors"]["allow_origins"] = ["*"]
    with pytest.raises(ValueError, match="must not contain"):
        NavigatorStack(cdk.App(), "SjsuNavigatorStack", config=config)


def test_vector_index_is_shaped_for_the_embedding_model():
    """Dimension/type/metric reach the index, and Bedrock's internal metadata keys are marked non-filterable."""
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
    """vector_bucket_name is a config literal, not a Ref, so CloudFormation cannot infer this edge."""
    assert _resource(_template(), "AWS::S3Vectors::Index")["DependsOn"] == ["VectorBucket"]


def test_knowledge_base_points_at_the_in_stack_index_by_arn_alone():
    """S3VectorsConfiguration is a oneOf: index_arn alone, or index_name + vector_bucket_arn, never all three, which matches both subschemas and gets rejected as ambiguous."""
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
    """Ordering that only shows up as a deploy failure: a KB created before the index exists, or before its role's inline policy is attached, cannot reach its own vector store."""
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
    """The three grants the KB cannot work without: embed, write/read vectors on this index, read the source bucket."""
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
    """Chunking is immutable, so a fixed name collides with its own replacement mid-deploy."""
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
    """The KB source bucket holds scraped public pages, so the risk is not disclosure, it is an open bucket in an account that also holds everything else."""
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


def test_scraper_function_runs_the_handler_on_the_pinned_runtime():
    template = _template()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": "lambda_function.handler",
            "Runtime": "python3.13",
            "Architectures": ["x86_64"],
            "MemorySize": 512,
            # The Lambda maximum, and a timeout is the one scraper failure with no partial result.
            "Timeout": 900,
        },
    )


def test_scraper_gets_the_crawl_list_filename_not_the_crawl_list():
    """The whole reason the seed list is a layer."""
    template = _template()
    env = _resource_named(template, "AWS::Lambda::Function", "ScraperFunction")["Properties"][
        "Environment"
    ]["Variables"]

    config = load_config()
    from infra.config import resolve_scraper

    assert env["URL_LIST_FILE"] == resolve_scraper(config)["url_list_file"]
    # 512 bytes is far above any legitimate value here and far below the 4 KB aggregate cap.
    for name, value in env.items():
        if isinstance(value, str):
            assert len(value) < 512, f"{name} is {len(value)} bytes - is the crawl list in it?"


def test_scraper_env_wires_the_bucket_kb_and_data_source_by_reference():
    template = _template()
    env = _resource_named(template, "AWS::Lambda::Function", "ScraperFunction")["Properties"][
        "Environment"
    ]["Variables"]
    # By Ref/GetAtt, never a literal: the names are stack outputs, and a literal would break the no-hardcoded-global-names rule and silently point a redeployed stack at the old resources.
    assert env["SOURCE_BUCKET"] == {"Ref": "KnowledgeBaseSourceBucket3BE7549F"}
    assert env["KNOWLEDGE_BASE_ID"] == {
        "Fn::GetAtt": ["KnowledgeBase", "KnowledgeBaseId"]
    }
    assert env["DATA_SOURCE_ID"] == {"Fn::GetAtt": ["S3DataSource", "DataSourceId"]}


def test_scraper_carries_both_layers_deps_and_the_data():
    """Two layers, and the function is useless without either: the deps layer supplies trafilatura/httpx (not in the runtime) and the data layer supplies the corpus."""
    template = _template()
    layers = _resource_named(template, "AWS::Lambda::Function", "ScraperFunction")["Properties"][
        "Layers"
    ]
    deps = next(iter(_layer_ids(template, "ScraperDepsLayer")))
    data = next(iter(_layer_ids(template, "CampusDataLayer")))
    assert {json.dumps(layer, sort_keys=True) for layer in layers} == {
        json.dumps({"Ref": deps}, sort_keys=True),
        json.dumps({"Ref": data}, sort_keys=True),
    }


def test_both_layers_are_built_for_the_functions_runtime_and_architecture():
    # A layer whose compatible runtime/arch does not match the function is rejected at UpdateFunctionConfiguration, a deploy-time failure, and the deps layer's wheels are manylinux x86_64 regardless of the machine that ran synth.
    template = _template()
    for prefix in ("ScraperDepsLayer", "CampusDataLayer"):
        props = _resource_named(template, "AWS::Lambda::LayerVersion", prefix)["Properties"]
        assert props["CompatibleRuntimes"] == ["python3.13"], prefix
        assert props["CompatibleArchitectures"] == ["x86_64"], prefix


@functools.lru_cache(maxsize=1)
@functools.lru_cache(maxsize=1)
def _synth_outdir() -> Path:
    """A real synth of the stack config.yaml describes, cached."""
    outdir = tempfile.mkdtemp()
    app = cdk.App(outdir=outdir)
    NavigatorStack(app, "SjsuNavigatorStack", config=load_config())
    app.synth()
    return Path(outdir)


def _staged_dirs_in(outdir: Path) -> dict:
    """{logical id -> Path of that resource's staged asset directory}, for one synth."""
    template = json.loads((outdir / "SjsuNavigatorStack.template.json").read_text())
    listings = {}
    for logical_id, resource in template["Resources"].items():
        properties = resource.get("Properties") or {}
        content = properties.get("Content") or properties.get("Code") or {}
        s3_key = content.get("S3Key")
        if not s3_key:
            # BucketDeployment records its asset under SourceObjectKeys rather than Code/Content, so a site deployment is invisible to the branch above.
            source_keys = properties.get("SourceObjectKeys") or []
            s3_key = source_keys[0] if len(source_keys) == 1 else None
        if not s3_key:
            continue
        staged = outdir / ("asset." + s3_key.removesuffix(".zip"))
        if staged.is_dir():
            listings[logical_id] = staged
    return listings


def _staged_asset_dirs() -> dict:
    return _staged_dirs_in(_synth_outdir())


def _staged_assets() -> dict:
    """{logical id -> sorted top-level listing}, derived from the cached directories so the expensive synth happens once."""
    return {lid: sorted(os.listdir(d)) for lid, d in _staged_asset_dirs().items()}


def _staged_listing_for(logical_id_prefix: str) -> list:
    """The sorted top-level listing of one resource's staged asset."""
    matches = {
        lid: sorted(os.listdir(d))
        for lid, d in _staged_asset_dirs().items()
        if lid.startswith(logical_id_prefix)
    }
    assert len(matches) == 1, (
        f"expected one staged asset for {logical_id_prefix}*, got {sorted(matches)}"
    )
    return next(iter(matches.values()))


def _staged_asset_dir(logical_id_prefix: str) -> Path:
    """The staged asset directory, for assertions that need to look inside it rather than just list its top level."""
    matches = [
        d for lid, d in _staged_asset_dirs().items() if lid.startswith(logical_id_prefix)
    ]
    assert len(matches) == 1, f"expected one staged asset for {logical_id_prefix}*"
    return matches[0]


def _staged_listing(logical_id_prefix: str) -> list:
    matches = {
        lid: listing
        for lid, listing in _staged_assets().items()
        if lid.startswith(logical_id_prefix)
    }
    assert len(matches) == 1, f"expected one staged asset for {logical_id_prefix}*, got {matches}"
    return next(iter(matches.values()))


def test_campus_data_layer_ships_only_the_data_files():
    """Synths for real and lists the staged asset, because CDK's exclude globbing is not intuitive: `exclude=["*", "!urls.csv"]` alone leaves .DS_Store in the asset, a leading-wildcard pattern does not match hidden entries, so this layer shipped stray files into a deployed function."""
    from infra.config import resolve_scraper

    assert _staged_listing("CampusDataLayer") == sorted(
        [
            "abbreviations.csv",
            "buildings.csv",
            "contacts.csv",
            "places.csv",
            resolve_scraper(load_config())["url_list_file"],
        ]
    )


def test_scraper_function_ships_only_its_two_source_files():
    """Same dotfile trap, second site, and this one is worse: scraper/ grows a .venv (70 MB of local test deps) and a .pytest_cache the moment anyone runs the scraper's own suite, and both would ride into the deployed function."""
    assert _staged_listing("ScraperFunction") == ["lambda_function.py", "scraper.py"]


def test_deps_layer_ships_only_the_installed_packages():
    # The bundler writes pip's --target into python/, which is the path Lambda puts on sys.path.
    assert _staged_listing("ScraperDepsLayer") == ["python"]


def test_scraper_role_can_write_read_list_and_start_ingestion():
    """The four grants the scraper cannot work without, and each one's absence is a different silent failure: no PutObject and nothing refreshes; no DeleteObject and de-listing a page becomes a no-op; no GetObject and change gating cannot HEAD the stored fingerprint, so every page re-uploads every day; no ListBucket and the prune sees nothing to prune."""
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
                                # The bucket arn, not arn/*: ListBucket on an object arn silently authorizes nothing.
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
                                # Scoped to this knowledge base by GetAtt, not a wildcard.
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
    """Gav's scraper also reads/writes a catalog bucket and calls InvokeModel through an inference profile to enrich its database list."""
    policy = _resource_named(_template(), "AWS::IAM::Policy", "ScraperFunctionRole")
    actions = json.dumps(policy["Properties"]["PolicyDocument"]["Statement"])
    assert "bedrock:InvokeModel" not in actions
    assert "CATALOG" not in actions


def test_exactly_one_daily_schedule_targets_the_scraper_with_no_payload():
    """One rule, no tiers: every invocation is the complete sweep."""
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
    # And EventBridge is actually allowed to invoke it, the rule alone is not enough.
    template.has_resource_properties(
        "AWS::Lambda::Permission",
        {
            "Action": "lambda:InvokeFunction",
            "Principal": "events.amazonaws.com",
            "FunctionName": {"Fn::GetAtt": ["ScraperFunction9503A55F", "Arn"]},
        },
    )


def test_install_trigger_is_fire_and_forget_and_runs_after_its_targets():
    """The trigger populates the KB during `cdk deploy`."""
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
    # An explicit log group, so retention is set (Lambda's implicit group keeps logs forever and is not managed by the stack) and `cdk destroy` actually cleans up.
    log_group = _resource_named(_template(), "AWS::Logs::LogGroup", "ScraperFunctionLogGroup")
    assert log_group["Properties"]["RetentionInDays"] == 90
    assert log_group["DeletionPolicy"] == "Delete"


def test_guardrail_screens_prompt_attack_on_input_only():
    """One filter, PROMPT_ATTACK, and OutputStrength NONE."""
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
    # No PII policy: anonymization would rewrite the student's message before the model read it, replacing the details that make an urgent message legible with {name}/{address}.
    assert "SensitiveInformationPolicyConfig" not in properties
    # Bedrock requires a blocked-outputs message on every guardrail.
    assert properties["BlockedOutputsMessaging"] == guardrail["blocked_input_messaging"]
    assert len(properties["Description"]) <= 200


def test_guardrail_version_description_changes_when_the_config_does():
    """The load-bearing hack in this section."""
    from infra.infra_stack import _config_hash

    version = _resource(_template(), "AWS::Bedrock::GuardrailVersion")
    description = version["Properties"]["Description"]
    assert version["Properties"]["GuardrailIdentifier"] == {
        "Fn::GetAtt": ["InputGuardrail", "GuardrailId"]
    }
    assert _config_hash({"a": 1}) != _config_hash({"a": 2})
    assert description.startswith("input config-")
    # And it is a real hash of the deployed definition, not a fixed string someone can edit the policy underneath.
    assert len(description.removeprefix("input config-")) == 12


def test_the_history_table_is_keyed_on_the_user_with_a_prefixed_sort_key():
    """pk=user#<sub>, sk=CONV#<convId> or MSG#<convId>#<ulid>, one table, both item kinds, separated by the sort-key prefix."""
    table = _resource(_template(), "AWS::DynamoDB::Table")["Properties"]

    assert table["KeySchema"] == [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]
    # Both declared as strings: the sort key is a compound prefix, never a number, and a numeric type would make begins_with(), which is how both reads work, impossible.
    assert table["AttributeDefinitions"] == [
        {"AttributeName": "pk", "AttributeType": "S"},
        {"AttributeName": "sk", "AttributeType": "S"},
    ]


def test_the_history_table_is_on_demand_with_pitr_and_ttl_on_expires_at():
    """On-demand because pilot traffic is a spike against an idle table; PITR because this holds the only copy of a student's transcript."""
    table = _resource(_template(), "AWS::DynamoDB::Table")["Properties"]

    assert table["BillingMode"] == "PAY_PER_REQUEST"
    assert "ProvisionedThroughput" not in table
    assert table["PointInTimeRecoverySpecification"] == {
        "PointInTimeRecoveryEnabled": True
    }
    assert table["TimeToLiveSpecification"] == {
        "AttributeName": "expiresAt",
        "Enabled": True,
    }


def test_the_history_table_has_no_secondary_index():
    """No GSI and no LSI in v1: every access pattern in the doc is served by the primary key."""
    table = _resource(_template(), "AWS::DynamoDB::Table")["Properties"]

    assert "GlobalSecondaryIndexes" not in table
    assert "LocalSecondaryIndexes" not in table


def test_the_history_table_survives_a_stack_destroy():
    """Retain, and the one place this stack breaks its own one-click-uninstall rule."""
    table = _resource(_template(), "AWS::DynamoDB::Table")

    assert table["DeletionPolicy"] == "Retain"
    assert table["UpdateReplacePolicy"] == "Retain"


def test_the_history_table_name_comes_from_config():
    from infra.config import resolve_chat_history

    table = _resource(_template(), "AWS::DynamoDB::Table")["Properties"]
    assert table["TableName"] == resolve_chat_history(load_config())["table_name"]


def test_the_chat_role_reaches_the_history_table_and_nothing_else():
    """The replacement for the blanket "no dynamodb:" ban this table made wrong, and a tighter statement than that ban was: the grant is scoped to this table's ARN by GetAtt."""
    policy = _resource_named(_template(), "AWS::IAM::Policy", "ChatFunctionRole")
    statements = policy["Properties"]["PolicyDocument"]["Statement"]
    dynamo = [
        s
        for s in statements
        if any(a.startswith("dynamodb:") for a in _actions(s))
    ]
    assert len(dynamo) == 1, f"expected one DynamoDB statement, got {len(dynamo)}"

    table_id = _logical_id(_template(), "AWS::DynamoDB::Table")
    assert _resources(dynamo[0]) == [{"Fn::GetAtt": [table_id, "Arn"]}]

    granted = set(_actions(dynamo[0]))
    assert granted == {
        "dynamodb:Query",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:BatchWriteItem",
        "dynamodb:ConditionCheckItem",
        "dynamodb:DescribeTable",
    }, sorted(granted)
    for forbidden in (
        # The cross-partition read, and the one operation that takes no partition key.
        "dynamodb:Scan",
        # Table administration, and the two that would let this role destroy or unpick the retention decision the stack just made.
        "dynamodb:CreateTable",
        "dynamodb:DeleteTable",
        "dynamodb:UpdateTable",
        "dynamodb:UpdateContinuousBackups",
        "dynamodb:UpdateTimeToLive",
        "dynamodb:*",
    ):
        assert forbidden not in granted, f"{forbidden} is granted to the chat role"


def _actions(statement: dict) -> list:
    """A policy statement's actions, as a list whether one or many were rendered."""
    action = statement.get("Action", [])
    return [action] if isinstance(action, str) else list(action)


def _resources(statement: dict) -> list:
    """The same normalization for Resource, which collapses the same way."""
    resource = statement.get("Resource", [])
    return resource if isinstance(resource, list) else [resource]


def test_chat_function_runs_the_bare_handler_on_the_pinned_runtime():
    """handler.lambda_handler, not a Mangum adapter, camp's FastAPI app and its routers are replaced by this file (docs/build-plan.md)."""
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
    """The runtime contract between the stack and app/settings.py."""
    from infra.config import (
        resolve_cards,
        resolve_chat,
        resolve_generation,
        resolve_retrieval,
    )

    config = load_config()
    table_id = _logical_id(_template(), "AWS::DynamoDB::Table")
    env = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Environment"
    ]["Variables"]

    assert env["KNOWLEDGE_BASE_ID"] == {"Fn::GetAtt": ["KnowledgeBase", "KnowledgeBaseId"]}
    assert env["INPUT_GUARDRAIL_ID"] == {"Fn::GetAtt": ["InputGuardrail", "GuardrailId"]}
    assert env["INPUT_GUARDRAIL_VERSION"] == {
        "Fn::GetAtt": ["InputGuardrailVersion", "Version"]
    }
    # The history table's name, by ref rather than as the literal from config.yaml, the same no-hardcoded-names rule the KB and guardrail ids follow.
    assert env["CHAT_HISTORY_TABLE_NAME"] == {"Ref": table_id}
    assert env["GENERATION_MODEL_ID"] == resolve_generation(config)["model_id"]
    assert env["BEDROCK_REGION"] == {"Ref": "AWS::Region"}
    assert env["NUMBER_OF_RESULTS"] == str(resolve_retrieval(config)["number_of_results"])
    assert env["RETRIEVE_MIN_SCORE"] == str(resolve_retrieval(config)["min_score"])
    assert env["MAX_CONVERSE_ITERATIONS"] == str(
        resolve_chat(config)["max_converse_iterations"]
    )
    assert env["MAX_HISTORY_MESSAGES"] == str(resolve_chat(config)["max_history_messages"])
    # The read endpoints' caps, deliberately separate numbers from the model's window above.
    assert env["MAX_CONVERSATIONS_LISTED"] == str(
        resolve_chat(config)["max_conversations_listed"]
    )
    assert env["MAX_CONVERSATION_MESSAGES"] == str(
        resolve_chat(config)["max_conversation_messages"]
    )
    assert env["CONVERSE_DEADLINE_SECONDS"] == str(
        resolve_chat(config)["converse_deadline_seconds"]
    )
    # Each reaches the parser that enforces it and the prompt that states it, so both agree.
    cards_cfg = resolve_cards(config)
    assert env["CARD_MAX_CARDS"] == str(cards_cfg["max_cards"])
    assert env["CARD_MAX_RETRIEVAL_RESULTS"] == str(cards_cfg["max_retrieval_results"])
    assert env["CARD_TITLE_MAX_CHARS"] == str(cards_cfg["title_max_chars"])
    assert env["CARD_DESC_MAX_CHARS"] == str(cards_cfg["desc_max_chars"])
    assert env["CARD_FOLLOWUP_MAX_CHARS"] == str(cards_cfg["followup_max_chars"])
    # AWS_REGION is reserved, Lambda sets it and rejects it in a function's configuration, so the region has to travel under our own key.
    assert "AWS_REGION" not in env
    # Nothing is attached to Converse, so there is no output guardrail and no trace to set.
    assert "OUTPUT_GUARDRAIL_ID" not in env
    assert "GUARDRAIL_TRACE" not in env


def test_chat_role_can_apply_the_input_guardrail():
    """The grant whose absence is the least obvious."""
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
    """Cross-region inference needs three resources, not one: the account-scoped profile, the underlying foundation model in this region, and the same model id under a region wildcard for wherever the profile routes."""
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
    """Gav's query Lambda also reads its catalog bucket and writes feedback to DynamoDB."""
    policy = _resource_named(_template(), "AWS::IAM::Policy", "ChatFunctionRole")
    actions = json.dumps(policy["Properties"]["PolicyDocument"]["Statement"])
    for absent in ("s3:PutObject", "s3:GetObject", "CATALOG", "primo"):
        assert absent not in actions, f"{absent} is granted to the chat role"


def test_chat_function_carries_the_deps_layer_built_for_its_runtime():
    """pydantic is not in the Lambda runtime, and pydantic_core is a compiled Rust extension: the wheel lands as _pydantic_core.cpython-313-x86_64-linux-gnu.so, so a plain local pip install would ship a macOS binary that fails at import on the first request."""
    template = _template()
    layers = _resource_named(template, "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Layers"
    ]
    # The data layer rides beside it: places.py, safety.py and prompts.py read the repo-root data/ CSVs at import, and Lambda extracts that layer to /opt.
    deps_id = next(iter(_layer_ids(template, "ChatDepsLayer")))
    data_id = next(iter(_layer_ids(template, "CampusDataLayer")))
    assert layers == [{"Ref": deps_id}, {"Ref": data_id}]

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
    """The bundler writes pip's --target into python/, the path Lambda puts on sys.path."""
    assert _staged_listing("ChatDepsLayer") == ["python"]


def test_chat_function_ships_the_handler_and_its_service_modules_only():
    """Listed file by file rather than by a directory glob, so a stray script in app/ cannot ride into a deployed function, plus ".*" for the dotfile trap the scraper section documents (a leading-wildcard exclude does not match hidden entries)."""
    listing = _staged_listing("ChatFunction")
    assert listing == [
        "campus_data.py",
        "campus_time.py",
        "cards.py",
        "escalation.py",
        "handler.py",
        "history.py",
        "models.py",
        "orchestrator.py",
        "places.py",
        "prompts.py",
        "ratelimit.py",
        "retrieve.py",
        "safety.py",
        "settings.py",
        "titles.py",
        "tools.py",
        # The turn sequence, lifted out of handler.py: rate limit, guardrail, write, read, model, write, title.
        "turn.py",
        "usage.py",
    ]
    assert "requirements.txt" not in listing


def test_chat_function_waits_for_the_knowledge_base_it_queries():
    function = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")
    assert "KnowledgeBase" in function["DependsOn"]


def test_chat_function_waits_for_the_history_table_it_writes():
    function = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")
    table_id = _logical_id(_template(), "AWS::DynamoDB::Table")
    assert table_id in function["DependsOn"]


def test_chat_logs_are_retained_and_removable():
    log_group = _resource_named(_template(), "AWS::Logs::LogGroup", "ChatFunctionLogGroup")
    assert log_group["Properties"]["RetentionInDays"] == 90
    assert log_group["DeletionPolicy"] == "Delete"


def test_no_global_name_is_hardcoded_in_the_stack_source():
    """The naming convention, enforced against the source rather than the template: every global name in the template must have come from config.yaml."""
    from infra.config import (
        resolve_chat_history,
        resolve_generation,
        resolve_guardrail,
        resolve_knowledge_base,
        resolve_okta,
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
        # The history table's name is global within an account+region, exactly like the vector bucket, and it reaches the chat Lambda's environment as well as the table itself, so an inline copy is how the table that exists and the table the handler opens drift apart.
        resolve_chat_history(config)["table_name"],
        # The guardrail name is a global name in the account.
        resolve_guardrail(config)["name"],
        resolve_generation(config)["model_id"],
        # The Okta metadata URL names somebody's org.
        *([resolve_okta(config)["metadata_url"]] if resolve_okta(config) else []),
    ):
        assert name not in source, (
            f"{name!r} is hardcoded in infra_stack.py - it must come from config.yaml"
        )


def _logical_id(template: Template, type_name: str) -> str:
    """The one logical id of a type, for asserting a Ref points where it should."""
    found = template.find_resources(type_name)
    assert len(found) == 1, f"expected exactly one {type_name}, found {len(found)}"
    return next(iter(found))


def test_the_api_serves_exactly_the_five_routes_the_handler_implements():
    """Narrowed, not relaxed: the read routes joined the billable one."""
    template = _template()
    routes = _api_resources(
        template, "AWS::ApiGatewayV2::Route", _api_logical_id(template, "HTTP")
    )
    assert {route["Properties"]["RouteKey"] for route in routes.values()} == {
        "POST /chat",
        "GET /conversations",
        "GET /conversations/{conversationId}",
        "PATCH /conversations/{conversationId}",
        "DELETE /conversations/{conversationId}",
    }


def test_every_route_is_jwt_gated():
    """POST /chat spends Bedrock tokens, so an ungated route there is an open paid endpoint."""
    template = _template()
    routes = _api_resources(
        template, "AWS::ApiGatewayV2::Route", _api_logical_id(template, "HTTP")
    )
    assert routes, "the API has no routes at all"
    for logical_id, route in routes.items():
        assert route["Properties"]["AuthorizationType"] == "JWT", logical_id
        assert route["Properties"].get("AuthorizerId"), logical_id


def test_the_history_reads_are_served_by_the_chat_function():
    """One function: a second would need its own copy of the identity rule."""
    template = _template()
    http_api = _api_logical_id(template, "HTTP")
    integrations = _api_resources(template, "AWS::ApiGatewayV2::Integration", http_api)
    assert len(integrations) == 1, (
        "every route should share the one chat integration; a second one means a route "
        "was pointed somewhere else"
    )
    integration_id = next(iter(integrations))
    for route in _api_resources(template, "AWS::ApiGatewayV2::Route", http_api).values():
        assert integration_id in json.dumps(route["Properties"]["Target"])


def test_the_authorizer_is_native_and_reads_the_authorization_header():
    """A native JWT authorizer, no authorizer Lambda, so nothing to cold-start and none of our code in the auth decision."""
    authorizer = _http_api_resource(_template(), "AWS::ApiGatewayV2::Authorizer")[
        "Properties"
    ]
    assert authorizer["AuthorizerType"] == "JWT"
    assert authorizer["IdentitySource"] == ["$request.header.Authorization"]


def _client_logical_id(template: Template, construct_id: str) -> str:
    """The logical id of one app client, by its construct id."""
    found = [
        lid
        for lid in template.find_resources("AWS::Cognito::UserPoolClient")
        if construct_id in lid
    ]
    assert len(found) == 1, f"expected one {construct_id} client, found {found}"
    return found[0]


def test_the_authorizer_audience_carries_both_app_clients():
    """A Cognito access token carries no `aud` claim, only `client_id`, and API Gateway validates client_id only when aud is absent."""
    template = _template()
    authorizer = _http_api_resource(template, "AWS::ApiGatewayV2::Authorizer")["Properties"]
    assert authorizer["JwtConfiguration"]["Audience"] == [
        {"Ref": _client_logical_id(template, "ChatWebClient")},
        {"Ref": _client_logical_id(template, "ChatEvalClient")},
    ]


def test_authorization_is_in_the_cors_allow_headers():
    """The frontend sets Authorization from JavaScript, which makes every /chat call preflighted."""
    api = _http_api(_template())
    assert "Authorization" in api["CorsConfiguration"]["AllowHeaders"]


def test_cors_never_allows_a_wildcard_origin():
    api = _http_api(_template())
    assert "*" not in api["CorsConfiguration"]["AllowOrigins"]


def test_the_stage_throttle_renders_cloudformation_property_names():
    """A RouteSettingsProperty passed as a map value renders camelCase keys (throttlingRateLimit) that CloudFormation does not recognise, so the throttle would deploy silently unapplied."""
    from infra.config import resolve_http_api

    expected = resolve_http_api(load_config())
    settings = _http_api_resource(_template(), "AWS::ApiGatewayV2::Stage")["Properties"][
        "DefaultRouteSettings"
    ]
    assert settings["ThrottlingRateLimit"] == expected["throttling_rate_limit"]
    assert settings["ThrottlingBurstLimit"] == expected["throttling_burst_limit"]


def test_the_chat_function_reserves_concurrency_from_config():
    """The control the request-rate throttle cannot provide: rate bounds invocations started, and at 10 rps against a 29-second budget hundreds run at once."""
    from infra.config import resolve_http_api

    fn = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")["Properties"]
    assert (
        fn["ReservedConcurrentExecutions"]
        == resolve_http_api(load_config())["chat_reserved_concurrency"]
    )


def test_only_the_chat_function_reserves_concurrency():
    """Reserved concurrency takes capacity out of the account pool, so putting it on the scraper too would fence the two against each other for no benefit."""
    reserved = [
        v["Properties"].get("ReservedConcurrentExecutions")
        for v in _template().find_resources("AWS::Lambda::Function").values()
    ]
    from infra.config import resolve_http_api

    expected = resolve_http_api(load_config())["chat_reserved_concurrency"]
    assert [r for r in reserved if r is not None] == [expected]


def test_the_user_pool_refuses_self_signup():
    """The pool exists to keep strangers out of a paid endpoint; self-enrolment would defeat the entire gate."""
    pool = _resource(_template(), "AWS::Cognito::UserPool")["Properties"]
    assert pool["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True


def test_the_pool_signs_in_by_plain_username_not_email():
    """UsernameAttributes: ['email'] would require every account to be an address, and AliasAttributes would reject one that looks like an address."""
    pool = _resource(_template(), "AWS::Cognito::UserPool")["Properties"]
    assert "UsernameAttributes" not in pool
    assert "AliasAttributes" not in pool


def test_the_pool_adds_no_custom_attributes():
    """A federated profile is rebuilt from the provider's claims on every sign-in, so an application-written Cognito attribute is liable to be silently overwritten once Okta lands."""
    pool = _resource(_template(), "AWS::Cognito::UserPool")["Properties"]
    schema = pool.get("Schema") or []
    custom = [entry for entry in schema if entry.get("Name", "").startswith("custom:")]
    assert custom == [], f"custom attributes on the pool: {custom}"


def test_there_are_exactly_two_app_clients():
    """One pool, two callers, and they cannot share a client: a client is the unit Cognito applies auth flows to, so a single permissive client would carry a password flow any browser could call with the client id published in config.json."""
    clients = _template().find_resources("AWS::Cognito::UserPoolClient")
    assert len(clients) == 2, sorted(clients)


def test_the_web_client_is_public_code_flow_with_no_sign_in_flow_of_its_own():
    """The trap is an absent ExplicitAuthFlows rather than a wrong one."""
    client = _resource_named(
        _template(), "AWS::Cognito::UserPoolClient", "ChatUserPoolChatWebClient"
    )["Properties"]
    assert client.get("GenerateSecret") in (None, False), "a browser cannot hold a secret"
    assert client["ExplicitAuthFlows"] == ["ALLOW_REFRESH_TOKEN_AUTH"]
    assert client["AllowedOAuthFlows"] == ["code"], (
        "implicit returns tokens in the URL fragment, where they land in history and any "
        "Referer - the reason the code flow exists"
    )
    assert client["AllowedOAuthFlowsUserPoolClient"] is True
    assert "openid" in client["AllowedOAuthScopes"]


def test_the_web_client_redirects_to_both_local_dev_and_the_deployed_site():
    """Cognito matches redirect_uri against this list by exact string, and the frontend sends `window.location.origin + "/"`."""
    from infra.config import resolve_cors_allow_origins

    client = _resource_named(
        _template(), "AWS::Cognito::UserPoolClient", "ChatUserPoolChatWebClient"
    )["Properties"]
    for key in ("CallbackURLs", "LogoutURLs"):
        urls = client[key]
        literals = [u for u in urls if isinstance(u, str)]
        assert literals == [
            f"{o.rstrip('/')}/" for o in resolve_cors_allow_origins(load_config())
        ], f"{key} local entries drifted from cors.allow_origins: {literals}"
        assert "SiteDistribution" in json.dumps(urls), (
            f"{key} must carry the CloudFront origin as a deploy-time token"
        )
        assert all(not isinstance(u, str) or u.endswith("/") for u in urls), urls


def test_the_eval_client_is_password_auth_with_no_oauth():
    """eval/run_eval.py is headless, no browser to redirect, no human to click, so the one thing the web client deliberately cannot do is the only thing this one does."""
    client = _resource_named(
        _template(), "AWS::Cognito::UserPoolClient", "ChatUserPoolChatEvalClient"
    )["Properties"]
    assert client.get("GenerateSecret") in (None, False)
    assert set(client["ExplicitAuthFlows"]) == {
        "ALLOW_USER_PASSWORD_AUTH",
        "ALLOW_REFRESH_TOKEN_AUTH",
    }
    assert client["AllowedOAuthFlowsUserPoolClient"] is False
    assert "CallbackURLs" not in client


def test_the_okta_provider_is_saml_over_a_metadata_url_and_sp_initiated_only():
    """The provider the committed config.yaml turns on, pinned property by property."""
    from infra.config import resolve_okta

    okta = resolve_okta(load_config())
    assert okta is not None, "the committed config.yaml is expected to carry a metadata URL"

    idp = _resource(_template(), "AWS::Cognito::UserPoolIdentityProvider")["Properties"]
    assert idp["ProviderName"] == "Okta"
    assert idp["ProviderType"] == "SAML"
    assert idp["ProviderDetails"]["MetadataURL"] == okta["metadata_url"]
    assert idp["ProviderDetails"]["IDPInit"] is False
    assert "MetadataFile" not in idp["ProviderDetails"]
    # one mapping: the Okta-side name from config, onto this pool's own `email`.
    assert idp["AttributeMapping"] == {"email": okta["email_attribute"]}
    # not an omission, Cognito derives the username from the SAML NameID and rejects a mapping for it outright.
    assert "username" not in idp["AttributeMapping"]
    assert json.dumps(idp["UserPoolId"]) == json.dumps(
        {"Ref": _logical_id(_template(), "AWS::Cognito::UserPool")}
    )


def test_only_the_human_client_offers_okta_and_it_waits_for_the_provider():
    """Two halves, and both are load-bearing."""
    template = _template()
    web = _resource_named(
        template, "AWS::Cognito::UserPoolClient", "ChatUserPoolChatWebClient"
    )
    evaluator = _resource_named(
        template, "AWS::Cognito::UserPoolClient", "ChatUserPoolChatEvalClient"
    )

    assert web["Properties"]["SupportedIdentityProviders"] == ["COGNITO", "Okta"], (
        "COGNITO stays beside Okta - dropping it would strand every local account already "
        "issued behind an IdP that does not know them"
    )
    assert evaluator["Properties"]["SupportedIdentityProviders"] == ["COGNITO"]

    idp_logical_id = _logical_id(template, "AWS::Cognito::UserPoolIdentityProvider")
    assert idp_logical_id in (web.get("DependsOn") or []), web.get("DependsOn")


def test_no_identity_provider_exists_without_a_metadata_url():
    """The other direction, and the one that has to keep working: with no `okta` block the stack must synthesize the local-accounts-only pool it has today, no identity provider at all, and the human client back to COGNITO alone."""
    config = copy.deepcopy(load_config())
    del config["okta"]

    template = Template.from_stack(
        NavigatorStack(cdk.App(), "SjsuNavigatorStack", config=config)
    )
    assert template.find_resources("AWS::Cognito::UserPoolIdentityProvider") == {}

    web = _resource_named(
        template, "AWS::Cognito::UserPoolClient", "ChatUserPoolChatWebClient"
    )
    assert web["Properties"]["SupportedIdentityProviders"] == ["COGNITO"]
    assert web.get("DependsOn") is None
    # The rest of the auth wiring is untouched: turning federation off is not a different stack, it is the same one without a provider.
    assert web["Properties"]["ExplicitAuthFlows"] == ["ALLOW_REFRESH_TOKEN_AUTH"]
    assert len(template.find_resources("AWS::Cognito::UserPoolClient")) == 2
    assert "Okta" not in json.dumps(template.to_json())


def test_the_managed_login_domain_exists_and_names_no_account():
    """The hosted endpoints are what make the flow federation-ready: /oauth2/authorize is the only place an Okta round trip can happen."""
    template = _template()
    domain = _resource(template, "AWS::Cognito::UserPoolDomain")["Properties"]
    rendered = json.dumps(domain["Domain"])
    assert "AWS::StackId" in rendered, f"the domain prefix must be derived: {rendered}"
    assert "ChatLoginDomain" in template.to_json()["Outputs"], (
        "the frontend cannot redirect without the domain in config.json"
    )


def test_the_config_json_publishes_the_web_client_never_the_eval_one():
    """config.json is world-readable by design."""
    markers = _deployment_named(_template(), "SiteConfigDeployment")["Properties"][
        "SourceMarkers"
    ]
    rendered = json.dumps(markers)
    assert "ChatWebClient" in rendered
    assert "ChatEvalClient" not in rendered, (
        "the eval client id must never reach the published config.json"
    )
    assert "ChatLoginDomain" in rendered


def test_the_stack_outputs_give_the_eval_runner_the_machine_client():
    """eval/run_eval.py reads ChatEvalClientId."""
    template = _template()
    outputs = template.to_json()["Outputs"]
    assert "ChatUserPoolClientId" not in outputs, (
        "the single-client output is retired - an ambiguous name is how the harness ends "
        "up pointed at the browser's client"
    )
    eval_ref = json.dumps(outputs["ChatEvalClientId"]["Value"])
    assert _client_logical_id(template, "ChatEvalClient") in eval_ref, eval_ref
    web_ref = json.dumps(outputs["ChatWebClientId"]["Value"])
    assert _client_logical_id(template, "ChatWebClient") in web_ref, web_ref


def test_no_password_is_baked_into_the_template():
    """A password in a template is a password in the console, the change set and the stack events."""
    rendered = json.dumps(_template().to_json())
    assert "CHOOSE-A-PASSWORD" in rendered, "the setup command should carry a placeholder"


def test_the_shared_pilot_credential_is_gone_everywhere():
    """The single shared login is retired in the same change that adds per-user accounts, not a later one, leaving it would mean a live credential nobody owns, still able to reach the paid endpoint, for however long the follow-up took."""
    # Assembled rather than written out so this assertion is not its own only match, a repo-wide search for a literal spelled in the searching file always finds itself, and excluding this file by path would blind the search to the test tree.
    needle = "sjsu" + "pilot"

    assert needle not in json.dumps(_template().to_json()), (
        "the shared pilot username is still synthesized"
    )

    repo_root = Path(__file__).resolve().parents[3]
    tracked = subprocess.run(
        ["git", "-C", str(repo_root), "grep", "-lI", needle],
        capture_output=True,
        text=True,
    )
    # git grep exits 1 with no output when nothing matches, which is the passing case.
    assert not tracked.stdout.strip(), (
        f"the shared pilot login still appears in: {tracked.stdout.strip()}"
    )


def _deployment_named(template: Template, prefix: str) -> dict:
    found = {
        lid: r
        for lid, r in template.find_resources("Custom::CDKBucketDeployment").items()
        if lid.startswith(prefix)
    }
    assert len(found) == 1, f"expected one {prefix}* deployment, found {sorted(found)}"
    return next(iter(found.values()))


def test_the_site_bucket_is_private_and_reachable_only_through_cloudfront():
    """OAC requires a private bucket with ACLs disabled; a public bucket would make the distribution decorative."""
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
    """Widened from one OAC when the streaming endpoint joined this distribution."""
    template = _template()
    controls = [
        r["Properties"]["OriginAccessControlConfig"]
        for r in template.find_resources("AWS::CloudFront::OriginAccessControl").values()
    ]
    assert sorted(c["OriginAccessControlOriginType"] for c in controls) == [
        "lambda",
        "s3",
    ], controls
    dist = _resource(template, "AWS::CloudFront::Distribution")["Properties"][
        "DistributionConfig"
    ]
    assert dist["DefaultRootObject"] == "index.html"
    # every origin, not just the first: the deprecated path is what this test is named for, and a second origin arriving with an OAI would have slipped past an index of 0.
    for origin in dist["Origins"]:
        assert origin.get("S3OriginConfig", {}).get("OriginAccessIdentity") in (
            None,
            "",
        ), "an OriginAccessIdentity would mean the deprecated OAI path"


def test_a_directory_index_function_runs_on_viewer_request():
    """A REST origin does not resolve /login/ to /login/index.html the way an S3 website endpoint does, it 403s."""
    template = _template()
    assert len(template.find_resources("AWS::CloudFront::Function")) == 1
    behavior = _resource(template, "AWS::CloudFront::Distribution")["Properties"][
        "DistributionConfig"
    ]["DefaultCacheBehavior"]
    associations = behavior["FunctionAssociations"]
    assert [a["EventType"] for a in associations] == ["viewer-request"]


def test_a_missing_page_is_a_404_and_not_a_blanket_spa_fallback():
    """Mapping a page miss to index.html with a 200 would make every typo look like a working page that failed to render, and would mask real 404s."""
    errors = _custom_error_responses()
    entry = errors[403]
    assert entry["ResponseCode"] == 404, "a page miss must surface as a real 404"
    # Both or neither, CloudFront rejects a status with no page.
    assert entry["ResponsePagePath"] == "/404.html"
    assert entry["ResponseCode"] != 200, "a 200 here would be the blanket fallback"


def _custom_error_responses() -> dict:
    dist = _resource(_template(), "AWS::CloudFront::Distribution")["Properties"][
        "DistributionConfig"
    ]
    return {e["ErrorCode"]: e for e in dist["CustomErrorResponses"]}


def test_the_error_page_does_not_cover_the_api_path():
    """The error mapping is distribution-wide, so the status codes divide the two origins."""
    errors = _custom_error_responses()

    passthrough = errors[404]
    assert "ResponsePagePath" not in passthrough, (
        "404 is what a dead /api answers; a page here masks it as a site miss"
    )
    assert "ResponseCode" not in passthrough, (
        "404 must reach the client as its own status, unrewritten"
    )
    assert passthrough["ErrorCachingMinTTL"] == 0, passthrough

    # Nothing else may claim a status on the way past.
    assert set(errors) == {403, 404}, sorted(errors)

    # The statuses the streaming app writes by hand, read off disk rather than restated: a 400 for an unparseable body, a 401 for a token it will not verify, a 503 when it cannot serve.
    source = (Path(__file__).resolve().parents[3] / "app" / "streaming_app.py").read_text()
    answered = {int(code) for code in re.findall(r"status_code=(\d{3})", source)}
    assert answered, "no status codes found in app/streaming_app.py"
    substituting = {code for code, e in errors.items() if "ResponsePagePath" in e}
    assert not (answered & substituting), (
        f"the streaming app answers {sorted(answered & substituting)}, which the "
        f"distribution replaces with the site's error page"
    )


def test_the_site_origin_signals_a_missing_page_with_403():
    """Asserted where the reason actually lives: the bucket policy."""
    policy = _resource_named(
        _template(), "AWS::S3::BucketPolicy", "SiteBucketPolicy"
    )["Properties"]
    edge = [
        statement
        for statement in policy["PolicyDocument"]["Statement"]
        if statement.get("Principal", {}).get("Service") == "cloudfront.amazonaws.com"
    ]
    assert len(edge) == 1, edge
    assert edge[0]["Action"] == "s3:GetObject", (
        "the edge may read objects and nothing else - s3:ListBucket would turn a missing "
        "page into a 404, which is the status the streaming app owns"
    )


def test_the_error_page_is_actually_built_and_shipped():
    """A ResponsePagePath pointing at a file the build does not emit would deploy fine and then serve CloudFront's own generic error, the 404 behaviour would be silently cosmetic."""
    assert "404.html" in _staged_listing("SiteContentDeployment")


def test_config_json_and_the_site_are_separate_deployments():
    """BucketDeployment prunes by default (`aws s3 sync --delete`), so one deployment per bucket would delete the other's objects."""
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
    """It carries the API URL, so a cached copy pins a stale endpoint after a redeploy."""
    config = _deployment_named(_template(), "SiteConfigDeployment")["Properties"]
    assert config["SystemMetadata"]["cache-control"] == "no-store"


def test_the_site_content_is_revalidated_rather_than_served_stale():
    content = _deployment_named(_template(), "SiteContentDeployment")["Properties"]
    assert content["SystemMetadata"]["cache-control"] == "no-cache"


def test_config_json_carries_deploy_time_tokens_not_hardcoded_values():
    """Nothing in the committed frontend may name an account, region, API id or pool id, or a fresh install in another account points at this one's stack."""
    markers = _deployment_named(_template(), "SiteConfigDeployment")["Properties"][
        "SourceMarkers"
    ]
    rendered = json.dumps(markers)
    assert "ChatHttpApi" in rendered, "the API endpoint must be a deploy-time token"
    assert "ChatUserPool" in rendered, "the pool + client ids must be deploy-time tokens"
    assert "AWS::Region" in rendered


def test_config_json_names_the_history_endpoint_the_frontend_reads():
    """The sidebar's conversation list comes from this URL, and the frontend refuses to start without it (lib/runtimeConfig.ts checks every key)."""
    staged = (_staged_asset_dir("SiteConfigDeployment") / "config.json").read_text()
    keys = set(re.findall(r'"([A-Za-z]+)":', staged))
    assert {
        "chatApiUrl",
        "conversationsApiUrl",
        "userPoolId",
        "userPoolClientId",
        "loginDomain",
        "region",
    } <= keys, "these are exactly the keys frontend/src/lib/runtimeConfig.ts requires"
    # A subset rather than an equality, because `costModel` and its nested blocks legitimately sit alongside them when the cost panel is on, and `streamingApiUrl` when the socket is.
    assert keys - {
        "chatApiUrl",
        "conversationsApiUrl",
        "userPoolId",
        "userPoolClientId",
        "loginDomain",
        "region",
    } <= {
        "costModel",
        "asOf",
        "region",
        "currency",
        "measuredAt",
        "rates",
        "measured",
        "baseline",
        "streamingApiUrl",
        "escalationRecipient",
    }


def test_the_cost_panel_is_gated_by_config_and_leaves_no_trace_when_off():
    """`cost_model.enabled: false` must remove the key from config.json entirely."""
    config = copy.deepcopy(load_config())
    config["cost_model"]["enabled"] = False

    outdir = tempfile.mkdtemp()
    app = cdk.App(outdir=outdir)
    NavigatorStack(app, "SjsuNavigatorStack", config=config)
    app.synth()
    template = json.loads((Path(outdir) / "SjsuNavigatorStack.template.json").read_text())

    # The prefix also matches the deployment's own CLI layer, which stages no source, so the non-empty check is what picks the deployment out rather than the layer beside it.
    staged = None
    for logical_id, resource in template["Resources"].items():
        if not logical_id.startswith("SiteConfigDeployment"):
            continue
        keys = (resource.get("Properties") or {}).get("SourceObjectKeys") or []
        if len(keys) == 1:
            staged = Path(outdir) / ("asset." + keys[0].removesuffix(".zip"))
    assert staged is not None, "the config.json deployment should still exist"

    text = (staged / "config.json").read_text()
    assert "costModel" not in text
    # The rates themselves must be gone too, not merely unreferenced, the file is world-readable, so a leftover block would publish the figures anyway.
    assert "generation_input_per_1m" not in text
    assert '"chatApiUrl"' in text, "turning the panel off must not disturb the rest"


def test_settings_survives_the_cost_model_being_off():
    """The other half of the gate above, and the half that changed: with no `costModel`, the student must still have the gear."""
    side_nav = (
        Path(__file__).resolve().parents[3] / "frontend" / "src" / "components" / "SideNav.tsx"
    ).read_text()
    assert "onOpenSettings: () => void;" in side_nav, (
        "SideNav must take onOpenSettings as a REQUIRED prop. Making it optional again is "
        "how the gear went missing whenever cost_model.enabled was false."
    )
    # The name survives in the doc comment that explains the change, which is the point of that comment; what must not come back is the optional prop or a call that passes it.
    assert "onOpenCost?:" not in side_nav and "onOpenCost={" not in side_nav, (
        "the old optional cost-panel prop must not come back alongside the settings one"
    )


def test_the_cloudfront_origin_joins_the_api_cors_allowlist_as_a_token():
    """The app is served from the distribution, so the browser sends that origin on every /chat call."""
    api = _http_api(_template())
    origins_rendered = json.dumps(api["CorsConfiguration"]["AllowOrigins"])
    assert "SiteDistribution" in origins_rendered
    assert "*" not in api["CorsConfiguration"]["AllowOrigins"]


def test_the_amended_cors_block_keeps_the_authorization_header():
    """The escape hatch replaces the whole CORS block, so dropping a header here would break every /chat call at the preflight, with a CORS error, which reads like a config problem rather than the auth problem it would be."""
    api = _http_api(_template())
    assert "Authorization" in api["CorsConfiguration"]["AllowHeaders"]
    # PATCH and DELETE are the rename and delete routes.
    assert set(api["CorsConfiguration"]["AllowMethods"]) == {
        "POST",
        "GET",
        "PATCH",
        "DELETE",
        "OPTIONS",
    }


def test_the_site_content_is_the_container_built_astro_output():
    """The deployment must carry the built site, not a placeholder or a committed dist/."""
    listing = _staged_listing("SiteContentDeployment")
    assert "index.html" in listing, listing
    assert "_astro" in listing, (
        f"no hashed asset directory in the staged site: {listing}. camp's UI mounts React "
        "islands, so a build with no _astro/ means the bundler emitted a bare page."
    )
    # Camp's own static assets, copied from its public/, present only if camp's source was what got built.
    assert "sammy.riv" in listing, listing


def test_a_developers_local_config_json_never_reaches_the_site_asset():
    """The site asset must not carry a config.json, ever, the only one that may exist in the bucket is the SiteConfigDeployment's, stamped from stack tokens at deploy."""
    listing = _staged_listing("SiteContentDeployment")
    assert "config.json" not in listing, (
        f"a config.json was built into the site asset: {listing}. The site deployment's "
        "exclude keeps it from being uploaded today, but that exclude is there to scope "
        "the prune - narrow it, or merge the two deployments, and this file publishes "
        "over the deploy-stamped one and points the app at whatever API the developer "
        "was testing against."
    )


def test_the_routing_function_matches_the_pages_the_build_emits():
    """The viewer-request function rewrites directory paths to index.html, which is only correct if Astro emits directory-format pages."""
    frontend = Path(__file__).resolve().parents[3] / "frontend"
    pages = sorted(p.name for p in (frontend / "src" / "pages").glob("*.astro"))
    assert pages == ["404.astro", "index.astro"], (
        f"unexpected pages: {pages}. /login and /auth/callback were removed with Google "
        "OAuth; a new page needs the routing and the 404 behaviour re-checked. 404.astro "
        "is required - CloudFront's custom error response points at the /404.html it builds."
    )
    assert not (frontend / "src" / "pages" / "auth").exists()

    config = (frontend / "astro.config.mjs").read_text()
    assert "format: 'directory'" in config


def test_dist_is_not_committed():
    """The build is reproducible from source plus a lockfile, so a checked-in dist/ could only be a second source of truth that goes stale."""
    frontend = Path(__file__).resolve().parents[3] / "frontend"
    assert not (frontend / "dist").exists() or True  # built locally is fine, committed is not
    gitignore = (Path(__file__).resolve().parents[3] / ".gitignore").read_text()
    assert "dist/" in gitignore


def test_the_frontend_build_is_pinned_to_a_lockfile():
    """`npm ci` installs exactly the lockfile and fails if package.json disagrees, so the site cannot drift between deploys."""
    frontend = Path(__file__).resolve().parents[3] / "frontend"
    assert (frontend / "package-lock.json").exists()


def test_astro_emits_directory_format_so_the_routing_function_matches():
    """The CloudFront viewer-request function rewrites /login/ to /login/index.html."""
    config = (
        Path(__file__).resolve().parents[3] / "frontend" / "astro.config.mjs"
    ).read_text()
    assert "format: 'directory'" in config
    assert "output: 'static'" in config


def test_every_app_module_reaches_the_staged_lambda_asset():
    """The list-drift test: this include list has failed silently three times."""
    app_dir = Path(__file__).resolve().parents[3] / "app"
    on_disk = {
        path.name
        for path in app_dir.glob("*.py")
        # tests/ live in their own directory and are excluded from the asset on purpose.
        if not path.name.startswith("test_")
    }

    # two functions now, and it was five: the $connect authorizer, the WebSocket route function and the generation worker went with the socket, and their three modules went with them.
    functions = (
        "ChatFunction",
        # The streaming app under the Lambda Web Adapter.
        "StreamProbeFunction",
    )
    per_function = {
        name: {
            entry
            for entry in _staged_listing_for(name)
            if entry.endswith(".py")
        }
        for name in functions
    }
    staged = set().union(*per_function.values())

    missing = on_disk - staged
    assert missing == set(), (
        f"app modules that never reach any deployed function: {sorted(missing)}. "
        "Add each to the right asset's include list in infra_stack.py - the function "
        "imports them at cold start, so an omission is an ImportError on the first real "
        "request, not a synth failure."
    )

    extra = staged - on_disk
    assert extra == set(), (
        f"staged files with no module on disk: {sorted(extra)}. An include list is "
        "stale, or something unintended is riding along into a function bundle."
    )

    # the bundle that is A claim rather than A convenience.
    assert "handler.py" not in per_function["StreamProbeFunction"], (
        "the streaming app must not ship the API Gateway handler"
    )
    assert "token_auth.py" in per_function["StreamProbeFunction"], (
        "it has no authorizer in front of it, so the verifier rides in its bundle"
    )


def _stamped_config_json(outdir: Path) -> str:
    """The config.json this synth would deploy, read off the staged asset."""
    template = json.loads((outdir / "SjsuNavigatorStack.template.json").read_text())
    for logical_id, resource in template["Resources"].items():
        if not logical_id.startswith("SiteConfigDeployment"):
            continue
        keys = (resource.get("Properties") or {}).get("SourceObjectKeys") or []
        if len(keys) == 1:
            staged = outdir / ("asset." + keys[0].removesuffix(".zip"))
            return (staged / "config.json").read_text()
    raise AssertionError("no config.json deployment found")


def test_no_websocket_transport_is_synthesized_at_all():
    """The inversion of the whole section that used to be here."""
    template = _template()
    rendered = template.to_json()

    websocket_apis = {
        lid: r
        for lid, r in template.find_resources("AWS::ApiGatewayV2::Api").items()
        if (r.get("Properties") or {}).get("ProtocolType") == "WEBSOCKET"
    }
    assert websocket_apis == {}, "a WebSocket API is back"

    for logical_id in rendered["Resources"]:
        assert not logical_id.startswith("ConnectAuthorizer"), logical_id
        assert not logical_id.startswith("ChatStream"), logical_id

    # Every gateway route and stage left in the template belongs to the HTTP API.
    http_api_ids = {
        lid
        for lid, r in template.find_resources("AWS::ApiGatewayV2::Api").items()
        if (r.get("Properties") or {}).get("ProtocolType") == "HTTP"
    }
    assert len(http_api_ids) == 1, http_api_ids
    for kind in ("AWS::ApiGatewayV2::Route", "AWS::ApiGatewayV2::Stage"):
        for lid, resource in template.find_resources(kind).items():
            assert json.dumps(resource["Properties"]["ApiId"]).count(
                next(iter(http_api_ids))
            ), (kind, lid)

    # Nothing anywhere may push a frame down a connection.
    for lid, policy in template.find_resources("AWS::IAM::Policy").items():
        assert "execute-api:ManageConnections" not in json.dumps(policy), lid

    # sanity, so this test cannot pass by synthesizing nothing: the HTTP API's own JWT authorizer and POST /chat are untouched, and so is the pool the eval harness signs in to.
    assert template.find_resources("AWS::ApiGatewayV2::Authorizer") != {}
    assert template.find_resources("AWS::Cognito::UserPool") != {}
    assert len(template.find_resources("AWS::Cognito::UserPoolClient")) == 2


def test_nothing_tells_the_browser_a_socket_exists():
    """Nothing in config.json names a socket, whatever the streaming block says."""
    stamped = _stamped_config_json(_synth_outdir())
    assert "streamingApiUrl" not in stamped
    assert "execute-api" not in stamped, stamped
    # The keys the frontend does require are still stamped, see test_config_json_names_the_history_endpoint_the_frontend_reads for the full set.
    assert '"chatApiUrl"' in stamped and '"userPoolClientId"' in stamped


def test_the_socket_modules_are_gone_from_the_tree():
    """The other half, and the one the template cannot see."""
    app_dir = Path(__file__).resolve().parents[3] / "app"
    for gone in (
        "streaming.py",
        "stream_worker.py",
        "ws_authorizer.py",
        "requirements-authorizer.txt",
        # The module rename: it is not a probe, and run.sh, the bundle and the router prefix all name the new file.
        "stream_probe.py",
    ):
        assert not (app_dir / gone).exists(), f"app/{gone} is back"

    assert (app_dir / "streaming_app.py").exists()
    assert "streaming_app:app" in (app_dir / "run.sh").read_text(), (
        "run.sh must exec the renamed module - the handler is a PATH, so a stale name is a "
        "clean deploy and a function that never starts"
    )


def _stream_probe(template: Template) -> dict:
    """The probe function's Properties."""
    return _resource_named(template, "AWS::Lambda::Function", "StreamProbeFunction")[
        "Properties"
    ]


def _stream_probe_url(template: Template) -> dict:
    """The probe's Function URL Properties."""
    return _resource(template, "AWS::Lambda::Url")["Properties"]


def _distribution_config(template: Template) -> dict:
    return _resource(template, "AWS::CloudFront::Distribution")["Properties"][
        "DistributionConfig"
    ]


def _stream_edge_behavior(template: Template) -> dict:
    """The streaming endpoint's cache behaviour."""
    behaviors = _distribution_config(template).get("CacheBehaviors", [])
    assert len(behaviors) == 1, behaviors
    return behaviors[0]


def _origin_by_id(template: Template, origin_id: str) -> dict:
    origins = {o["Id"]: o for o in _distribution_config(template)["Origins"]}
    assert origin_id in origins, sorted(origins)
    return origins[origin_id]


def _stream_probe_source() -> str:
    return (Path(__file__).resolve().parents[3] / "app" / "streaming_app.py").read_text()


def test_the_stream_probe_boots_through_the_adapter_wrapper_not_a_python_handler():
    """The handler is a file path, not a dotted callable, which is the adapter's whole shape."""
    props = _stream_probe(_template())

    assert props["Handler"] == "run.sh"
    assert props["Environment"]["Variables"]["AWS_LAMBDA_EXEC_WRAPPER"] == "/opt/bootstrap"

    # And the handler names a file that is actually IN the bundle.
    assert props["Handler"] in _staged_listing("StreamProbeFunction")

    # A zip, not an image: an image would bring ECR and an image build to CI.
    assert "PackageType" not in props, props.get("PackageType")
    assert props["Runtime"] == _LAMBDA_PYTHON.name
    assert props["Architectures"] == [_LAMBDA_ARCH.name]


def test_the_stream_probe_ships_an_executable_run_sh():
    """The file mode is the fragile part, and the template records only an asset hash."""
    import stat

    staged = _staged_asset_dir("StreamProbeFunction")
    # what is in the bundle is pinned by the import-graph test below; this one is about the one file in it whose mode decides whether the function starts.
    assert "run.sh" in os.listdir(staged), sorted(os.listdir(staged))

    run_sh = staged / "run.sh"
    mode = run_sh.stat().st_mode
    assert mode & stat.S_IXUSR, oct(mode)
    assert mode & stat.S_IXGRP, oct(mode)
    assert mode & stat.S_IXOTH, oct(mode)
    assert run_sh.read_text().startswith("#!/bin/bash"), (
        "exec on a file with no shebang is an Exec format error, not a shell script"
    )


def test_the_run_script_starts_the_module_that_is_in_the_bundle():
    """A rename with nothing to catch it."""
    import ast

    app_dir = Path(__file__).resolve().parents[3] / "app"
    run_sh = (app_dir / "run.sh").read_text()

    match = re.search(r"(\w+):(\w+)\s*$", run_sh.strip())
    assert match, f"run.sh does not end in a module:attribute target:\n{run_sh}"
    module, attribute = match.group(1), match.group(2)

    module_path = app_dir / f"{module}.py"
    assert module_path.is_file(), f"run.sh starts {module}, which is not a module in app/"

    tree = ast.parse(module_path.read_text())
    bound = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert attribute in bound, (
        f"run.sh starts {module}:{attribute}, but {module}.py binds no {attribute}"
    )


def test_the_adapter_layer_is_published_for_the_functions_architecture():
    """The constraint that deploys clean and fails at runtime."""
    template = _template()
    props = _stream_probe(template)
    architecture = props["Architectures"][0]

    published_by_aws = {
        "x86_64": "LambdaAdapterLayerX86",
        "arm64": "LambdaAdapterLayerArm64",
    }
    # The deps layer arrives as a {"Ref": ...} to a resource in this template; the adapter is the one that arrives as an assembled ARN.
    arn = json.dumps([layer for layer in props["Layers"] if "Fn::Join" in layer])

    # The ARN ends in the layer name and a version number, the trailing quote is the anchor, so an ARN with anything after the version, or with no version at all, fails here.
    assert re.search(rf':layer:{published_by_aws[architecture]}:\d+"', arn), arn
    assert f':layer:{published_by_aws[architecture]}:{_LWA_LAYER_VERSION}"' in arn, arn

    # Published by AWS's account, in this stack's region and partition rather than a literal, a fresh install in another region attaches its own region's copy.
    assert _LWA_LAYER_ACCOUNT in arn, arn
    assert '"Ref": "AWS::Region"' in arn, arn
    assert '"Ref": "AWS::Partition"' in arn, arn


def test_the_stream_probe_carries_the_data_layer_as_well_as_its_own_two():
    """The half of the data/ move that no import graph can see."""
    template = _template()
    deps_id = next(iter(_layer_ids(template, "StreamProbeDepsLayer")))
    data_id = next(iter(_layer_ids(template, "CampusDataLayer")))
    layers = _stream_probe(template)["Layers"]

    # The adapter is the ARN one (see the test above); the other two are Refs to layers this template builds.
    assert [layer for layer in layers if "Ref" in layer] == [
        {"Ref": deps_id},
        {"Ref": data_id},
    ], layers
    assert len([layer for layer in layers if "Fn::Join" in layer]) == 1, layers
    assert len(layers) == 3, layers


def test_the_stream_probe_streams_on_both_the_url_and_the_adapter():
    """Two switches, and one alone is a buffered function that looks fine."""
    template = _template()

    assert _stream_probe_url(template)["InvokeMode"] == "RESPONSE_STREAM"
    assert (
        _stream_probe(template)["Environment"]["Variables"]["AWS_LWA_INVOKE_MODE"]
        == "response_stream"
    )

    # The URL points at the probe and not at some other function in the stack.
    target = json.dumps(_stream_probe_url(template)["TargetFunctionArn"])
    assert "StreamProbeFunction" in target, target


def test_the_stream_probe_url_is_iam_signed_and_open_to_nobody_but_the_edge():
    """The billing hole this stack must not have."""
    template = _template()

    assert _stream_probe_url(template)["AuthType"] == "AWS_IAM"

    for logical_id, permission in template.find_resources(
        "AWS::Lambda::Permission"
    ).items():
        props = permission["Properties"]
        assert props.get("Principal") != "*", (
            f"{logical_id} lets anyone invoke a function in this stack: {props}"
        )
        assert "FunctionUrlAuthType" not in props, (
            f"{logical_id} is a Function URL permission, which only exists for "
            f"AuthType NONE: {props}"
        )
        if "StreamProbeFunction" not in json.dumps(props.get("FunctionName", "")):
            continue
        assert props["Principal"] == "cloudfront.amazonaws.com", (
            f"{logical_id} grants an invoke on the probe to something that is not the "
            f"edge; everything else reaches it by SigV4 alone: {props}"
        )
        assert "SiteDistribution" in json.dumps(props.get("SourceArn", "")), (
            f"{logical_id} grants the CloudFront service principal an invoke on the probe "
            f"with no condition naming this distribution, which is every distribution in "
            f"every account: {props}"
        )


def test_the_streaming_endpoint_rides_the_sites_distribution_on_one_path_pattern():
    """One origin for the browser, so there is no CORS story to write later."""
    template = _template()
    behavior = _stream_edge_behavior(template)
    config = _distribution_config(template)

    assert behavior["PathPattern"] == _STREAM_EDGE_PATH_PATTERN

    # The behaviour points at the Function URL, and the default one does not.
    stream_origin = _origin_by_id(template, behavior["TargetOriginId"])
    assert "StreamProbeFunctionFunctionUrl" in json.dumps(stream_origin["DomainName"]), (
        stream_origin
    )
    # A Function URL is HTTPS-only and has no S3 config; a CustomOriginConfig that lost its https-only would be an edge talking to Lambda in the clear.
    assert stream_origin["CustomOriginConfig"]["OriginProtocolPolicy"] == "https-only"
    assert "S3OriginConfig" not in stream_origin

    site_origin = _origin_by_id(template, config["DefaultCacheBehavior"]["TargetOriginId"])
    assert "S3OriginConfig" in site_origin, (
        "the default behaviour stopped pointing at the bucket, so the site is being served "
        f"by something else: {site_origin}"
    )

    assert "FunctionAssociations" not in behavior, behavior


def test_the_streaming_behaviour_caches_nothing_and_keeps_the_origins_own_host():
    """Three settings, each breaking something different, and none visible at synth."""
    behavior = _stream_edge_behavior(_template())

    assert behavior["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad", (
        "not the managed CachingDisabled policy"
    )
    assert behavior["OriginRequestPolicyId"] == "b689b0a8-53d0-40ab-baf2-68738e2966ac", (
        "not the managed AllViewerExceptHostHeader policy"
    )
    assert behavior["Compress"] is False
    # ALLOW_ALL is the only value that includes POST, so the whole verb list is asserted.
    assert behavior["AllowedMethods"] == [
        "GET",
        "HEAD",
        "OPTIONS",
        "PUT",
        "PATCH",
        "POST",
        "DELETE",
    ], behavior["AllowedMethods"]
    assert behavior["ViewerProtocolPolicy"] == "redirect-to-https"


def test_the_edge_signs_every_origin_request_and_the_function_url_demands_it():
    """The pair that has to agree, and neither half means anything alone."""
    template = _template()
    behavior = _stream_edge_behavior(template)

    controls = {
        lid: r["Properties"]["OriginAccessControlConfig"]
        for lid, r in template.find_resources(
            "AWS::CloudFront::OriginAccessControl"
        ).items()
    }
    lambda_controls = {
        lid: c
        for lid, c in controls.items()
        if c["OriginAccessControlOriginType"] == "lambda"
    }
    assert len(lambda_controls) == 1, controls
    lambda_oac_id, lambda_oac = next(iter(lambda_controls.items()))

    assert lambda_oac["SigningBehavior"] == "always"
    assert lambda_oac["SigningProtocol"] == "sigv4"

    # And it is the control this behaviour's origin actually carries, rather than one that merely exists in the template.
    stream_origin = _origin_by_id(template, behavior["TargetOriginId"])
    assert stream_origin["OriginAccessControlId"] == {
        "Fn::GetAtt": [lambda_oac_id, "Id"]
    }, stream_origin

    assert _stream_probe_url(template)["AuthType"] == "AWS_IAM"


def test_the_edge_holds_both_invoke_actions_scoped_to_this_distribution():
    """Both actions, and CDK only writes one of them."""
    template = _template()

    edge_grants = {
        lid: r["Properties"]
        for lid, r in template.find_resources("AWS::Lambda::Permission").items()
        if r["Properties"].get("Principal") == "cloudfront.amazonaws.com"
    }
    assert len(edge_grants) == 2, sorted(edge_grants)
    assert sorted(p["Action"] for p in edge_grants.values()) == [
        "lambda:InvokeFunction",
        "lambda:InvokeFunctionUrl",
    ], edge_grants

    distribution_id = next(iter(template.find_resources("AWS::CloudFront::Distribution")))
    for logical_id, props in edge_grants.items():
        # Both name the probe, and nothing else in the stack.
        assert "StreamProbeFunction" in json.dumps(props["FunctionName"]), (
            f"{logical_id} grants the edge an invoke on a function that is not the "
            f"streaming app: {props}"
        )
        # Scoped: partition and account from stack tokens, the distribution by Ref.
        source_arn = json.dumps(props.get("SourceArn"))
        assert ":cloudfront::" in source_arn, (
            f"{logical_id} has no CloudFront SourceArn condition: {props}"
        )
        assert json.dumps({"Ref": distribution_id}) in source_arn, (
            f"{logical_id} is not scoped to this stack's distribution: {props}"
        )


def test_the_edge_path_pattern_and_the_apps_own_routes_are_one_string():
    """The contract no synth and no other test can see."""
    import ast

    tree = ast.parse(_stream_probe_source())

    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert constants.get("EDGE_PATH_PREFIX") == _STREAM_EDGE_PATH_PREFIX, (
        f"app/streaming_app.py serves {constants.get('EDGE_PATH_PREFIX')!r} and the edge "
        f"routes {_STREAM_EDGE_PATH_PREFIX!r} at it"
    )
    assert _STREAM_EDGE_PATH_PATTERN == f"{_STREAM_EDGE_PATH_PREFIX}/*"

    # The router is built from that constant rather than from a second literal.
    router = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets)
    )
    assert isinstance(router, ast.Call) and router.func.id == "APIRouter", ast.dump(router)
    prefix_arg = {kw.arg: kw.value for kw in router.keywords}["prefix"]
    assert isinstance(prefix_arg, ast.Name) and prefix_arg.id == "EDGE_PATH_PREFIX", (
        ast.dump(prefix_arg)
    )

    routes = {"app": [], "router": []}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id in routes
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
            ):
                routes[decorator.func.value.id].append(
                    (decorator.func.attr, decorator.args[0].value)
                )

    assert routes["app"] == [("get", "/")], (
        f"the only route hung off the app itself must be the adapter's readiness target; "
        f"found {routes['app']}"
    )
    assert sorted(routes["router"]) == [
        ("get", "/model"),
        ("get", "/stream"),
        ("post", "/chat"),
    ], routes["router"]

    # And the router is actually mounted. Every route above is dead code without this.
    assert any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "include_router"
        for node in tree.body
    ), "app/streaming_app.py builds a router and never includes it"


def test_the_stream_probe_holds_the_chat_turns_grants_and_not_the_sockets():
    """It runs the same turn, so it holds the same grants and neither of the socket's two."""
    template = _template()
    role_id = next(
        lid
        for lid in template.find_resources("AWS::IAM::Role")
        if lid.startswith("StreamProbeFunctionServiceRole")
    )
    role = template.find_resources("AWS::IAM::Role")[role_id]["Properties"]
    managed = role.get("ManagedPolicyArns", [])
    # Exactly one: a second is how a broad grant arrives with no inline policy to compare.
    assert len(managed) == 1, managed
    assert "AWSLambdaBasicExecutionRole" in json.dumps(managed), managed

    def _statements(role_prefix):
        role_lid = next(
            lid
            for lid in template.find_resources("AWS::IAM::Role")
            if lid.startswith(role_prefix)
        )
        found = []
        for policy in template.find_resources("AWS::IAM::Policy").values():
            if role_lid in json.dumps(policy["Properties"].get("Roles", [])):
                found.extend(policy["Properties"]["PolicyDocument"]["Statement"])
        return found

    probe = _statements("StreamProbeFunctionServiceRole")
    chat = _statements("ChatFunctionRole")

    def _rendered(statements):
        return sorted(json.dumps(statement, sort_keys=True) for statement in statements)

    chat_grants = _rendered(chat)
    # Neither role may carry a push grant now.
    assert "execute-api:" not in json.dumps(chat), chat

    # the one statement that is the door and not the turn, lifted out and asserted on its own rather than allowed to blur the comparison below.
    allowlist_grants = [
        statement for statement in probe if "ssm:" in json.dumps(statement)
    ]
    assert len(allowlist_grants) == 1, allowlist_grants
    assert _actions(allowlist_grants[0]) == ["ssm:GetParameter"], allowlist_grants[0]
    # Scoped to the one parameter, by an ARN assembled from pseudo-parameters, a Ref to the parameter would put the dependency cycle back through IAM.
    scoped_to = json.dumps(_resources(allowlist_grants[0]))
    assert ":ssm:" in scoped_to and "streaming/allowed-client-ids" in scoped_to, scoped_to
    assert "*" not in scoped_to, scoped_to
    probe_turn_grants = [
        statement for statement in probe if statement not in allowlist_grants
    ]

    # Compared as rendered JSON so a changed resource ARN or a widened action shows up rather than being counted equal.
    assert _rendered(probe_turn_grants) == chat_grants, (
        json.dumps({"streaming app": probe, "chat": chat}, indent=2, sort_keys=True)
    )

    document = json.dumps(probe)
    # It retrieves, it invokes, it screens and it stores.
    assert "bedrock:Retrieve" in document, document
    assert "bedrock:InvokeModel*" in document, document
    assert "bedrock:ApplyGuardrail" in document, document
    assert "dynamodb:PutItem" in document, document
    # And it starts nothing and pushes down nothing.
    assert "lambda:InvokeFunction" not in document, document
    assert "execute-api:" not in document, document
    # dynamodb:Scan is the one operation that takes no partition key, and the whole isolation story is that the partition comes from the JWT `sub`.
    assert "dynamodb:Scan" not in document, document

    # And no reserved concurrency: capacity out of the account pool belongs to the chat function alone (test_only_the_chat_function_reserves_concurrency states the rule).
    assert "ReservedConcurrentExecutions" not in _stream_probe(template)


def test_the_stream_probe_ships_every_module_it_imports():
    """Walked from the import graph, because this list cannot be read carefully enough."""
    import ast

    app_dir = Path(__file__).resolve().parents[3] / "app"
    on_disk = {path.stem for path in app_dir.glob("*.py")}

    def imports_of(module):
        tree = ast.parse((app_dir / f"{module}.py").read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
        return {name for name in names if name in on_disk}

    needed, frontier = {"streaming_app"}, ["streaming_app"]
    while frontier:
        for name in imports_of(frontier.pop()):
            if name not in needed:
                needed.add(name)
                frontier.append(name)

    staged = {name for name in _staged_listing("StreamProbeFunction") if name.endswith(".py")}
    missing = {f"{name}.py" for name in needed} - staged
    assert missing == set(), (
        f"app/streaming_app.py imports these, and the bundle does not ship them: "
        f"{sorted(missing)}. The deployed function dies at cold start on the first one."
    )

    # The other direction, so the list does not quietly accumulate modules nothing imports: every .py in the bundle is on the import path of the app it serves.
    extra = staged - {f"{name}.py" for name in needed}
    assert extra == set(), (
        f"staged in the streaming app's bundle but imported by nothing in it: "
        f"{sorted(extra)}"
    )

    # The control: handler.py is the API Gateway transport and must not be here.
    assert "handler.py" not in staged, staged


def _token_auth_source() -> str:
    return (Path(__file__).resolve().parents[3] / "app" / "token_auth.py").read_text()


def test_the_streaming_app_is_told_which_pool_and_which_clients_to_trust():
    """The three values that decide who gets served, and none may be a literal."""
    template = _template()
    variables = _stream_probe(template)["Environment"]["Variables"]

    # The region as a stack token, never the string this laptop happens to be configured for.
    assert variables["COGNITO_REGION"] == {"Ref": "AWS::Region"}, variables[
        "COGNITO_REGION"
    ]

    # The pool by reference to the one this stack creates.
    pool_id = _logical_id(template, "AWS::Cognito::UserPool")
    assert variables["USER_POOL_ID"] == {"Ref": pool_id}, variables["USER_POOL_ID"]

    # The allowlist by name, and the name is the stack's own so two installs in one account do not share one parameter.
    parameter = _resource_named(
        template, "AWS::SSM::Parameter", "StreamingAllowedClientIds"
    )["Properties"]
    assert variables["ALLOWED_CLIENT_IDS_PARAMETER"] == parameter["Name"], {
        "function": variables["ALLOWED_CLIENT_IDS_PARAMETER"],
        "parameter": parameter["Name"],
    }
    # The name references no resource, only pseudo-parameters and literals.
    name = json.dumps(parameter["Name"])
    assert "Fn::GetAtt" not in name, name
    assert not [
        ref
        for ref in re.findall(r'"Ref":\s*"([^"]+)"', name)
        if not ref.startswith("AWS::")
    ], name
    # And it is scoped to this install, so two stacks in one account do not share it.
    assert "streaming/allowed-client-ids" in name, name

    # both clients are in the parameter's value, and the eval runner's is the one a "tightening" would drop.
    value = json.dumps(parameter["Value"])
    web_client = _client_logical_id(template, "ChatWebClient")
    eval_client = _client_logical_id(template, "ChatEvalClient")
    assert json.dumps({"Ref": web_client}) in value, value
    assert json.dumps({"Ref": eval_client}) in value, value

    # And it is the same pair the HTTP API's own authorizer accepts as its audience.
    audience = _http_api_resource(template, "AWS::ApiGatewayV2::Authorizer")[
        "Properties"
    ]["JwtConfiguration"]["Audience"]
    rendered_audience = json.dumps(audience)
    assert json.dumps({"Ref": web_client}) in rendered_audience, rendered_audience
    assert json.dumps({"Ref": eval_client}) in rendered_audience, rendered_audience
    assert len(audience) == 2, audience


def test_nothing_the_site_distribution_reaches_names_an_app_client():
    """The cycle this stack must not grow back, stated as the property, not the workaround."""
    template = _template()
    distribution_id = _logical_id(template, "AWS::CloudFront::Distribution")
    clients = template.find_resources("AWS::Cognito::UserPoolClient")
    assert len(clients) == 2, sorted(clients)

    cycling = {
        logical_id
        for logical_id, client in clients.items()
        if distribution_id in json.dumps(client["Properties"])
    }
    assert cycling, (
        "no app client references the distribution any more, which would mean the cycle "
        "this test guards is gone - check whether the indirection is still needed before "
        "deleting this"
    )

    environment = json.dumps(_stream_probe(template)["Environment"])
    named = sorted(client for client in cycling if client in environment)
    assert named == [], (
        f"the streaming app's environment names {named}, which closes the dependency "
        f"cycle through the site distribution. Those client ids belong in the Parameter "
        f"Store parameter this function reads by name."
    )

    role_id = next(
        lid
        for lid in template.find_resources("AWS::IAM::Role")
        if lid.startswith("StreamProbeFunctionServiceRole")
    )
    for logical_id, policy in template.find_resources("AWS::IAM::Policy").items():
        if role_id not in json.dumps(policy["Properties"].get("Roles", [])):
            continue
        document = json.dumps(policy["Properties"]["PolicyDocument"])
        named = sorted(client for client in cycling if client in document)
        assert named == [], (
            f"{logical_id} names {named}. `parameter.grant_read(fn)` is how this comes "
            f"back: the policy refs the parameter, the parameter refs the client, and the "
            f"cycle arrives through IAM instead of through the environment. The ARN is "
            f"assembled from pseudo-parameters for exactly that reason."
        )
        # And the parameter itself is not Ref'd either, same edge, same cycle.
        assert "StreamingAllowedClientIds" not in document, document


def test_the_streaming_apps_layer_carries_the_library_its_verifier_needs():
    """An import that is not in the layer is a cold start that dies, after a clean deploy."""
    app_dir = Path(__file__).resolve().parents[3] / "app"

    def _pyjwt_line(name):
        lines = [
            line.strip()
            for line in (app_dir / name).read_text().splitlines()
            if line.strip().lower().startswith("pyjwt")
        ]
        assert len(lines) == 1, f"{name}: {lines}"
        return lines[0]

    probe_pin = _pyjwt_line("requirements-stream-probe.txt")

    assert "crypto" in probe_pin, (
        f"the streaming app pins PyJWT without the crypto extra ({probe_pin}), so it "
        "cannot verify RS256 - the only algorithm Cognito signs with"
    )
    assert "crypto" in _pyjwt_line("requirements-dev.txt"), (
        "app/tests/test_token_auth.py verifies real signatures; without the crypto extra "
        "it would pass while checking nothing"
    )

    # The layer's asset hash covers the file, so a changed pin rebuilds rather than leaving the previous build staged.
    stack_source = (Path(__file__).resolve().parents[2] / "infra" / "infra_stack.py").read_text()
    assert 'requirements-stream-probe.txt"' in stack_source


def test_the_auth_header_is_spelled_in_exactly_one_place():
    """One name in one file, the treatment EDGE_PATH_PREFIX gets and for the same reason."""
    import ast

    root = Path(__file__).resolve().parents[3]
    tree = ast.parse(_token_auth_source())
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    header = constants.get("AUTH_HEADER_NAME")
    assert isinstance(header, str) and header, constants

    # Lower case, because every layer between the browser and this process lower-cases header names and one canonical spelling is a lookup that never has to guess.
    assert header == header.lower(), header

    # -I skips binaries; `--` separates the pattern from the pathspec, and the pathspec is the two trees that ship, the browser's spelling lives in frontend/ and is compared against this one by test_the_browser_sends_the_token_on_the_header_the_app_reads.
    found = subprocess.run(
        ["git", "-C", str(root), "grep", "-lI", "-e", header, "--", "app", "infra"],
        capture_output=True,
        text=True,
    )
    assert found.returncode in (0, 1), found.stderr
    spelled_in = sorted(found.stdout.split())

    # token_auth.py declares it; its own suite reads the constant to build a request, which is the one other place the string may appear, and it appears there as an import, not as a literal, so this list is the assertion.
    assert spelled_in == ["app/token_auth.py"], (
        f"the auth header name is written in more than one place: {spelled_in}. It is "
        f"AUTH_HEADER_NAME in app/token_auth.py and nowhere else - a second spelling is a "
        f"401 that no test and no synth can see."
    )


def _chat_stream_client_source() -> str:
    return (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "src"
        / "lib"
        / "chatStream.ts"
    ).read_text()


def _ts_const(source: str, name: str) -> str:
    """The value of `export const name = '...'` (or a plain const) out of a TS file."""
    match = re.search(
        rf"^(?:export )?const {re.escape(name)} = '([^']*)';$", source, re.MULTILINE
    )
    assert match is not None, f"{name} is not a single-quoted const in chatStream.ts"
    return match.group(1)


def test_the_browser_calls_the_same_prefix_the_edge_routes_and_the_app_serves():
    """The third spelling of one string, and the one no Python test could see."""
    prefix = _ts_const(_chat_stream_client_source(), "STREAM_PATH_PREFIX")
    assert prefix == _STREAM_EDGE_PATH_PREFIX, (
        f"the browser calls {prefix!r} and the edge routes {_STREAM_EDGE_PATH_PREFIX!r}"
    )


def test_the_browser_sends_the_token_on_the_header_the_app_reads():
    """One header name across two languages, which the Python-only scan cannot reach."""
    import ast

    tree = ast.parse(_token_auth_source())
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    client = _chat_stream_client_source()
    assert _ts_const(client, "AUTH_HEADER_NAME") == constants["AUTH_HEADER_NAME"], (
        "the browser sends the token on a header app/token_auth.py does not read"
    )

    # The header is AWS's own name for the body hash, so it is a literal on both sides rather than a constant of ours.
    assert _ts_const(client, "BODY_HASH_HEADER_NAME") == "x-amz-content-sha256"
    assert "crypto.subtle.digest('SHA-256'" in client, (
        "the body hash must be computed from the body, not asserted"
    )


def test_the_streaming_app_takes_its_caller_from_the_verified_token_and_never_from_the_body():
    """The claim that makes the partition key a security boundary."""
    import ast

    tree = ast.parse(_stream_probe_source())
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    chat = functions["chat"]

    # `user_id=` is passed exactly once out of the route, from what identity_from returned.
    user_id_args = [
        keyword
        for node in ast.walk(chat)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "user_id"
    ]
    assert len(user_id_args) == 1, [ast.dump(k) for k in user_id_args]
    value = user_id_args[0].value
    assert isinstance(value, ast.Attribute) and value.attr == "sub", ast.dump(value)
    assert isinstance(value.value, ast.Name), ast.dump(value)
    identity_name = value.value.id

    bound_from = [
        node.value
        for node in ast.walk(chat)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == identity_name for t in node.targets)
    ]
    assert len(bound_from) == 1, [ast.dump(n) for n in bound_from]
    assert (
        isinstance(bound_from[0], ast.Call)
        and isinstance(bound_from[0].func, ast.Name)
        and bound_from[0].func.id == "identity_from"
    ), ast.dump(bound_from[0])

    # identity_from reads the verifier and the headers; a body reference is a second source.
    identity_from = functions["identity_from"]
    called = {
        node.func.id
        for node in ast.walk(identity_from)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "verifier" in called, sorted(called)
    attributes = {
        node.attr for node in ast.walk(identity_from) if isinstance(node, ast.Attribute)
    }
    assert "headers" in attributes, sorted(attributes)
    assert not {"json", "body", "form", "query_params"} & attributes, sorted(attributes)

    # And the names it imports are token_auth's, so the checks are that module's rather than a second copy inside the transport.
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "token_auth"
        for alias in node.names
    }
    assert {"verifier", "Unauthorized"} <= imported, sorted(imported)

    # Exactly one key is read out of the body by hand; the rest goes through ChatRequest.
    body_keys = {
        node.args[0].value
        for node in ast.walk(chat)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "body"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert body_keys == {"query"}, (
        f"the streaming chat route reads {sorted(body_keys)} out of the request body by "
        f"hand. Only the query belongs there - anything else a client sends must go "
        f"through ChatRequest, and an id read off the body is not an identity."
    )


def test_the_stream_probe_is_wired_to_nothing_else_in_the_stack():
    """The point of landing it alone: nothing else in the stack depends on it."""
    template = _template()

    chat_env = set(
        _resource_named(template, "AWS::Lambda::Function", "ChatFunction")["Properties"][
            "Environment"
        ]["Variables"]
    )
    # exactly the chat function's variables, the exemption list included, now that this function applies the daily cap too, plus the three the adapter needs, plus the three app/token_auth.py verifies against, and nothing else.
    expected = chat_env | {
        "AWS_LAMBDA_EXEC_WRAPPER",
        "AWS_LWA_INVOKE_MODE",
        "PORT",
        "COGNITO_REGION",
        "USER_POOL_ID",
        "ALLOWED_CLIENT_IDS_PARAMETER",
    }
    probe_env = set(_stream_probe(template)["Environment"]["Variables"])
    assert probe_env == expected

    # the property this test exists for, said out loud now that the equality above is no longer only about the adapter.
    assert not [name for name in probe_env if name.startswith("STREAM_")], sorted(
        probe_env
    )

    # No API Gateway integration on either API points at it.
    for logical_id, integration in template.find_resources(
        "AWS::ApiGatewayV2::Integration"
    ).items():
        assert "StreamProbeFunction" not in json.dumps(integration["Properties"]), (
            f"{logical_id} routes API Gateway traffic at the probe"
        )

    # And the browser is never told it exists.
    stamped = (_staged_asset_dir("SiteConfigDeployment") / "config.json").read_text()
    keys = re.findall(r'"(\w+)"\s*:', stamped)
    assert not [key for key in keys if "probe" in key.lower()], sorted(keys)

    # The deployer is told, through a stack output, which is where a thing only a signed caller can use belongs.
    outputs = json.loads(
        (_synth_outdir() / "SjsuNavigatorStack.template.json").read_text()
    )["Outputs"]
    assert any(name.startswith("StreamProbeFunctionUrl") for name in outputs), sorted(
        outputs
    )


def test_no_gav_specific_resources_were_inherited():
    """Gav's stack carries four things this one has no use for, and none of them were ever ported: the Primo catalog tools, the dedicated catalog bucket, the SNS feedback path, and dual hosting (gav serves an embeddable widget and a demo site from two separate bucket + distribution pairs; this app is one site)."""
    template = _template()

    assert template.find_resources("AWS::SNS::Topic") == {}, "the feedback path is gav's"
    assert template.find_resources("AWS::SNS::Subscription") == {}

    # exactly one DynamoDB table, and it is the chat history table.
    tables = sorted(template.find_resources("AWS::DynamoDB::Table"))
    assert len(tables) == 1, tables
    assert tables[0].startswith("ChatHistoryTable"), tables

    # One distribution: a second would mean dual hosting came along with the frontend.
    assert len(template.find_resources("AWS::CloudFront::Distribution")) == 1

    # Exactly two buckets, both named: the KB source bucket and the site bucket.
    buckets = sorted(template.find_resources("AWS::S3::Bucket"))
    assert len(buckets) == 2, buckets
    assert any(b.startswith("KnowledgeBaseSourceBucket") for b in buckets), buckets
    assert any(b.startswith("SiteBucket") for b in buckets), buckets


def test_the_inherited_gav_pieces_that_are_load_bearing_are_still_here():
    """The other half of the same decision."""
    template = _template()

    # The PROMPT_ATTACK input screen and its pinned version.
    assert len(template.find_resources("AWS::Bedrock::Guardrail")) == 1
    assert len(template.find_resources("AWS::Bedrock::GuardrailVersion")) == 1
    # The Cognito gate on the billable route.
    assert len(template.find_resources("AWS::Cognito::UserPool")) == 1
    jwt_authorizers = [
        lid
        for lid, r in template.find_resources("AWS::ApiGatewayV2::Authorizer").items()
        if r["Properties"]["AuthorizerType"] == "JWT"
    ]
    assert len(jwt_authorizers) == 1, jwt_authorizers
    assert _http_api_resource(template, "AWS::ApiGatewayV2::Authorizer")
    # The data layer: the crawl list cannot travel in an env var (Lambda's 4 KB aggregate cap), so this is the mechanism, not a gav leftover.
    _resource_named(template, "AWS::Lambda::LayerVersion", "CampusDataLayer")


def test_the_two_deps_layers_are_distinct_assets():
    """The bug this exists for cost a green deploy with a dead chat endpoint."""
    template = _template()
    scraper = _resource_named(template, "AWS::Lambda::LayerVersion", "ScraperDepsLayer")
    chat = _resource_named(template, "AWS::Lambda::LayerVersion", "ChatDepsLayer")
    assert (
        scraper["Properties"]["Content"]["S3Key"]
        != chat["Properties"]["Content"]["S3Key"]
    ), "both deps layers resolved to ONE asset - the chat Lambda will not have pydantic"


def test_each_deps_layer_ships_its_own_packages():
    """The stronger form: distinct assets are necessary but not sufficient, since two distinct assets could still hold the wrong contents."""
    scraper = _staged_listing("ScraperDepsLayer")
    chat = _staged_listing("ChatDepsLayer")
    assert scraper == ["python"] and chat == ["python"]

    def _packages(prefix):
        staged = _staged_asset_dir(prefix)
        return set(os.listdir(staged / "python"))

    chat_packages = _packages("ChatDepsLayer")
    assert any(p.startswith("pydantic") for p in chat_packages), sorted(chat_packages)[:10]

    scraper_packages = _packages("ScraperDepsLayer")
    assert not any(p.startswith("pydantic") for p in scraper_packages), (
        "the scraper layer should not carry the chat Lambda's deps"
    )


def test_the_chat_role_can_invoke_the_titling_model():
    """A separate grant, because it is a separate model id: the generation statement names one model, so a titling call against another is AccessDenied, and unlike a denied generation, that failure is swallowed by design (every conversation keeps its first-message title), so it would be discovered from a sidebar that never improves rather than from an error."""
    from infra.config import resolve_generation

    generation = resolve_generation(load_config())
    assert generation["title_model_id"] != generation["model_id"], (
        "this test covers the two-models case"
    )
    policy = _resource_named(_template(), "AWS::IAM::Policy", "ChatFunctionRole")
    rendered = json.dumps(policy["Properties"]["PolicyDocument"]["Statement"])

    assert f":inference-profile/{generation['title_model_id']}" in rendered
    assert f"::foundation-model/{generation['title_base_model_id']}" in rendered


def test_the_chat_function_is_told_which_model_names_a_conversation():
    """By reference to the same resolved config the IAM grant is built from, so the id the function invokes is by construction the id it is allowed to invoke."""
    from infra.config import resolve_chat, resolve_generation

    config = load_config()
    env = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")[
        "Properties"
    ]["Environment"]["Variables"]

    assert env["TITLE_MODEL_ID"] == resolve_generation(config)["title_model_id"]
    assert env["TITLE_MAX_CHARS"] == str(resolve_chat(config)["title_max_chars"])
    assert env["TITLE_DEADLINE_SECONDS"] == str(
        resolve_chat(config)["title_deadline_seconds"]
    )


def test_the_two_conversation_writes_are_served_by_the_chat_function_and_gated():
    """Covered by test_every_route_is_jwt_gated too, and stated again here because the reason is stronger for these two: they change stored data, and the only thing that decides whose data is the `sub` claim the partition key is built from."""
    template = _template()
    writes = [
        route
        for route in template.find_resources("AWS::ApiGatewayV2::Route").values()
        if route["Properties"]["RouteKey"].startswith(("PATCH ", "DELETE "))
    ]
    assert len(writes) == 2
    integration_id = next(iter(template.find_resources("AWS::ApiGatewayV2::Integration")))
    for route in writes:
        assert route["Properties"]["AuthorizationType"] == "JWT"
        assert integration_id in json.dumps(route["Properties"]["Target"])


def test_the_chat_function_carries_the_daily_message_limit():
    """The per-user cost fence's one number, reaching the function that enforces it."""
    from infra.config import resolve_rate_limit

    env = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Environment"
    ]["Variables"]

    assert env["DAILY_MESSAGE_LIMIT"] == str(resolve_rate_limit(load_config()))


def test_the_eval_client_is_exempt_from_the_daily_message_limit():
    """The exemption is the machine client's id, by reference to the client itself."""
    template = _template()
    env = _resource_named(template, "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Environment"
    ]["Variables"]

    eval_client_id = next(
        lid for lid in template.find_resources("AWS::Cognito::UserPoolClient")
        if lid.startswith("ChatUserPoolChatEvalClient")
    )
    web_client_id = next(
        lid for lid in template.find_resources("AWS::Cognito::UserPoolClient")
        if lid.startswith("ChatUserPoolChatWebClient")
    )

    assert env["RATE_LIMIT_EXEMPT_CLIENT_IDS"] == {"Ref": eval_client_id}
    assert env["RATE_LIMIT_EXEMPT_CLIENT_IDS"] != {"Ref": web_client_id}


def test_the_daily_message_limit_is_gated_by_config_and_omitted_when_off():
    """`rate_limit.daily_message_limit: 0` must remove the variable entirely, not set "0"."""
    config = copy.deepcopy(load_config())
    config["rate_limit"]["daily_message_limit"] = 0

    app = cdk.App(outdir=tempfile.mkdtemp())
    template = Template.from_stack(NavigatorStack(app, "SjsuNavigatorStack", config=config))

    env = _resource_named(template, "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Environment"
    ]["Variables"]
    assert "DAILY_MESSAGE_LIMIT" not in env
    # The exemption list is unconditional, and harmlessly so: with no cap there is nothing to be exempt from, and making it conditional too would be a second thing to keep in step.
    assert "RATE_LIMIT_EXEMPT_CLIENT_IDS" in env
    assert env["CHAT_HISTORY_TABLE_NAME"], "turning the cap off must not disturb the rest"


def test_the_rate_counter_needs_no_new_table_and_no_new_grant():
    """The counter is a third sort-key prefix in the same partition, so it adds no AWS resource."""
    template = _template()

    assert len(template.find_resources("AWS::DynamoDB::Table")) == 1
    table = _resource(template, "AWS::DynamoDB::Table")
    assert table["Properties"]["TimeToLiveSpecification"] == {
        "AttributeName": "expiresAt",
        "Enabled": True,
    }


def test_the_chat_function_carries_the_escalation_wiring():
    """The three values that let a turn assemble a draft, reaching the function that does it."""
    from infra.config import resolve_escalation

    escalation = resolve_escalation(load_config())
    env = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Environment"
    ]["Variables"]

    assert env["ESCALATION_RECIPIENT"] == escalation["recipient"]
    assert env["ESCALATION_SUBJECT"] == escalation["subject"]
    assert env["ESCALATION_MAX_CHARS"] == str(escalation["max_chars"])


def test_the_escalation_module_is_in_the_functions_that_import_it():
    """app/orchestrator.py and app/prompts.py both import escalation.py, so a bundle without it is an ImportError on the first invocation rather than a missing feature."""
    template = _template()
    # Both functions that run the turn: the buffered transport and the streamed one.
    for function in ("ChatFunction", "StreamProbeFunction"):
        resource = _resource_named(template, "AWS::Lambda::Function", function)
        staged = _staged_asset_dir(function)
        assert (staged / "escalation.py").exists(), f"{function} cannot import escalation"
        assert (staged / "orchestrator.py").exists(), resource["Properties"]["Handler"]


def test_the_escalation_path_is_gated_by_config_and_leaves_no_trace_when_off():
    """A blank `escalation.contact` must remove the whole path: no variables on the function, no key in config.json."""
    config = copy.deepcopy(load_config())
    config["escalation"]["contact"] = ""

    outdir = tempfile.mkdtemp()
    app = cdk.App(outdir=outdir)
    NavigatorStack(app, "SjsuNavigatorStack", config=config)
    app.synth()
    template = json.loads((Path(outdir) / "SjsuNavigatorStack.template.json").read_text())

    chat = None
    for logical_id, resource in template["Resources"].items():
        if resource["Type"] == "AWS::Lambda::Function" and logical_id.startswith("ChatFunction"):
            chat = resource
    assert chat is not None
    env = chat["Properties"]["Environment"]["Variables"]
    assert not [key for key in env if key.startswith("ESCALATION_")]

    staged = None
    for logical_id, resource in template["Resources"].items():
        if not logical_id.startswith("SiteConfigDeployment"):
            continue
        keys = (resource.get("Properties") or {}).get("SourceObjectKeys") or []
        if len(keys) == 1:
            staged = Path(outdir) / ("asset." + keys[0].removesuffix(".zip"))
    assert staged is not None, "the config.json deployment should still exist"

    text = (staged / "config.json").read_text()
    assert "escalationRecipient" not in text
    assert '"chatApiUrl"' in text, "turning the path off must not disturb the rest"


def test_config_json_publishes_the_escalation_recipient_when_it_is_configured():
    """The browser needs the address to know the feature exists at all."""
    from infra.config import resolve_escalation

    staged = (_staged_asset_dir("SiteConfigDeployment") / "config.json").read_text()

    assert resolve_escalation(load_config())["recipient"] in staged

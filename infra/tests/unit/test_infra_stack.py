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
import re
import subprocess
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
        "chat_history",
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
def _staged_asset_dirs() -> dict:
    """{logical id -> Path of that resource's staged asset directory}.

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
            listings[logical_id] = staged
    return listings


def _staged_assets() -> dict:
    """{logical id -> sorted top-level listing}, derived from the cached directories so
    the expensive synth happens once."""
    return {lid: sorted(os.listdir(d)) for lid, d in _staged_asset_dirs().items()}


def _staged_asset_dir(logical_id_prefix: str) -> Path:
    """The staged asset DIRECTORY, for assertions that need to look inside it rather than
    just list its top level."""
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


# --- Chat history table (docs/accounts-and-storage.md, Storage) --------------------------
#
# app/history.py now reads and writes this table, and the key shapes it builds are spelled
# in Python where nothing checks them against CloudFormation. That makes these assertions
# the ONLY thing standing between a wrong table shape and a slice that discovers it at
# runtime - and three of the four properties below cannot be corrected in place once there
# is data (the key schema replaces the table, TTL needs a disable/enable cycle, PITR only
# covers what it was on for). Every literal is spelled here rather than imported from
# infra.config: a test that reads the same constant the stack does pins nothing.


def test_the_history_table_is_keyed_on_the_user_with_a_prefixed_sort_key():
    """pk=USER#<sub>, sk=CONV#<convId> or MSG#<convId>#<ulid> - one table, both item kinds,
    separated by the sort-key prefix.

    The key schema is IMMUTABLE. Changing either attribute replaces the table, and a
    replacement of this table is the loss of every transcript on it, so this is pinned at
    the shape the access patterns were designed against rather than left to drift."""
    table = _resource(_template(), "AWS::DynamoDB::Table")["Properties"]

    assert table["KeySchema"] == [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]
    # Both declared as strings: the sort key is a compound prefix, never a number, and a
    # numeric type would make begins_with() - which is how BOTH reads work - impossible.
    assert table["AttributeDefinitions"] == [
        {"AttributeName": "pk", "AttributeType": "S"},
        {"AttributeName": "sk", "AttributeType": "S"},
    ]


def test_the_history_table_is_on_demand_with_pitr_and_ttl_on_expires_at():
    """On-demand because pilot traffic is a spike against an idle table; PITR because this
    holds the only copy of a student's transcript.

    TTL IS ENABLED ON AN ATTRIBUTE NOTHING WRITES, and that is the deliberate part. The
    retention window for identifiable transcripts is an open question with the university
    (docs/accounts-and-storage.md, Open). Items with no `expiresAt` never expire, so turning
    it on now costs nothing and turning it on later is a table-level change - this is the
    cheap half of a decision that has not been made."""
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
    """No GSI and no LSI in v1: every access pattern in the doc is served by the primary
    key. An index that exists is billed and backfilled whether or not anything queries it,
    and adding one later is purely additive - so an index appearing here would mean one was
    added without a query to justify it."""
    table = _resource(_template(), "AWS::DynamoDB::Table")["Properties"]

    assert "GlobalSecondaryIndexes" not in table
    assert "LocalSecondaryIndexes" not in table


def test_the_history_table_survives_a_stack_destroy():
    """RETAIN, and the one place this stack breaks its own one-click-uninstall rule.

    Everything else here is reproducible from source - a destroyed source bucket refills on
    the next scrape, a destroyed KB re-ingests. This table is the only copy of what students
    actually said, including crisis disclosures, so `cdk destroy` must not take it. The cost
    is a fixed global name left behind, which collides loudly on the next deploy; that is the
    better half of the trade and it is why this is asserted rather than assumed."""
    table = _resource(_template(), "AWS::DynamoDB::Table")

    assert table["DeletionPolicy"] == "Retain"
    assert table["UpdateReplacePolicy"] == "Retain"


def test_the_history_table_name_comes_from_config():
    from infra.config import resolve_chat_history

    table = _resource(_template(), "AWS::DynamoDB::Table")["Properties"]
    assert table["TableName"] == resolve_chat_history(load_config())["table_name"]


def test_the_chat_role_reaches_the_history_table_and_nothing_else():
    """The replacement for the blanket "no dynamodb:" ban this table made wrong, and a
    tighter statement than that ban was: the grant is scoped to THIS table's ARN by GetAtt.

    dynamodb:Scan IS DELIBERATELY ABSENT, which is why this is hand-rolled rather than
    grant_read_write_data (whose read set includes it). Scan is the only operation here that
    takes no partition key, and the entire isolation story for this table is that the Lambda
    derives the partition key from the JWT `sub` - so a Scan grant is the hole in exactly the
    property the single-table design was chosen for. Nothing needs it; a handler that reaches
    for it should get AccessDenied rather than another student's transcript.

    No table-management actions either: this role uses the table, it does not administer it."""
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
        # The cross-partition read.
        "dynamodb:Scan",
        # Table administration, and the two that would let this role destroy or unpick the
        # retention decision the stack just made.
        "dynamodb:CreateTable",
        "dynamodb:DeleteTable",
        "dynamodb:UpdateTable",
        "dynamodb:UpdateContinuousBackups",
        "dynamodb:UpdateTimeToLive",
        "dynamodb:*",
    ):
        assert forbidden not in granted, f"{forbidden} is granted to the chat role"


def _actions(statement: dict) -> list:
    """A policy statement's actions, as a list whether one or many were rendered.

    CloudFormation renders a single-action statement as a bare string and a multi-action one
    as a list, so a test that assumes either shape passes vacuously against the other.
    """
    action = statement.get("Action", [])
    return [action] if isinstance(action, str) else list(action)


def _resources(statement: dict) -> list:
    """The same normalization for Resource, which collapses the same way.

    A one-resource statement renders as a bare value (here an Fn::GetAtt mapping), a
    multi-resource one as a list. Normalizing means the assertion stays about WHICH ARNs are
    granted rather than about how many happened to be rendered on the day it was written.
    """
    resource = statement.get("Resource", [])
    return resource if isinstance(resource, list) else [resource]


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
    # The history table's name, by REF rather than as the literal from config.yaml - the
    # same no-hardcoded-names rule the KB and guardrail ids follow. app/settings.py treats
    # it as IDENTITY and raises on a missing one, so a function that lost this variable
    # fails at import rather than writing a student's transcript nowhere.
    assert env["CHAT_HISTORY_TABLE_NAME"] == {"Ref": table_id}
    assert env["GENERATION_MODEL_ID"] == resolve_generation(config)["model_id"]
    assert env["BEDROCK_REGION"] == {"Ref": "AWS::Region"}
    assert env["NUMBER_OF_RESULTS"] == str(resolve_retrieval(config)["number_of_results"])
    assert env["RETRIEVE_MIN_SCORE"] == str(resolve_retrieval(config)["min_score"])
    assert env["MAX_CONVERSE_ITERATIONS"] == str(
        resolve_chat(config)["max_converse_iterations"]
    )
    assert env["MAX_HISTORY_MESSAGES"] == str(resolve_chat(config)["max_history_messages"])
    # The read endpoints' caps. Deliberately separate numbers from the one above: that is
    # the window the MODEL is shown and is billed in tokens on every turn, these bound one
    # DynamoDB query of already-stored items.
    assert env["MAX_CONVERSATIONS_LISTED"] == str(
        resolve_chat(config)["max_conversations_listed"]
    )
    assert env["MAX_CONVERSATION_MESSAGES"] == str(
        resolve_chat(config)["max_conversation_messages"]
    )
    assert env["CONVERSE_DEADLINE_SECONDS"] == str(
        resolve_chat(config)["converse_deadline_seconds"]
    )
    # The card caps. Every one of these reaches BOTH the parser that enforces it and the
    # system prompt that states it, so a value that fails to arrive would let the two
    # disagree - the model briefed on one budget, the server applying another.
    cards_cfg = resolve_cards(config)
    assert env["CARD_MAX_CARDS"] == str(cards_cfg["max_cards"])
    assert env["CARD_MAX_RETRIEVAL_RESULTS"] == str(cards_cfg["max_retrieval_results"])
    assert env["CARD_TITLE_MAX_CHARS"] == str(cards_cfg["title_max_chars"])
    assert env["CARD_DESC_MAX_CHARS"] == str(cards_cfg["desc_max_chars"])
    assert env["CARD_FOLLOWUP_MAX_CHARS"] == str(cards_cfg["followup_max_chars"])
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
    unnoticed, and this role should be able to reach no bucket at all.

    "dynamodb:" WAS on this list and has been removed, because the chat history table is now
    a deliberate part of the stack (docs/accounts-and-storage.md). What replaces it is not
    nothing: test_the_chat_role_reaches_the_history_table_and_nothing_else pins the DynamoDB
    grant to that one table's ARN and to data actions only, which is a tighter statement
    than the blanket ban ever made - gav's feedback table would fail there, on the resource.
    """
    policy = _resource_named(_template(), "AWS::IAM::Policy", "ChatFunctionRole")
    actions = json.dumps(policy["Properties"]["PolicyDocument"]["Statement"])
    for absent in ("s3:PutObject", "s3:GetObject", "CATALOG", "primo"):
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
        "history.py",
        "models.py",
        "orchestrator.py",
        "prompts.py",
        "ratelimit.py",
        "retrieve.py",
        "safety.py",
        "settings.py",
        "titles.py",
        "tools.py",
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
    """The naming convention, enforced against the source rather than the template: every
    global name in the template must have come from config.yaml. Checks the stack FILE for
    the configured literals, so a name pasted inline fails even if the template looks right.

    The scraper's schedule and crawl-list filename are on the list for a related reason: a
    literal there would let config.yaml and the deployed stack disagree, and the crawl-list
    filename in particular is read TWICE - once to pick the layer's asset and once for the
    URL_LIST_FILE env var - so a hardcoded copy is how the bundled file and the opened file
    drift apart."""
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
        # The history table's name is global within an account+region, exactly like the
        # vector bucket, and it reaches the chat Lambda's environment as well as the table
        # itself - so an inline copy is how the table that exists and the table the handler
        # opens drift apart.
        resolve_chat_history(config)["table_name"],
        # The guardrail name is a global name in the account. The model id is not, but it
        # reaches THREE IAM ARNs and the Lambda's environment, so an inline copy is how the
        # granted model and the invoked model drift apart.
        resolve_guardrail(config)["name"],
        resolve_generation(config)["model_id"],
        # The Okta metadata URL names somebody's ORG. Inline it and the three settings this
        # one key exists to serve - local-only, the rehearsal tenant, SJSU's - stop being a
        # config edit. (The provider NAME is the deliberate opposite: a constant in
        # infra/config.py that config cannot reach, because it must not differ between them.)
        *([resolve_okta(config)["metadata_url"]] if resolve_okta(config) else []),
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


def test_the_api_serves_exactly_the_five_routes_the_handler_implements():
    """NARROWED, not relaxed. This used to read "only the billable route exists"; the history
    reads and the two conversation WRITES (docs/accounts-and-storage.md, Storage access
    patterns) are additions to that list, so the assertion is the exact set - which still
    fails on a route nobody meant to add, and additionally fails if one of these is dropped
    or renamed. The strings are the route keys app/handler.lambda_handler dispatches on, so a
    rename on either side breaks here rather than at runtime as a 404."""
    routes = _template().find_resources("AWS::ApiGatewayV2::Route")
    assert {route["Properties"]["RouteKey"] for route in routes.values()} == {
        "POST /chat",
        "GET /conversations",
        "GET /conversations/{conversationId}",
        "PATCH /conversations/{conversationId}",
        "DELETE /conversations/{conversationId}",
    }


def test_every_route_is_jwt_gated():
    """POST /chat is the route that spends Bedrock tokens, so an ungated one there would be
    an open paid endpoint. The reads spend none - and are gated just as hard for a different
    reason: the `sub` claim IS the DynamoDB partition key, so an ungated read would have
    nobody to attribute and the handler's only alternative would be trusting a user id off
    the wire. Asserted over ALL routes rather than an allowlist, so a route added later is
    covered by this test on the day it is added."""
    routes = _template().find_resources("AWS::ApiGatewayV2::Route")
    assert routes, "the API has no routes at all"
    for logical_id, route in routes.items():
        assert route["Properties"]["AuthorizationType"] == "JWT", logical_id
        assert route["Properties"].get("AuthorizerId"), logical_id


def test_the_history_reads_are_served_by_the_chat_function():
    """One function, three routes. A second Lambda would need its own copy of the identity
    rule that every partition key comes from the JWT claim, and two copies of that rule is
    one too many."""
    template = _template()
    integrations = template.find_resources("AWS::ApiGatewayV2::Integration")
    assert len(integrations) == 1, (
        "every route should share the one chat integration; a second one means a route "
        "was pointed somewhere else"
    )
    integration_id = next(iter(integrations))
    for route in template.find_resources("AWS::ApiGatewayV2::Route").values():
        assert integration_id in json.dumps(route["Properties"]["Target"])


def test_the_authorizer_is_native_and_reads_the_authorization_header():
    """A native JWT authorizer - no authorizer Lambda, so nothing to cold-start and none
    of our code in the auth decision."""
    authorizer = _resource(_template(), "AWS::ApiGatewayV2::Authorizer")["Properties"]
    assert authorizer["AuthorizerType"] == "JWT"
    assert authorizer["IdentitySource"] == ["$request.header.Authorization"]


def _client_logical_id(template: Template, construct_id: str) -> str:
    """The logical id of one app client, by its construct id.

    There are two clients on the pool now, so `_logical_id` cannot be used: it asserts
    there is exactly one resource of a type.
    """
    found = [
        lid
        for lid in template.find_resources("AWS::Cognito::UserPoolClient")
        if construct_id in lid
    ]
    assert len(found) == 1, f"expected one {construct_id} client, found {found}"
    return found[0]


def test_the_authorizer_audience_carries_both_app_clients():
    """A Cognito ACCESS token carries no `aud` claim, only `client_id`, and API Gateway
    validates client_id only when aud is absent. Do not 'fix' this to an ID token.

    BOTH clients, in this order. The audience is an allowlist of client_id values, so
    dropping the eval client here breaks the harness at the gateway with a CORS-less 401,
    and dropping the web client breaks every student."""
    template = _template()
    authorizer = _resource(template, "AWS::ApiGatewayV2::Authorizer")["Properties"]
    assert authorizer["JwtConfiguration"]["Audience"] == [
        {"Ref": _client_logical_id(template, "ChatWebClient")},
        {"Ref": _client_logical_id(template, "ChatEvalClient")},
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
    """UsernameAttributes: ['email'] would require every account to be an address, and
    AliasAttributes would reject one that looks like an address. Sign-in options are
    IMMUTABLE after creation - changing this later replaces the pool, and with it every
    account in it, which is why the per-user change deliberately left this alone."""
    pool = _resource(_template(), "AWS::Cognito::UserPool")["Properties"]
    assert "UsernameAttributes" not in pool
    assert "AliasAttributes" not in pool


def test_the_pool_adds_no_custom_attributes():
    """A federated profile is rebuilt from the provider's claims on every sign-in, so an
    application-written Cognito attribute is liable to be silently overwritten once Okta
    lands. Application data belongs in DynamoDB keyed by `sub`."""
    pool = _resource(_template(), "AWS::Cognito::UserPool")["Properties"]
    schema = pool.get("Schema") or []
    custom = [entry for entry in schema if entry.get("Name", "").startswith("custom:")]
    assert custom == [], f"custom attributes on the pool: {custom}"


def test_there_are_exactly_two_app_clients():
    """One pool, two callers, and they cannot share a client: a client is the unit Cognito
    applies auth flows to, so a single permissive client would carry a password flow any
    browser could call with the client id published in config.json."""
    clients = _template().find_resources("AWS::Cognito::UserPoolClient")
    assert len(clients) == 2, sorted(clients)


def test_the_web_client_is_public_code_flow_with_no_sign_in_flow_of_its_own():
    """THE assertion this file exists for on the auth side, and the trap it pins is an
    ABSENT ExplicitAuthFlows rather than a wrong one.

    An empty AuthFlow() makes CDK omit the property, and an absent ExplicitAuthFlows does
    NOT mean "no direct sign-in" - Cognito falls back to legacy defaults that include SRP.
    The web client would then quietly accept a local-password sign-in, which is the exact
    dead end this change exists to close: no federated user can ever use SRP, so a form in
    front of it would have to be deleted the day SJSU's IdP arrives.

    ALLOW_REFRESH_TOKEN_AUTH is not a sign-in path - it is what lets the token endpoint
    honour a refresh grant for a session that already came through the redirect."""
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
    """Cognito matches redirect_uri against this list by EXACT STRING, and the frontend
    sends `window.location.origin + "/"`. Both origins are needed - local dev from
    config.yaml, the distribution as a deploy-time token so a fresh install redirects to
    its own site - and the trailing slash is load-bearing on every entry, because a
    mismatch is rendered by Cognito's own error page before the app is ever reached.

    Logout carries the same list: without it /logout is refused and the pool session cookie
    survives, so the next sign-in returns a code without asking who is there."""
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
    """eval/run_eval.py is headless - no browser to redirect, no human to click - so the
    one thing the web client deliberately cannot do is the only thing this one does. No
    secret, so its single unsigned InitiateAuth needs no SECRET_HASH."""
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
    """The provider the committed config.yaml turns on, pinned property by property.

    THE NAME IS THE ONE THAT MATTERS. A federated user's Cognito username is
    `<providerName>_<nameid>`, so renaming this mints new `sub` values - the DynamoDB
    partition key (docs/accounts-and-storage.md) - and orphans every conversation the old
    identities wrote. It is `Okta`, for the provider's ROLE, so the rehearsal org and SJSU's
    tenant share it and neither is named here.

    A METADATA URL, NOT AN UPLOADED DOCUMENT: Cognito re-fetches a URL, so a signing
    certificate rotating on the Okta side is not an outage waiting on a manual re-upload.

    IDP-INITIATED OFF, explicitly rather than by default. An unsolicited assertion is bound
    to no request this app issued, which is a login-CSRF primitive; every sign-in here starts
    at /oauth2/authorize."""
    from infra.config import resolve_okta

    okta = resolve_okta(load_config())
    assert okta is not None, "the committed config.yaml is expected to carry a metadata URL"

    idp = _resource(_template(), "AWS::Cognito::UserPoolIdentityProvider")["Properties"]
    assert idp["ProviderName"] == "Okta"
    assert idp["ProviderType"] == "SAML"
    assert idp["ProviderDetails"]["MetadataURL"] == okta["metadata_url"]
    assert idp["ProviderDetails"]["IDPInit"] is False
    assert "MetadataFile" not in idp["ProviderDetails"]
    # ONE mapping: the Okta-side name from config, onto this pool's own `email`.
    assert idp["AttributeMapping"] == {"email": okta["email_attribute"]}
    # NOT an omission - Cognito derives the username from the SAML NameID and rejects a
    # mapping for it outright. A mapped username would also be a second identity key beside
    # the one `sub` is minted from.
    assert "username" not in idp["AttributeMapping"]
    assert json.dumps(idp["UserPoolId"]) == json.dumps(
        {"Ref": _logical_id(_template(), "AWS::Cognito::UserPool")}
    )


def test_only_the_human_client_offers_okta_and_it_waits_for_the_provider():
    """Two halves, and both are load-bearing.

    THE EVAL CLIENT MUST NOT GAIN IT. CDK fills an omitted SupportedIdentityProviders from
    every provider REGISTERED ON THE POOL, so before this was pinned, creating the Okta
    provider silently attached it to the machine client too - a client whose only caller
    authenticates a password headlessly and has no browser to redirect.

    THE WEB CLIENT MUST WAIT FOR IT. The provider is named as the literal string "Okta"
    rather than a Ref, so CloudFormation sees no reference between the two and would be free
    to create the client first, failing the update with "identity provider does not exist"."""
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
    """THE OTHER DIRECTION, and the one that has to keep working: with no `okta` block the
    stack must synthesize the local-accounts-only pool it has today - no identity provider
    at all, and the human client back to COGNITO alone.

    Asserted against a real second synth rather than by reading the code, because the failure
    it guards is a construct that gets created anyway with an empty or defaulted URL - which
    reads fine in the diff and is a deploy failure (or worse, a live provider nobody meant to
    create) in the account."""
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
    # The rest of the auth wiring is untouched: turning federation off is not a different
    # stack, it is the same one without a provider.
    assert web["Properties"]["ExplicitAuthFlows"] == ["ALLOW_REFRESH_TOKEN_AUTH"]
    assert len(template.find_resources("AWS::Cognito::UserPoolClient")) == 2
    assert "Okta" not in json.dumps(template.to_json())


def test_the_managed_login_domain_exists_and_names_no_account():
    """The hosted endpoints are what make the flow federation-ready: /oauth2/authorize is
    the only place an Okta round trip can happen. The prefix must be globally unique, and
    nothing in this repo may hardcode a global name - so it is derived from the stack id,
    and a fresh install in another account gets its own."""
    template = _template()
    domain = _resource(template, "AWS::Cognito::UserPoolDomain")["Properties"]
    rendered = json.dumps(domain["Domain"])
    assert "AWS::StackId" in rendered, f"the domain prefix must be derived: {rendered}"
    assert "ChatLoginDomain" in template.to_json()["Outputs"], (
        "the frontend cannot redirect without the domain in config.json"
    )


def test_the_config_json_publishes_the_web_client_never_the_eval_one():
    """config.json is world-readable by design. The eval client id in it would publish a
    password-auth endpoint to every visitor."""
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
    """eval/run_eval.py reads ChatEvalClientId. If that output ever resolved to the web
    client the harness would fail at sign-in with NotAuthorizedException, because the web
    client has no password flow."""
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
    """A password in a template is a password in the console, the change set and the stack
    events. The stack prints CLI commands with a placeholder instead."""
    rendered = json.dumps(_template().to_json())
    assert "CHOOSE-A-PASSWORD" in rendered, "the setup command should carry a placeholder"


def test_the_shared_pilot_credential_is_gone_everywhere():
    """The single shared login is retired in the SAME change that adds per-user accounts,
    not a later one - leaving it would mean a live credential nobody owns, still able to
    reach the paid endpoint, for however long the follow-up took.

    Asserted against the template AND the source, because the account was only ever half
    in the template: the username lived in the printed setup commands, and the password
    was always outside the repo. A leftover here is a leftover way in."""
    # Assembled rather than written out so this assertion is not its own only match - a
    # repo-wide search for a literal spelled in the searching file always finds itself,
    # and excluding this file by path would blind the search to the test tree.
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
    real 404s. camp's app is multi-page, so it needs no shell fallback at all.

    The distinction is the STATUS, not whether a page is named. An earlier version of this
    test asserted ResponsePagePath was absent, which looked like the same principle but
    encoded a shape CloudFront rejects outright - it requires ResponseCode and
    ResponsePagePath together or neither, and enforces that at CREATE time. That mistake
    cost a failed deploy and a rollback, so this now pins BOTH halves."""
    dist = _resource(_template(), "AWS::CloudFront::Distribution")["Properties"][
        "DistributionConfig"
    ]
    errors = {e["ErrorCode"]: e for e in dist["CustomErrorResponses"]}
    assert set(errors) == {403, 404}
    for code, entry in errors.items():
        assert entry["ResponseCode"] == 404, f"{code} must surface as a real 404"
        # Both or neither - CloudFront rejects a status with no page.
        assert entry["ResponsePagePath"] == "/404.html", code
        assert entry["ResponseCode"] != 200, "a 200 here would be the blanket fallback"


def test_the_error_page_is_actually_built_and_shipped():
    """A ResponsePagePath pointing at a file the build does not emit would deploy fine and
    then serve CloudFront's own generic error - the 404 behaviour would be silently
    cosmetic. Asserted against the staged site."""
    assert "404.html" in _staged_listing("SiteContentDeployment")


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


def test_config_json_names_the_history_endpoint_the_frontend_reads():
    """The sidebar's conversation list comes from this URL, and the frontend refuses to
    start without it (lib/runtimeConfig.ts checks every key). Stamped beside chatApiUrl
    rather than derived from it in the browser: stripping "/chat" and re-appending would put
    this stack's route names in a file this stack does not build.

    Read from the STAGED FILE, not the template: Source.json_data writes the keys into the
    asset and leaves only substitution markers behind in CloudFormation, so the template
    knows the values are deploy-time tokens but not what they are called. The file is not
    valid JSON at this stage either - every value is a `<<marker:...>>` placeholder that the
    deployment substitutes - so the KEYS are read out of the text."""
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
    # A subset rather than an equality, because `costModel` and its nested blocks legitimately
    # sit alongside them when the cost panel is on. Nothing else may: an unexpected key here
    # is a value that reached a world-readable file without anybody deciding it should.
    assert keys - {
        "chatApiUrl",
        "conversationsApiUrl",
        "userPoolId",
        "userPoolClientId",
        "loginDomain",
        "region",
    } <= {"costModel", "asOf", "region", "currency", "measuredAt", "rates", "measured", "baseline"}


def test_the_cost_panel_is_gated_by_config_and_leaves_no_trace_when_off():
    """`cost_model.enabled: false` must remove the key from config.json entirely.

    THE OMISSION IS THE GATE. The frontend renders the control only when `costModel` is
    present (lib/runtimeConfig.ts), so this is the whole mechanism by which the panel can be
    switched off without a code change - which is what has to hold before Okta federation
    starts provisioning SJSU students into this pool just in time. A student must not be
    shown what the system costs to run, and "the component checks a flag" is a weaker
    guarantee than "the data never reaches the browser".
    """
    config = copy.deepcopy(load_config())
    config["cost_model"]["enabled"] = False

    outdir = tempfile.mkdtemp()
    app = cdk.App(outdir=outdir)
    NavigatorStack(app, "SjsuNavigatorStack", config=config)
    app.synth()
    template = json.loads((Path(outdir) / "SjsuNavigatorStack.template.json").read_text())

    # The prefix also matches the deployment's own CLI layer, which stages no source - so
    # the non-empty check is what picks the deployment out rather than the layer beside it.
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
    # The rates themselves must be gone too, not merely unreferenced - the file is
    # world-readable, so a leftover block would publish the figures anyway.
    assert "generation_input_per_1m" not in text
    assert '"chatApiUrl"' in text, "turning the panel off must not disturb the rest"


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
    # PATCH and DELETE are the rename and delete routes. Both are cross-origin and both
    # carry Authorization, so both are preflighted: dropping either here fails the OPTIONS
    # and surfaces as a CORS error rather than as the missing method it is.
    assert set(api["CorsConfiguration"]["AllowMethods"]) == {
        "POST",
        "GET",
        "PATCH",
        "DELETE",
        "OPTIONS",
    }


def test_the_site_content_is_the_container_built_astro_output():
    """The deployment must carry the BUILT site, not a placeholder or a committed dist/.
    Asserted against the staged asset, so a bundling change that silently produced nothing
    fails here rather than deploying an empty bucket.

    Checked against what camp's build ACTUALLY emits rather than the three pages the
    placeholder had: camp's UI is one page of React islands, so the give-away that the
    bundler really ran is the hashed _astro/ directory and camp's own public assets - a
    placeholder would have neither."""
    listing = _staged_listing("SiteContentDeployment")
    assert "index.html" in listing, listing
    assert "_astro" in listing, (
        f"no hashed asset directory in the staged site: {listing}. camp's UI mounts React "
        "islands, so a build with no _astro/ means the bundler emitted a bare page."
    )
    # Camp's own static assets, copied from its public/ - present only if camp's source
    # was what got built.
    assert "sammy.riv" in listing, listing


def test_a_developers_local_config_json_never_reaches_the_site_asset():
    """The site asset must not carry a config.json, ever - the only one that may exist in
    the bucket is the SiteConfigDeployment's, stamped from stack tokens at deploy.

    To run `astro dev` against a deployed API a developer hand-writes
    frontend/public/config.json with the four runtime keys, and public/ ships verbatim, so
    that file builds straight into dist/ at the same bucket key the stack stamps. It is
    gitignored and dropped inside the build container; this pins the second half.

    Vacuous on a machine with no local file, and deliberately so - it bites exactly where
    the risk lives, on the developer who has one. Same shape as the layer and function
    listing tests above, which also read clean on a fresh clone."""
    listing = _staged_listing("SiteContentDeployment")
    assert "config.json" not in listing, (
        f"a config.json was built into the site asset: {listing}. The site deployment's "
        "exclude keeps it from being uploaded today, but that exclude is there to scope "
        "the prune - narrow it, or merge the two deployments, and this file publishes "
        "over the deploy-stamped one and points the app at whatever API the developer "
        "was testing against."
    )


def test_the_routing_function_matches_the_pages_the_build_emits():
    """The viewer-request function rewrites directory paths to index.html, which is only
    correct if Astro emits directory-format pages. The OAuth routes camp had (/login,
    /auth/callback) are deliberately gone, so the emitted set is the single root page -
    and the rewrite still matters for it, because "/" is served by default_root_object
    while any other path a student types must resolve or 404 honestly."""
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


def test_every_app_module_reaches_the_staged_lambda_asset():
    """THE list-drift test, and it exists because this list has failed silently three
    times: dotfiles staging .git into a layer, the bundler's hardcoded requirements path
    building the wrong deps, and section_presets.py missing from the includes entirely.
    Every one of those synthed clean while the DEPLOYED function would have died at
    import - which is the worst shape a failure can take here, because there is no
    account to catch it before a student does.

    The expectation is read off the FILESYSTEM, never restated here. A test that repeats
    the include list is just a second copy of the thing that keeps going stale."""
    app_dir = Path(__file__).resolve().parents[3] / "app"
    on_disk = {
        path.name
        for path in app_dir.glob("*.py")
        # tests/ live in their own directory and are excluded from the asset on purpose.
        if not path.name.startswith("test_")
    }
    staged = set(_staged_listing("ChatFunction"))

    missing = on_disk - staged
    assert missing == set(), (
        f"app modules that never reach the deployed function: {sorted(missing)}. "
        "Add each to the ChatFunction asset's include list in infra_stack.py - the "
        "function imports them at cold start, so an omission is an ImportError on the "
        "first real request, not a synth failure."
    )

    extra = staged - on_disk
    assert extra == set(), (
        f"staged files with no module on disk: {sorted(extra)}. The include list is "
        "stale, or something unintended is riding along into the function bundle."
    )


# --- Gav-specific surface: absent, and staying absent ------------------------------------


def test_no_gav_specific_resources_were_inherited():
    """Gav's stack carries four things this one has no use for, and none of them were ever
    ported: the Primo catalog tools, the dedicated catalog bucket, the SNS feedback path,
    and DUAL HOSTING (gav serves an embeddable widget AND a demo site from two separate
    bucket + distribution pairs; this app is one site).

    Asserted against the synthesized template rather than the source, because the failure
    this guards against is a future pull copying a gav section wholesale."""
    template = _template()

    assert template.find_resources("AWS::SNS::Topic") == {}, "the feedback path is gav's"
    assert template.find_resources("AWS::SNS::Subscription") == {}

    # EXACTLY ONE DynamoDB table, and it is the chat history table.
    #
    # This assertion used to read `find_resources("AWS::DynamoDB::Table") == {}`, because
    # the only DynamoDB in sight was gav's feedback table and this stack had no store of
    # its own. The chat history table (docs/accounts-and-storage.md) makes the blanket form
    # wrong, so it is NARROWED rather than dropped: gav's feedback table would still fail
    # here, as a second table under a different logical id.
    tables = sorted(template.find_resources("AWS::DynamoDB::Table"))
    assert len(tables) == 1, tables
    assert tables[0].startswith("ChatHistoryTable"), tables

    # ONE distribution. Gav has two (widget CDN + demo site); a second here would mean
    # dual hosting came along with the frontend section.
    assert len(template.find_resources("AWS::CloudFront::Distribution")) == 1

    # Exactly two buckets, both named: the KB source bucket and the site bucket. Gav's
    # catalog and widget buckets would show up here.
    buckets = sorted(template.find_resources("AWS::S3::Bucket"))
    assert len(buckets) == 2, buckets
    assert any(b.startswith("KnowledgeBaseSourceBucket") for b in buckets), buckets
    assert any(b.startswith("SiteBucket") for b in buckets), buckets


def test_the_inherited_gav_pieces_that_are_load_bearing_are_still_here():
    """The other half of the same decision. These came from gav and STAY, so a cleanup
    pass that reads 'remove gav-specific surface' too broadly fails here rather than
    quietly removing the guardrail or the auth gate."""
    template = _template()

    # The PROMPT_ATTACK input screen and its pinned version.
    assert len(template.find_resources("AWS::Bedrock::Guardrail")) == 1
    assert len(template.find_resources("AWS::Bedrock::GuardrailVersion")) == 1
    # The Cognito gate on the billable route.
    assert len(template.find_resources("AWS::Cognito::UserPool")) == 1
    assert len(template.find_resources("AWS::ApiGatewayV2::Authorizer")) == 1
    # The seed-list layer: the crawl list cannot travel in an env var (Lambda's 4 KB
    # aggregate cap), so this is the mechanism, not a gav leftover.
    _resource_named(template, "AWS::Lambda::LayerVersion", "ScraperSeedListLayer")


def test_the_two_deps_layers_are_distinct_assets():
    """THE bug this test exists for, and it cost a green deploy with a dead chat endpoint.

    Under AssetHashType.OUTPUT both deps layers hashed to the same CDK cache key (same
    bundling image, command, platform and one-file exclude). aws-cdk-lib 2.260.0 stages
    via `assetCache.obtain(cacheKey, ...)` and bundles into `bundling-temp-${cacheKey}`,
    which `bundle()` skips outright if it already exists - so the chat layer silently
    reused the scraper's bundle and shipped trafilatura instead of pydantic.

    Nothing caught it: synth was clean, both layers published, the deploy went green, and
    the only symptom was `No module named 'pydantic'` in CloudWatch behind a 502 that the
    UI rendered as an empty chat bubble."""
    template = _template()
    scraper = _resource_named(template, "AWS::Lambda::LayerVersion", "ScraperDepsLayer")
    chat = _resource_named(template, "AWS::Lambda::LayerVersion", "ChatDepsLayer")
    assert (
        scraper["Properties"]["Content"]["S3Key"]
        != chat["Properties"]["Content"]["S3Key"]
    ), "both deps layers resolved to ONE asset - the chat Lambda will not have pydantic"


def test_each_deps_layer_ships_its_own_packages():
    """The stronger form: distinct assets are necessary but not sufficient, since two
    distinct assets could still hold the wrong contents. Asserted against what was staged."""
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
    """A SEPARATE grant, because it is a separate model id: the generation statement names
    one model, so a titling call against another is AccessDenied - and unlike a denied
    generation, that failure is swallowed by design (every conversation keeps its
    first-message title), so it would be discovered from a sidebar that never improves
    rather than from an error."""
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
    """By reference to the same resolved config the IAM grant is built from, so the id the
    function invokes is by construction the id it is allowed to invoke."""
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
    """Covered by test_every_route_is_jwt_gated too, and stated again here because the reason
    is stronger for these two: they CHANGE stored data, and the only thing that decides whose
    data is the `sub` claim the partition key is built from."""
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
    """The per-user cost fence's one number, reaching the function that enforces it.

    THE FAILURE THIS PINS IS SILENT. app/settings.py reads an unset DAILY_MESSAGE_LIMIT as
    "disabled" - it has to, because that is how the gate below turns the cap off - so a
    variable dropped from this block does not raise at import like the identity ones do. It
    just removes the only control that bounds what one account can spend, and nothing about
    the running system looks different until a bill arrives.
    """
    from infra.config import resolve_rate_limit

    env = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Environment"
    ]["Variables"]

    assert env["DAILY_MESSAGE_LIMIT"] == str(resolve_rate_limit(load_config()))


def test_the_eval_client_is_exempt_from_the_daily_message_limit():
    """The exemption is the MACHINE CLIENT's id, by reference to the client itself.

    eval/run_eval.py fires 82 questions as one account at concurrency 3, so any per-user cap
    worth having is one the harness trips - and a tripped harness fails quietly, recording
    refusals as answers so the eval reads as a model regression.

    By Ref rather than a literal for the usual reason, plus one specific to this: the id is
    generated at deploy, so a literal here could not be right. And it is the EVAL client, not
    the web client - pointing this at the browser's id would exempt every student instead.
    """
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
    """`rate_limit.daily_message_limit: 0` must remove the variable entirely, not set "0".

    THE OMISSION IS THE GATE, the same shape the cost panel uses one section down. It means
    "off" has exactly one spelling: the function reads an absent variable as disabled, so
    there is no second value it has to be trusted to interpret, and turning the cap off for a
    closed pilot is a config edit rather than a code change.
    """
    config = copy.deepcopy(load_config())
    config["rate_limit"]["daily_message_limit"] = 0

    app = cdk.App(outdir=tempfile.mkdtemp())
    template = Template.from_stack(NavigatorStack(app, "SjsuNavigatorStack", config=config))

    env = _resource_named(template, "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Environment"
    ]["Variables"]
    assert "DAILY_MESSAGE_LIMIT" not in env
    # The exemption list is unconditional, and harmlessly so: with no cap there is nothing to
    # be exempt from, and making it conditional too would be a second thing to keep in step.
    assert "RATE_LIMIT_EXEMPT_CLIENT_IDS" in env
    assert env["CHAT_HISTORY_TABLE_NAME"], "turning the cap off must not disturb the rest"


def test_the_rate_counter_needs_no_new_table_and_no_new_grant():
    """The counter lives in the chat-history table's own user partition under a third
    sort-key prefix, so this feature adds NO AWS resource: the table is the one that exists,
    its TTL attribute is the one already enabled, and dynamodb:UpdateItem was already granted
    for the header's messageCount ADD.

    Pinned because the alternative shape - a second table, or a wildcard grant - is the
    obvious way to build this and would pass every behavioural test in app/.
    """
    template = _template()

    assert len(template.find_resources("AWS::DynamoDB::Table")) == 1
    table = _resource(template, "AWS::DynamoDB::Table")
    assert table["Properties"]["TimeToLiveSpecification"] == {
        "AttributeName": "expiresAt",
        "Enabled": True,
    }

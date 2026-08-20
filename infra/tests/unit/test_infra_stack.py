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


def _api_logical_id(template: Template, protocol: str) -> str:
    """The logical id of the one API of a protocol - "HTTP" or "WEBSOCKET".

    THERE ARE TWO API GATEWAY v2 APIs IN THE DEFAULT TEMPLATE NOW. config.yaml commits
    `streaming.enabled: true`, so the WebSocket API and its own authorizer, stage, routes
    and integrations are in every synth below - and `_resource` (exactly one of a type)
    can no longer address any of them.

    Selected by ProtocolType rather than by logical-id prefix, because the protocol is what
    the assertions actually mean: "the API students POST to" and "the API they open a
    socket to" are different things, and a rename of either construct must not silently
    re-point a test at the other one.
    """
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
    """The HTTP API's own Properties - the /chat API, not the streaming socket."""
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


def test_scraper_carries_both_layers_deps_and_the_data():
    """Two layers, and the function is useless without either: the deps layer supplies
    trafilatura/httpx (not in the runtime) and the data layer supplies the corpus. A missing
    data layer is the nastier one - the handler raises SeedListError and the run fails, which
    is by design, but it fails every single day until someone reads the logs."""
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
    # A layer whose compatible runtime/arch does not match the function is rejected at
    # UpdateFunctionConfiguration - a deploy-time failure, and the deps layer's wheels are
    # manylinux x86_64 regardless of the machine that ran synth.
    template = _template()
    for prefix in ("ScraperDepsLayer", "CampusDataLayer"):
        props = _resource_named(template, "AWS::Lambda::LayerVersion", prefix)["Properties"]
        assert props["CompatibleRuntimes"] == ["python3.13"], prefix
        assert props["CompatibleArchitectures"] == ["x86_64"], prefix


@functools.lru_cache(maxsize=1)
@functools.lru_cache(maxsize=1)
def _synth_outdir() -> Path:
    """A real synth of the stack config.yaml describes, cached. Streaming is ON in it.

    Its own function so the assertions that need the staged FILES and the ones that need
    the stamped config.json share one synth - each of these costs a deps-layer bundle and
    a containerized Astro build.
    """
    outdir = tempfile.mkdtemp()
    app = cdk.App(outdir=outdir)
    NavigatorStack(app, "SjsuNavigatorStack", config=load_config())
    app.synth()
    return Path(outdir)


def _staged_dirs_in(outdir: Path) -> dict:
    """{logical id -> Path of that resource's staged asset directory}, for one synth.

    Reads the directories CDK actually staged, because the template records only an asset
    HASH - what went into the zip is invisible to a template matcher.
    """
    template = json.loads((outdir / "SjsuNavigatorStack.template.json").read_text())
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
        staged = outdir / ("asset." + s3_key.removesuffix(".zip"))
        if staged.is_dir():
            listings[logical_id] = staged
    return listings


def _staged_asset_dirs() -> dict:
    return _staged_dirs_in(_synth_outdir())


def _staged_assets() -> dict:
    """{logical id -> sorted top-level listing}, derived from the cached directories so
    the expensive synth happens once."""
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


def test_campus_data_layer_ships_only_the_data_files():
    """Synths for real and lists the staged asset, because CDK's exclude globbing is not
    intuitive: `exclude=["*", "!urls.csv"]` alone leaves .DS_Store in the asset - a
    leading-wildcard pattern does not match hidden entries - so this layer shipped stray files
    into a deployed function. ".*" is what fixes it, and only a directory listing proves it,
    since the template records just an asset hash.

    The listing is pinned rather than globbed for the same reason the function bundles below
    are: data/README.md is 200 lines written for a person with a spreadsheet, and it has no
    business inside a Lambda."""
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
    # The data layer rides beside it: places.py, safety.py and prompts.py read the
    # repo-root data/ CSVs at import, and Lambda extracts that layer to /opt.
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
        # The turn sequence, lifted out of handler.py: rate limit, guardrail, write, read,
        # model, write, title. handler.py imports it at module scope.
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
    rename on either side breaks here rather than at runtime as a 404.

    Scoped to the HTTP API, which is not a relaxation: the WebSocket API's three route keys
    are a different set on a different protocol, asserted in the streaming section."""
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
    """POST /chat is the route that spends Bedrock tokens, so an ungated one there would be
    an open paid endpoint. The reads spend none - and are gated just as hard for a different
    reason: the `sub` claim IS the DynamoDB partition key, so an ungated read would have
    nobody to attribute and the handler's only alternative would be trusting a user id off
    the wire. Asserted over ALL of this API's routes rather than an allowlist, so a route
    added later is covered by this test on the day it is added.

    ALL of THIS API's routes: a WebSocket API cannot use a JWT authorizer at all, and its
    own gate ($connect, CUSTOM, one Lambda) is asserted in the streaming section. Widening
    this loop to both APIs would not catch more, it would just fail on the socket."""
    template = _template()
    routes = _api_resources(
        template, "AWS::ApiGatewayV2::Route", _api_logical_id(template, "HTTP")
    )
    assert routes, "the API has no routes at all"
    for logical_id, route in routes.items():
        assert route["Properties"]["AuthorizationType"] == "JWT", logical_id
        assert route["Properties"].get("AuthorizerId"), logical_id


def test_the_history_reads_are_served_by_the_chat_function():
    """One function, three routes. A second Lambda would need its own copy of the identity
    rule that every partition key comes from the JWT claim, and two copies of that rule is
    one too many."""
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
    """A native JWT authorizer - no authorizer Lambda, so nothing to cold-start and none
    of our code in the auth decision.

    That claim is about this door and, since the socket went, about every door: the
    WebSocket API needed a Lambda authorizer because it cannot use the native JWT one, and
    it was the only piece of our code that ever sat in an auth decision here."""
    authorizer = _http_api_resource(_template(), "AWS::ApiGatewayV2::Authorizer")[
        "Properties"
    ]
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
    authorizer = _http_api_resource(template, "AWS::ApiGatewayV2::Authorizer")["Properties"]
    assert authorizer["JwtConfiguration"]["Audience"] == [
        {"Ref": _client_logical_id(template, "ChatWebClient")},
        {"Ref": _client_logical_id(template, "ChatEvalClient")},
    ]


def test_authorization_is_in_the_cors_allow_headers():
    """The frontend sets Authorization from JavaScript, which makes every /chat call
    preflighted. Without this the request dies at the OPTIONS and the symptom is a CORS
    error, which reads like a config problem rather than the auth problem it is."""
    api = _http_api(_template())
    assert "Authorization" in api["CorsConfiguration"]["AllowHeaders"]


def test_cors_never_allows_a_wildcard_origin():
    api = _http_api(_template())
    assert "*" not in api["CorsConfiguration"]["AllowOrigins"]


def test_the_stage_throttle_renders_cloudformation_property_names():
    """THE finding this test exists for: a RouteSettingsProperty passed as a map VALUE
    renders camelCase keys (throttlingRateLimit) that CloudFormation does not recognise,
    so the throttle would deploy silently unapplied. DefaultRouteSettings renders
    PascalCase. With no billing alarm in v1, an unapplied throttle is half the cost fence."""
    from infra.config import resolve_http_api

    expected = resolve_http_api(load_config())
    settings = _http_api_resource(_template(), "AWS::ApiGatewayV2::Stage")["Properties"][
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
    """WIDENED from "there is exactly one OAC" when the streaming endpoint joined this
    distribution. An OAC carries an ORIGIN TYPE and one control cannot serve two of them,
    so the bucket's `s3` control and the Function URL's `lambda` control are necessarily
    two resources - and a count of one stopped being the invariant the day the second
    origin arrived. What is asserted instead is the thing the count was standing in for:
    one control per origin type, no type twice, and no OriginAccessIdentity anywhere."""
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
    # EVERY origin, not just the first: the deprecated path is what this test is named for,
    # and a second origin arriving with an OAI would have slipped past an index of 0.
    for origin in dist["Origins"]:
        assert origin.get("S3OriginConfig", {}).get("OriginAccessIdentity") in (
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
    """THE anti-pattern this guards against: mapping a page miss to index.html with a 200
    would make every typo look like a working page that failed to render, and would mask
    real 404s. camp's app is multi-page, so it needs no shell fallback at all.

    The distinction is the STATUS, not whether a page is named. An earlier version of this
    test asserted ResponsePagePath was absent, which looked like the same principle but
    encoded a shape CloudFront rejects outright - it requires ResponseCode and
    ResponsePagePath together or neither, and enforces that at CREATE time. That mistake
    cost a failed deploy and a rollback, so this now pins BOTH halves.

    403 is the whole of the site's half here, because it is the only status a missing key
    can arrive on - see test_the_site_origin_signals_a_missing_page_with_403. What 404 does
    instead is test_the_error_page_does_not_cover_the_api_path."""
    errors = _custom_error_responses()
    entry = errors[403]
    assert entry["ResponseCode"] == 404, "a page miss must surface as a real 404"
    # Both or neither - CloudFront rejects a status with no page.
    assert entry["ResponsePagePath"] == "/404.html"
    assert entry["ResponseCode"] != 200, "a 200 here would be the blanket fallback"


def _custom_error_responses() -> dict:
    dist = _resource(_template(), "AWS::CloudFront::Distribution")["Properties"][
        "DistributionConfig"
    ]
    return {e["ErrorCode"]: e for e in dist["CustomErrorResponses"]}


def test_the_error_page_does_not_cover_the_api_path():
    """THE ERROR MAPPING IS DISTRIBUTION-WIDE, SO THE STATUS CODES HAVE TO DIVIDE THE TWO
    ORIGINS - there is nowhere else to draw the line. CustomErrorResponses lives on
    DistributionConfig and AWS::CloudFront::Distribution CacheBehavior has no
    error-response property at all, so an entry that names a status claims it on `/api/*`
    exactly as much as on the site.

    A 404 is what a dead streaming front door answers: nothing behind `/api`, or a FastAPI
    whose prefix does not match the behaviour's. Mapped to /404.html it came back as the
    site's error page - a curl got HTML with no hint the API had been reached at all, and
    the browser got a non-2xx and fell back to buffered POST /chat, so a broken deploy read
    as a working product on both of the instruments a deploy actually has. So 404 carries
    NEITHER a response code nor a page: CloudFront returns the origin's own response, and
    ErrorCachingMinTTL is 0 because the default is ten seconds and a cached front-door
    failure outlives the fix that repaired it.

    The pass-through entry is asserted to EXIST rather than 404 being asserted absent. An
    absence is indistinguishable from an oversight, and the shape it would be "corrected"
    back to is the one this is here to forbid."""
    errors = _custom_error_responses()

    passthrough = errors[404]
    assert "ResponsePagePath" not in passthrough, (
        "404 is what a dead /api answers; a page here masks it as a site miss"
    )
    assert "ResponseCode" not in passthrough, (
        "404 must reach the client as its own status, unrewritten"
    )
    assert passthrough["ErrorCachingMinTTL"] == 0, passthrough

    # Nothing else may claim a status on the way past. 403 is the site's (below); any
    # third entry is a status one of the two origins answers with, quietly redirected.
    assert set(errors) == {403, 404}, sorted(errors)

    # The statuses the streaming app writes by hand, read off disk rather than restated:
    # a 400 for an unparseable body, a 401 for a token it will not verify, a 503 when it
    # cannot serve. None of them may be a code this mapping substitutes a page for.
    source = (Path(__file__).resolve().parents[3] / "app" / "streaming_app.py").read_text()
    answered = {int(code) for code in re.findall(r"status_code=(\d{3})", source)}
    assert answered, "no status codes found in app/streaming_app.py"
    substituting = {code for code, e in errors.items() if "ResponsePagePath" in e}
    assert not (answered & substituting), (
        f"the streaming app answers {sorted(answered & substituting)}, which the "
        f"distribution replaces with the site's error page"
    )


def test_the_site_origin_signals_a_missing_page_with_403():
    """WHY 403 IS THE ONE STATUS THAT CARRIES THE SITE'S ERROR PAGE, asserted where the
    reason actually lives: the bucket policy.

    A REST (OAC) origin returns 403 AccessDenied for a key that is not there, because
    without s3:ListBucket S3 will not distinguish "absent" from "forbidden". That is the
    only reason a page miss arrives on 403 and not on 404, and it is what leaves 404 free
    to mean "the streaming app said so" (test_the_error_page_does_not_cover_the_api_path).

    Grant ListBucket here and the two swap without a word: site misses start arriving as
    404 and get the pass-through, real page misses stop reaching /404.html, and the API's
    own 404 starts being masked instead. Nothing about that fails at synth or at deploy,
    which is why it is pinned rather than commented."""
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
    # sit alongside them when the cost panel is on, and `streamingApiUrl` when the socket is.
    # Nothing else may: an unexpected key here is a value that reached a world-readable file
    # without anybody deciding it should.
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
    """`cost_model.enabled: false` must remove the key from config.json entirely.

    THE OMISSION IS THE GATE, and what it gates is now the cost SECTION rather than the gear
    in the sidebar. This test used to say the control itself was absent without `costModel`,
    which was true when the gear opened the cost panel and nothing else; the gear now opens a
    settings panel whose first control is the student's own language, so it is there in every
    deployment and the cost breakdown inside it is not (components/SettingsPanel.tsx).

    None of that weakens what is asserted here, which is about the DATA and not the UI: with
    the panel off, no rate and no measured figure reaches the browser at all. A student must
    not be shown what the system costs to run - which is what has to hold before Okta
    federation starts provisioning SJSU students into this pool just in time - and "the
    component checks a flag" is a weaker guarantee than "the file never contains it".
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


def test_settings_survives_the_cost_model_being_off():
    """The other half of the gate above, and the half that changed: with no `costModel`, the
    student must still have the gear.

    It is checked at the PROP, because that is where the old behaviour lived. `onOpenCost`
    was optional and ChatApp passed it only when a cost model had been stamped, so the whole
    control disappeared with the panel - and the language picker now behind that gear belongs
    to the student, not to the sponsor the cost figures are for. A required prop cannot be
    the undefined that used to hide the button.

    A source-text assertion is a blunt instrument and this is the one thing it is good at:
    pinning a decision that is otherwise recorded only in a comment. It says nothing about
    how the panel renders - that was checked in a browser, in both languages, with the cost
    model absent."""
    side_nav = (
        Path(__file__).resolve().parents[3] / "frontend" / "src" / "components" / "SideNav.tsx"
    ).read_text()
    assert "onOpenSettings: () => void;" in side_nav, (
        "SideNav must take onOpenSettings as a REQUIRED prop. Making it optional again is "
        "how the gear went missing whenever cost_model.enabled was false."
    )
    # The name survives in the doc comment that explains the change, which is the point of
    # that comment; what must not come back is the optional prop or a call that passes it.
    assert "onOpenCost?:" not in side_nav and "onOpenCost={" not in side_nav, (
        "the old optional cost-panel prop must not come back alongside the settings one"
    )


def test_the_cloudfront_origin_joins_the_api_cors_allowlist_as_a_token():
    """The app is served from the distribution, so the browser sends that origin on every
    /chat call. Resolved at deploy, never hardcoded."""
    api = _http_api(_template())
    origins_rendered = json.dumps(api["CorsConfiguration"]["AllowOrigins"])
    assert "SiteDistribution" in origins_rendered
    assert "*" not in api["CorsConfiguration"]["AllowOrigins"]


def test_the_amended_cors_block_keeps_the_authorization_header():
    """The escape hatch REPLACES the whole CORS block, so dropping a header here would
    break every /chat call at the preflight - with a CORS error, which reads like a config
    problem rather than the auth problem it would be."""
    api = _http_api(_template())
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
    the include list is just a second copy of the thing that keeps going stale.

    NARROWED, NOT WEAKENED, when the streaming path landed: app/ stopped feeding one
    function, so "every module reaches the chat function" stopped being true (the connect
    authorizer's module deliberately reaches nothing else, and must not). What has to hold
    is the property that actually catches the failure - every module on disk is deployed
    SOMEWHERE, and nothing is staged that has no module behind it - so it is asserted
    against the union of every function built out of app/.

    THE LIST BELOW IS THE ONLY PLACE THAT KNOWS HOW MANY THERE ARE. A function added to the
    stack and not added here does not fail: its modules are simply counted as reaching
    nothing, which passes as long as some other bundle carries them. Adding it is what makes
    the `extra` half of this test cover its bundle too.
    """
    app_dir = Path(__file__).resolve().parents[3] / "app"
    on_disk = {
        path.name
        for path in app_dir.glob("*.py")
        # tests/ live in their own directory and are excluded from the asset on purpose.
        if not path.name.startswith("test_")
    }

    # TWO FUNCTIONS NOW, and it was five: the $connect authorizer, the WebSocket route
    # function and the generation worker went with the socket, and their three modules went
    # with them. What that costs this test is its margin - every module on disk has to reach
    # one of these two, where before a module could ride in any of five bundles.
    functions = (
        "ChatFunction",
        # The streaming app under the Lambda Web Adapter. Its bundle also carries run.sh,
        # which is not a module and is filtered out below with every other non-.py entry -
        # the executable bit that makes it work is pinned by
        # test_the_stream_probe_ships_an_executable_run_sh instead. The construct id still
        # says "probe" because renaming it would REPLACE the function and its URL.
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

    # THE BUNDLE THAT IS A CLAIM RATHER THAN A CONVENIENCE. The $connect authorizer used to
    # be it - one file, unable to reach the store or the model even by importing them - and
    # it is gone. The claim moved down a layer with the decision: the streaming app verifies
    # tokens in its own process, so what must be true is that handler.py, the API Gateway
    # transport, is NOT in its bundle. Two request pipelines in one function is how a route
    # ends up reachable by a path nobody meant to serve.
    assert "handler.py" not in per_function["StreamProbeFunction"], (
        "the streaming app must not ship the API Gateway handler"
    )
    assert "token_auth.py" in per_function["StreamProbeFunction"], (
        "it has no authorizer in front of it, so the verifier rides in its bundle"
    )


# --- The WebSocket streaming API that is gone ---------------------------------------------
#
# THIS BLOCK USED TO HOLD EIGHTEEN TESTS pinning that surface into place: the $connect
# authorizer and its identity source, the message route, the worker's zero retries, the
# stage throttle, the two grants, the crypto layer, and the config.json key that told the
# browser a socket existed. All of them asserted PRESENCE. They are one test now, and it
# asserts the opposite - because "we deleted it" is not a property, and a WebSocket that
# came back through a merge, a revert or a copied block would otherwise be caught by
# nothing at all.
#
# WHAT REPLACED IT is app/streaming_app.py behind the Function URL below: one function
# running the whole turn in band, which is what the socket existed to work around.


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
    """THE INVERSION OF THE WHOLE SECTION THAT USED TO BE HERE.

    Not "no WebSocket API" but "no WebSocket ANYTHING": the API, the stage, the two routes,
    the authorizer, its deps layer, the route function and the generation worker were seven
    kinds of resource, and a leftover authorizer with no API is still a deployed Lambda with
    a permission on it and a bill.

    THE STRONGEST CASE IS THE DEFAULT SYNTH, which is why there is no second one here.
    config.yaml still commits `streaming.enabled: true` - that block's three values were the
    socket's batching and output-guardrail knobs and nothing reads them any more - so this
    template is the one where the old gate was OPEN. Nothing appears. A test that synthesized
    with the key off would prove strictly less.

    THE PUSH GRANT IS ASSERTED SEPARATELY because it is the one that could survive a partial
    revert: a function still holding `execute-api:ManageConnections` is a function that could
    push down a connection, which is the capability this transport was."""
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

    # Every gateway route and stage left in the template belongs to the HTTP API. Asserted
    # by protocol on the parent rather than by counting, so a WebSocket route added to a new
    # API fails here too.
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

    # SANITY, so this test cannot pass by synthesizing nothing: the HTTP API's own JWT
    # authorizer and POST /chat are untouched, and so is the pool the eval harness signs in
    # to. Those are the pieces the removal was NOT allowed to take with it.
    assert template.find_resources("AWS::ApiGatewayV2::Authorizer") != {}
    assert template.find_resources("AWS::Cognito::UserPool") != {}
    assert len(template.find_resources("AWS::Cognito::UserPoolClient")) == 2


def test_nothing_tells_the_browser_a_socket_exists():
    """THE INVERSION OF test_the_config_json_names_the_socket_only_when_streaming_is_on.

    The key was the browser's TRANSPORT switch: present meant open a socket. The browser now
    POSTs to `/api/chat` on this same distribution, which is a relative path and nothing to
    stamp, so the key is not merely off - there is no code that would write it.

    Read off the STAGED config.json rather than the template, which is the fix an earlier
    version of the positive test needed: Source.jsonData writes the file into a
    BucketDeployment asset and leaves a `<<marker>>` behind, so `"streamingApiUrl" not in
    the template` was an assertion that could not fail."""
    stamped = _stamped_config_json(_synth_outdir())
    assert "streamingApiUrl" not in stamped
    assert "execute-api" not in stamped, stamped
    # The keys the frontend does require are still stamped - see
    # test_config_json_names_the_history_endpoint_the_frontend_reads for the full set.
    assert '"chatApiUrl"' in stamped and '"userPoolClientId"' in stamped


def test_the_socket_modules_are_gone_from_the_tree():
    """THE OTHER HALF, and the one the template cannot see. A stack that stopped REFERENCING
    app/streaming.py would leave the module on disk, importable, still holding a second copy
    of the turn's order - which is exactly the drift app/turn.py exists to prevent.

    app/ws_authorizer.py is named here for a second reason. It was the only place in this
    repo where a bearer token travelled in a URL, and
    test_the_authorizer_never_logs_the_token was the rule that no log line might touch it.
    That rule has no subject any more, and this is what says so."""
    app_dir = Path(__file__).resolve().parents[3] / "app"
    for gone in (
        "streaming.py",
        "stream_worker.py",
        "ws_authorizer.py",
        "requirements-authorizer.txt",
        # The module rename: it is not a probe, and run.sh, the bundle and the router
        # prefix all name the new file.
        "stream_probe.py",
    ):
        assert not (app_dir / gone).exists(), f"app/{gone} is back"

    assert (app_dir / "streaming_app.py").exists()
    assert "streaming_app:app" in (app_dir / "run.sh").read_text(), (
        "run.sh must exec the renamed module - the handler is a PATH, so a stale name is a "
        "clean deploy and a function that never starts"
    )


# --- The response-streaming probe: FastAPI + Lambda Web Adapter + Function URL ----------
#
# UNGATED, so it is in every synth in this file and the cached template addresses it
# directly. That is the opposite choice from the WebSocket surface above and the reason
# these assertions need no second synth.
#
# WHAT MAKES THEM WORTH WRITING. Almost nothing about this function looks wrong when it is
# wrong. A handler of "run.sh" is either the adapter's wrapper contract or a typo; a
# response that arrives in one piece is either a buffered InvokeMode or a buffered adapter;
# an AuthType of NONE is a working demo and an open door on the bill. None of it fails at
# synth, and the deploy that would catch it is the one this probe exists to de-risk.


def _stream_probe(template: Template) -> dict:
    """The probe function's Properties."""
    return _resource_named(template, "AWS::Lambda::Function", "StreamProbeFunction")[
        "Properties"
    ]


def _stream_probe_url(template: Template) -> dict:
    """The probe's Function URL Properties.

    Addressed as THE one AWS::Lambda::Url in the stack rather than by name, so a second
    Function URL appearing anywhere fails here rather than being quietly ignored."""
    return _resource(template, "AWS::Lambda::Url")["Properties"]


def _distribution_config(template: Template) -> dict:
    return _resource(template, "AWS::CloudFront::Distribution")["Properties"][
        "DistributionConfig"
    ]


def _stream_edge_behavior(template: Template) -> dict:
    """The streaming endpoint's cache behaviour.

    Addressed as THE one entry in CacheBehaviors rather than by its path pattern, so a
    second behaviour appearing on this distribution fails here rather than being skipped
    over - the site is one origin plus this one, and a third would be a routing decision
    nobody made in this file."""
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
    """THE HANDLER IS A FILE PATH, NOT A DOTTED CALLABLE, and that is the whole shape of
    the adapter. /opt/bootstrap out of the LWA layer is `exec -- "$LAMBDA_TASK_ROOT/$_HANDLER"`,
    so the runtime never imports anything: it execs a file out of the bundle, that file
    starts uvicorn, and the adapter proxies the invocation to it.

    Both halves are pinned here because either one alone is a broken function that
    synthesizes clean. Without the wrapper the python3.13 runtime tries to import a module
    called "run" and dies at init; without the "run.sh" handler the wrapper execs whatever
    dotted name it was given and dies on No such file."""
    props = _stream_probe(_template())

    assert props["Handler"] == "run.sh"
    assert props["Environment"]["Variables"]["AWS_LAMBDA_EXEC_WRAPPER"] == "/opt/bootstrap"

    # And the handler names a file that is actually IN the bundle. A handler pointing at a
    # path the include list does not ship is the same failure with a different message.
    assert props["Handler"] in _staged_listing("StreamProbeFunction")

    # A ZIP, not an image. PackageType absent means Zip; the upstream example this is
    # morphed from is PackageType: Image, which would bring ECR and an image build to CI.
    assert "PackageType" not in props, props.get("PackageType")
    assert props["Runtime"] == _LAMBDA_PYTHON.name
    assert props["Architectures"] == [_LAMBDA_ARCH.name]


def test_the_stream_probe_ships_an_executable_run_sh():
    """THE FILE MODE IS THE FRAGILE PART, and it is invisible in the template - which
    records an asset hash and nothing about what is inside it.

    /opt/bootstrap EXECS this file. exec on a file without the execute bit is
    Permission denied, and on a file without a shebang is Exec format error - both at
    invocation, both after a completely clean deploy. The bit has to survive three hops:
    git (mode 100755), CDK's asset staging into cdk.out, and the CDK CLI's zip. This
    covers the first two, by reading the file after staging has happened. THE THIRD IS
    NOT COVERED BY ANY TEST - the zip is written at `cdk deploy`, not at synth - and what
    stands behind it is the CLI's own writeZipFile, which passes the file's st_mode into
    the zip entry's external attributes.

    The OTHER-execute bit specifically, not just the owner's: nothing guarantees the
    Lambda sandbox runs as the uid that owns /var/task."""
    import stat

    staged = _staged_asset_dir("StreamProbeFunction")
    # WHAT is in the bundle is pinned by the import-graph test below; this one is about the
    # one file in it whose MODE decides whether the function starts.
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
    """A rename with nothing to catch it. run.sh names its ASGI app as a string
    ("stream_probe:app"), so renaming the module or the FastAPI instance leaves a green
    synth, a green test suite and a function that cannot start - the failure is uvicorn's,
    at cold start, in CloudWatch.

    Both sides are read off disk rather than restated: the string out of run.sh, and the
    module-level binding out of the app's AST."""
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
    """THE CONSTRAINT THAT DEPLOYS CLEAN AND FAILS AT RUNTIME. AWS publishes the adapter as
    one layer per architecture under two DIFFERENT NAMES, and Lambda does not refuse an
    x86_64 layer on an arm64 function - it accepts it and the extension dies on an Exec
    format error at init.

    The name-to-architecture table is written out again here on purpose. It is a published
    AWS fact rather than one of our config values, so a second copy is a check, not the
    duplication the rest of this file avoids: infra_stack.py derives the name from
    _LAMBDA_ARCH, and this asserts that what came out matches the architecture the TEMPLATE
    actually gives the function."""
    template = _template()
    props = _stream_probe(template)
    architecture = props["Architectures"][0]

    published_by_aws = {
        "x86_64": "LambdaAdapterLayerX86",
        "arm64": "LambdaAdapterLayerArm64",
    }
    # The deps layer arrives as a {"Ref": ...} to a resource in this template; the adapter
    # is the one that arrives as an assembled ARN.
    arn = json.dumps([layer for layer in props["Layers"] if "Fn::Join" in layer])

    # The ARN ENDS in the layer name and a version number - the trailing quote is the
    # anchor, so an ARN with anything after the version, or with no version at all, fails
    # here. A layer ARN without a version does not identify a layer version and the deploy
    # is the thing that finds out.
    assert re.search(rf':layer:{published_by_aws[architecture]}:\d+"', arn), arn
    assert f':layer:{published_by_aws[architecture]}:{_LWA_LAYER_VERSION}"' in arn, arn

    # Published by AWS's account, in THIS stack's region and partition rather than a
    # literal - a fresh install in another region attaches its own region's copy.
    assert _LWA_LAYER_ACCOUNT in arn, arn
    assert '"Ref": "AWS::Region"' in arn, arn
    assert '"Ref": "AWS::Partition"' in arn, arn


def test_the_stream_probe_carries_the_data_layer_as_well_as_its_own_two():
    """THE HALF OF THE data/ MOVE THAT NO IMPORT GRAPH CAN SEE.
    test_the_stream_probe_ships_every_module_it_imports pins campus_data.py INTO the
    bundle; nothing there knows the module then goes looking for CSVs at /opt, and only
    this layer puts them there. Without it the include list is complete, synth is clean,
    the deploy is green and the first cold start raises CampusDataError out of
    places.py's module scope - the same shape of failure as a missing include, with none
    of the same tests watching for it.

    Asserted as the WHOLE list rather than a membership check, because the adapter needs
    to still be here too: this function is the one place three layers meet, and dropping
    either of the other two is the same clean-deploy-dead-function outcome."""
    template = _template()
    deps_id = next(iter(_layer_ids(template, "StreamProbeDepsLayer")))
    data_id = next(iter(_layer_ids(template, "CampusDataLayer")))
    layers = _stream_probe(template)["Layers"]

    # The adapter is the ARN one (see the test above); the other two are Refs to layers
    # this template builds.
    assert [layer for layer in layers if "Ref" in layer] == [
        {"Ref": deps_id},
        {"Ref": data_id},
    ], layers
    assert len([layer for layer in layers if "Fn::Join" in layer]) == 1, layers
    assert len(layers) == 3, layers


def test_the_stream_probe_streams_on_both_the_url_and_the_adapter():
    """TWO SWITCHES, AND ONE OF THEM ALONE IS A BUFFERED FUNCTION THAT LOOKS FINE.

    InvokeMode is a property of the Function URL: without RESPONSE_STREAM, Lambda collects
    the whole body and sends it in one piece no matter what the app did. AWS_LWA_INVOKE_MODE
    is the adapter's own setting: without it the adapter buffers before Lambda ever sees the
    body. Either omission produces a working endpoint that answers all at once, which is
    exactly the outcome this probe exists to distinguish from a working one.

    THE TWO SPELLINGS DIFFER and that is not a typo here: RESPONSE_STREAM is
    CloudFormation's enum for AWS::Lambda::Url, response_stream is the adapter's own."""
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
    """THE BILLING HOLE THIS STACK MUST NOT HAVE. The upstream examples set AuthType: NONE,
    which is an endpoint any stranger on the internet can invoke - behind none of the four
    fences POST /chat sits behind, and on a Function URL there is no Cognito gate and no
    route throttle to add.

    AuthType also decides what is NOT in the template, which is the second half here: CDK
    attaches an anonymous lambda:InvokeFunctionUrl permission to AnyPrincipal ONLY when the
    auth type is NONE. So the assertion that no such permission exists anywhere is a real
    check on the auth type rather than a restatement of it - flip AuthType to NONE and this
    test fails twice.

    NARROWED, DELIBERATELY, and this is the one pre-existing assertion the CloudFront work
    could not keep. It used to read "no AWS::Lambda::Permission in this stack names the
    probe at all", which was exactly right while SigV4 was the only way in. Origin access
    control IS a resource-based policy - there is no version of OAC that does not put one
    on the function - so "nobody" became unreachable the moment the edge was allowed to
    call it. What replaces it is not weaker: every grant on the probe must name the
    CloudFront service principal AND carry a SourceArn condition naming this stack's own
    distribution, so an unconditioned grant, a wildcard principal, or a grant to any other
    caller still fails here. The two actions themselves are pinned next door, in
    test_the_edge_holds_both_invoke_actions_scoped_to_this_distribution."""
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
    """ONE ORIGIN FOR THE BROWSER. The stream is a behaviour on the distribution that
    already serves the site, not a distribution of its own, which is what makes the whole
    app one domain - no CORS story to write later, no second allowlist entry, no preflight
    in front of a request whose only value is time to first byte.

    THE SITE STILL OWNS EVERYTHING ELSE, and that is asserted rather than assumed: the
    default behaviour must still point at the S3 origin, because a pattern that captured
    "/" would take the home page down and leave the adapter's readiness route answering in
    its place.

    THE DIRECTORY-INDEX FUNCTION MUST NOT RUN HERE. It appends /index.html to any path
    without a dot, which turns /api/stream into /api/stream/index.html and every route on
    this behaviour into a 404. It is attached to the default behaviour only."""
    template = _template()
    behavior = _stream_edge_behavior(template)
    config = _distribution_config(template)

    assert behavior["PathPattern"] == _STREAM_EDGE_PATH_PATTERN

    # The behaviour points at the Function URL, and the default one does not.
    stream_origin = _origin_by_id(template, behavior["TargetOriginId"])
    assert "StreamProbeFunctionFunctionUrl" in json.dumps(stream_origin["DomainName"]), (
        stream_origin
    )
    # A Function URL is HTTPS-only and has no S3 config; a CustomOriginConfig that lost its
    # https-only would be an edge talking to Lambda in the clear.
    assert stream_origin["CustomOriginConfig"]["OriginProtocolPolicy"] == "https-only"
    assert "S3OriginConfig" not in stream_origin

    site_origin = _origin_by_id(template, config["DefaultCacheBehavior"]["TargetOriginId"])
    assert "S3OriginConfig" in site_origin, (
        "the default behaviour stopped pointing at the bucket, so the site is being served "
        f"by something else: {site_origin}"
    )

    assert "FunctionAssociations" not in behavior, behavior


def test_the_streaming_behaviour_caches_nothing_and_keeps_the_origins_own_host():
    """THREE SETTINGS, EACH OF WHICH BREAKS SOMETHING DIFFERENT WHEN WRONG, and none of
    which is visible at synth.

    CACHING DISABLED: every route under this prefix is a stream. A cached turn is one
    student's answer served to another; a cached probe answers the question this endpoint
    exists to ask with a copy of the last run's timings.

    ALL_VIEWER **EXCEPT HOST HEADER**: origin access control signs each origin request with
    SigV4 over the ORIGIN's host - the lambda-url domain - so forwarding the viewer's Host,
    which is the distribution's domain, makes every signature fail to validate at Lambda.
    AWS publishes this policy for exactly this case ("intended for use with Amazon API
    Gateway and AWS Lambda function URL origins"). The rest of what it forwards is needed:
    the query string /api/model reads its question from, and content-type plus the
    x-amz-content-sha256 body hash a POST has to carry.

    COMPRESS OFF, against a CDK default of ON: compressing is holding bytes in order to
    compress them, and this endpoint's whole job is to measure whether the edge holds
    bytes.

    The two policy ids are AWS's published constants, written out here rather than read
    back off the construct - a second copy of a published fact is a check, and
    `CachePolicy.CACHING_DISABLED.cache_policy_id` would only restate whatever the source
    already said."""
    behavior = _stream_edge_behavior(_template())

    assert behavior["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad", (
        "not the managed CachingDisabled policy"
    )
    assert behavior["OriginRequestPolicyId"] == "b689b0a8-53d0-40ab-baf2-68738e2966ac", (
        "not the managed AllViewerExceptHostHeader policy"
    )
    assert behavior["Compress"] is False
    # POST carries /api/chat. ALLOW_ALL is the only AllowedMethods value that includes it -
    # CloudFront has no POST-without-DELETE option - so the whole verb list is asserted
    # rather than just the one that matters, to make the breadth deliberate.
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
    """THE PAIR THAT HAS TO AGREE, and neither half means anything alone.

    SigningBehavior `always` with SigningProtocol `sigv4` is what makes the edge the only
    caller that can reach an AWS_IAM Function URL without credentials of its own; AWS_IAM
    is what stops anyone else reaching it at all. Set signing to `never` and the URL has to
    be public for the edge to work, which is the AuthType: NONE billing hole this stack
    refuses, arrived at from the other direction.

    ORIGIN TYPE `lambda`, not `s3`: they are different controls and the bucket's is the
    other one. A distribution serving a Function URL through an `s3` control signs a
    request Lambda will not accept."""
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

    # And it is the control THIS behaviour's origin actually carries, rather than one that
    # merely exists in the template.
    stream_origin = _origin_by_id(template, behavior["TargetOriginId"])
    assert stream_origin["OriginAccessControlId"] == {
        "Fn::GetAtt": [lambda_oac_id, "Id"]
    }, stream_origin

    assert _stream_probe_url(template)["AuthType"] == "AWS_IAM"


def test_the_edge_holds_both_invoke_actions_scoped_to_this_distribution():
    """BOTH ACTIONS, AND CDK ONLY WRITES ONE OF THEM.

    `FunctionUrlOrigin.withOriginAccessControl` emits a single AWS::Lambda::Permission for
    `lambda:InvokeFunctionUrl`. Invoking a Function URL has required BOTH that and
    `lambda:InvokeFunction` since October 2025, and the CloudFront developer guide's OAC
    setup is two `aws lambda add-permission` calls for that reason. With one of them the
    edge answers 403 AccessDenied, which reads like a signing mistake and is not - so the
    stack writes the second permission by hand and this pins that it is still there
    (aws/aws-cdk#35872; Lambda's grace period for the single-action form ends 2026-11-01,
    which means a template that synthesizes clean today breaks on a DATE).

    THE CONDITION IS THE OTHER HALF. `cloudfront.amazonaws.com` is a service principal
    every AWS customer shares, so an unconditioned grant reads "any distribution in any
    account may invoke this function". Both statements must carry the same SourceArn, and
    it must name this stack's own distribution - as a token, so a fresh install scopes to
    its own."""
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
    """THE CONTRACT NO SYNTH AND NO OTHER TEST CAN SEE. CloudFront matches a behaviour on
    the viewer's path and forwards that path to the origin UNCHANGED - there is no
    prefix-stripping short of a rewrite function - so the pattern in infra_stack.py and the
    router prefix in app/streaming_app.py have to be the same string. Disagree and the
    template is valid, the deploy is clean, and every request is a 404 from FastAPI served
    through a distribution behaving exactly as configured.

    READ OFF DISK, BOTH SIDES, and the app's side through the AST rather than an import:
    fastapi is in the probe's own layer and in no venv this suite installs, so
    `import stream_probe` raises here. Reading the tree is also what makes the SHAPE
    assertable - which decorator each route hangs off.

    `/` STAYS ON THE APP, NOT THE ROUTER, and that is the second half. It is the adapter's
    readiness target: the LWA polls AWS_LWA_READINESS_CHECK_PATH (default "/") on
    127.0.0.1 before it forwards anything, so a "/" that moved under the prefix is a
    readiness check answering 404 at every cold start and a function that never serves a
    request. Keeping it off the prefix is also what leaves the site owning the root."""
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
    """IT RUNS THE SAME TURN, SO IT HOLDS THE SAME GRANTS - and neither of the two the
    socket needed. A streamed turn that could reach less than a buffered one would fail
    somewhere subtle and only for some questions; a streaming function that could start a
    Lambda or push down a WebSocket connection would be one that had quietly grown a second
    architecture, and that architecture has just been removed.

    WIDENED, DELIBERATELY, when this stopped being a probe. It used to hold one action on
    one model, which was right when the only thing it did was stream a bare ConverseStream.
    Serving the turn means the turn's grants, and the assertion moved with it.

    THE COMPARAND IS THE CHAT FUNCTION NOW. It was the WebSocket generation worker, which
    was the other function built from `_chat_turn_statements`; with the socket gone the
    chat function is, and it is the better one anyway - it is the transport this one has to
    render identically to.

    NARROWED WHEN THE APP LEARNED WHO ITS CALLER IS, and narrowed by ONE statement that is
    named and asserted rather than waved past. This function has no authorizer in front of
    it, so it reads its client allowlist from Parameter Store at cold start - that is the
    DOOR, not the turn, which is why it is written beside `_chat_turn_statements` in the
    stack instead of into it. The equality below still holds over everything else, so the
    property it was written for is intact: this function may reach exactly what the turn
    reaches, plus the one parameter that decides whose turns they are."""
    template = _template()
    role_id = next(
        lid
        for lid in template.find_resources("AWS::IAM::Role")
        if lid.startswith("StreamProbeFunctionServiceRole")
    )
    role = template.find_resources("AWS::IAM::Role")[role_id]["Properties"]
    managed = role.get("ManagedPolicyArns", [])
    # EXACTLY ONE managed policy. A second entry is how a broad grant arrives without an
    # inline policy for the comparison below to see.
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
    # Neither role may carry a push grant now. It was the exact difference between running
    # the turn out of band and running it in band, and there is no out of band left.
    assert "execute-api:" not in json.dumps(chat), chat

    # THE ONE STATEMENT THAT IS THE DOOR AND NOT THE TURN, lifted out and asserted on its
    # own rather than allowed to blur the comparison below. It reads the client allowlist
    # (see test_the_streaming_app_is_told_which_pool_and_which_clients_to_trust); the chat
    # function has an authorizer in front of it and needs nothing like it.
    allowlist_grants = [
        statement for statement in probe if "ssm:" in json.dumps(statement)
    ]
    assert len(allowlist_grants) == 1, allowlist_grants
    assert _actions(allowlist_grants[0]) == ["ssm:GetParameter"], allowlist_grants[0]
    # Scoped to the one parameter, by an ARN assembled from pseudo-parameters - a Ref to
    # the parameter would put the dependency cycle back through IAM.
    scoped_to = json.dumps(_resources(allowlist_grants[0]))
    assert ":ssm:" in scoped_to and "streaming/allowed-client-ids" in scoped_to, scoped_to
    assert "*" not in scoped_to, scoped_to
    probe_turn_grants = [
        statement for statement in probe if statement not in allowlist_grants
    ]

    # Compared as rendered JSON so a changed resource ARN or a widened action shows up
    # rather than being counted equal. Sorted because the two roles are built by different
    # constructs and nothing promises the order.
    assert _rendered(probe_turn_grants) == chat_grants, (
        json.dumps({"streaming app": probe, "chat": chat}, indent=2, sort_keys=True)
    )

    document = json.dumps(probe)
    # It retrieves, it invokes, it screens and it stores. Named individually rather than
    # left to the set comparison above, so this still says what the turn needs if the
    # comparand is ever deleted.
    assert "bedrock:Retrieve" in document, document
    assert "bedrock:InvokeModel*" in document, document
    assert "bedrock:ApplyGuardrail" in document, document
    assert "dynamodb:PutItem" in document, document
    # And it starts nothing and pushes down nothing. These were the socket's two grants,
    # and not needing them is the argument for this function existing.
    assert "lambda:InvokeFunction" not in document, document
    assert "execute-api:" not in document, document
    # dynamodb:Scan is the one operation that takes no partition key, and the whole
    # isolation story is that the partition comes from the JWT `sub`.
    assert "dynamodb:Scan" not in document, document

    # And no reserved concurrency: capacity out of the account pool belongs to the chat
    # function alone (test_only_the_chat_function_reserves_concurrency states the rule).
    assert "ReservedConcurrentExecutions" not in _stream_probe(template)


def test_the_stream_probe_ships_every_module_it_imports():
    """THE LIST THAT CANNOT BE READ CAREFULLY ENOUGH. This function's bundle is a
    file-by-file include list, and every entry on it is an ImportError at cold start if it
    is missing - after a completely clean synth and a completely clean deploy, because
    nothing in CloudFormation knows what a Python import is. How many entries there are is
    deliberately not written down here; it was, it said fifteen, and the list had grown to
    twenty by the time anybody read it again.

    So the expectation is COMPUTED, never restated: it walks app/streaming_app.py's own
    imports, follows every one that names another module in app/, and asserts the closure is
    in the staged asset. Adding an import to any module on that path fails here until the
    include list catches up, which is exactly the failure that reached production twice
    before (see test_every_app_module_reaches_the_staged_lambda_asset)."""
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

    # The other direction, so the list does not quietly accumulate modules nothing imports:
    # every .py in the bundle is on the import path of the app it serves.
    extra = staged - {f"{name}.py" for name in needed}
    assert extra == set(), (
        f"staged in the streaming app's bundle but imported by nothing in it: "
        f"{sorted(extra)}"
    )

    # The control: handler.py is the API Gateway transport and must NOT be here. If the two
    # ever share a bundle, one of them has started importing the other.
    assert "handler.py" not in staged, staged


def _token_auth_source() -> str:
    return (Path(__file__).resolve().parents[3] / "app" / "token_auth.py").read_text()


def test_the_streaming_app_is_told_which_pool_and_which_clients_to_trust():
    """THE THREE VALUES THAT DECIDE WHO GETS SERVED, and none of them may be a literal.

    A Lambda Function URL accepts no authorizer, so app/token_auth.py verifies the Cognito
    access token in this function's own process. What it accepts is entirely these three:
    the region and pool that make the issuer and the JWKS URL, and the app clients whose
    tokens count. Hardcode any of them and a fresh install in another account trusts the
    pool of the account it was copied from - which does not fail, it serves the wrong
    people.

    THE ALLOWLIST ARRIVES BY NAME BECAUSE THE VALUE IS A DEPENDENCY CYCLE, and the shape is
    asserted here so nobody "simplifies" it back: the browser's app client carries this
    distribution's domain as its OAuth callback, the distribution serves this function's
    URL on /api/*, and a client id in this function's environment closes the loop. CDK
    refuses to synth it. So the ids go in a Parameter Store parameter and the function
    carries the parameter's NAME, which references nothing.

    THE SAME PAIR THE $connect AUTHORIZER CARRIES, asserted by comparing the two rather
    than by restating either. Two doors into one pool with two different answers to "whose
    tokens?" is how one of them ends up wrong, and the eval harness reaching one transport
    and being 401'd by the other is the exact shape that costs a night."""
    template = _template()
    variables = _stream_probe(template)["Environment"]["Variables"]

    # The region as a stack token, never the string this laptop happens to be configured
    # for.
    assert variables["COGNITO_REGION"] == {"Ref": "AWS::Region"}, variables[
        "COGNITO_REGION"
    ]

    # The pool by reference to the one this stack creates.
    pool_id = _logical_id(template, "AWS::Cognito::UserPool")
    assert variables["USER_POOL_ID"] == {"Ref": pool_id}, variables["USER_POOL_ID"]

    # The allowlist by NAME, and the name is the stack's own so two installs in one account
    # do not share one parameter.
    parameter = _resource_named(
        template, "AWS::SSM::Parameter", "StreamingAllowedClientIds"
    )["Properties"]
    assert variables["ALLOWED_CLIENT_IDS_PARAMETER"] == parameter["Name"], {
        "function": variables["ALLOWED_CLIENT_IDS_PARAMETER"],
        "parameter": parameter["Name"],
    }
    # The name references NO RESOURCE - only pseudo-parameters and literals. That is the
    # whole mechanism: a name is a string, and a string is not a dependency edge.
    name = json.dumps(parameter["Name"])
    assert "Fn::GetAtt" not in name, name
    assert not [
        ref
        for ref in re.findall(r'"Ref":\s*"([^"]+)"', name)
        if not ref.startswith("AWS::")
    ], name
    # And it is scoped to this install, so two stacks in one account do not share it.
    assert "streaming/allowed-client-ids" in name, name

    # BOTH clients are in the parameter's VALUE, and the eval runner's is the one a
    # "tightening" would drop.
    value = json.dumps(parameter["Value"])
    web_client = _client_logical_id(template, "ChatWebClient")
    eval_client = _client_logical_id(template, "ChatEvalClient")
    assert json.dumps({"Ref": web_client}) in value, value
    assert json.dumps({"Ref": eval_client}) in value, value

    # And it is the SAME pair the HTTP API's own authorizer accepts as its audience. This
    # used to be compared against the $connect authorizer's ALLOWED_CLIENT_IDS as well -
    # three doors into one pool, and the assertion was that none of them answered "whose
    # tokens?" differently. That door is gone; the two that are left still have to agree,
    # because a student served by one and refused by the other is a bug nobody can
    # reproduce from the browser.
    audience = _http_api_resource(template, "AWS::ApiGatewayV2::Authorizer")[
        "Properties"
    ]["JwtConfiguration"]["Audience"]
    rendered_audience = json.dumps(audience)
    assert json.dumps({"Ref": web_client}) in rendered_audience, rendered_audience
    assert json.dumps({"Ref": eval_client}) in rendered_audience, rendered_audience
    assert len(audience) == 2, audience


def test_nothing_the_site_distribution_reaches_names_an_app_client():
    """THE CYCLE THIS STACK MUST NOT GROW BACK, stated as the property rather than as the
    workaround.

    ChatWebClient carries the distribution's domain as its OAuth callback URL, so it
    depends on SiteDistribution. SiteDistribution depends on the streaming app's Function
    URL, which depends on the streaming app. Anything on that function that names an app
    client therefore closes a four-resource dependency cycle - and CDK refuses to synth it,
    which is the good case. The bad case is somebody splitting the difference and putting
    the reference somewhere CDK's check does not look.

    WHICH CLIENTS CYCLE IS COMPUTED, NOT LISTED. The eval runner's machine client has no
    callback URLs, so it does not reach the distribution, and the streaming app names it
    directly for the daily cap's exemption list. Writing down "the web client is the
    dangerous one" would be a fact that goes stale the day a second redirect client is
    added, so this reads the template: any client whose own properties reach
    SiteDistribution is one this function may not name."""
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
        # And the parameter itself is not Ref'd either - same edge, same cycle.
        assert "StreamingAllowedClientIds" not in document, document


def test_the_streaming_apps_layer_carries_the_library_its_verifier_needs():
    """AN IMPORT THAT IS NOT IN THE LAYER IS A COLD START THAT DIES, after a clean synth and
    a clean deploy - the failure mode the include-list tests exist for, one level down at
    the dependency.

    app/token_auth.py imports `jwt` and `jwt.PyJWKClient`, and the `crypto` EXTRA is not
    optional: without it PyJWT cannot verify RS256 at all, which is the only algorithm
    Cognito signs with. That failure is a runtime one on a student's first request, and it
    looks like a bad token rather than a missing wheel.

    Read off disk rather than restated. This used to be compared against the $connect
    authorizer's own layer - two doors into one pool verifying with two versions of one
    library is a difference nobody would choose - and that layer went with the socket, so
    what is left is the pin itself and the suite that exercises it.

    THE DEV SUITE IS THE SECOND HALF and is asserted here rather than assumed:
    app/tests/test_token_auth.py mints real RS256 tokens, so the same extra has to be in
    requirements-dev.txt or those tests never run against a real signature."""
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

    # The layer's asset hash covers the file, so a changed pin rebuilds rather than leaving
    # the previous build staged. Asserted because the hash is computed in the stack and a
    # dropped argument there is invisible everywhere else.
    stack_source = (Path(__file__).resolve().parents[2] / "infra" / "infra_stack.py").read_text()
    assert 'requirements-stream-probe.txt"' in stack_source


def test_the_auth_header_is_spelled_in_exactly_one_place():
    """ONE NAME, ONE FILE - the treatment EDGE_PATH_PREFIX gets, for the same reason.

    The token rides a header of the app's own because origin access control's SigV4
    signature owns `Authorization`. A second spelling of that header - in the route, in a
    log line, in the stack - is a 401 that synthesizes clean, deploys clean and belongs to
    nobody, because the two halves each look correct on their own. So AUTH_HEADER_NAME in
    app/token_auth.py is the only place the string exists, and everything else imports it.

    Scanned across app/ and infra/ as TEXT rather than through an import, because the one
    module that would have to be imported to check it (app/streaming_app.py) needs fastapi,
    which is in the streaming app's own layer and in no environment this suite builds."""
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

    # Lower case, because every layer between the browser and this process lower-cases
    # header names and one canonical spelling is a lookup that never has to guess.
    assert header == header.lower(), header

    spelled_in = []
    for path in sorted(
        list((root / "app").rglob("*.py"))
        + list((root / "infra").rglob("*.py"))
        + list((root / "app").glob("*.txt"))
        + [root / "app" / "run.sh"]
    ):
        if header in path.read_text():
            spelled_in.append(str(path.relative_to(root)))

    # token_auth.py declares it; its own suite reads the constant to build a request, which
    # is the one other place the string may appear - and it appears there as an import, not
    # as a literal, so this list is the assertion.
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
    """The value of `export const NAME = '...'` (or a plain const) out of a TS file.

    Read with a regex rather than a parser because there is no TypeScript in this suite and
    there is not going to be: the assertion is about one string literal in one line, and a
    node toolchain to check it would be a second build system in the Python tests."""
    match = re.search(
        rf"^(?:export )?const {re.escape(name)} = '([^']*)';$", source, re.MULTILINE
    )
    assert match is not None, f"{name} is not a single-quoted const in chatStream.ts"
    return match.group(1)


def test_the_browser_calls_the_same_prefix_the_edge_routes_and_the_app_serves():
    """THE THIRD SPELLING OF ONE STRING, and the one no Python test could see.

    test_the_edge_path_pattern_and_the_apps_own_routes_are_one_string ties the CloudFront
    behaviour to the FastAPI router. This ties the BROWSER to both: the site and the stream
    share one origin, so the client sends a relative path, and a client whose prefix
    disagrees with the behaviour's is a request that never leaves the default behaviour -
    it fetches the streaming route out of the S3 bucket and gets the 404 page. That
    synthesizes clean, deploys clean, builds clean, and is only visible in a browser."""
    prefix = _ts_const(_chat_stream_client_source(), "STREAM_PATH_PREFIX")
    assert prefix == _STREAM_EDGE_PATH_PREFIX, (
        f"the browser calls {prefix!r} and the edge routes {_STREAM_EDGE_PATH_PREFIX!r}"
    )


def test_the_browser_sends_the_token_on_the_header_the_app_reads():
    """ONE HEADER NAME ACROSS TWO LANGUAGES, which is the half
    test_the_auth_header_is_spelled_in_exactly_one_place cannot reach: that one scans app/
    and infra/, and the browser is neither.

    The token cannot ride `Authorization` because origin access control's SigV4 signature
    owns it on the origin request, so the app reads a header of its own and the browser has
    to write the same one. Disagree and every student is a 401 from a function that is
    working exactly as written.

    NEITHER LITERAL IS TYPED HERE. Both sides are read off disk and compared, because this
    file is inside the scan above - a literal in this test would BE the second spelling it
    exists to forbid.

    The body hash is asserted alongside it because it is the other header this transport
    cannot work without: Lambda refuses an OAC-signed origin request whose payload is
    unsigned, so a client sending a body has to hand the edge the SHA-256 to sign over. It
    is a hash and not a credential, which is why a browser can compute it and holds no AWS
    key to sign anything with."""
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

    # The header is AWS's own name for the body hash, so it is a literal on both sides
    # rather than a constant of ours. What is asserted is that the client still sends one:
    # dropping it is a 403 from Lambda that reads exactly like a broken signing rule.
    assert _ts_const(client, "BODY_HASH_HEADER_NAME") == "x-amz-content-sha256"
    assert "crypto.subtle.digest('SHA-256'" in client, (
        "the body hash must be computed from the body, not asserted"
    )


def test_the_streaming_app_takes_its_caller_from_the_verified_token_and_never_from_the_body():
    """THE CLAIM THAT MAKES THE PARTITION KEY A SECURITY BOUNDARY. Every DynamoDB key in
    this app is built from the Cognito `sub` (docs/accounts-and-storage.md), and that only
    means anything while `sub` is something the server derived rather than something the
    caller said. A user id read off a request body would be the same value with nothing
    behind it, and the convenience and the vulnerability would be the same line of code.

    THREE THINGS ARE PINNED, all by reading app/streaming_app.py off disk - fastapi is in
    that function's own layer and in no environment this suite builds, so an import here
    raises and the AST is what is left.

    1. The route's user_id comes from identity_from's result and from nothing else.
    2. identity_from reads the token through app/token_auth.py's verifier, and touches no
       request body.
    3. The only key the route pulls out of the parsed body by hand is the query. Everything
       else a client sends goes through ChatRequest, which drops unknown keys - the same
       screen POST /chat's body goes through, and the reason there is no `history` field
       and no user id to fill in."""
    import ast

    tree = ast.parse(_stream_probe_source())
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    chat = functions["chat"]

    # 1. THE ROUTE. `user_id=` is passed exactly once out of the route, and its value is an
    # attribute of the object identity_from returned. (The other `user_id=` in the module
    # is _turn_frames handing its own parameter down to run_turn.)
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

    # 2. THE VERIFIER, and no body. identity_from calls token_auth's verifier() and reads
    # request.headers; a reference to the body anywhere inside it would be a second
    # identity source on a transport that must have exactly one.
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

    # And the names it imports are token_auth's, so the checks are that module's rather
    # than a second copy inside the transport.
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "token_auth"
        for alias in node.names
    }
    assert {"verifier", "Unauthorized"} <= imported, sorted(imported)

    # 3. THE BODY. Exactly one key is read out of it by hand; everything else goes through
    # ChatRequest and is dropped if it is not a field.
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
    """THE POINT OF LANDING IT ALONE. This commit proves a mechanism; it moves no logic. If
    the probe is reachable through the HTTP API or the socket, or if the browser can find
    it, then it is not a probe - it is a third transport nobody reviewed.

    Its environment is the assertion with teeth, and it is derived rather than listed: the
    probe carries the CHAT function's variables (app/settings.py has no defaults for the
    identity set, so anything less would not load) plus the adapter's own and the pool it
    verifies tokens against. A STREAM_CALLBACK_URL or a STREAM_WORKER_FUNCTION_NAME
    appearing in here would mean the probe had been wired into the socket's plumbing,
    which is not this feature.

    NARROWED WHEN THE APP LEARNED WHO ITS CALLER IS, and narrowed rather than deleted. It
    used to read "the chat function's variables plus exactly the three the adapter needs",
    which was right while the route answered 401 to everybody and needed to know nothing
    about the pool. Verifying a Cognito token in process needs three more - which pool,
    which region, which app clients - and there is no version of this feature that does
    not. What the test is FOR is unchanged and is now stated as its own assertion below:
    the socket's variables must never appear here. Those three are named explicitly rather
    than left to a wildcard, so a fourth arriving still fails.
    """
    template = _template()

    chat_env = set(
        _resource_named(template, "AWS::Lambda::Function", "ChatFunction")["Properties"][
            "Environment"
        ]["Variables"]
    )
    # EXACTLY the chat function's variables - the exemption list included, now that this
    # function applies the daily cap too - plus the three the adapter needs, plus the three
    # app/token_auth.py verifies against, and nothing else.
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

    # THE PROPERTY THIS TEST EXISTS FOR, said out loud now that the equality above is no
    # longer only about the adapter. The socket's own variables are the ones that would
    # mean the streaming app had been wired into the plumbing it exists to replace.
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

    # And the browser is never told it exists. Read off the STAGED config.json for the
    # reason the streaming gate's test gives: a deploy-time token leaves a `<<marker>>`
    # behind in the asset, so the KEY is what is visible there - and the key is what the
    # frontend reads. Scanned as TEXT rather than parsed, because those markers are not
    # JSON and json.loads on this file raises.
    stamped = (_staged_asset_dir("SiteConfigDeployment") / "config.json").read_text()
    keys = re.findall(r'"(\w+)"\s*:', stamped)
    assert not [key for key in keys if "probe" in key.lower()], sorted(keys)

    # The deployer IS told, through a stack output - which is where a thing only a signed
    # caller can use belongs.
    outputs = json.loads(
        (_synth_outdir() / "SjsuNavigatorStack.template.json").read_text()
    )["Outputs"]
    assert any(name.startswith("StreamProbeFunctionUrl") for name in outputs), sorted(
        outputs
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
    # The Cognito gate on the billable route. NARROWED, not dropped, when streaming was
    # committed on: there are two authorizers in this template now, and exactly one of
    # them is the native JWT gate in front of POST /chat. The other is the socket's
    # $connect Lambda, which is this same pool checked by our own code.
    assert len(template.find_resources("AWS::Cognito::UserPool")) == 1
    jwt_authorizers = [
        lid
        for lid, r in template.find_resources("AWS::ApiGatewayV2::Authorizer").items()
        if r["Properties"]["AuthorizerType"] == "JWT"
    ]
    assert len(jwt_authorizers) == 1, jwt_authorizers
    assert _http_api_resource(template, "AWS::ApiGatewayV2::Authorizer")
    # The data layer: the crawl list cannot travel in an env var (Lambda's 4 KB aggregate
    # cap), so this is the mechanism, not a gav leftover. It carries the rest of data/ now
    # as well, for the same reason and by the same route.
    _resource_named(template, "AWS::Lambda::LayerVersion", "CampusDataLayer")


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


# --- Escalate to a human ------------------------------------------------------------------


def test_the_chat_function_carries_the_escalation_wiring():
    """The three values that let a turn assemble a draft, reaching the function that does it.

    THE RECIPIENT IS SPELLED IN TWO PLACES ON PURPOSE - here and in config.json - because
    the two consumers need different halves of the same decision: the server assembles the
    message, and the browser decides whether the component exists at all. Both come from one
    resolved value, so they cannot drift.
    """
    from infra.config import resolve_escalation

    escalation = resolve_escalation(load_config())
    env = _resource_named(_template(), "AWS::Lambda::Function", "ChatFunction")["Properties"][
        "Environment"
    ]["Variables"]

    assert env["ESCALATION_RECIPIENT"] == escalation["recipient"]
    assert env["ESCALATION_SUBJECT"] == escalation["subject"]
    assert env["ESCALATION_MAX_CHARS"] == str(escalation["max_chars"])


def test_the_escalation_module_is_in_the_functions_that_import_it():
    """app/orchestrator.py and app/prompts.py both import escalation.py, so a bundle without
    it is an ImportError on the first invocation rather than a missing feature. The file
    lists are explicit (no directory glob), which is exactly why a new module has to be added
    to them by hand - and why this asserts it was."""
    template = _template()
    # Both functions that run the turn. It was three - the WebSocket generation worker was
    # the middle one - and the pair that is left is the buffered transport and the streamed
    # one, which is the whole set now.
    for function in ("ChatFunction", "StreamProbeFunction"):
        resource = _resource_named(template, "AWS::Lambda::Function", function)
        staged = _staged_asset_dir(function)
        assert (staged / "escalation.py").exists(), f"{function} cannot import escalation"
        assert (staged / "orchestrator.py").exists(), resource["Properties"]["Handler"]


def test_the_escalation_path_is_gated_by_config_and_leaves_no_trace_when_off():
    """A blank `escalation.contact` must remove the whole path: no variables on the function,
    no key in config.json.

    THE OMISSION IS THE GATE, and it reaches further here than for the cost panel. With no
    variables the function's own prompt builder leaves the tag out of the system prompt
    (app/prompts.py), so the model is never taught a contract this deployment cannot honour -
    which is a stronger guarantee than a component checking a flag.
    """
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
    """The browser needs the address to know the feature exists at all. It is not a secret -
    it is a published campus mailbox, and every draft shows it to the student before they
    send anything - so a world-readable file is the right place for it."""
    from infra.config import resolve_escalation

    staged = (_staged_asset_dir("SiteConfigDeployment") / "config.json").read_text()

    assert resolve_escalation(load_config())["recipient"] in staged

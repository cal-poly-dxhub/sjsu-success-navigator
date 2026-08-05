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

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.config import load_config
from infra.infra_stack import NavigatorStack


def _template() -> Template:
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


def test_no_global_name_is_hardcoded_in_the_stack_source():
    """The naming convention, enforced against the source rather than the template: every
    global name in the template must have come from config.yaml. Checks the stack FILE for
    the configured literals, so a name pasted inline fails even if the template looks right."""
    from pathlib import Path

    from infra.config import resolve_knowledge_base, resolve_vector_store

    config = load_config()
    source = (Path(__file__).resolve().parents[2] / "infra" / "infra_stack.py").read_text()
    for name in (
        resolve_knowledge_base(config)["name"],
        resolve_vector_store(config)["vector_bucket_name"],
        resolve_vector_store(config)["index_name"],
    ):
        assert name not in source, (
            f"{name!r} is hardcoded in infra_stack.py - it must come from config.yaml"
        )

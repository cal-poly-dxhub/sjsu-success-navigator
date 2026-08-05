"""Scaffold assertions: config loads with the expected shape, the stack synthesizes
empty, and an invalid config fails at synth rather than at deploy. Run from infra/ with
`python -m pytest` (gav convention: cwd on sys.path makes `infra.*` resolve to
infra/infra/)."""

import copy

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.config import load_config
from infra.infra_stack import NavigatorStack


def _template() -> Template:
    app = cdk.App()
    stack = NavigatorStack(app, "SjsuNavigatorStack", config=load_config())
    return Template.from_stack(stack)


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


def test_scaffold_stack_synthesizes_empty():
    template = _template()
    resources = template.to_json().get("Resources", {})
    # Tolerate the CDK metadata resource if analytics context ever adds it;
    # anything else in a scaffold is a mistake.
    real = {k: v for k, v in resources.items() if v.get("Type") != "AWS::CDK::Metadata"}
    assert real == {}, f"scaffold stack must be empty, found: {sorted(real)}"


def test_stack_rejects_an_invalid_config_at_synth():
    """The validators are only worth having if the STACK runs them - a validator nothing
    calls is a comment. Instantiating the stack must fail on a bad config, and it must fail
    for a section that has not been built yet (here: cors, consumed by the API section)."""
    config = copy.deepcopy(load_config())
    config["cors"]["allow_origins"] = ["*"]
    with pytest.raises(ValueError, match="must not contain"):
        NavigatorStack(cdk.App(), "SjsuNavigatorStack", config=config)

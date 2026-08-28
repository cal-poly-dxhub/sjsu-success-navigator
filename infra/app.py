#!/usr/bin/env python3
"""CDK entrypoint: load the repo-root config.yaml, instantiate the one stack."""

import aws_cdk as cdk

from infra.config import load_config
from infra.infra_stack import NavigatorStack

app = cdk.App()
NavigatorStack(app, "SjsuNavigatorStack", config=load_config())
app.synth()

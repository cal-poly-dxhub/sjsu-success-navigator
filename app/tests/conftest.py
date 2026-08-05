"""Put app/ on sys.path so the Lambda's flat imports (`from settings import ...`) resolve
in tests exactly as they do in the deployed function, where the handler and its modules sit
side by side at the bundle root.

Also stubs boto3 before any app module is imported. The suite is hermetic by design: the
chat path is Bedrock calls all the way down, and none of them can be exercised without an
account, so a test run must never depend on boto3 being installed or on credentials
existing. Anything that would touch AWS is monkeypatched per test.
"""

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The identity variables the CDK stack sets. settings.load_settings() raises without them,
# by design (app/settings.py), and handler.py calls it at import - so they have to exist
# before collection, not inside a fixture. setdefault so a test can still override.
for _name, _value in {
    "KNOWLEDGE_BASE_ID": "KB-TEST",
    "GENERATION_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
    "BEDROCK_REGION": "us-west-2",
    "INPUT_GUARDRAIL_ID": "gr-test",
    "INPUT_GUARDRAIL_VERSION": "1",
}.items():
    os.environ.setdefault(_name, _value)

if "boto3" not in sys.modules:
    boto3_stub = types.ModuleType("boto3")

    def _no_client(*args, **kwargs):  # pragma: no cover - guard, not behaviour
        raise AssertionError(
            "a test reached boto3.client(); AWS calls must be monkeypatched"
        )

    boto3_stub.client = _no_client
    sys.modules["boto3"] = boto3_stub

if "botocore" not in sys.modules:
    botocore_stub = types.ModuleType("botocore")
    botocore_config = types.ModuleType("botocore.config")

    class _Config:  # pragma: no cover - a stand-in for botocore.config.Config
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    botocore_config.Config = _Config
    botocore_stub.config = botocore_config
    sys.modules["botocore"] = botocore_stub
    sys.modules["botocore.config"] = botocore_config

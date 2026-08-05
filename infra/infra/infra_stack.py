"""SJSU Student Success Navigator infrastructure stack - scaffold.

Empty but valid: it accepts the loaded config and creates no resources. Each
"Copy over and morph" bullet in docs/build-plan.md lands its gav section into the
banner below that names it, in this order (mirroring gav's own section order so a
pull is a copy + rename, not a re-architecture):

  1. Vector store + Knowledge Base + S3 data source   ("pull gav kb section")
  2. Scraper Lambda + deps layer + daily schedule
     + on-deploy install trigger                      ("pull gav scraper shell")
  3. Bedrock Guardrail (PROMPT_ATTACK input screen)   (docs/synthesis.md decision)
  4. Chat Lambda + role + deps layer                  ("pull gav lambda section")
  5. Cognito pool + client + JWT authorizer
     + HTTP API + routes + throttling                 ("pull gav api gateway")
  6. Site bucket + CloudFront (OAC) + Astro deploy
     + config.json stamping                           ("pull gav frontend s3 + cloudfront")

All changeable knobs come from the repo-root config.yaml (see infra/config.py).
"""

from typing import Any, Dict

from aws_cdk import Stack
from constructs import Construct


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

        # Held for the sections below; the scaffold intentionally creates nothing.
        self._config = config

        # --- 1. Vector store + Knowledge Base + S3 data source -------------------

        # --- 2. Scraper: Lambda + deps layer + daily schedule + install trigger --

        # --- 3. Guardrail: PROMPT_ATTACK input screen (after safety intercept) ---

        # --- 4. Chat Lambda: bare handler + role + deps layer --------------------

        # --- 5. Auth + API: Cognito pool/client + JWT authorizer + HTTP API ------

        # --- 6. Site delivery: S3 + CloudFront (OAC) + Astro + config.json -------

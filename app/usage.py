"""What one turn actually consumed, counted from what Bedrock itself reports.

One student message is not one model call, and prompt-cache units are deliberately not
counted; see docs/chat-service.md, What one turn cost.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TurnUsage(BaseModel):
    """The billable units one /chat request consumed, on its way to the browser."""

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    model_calls: int = Field(default=0, alias="modelCalls")
    input_tokens: int = Field(default=0, alias="inputTokens")
    output_tokens: int = Field(default=0, alias="outputTokens")
    guardrail_content_units: int = Field(default=0, alias="guardrailContentUnits")
    retrievals: int = Field(default=0, alias="retrievals")

    def record_model_call(self, response: Any) -> None:
        """Fold one Converse response's token usage in, counting the call either way."""
        self.model_calls += 1
        reported = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(reported, dict):
            return
        for key, field in (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens")):
            value = reported.get(key)
            if isinstance(value, int):
                setattr(self, field, getattr(self, field) + value)

    def record_guardrail(self, reported: Any) -> None:
        """Fold one ApplyGuardrail response's content-policy units in."""
        if not isinstance(reported, dict):
            return
        value = reported.get("contentPolicyUnits")
        if isinstance(value, int):
            self.guardrail_content_units += value

    def record_retrieval(self) -> None:
        self.retrievals += 1

"""What one turn actually consumed, counted from what Bedrock itself reports.

One student message is not one model call, the turn calls two DIFFERENT models, and
prompt-cache units are deliberately not counted; see docs/chat-service.md, What one turn
cost.
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
    title_input_tokens: int = Field(default=0, alias="titleInputTokens")
    title_output_tokens: int = Field(default=0, alias="titleOutputTokens")
    guardrail_content_units: int = Field(default=0, alias="guardrailContentUnits")
    retrievals: int = Field(default=0, alias="retrievals")

    def record_model_call(self, response: Any) -> None:
        """Fold one GENERATION Converse response's token usage in, counting the call either way."""
        self.model_calls += 1
        self._fold(response, "input_tokens", "output_tokens")

    def record_title_call(self, response: Any) -> None:
        """Fold the titling Converse response in, into fields of its own.

        THE TITLE IS WRITTEN BY A DIFFERENT MODEL, and a cheaper one: this stack answers on
        Sonnet and names conversations on Haiku. Added to the generation totals - which is
        what this did until it was audited against Bedrock's own usage blocks - those tokens
        get priced at the generation rate, so every conversation's first turn is billed for
        a call that never happened at that price. The call is still counted as a model call,
        because it was still billed as one.
        """
        self.model_calls += 1
        self._fold(response, "title_input_tokens", "title_output_tokens")

    def _fold(self, response: Any, input_field: str, output_field: str) -> None:
        """Add one response's reported tokens to the named pair, or nothing if it reported
        none. A response that arrives without its `usage` block still counted as a call."""
        reported = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(reported, dict):
            return
        for key, field in (("inputTokens", input_field), ("outputTokens", output_field)):
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

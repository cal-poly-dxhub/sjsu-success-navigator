"""What one turn actually consumed, counted from what Bedrock itself reports.

One message is not one model call, the two calls use different models, and prompt-cache
units are deliberately not counted.
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
        """The call counts even when the response reports no tokens."""
        self.model_calls += 1
        self._fold(response, "input_tokens", "output_tokens")

    def record_title_call(self, response: Any) -> None:
        """Its own fields: a cheaper model writes the title, and would be overpriced."""
        self.model_calls += 1
        self._fold(response, "title_input_tokens", "title_output_tokens")

    def _fold(self, response: Any, input_field: str, output_field: str) -> None:
        """A response with no `usage` block has already been counted as a call."""
        reported = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(reported, dict):
            return
        for key, field in (("inputTokens", input_field), ("outputTokens", output_field)):
            value = reported.get(key)
            if isinstance(value, int):
                setattr(self, field, getattr(self, field) + value)

    def record_guardrail(self, reported: Any) -> None:
        if not isinstance(reported, dict):
            return
        value = reported.get("contentPolicyUnits")
        if isinstance(value, int):
            self.guardrail_content_units += value

    def record_retrieval(self) -> None:
        self.retrievals += 1

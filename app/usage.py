"""What one turn actually consumed, counted from what Bedrock itself reports.

WHY THIS EXISTS. The cost panel used to price every figure from ONE average - 24 sample
questions in config.yaml's `cost_model.measured` - including the number it put in front of
the student as "this conversation". That number was never this conversation: it was the
sample's mean multiplied by a message count. The panel's left half now prices the
conversation actually in front of the reader, and that needs the tokens this turn really
billed rather than the tokens an average turn bills.

The hard part is not the arithmetic, it is that ONE STUDENT MESSAGE IS NOT ONE MODEL CALL.
The Converse loop runs until the model ends its turn, so a message that triggers a second
search bills two invocations, and the second one resends everything before it - system
prompt, history, and every retrieved passage already in the transcript. A conversation with
a title also paid for the small titling call (app/titles.py). None of that is guessable
from outside the loop, so it is counted inside it and reported.

Everything here is READ OFF RESPONSES THE LOOP ALREADY RECEIVES. Nothing extra is asked of
Bedrock, no request changes shape, and no branch reads these numbers back - the tally is
carried out to the wire and nowhere else.

NOT COUNTED, deliberately: prompt-cache reads and writes. Nothing in this stack enables
prompt caching, and the panel's rate table has no cache rates to price them with, so a
field for them would be a zero that looks like a measurement. If caching is ever turned on,
this is the file that has to learn about it, and the symptom of forgetting would be input
tokens that read low.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TurnUsage(BaseModel):
    """The billable units one /chat request consumed, on its way to the browser.

    A wire shape with three recorders on it. The recorders live here rather than in the
    orchestrator because they are all defensive in the same way: a response that arrives
    without its `usage` block still counts as a call, since the call was still billed.

    `protected_namespaces` is emptied for `model_calls`. Pydantic reserves the `model_`
    prefix for its own methods and warns on any field that uses it; the field is named for
    the thing it counts, and the alternative would be renaming a wire key to avoid a
    warning about a collision that does not exist.
    """

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    # Converse invocations in this turn, including the titling call on a new conversation.
    # This is the number the sample average cannot supply for a particular conversation.
    model_calls: int = Field(default=0, alias="modelCalls")
    # Summed Converse inputTokens. Dominated by the retrieved passages and the replayed
    # history, NOT by what the student typed.
    input_tokens: int = Field(default=0, alias="inputTokens")
    output_tokens: int = Field(default=0, alias="outputTokens")
    # ApplyGuardrail content-filter text units. One screen per message, over the bare
    # query; a unit covers 1,000 characters. There is no PII policy on this stack's
    # guardrail (PROMPT_ATTACK only), so this is the whole guardrail line.
    guardrail_content_units: int = Field(default=0, alias="guardrailContentUnits")
    # KB Retrieve calls: the primed first search plus any the model made itself. Each one
    # embeds the query text and queries the vector index.
    retrievals: int = Field(default=0, alias="retrievals")

    def record_model_call(self, response: Any) -> None:
        """Fold one Converse response's token usage in. A response with no `usage` block
        still counts as a call, because the invocation was still billed."""
        self.model_calls += 1
        reported = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(reported, dict):
            return
        for key, field in (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens")):
            value = reported.get(key)
            if isinstance(value, int):
                setattr(self, field, getattr(self, field) + value)

    def record_guardrail(self, reported: Any) -> None:
        """Fold one ApplyGuardrail response's `usage` block in.

        Only `contentPolicyUnits` is read, and that is not laziness about the other policy
        counters: guardrails bill PER POLICY, this stack configures exactly one policy, and
        the panel carries exactly one guardrail rate. Summing policies that are not
        configured into a number priced as content units would invent spend.
        """
        if not isinstance(reported, dict):
            return
        value = reported.get("contentPolicyUnits")
        if isinstance(value, int):
            self.guardrail_content_units += value

    def record_retrieval(self) -> None:
        self.retrievals += 1

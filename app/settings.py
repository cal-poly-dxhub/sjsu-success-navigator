"""Runtime settings for the chat Lambda, read from the environment the stack sets.

Camp's backend/config.py reshaped for Lambda, and the reshaping is the point:

  - NO pydantic-settings and no .env file. Every value arrives as an environment
    variable set by infra/infra_stack.py, so config.yaml is the single source of truth
    and there is no second place a value can come from.
  - NO DEFAULTS for the wiring that identifies AWS resources. Camp defaulted
    bedrock_kb_id to a literal knowledge-base id and the model id to a literal model
    string; a misconfigured deploy therefore ran happily against whatever that id
    pointed at. Here a missing one raises at import, so the function fails on its first
    invocation with a named variable instead of a 502 on a student's question.
  - Behavioural knobs (top-k, thresholds, caps) DO carry defaults, matching camp's
    values exactly. They are tuning, not identity: a missing one is not ambiguous.

Read once at module import (cold start), not per request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class SettingsError(RuntimeError):
    """A required environment variable is missing or unusable. Raised at import."""


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SettingsError(
            f"{name} is not set. The CDK stack sets it from config.yaml; an unset value "
            "means the function was deployed outside the stack or the stack's environment "
            "wiring was changed without updating app/settings.py."
        )
    return value


def _int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer (got {raw!r}).") from exc


def _float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number (got {raw!r}).") from exc


@dataclass(frozen=True)
class Settings:
    # Identity: no defaults (see the module docstring).
    knowledge_base_id: str
    generation_model_id: str
    bedrock_region: str
    input_guardrail_id: str
    input_guardrail_version: str
    # The conversation-history table. Identity, so no default: a chat Lambda that cannot
    # name its table would otherwise write a student's transcript into whatever a typo
    # pointed at, or nowhere at all.
    chat_history_table_name: str

    # Behaviour: camp's values as defaults.
    number_of_results: int = 8
    retrieve_min_score: float = 0.35
    generation_max_tokens: int = 1200
    generation_temperature: float = 0.2
    max_query_chars: int = 2000
    max_converse_iterations: int = 6
    # How many previous messages a turn reads back out of DynamoDB - the Limit on the one
    # descending query, and the window the model is shown. Messages, not turns: 12 is six
    # exchanges. It used to trim a client-supplied transcript; the transcript is the
    # server's now, so this bounds a read rather than distrusting a payload.
    max_history_messages: int = 12
    # Wall-clock budget for the whole Converse loop. Defaults to 22 to match config.yaml;
    # the handler narrows it further using Lambda's own remaining time.
    converse_deadline_seconds: int = 22

    # The card contract (config.yaml `cards`). Each value is read by BOTH the parser in
    # cards.py and the prompt builder in prompts.py, which is the whole point of it being one
    # value: the cap the model is told is the cap the server enforces. Defaults match
    # config.yaml, where each number is explained - the length caps are guards against a
    # runaway response, set far above the length the prompt steers toward, so a truncation
    # is a bug worth a WARNING rather than a daily event.
    card_max_cards: int = 4
    card_max_retrieval_results: int = 6
    card_title_max_chars: int = 90
    card_desc_max_chars: int = 600
    card_followup_max_chars: int = 120


def load_settings() -> Settings:
    """Build Settings from the environment. Raises SettingsError on missing identity."""
    return Settings(
        knowledge_base_id=_required("KNOWLEDGE_BASE_ID"),
        generation_model_id=_required("GENERATION_MODEL_ID"),
        # Lambda auto-sets AWS_REGION and it is reserved, so the stack passes the region
        # under its own key (see the chat Lambda's environment block).
        bedrock_region=_required("BEDROCK_REGION"),
        input_guardrail_id=_required("INPUT_GUARDRAIL_ID"),
        input_guardrail_version=_required("INPUT_GUARDRAIL_VERSION"),
        chat_history_table_name=_required("CHAT_HISTORY_TABLE_NAME"),
        number_of_results=_int("NUMBER_OF_RESULTS", 8),
        retrieve_min_score=_float("RETRIEVE_MIN_SCORE", 0.35),
        generation_max_tokens=_int("GENERATION_MAX_TOKENS", 1200),
        generation_temperature=_float("GENERATION_TEMPERATURE", 0.2),
        max_query_chars=_int("MAX_QUERY_CHARS", 2000),
        max_converse_iterations=_int("MAX_CONVERSE_ITERATIONS", 6),
        max_history_messages=_int("MAX_HISTORY_MESSAGES", 12),
        converse_deadline_seconds=_int("CONVERSE_DEADLINE_SECONDS", 22),
        card_max_cards=_int("CARD_MAX_CARDS", 4),
        card_max_retrieval_results=_int("CARD_MAX_RETRIEVAL_RESULTS", 6),
        card_title_max_chars=_int("CARD_TITLE_MAX_CHARS", 90),
        card_desc_max_chars=_int("CARD_DESC_MAX_CHARS", 600),
        card_followup_max_chars=_int("CARD_FOLLOWUP_MAX_CHARS", 120),
    )

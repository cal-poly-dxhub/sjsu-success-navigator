"""Runtime settings for the chat Lambda, read once at import from the stack's environment.

Identity values raise when missing; behavioural knobs carry defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class SettingsError(RuntimeError):
    """A required environment variable is missing or unusable."""


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


def _id_set(name: str) -> frozenset[str]:
    raw = os.environ.get(name) or ""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


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
    knowledge_base_id: str
    generation_model_id: str
    title_model_id: str
    bedrock_region: str
    input_guardrail_id: str
    input_guardrail_version: str
    chat_history_table_name: str

    number_of_results: int = 8
    retrieve_min_score: float = 0.35
    generation_max_tokens: int = 1200
    generation_temperature: float = 0.2
    max_query_chars: int = 2000
    max_converse_iterations: int = 6
    max_history_messages: int = 12
    max_conversations_listed: int = 40
    max_conversation_messages: int = 60
    converse_deadline_seconds: int = 22
    title_max_chars: int = 80
    title_deadline_seconds: int = 3

    # 0 disables the cap.
    daily_message_limit: int = 0
    rate_limit_exempt_client_ids: frozenset[str] = frozenset()

    card_max_cards: int = 4
    card_max_retrieval_results: int = 6
    card_title_max_chars: int = 90
    card_desc_max_chars: int = 600
    card_followup_max_chars: int = 120

    # Empty disables the escalation path.
    escalation_recipient: str = ""
    escalation_subject: str = "A student would like to talk with someone"
    escalation_max_chars: int = 1200


def load_settings() -> Settings:
    return Settings(
        knowledge_base_id=_required("KNOWLEDGE_BASE_ID"),
        generation_model_id=_required("GENERATION_MODEL_ID"),
        title_model_id=_required("TITLE_MODEL_ID"),
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
        max_conversations_listed=_int("MAX_CONVERSATIONS_LISTED", 40),
        max_conversation_messages=_int("MAX_CONVERSATION_MESSAGES", 60),
        converse_deadline_seconds=_int("CONVERSE_DEADLINE_SECONDS", 22),
        title_max_chars=_int("TITLE_MAX_CHARS", 80),
        title_deadline_seconds=_int("TITLE_DEADLINE_SECONDS", 3),
        daily_message_limit=_int("DAILY_MESSAGE_LIMIT", 0),
        rate_limit_exempt_client_ids=_id_set("RATE_LIMIT_EXEMPT_CLIENT_IDS"),
        card_max_cards=_int("CARD_MAX_CARDS", 4),
        card_max_retrieval_results=_int("CARD_MAX_RETRIEVAL_RESULTS", 6),
        card_title_max_chars=_int("CARD_TITLE_MAX_CHARS", 90),
        card_desc_max_chars=_int("CARD_DESC_MAX_CHARS", 600),
        card_followup_max_chars=_int("CARD_FOLLOWUP_MAX_CHARS", 120),
        escalation_recipient=(os.environ.get("ESCALATION_RECIPIENT") or "").strip(),
        escalation_subject=(
            (os.environ.get("ESCALATION_SUBJECT") or "").strip()
            or "A student would like to talk with someone"
        ),
        escalation_max_chars=_int("ESCALATION_MAX_CHARS", 1200),
    )

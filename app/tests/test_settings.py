"""Settings: identity has no defaults, behaviour does."""

import pytest

from settings import SettingsError, load_settings

_IDENTITY = {
    "KNOWLEDGE_BASE_ID": "KB123",
    "GENERATION_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
    "BEDROCK_REGION": "us-west-2",
    "INPUT_GUARDRAIL_ID": "gr-1",
    "INPUT_GUARDRAIL_VERSION": "3",
# The history table is identity, not tuning.
    "CHAT_HISTORY_TABLE_NAME": "chat-history-test",
}


def _set_identity(monkeypatch, **overrides):
    for key, value in {**_IDENTITY, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


@pytest.mark.parametrize("missing", sorted(_IDENTITY))
def test_missing_identity_raises_and_names_the_variable(monkeypatch, missing):
    """A missing identity value raises at import, naming the variable."""
    _set_identity(monkeypatch, **{missing: None})
    with pytest.raises(SettingsError, match=missing):
        load_settings()


def test_behavioural_knobs_default_to_camps_values(monkeypatch):
    """Tuning values are not identity: a missing one is unambiguous, so it defaults."""
    _set_identity(monkeypatch)
    for key in (
        "NUMBER_OF_RESULTS",
        "RETRIEVE_MIN_SCORE",
        "GENERATION_MAX_TOKENS",
        "GENERATION_TEMPERATURE",
        "MAX_QUERY_CHARS",
        "MAX_CONVERSE_ITERATIONS",
        "MAX_HISTORY_MESSAGES",
        "MAX_CONVERSATIONS_LISTED",
        "MAX_CONVERSATION_MESSAGES",
        "CONVERSE_DEADLINE_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings()
    assert settings.number_of_results == 8
    assert settings.retrieve_min_score == 0.35
    assert settings.generation_max_tokens == 1200
    assert settings.generation_temperature == 0.2
    assert settings.max_query_chars == 2000
    assert settings.max_converse_iterations == 6
    assert settings.max_history_messages == 12
    # The read caps are not the model's window: they bound a browser read, not a token bill.
    assert settings.max_conversations_listed == 40
    assert settings.max_conversation_messages == 60
    assert settings.converse_deadline_seconds == 22


def test_env_values_override_the_defaults(monkeypatch):
    _set_identity(monkeypatch)
    monkeypatch.setenv("MAX_CONVERSE_ITERATIONS", "3")
    monkeypatch.setenv("CONVERSE_DEADLINE_SECONDS", "10")
    monkeypatch.setenv("RETRIEVE_MIN_SCORE", "0.5")

    settings = load_settings()
    assert settings.max_converse_iterations == 3
    assert settings.converse_deadline_seconds == 10
    assert settings.retrieve_min_score == 0.5


def test_a_non_numeric_knob_raises_rather_than_silently_defaulting(monkeypatch):
    _set_identity(monkeypatch)
    monkeypatch.setenv("MAX_CONVERSE_ITERATIONS", "six")
    with pytest.raises(SettingsError, match="MAX_CONVERSE_ITERATIONS"):
        load_settings()


def test_the_daily_message_limit_defaults_to_disabled(monkeypatch):
    """The one behavioural default that is not config.yaml's value, and deliberately so."""
    _set_identity(monkeypatch)
    monkeypatch.delenv("DAILY_MESSAGE_LIMIT", raising=False)

    assert load_settings().daily_message_limit == 0


def test_the_daily_message_limit_is_read_from_the_environment(monkeypatch):
    _set_identity(monkeypatch)
    monkeypatch.setenv("DAILY_MESSAGE_LIMIT", "60")

    assert load_settings().daily_message_limit == 60


def test_the_exemption_list_is_empty_when_unset(monkeypatch):
    """Empty is the safe direction: a missing value exempts nobody rather than everybody."""
    _set_identity(monkeypatch)
    monkeypatch.delenv("RATE_LIMIT_EXEMPT_CLIENT_IDS", raising=False)

    assert load_settings().rate_limit_exempt_client_ids == frozenset()


def test_the_exemption_list_takes_more_than_one_client(monkeypatch):
    """Plural on purpose, so a second machine client is a config edit rather than a code one."""
    _set_identity(monkeypatch)
    monkeypatch.setenv("RATE_LIMIT_EXEMPT_CLIENT_IDS", " eval-client , , second-client ")

    assert load_settings().rate_limit_exempt_client_ids == frozenset(
        {"eval-client", "second-client"}
    )


def test_the_escalation_recipient_defaults_to_empty(monkeypatch):
    """Empty is the gate, the same shape as the daily limit's zero."""
    _set_identity(monkeypatch)
    for key in ("ESCALATION_RECIPIENT", "ESCALATION_SUBJECT", "ESCALATION_MAX_CHARS"):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings()

    assert settings.escalation_recipient == ""
    assert settings.escalation_max_chars == 1200


def test_the_escalation_wiring_is_read_from_the_environment(monkeypatch):
    _set_identity(monkeypatch)
    monkeypatch.setenv("ESCALATION_RECIPIENT", " sjsucares@sjsu.edu ")
    monkeypatch.setenv("ESCALATION_SUBJECT", "A student would like to talk with someone")
    monkeypatch.setenv("ESCALATION_MAX_CHARS", "900")

    settings = load_settings()

    assert settings.escalation_recipient == "sjsucares@sjsu.edu"
    assert settings.escalation_subject == "A student would like to talk with someone"
    assert settings.escalation_max_chars == 900


def test_a_blank_subject_falls_back_rather_than_shipping_an_empty_line(monkeypatch):
    """Otherwise a deploy could put an empty subject line in front of a staff mailbox."""
    _set_identity(monkeypatch)
    monkeypatch.setenv("ESCALATION_RECIPIENT", "sjsucares@sjsu.edu")
    monkeypatch.setenv("ESCALATION_SUBJECT", "   ")

    assert load_settings().escalation_subject.strip() != ""

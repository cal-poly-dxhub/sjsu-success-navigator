"""Settings: identity has no defaults, behaviour does."""

import pytest

from settings import SettingsError, load_settings

_IDENTITY = {
    "KNOWLEDGE_BASE_ID": "KB123",
    "GENERATION_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
    "BEDROCK_REGION": "us-west-2",
    "INPUT_GUARDRAIL_ID": "gr-1",
    "INPUT_GUARDRAIL_VERSION": "3",
    # The history table. Identity, not tuning: a function that cannot name its table would
    # otherwise write a student's transcript into whatever a typo pointed at.
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
    """Camp defaulted the KB id and the model id to literals, so a misconfigured deploy
    ran happily against whatever those pointed at. Here it fails at import, by name."""
    _set_identity(monkeypatch, **{missing: None})
    with pytest.raises(SettingsError, match=missing):
        load_settings()


def test_behavioural_knobs_default_to_camps_values(monkeypatch):
    """Tuning values are not identity: a missing one is unambiguous, so it defaults
    rather than failing the invocation."""
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
    # The read endpoints' caps, which are NOT the model's window: they bound a browser read
    # of stored items, so they are larger and cost one query rather than tokens per turn.
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
    """THE ONE BEHAVIOURAL DEFAULT THAT IS NOT config.yaml's VALUE, and deliberately so.

    The stack omits DAILY_MESSAGE_LIMIT entirely when the cap is off (the cost panel's gate
    shape), so an unset variable HAS to mean disabled. A default of 60 here would mean a
    wiring mistake invented a limit nobody configured, and students would start being refused
    with nothing in config.yaml to explain it.
    """
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
    """Plural on purpose, so a second machine client is a config edit rather than a code
    change. Whitespace and empty entries are dropped rather than becoming ids that no token
    can match but that read as if they might."""
    _set_identity(monkeypatch)
    monkeypatch.setenv("RATE_LIMIT_EXEMPT_CLIENT_IDS", " eval-client , , second-client ")

    assert load_settings().rate_limit_exempt_client_ids == frozenset(
        {"eval-client", "second-client"}
    )

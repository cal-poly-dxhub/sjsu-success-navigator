"""Settings: identity has no defaults, behaviour does."""

import pytest

from settings import SettingsError, load_settings

_IDENTITY = {
    "KNOWLEDGE_BASE_ID": "KB123",
    "GENERATION_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
    "BEDROCK_REGION": "us-west-2",
    "INPUT_GUARDRAIL_ID": "gr-1",
    "INPUT_GUARDRAIL_VERSION": "3",
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

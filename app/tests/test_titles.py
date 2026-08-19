"""Conversation titling: the model's reply is a suggestion, and this is what rejects it.

A rejection is never a repair, and titling can never delay or fail a turn; see
docs/chat-service.md, Conversation titling.
"""

import time

import pytest

import titles
from settings import Settings

_SETTINGS = Settings(
    knowledge_base_id="KB",
    generation_model_id="us.anthropic.claude-sonnet-4-6",
    title_model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    bedrock_region="us-west-2",
    input_guardrail_id="gr",
    input_guardrail_version="1",
    chat_history_table_name="chat-history-test",
    title_max_chars=80,
    title_deadline_seconds=3,
)


class _FakeBedrock:
    """A Converse stand-in. `reply` is the text the model returns; `raises` fails the call."""

    def __init__(self, reply="Financial aid appeal deadline", raises=None):
        self.reply = reply
        self.raises = raises
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return {"output": {"message": {"content": [{"text": self.reply}]}}}


@pytest.fixture
def bedrock(monkeypatch):
    fake = _FakeBedrock()
    monkeypatch.setattr(titles, "_bedrock_client", lambda settings: fake)
    return fake


def _generate(deadline=None):
    return titles.generate_title(
        question="how do I appeal my financial aid?",
        answer="The Financial Aid office takes appeals until the third week.",
        settings=_SETTINGS,
        deadline=time.monotonic() + 5 if deadline is None else deadline,
    )


# --- the usable_title contract -----------------------------------------------------------


def test_a_plain_short_reply_is_the_title(bedrock):
    assert _generate() == "Financial aid appeal deadline"


@pytest.mark.parametrize(
    "reply",
    [
        "Sure! Here's a title: Financial aid appeal",
        "Here is a good title for this conversation",
        "Title: Financial aid appeal",
        "Certainly, Financial aid appeal",
        "Of course. Financial aid appeal",
        "I'd suggest Financial aid appeal",
        "Okay, Financial aid appeal",
    ],
)
def test_a_preamble_is_a_failure_not_a_title(bedrock, reply):
    """THE FAILURE THIS FEATURE IS ACTUALLY ABOUT: a model that answers about the task."""
    bedrock.reply = reply
    assert _generate() is None


@pytest.mark.parametrize(
    "reply",
    ['"Financial aid appeal"', "'Financial aid appeal'", "`Financial aid appeal`",
     "“Financial aid appeal”"],
)
def test_a_quoted_reply_is_a_failure(bedrock, reply):
    """A quoted string is the model PRESENTING a title rather than writing one."""
    bedrock.reply = reply
    assert _generate() is None


def test_a_multi_line_reply_is_a_failure(bedrock):
    """A title and a commentary about the title. Only one of them belongs in a sidebar."""
    bedrock.reply = "Financial aid appeal\n\nThis names the student's question."
    assert _generate() is None


def test_a_reply_over_the_configured_cap_is_a_failure(bedrock):
    """A sentence, not a title. The cap is config, never a literal."""
    bedrock.reply = "A" * (_SETTINGS.title_max_chars + 1)
    assert _generate() is None
    assert titles.usable_title("A" * _SETTINGS.title_max_chars, _SETTINGS.title_max_chars)


def test_a_reply_carrying_markup_is_a_failure(bedrock):
    """The model has been trained around tag contracts. A leaked one is not a title."""
    bedrock.reply = "<card>Financial aid appeal</card>"
    assert _generate() is None


def test_an_empty_reply_is_a_failure(bedrock):
    bedrock.reply = "   "
    assert _generate() is None


def test_a_title_carrying_a_dash_is_normalised_rather_than_rejected():
    """The one repair this does make, and it is not a repair of the model's judgement."""
    title = titles.usable_title("Financial aid — appeal deadline", 80)
    assert "—" not in title and "–" not in title
    assert title == "Financial aid, appeal deadline"


def test_no_title_this_module_can_produce_carries_an_em_or_en_dash():
    """The dash constraint as one assertion over the whole surface, prompt included."""
    assert "—" not in titles.TITLE_SYSTEM_PROMPT
    assert "–" not in titles.TITLE_SYSTEM_PROMPT


# --- never delaying or failing a turn ----------------------------------------------------


def test_a_bedrock_failure_returns_none_rather_than_raising(bedrock):
    """A forced titling failure still leaves a good answer: the reply is already written."""
    bedrock.raises = RuntimeError("ThrottlingException")
    assert _generate() is None


def test_nothing_is_called_once_the_deadline_has_passed(bedrock):
    """The point of a deadline is that no network call STARTS which cannot finish inside it."""
    assert _generate(deadline=time.monotonic() - 1) is None
    assert bedrock.calls == [], "a call was started past the deadline"


def test_the_call_is_small_and_deterministic(bedrock):
    """A four-word output does not need a large budget, and temperature 0 keeps it stable."""
    _generate()
    config = bedrock.calls[0]["inferenceConfig"]
    assert config["maxTokens"] <= 64
    assert config["temperature"] == 0
    assert bedrock.calls[0]["modelId"] == _SETTINGS.title_model_id


def test_the_model_is_shown_the_answer_as_well_as_the_question(bedrock):
    """Why this runs AFTER the exchange: a title that has seen the reply names what the
    conversation turned out to be about."""
    _generate()
    sent = bedrock.calls[0]["messages"][0]["content"][0]["text"]
    assert "how do I appeal my financial aid?" in sent
    assert "The Financial Aid office takes appeals until the third week." in sent
    assert len(bedrock.calls[0]["messages"]) == 1, (
        "the finished exchange is one message, not a transcript the model might reply into"
    )


def test_a_turn_whose_reply_failed_is_still_titled_from_the_question(bedrock):
    """The answer is a helpful signal, not a required one."""
    assert (
        titles.generate_title(
            question="how do I appeal my financial aid?",
            answer="",
            settings=_SETTINGS,
            deadline=time.monotonic() + 5,
        )
        == "Financial aid appeal deadline"
    )

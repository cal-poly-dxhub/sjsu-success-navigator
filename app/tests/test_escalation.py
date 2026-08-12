"""The escalate-to-human draft: what the server assembles, and every reason it refuses to.

The assembly itself is the least interesting test here. What matters is that this module
is the only thing that can address a message and the only thing that decides whether an
offer exists at all, so each refusal below is a way the feature is asked to do something
the design says it must not:

  - a tag in a deployment with no recipient  -> no offer (the prompt never taught the tag)
  - prose past the cap                       -> no offer, and NEVER a shortened one
  - an empty block                           -> no offer

The cap case is the one that must not regress into the card contract's habit. Everything
else in this app truncates at a word boundary and logs; a truncated email is a message the
student can send before noticing what went missing.
"""

import logging

import pytest

from escalation import PROVENANCE_LINE, build_email_draft, escalation_available
from settings import Settings

_SETTINGS = Settings(
    knowledge_base_id="KB123",
    generation_model_id="us.anthropic.claude-sonnet-4-6",
    title_model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    bedrock_region="us-west-2",
    input_guardrail_id="gr-1",
    input_guardrail_version="3",
    chat_history_table_name="chat-history-test",
    escalation_recipient="sjsucares@sjsu.edu",
    escalation_subject="A student would like to talk with someone",
    escalation_max_chars=1200,
)

_PROSE = (
    "Hi, I have been trying to sort out a hold on my registration since last week and "
    "the answers I can find online do not cover my situation. Could someone help me "
    "work out who to talk to?"
)


def _settings(**overrides):
    return Settings(**{**_SETTINGS.__dict__, **overrides})


def test_the_draft_is_the_prose_and_the_provenance_line():
    draft = build_email_draft(_PROSE, settings=_SETTINGS)

    assert draft is not None
    assert draft.to == "sjsucares@sjsu.edu"
    assert draft.subject == "A student would like to talk with someone"
    # The whole body, in order, and nothing else in it. Spelled as an equality rather than
    # two `in` checks: this string IS what the mail client is handed and what the student
    # reads on screen before pressing send, so a third line appearing here is a change to
    # what a member of staff receives.
    assert draft.body == f"{_PROSE}\n\n{PROVENANCE_LINE}"


def test_the_draft_names_no_email_addresses_at_all():
    """It used to end with "you can reach me at <the student's verified email>", read from
    the JWT claim. The message leaves from the student's own mail client and their own
    account, so that line told staff what the header already told them - and removing it
    removed every reason this path needed to know who the student is."""
    draft = build_email_draft(_PROSE, settings=_SETTINGS)

    assert "@" not in draft.body
    assert "reach me at" not in draft.body


def test_the_draft_carries_no_conversation_id_transcript_or_assessment():
    """The message contains what the student can see, and nothing about the machinery.

    A conversation id or a transcript in here would be a thing staff received that the
    preview did not show and the student never agreed to send.
    """
    draft = build_email_draft(_PROSE, settings=_SETTINGS)

    assert set(draft.model_dump()) == {"to", "subject", "body"}


def test_no_tag_is_no_offer_and_no_noise(caplog):
    with caplog.at_level(logging.INFO):
        assert build_email_draft(None, settings=_SETTINGS) is None
    assert caplog.records == [], "the ordinary turn must not log anything"


def test_a_deployment_with_no_recipient_makes_no_offer(caplog):
    """The absence of the address is the gate, and it holds even if a tag arrives anyway.

    The prompt does not mention the tag in this deployment (app/prompts.py), so a block
    here is the model reaching for a contract nobody gave it - which is worth a WARNING and
    is still not an offer.
    """
    settings = _settings(escalation_recipient="")

    assert not escalation_available(settings)
    with caplog.at_level(logging.WARNING):
        assert build_email_draft(_PROSE, settings=settings) is None
    assert "no escalation.recipient" in caplog.text


def test_over_the_cap_the_offer_is_dropped_and_never_truncated(caplog):
    long_prose = "word " * 400  # 1999 characters once stripped, past the 1200 guard

    with caplog.at_level(logging.WARNING):
        draft = build_email_draft(long_prose, settings=_SETTINGS)

    assert draft is None, "an over-cap draft is dropped, not shortened"
    assert "dropped" in caplog.text
    assert "1999 chars, cap 1200" in caplog.text


def test_at_the_cap_exactly_the_offer_stands():
    """The cap is a ceiling on the model's prose, not on the assembled body.

    The two lines the server adds are not the model's budget to spend, and a boundary that
    counted them would move whenever that copy was edited.
    """
    prose = "x" * _SETTINGS.escalation_max_chars

    draft = build_email_draft(prose, settings=_SETTINGS)

    assert draft is not None
    assert len(draft.body) > _SETTINGS.escalation_max_chars


def test_an_empty_block_makes_no_offer(caplog):
    with caplog.at_level(logging.WARNING):
        assert build_email_draft("   ", settings=_SETTINGS) is None
    assert "empty" in caplog.text


@pytest.mark.parametrize("recipient,expected", [("", False), ("  ", False), ("a@b.edu", True)])
def test_availability_is_the_presence_of_an_address(recipient, expected):
    """One value gates the prompt section and the assembler, so they cannot disagree about
    whether this deployment has the feature."""
    assert escalation_available(_settings(escalation_recipient=recipient)) is expected

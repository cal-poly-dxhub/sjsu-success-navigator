"""One small model call that names a conversation, and the rules for distrusting it.

WHY THIS IS NOT PART OF THE LOOP. The conversation title is a label in a sidebar. It is not
part of the answer, so it does not belong in the answer's contract: no new output tag, no
new sentence in the system prompt, nothing the card parser has to learn. The tag contract
and the prompt are settled, and a label has no business reopening them. This is a separate,
tiny Converse call with its own system prompt, made once in a conversation's life.

WHEN. After the first exchange is written, so the model can see the student's question AND
the assistant's answer. A title drawn from the question alone would name what was asked;
one that has seen the reply can name what the conversation turned out to be about.

IT MUST NEVER DELAY OR FAIL A TURN, and that is the whole design constraint here:

  - its own short deadline (chat.title_deadline_seconds), checked against Lambda's real
    remaining time by the caller. Past it, nothing is attempted.
  - no retries. A retry inside a two-second budget is a way to spend the budget without
    an answer.
  - every failure is swallowed and logged. The student already has their reply by the
    time this runs; a titling outage cannot be allowed to take it away.

AND THE OUTPUT IS NOT TRUSTED. A titling model happily answers "Sure! Here's a title:" or
returns a quoted string, and both are worse than the fallback - a student reading "Sure!
Here's a title" in their sidebar has been shown the model's throat-clearing as if it were
their own words. usable_title below rejects those shapes outright rather than trying to
repair them, and the fallback is the first-message truncation that is ALREADY on the header
by then. That ordering is what makes a rejection free: there is nothing to write, so the
conversation keeps the name it already had.
"""

from __future__ import annotations

import logging
import re
import time

import boto3
from botocore.config import Config

from cards import normalise_dashes
from settings import Settings
from usage import TurnUsage

logger = logging.getLogger(__name__)

# The whole instruction. Deliberately short: a long prompt for a four-word output costs
# tokens and latency inside a budget measured in seconds, and every extra clause is another
# thing the model can decide to comment on.
#
# The dash ban is the same one the display path enforces (cards.normalise_dashes), stated
# here so the model does not write one in the first place. This file, like app/prompts.py,
# is kept free of em and en dashes so an example never teaches the habit the server edits.
TITLE_SYSTEM_PROMPT = (
    "You name conversations for a university student services chat sidebar.\n\n"
    "Reply with the title and nothing else. No preamble, no quotation marks, no trailing "
    "punctuation, no explanation.\n\n"
    "A title is three to six words naming the student's topic, in their own vocabulary. "
    "Write it in sentence case. Never use an em dash or an en dash.\n\n"
    "Examples of the whole reply:\n"
    "Financial aid appeal deadline\n"
    "Finding a math tutor\n"
    "Counseling appointment options"
)

# Enough for a six-word title and nothing like enough for a paragraph. A model that ignores
# the instruction runs into this rather than billing for an essay, and the truncated result
# fails usable_title on the way out.
_MAX_TOKENS = 32

# Openings that mean the model answered ABOUT the task instead of doing it.
_PREAMBLE_RE = re.compile(
    r"^(sure|here|okay|ok|certainly|absolutely|of course|title|got it|understood|i\b)",
    re.IGNORECASE,
)

# Any quote or backtick anywhere. A quoted title is the model presenting a title rather than
# writing one, and stripping the quotes would be repairing output that has already shown it
# is not following the instruction.
_QUOTE_CHARS = "\"'`‘’“”"

_WHITESPACE_RE = re.compile(r"\s+")

_BEDROCK_CLIENT = None


def _bedrock_client(settings: Settings):
    """The client for the titling call, built once per container.

    ITS OWN TIMEOUTS, and they are the short ones: read_timeout is the title budget itself,
    so a stalled socket cannot outlive the deadline this function is supposed to respect.
    NO RETRIES either - the loop's client retries three times because an answer is worth
    waiting for, and a label is not.
    """
    global _BEDROCK_CLIENT
    if _BEDROCK_CLIENT is None:
        _BEDROCK_CLIENT = boto3.client(
            "bedrock-runtime",
            region_name=settings.bedrock_region,
            config=Config(
                retries={"max_attempts": 1, "mode": "standard"},
                read_timeout=max(1, int(settings.title_deadline_seconds)),
                connect_timeout=2,
            ),
        )
    return _BEDROCK_CLIENT


def usable_title(raw: str | None, cap: int) -> str | None:
    """The model's reply as a title, or None if it is not one.

    A REJECTION IS NOT A FAILURE TO REPAIR. Every rule below could be written as a fix -
    strip the quotes, cut off the preamble, truncate to the cap - and every one of those
    fixes would hand a student a title assembled out of a reply that ignored the
    instruction. The fallback is a truncation of their own first message, which is worse
    than a good model title and better than a salvaged bad one.

    The rules, and what each one is actually catching:

      empty              nothing to show.
      multi-line         a title and a commentary about the title.
      quoted or backtick the model presenting a title rather than writing one.
      preamble           "Sure! Here's a title:" and its relatives.
      ends with a colon  the same thing with the title itself missing.
      angle brackets     markup leaking out of a model that has seen a tag contract.
      over the cap       a sentence, not a title. The cap is config, never a literal.

    Dashes are normalised BEFORE the cap is measured, exactly as the card path does it, so
    the length checked is the length displayed.
    """
    title = _WHITESPACE_RE.sub(" ", normalise_dashes(raw or "")).strip()

    if not title:
        return None
    if "\n" in (raw or "").strip():
        logger.info("Rejecting a generated title: more than one line.")
        return None
    if any(char in title for char in _QUOTE_CHARS):
        logger.info("Rejecting a generated title: quoted or backticked.")
        return None
    if _PREAMBLE_RE.match(title):
        logger.info("Rejecting a generated title: it starts with a preamble.")
        return None
    if title.endswith(":"):
        logger.info("Rejecting a generated title: it ends with a colon.")
        return None
    if "<" in title or ">" in title:
        logger.info("Rejecting a generated title: it contains markup.")
        return None
    if len(title) > cap:
        logger.info(
            "Rejecting a generated title: %s chars against a cap of %s.", len(title), cap
        )
        return None

    return title


def generate_title(
    *,
    question: str,
    answer: str,
    settings: Settings,
    deadline: float,
    usage: TurnUsage | None = None,
) -> str | None:
    """A title for one exchange, or None. NEVER RAISES.

    `deadline` is a `time.monotonic()` timestamp, derived by the caller from the smaller of
    the configured budget and Lambda's real remaining time. Checked BEFORE the call for the
    same reason the Converse loop checks its own: the point of a deadline is that no network
    call starts which cannot finish inside it.

    `usage` is the turn's billable tally (app/usage.py). This call is small - 32 output
    tokens against an exchange - but it is a real Converse invocation on a real model, so
    leaving it out would make the first message of every conversation read cheaper than it
    was. It is counted whether or not the title turns out to be usable: a rejected title
    was billed exactly like an accepted one.

    The exchange is sent as one user message rather than as a two-turn transcript. The model
    is not continuing this conversation, it is looking at a finished one, and a transcript
    shape invites it to reply to the student instead of naming what they discussed.
    """
    if time.monotonic() >= deadline:
        logger.info("Skipping conversation titling: no time left in this invocation.")
        return None

    question = (question or "").strip()
    if not question:
        return None

    # The answer is a helpful signal, not a required one: a turn whose reply failed still
    # deserves a name, and the question alone is enough to write one.
    exchange = f"Student: {question}"
    answer = (answer or "").strip()
    if answer:
        exchange = f"{exchange}\n\nAssistant: {answer}"

    try:
        response = _bedrock_client(settings).converse(
            modelId=settings.title_model_id,
            system=[{"text": TITLE_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": exchange}]}],
            inferenceConfig={"maxTokens": _MAX_TOKENS, "temperature": 0},
        )
    except Exception:
        # Includes the read timeout, which is the ordinary shape of "this took too long".
        logger.warning(
            "Could not generate a conversation title; keeping the first-message title.",
            exc_info=True,
        )
        return None

    if usage is not None:
        usage.record_model_call(response)

    parts = [
        block["text"]
        for block in (response.get("output", {}).get("message", {}).get("content") or [])
        if "text" in block
    ]
    title = usable_title("\n".join(parts), settings.title_max_chars)
    if title is None:
        logger.info("A generated title was unusable; keeping the first-message title.")
    return title

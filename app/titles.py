"""One small model call that names a conversation, and the rules for distrusting it.

It can never delay or fail a turn, and an unusable reply is rejected, never repaired.
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

# Enough for a six-word title and nothing like enough for a paragraph.
_MAX_TOKENS = 32

# Openings that mean the model answered about the task instead of doing it.
_PREAMBLE_RE = re.compile(
    r"^(sure|here|okay|ok|certainly|absolutely|of course|title|got it|understood|i\b)",
    re.IGNORECASE,
)

# Any quote or backtick: the model presenting a title rather than writing one.
_QUOTE_CHARS = "\"'`‘’“”"

_WHITESPACE_RE = re.compile(r"\s+")

_BEDROCK_CLIENT = None


def _bedrock_client(settings: Settings):
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
    """The model's reply as a title, or None. Rejected, never repaired."""
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
    """A title for one exchange, or None. Never raises, never delays the turn."""
    if time.monotonic() >= deadline:
        logger.info("Skipping conversation titling: no time left in this invocation.")
        return None

    question = (question or "").strip()
    if not question:
        return None

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
        # Includes the read timeout, the ordinary shape of taking too long.
        logger.warning(
            "Could not generate a conversation title; keeping the first-message title.",
            exc_info=True,
        )
        return None

    if usage is not None:
        # Its own fields: a different model wrote this and is billed at another rate.
        usage.record_title_call(response)

    parts = [
        block["text"]
        for block in (response.get("output", {}).get("message", {}).get("content") or [])
        if "text" in block
    ]
    title = usable_title("\n".join(parts), settings.title_max_chars)
    if title is None:
        logger.info("A generated title was unusable; keeping the first-message title.")
    return title

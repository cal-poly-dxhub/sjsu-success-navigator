"""The escalate-to-human draft: the model writes the words, the server addresses the mail.

Nothing sends from here and an over-cap draft is dropped rather than trimmed; see
docs/chat-service.md, Escalation.
"""

from __future__ import annotations

import logging

from models import EmailDraft
from settings import Settings

logger = logging.getLogger(__name__)

# The one line the server adds to every draft: staff should know a machine helped write it.
PROVENANCE_LINE = "I wrote this draft with the help of the SJSU Student Success Navigator."

# Shown when a turn offers a draft and the block left no prose to introduce it.
ESCALATION_FALLBACK_TEXT = (
    "This one is worth putting in front of a person. I've started a message you can send."
)


def escalation_available(settings: Settings) -> bool:
    """Is there anywhere to send a draft? The recipient's presence is the whole gate."""
    return bool(_recipient(settings))


def _recipient(settings: Settings) -> str:
    """Return the configured address, stripped. Empty means the feature is off."""
    return (settings.escalation_recipient or "").strip()


def build_email_draft(prose: str | None, *, settings: Settings) -> EmailDraft | None:
    """Build one block as a finished message, or None with a dropped-offer reason logged."""
    if prose is None:
        return None

    if not escalation_available(settings):
        logger.warning(
            "The model emitted an escalate_to_human block, but this deployment has no "
            "escalation destination configured (config.yaml escalation.contact, which the "
            "stack resolves against data/contacts.csv and stamps into ESCALATION_RECIPIENT); "
            "dropping the offer."
        )
        return None

    body_prose = prose.strip()
    if not body_prose:
        logger.warning("An empty escalate_to_human block; dropping the offer.")
        return None

    if len(body_prose) > settings.escalation_max_chars:
        logger.warning(
            "An escalate_to_human block ran past its guard cap (%s chars, cap %s) and the "
            "offer was dropped. The cap sits far above the length the prompt steers "
            "toward, so hitting it means the prompt or the model is broken.",
            len(body_prose),
            settings.escalation_max_chars,
        )
        return None

    return EmailDraft(
        to=_recipient(settings),
        subject=settings.escalation_subject,
        body=f"{body_prose}\n\n{PROVENANCE_LINE}",
    )

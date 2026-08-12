"""The escalate-to-human draft: the model writes the words, the server addresses the mail.

NOTHING SENDS FROM HERE, and that is the design rather than a limitation. There is no SES
client in this repo, no verified sending identity, and no route that posts a message
anywhere. What this module produces is a DRAFT - three strings - which the browser hands to
the student's own mail client (frontend/src/components/EscalationDraft.tsx). The student
presses send, from their own address, so a staff reply lands in the mailbox they actually
read instead of in a no-reply nobody owns.

THE MODEL CANNOT ADDRESS AN EMAIL, in exactly the way it cannot author a card's URL. The
tag it writes is `<escalate_to_human>` with PROSE INSIDE IT and nothing else: no id, no
attributes, no recipient. So a model-chosen destination is not a thing that is validated
and rejected here - it is unrepresentable, because there is nowhere in the shape to put
one. The address and the subject come from deploy config (config.yaml `escalation`). The
model contributes one thing: the body's prose.

THE DRAFT NAMES NO ADDRESSES AT ALL, and that is a decision rather than an omission. It
used to end with "you can reach me at <the student's verified email>", read from the JWT
claim - which was a line telling a member of staff something the message header already
tells them, since it leaves from the student's own mail client and their own account. So
the line is gone, and with it every reason this path needed to know who the student is. A
draft is now a function of the model's prose and config, and nothing else.

ASSEMBLED ONCE, AT PARSE TIME, AND STORED WITH THE TURN. The draft is built here while the
reply is being parsed, goes out on the response, and is written to DynamoDB beside the
turn's cards - so reopening the conversation re-renders the same bytes rather than
re-deriving them from a live config that may have moved. It also makes the preview honest:
what the student reads on screen is the string the mail client is handed.

THE CAP DROPS THE OFFER RATHER THAN CUTTING IT. Every other length cap in this app
truncates at a word boundary and logs (app/cards.py); this one refuses. A card that ends in
an ellipsis is a visible symptom in front of the person who can judge it, but a truncated
email is a half-sentence the student may send to a stranger before noticing what went
missing. Over the cap there is no offer, and the WARNING says so.
"""

from __future__ import annotations

import logging

from models import EmailDraft
from settings import Settings

logger = logging.getLogger(__name__)

# The one line the SERVER adds to every draft, and it is not decoration: staff open a
# message written with a machine's help, so it says so. Fixed here rather than in config for
# the same reason SAFETY_FALLBACK_TEXT is fixed in app/safety.py - it is the server's own
# voice, and there is exactly one of it.
PROVENANCE_LINE = "I wrote this draft with the help of the SJSU Student Success Navigator."

# Shown when a turn offers a draft and has no prose left to introduce it. The model is told
# to always write prose, and the block's content is removed from the bubble (it is an email,
# not a message to the student), so a reply that is ONLY an escalation block leaves nothing
# above the draft. Without this the turn falls through to the loop's "I ran out of time"
# line, which is a false account of a turn that did the work and wrote the message.
#
# The same exception SAFETY_FALLBACK_TEXT is: one server-authored sentence, because the
# alternative is a component on screen with nothing saying why it is there.
ESCALATION_FALLBACK_TEXT = (
    "This one is worth putting in front of a person. I've started a message you can send."
)


def escalation_available(settings: Settings) -> bool:
    """Is there anywhere to send a draft?

    The presence of a recipient is the whole gate, and it is read in two places: here, to
    decide whether prompts.py tells the model the tag exists at all, and in
    build_email_draft below, which cannot address a message without one. Both read the same
    value THROUGH THIS FUNCTION, so the model is never taught a tag whose output the server
    would then discard - including in the one case where the two could have disagreed, a
    recipient that is nothing but whitespace.
    """
    return bool(_recipient(settings))


def _recipient(settings: Settings) -> str:
    """The configured address, stripped. Empty means the feature is off.

    Stripped here as well as in settings.load_settings because a Settings can also be built
    directly - the tests do, and so would any caller constructing one - and "  " must not be
    an address that gates the prompt on and then addresses a mail client to nothing.
    """
    return (settings.escalation_recipient or "").strip()


def build_email_draft(prose: str | None, *, settings: Settings) -> EmailDraft | None:
    """One `<escalate_to_human>` block as a finished message, or None with a reason logged.

    `prose` is the block's content as cards.py parsed it, or None when the model emitted no
    tag - the ordinary case, and the only one that returns quietly.

    Every other None is a DROPPED OFFER and is logged, because each of them means something
    upstream is wrong rather than absent: a tag emitted into a deployment that never
    mentioned it, an empty block, or a draft past its guard. Dropping is always the safe
    direction here - the reply still answers the student, it just does not offer to write to
    anyone.
    """
    if prose is None:
        return None

    if not escalation_available(settings):
        # The prompt does not mention the tag when no recipient is configured, so this is
        # the model reaching for a contract it was never given.
        logger.warning(
            "The model emitted an escalate_to_human block, but this deployment has no "
            "escalation.recipient configured; dropping the offer."
        )
        return None

    body_prose = prose.strip()
    if not body_prose:
        logger.warning("An empty escalate_to_human block; dropping the offer.")
        return None

    if len(body_prose) > settings.escalation_max_chars:
        # NOT TRUNCATED. See the module docstring: a cut email is a message the student may
        # send before they notice half of it is missing.
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
        # THE WHOLE MESSAGE, in the order it is read: what the model wrote for this student,
        # then the provenance line. Nothing else - no return address (the message carries
        # the student's own), no conversation id, no transcript, no assessment of the
        # student. What staff receive is what the student can see on screen before they
        # send it.
        body=f"{body_prose}\n\n{PROVENANCE_LINE}",
    )

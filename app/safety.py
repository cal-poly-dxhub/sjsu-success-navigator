"""Safety handoff: the model triages, the server owns every digit of contact info.

There is no pre-model phrase gate (decision, 2026-08-10). The system prompt carries the
emergency instruction and a roster of handoff resources, each with a short key; on an
emergency the model emits `<safety>key, key</safety>` and this module resolves those keys
into the fixed contact panel. The split mirrors the card ref contract: the model decides
WHEN a handoff is needed and WHICH resources fit, but the label, number, and link the
student sees come only from the table below - a contact the model invented has no way onto
the screen, because keys are the only thing it can say.

Failure direction is always toward showing help: an unknown key is dropped with a WARNING,
a safety tag whose keys all fail to resolve gets the DEFAULT crisis set, and a reply whose
prose cites crisis lines without the tag has the panel attached anyway (see
apply_safety_handoff_to_response). No path renders an empty panel.

Contact facts are drawn from the live SJSU pages, verified 2026-08-10 against
eval/ground-truth.yaml - never LLM-generated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from models import ChatResponse, SafetyContact, SafetyHandoff

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SafetyResource:
    """One handoff destination: the contact block the panel renders, and the one-line
    "when" the system prompt shows the model. `when=None` keeps a resource resolvable
    (and usable in the default set) without offering it to the model."""

    contact: SafetyContact
    when: str | None


SAFETY_RESOURCES: dict[str, SafetyResource] = {
    "emergency-911": SafetyResource(
        contact=SafetyContact(
            id="emergency-911",
            label="Call 911",
            detail="Immediate danger to life or safety, right now",
            href="tel:911",
        ),
        when="someone is in immediate physical danger or a life-threatening situation is happening now",
    ),
    "crisis-988": SafetyResource(
        contact=SafetyContact(
            id="crisis-988",
            label="Call or text 988",
            detail="Suicide & Crisis Lifeline, 24/7",
            href="https://988lifeline.org/",
        ),
        when="thoughts of suicide or self-harm, or an emotional crisis they cannot cope with",
    ),
    "after-hours": SafetyResource(
        contact=SafetyContact(
            id="after-hours",
            label="Call 408-924-5678",
            detail="Urgent medical or mental health support after hours",
            href="tel:4089245678",
        ),
        when="an urgent medical or mental health need outside business hours",
    ),
    "caps": SafetyResource(
        contact=SafetyContact(
            id="caps",
            label="CAPS same-day support",
            detail="Counseling & Psychological Services at SJSU",
            href="https://www.sjsu.edu/wellness/access-services/counseling/index.php",
        ),
        when="needs to talk to a counselor soon, during business hours",
    ),
    "sas": SafetyResource(
        contact=SafetyContact(
            id="sas",
            label="Survivor Advocacy Services (confidential)",
            detail="Campus survivor advocate: 408-924-7300, survivoradvocate@sjsu.edu",
            href="https://www.sjsu.edu/wellness/access-services/survivor-advocacy-services.php",
        ),
        when="sexual violence, relationship abuse, or stalking, and confidential support that does not trigger a report",
    ),
    "upd": SafetyResource(
        contact=SafetyContact(
            id="upd",
            label="University Police Department",
            detail="Campus emergencies: 911, or 408-924-2222 around the clock",
            href="https://www.sjsu.edu/police/",
        ),
        when="a crime or an unsafe situation on campus",
    ),
    "crisis-page": SafetyResource(
        contact=SafetyContact(
            id="crisis-page",
            label="Emergency & crisis page",
            detail="Official SJSU wellness guidance",
            href="https://www.sjsu.edu/wellness/access-services/emergency-crisis.php",
        ),
        when=None,  # default-set filler, not a triage choice the model needs
    ),
}

# The panel when the model emits a bare <safety/> or none of its keys resolve: today's
# standard crisis set, unchanged from the fixed panel this module used to hardcode.
DEFAULT_SAFETY_KEYS: tuple[str, ...] = ("crisis-988", "after-hours", "caps", "crisis-page")

# Shown when a safety turn arrives with no usable prose: the panel needs an introduction,
# and this is the one sentence the server is allowed to author.
SAFETY_FALLBACK_TEXT = (
    "Thanks for telling me. I'm not able to give counseling myself, so please reach a "
    "real person using the options below. You're not alone, and help is available right now."
)

_HEADLINE = "Talk to someone now"
_BODY = (
    "If this is life-threatening, call 911. For urgent support, "
    "use a trusted crisis line or the campus services below."
)


def safety_roster_for_prompt() -> list[tuple[str, str]]:
    """(key, when) pairs for the system prompt, in table order. The prompt and the resolver
    read the same table, so a key the model is taught always resolves and a key it was
    never taught is the only kind that can miss."""
    return [
        (key, resource.when)
        for key, resource in SAFETY_RESOURCES.items()
        if resource.when is not None
    ]


def resolve_safety_handoff(keys: tuple[str, ...]) -> SafetyHandoff:
    """The panel for one model-emitted key list: valid keys in emitted order, deduplicated;
    unknown keys dropped with a WARNING; nothing valid means the default crisis set."""
    contacts: list[SafetyContact] = []
    seen: set[str] = set()
    for key in keys:
        normalized = key.strip().lower()
        if not normalized or normalized in seen:
            continue
        resource = SAFETY_RESOURCES.get(normalized)
        if resource is None:
            logger.warning(
                "Unknown safety key %r from model; known keys: %s",
                key, ", ".join(SAFETY_RESOURCES),
            )
            continue
        seen.add(normalized)
        contacts.append(resource.contact)

    if not contacts:
        contacts = [SAFETY_RESOURCES[key].contact for key in DEFAULT_SAFETY_KEYS]

    return SafetyHandoff(headline=_HEADLINE, body=_BODY, contacts=contacts)


# If the model cites crisis lines in prose but forgets the safety tag, attach the panel.
_CRISIS_OUTPUT_MARKERS: tuple[str, ...] = (
    "988",
    "988lifeline",
    "call 911",
    "call or text 988",
    "suicide & crisis",
    "suicide and crisis",
    "crisis lifeline",
    "408-924-5678",
    "4089245678",
)


def suggests_crisis_resources_in_text(text: str) -> bool:
    lowered = " ".join(text.lower().split()).replace("-", "")
    compact = lowered.replace(" ", "")
    for marker in _CRISIS_OUTPUT_MARKERS:
        normalized_marker = marker.replace("-", "").replace(" ", "")
        if normalized_marker in compact or marker in lowered:
            return True
    return False


def apply_safety_handoff_to_response(
    response: ChatResponse,
    *,
    conversational_text: str,
    safety_keys: tuple[str, ...] | None,
) -> ChatResponse:
    """Attach the resolved panel when the model emitted a safety tag, or the default panel
    when its prose cites crisis lines without one. `safety_keys=None` means no tag; an
    empty tuple means a bare tag. A safety turn carries no cards.

    Attaching the panel also collapses a split reply back into one bubble. The cards it
    dropped are what trailing prose renders under, so leaving the split would put half the
    message below the panel - and the panel sits directly under the message it belongs to,
    never buried inside it. That placement is a safety property, so it is enforced here,
    beside the card drop, rather than left to the caller.

    THE ESCALATION OFFER GOES WITH THEM, for the same reason and on the same line. A safety
    turn's answer is the panel; an email draft under it would put a message the student has
    to write, and wait on, between them and a number that answers now. The orchestrator
    already skips building one when the model tagged the turn itself, so what this catches
    is the other route in - prose that names crisis lines without the tag, where the model
    thought it was writing an ordinary reply and offered to email an office.

    THE LOCATION CARD GOES WITH THEM TOO, on the same line and for the same reason. It is
    the same rule about what a safety turn may contain rather than a new one: a map and a
    walking route are an errand, and a turn that attached the panel did so because somebody
    needs a number now."""
    if response.safety_handoff is not None:
        return response

    if safety_keys is None:
        if not suggests_crisis_resources_in_text(conversational_text):
            return response
        handoff = resolve_safety_handoff(())
    else:
        handoff = resolve_safety_handoff(safety_keys)

    whole_message = "\n\n".join(
        part.strip()
        for part in (response.conversational_text, response.trailing_text)
        if part and part.strip()
    )

    if response.escalation is not None:
        logger.info("Dropping an escalation offer from a safety turn.")

    if response.place is not None:
        logger.info("Dropping a location card from a safety turn.")

    return response.model_copy(
        update={
            "safety_handoff": handoff,
            "statement_batches": None,
            "place": None,
            "escalation": None,
            "conversational_text": whole_message,
            "trailing_text": None,
        }
    )

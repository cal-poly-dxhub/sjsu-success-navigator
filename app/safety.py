from __future__ import annotations

import re
from dataclasses import dataclass

from models import ChatResponse, SafetyContact, SafetyHandoff

_WHITESPACE_RE = re.compile(r"\s+")

# Hard gate only — explicit crisis / harm language. Broader distress is handled by the agent.
_SAFETY_PHRASES: tuple[str, ...] = (
    "suicide",
    "suicidal",
    "kill myself",
    "killing myself",
    "end my life",
    "want to die",
    "wanna die",
    "self-harm",
    "self harm",
    "hurt myself",
    "hurting myself",
    "cut myself",
    "cutting myself",
    "sexual assault",
    "was raped",
    "been raped",
    "raped me",
    "domestic violence",
    "being abused",
    "abused by",
    "in crisis",
    "having a crisis",
    "mental health crisis",
    "not safe at home",
    "afraid for my life",
)

# Contacts drawn from SJSU emergency/crisis page + frontend fixture (not LLM-generated).
_SAFETY_HANDOFF = SafetyHandoff(
    headline="Talk to someone now",
    body=(
        "If this is life-threatening, call 911. For urgent mental health support, "
        "use a trusted crisis line or campus same-day services."
    ),
    contacts=[
        SafetyContact(
            id="988",
            label="Call or text 988",
            detail="Suicide & Crisis Lifeline — 24/7",
            href="https://988lifeline.org/",
        ),
        SafetyContact(
            id="after-hours",
            label="Call 408-924-5678",
            detail="Urgent medical or mental health support after hours",
            href="tel:4089245678",
        ),
        SafetyContact(
            id="caps",
            label="CAPS same-day support",
            detail="Counseling & Psychological Services at SJSU",
            href="https://www.sjsu.edu/wellness/access-services/counseling/index.php",
        ),
        SafetyContact(
            id="crisis-page",
            label="Emergency & crisis page",
            detail="Official SJSU wellness guidance",
            href="https://www.sjsu.edu/wellness/access-services/emergency-crisis.php",
        ),
    ],
)

_SAFETY_CONVERSATIONAL_TEXT = (
    "Thanks for telling me. I'm not able to give counseling myself — please reach a "
    "real person using the options below. You're not alone, and help is available right now."
)


@dataclass(frozen=True)
class SafetyMatch:
    matched_phrase: str


def normalize_query(query: str) -> str:
    return _WHITESPACE_RE.sub(" ", query.lower()).strip()


def detect_safety(query: str) -> SafetyMatch | None:
    normalized = normalize_query(query)
    if not normalized:
        return None

    for phrase in _SAFETY_PHRASES:
        if phrase in normalized:
            return SafetyMatch(matched_phrase=phrase)

    return None


def get_standard_safety_handoff() -> SafetyHandoff:
    """Fixed crisis panel shown in the UI — not LLM-generated."""
    return _SAFETY_HANDOFF


# If the model mentions crisis lines in prose but forgets needsSafetyHandoff, attach the panel.
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
    lowered = normalize_query(text.replace("-", ""))
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
    requested: bool,
) -> ChatResponse:
    """Attach the standard crisis handoff when the agent requests it or cites crisis lines."""
    if response.safety_handoff is not None:
        return response

    if not requested and not suggests_crisis_resources_in_text(conversational_text):
        return response

    return response.model_copy(
        update={
            "safety_handoff": get_standard_safety_handoff(),
            "statement_batches": None,
        }
    )


def build_safety_response() -> ChatResponse:
    return ChatResponse(
        conversationalText=_SAFETY_CONVERSATIONAL_TEXT,
        statementBatches=[],
        safetyHandoff=_SAFETY_HANDOFF,
        talkToPersonAvailable=True,
    )


def try_safety_response(query: str) -> ChatResponse | None:
    if detect_safety(query) is None:
        return None
    return build_safety_response()

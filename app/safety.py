"""Safety handoff: the model triages with keys, the server owns every digit of contact.

There is no pre-model phrase gate, and failure always leans toward showing help.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from campus_data import CampusDataError, load_rows, parse_flag
from models import ChatResponse, SafetyContact, SafetyHandoff

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SafetyResource:
    """One handoff destination. `when=None` keeps it resolvable without offering it."""

    contact: SafetyContact
    when: str | None


# Read at import, so a malformed row fails the cold start rather than the panel.
_CONTACTS_FILE = "contacts.csv"
_SAFETY_KIND = "safety"


def _load_safety_table() -> tuple[dict[str, SafetyResource], tuple[str, ...]]:
    resources: dict[str, SafetyResource] = {}
    default_keys: list[str] = []
    for row in load_rows(
        _CONTACTS_FILE,
        ("kind", "id"),
        optional=("label", "detail", "href", "when", "in_default_panel", "note"),
    ):
        if row["kind"] != _SAFETY_KIND:
            continue
        key = row["id"]
        # Other kinds may leave these blank; a button on a crisis panel may not.
        for column in ("label", "detail", "href"):
            if not row[column]:
                raise CampusDataError(
                    f"{_CONTACTS_FILE}: the {_SAFETY_KIND} row {key!r} has an empty "
                    f"`{column}`. Every safety row becomes a button on the crisis panel, so "
                    "it needs the words on it, the line under them, and somewhere to go."
                )
        if key in resources:
            raise CampusDataError(
                f"{_CONTACTS_FILE}: two {_SAFETY_KIND} rows share the id {key!r}. One key, "
                "one row - a second one would quietly win, and the panel would show "
                "whichever came last."
            )
        resources[key] = SafetyResource(
            contact=SafetyContact(
                id=key, label=row["label"], detail=row["detail"], href=row["href"]
            ),
            when=row["when"] or None,
        )
        if parse_flag(
            row["in_default_panel"],
            name=_CONTACTS_FILE,
            key=key,
            column="in_default_panel",
        ):
            default_keys.append(key)

    if not resources:
        raise CampusDataError(
            f"{_CONTACTS_FILE} carries no `{_SAFETY_KIND}` rows. Every crisis contact a "
            "student can be shown comes from those rows, so a file without one is a safety "
            "panel with nothing on it."
        )
    if not default_keys:
        raise CampusDataError(
            f"no `{_SAFETY_KIND}` row in {_CONTACTS_FILE} has in_default_panel set. That "
            "column is the panel a student gets when the model tags an emergency and names "
            "no resources, and an empty one is a handoff with no numbers on it."
        )
    return resources, tuple(default_keys)


# The panel a bare tag or an all-unknown key list resolves to.
SAFETY_RESOURCES, DEFAULT_SAFETY_KEYS = _load_safety_table()

# For a safety turn that arrives with no prose.
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
    return [
        (key, resource.when)
        for key, resource in SAFETY_RESOURCES.items()
        if resource.when is not None
    ]


def resolve_safety_handoff(keys: tuple[str, ...]) -> SafetyHandoff:
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
    """Attach the panel, and drop the cards, offer and location card a safety turn excludes."""
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

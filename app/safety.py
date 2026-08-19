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

THE TABLE IS data/contacts.csv, the `safety` rows of it, read at import through
app/campus_data.py. The facts left this file for the reason the whole `data/` directory
exists: a contact spelled once in Python and again in TypeScript has no test that can see
both copies. Here it also buys something narrower - a crisis number is now a spreadsheet row
a person at Student Affairs can correct without a code change, and `in_default_panel` marks
the standard set on the same rows rather than in a second list beside them.

Contact facts are drawn from the live SJSU pages, verified 2026-08-10 against
eval/ground-truth.yaml - never LLM-generated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from campus_data import CampusDataError, load_rows, parse_flag
from models import ChatResponse, SafetyContact, SafetyHandoff

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SafetyResource:
    """One handoff destination: the contact block the panel renders, and the one-line
    "when" the system prompt shows the model. `when=None` keeps a resource resolvable
    (and usable in the default set) without offering it to the model."""

    contact: SafetyContact
    when: str | None


# The handoff table, from the `safety` rows of data/contacts.csv, in file order. Read at
# import: a malformed row is a cold start that fails with a line number, never a panel that
# comes up one crisis number short.
_CONTACTS_FILE = "contacts.csv"
_SAFETY_KIND = "safety"


def _load_safety_table() -> tuple[dict[str, SafetyResource], tuple[str, ...]]:
    """The resources and the default panel, from ONE pass over the same rows.

    Both come back together because they are the same table read two ways, and the default
    set's ORDER IS THE FILE'S. A second list holding that order would be one more thing to
    keep in step with the rows it names, which is the shape this whole directory exists to
    stop.
    """
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
        # THE THREE CELLS THE PANEL RENDERS, checked here rather than by the file reader,
        # because the OTHER kinds in this file legitimately leave some of them blank - a
        # cares row is a link with no phone number, or a number with no link. A safety row is
        # a button on a crisis panel, so all three are load-bearing and a blank one is fatal.
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
            # An empty `when` keeps a resource resolvable without offering it to the model.
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


# The panel when the model emits a bare <safety/> or none of its keys resolve is today's
# standard crisis set, marked row by row in data/contacts.csv rather than listed here.
SAFETY_RESOURCES, DEFAULT_SAFETY_KEYS = _load_safety_table()

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

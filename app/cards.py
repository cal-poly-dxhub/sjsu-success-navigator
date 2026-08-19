"""Cards, parsed from the tags the model emits, not cut out of anybody's prose.

The model cites a ref rather than a URL and the caps are runaway guards; see
docs/cards-v2.md and docs/chat-service.md, Cards and the tag contract.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from settings import Settings
from models import FollowupAction, SourceAction, StatementBatch, StatementCard
from retrieve import RetrievedChunk

logger = logging.getLogger(__name__)

# Fixed chrome, never model-authored. The model authors only the hidden <followup> prompt.
SOURCE_ACTION_LABEL = "Open source"

FOLLOWUP_ACTION_LABEL = "Tell me more"

_WHITESPACE_RE = re.compile(r"\s+")
# Spaces and tabs, never a newline: <desc> keeps the line breaks the model wrote.
_HORIZONTAL_WHITESPACE_RE = re.compile(r"[^\S\n]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

# Escapes rather than the characters, so grepping the repo for a dash stays meaningful.
_EM_DASH = "\u2014"
_EN_DASH = "\u2013"
_DIGIT_RANGE_EN_DASH_RE = re.compile(rf"(?<=\d){_EN_DASH}(?=\d)")
_EM_EN_DASH_RE = re.compile(rf"\s*[{_EM_DASH}{_EN_DASH}]\s*")

# Non-greedy, so two adjacent cards do not collapse into one. `ref` is read separately,
# because a card with no ref is still a card.
_CARD_BLOCK_RE = re.compile(r"<card\b([^>]*)>(.*?)</card\s*>", re.DOTALL | re.IGNORECASE)
_REF_ATTR_RE = re.compile(r"\bref\s*=\s*[\"']?\s*(\d+)", re.IGNORECASE)
_SAFETY_TAG_RE = re.compile(r"<safety\s*/?>", re.IGNORECASE)
# A keyed safety block. The whole block leaves the prose: the keys address the server.
_SAFETY_BLOCK_RE = re.compile(r"<safety\s*>(.*?)</safety\s*>", re.DOTALL | re.IGNORECASE)
_SAFETY_KEY_RE = re.compile(r"[a-z0-9][a-z0-9-]*", re.IGNORECASE)
# Prose only, no attribute group: a model-chosen recipient has nowhere to arrive.
_ESCALATION_BLOCK_RE = re.compile(
    r"<escalate_to_human\s*>(.*?)</escalate_to_human\s*>", re.DOTALL | re.IGNORECASE
)
# One catalogue key, no attribute group: a model-authored address has nowhere to arrive.
_PLACE_BLOCK_RE = re.compile(r"<place\s*>(.*?)</place\s*>", re.DOTALL | re.IGNORECASE)
_PLACE_KEY_RE = re.compile(r"[a-z0-9][a-z0-9-]*", re.IGNORECASE)


def _field_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"<{name}\s*>(.*?)</{name}\s*>", re.DOTALL | re.IGNORECASE)


_TITLE_RE = _field_re("title")
_DESC_RE = _field_re("desc")
_FOLLOWUP_RE = _field_re("followup")

# Deliberately not a generic <[^>]+> sweep; only this contract's own vocabulary.
_ANY_KNOWN_TAG_RE = re.compile(
    r"</?\s*(?:card|title|desc|followup|safety|escalate_to_human|place)\b[^>]*/?>",
    re.IGNORECASE,
)

# The same vocabulary as literals, for preview_safe_prefix. One tuple, so they agree.
_TAG_NAMES = ("card", "title", "desc", "followup", "safety", "escalate_to_human", "place")
_TAG_OPENINGS = tuple(
    f"<{slash}{name}" for name in _TAG_NAMES for slash in ("", "/")
)


def preview_safe_prefix(text: str) -> str:
    """The leading run of a PARTIAL reply that is certainly outside this contract's tags."""
    for index, character in enumerate(text):
        if character != "<":
            continue
        rest = text[index:].lower()
        for opening in _TAG_OPENINGS:
            # A tag starts here, or `rest` is a partial that could still become one.
            if rest.startswith(opening) or opening.startswith(rest):
                return text[:index]
    return text


def card_block_started(text: str) -> bool:
    """Has the model DEFINITELY begun writing cards? A <safety> anywhere takes it back to no."""
    lowered = text.lower()
    if "<safety" in lowered:
        return False
    return "<card" in lowered


@dataclass(frozen=True)
class SourceOption:
    """One retrieved source as the model sees it: an id, a title and text. No URL."""

    ref_id: int
    title: str
    text: str
    source_url: str
    section: str | None


class TurnSources:
    """The id-to-source map for ONE turn, and the only thing that can produce a card's URL."""

    def __init__(self) -> None:
        self._by_url: dict[str, SourceOption] = {}
        self._by_id: dict[int, SourceOption] = {}
        self._next_id = 1

    def add_chunks(self, chunks: list[RetrievedChunk], *, limit: int) -> list[SourceOption]:
        """Register a retrieval result set and return what to show the model for THIS call."""
        options: list[SourceOption] = []

        for chunk in sorted(chunks, key=lambda c: c.score, reverse=True):
            if len(options) >= limit:
                break

            url = (chunk.source_url or "").strip()
            if not url:
                continue

            existing = self._by_url.get(url)
            if existing is not None:
                # Same page, seen in an earlier search this turn; reuse its id.
                if existing not in options:
                    options.append(existing)
                continue

            option = SourceOption(
                ref_id=self._next_id,
                title=(chunk.title or "").strip() or "Campus resource",
                text=chunk.text,
                source_url=url,
                section=chunk.section,
            )
            self._by_url[url] = option
            self._by_id[option.ref_id] = option
            self._next_id += 1
            options.append(option)

        return options

    @classmethod
    def from_stored(cls, urls_by_ref: dict[int, str] | None) -> "TurnSources":
        """Rebuild the map a stored reply cited, off the record rather than a fresh search."""
        sources = cls()
        for ref_id, url in (urls_by_ref or {}).items():
            option = SourceOption(
                ref_id=int(ref_id),
                title="",
                text="",
                source_url=str(url),
                section=None,
            )
            sources._by_id[option.ref_id] = option
            sources._by_url[option.source_url] = option
            sources._next_id = max(sources._next_id, option.ref_id + 1)
        return sources

    def resolve(self, ref_id: int | None) -> SourceOption | None:
        if ref_id is None:
            return None
        return self._by_id.get(ref_id)

    def ref_for_url(self, url: str) -> int | None:
        """The id this turn gave a URL, or None. The inverse of `resolve`."""
        option = self._by_url.get((url or "").strip())
        return None if option is None else option.ref_id

    def known_ids(self) -> list[int]:
        return sorted(self._by_id)

    def __len__(self) -> int:
        return len(self._by_id)


@dataclass(frozen=True)
class ParsedCard:
    """One <card> block, exactly as written. Nothing resolved, nothing capped yet."""

    ref_id: int | None
    title: str
    desc: str
    followup: str


@dataclass(frozen=True)
class ParsedResponse:
    prose: str
    cards: list[ParsedCard]
    # None means no tag; an empty tuple means a bare <safety/>.
    safety_keys: tuple[str, ...] | None
    trailing_prose: str = ""
    escalation_prose: str | None = None
    place_key: str | None = None

    @property
    def needs_safety(self) -> bool:
        return self.safety_keys is not None


def parse_model_response(text: str) -> ParsedResponse:
    """Split one model turn into prose and card blocks, keeping the card group's position."""
    source = text or ""

    matches = list(_CARD_BLOCK_RE.finditer(source))

    cards: list[ParsedCard] = []
    for match in matches:
        ref_match = _REF_ATTR_RE.search(match.group(1))
        body = match.group(2)
        cards.append(
            ParsedCard(
                ref_id=int(ref_match.group(1)) if ref_match else None,
                title=_first_field(_TITLE_RE, body),
                desc=_first_field(_DESC_RE, body, keep_line_breaks=True),
                followup=_first_field(_FOLLOWUP_RE, body),
            )
        )

    split_at = matches[-1].end() if matches else len(source)
    lead = _CARD_BLOCK_RE.sub("\n\n", source[:split_at])
    trailing = source[split_at:]

    return ParsedResponse(
        prose=_clean_prose(lead),
        cards=cards,
        safety_keys=_safety_keys_in(lead, trailing),
        trailing_prose=_clean_prose(trailing),
        escalation_prose=_escalation_prose_in(lead, trailing),
        place_key=_place_key_in(lead, trailing),
    )


def _place_key_in(*parts: str) -> str | None:
    """The one campus-location key, read from both sides of the split."""
    contents = [content for part in parts for content in _PLACE_BLOCK_RE.findall(part)]
    if not contents:
        return None
    if len(contents) > 1:
        logger.warning(
            "The model emitted %s place blocks; keeping the first and ignoring the rest. "
            "One turn points at one place.",
            len(contents),
        )
    keys = _PLACE_KEY_RE.findall(contents[0])
    if not keys:
        # The model reached for the contract and wrote nothing usable.
        logger.warning("An empty place block; no location card.")
        return None
    if len(keys) > 1:
        logger.warning(
            "A place block carried %s keys (%s); keeping the first. A location card has "
            "one address on it.",
            len(keys),
            ", ".join(keys),
        )
    return keys[0].lower()


def _escalation_prose_in(*parts: str) -> str | None:
    """The one escalate-to-human block's prose, read from both sides of the split."""
    contents = [content for part in parts for content in _ESCALATION_BLOCK_RE.findall(part)]
    if not contents:
        return None
    if len(contents) > 1:
        logger.warning(
            "The model emitted %s escalate_to_human blocks; keeping the first and "
            "ignoring the rest. One turn makes one offer.",
            len(contents),
        )
    return _collapse_keeping_line_breaks(normalise_dashes(contents[0]))


def _safety_keys_in(*parts: str) -> tuple[str, ...] | None:
    """The model's safety keys, read from both sides of the split."""
    block_contents = [content for part in parts for content in _SAFETY_BLOCK_RE.findall(part)]
    if block_contents:
        return tuple(
            match.group(0).lower()
            for content in block_contents
            for match in _SAFETY_KEY_RE.finditer(content)
        )
    if any(_SAFETY_TAG_RE.search(part) for part in parts):
        # A bare or unclosed <safety>: the handoff fires with no keys, so the default set.
        return ()
    return None


def join_prose(*parts: str | None) -> str:
    """Put a split reply back together as one bubble, blank line between the halves."""
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def normalise_dashes(text: str) -> str:
    """Rewrite em and en dashes into punctuation the UI may show. Runs before the caps."""

    def _to_hyphen(match: re.Match[str]) -> str:
        logger.info("Normalised an en dash in a digit range to a hyphen.")
        return "-"

    def _to_comma(match: re.Match[str]) -> str:
        logger.info("Normalised a dash (%r) in display text to a comma.", match.group(0))
        return ", "

    result = _DIGIT_RANGE_EN_DASH_RE.sub(_to_hyphen, text or "")
    return _EM_EN_DASH_RE.sub(_to_comma, result)


def _collapse_whitespace(text: str) -> str:
    """One line, single-spaced. Every field but the description gets this."""
    return _WHITESPACE_RE.sub(" ", text or "").strip()


def _collapse_keeping_line_breaks(text: str) -> str:
    """The same collapse, except that the model's line breaks stay. Only <desc> needs it."""
    lines = [
        _HORIZONTAL_WHITESPACE_RE.sub(" ", line).strip() for line in (text or "").splitlines()
    ]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _first_field(pattern: re.Pattern[str], body: str, *, keep_line_breaks: bool = False) -> str:
    match = pattern.search(body)
    if match is None:
        return ""
    collapse = _collapse_keeping_line_breaks if keep_line_breaks else _collapse_whitespace
    return collapse(match.group(1))


def _clean_prose(text: str) -> str:
    """Scrub every known tag, blocks whole, and close up the blank lines they leave."""
    stripped = _SAFETY_BLOCK_RE.sub("\n\n", text)
    stripped = _ESCALATION_BLOCK_RE.sub("\n\n", stripped)
    stripped = _PLACE_BLOCK_RE.sub("\n\n", stripped)
    stripped = _ANY_KNOWN_TAG_RE.sub("", stripped)
    stripped = normalise_dashes(stripped)
    stripped = _BLANK_LINES_RE.sub("\n\n", stripped)
    return "\n".join(line.rstrip() for line in stripped.splitlines()).strip()


def strip_card_tags(text: str) -> str:
    """The whole response as prose, markup removed. The zero-cards fallback."""
    return _clean_prose(text or "")


def truncate_to_cap(text: str, cap: int, *, keep_line_breaks: bool = False) -> str:
    """Shorten to `cap` characters INCLUDING the ellipsis, breaking on a word boundary."""
    collapse = _collapse_keeping_line_breaks if keep_line_breaks else _collapse_whitespace
    normalized = collapse(text)
    if len(normalized) <= cap:
        return normalized

    # One character of the budget belongs to the ellipsis.
    head = normalized[: cap - 1]
    boundary = max(head.rfind(" "), head.rfind("\n"))
    if boundary > 0:
        head = head[:boundary]

    return head.rstrip(" \n,;:.-") + "…"


def _capped(
    text: str, cap: int, field: str, ref_id: int | None, *, keep_line_breaks: bool = False
) -> str:
    """Apply one display field's guard cap, logging when it actually bites."""
    collapse = _collapse_keeping_line_breaks if keep_line_breaks else _collapse_whitespace
    normalized = collapse(text)
    if len(normalized) > cap:
        logger.warning(
            "A card %s ran past its guard cap (%s chars, cap %s, ref=%r) and was "
            "truncated. The cap sits far above the length the prompt steers toward, so "
            "hitting it means the prompt or the model is broken.",
            field,
            len(normalized),
            cap,
            ref_id,
        )
    return truncate_to_cap(normalized, cap, keep_line_breaks=keep_line_breaks)


def cards_from_parsed(
    parsed_cards: list[ParsedCard],
    sources: TurnSources,
    settings: Settings,
) -> list[StatementCard]:
    """Turn parsed blocks into wire cards: resolve the ref, apply the caps, build the actions."""
    cards: list[StatementCard] = []

    for index, parsed in enumerate(parsed_cards):
        if len(cards) >= settings.card_max_cards:
            logger.info(
                "Model emitted %s cards; keeping the first %s.",
                len(parsed_cards),
                settings.card_max_cards,
            )
            break

        title = _capped(
            normalise_dashes(parsed.title), settings.card_title_max_chars, "title", parsed.ref_id
        )
        desc = _capped(
            normalise_dashes(parsed.desc),
            settings.card_desc_max_chars,
            "description",
            parsed.ref_id,
            keep_line_breaks=True,
        )
        if not title or not desc:
            logger.warning(
                "Dropping a card block with no %s (ref=%r).",
                "title" if not title else "description",
                parsed.ref_id,
            )
            continue

        source = sources.resolve(parsed.ref_id)
        if source is None:
            logger.warning(
                "Card ref %r did not resolve against this turn's sources (available: %s). "
                "Keeping the card without its source link.",
                parsed.ref_id,
                sources.known_ids(),
            )

        actions: list = []
        if source is not None:
            actions.append(SourceAction(label=SOURCE_ACTION_LABEL))

        # Never truncated: a shortened question is a different question. Overruns are logged.
        followup = _collapse_whitespace(parsed.followup)
        if followup and len(followup) > settings.card_followup_max_chars:
            logger.warning(
                "An over-cap follow-up prompt (%s chars, cap %s) on card ref=%r. Keeping "
                "the button and sending the prompt whole.",
                len(followup),
                settings.card_followup_max_chars,
                parsed.ref_id,
            )
        if followup:
            actions.append(
                FollowupAction(label=FOLLOWUP_ACTION_LABEL, prompt=followup)
            )

        cards.append(
            StatementCard(
                id=_card_id(index, source),
                title=title,
                body=desc,
                # Nothing renders this without a `source` action, so an unresolved ref
                # yields a card with no link rather than a broken one.
                sourceUrl=source.source_url if source is not None else "",
                actions=actions,
            )
        )

    return cards


def cited_source_urls(
    cards: list[StatementCard],
    sources: TurnSources,
) -> dict[int, str]:
    """The ref-to-URL pairs a FINISHED reply cited. What gets stored beside its text."""
    urls: dict[int, str] = {}
    for card in cards:
        ref_id = sources.ref_for_url(card.source_url)
        if ref_id is not None:
            urls[ref_id] = card.source_url
    return urls


def _card_id(index: int, source: SourceOption | None) -> str:
    """A batch-unique React key. Index-prefixed so two cards citing one source still differ."""
    if source is None:
        return f"c{index}-unsourced"

    path = urlparse(source.source_url).path.strip("/")
    slug = path.replace("/", "-").replace(".php", "").replace(".html", "")
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", slug).strip("-").lower()
    return f"c{index}-{slug}"[:80] if slug else f"c{index}-source"


def source_options_for_tool(options: list[SourceOption]) -> list[dict[str, object]]:
    """The retrieval tool result: id, title and the WHOLE chunk. No URL, deliberately."""
    return [
        {
            "id": option.ref_id,
            "title": option.title,
            "text": option.text,
        }
        for option in options
    ]


def create_statement_batch(cards: list[StatementCard], query: str) -> StatementBatch:
    return StatementBatch(
        id=f"batch-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
        cards=cards,
        query=query,
        createdAt=int(time.time() * 1000),
    )

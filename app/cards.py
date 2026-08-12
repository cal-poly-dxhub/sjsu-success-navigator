"""Cards, parsed from the tags the model emits - not cut out of anybody's prose.

WHAT REPLACED WHAT. v1 had two card paths and neither one had an author. The tool path took
a `submit_chat_response` JSON payload in which the model typed a `sourceUrl` string, which
was then validated by EXACT string match against the retrieved URLs - so a URL that was
right except for a trailing slash lost its link with no signal, and a URL the model invented
outright was merely unlinked rather than rejected. The fallback path was worse: it built
cards mechanically out of the retrieved PAGE text with a regex sentence split and a 220-char
truncate, so on any timeout the student got cards nobody wrote, answering a question nobody
had read. Both are gone. The model now emits its whole turn as text - prose plus <card>
blocks - and this module is the only thing that reads it.

THE MODEL CANNOT EXPRESS A URL. A card cites `ref="2"`, an integer from this turn's
retrieval list, and the server resolves it against a map it built itself (TurnSources). The
model is never shown a URL in the first place (see source_options_for_tool), so "do not
invent URLs" stops being an instruction the model follows or ignores and becomes a shape its
output has no room for. The failure mode is not reduced, it is unrepresentable.

WHERE THE CAPS COME FROM. Every length cap arrives on Settings, sourced from config.yaml,
and is read in exactly two places: here, where it is enforced, and prompts.py, where it is
written into the contract the model is given. One value, so the number the model is told is
by construction the number the server applies. The caps are GUARDS against a runaway
response, set far above the length the prompt steers toward: an ellipsis in production
means the prompt or the model is broken, so every truncation is logged at WARNING. Length
steering is the prompt's job, not this module's.

THE CARDS RENDER WHERE THE MODEL PUT THEM. The reply is split ONCE, at the end of the last
card block: prose before it becomes the bubble above the grid, prose after it becomes the
bubble below. One split rather than a general block list because a turn produces exactly one
card group, so the only thing there is to preserve is which side of that group each piece of
prose was on. This is what keeps a closing question under the cards it refers to instead of
above the answer it is asking about.

THE DESCRIPTION KEEPS ITS LINE BREAKS. Every other field is collapsed to one line, because
every other field IS one line. <desc> is the one the student reads as a body of text, and
the display parser keys on line starts - a bullet is a line beginning with a marker - so
flattening its newlines into spaces turns a list the model wrote into one long sentence with
dashes in it. Indentation is still collapsed: a model that indents its bullets under <desc>
is formatting its XML, not asking for leading space on screen.

AN UNRESOLVABLE REF KEEPS THE CARD. Decided against docs/cards-v2.md, which drops it. The
reason is observability: a card that renders without its source button is a visible symptom,
where a silently dropped card is a student seeing three cards instead of four and nobody
finding out. The event is logged at WARNING with both the bad ref and the ids that were
actually available, because the UI is the weaker half of that signal.
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

# The label on the source link. Fixed here, not model-authored: it is chrome, and a model
# that writes its own link labels eventually writes one that misdescribes the destination.
SOURCE_ACTION_LABEL = "Open source"

# The label on the follow-up button. Also fixed - the model authors only the hidden PROMPT
# behind it (<followup>), which is what actually varies per card.
FOLLOWUP_ACTION_LABEL = "Tell me more"

_WHITESPACE_RE = re.compile(r"\s+")
# Spaces and tabs, never a newline. The description is collapsed with this one so the line
# breaks the model wrote survive to the browser - see _collapse_keeping_line_breaks.
_HORIZONTAL_WHITESPACE_RE = re.compile(r"[^\S\n]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

# Em and en dashes are rewritten out of everything the student reads. The prompt bans them
# and its examples model the ban; this is the deterministic backstop for when the model
# writes one anyway. An en dash between digits is a range and becomes a hyphen ("10-12");
# every other em or en dash, with whatever whitespace surrounds it, becomes a comma and a
# space, which is the punctuation doing the same job. ASCII hyphens pass through untouched.
# Escapes rather than the characters themselves, so grepping the repo for a literal dash
# stays a meaningful check.
_EM_DASH = "\u2014"
_EN_DASH = "\u2013"
_DIGIT_RANGE_EN_DASH_RE = re.compile(rf"(?<=\d){_EN_DASH}(?=\d)")
_EM_EN_DASH_RE = re.compile(rf"\s*[{_EM_DASH}{_EN_DASH}]\s*")

# A <card> block: any attributes, any body, non-greedy so two adjacent cards do not collapse
# into one. `ref` is pulled out of the attributes separately rather than being required by
# this pattern, because a card whose ref is MISSING still has to be found - it is a card that
# loses its link, not text that silently stays in the prose.
_CARD_BLOCK_RE = re.compile(r"<card\b([^>]*)>(.*?)</card\s*>", re.DOTALL | re.IGNORECASE)
_REF_ATTR_RE = re.compile(r"\bref\s*=\s*[\"']?\s*(\d+)", re.IGNORECASE)
_SAFETY_TAG_RE = re.compile(r"<safety\s*/?>", re.IGNORECASE)
# A keyed safety block: <safety>crisis-988, caps</safety>. The content is resource KEYS the
# server resolves into the contact panel (app/safety.py), so the whole block - keys included -
# is removed from the prose: the keys are instructions to the server, not text for the student.
_SAFETY_BLOCK_RE = re.compile(r"<safety\s*>(.*?)</safety\s*>", re.DOTALL | re.IGNORECASE)
_SAFETY_KEY_RE = re.compile(r"[a-z0-9][a-z0-9-]*", re.IGNORECASE)


def _field_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"<{name}\s*>(.*?)</{name}\s*>", re.DOTALL | re.IGNORECASE)


_TITLE_RE = _field_re("title")
_DESC_RE = _field_re("desc")
_FOLLOWUP_RE = _field_re("followup")

# Every tag this contract defines, opening or closing, well-formed or not. Used to scrub the
# prose so a malformed block - an unclosed <card ref="2">, a stray </desc> - reaches the
# student as text rather than as markup. Deliberately NOT a generic <[^>]+> sweep: that would
# eat legitimate content the model might write about, and the guarantee needed here is only
# that OUR tags never surface.
_ANY_KNOWN_TAG_RE = re.compile(
    r"</?\s*(?:card|title|desc|followup|safety)\b[^>]*/?>",
    re.IGNORECASE,
)

# The same vocabulary again, as literals rather than a pattern, for preview_safe_prefix
# below. Derived from one tuple so the two can never come to disagree about what one of
# this contract's tags looks like.
_TAG_NAMES = ("card", "title", "desc", "followup", "safety")
_TAG_OPENINGS = tuple(
    f"<{slash}{name}" for name in _TAG_NAMES for slash in ("", "/")
)


def preview_safe_prefix(text: str) -> str:
    """The leading run of a PARTIAL reply that is certainly outside this contract's tags.

    THIS IS NOT A PARSER AND MUST NEVER BECOME ONE. It answers one question - "where does
    the part I can safely show a student end?" - and it answers it by stopping, never by
    interpreting. Nothing it returns is used to build a card, resolve a ref, decide a safety
    handoff or apply a cap; all of that comes off the COMPLETE reply in parse_model_response,
    which is the only thing in this file that reads a tag for meaning.

    It exists because the model writes its whole turn as one text stream - lead-in prose,
    then `<card>` blocks, then any closing prose - so a streaming preview that pushed raw
    deltas would type `<card ref="2">` onto the screen. Stopping at the first tag makes the
    preview exactly the lead-in, which is the half of the reply the student reads first and
    the half the final payload calls `conversationalText`.

    APPEND-ONLY, which is what makes it safe to stream. The text only ever grows, and this
    only ever stops earlier or later in it, so the returned prefix never shrinks and never
    rewrites what was already sent. A caller emits `preview_safe_prefix(accumulated)` minus
    what it has already pushed.

    A `<` that is not one of ours does NOT stop the preview - "under <15 units" streams
    intact - because the guarantee needed is only that OUR tags never surface, exactly as
    _ANY_KNOWN_TAG_RE's note says. A trailing partial (`...see <ca`) DOES stop it, because
    the rest of that tag has not arrived yet and might.

    Deliberately no dash normalisation and no capping. Those belong to the finished reply
    and are applied there, once; doing them here would be the duplication this contract's
    whole streaming design exists to avoid, and a substitution near the end of a partial
    string could rewrite text already on screen.
    """
    for index, character in enumerate(text):
        if character != "<":
            continue
        rest = text[index:].lower()
        for opening in _TAG_OPENINGS:
            # Either a tag starts here, or `rest` is a partial that could still become one
            # once more of the stream arrives.
            if rest.startswith(opening) or opening.startswith(rest):
                return text[:index]
    return text


@dataclass(frozen=True)
class SourceOption:
    """One retrieved source, as the model sees it: an id, a title, and text.

    No URL. The model never receives one, so it can never echo one back.
    """

    ref_id: int
    title: str
    text: str
    source_url: str
    section: str | None


class TurnSources:
    """The id-to-source map for ONE turn, and the only thing that can produce a card's URL.

    Ids are assigned here, per turn, and are deduplicated by URL across every retrieval call
    the loop makes: a source that comes back from two different searches keeps the id it was
    given the first time, so the model is never shown the same page under two numbers and
    never has to guess which one to cite.

    Ids are NOT stable across turns and nothing persists them. A ref from a previous turn is
    exactly as unresolvable as one the model invented, which is the intended behaviour - the
    map is rebuilt from the sources this turn actually retrieved.
    """

    def __init__(self) -> None:
        self._by_url: dict[str, SourceOption] = {}
        self._by_id: dict[int, SourceOption] = {}
        self._next_id = 1

    def add_chunks(self, chunks: list[RetrievedChunk], *, limit: int) -> list[SourceOption]:
        """Register a retrieval result set and return what to show the model for THIS call.

        Capped at `limit` per call rather than per turn: the cap exists so one tool result
        stays readable, not to bound how much the model may learn over several searches.
        Chunks with no source_url are dropped here - a source that cannot be linked has
        nothing to offer a card whose entire job is provenance.
        """
        options: list[SourceOption] = []

        for chunk in sorted(chunks, key=lambda c: c.score, reverse=True):
            if len(options) >= limit:
                break

            url = (chunk.source_url or "").strip()
            if not url:
                continue

            existing = self._by_url.get(url)
            if existing is not None:
                # Same page, seen in an earlier search this turn. Reuse its id so the model
                # is not shown one source under two numbers.
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

    def resolve(self, ref_id: int | None) -> SourceOption | None:
        if ref_id is None:
            return None
        return self._by_id.get(ref_id)

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
    # None = no safety tag. An empty tuple = a bare <safety/> (the standard panel). Keys are
    # the model's triage choice; app/safety.py owns what each one resolves to.
    safety_keys: tuple[str, ...] | None
    # Prose the model wrote AFTER its last card block, which renders BELOW the grid. Empty
    # for the ordinary reply that ends with its cards.
    trailing_prose: str = ""

    @property
    def needs_safety(self) -> bool:
        return self.safety_keys is not None


def parse_model_response(text: str) -> ParsedResponse:
    """Split one model turn into its prose and its card blocks, keeping the card group's
    position in the reply.

    ONE SPLIT POINT, at the end of the last card block: prose before it is the lead-in that
    renders above the grid, prose after it renders below. That is deliberately not a general
    block-interleaving parser, because a turn can only ever produce ONE card group - the
    cards become a single StatementBatch, dealt as one deck, anchored to once - so the only
    position information there is to keep is which side of that group a piece of prose was
    on. Prose written BETWEEN two card blocks joins the lead: nothing can render inside the
    grid, and it was written to introduce the cards that follow it.

    The prose is everything OUTSIDE the card blocks, which is the fallback's whole mechanism:
    if no block is well-formed then nothing is removed, every known tag is scrubbed, and the
    student gets the model's complete text as one bubble. Content is never dropped on a parse
    failure - only markup is.
    """
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
    )


def _safety_keys_in(*parts: str) -> tuple[str, ...] | None:
    """The model's safety keys, read from both sides of the split.

    Both sides, because a tag written under the cards is still a tag and losing it would
    cost the panel entirely - the one failure this module cannot afford. Called with the
    card blocks already removed, so a stray tag inside a card body does not fire a handoff.
    """
    block_contents = [content for part in parts for content in _SAFETY_BLOCK_RE.findall(part)]
    if block_contents:
        return tuple(
            match.group(0).lower()
            for content in block_contents
            for match in _SAFETY_KEY_RE.finditer(content)
        )
    if any(_SAFETY_TAG_RE.search(part) for part in parts):
        # A bare <safety/> (or a stray unclosed <safety>): the tag still fires the handoff,
        # with no keys, which resolves to the default crisis set.
        return ()
    return None


def join_prose(*parts: str | None) -> str:
    """Put a split reply back together as one bubble, blank line between the halves."""
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def normalise_dashes(text: str) -> str:
    """Rewrite em and en dashes into punctuation the UI is allowed to show.

    Runs on the display path BEFORE cap truncation, so the caps measure the rewritten
    string rather than the one the model typed. Each substitution is logged at INFO so the
    model's dash rate stays measurable from the logs alone: if the prompt's ban is working,
    these lines stop appearing.
    """

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
    """The same collapse, except that the model's line breaks stay.

    ONLY the description needs this, and it needs it because the renderer keys on line
    starts: a bullet is a line beginning with a marker, so a description whose newlines were
    flattened into spaces arrives as one paragraph reading "... - Email: ... - Walk in: ..."
    rather than as a list. The tags themselves are unchanged - this is whitespace handling
    inside one field, not a new construct in the contract.

    Indentation is still incidental and still goes: a model that indents its bullets under
    <desc> is formatting its XML, not asking for leading space on screen. So each line is
    collapsed and stripped on its own, and a run of blank lines closes up to one, the same
    normalisation _clean_prose already applies to the prose either side of the cards.
    """
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
    """Scrub every known tag and normalise the blank lines a removed block leaves behind.

    Safety blocks go WHOLE, content included, before the tag sweep: their content is resource
    keys addressed to the server, and a fallback path that only stripped the tags would leak
    "crisis-988, caps" into the bubble as text."""
    stripped = _SAFETY_BLOCK_RE.sub("\n\n", text)
    stripped = _ANY_KNOWN_TAG_RE.sub("", stripped)
    stripped = normalise_dashes(stripped)
    stripped = _BLANK_LINES_RE.sub("\n\n", stripped)
    return "\n".join(line.rstrip() for line in stripped.splitlines()).strip()


def strip_card_tags(text: str) -> str:
    """The whole response as prose, markup removed. The zero-cards fallback."""
    return _clean_prose(text or "")


def truncate_to_cap(text: str, cap: int, *, keep_line_breaks: bool = False) -> str:
    """Shorten to `cap` characters INCLUDING the ellipsis, breaking on a word boundary.

    Shortening happens here, where it can be measured, rather than in the layout, where v1
    did it: a CSS line clamp hides text without shortening it, so the model was never held to
    a budget and roughly a third of every description was cut with no ellipsis to show it.

    `keep_line_breaks` is the description's normalisation (_collapse_keeping_line_breaks);
    the cap value and the word-boundary rule are the same either way. A break is a boundary
    like a space is, because after either collapse those are the only two whitespace
    characters left in the string.

    The caps this enforces are runaway guards, not editorial budgets - see _capped, which is
    where enforcement actually routes and where hitting one is logged.
    """
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
    """Apply one display field's guard cap, logging when it actually bites.

    The caps sit far above what the prompt asks for, so a truncation here is a bug in the
    prompt or the model rather than routine length variance - the WARNING is what turns a
    quiet ellipsis on screen into a diagnosable event in the logs.
    """
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
    """Turn parsed blocks into wire cards: resolve the ref, apply the caps, build the actions.

    A card needs a title and a description to exist at all - those are the card. Everything
    else degrades rather than dropping: an unresolvable ref costs the source button (see the
    module docstring), and only a missing or empty follow-up costs the follow-up button - an
    over-cap one keeps it, logged, with the prompt sent whole.
    """
    cards: list[StatementCard] = []

    for index, parsed in enumerate(parsed_cards):
        if len(cards) >= settings.card_max_cards:
            logger.info(
                "Model emitted %s cards; keeping the first %s.",
                len(parsed_cards),
                settings.card_max_cards,
            )
            break

        # Dashes are normalised before the cap is applied, so the cap measures the string
        # the student will actually read.
        title = _capped(
            normalise_dashes(parsed.title), settings.card_title_max_chars, "title", parsed.ref_id
        )
        # The description keeps its line breaks; every other field is one line. A bullet is
        # a line that starts with a marker, so flattening them here would cost the list on
        # screen and nothing would show that it had happened.
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

        # A follow-up is never truncated - a shortened question is a DIFFERENT question -
        # and past the cap it keeps its button anyway. The prompt is sent, and shown, as
        # the student's next turn, where it wraps like any typed message, so an over-long
        # one costs nothing visible; the overrun is logged because the cap is a guard on a
        # paid model input, and passing it means the prompt or the model is broken.
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
                # The type requires a string and the frontend reads the field, but nothing
                # renders it without a `source` action - so an unresolved ref yields a card
                # with no link rather than a card with a broken one.
                sourceUrl=source.source_url if source is not None else "",
                actions=actions,
            )
        )

    return cards


def _card_id(index: int, source: SourceOption | None) -> str:
    """A batch-unique React key. Index-prefixed so two cards citing one source still differ."""
    if source is None:
        return f"c{index}-unsourced"

    path = urlparse(source.source_url).path.strip("/")
    slug = path.replace("/", "-").replace(".php", "").replace(".html", "")
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", slug).strip("-").lower()
    return f"c{index}-{slug}"[:80] if slug else f"c{index}-source"


def source_options_for_tool(options: list[SourceOption]) -> list[dict[str, object]]:
    """The retrieval tool result, as the model sees it.

    `id`, `title`, `text`. No URL, deliberately - see the module docstring. Nothing the model
    can write consumes a URL, so sending one would only give it a string to copy into prose.

    `text` is the WHOLE retrieved chunk, untruncated. It used to be sliced to 500 chars
    here, which silently hid whatever sat past the cut - in practice the contact band at
    the tail of every scraped page, so the model kept citing the right page while saying
    it could not see a phone number that retrieval had already fetched (eval 2026-08-10).
    The chunk is already bounded upstream by the ingestion chunking config; a second cap
    in this layer has no job to do.
    """
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

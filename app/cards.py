from __future__ import annotations

import re
import time
import uuid
from urllib.parse import urlparse

from settings import Settings
from models import FollowupAction, SourceAction, StatementBatch, StatementCard
from retrieve import RetrievedChunk, retrieve_chunks

MAX_CARDS_PER_BATCH = 4
BODY_MAX_CHARS = 220

NOISY_SECTIONS = frozenset({"jobs", "events", "blog", "channels"})
NOISY_QUERY_KEYWORDS = frozenset(
    {
        "job",
        "jobs",
        "career fair",
        "event",
        "events",
        "workshop schedule",
        "blog",
        "channel",
        "internship posting",
    }
)

_WHITESPACE_RE = re.compile(r"\s+")


def build_statement_cards(
    chunks: list[RetrievedChunk],
    query: str,
    *,
    max_cards: int = MAX_CARDS_PER_BATCH,
) -> list[StatementCard]:
    ranked = _rank_chunks(chunks, query)
    deduped = _dedupe_by_source_url(ranked)

    cards: list[StatementCard] = []
    for chunk in deduped:
        if len(cards) >= max_cards:
            break
        card = _chunk_to_card(chunk)
        if card is not None:
            cards.append(card)

    return cards


def retrieve_statement_cards(
    query: str,
    # REQUIRED, where camp defaulted it to None. Not a behaviour change - a port
    # necessity: camp's retrieve_chunks fell back to get_settings() when handed None,
    # and this port has no such global (settings come from the environment the stack
    # sets, resolved once at cold start). Leaving the default would turn a missing
    # argument into an AttributeError on None inside retrieval.
    settings: Settings,
    *,
    max_cards: int = MAX_CARDS_PER_BATCH,
) -> list[StatementCard]:
    chunks = retrieve_chunks(query, settings)
    return build_statement_cards(chunks, query, max_cards=max_cards)


def _rank_chunks(chunks: list[RetrievedChunk], query: str) -> list[RetrievedChunk]:
    allow_noisy = _query_allows_noisy_sections(query)
    preferred: list[RetrievedChunk] = []
    noisy: list[RetrievedChunk] = []

    for chunk in chunks:
        section = (chunk.section or "").lower()
        if section in NOISY_SECTIONS and not allow_noisy:
            noisy.append(chunk)
        else:
            preferred.append(chunk)

    return preferred + noisy


def _query_allows_noisy_sections(query: str) -> bool:
    normalized = query.lower()
    return any(keyword in normalized for keyword in NOISY_QUERY_KEYWORDS)


def _dedupe_by_source_url(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    best_by_url: dict[str, RetrievedChunk] = {}

    for chunk in chunks:
        key = chunk.source_url or chunk.s3_uri or chunk.text[:80]
        existing = best_by_url.get(key)
        if existing is None or chunk.score > existing.score:
            best_by_url[key] = chunk

    deduped = list(best_by_url.values())
    deduped.sort(key=lambda chunk: chunk.score, reverse=True)
    return deduped


def _chunk_to_card(chunk: RetrievedChunk) -> StatementCard | None:
    source_url = chunk.source_url
    if not source_url:
        return None

    title = _clean_title(chunk.title)
    if not title:
        return None

    body = _summarize_body(chunk.text)
    if not body:
        return None

    card_id = _card_id(chunk.section, source_url)
    followup_label, followup_prompt = _followup_actions(title, chunk.section)

    return StatementCard(
        id=card_id,
        title=title,
        body=body,
        sourceUrl=source_url,
        actions=[
            SourceAction(label="Open source"),
            FollowupAction(label=followup_label, prompt=followup_prompt),
        ],
    )


def _clean_title(title: str | None) -> str:
    if not title:
        return "Campus resource"

    cleaned = title.split("|", maxsplit=1)[0].strip()
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned or "Campus resource"


def _summarize_body(text: str) -> str:
    normalized = text.replace("\r", " ")
    lines: list[str] = []
    for line in normalized.split("\n"):
        cleaned = re.sub(r"^#+\s*", "", line).strip()
        if cleaned:
            lines.append(cleaned)

    normalized = _WHITESPACE_RE.sub(" ", " ".join(lines)).strip()
    if not normalized:
        return ""

    sentence_break = re.search(r"[.!?]\s", normalized)
    if sentence_break and sentence_break.start() < BODY_MAX_CHARS:
        candidate = normalized[: sentence_break.end()].strip()
    else:
        candidate = normalized

    if len(candidate) > BODY_MAX_CHARS:
        candidate = candidate[: BODY_MAX_CHARS - 1].rstrip() + "…"

    return candidate


def _card_id(section: str | None, source_url: str) -> str:
    path = urlparse(source_url).path.strip("/")
    slug = path.replace("/", "-").replace(".php", "").replace(".html", "")
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", slug).strip("-").lower()

    if section and slug:
        return f"{section}-{slug}"[:80]
    if slug:
        return slug[:80]
    if section:
        return section[:80]
    return "campus-resource"


def _followup_actions(title: str, section: str | None) -> tuple[str, str]:
    section_labels = {
        "peerconnections": ("Book tutor", "How do I book a Peer Connections tutor?"),
        "advising": ("Find advisor", "How do I find my academic advisor?"),
        "sjsucares": ("Get assistance", "How do I request assistance from SJSU Cares?"),
        "wellness": ("Same-day help", "How do I access same-day wellness support?"),
        "aec": ("Register AEC", "How do I register with the Accessible Education Center?"),
        "careercenter": ("Explore careers", "What Career Center services are available?"),
        "faso": ("Financial aid help", "How do I get help from Financial Aid and Scholarships?"),
    }

    if section:
        preset = section_labels.get(section.lower())
        if preset:
            return preset

    return ("Learn more", f"Tell me more about {title}.")


def create_statement_batch(
    cards: list[StatementCard],
    query: str,
) -> StatementBatch:
    return StatementBatch(
        id=f"batch-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
        cards=cards[:MAX_CARDS_PER_BATCH],
        query=query,
        createdAt=int(time.time() * 1000),
    )


def chunks_to_tool_results(chunks: list[RetrievedChunk]) -> list[dict[str, object]]:
    return [
        {
            "title": chunk.title,
            "sourceUrl": chunk.source_url,
            "section": chunk.section,
            "score": round(chunk.score, 4),
            "excerpt": chunk.text[:500],
        }
        for chunk in chunks
    ]


def cards_from_submission(
    submitted_cards: list[dict[str, object]],
    *,
    known_chunks: list[RetrievedChunk],
) -> list[StatementCard]:
    known_by_url = {
        chunk.source_url: chunk for chunk in known_chunks if chunk.source_url
    }
    cards: list[StatementCard] = []

    for item in submitted_cards[:MAX_CARDS_PER_BATCH]:
        title = str(item.get("title", "")).strip()
        body = str(item.get("body", "")).strip()
        source_url = str(item.get("sourceUrl", "")).strip()
        if not title or not body or not source_url:
            continue

        known = known_by_url.get(source_url)
        section = known.section if known else _section_from_url(source_url)
        card_id = _card_id(section, source_url)
        followup_label, followup_prompt = _followup_actions(title, section)

        cards.append(
            StatementCard(
                id=card_id,
                title=title,
                body=body,
                sourceUrl=source_url,
                actions=[
                    SourceAction(label="Open source"),
                    FollowupAction(label=followup_label, prompt=followup_prompt),
                ],
            )
        )

    return cards


def _section_from_url(source_url: str) -> str | None:
    path = urlparse(source_url).path.strip("/").split("/")
    return path[0].lower() if path else None

"""Card shaping - camp's behaviour, carried over unchanged.

Includes the section-to-preset check the build plan asks for: which of OUR crawl-list
section values actually reach a follow-up preset today. The mismatch itself is fixed in
its own later bullet, so this test RECORDS the current state rather than asserting a fix.
"""

import csv
from pathlib import Path

import cards
from retrieve import RetrievedChunk


def _chunk(**overrides):
    base = {
        "text": "Peer Connections offers free tutoring for enrolled students. Drop in any weekday.",
        "score": 0.9,
        "source_url": "https://www.sjsu.edu/peerconnections/index.php",
        "title": "Peer Connections | SJSU",
        "section": "peerconnections",
        "s3_uri": None,
    }
    base.update(overrides)
    return RetrievedChunk(**base)


def test_a_chunk_becomes_a_card_with_the_wire_contracts_field_names():
    card = cards.build_statement_cards([_chunk()], "tutoring")[0]
    body = card.model_dump(by_alias=True)
    assert body["sourceUrl"].startswith("https://")
    assert {"id", "title", "body", "sourceUrl", "actions"} <= set(body)


def test_a_chunk_without_a_source_url_is_dropped():
    """Grounding discipline: a card with no real source is not shown at all."""
    assert cards.build_statement_cards([_chunk(source_url=None)], "tutoring") == []


def test_the_body_is_trimmed_at_a_sentence_boundary():
    long_text = "First sentence here. " + ("padding " * 80)
    card = cards.build_statement_cards([_chunk(text=long_text)], "tutoring")[0]
    assert card.body == "First sentence here."


def test_the_body_is_capped_at_220_characters():
    card = cards.build_statement_cards([_chunk(text="word " * 200)], "tutoring")[0]
    assert len(card.body) <= cards.BODY_MAX_CHARS


def test_duplicate_urls_keep_the_highest_scoring_chunk():
    low = _chunk(score=0.4, text="Low scoring version of the page.")
    high = _chunk(score=0.95, text="High scoring version of the page.")
    result = cards.build_statement_cards([low, high], "tutoring")
    assert len(result) == 1
    assert "High scoring" in result[0].body


def test_noisy_sections_are_deprioritised_unless_the_query_asks_for_them():
    """`section` drives this, which is why the scraper must put it in every sidecar."""
    jobs = _chunk(section="jobs", source_url="https://www.sjsu.edu/careercenter/jobs.php")
    tutoring = _chunk()
    ordered = cards.build_statement_cards([jobs, tutoring], "tutoring")
    assert ordered[0].source_url == tutoring.source_url

    asked_for_jobs = cards.build_statement_cards([jobs, tutoring], "campus jobs")
    assert asked_for_jobs[0].source_url == jobs.source_url


def test_at_most_four_cards_per_batch():
    many = [
        _chunk(source_url=f"https://www.sjsu.edu/page{i}.php", score=0.9 - i / 100)
        for i in range(10)
    ]
    assert len(cards.build_statement_cards(many, "tutoring")) == cards.MAX_CARDS_PER_BATCH


def test_a_known_section_gets_its_tailored_followup():
    card = cards.build_statement_cards([_chunk(section="peerconnections")], "tutoring")[0]
    followup = [a for a in card.actions if a.type == "followup"][0]
    assert followup.label == "Book tutor"


def test_an_unknown_section_falls_back_to_a_generic_followup():
    card = cards.build_statement_cards([_chunk(section="counseling-psych")], "help")[0]
    followup = [a for a in card.actions if a.type == "followup"][0]
    assert followup.label == "Learn more"


def test_which_crawl_list_sections_reach_a_followup_preset_today():
    """RECORDED, NOT FIXED (docs/build-plan.md: the mismatch is its own later bullet).

    camp's presets are keyed on ITS section vocabulary; our crawl list uses a different
    one. This test pins the current overlap so the later bullet has a baseline and so an
    accidental drive-by edit to either vocabulary shows up as a failure here.
    """
    url_list = Path(__file__).resolve().parents[2] / "url-list.csv"
    with open(url_list, newline="", encoding="utf-8") as fh:
        our_sections = {row["section"].strip() for row in csv.DictReader(fh)}

    # The preset keys live inside _followup_actions; probe them through the public path
    # rather than reaching into the function's literal.
    matched = {
        section
        for section in our_sections
        if cards._followup_actions("Some Page", section)[0] != "Learn more"
    }

    assert matched == set(), (
        "expected NO crawl-list section to hit a preset today; if this fails the "
        f"vocabularies have started to overlap: {sorted(matched)}"
    )
    assert our_sections, "the crawl list must define sections at all"

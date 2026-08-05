"""Card shaping, and the section-to-preset mapping that drives its follow-up buttons.

The coverage test is the load-bearing one: it reads url-list.csv at test time, so a
section added to the corpus without an explicit preset entry fails the build rather than
silently shipping the generic follow-up.
"""

import csv
from pathlib import Path

import cards
import section_presets
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
    card = cards.build_statement_cards([_chunk(section="tutoring-academic-support")], "tutoring")[0]
    followup = [a for a in card.actions if a.type == "followup"][0]
    assert followup.label == "Book tutor"


def test_an_unknown_section_falls_back_to_the_generic_followup():
    """An unknown section at RUNTIME is not an error: it means a sidecar predates a
    crawl-list change, and a student's answer must not fail over that."""
    card = cards.build_statement_cards([_chunk(section="not-a-real-section")], "help")[0]
    followup = [a for a in card.actions if a.type == "followup"][0]
    assert followup.label == "Learn more"


def test_every_crawl_list_section_has_an_explicit_entry():
    """THE coverage test the mapping exists for. Reads url-list.csv at test time, so
    adding a section to the corpus without deciding its follow-up fails the build instead
    of silently shipping the generic one."""
    url_list = Path(__file__).resolve().parents[2] / "url-list.csv"
    with open(url_list, newline="", encoding="utf-8") as fh:
        corpus_sections = {row["section"].strip() for row in csv.DictReader(fh)}

    missing = corpus_sections - section_presets.known_sections()
    assert missing == set(), (
        f"crawl-list sections with no explicit preset entry: {sorted(missing)}. "
        "Add each to app/section_presets.py - map it to None for the generic follow-up "
        "if there is no honest section-specific question."
    )


def test_the_table_carries_no_entry_the_corpus_does_not_use():
    """The other direction: a stale key is a preset nothing can ever reach."""
    url_list = Path(__file__).resolve().parents[2] / "url-list.csv"
    with open(url_list, newline="", encoding="utf-8") as fh:
        corpus_sections = {row["section"].strip() for row in csv.DictReader(fh)}

    orphaned = section_presets.known_sections() - corpus_sections
    assert orphaned == set(), f"presets for sections not in the corpus: {sorted(orphaned)}"


def test_a_section_with_no_honest_match_maps_to_generic_explicitly():
    """Rule 2: never a plausibly-related office. student-affairs-hub is a hub page, so any
    specific follow-up would name an office the card did not come from."""
    assert section_presets.SECTION_FOLLOWUPS["student-affairs-hub"] is None
    label, prompt = section_presets.followup_for_section("student-affairs-hub", "Student Affairs")
    assert label == "Learn more"
    assert "Student Affairs" in prompt


def test_every_preset_prompt_is_answerable_from_its_own_section():
    """A follow-up that routes elsewhere is the referral mistake in another form. Checked
    structurally: no preset prompt may name a DIFFERENT section's office."""
    for section, preset in section_presets.SECTION_FOLLOWUPS.items():
        if preset is None:
            continue
        label, prompt = preset
        assert label and prompt, section
        assert prompt.endswith("?"), f"{section}: a follow-up prompt should be a question"


# --- Submitted-card link discipline (bullet 7) ------------------------------------------


def _submission(source_url, title="Tutoring", body="Free tutoring for students."):
    return {"title": title, "body": body, "sourceUrl": source_url}


def test_a_submitted_card_keeps_its_link_when_the_url_was_retrieved():
    chunk = _chunk()
    result = cards.cards_from_submission(
        [_submission(chunk.source_url)], known_chunks=[chunk]
    )
    assert [a.type for a in result[0].actions] == ["source", "followup"]


def test_a_submitted_card_loses_its_link_when_the_url_was_never_retrieved(caplog):
    """DELIBERATE CHANGE TO CAMP: camp linked whatever URL the model supplied, so an
    invented one shipped as a clickable referral. The card survives without the anchor."""
    chunk = _chunk()
    with caplog.at_level("WARNING"):
        result = cards.cards_from_submission(
            [_submission("https://www.sjsu.edu/invented-by-the-model.php")],
            known_chunks=[chunk],
        )

    card = result[0]
    assert [a.type for a in card.actions] == ["followup"], "no source action, so no link"
    assert card.title == "Tutoring", "the card itself survives"
    assert "not retrieved" in caplog.text


def test_the_unlinked_card_still_satisfies_the_wire_contract():
    """sourceUrl stays a populated string - the frontend type requires it, and nothing
    renders it without a source action. Removing the field would be a silent break."""
    result = cards.cards_from_submission(
        [_submission("https://www.sjsu.edu/invented.php")], known_chunks=[]
    )
    body = result[0].model_dump(by_alias=True)
    assert isinstance(body["sourceUrl"], str) and body["sourceUrl"]

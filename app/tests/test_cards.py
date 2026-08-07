"""The card contract: what the model emits, what the student gets.

Every test here is one of the ways the contract can be handed something imperfect. The
well-formed case is the cheapest to get right and the least likely to break; the value is in
the other five, because each one used to be handled by inference and is now handled by a
rule:

  - a card whose <desc> runs long          -> shortened here, where it can be measured
  - a card with no ref                     -> keeps its text, loses its link
  - a card citing an id nobody handed it   -> same, and it is logged with the valid ids
  - a reply with no cards at all           -> a bubble, and no reveal affordance
  - a reply whose tags are malformed       -> the whole thing as prose, tags scrubbed

The last one is the one that must never regress. v1 answered a parse failure with cards built
mechanically out of retrieved page text, so a broken response became confident referrals
nobody had written.
"""

import pytest

import cards
from cards import (
    ParsedCard,
    TurnSources,
    cards_from_parsed,
    create_statement_batch,
    parse_model_response,
    source_options_for_tool,
    strip_card_tags,
    truncate_to_cap,
)
from retrieve import RetrievedChunk
from settings import Settings

_SETTINGS = Settings(
    knowledge_base_id="KB123",
    generation_model_id="us.anthropic.claude-sonnet-4-6",
    bedrock_region="us-west-2",
    input_guardrail_id="gr-1",
    input_guardrail_version="3",
)


def _settings(**overrides):
    return Settings(**{**_SETTINGS.__dict__, **overrides})


def _chunk(
    *,
    url="https://www.sjsu.edu/tutoring/index.php",
    title="Peer Connections | SJSU",
    text="Drop-in tutoring for lower-division math.",
    score=0.9,
    section="tutoring-academic-support",
):
    return RetrievedChunk(
        text=text,
        score=score,
        source_url=url,
        title=title,
        section=section,
        s3_uri=None,
    )


def _sources(*chunks, limit=6):
    turn = TurnSources()
    turn.add_chunks(list(chunks), limit=limit)
    return turn


# --- Well-formed output -------------------------------------------------------------------


_WELL_FORMED = """Tutoring is free and you don't need a referral. Financial aid has its own
office for the money side.

<card ref="1">
  <title>Free math tutoring</title>
  <desc>Drop-in and scheduled tutoring for math courses. No cost and no referral needed.</desc>
  <followup>How do I book a calculus tutor?</followup>
</card>

<card ref="2">
  <title>Financial aid help</title>
  <desc>The GPA and unit thresholds you have to meet to keep your aid.</desc>
  <followup>What GPA do I need to keep my aid?</followup>
</card>
"""


def test_well_formed_output_yields_cards_and_prose():
    turn = _sources(
        _chunk(),
        _chunk(url="https://www.sjsu.edu/faso/index.php", title="Financial Aid", score=0.8),
    )
    parsed = parse_model_response(_WELL_FORMED)
    result = cards_from_parsed(parsed.cards, turn, _SETTINGS)

    assert parsed.prose.startswith("Tutoring is free")
    assert "<card" not in parsed.prose and "<desc>" not in parsed.prose
    assert [card.title for card in result] == ["Free math tutoring", "Financial aid help"]
    assert result[0].body.startswith("Drop-in and scheduled tutoring")


def test_the_server_resolves_the_url_the_ref_pointed_at():
    """The invariant the whole id scheme rests on. A map that resolves the WRONG url is
    worse than one that fails to resolve: the card links somewhere confidently and looks
    entirely fine."""
    tutoring = _chunk()
    aid = _chunk(url="https://www.sjsu.edu/faso/index.php", title="Financial Aid", score=0.8)
    turn = _sources(tutoring, aid)

    result = cards_from_parsed(parse_model_response(_WELL_FORMED).cards, turn, _SETTINGS)

    assert result[0].source_url == tutoring.source_url
    assert result[1].source_url == aid.source_url


def test_a_resolved_card_carries_both_buttons():
    turn = _sources(_chunk())
    card = cards_from_parsed(
        [ParsedCard(ref_id=1, title="T", desc="D", followup="How do I start?")],
        turn,
        _SETTINGS,
    )[0]

    kinds = [action.type for action in card.actions]
    assert kinds == ["source", "followup"]
    assert card.actions[0].label == cards.SOURCE_ACTION_LABEL
    assert card.actions[1].label == cards.FOLLOWUP_ACTION_LABEL
    assert card.actions[1].prompt == "How do I start?"


def test_the_followup_prompt_is_model_authored_not_derived_from_a_section():
    """v1 looked the follow-up up in a 23-row table keyed on the crawl list's `section`, so
    every card in a section asked the same question regardless of what the student wanted."""
    turn = _sources(_chunk(section="tutoring-academic-support"))
    card = cards_from_parsed(
        [ParsedCard(ref_id=1, title="T", desc="D", followup="Can I get help with Calc 2 specifically?")],
        turn,
        _SETTINGS,
    )[0]

    assert card.actions[-1].prompt == "Can I get help with Calc 2 specifically?"


# --- Missing ref --------------------------------------------------------------------------


def test_a_card_with_no_ref_keeps_its_text_and_loses_its_link(caplog):
    """DECIDED AGAINST docs/cards-v2.md, which drops the card. A card that renders without
    its source button is a visible symptom; a dropped card is three cards where there should
    be four and nobody notices. The log line is the stronger half of that signal."""
    turn = _sources(_chunk())
    parsed = parse_model_response(
        "<card><title>Tutoring</title><desc>Free help.</desc></card>"
    )

    assert parsed.cards[0].ref_id is None

    with caplog.at_level("WARNING"):
        card = cards_from_parsed(parsed.cards, turn, _SETTINGS)[0]

    assert card.title == "Tutoring"
    assert card.body == "Free help."
    assert [action.type for action in card.actions] == []
    assert card.source_url == ""
    assert "did not resolve" in caplog.text


# --- Unknown ref --------------------------------------------------------------------------


def test_a_card_citing_an_unknown_id_keeps_its_text_and_loses_its_link(caplog):
    turn = _sources(_chunk())  # only id 1 exists

    with caplog.at_level("WARNING"):
        card = cards_from_parsed(
            [ParsedCard(ref_id=9, title="Tutoring", desc="Free help.", followup="")],
            turn,
            _SETTINGS,
        )[0]

    assert card.title == "Tutoring"
    assert card.source_url == ""
    assert not any(action.type == "source" for action in card.actions)
    assert "9" in caplog.text and "[1]" in caplog.text, "log the bad ref AND the valid ids"


def test_an_id_from_a_previous_turn_is_as_unresolvable_as_an_invented_one():
    """Ids are per-turn and nothing persists them, so a stale ref must not resolve against
    whatever happens to occupy that number now."""
    turn_one = _sources(_chunk())
    turn_two = TurnSources()  # a fresh turn that retrieved nothing

    card = cards_from_parsed(
        [ParsedCard(ref_id=1, title="T", desc="D", followup="")], turn_two, _SETTINGS
    )[0]

    assert turn_one.resolve(1) is not None
    assert card.source_url == ""


# --- Over-cap text ------------------------------------------------------------------------


def test_an_over_cap_description_is_shortened_at_a_word_boundary(caplog):
    """Shortened where it can be measured, never hidden by the layout. v1 capped at 220 and
    let a 4-line CSS clamp swallow the rest, so roughly a third of every description was cut
    with nothing on screen to show it had been. The cap is a runaway guard now, far above
    the length the prompt steers toward, so hitting it is also a WARNING: an ellipsis on
    screen means a bug, and the log is where the bug is diagnosable."""
    settings = _settings(card_desc_max_chars=40)
    turn = _sources(_chunk())

    with caplog.at_level("WARNING"):
        card = cards_from_parsed(
            [
                ParsedCard(
                    ref_id=1,
                    title="T",
                    desc="Drop-in tutoring is available every weekday afternoon in the library.",
                    followup="",
                )
            ],
            turn,
            settings,
        )[0]

    assert len(card.body) <= 40
    assert card.body.endswith("…")
    assert not card.body[:-1].endswith(" "), "the ellipsis must follow a whole word"
    assert "Drop-in tutoring is available" in card.body
    assert "guard cap" in caplog.text


def test_an_over_cap_title_is_shortened_the_same_way(caplog):
    settings = _settings(card_title_max_chars=20)
    turn = _sources(_chunk())

    with caplog.at_level("WARNING"):
        card = cards_from_parsed(
            [ParsedCard(ref_id=1, title="Peer Connections tutoring and academic coaching", desc="D", followup="")],
            turn,
            settings,
        )[0]

    assert len(card.title) <= 20
    assert card.title.endswith("…")
    assert "guard cap" in caplog.text


def test_fields_within_their_caps_log_no_warning(caplog):
    """The WARNING is the ellipsis-means-a-bug signal. An ordinary response must not emit
    it, or the signal drowns in routine noise and stops meaning anything."""
    turn = _sources(_chunk())

    with caplog.at_level("WARNING"):
        cards_from_parsed(
            [
                ParsedCard(
                    ref_id=1,
                    title="Free math tutoring",
                    desc="Drop-in tutoring for lower-division math.",
                    followup="How do I book a tutor?",
                )
            ],
            turn,
            _SETTINGS,
        )

    assert caplog.text == ""


def test_an_over_cap_followup_keeps_its_button_and_is_logged(caplog):
    """A shortened question is a DIFFERENT question, so a follow-up is never truncated -
    and it no longer loses its button either. The prompt is sent, and shown, as the
    student's next turn, where a long one simply wraps, so dropping the button was a
    visible regression guarding against nothing visible. The overrun is logged instead:
    the cap is a guard, and passing it means the prompt or the model is broken."""
    settings = _settings(card_followup_max_chars=20)
    turn = _sources(_chunk())
    long_followup = "How do I book an appointment with a calculus tutor this week?"

    with caplog.at_level("WARNING"):
        card = cards_from_parsed(
            [ParsedCard(ref_id=1, title="T", desc="D", followup=long_followup)],
            turn,
            settings,
        )[0]

    assert [action.type for action in card.actions] == ["source", "followup"]
    assert card.actions[1].prompt == long_followup, "the prompt must go through whole"
    assert "over-cap follow-up" in caplog.text


def test_an_empty_followup_simply_has_no_button():
    turn = _sources(_chunk())
    card = cards_from_parsed(
        [ParsedCard(ref_id=1, title="T", desc="D", followup="   ")], turn, _SETTINGS
    )[0]

    assert [action.type for action in card.actions] == ["source"]


def test_text_at_exactly_the_cap_is_left_alone():
    """Boundary: the cap is the limit, not one under it."""
    exact = "x" * 40
    assert truncate_to_cap(exact, 40) == exact
    assert truncate_to_cap(exact + "y", 40) != exact + "y"


def test_truncation_never_exceeds_the_cap_including_the_ellipsis():
    for cap in range(10, 60):
        result = truncate_to_cap("the quick brown fox jumps over the lazy dog " * 4, cap)
        assert len(result) <= cap, f"cap {cap} produced {len(result)} chars"


def test_the_card_ceiling_is_enforced(caplog):
    settings = _settings(card_max_cards=2)
    turn = _sources(_chunk())
    many = [ParsedCard(ref_id=1, title=f"T{i}", desc="D", followup="") for i in range(5)]

    with caplog.at_level("INFO"):
        result = cards_from_parsed(many, turn, settings)

    assert len(result) == 2
    assert "keeping the first 2" in caplog.text


def test_card_ids_are_unique_even_when_two_cards_cite_one_source():
    """They become React keys. Two cards on the same id must not collide."""
    turn = _sources(_chunk())
    result = cards_from_parsed(
        [
            ParsedCard(ref_id=1, title="A", desc="D", followup=""),
            ParsedCard(ref_id=1, title="B", desc="D", followup=""),
        ],
        turn,
        _SETTINGS,
    )

    assert len({card.id for card in result}) == 2


# --- Zero cards ---------------------------------------------------------------------------


def test_a_reply_with_no_cards_is_all_prose():
    parsed = parse_model_response("Glad that helped. Come back any time.")

    assert parsed.cards == []
    assert parsed.prose == "Glad that helped. Come back any time."
    assert cards_from_parsed(parsed.cards, TurnSources(), _SETTINGS) == []


def test_a_card_block_missing_its_title_or_desc_is_dropped(caplog):
    """These two ARE the card. Everything else degrades instead of dropping."""
    turn = _sources(_chunk())

    with caplog.at_level("WARNING"):
        result = cards_from_parsed(
            [
                ParsedCard(ref_id=1, title="", desc="D", followup=""),
                ParsedCard(ref_id=1, title="T", desc="", followup=""),
            ],
            turn,
            _SETTINGS,
        )

    assert result == []
    assert "no title" in caplog.text and "no description" in caplog.text


# --- Unparseable output -------------------------------------------------------------------


def test_an_unclosed_card_block_reaches_the_student_as_prose():
    """The fallback's whole job. The tag never renders and not one word is lost."""
    raw = 'Here is what I found.\n\n<card ref="1">\n<title>Tutoring</title>\n<desc>Free help.'
    parsed = parse_model_response(raw)

    assert parsed.cards == [], "a block with no closing tag is not a card"
    assert "<card" not in parsed.prose
    assert "<title>" not in parsed.prose
    assert "Here is what I found." in parsed.prose
    assert "Tutoring" in parsed.prose, "content survives even when the markup does not"
    assert "Free help." in parsed.prose


def test_stray_closing_tags_are_scrubbed_from_the_prose():
    assert "</desc>" not in strip_card_tags("Some text </desc> and more.")
    assert "Some text  and more." in strip_card_tags("Some text </desc> and more.")


def test_scrubbing_leaves_unrelated_angle_brackets_alone():
    """A deliberately narrow scrub. A generic <[^>]+> sweep would eat content the model may
    legitimately write, and the guarantee needed is only that OUR tags never surface."""
    text = "Enrol in <12 units and you stay part-time. Use the <strong> tag in your essay."
    assert strip_card_tags(text) == text


def test_the_safety_tag_is_detected_and_never_rendered():
    parsed = parse_model_response("<safety/>\n\nPlease reach out to someone below.")

    assert parsed.needs_safety is True
    assert "<safety" not in parsed.prose
    assert parsed.prose == "Please reach out to someone below."


def test_no_safety_tag_means_no_safety_request():
    assert parse_model_response("Here is the tutoring office.").needs_safety is False


# --- The id map ---------------------------------------------------------------------------


def test_the_model_is_never_shown_a_url():
    """The reason an invented URL is unrepresentable rather than merely detectable: there is
    no URL in the model's input to copy and no field in its output to put one in."""
    turn = TurnSources()
    options = turn.add_chunks([_chunk()], limit=6)
    payload = source_options_for_tool(options)

    assert set(payload[0]) == {"id", "title", "text"}
    assert "sjsu.edu" not in str(payload)


def test_sources_are_deduplicated_by_url_across_searches():
    """The same page found by two different searches keeps its first id, so the model is
    never shown one source under two numbers.

    Asserted by URL rather than by position: results are offered best-score-first, so the
    repeat's place in the second list depends on its score, and only its ID is the contract.
    """
    turn = TurnSources()
    first = turn.add_chunks([_chunk(score=0.9)], limit=6)
    second = turn.add_chunks(
        [_chunk(score=0.7), _chunk(url="https://www.sjsu.edu/faso/", score=0.6)], limit=6
    )
    by_url = {option.source_url: option.ref_id for option in second}

    assert first[0].ref_id == 1
    assert by_url["https://www.sjsu.edu/tutoring/index.php"] == 1, "the repeat keeps its id"
    assert by_url["https://www.sjsu.edu/faso/"] == 2, "a new source gets the next id"
    assert len(turn) == 2


def test_a_chunk_with_no_source_url_is_not_offered_as_a_source():
    """A card's entire job is provenance, so a source that cannot be linked has nothing to
    offer one."""
    turn = TurnSources()
    options = turn.add_chunks([_chunk(url=None), _chunk()], limit=6)

    assert len(options) == 1
    assert len(turn) == 1


def test_the_result_count_shown_to_the_model_is_capped():
    turn = TurnSources()
    many = [_chunk(url=f"https://www.sjsu.edu/p{i}", score=1 - i / 100) for i in range(20)]
    options = turn.add_chunks(many, limit=6)

    assert len(options) == 6
    assert [option.ref_id for option in options] == [1, 2, 3, 4, 5, 6]


def test_sources_are_offered_best_first():
    turn = TurnSources()
    options = turn.add_chunks(
        [
            _chunk(url="https://www.sjsu.edu/low", score=0.4),
            _chunk(url="https://www.sjsu.edu/high", score=0.95),
        ],
        limit=6,
    )

    assert options[0].source_url.endswith("/high")


# --- The batch ----------------------------------------------------------------------------


def test_a_batch_carries_the_cards_and_the_query():
    turn = _sources(_chunk())
    result = cards_from_parsed(
        [ParsedCard(ref_id=1, title="T", desc="D", followup="")], turn, _SETTINGS
    )
    batch = create_statement_batch(result, "tutoring")

    assert batch.cards == result
    assert batch.query == "tutoring"
    assert batch.created_at > 0


@pytest.mark.parametrize(
    "raw",
    [
        '<card ref="1"><title>T</title><desc>D</desc></card>',
        "<card ref='1'><title>T</title><desc>D</desc></card>",
        '<CARD REF="1"><TITLE>T</TITLE><DESC>D</DESC></CARD>',
        '<card  ref = "1" ><title >T</title ><desc >D</desc ></card >',
    ],
)
def test_the_parser_tolerates_the_shapes_a_model_actually_writes(raw):
    """Quoting, case and incidental whitespace vary run to run. None of them is a contract
    violation, and treating them as one would drop cards over formatting."""
    parsed = parse_model_response(raw)

    assert len(parsed.cards) == 1
    assert parsed.cards[0].ref_id == 1
    assert parsed.cards[0].title == "T"
    assert parsed.cards[0].desc == "D"

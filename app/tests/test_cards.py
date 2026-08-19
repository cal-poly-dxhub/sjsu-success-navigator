"""The card contract: what the model emits, and what the student gets.

The ref scheme, the runaway-guard caps and the one split point are in docs/cards-v2.md
and docs/chat-service.md, Cards and the tag contract.
"""

import logging

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
    title_model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    bedrock_region="us-west-2",
    input_guardrail_id="gr-1",
    input_guardrail_version="3",
    chat_history_table_name="chat-history-test",
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
    """The invariant the whole id scheme rests on: a map that resolves the WRONG url is worse
    than one that resolves nothing."""
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
    """v1 looked the follow-up up in a table keyed on `section`; now the model authors it."""
    turn = _sources(_chunk(section="tutoring-academic-support"))
    card = cards_from_parsed(
        [ParsedCard(ref_id=1, title="T", desc="D", followup="Can I get help with Calc 2 specifically?")],
        turn,
        _SETTINGS,
    )[0]

    assert card.actions[-1].prompt == "Can I get help with Calc 2 specifically?"


# --- Whitespace inside a card -------------------------------------------------------------


_BULLETED_DESC = """Sammy here.

<card ref="1">
  <title>Advising drop-ins</title>
  <desc>Two ways in, and both get you a version an advisor has read.
- **Email:** advising@sjsu.edu
- **Walk in:** Clark Hall 240, weekdays 9 to 4</desc>
  <followup>Which one is faster?</followup>
</card>"""


def test_a_description_keeps_the_line_breaks_the_model_wrote():
    """The whole point of this field's separate normalisation: the display parser keys on
    line starts, so a flattened description loses the list with nothing on screen to say so."""
    parsed = parse_model_response(_BULLETED_DESC)
    body = cards_from_parsed(parsed.cards, _sources(_chunk()), _SETTINGS)[0].body

    assert body.splitlines() == [
        "Two ways in, and both get you a version an advisor has read.",
        "- **Email:** advising@sjsu.edu",
        "- **Walk in:** Clark Hall 240, weekdays 9 to 4",
    ]


def test_a_description_still_loses_its_indentation():
    """Incidental whitespace is still incidental: indented bullets are formatted XML."""
    parsed = parse_model_response(
        "<card ref='1'>"
        "<desc>\n"
        "      What to bring:\n"
        "        -   Your ID\t\n"
        "        -   The form\n"
        "    </desc>"
        "<title>T</title>"
        "</card>"
    )

    assert parsed.cards[0].desc == "What to bring:\n- Your ID\n- The form"


def test_a_run_of_blank_lines_in_a_description_closes_up():
    """The same normalisation the prose either side of the cards already gets."""
    parsed = parse_model_response(
        '<card ref="1"><title>T</title><desc>First.\n\n\n\nSecond.</desc></card>'
    )

    assert parsed.cards[0].desc == "First.\n\nSecond."


def test_the_other_fields_are_still_collapsed_to_one_line():
    """Only <desc> changed: a title is a heading and a follow-up is one question."""
    parsed = parse_model_response(
        '<card ref="1">'
        "<title>Advising\n  drop-ins</title>"
        "<desc>D</desc>"
        "<followup>Which\n  one is faster?</followup>"
        "</card>"
    )

    assert parsed.cards[0].title == "Advising drop-ins"
    assert parsed.cards[0].followup == "Which one is faster?"


def test_a_bulleted_description_is_measured_and_capped_like_any_other(caplog):
    """The guard does not get weaker because the description happens to be a list."""
    settings = _settings(card_desc_max_chars=40)

    with caplog.at_level("WARNING"):
        card = cards_from_parsed(
            parse_model_response(_BULLETED_DESC).cards, _sources(_chunk()), settings
        )[0]

    assert len(card.body) <= 40
    assert card.body.endswith("…")
    assert "guard cap" in caplog.text


def test_a_bulleted_description_within_its_cap_is_not_truncated_or_logged(caplog):
    """The newlines count toward the cap, but the cap sits far above the steer."""
    with caplog.at_level("WARNING"):
        card = cards_from_parsed(
            parse_model_response(_BULLETED_DESC).cards, _sources(_chunk()), _SETTINGS
        )[0]

    assert "…" not in card.body
    assert caplog.text == ""


def test_truncating_a_multiline_field_leaves_no_bullet_marker_hanging():
    """A break is a word boundary like a space, so a hanging marker goes with the cut."""
    text = "Two ways in.\n- Email advising@sjsu.edu"

    assert truncate_to_cap(text, 20, keep_line_breaks=True) == "Two ways in…"


def test_truncation_still_respects_the_cap_when_line_breaks_are_kept():
    for cap in range(10, 60):
        result = truncate_to_cap(
            "a line of text\n- a bullet under it\n- and another one\n" * 3,
            cap,
            keep_line_breaks=True,
        )
        assert len(result) <= cap, f"cap {cap} produced {len(result)} chars"


# --- Where the cards sit in the reply -----------------------------------------------------


_PROSE_ON_BOTH_SIDES = """Two offices handle this, and they are below.

<card ref="1">
  <title>Free math tutoring</title>
  <desc>Drop-in and scheduled tutoring for math courses.</desc>
  <followup>How do I book a calculus tutor?</followup>
</card>

Does either of those sound like what you need?"""


def test_prose_before_and_after_the_cards_keeps_its_side():
    """The ordering this contract exists for: a closing question renders under its cards."""
    parsed = parse_model_response(_PROSE_ON_BOTH_SIDES)

    assert parsed.prose == "Two offices handle this, and they are below."
    assert parsed.trailing_prose == "Does either of those sound like what you need?"
    assert [card.title for card in parsed.cards] == ["Free math tutoring"]


def test_prose_between_two_cards_joins_the_lead():
    """The grid is one group, so there is no inside for prose to render in."""
    parsed = parse_model_response(
        "Start here.\n\n"
        '<card ref="1"><title>A</title><desc>D</desc></card>\n\n'
        "And if that is not it:\n\n"
        '<card ref="2"><title>B</title><desc>D</desc></card>\n\n'
        "Which one fits?"
    )

    assert parsed.prose == "Start here.\n\nAnd if that is not it:"
    assert parsed.trailing_prose == "Which one fits?"


def test_a_reply_that_ends_with_its_cards_has_no_trailing_prose():
    """The ordinary shape: nothing after the last block means nothing renders below the grid."""
    parsed = parse_model_response(_WELL_FORMED)
    assert parsed.trailing_prose == ""


def test_a_reply_with_no_cards_is_never_split():
    parsed = parse_model_response("Glad that helped. Come back any time.")
    assert parsed.trailing_prose == ""


def test_an_unclosed_block_leaves_the_reply_unsplit():
    """No card parsed means no grid, so there is no position to preserve."""
    parsed = parse_model_response(
        'Here is what I found.\n\n<card ref="1"><title>T</title><desc>D.\n\nAnything else?'
    )

    assert parsed.cards == []
    assert parsed.trailing_prose == ""
    assert "Anything else?" in parsed.prose


def test_a_safety_tag_under_the_cards_still_fires():
    """Read from both sides of the split: losing a tag to its position would cost the panel."""
    parsed = parse_model_response(
        '<card ref="1"><title>T</title><desc>D</desc></card>\n\n<safety>crisis-988</safety>'
    )

    assert parsed.safety_keys == ("crisis-988",)
    assert "crisis-988" not in parsed.trailing_prose


def test_stray_tags_in_trailing_prose_are_scrubbed():
    parsed = parse_model_response(
        '<card ref="1"><title>T</title><desc>D</desc></card>\n\nAnything else? </desc>'
    )
    assert "</desc>" not in parsed.trailing_prose
    assert "Anything else?" in parsed.trailing_prose


def test_joining_a_split_reply_puts_a_blank_line_between_the_halves():
    assert cards.join_prose("Above.", "Below.") == "Above.\n\nBelow."
    assert cards.join_prose("Above.", "") == "Above."
    assert cards.join_prose("", "  ", None) == ""


# --- Missing ref --------------------------------------------------------------------------


def test_a_card_with_no_ref_keeps_its_text_and_loses_its_link(caplog):
    """DECIDED AGAINST docs/cards-v2.md, which drops the card, for observability."""
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
    """Ids are per-turn and nothing persists them, so a stale ref must not resolve."""
    turn_one = _sources(_chunk())
    turn_two = TurnSources()  # a fresh turn that retrieved nothing

    card = cards_from_parsed(
        [ParsedCard(ref_id=1, title="T", desc="D", followup="")], turn_two, _SETTINGS
    )[0]

    assert turn_one.resolve(1) is not None
    assert card.source_url == ""


# --- Over-cap text ------------------------------------------------------------------------


def test_an_over_cap_description_is_shortened_at_a_word_boundary(caplog):
    """Shortened where it can be measured, never hidden by the layout: v1 capped at 220 and
    a CSS clamp swallowed the rest with nothing on screen to show it."""
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
    """The WARNING is the ellipsis-means-a-bug signal, so an ordinary response must not emit one."""
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
    """A shortened question is a DIFFERENT question, so a follow-up is never truncated and
    keeps its button; the overrun is logged because the cap guards a paid model input."""
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


# --- Dash normalisation ---------------------------------------------------------------------

# The dashes under test are written as escapes, so grepping the repo for one stays meaningful.
_EM = "\u2014"
_EN = "\u2013"


def test_an_em_dash_in_a_card_becomes_a_comma():
    """The prompt bans em and en dashes; this is the deterministic backstop."""
    parsed = parse_model_response(
        '<card ref="1">'
        f"<title>Tutoring {_EM} free</title>"
        f"<desc>Drop-in help {_EM} no referral needed {_EM} for lower-division math.</desc>"
        "<followup>How do I book?</followup>"
        "</card>"
    )
    result = cards_from_parsed(parsed.cards, _sources(_chunk()), _SETTINGS)

    assert result[0].title == "Tutoring, free"
    assert result[0].body == "Drop-in help, no referral needed, for lower-division math."


def test_trailing_prose_dashes_are_normalised_like_the_lead():
    """The prose under the cards goes through the same display path as the prose above it."""
    parsed = parse_model_response(
        '<card ref="1"><title>T</title><desc>D</desc></card>\n\n'
        f"Want the hours {_EM} or the location?"
    )
    assert parsed.trailing_prose == "Want the hours, or the location?"


def test_prose_dashes_are_normalised_like_card_text():
    assert (
        strip_card_tags(f"One office {_EM} SJSU Cares {_EM} handles this.")
        == "One office, SJSU Cares, handles this."
    )


def test_an_en_dash_between_digits_becomes_a_hyphen():
    """The one place an en dash carries meaning a comma would destroy: a digit range."""
    assert cards.normalise_dashes(f"Open 10{_EN}12 on weekdays.") == "Open 10-12 on weekdays."


def test_ascii_hyphens_pass_through_untouched():
    assert (
        cards.normalise_dashes("drop-in tutoring, call 408-924-5678")
        == "drop-in tutoring, call 408-924-5678"
    )


def test_dashes_are_normalised_before_the_cap_is_measured():
    """The cap must measure the string the student reads, not the one the model typed."""
    desc = ("a" * 10) + f" {_EM} " + ("b" * 9)  # 22 chars as written, 21 once rewritten
    parsed = [ParsedCard(ref_id=1, title="T", desc=desc, followup="")]

    result = cards_from_parsed(parsed, _sources(_chunk()), _settings(card_desc_max_chars=21))

    assert result[0].body == ("a" * 10) + ", " + ("b" * 9)
    assert "…" not in result[0].body


def test_each_dash_substitution_is_logged(caplog):
    """The INFO line per substitution is how the dash rate stays measurable from the logs."""
    with caplog.at_level("INFO"):
        cards.normalise_dashes(f"stress {_EM} sleep {_EN} both")

    assert caplog.text.count("Normalised") == 2


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
    """A deliberately narrow scrub: a generic sweep would eat content the model may write."""
    text = "Enrol in <12 units and you stay part-time. Use the <strong> tag in your essay."
    assert strip_card_tags(text) == text


def test_the_safety_tag_is_detected_and_never_rendered():
    parsed = parse_model_response("<safety/>\n\nPlease reach out to someone below.")

    assert parsed.needs_safety is True
    assert parsed.safety_keys == ()
    assert "<safety" not in parsed.prose
    assert parsed.prose == "Please reach out to someone below."


def test_no_safety_tag_means_no_safety_request():
    parsed = parse_model_response("Here is the tutoring office.")
    assert parsed.needs_safety is False
    assert parsed.safety_keys is None


def test_a_keyed_safety_block_yields_its_keys_and_leaks_nothing_into_the_prose():
    """The keys are instructions to the server, so the WHOLE block leaves the prose."""
    parsed = parse_model_response(
        "You're not alone, and help is close.\n\n<safety>crisis-988, caps</safety>"
    )

    assert parsed.safety_keys == ("crisis-988", "caps")
    assert parsed.prose == "You're not alone, and help is close."
    assert "crisis-988" not in parsed.prose


def test_safety_keys_survive_odd_spacing_case_and_separators():
    parsed = parse_model_response("<safety>\n  SAS,  crisis-988\n</safety>\n\nHelp is here.")
    assert parsed.safety_keys == ("sas", "crisis-988")


def test_a_keyed_block_with_no_valid_tokens_is_a_bare_tag():
    parsed = parse_model_response("<safety> , </safety>\n\nHelp is below.")
    assert parsed.needs_safety is True
    assert parsed.safety_keys == ()


def test_the_fallback_scrub_removes_a_safety_block_whole():
    """The zero-card fallback rebuilds the bubble from the raw reply, so it must drop it too."""
    text = "Support is below.\n\n<safety>sas</safety>"
    assert strip_card_tags(text) == "Support is below."


# --- The place block ----------------------------------------------------------------------


def test_a_place_block_yields_its_key_and_leaks_nothing_into_the_prose():
    """A catalogue key is addressed to the server, exactly as a safety key is."""
    parsed = parse_model_response(
        "The Career Center does this all day.\n\n<place>career-center</place>"
    )

    assert parsed.place_key == "career-center"
    assert parsed.prose == "The Career Center does this all day."
    assert "career-center" not in parsed.prose


def test_a_place_key_survives_odd_spacing_and_case():
    parsed = parse_model_response("<place>\n  Career-Center\n</place>\n\nGo early.")
    assert parsed.place_key == "career-center"


def test_no_place_block_means_no_key():
    parsed = parse_model_response("Here is the tutoring office.")
    assert parsed.place_key is None


def test_a_place_block_under_the_cards_still_counts():
    """Both sides of the split are read: the split point says where prose renders, not which
    tags count."""
    parsed = parse_model_response(
        '<card ref="1"><title>t</title><desc>d</desc></card>\n\n'
        "<place>spartan-food-pantry</place>"
    )
    assert parsed.place_key == "spartan-food-pantry"


def test_a_second_place_block_is_ignored_and_logged(caplog):
    """One turn points at one place; choosing between two would be an editorial rule."""
    with caplog.at_level(logging.WARNING, logger="cards"):
        parsed = parse_model_response(
            "<place>career-center</place>\n\n<place>writing-center</place>"
        )
    assert parsed.place_key == "career-center"
    assert "2 place blocks" in caplog.text


def test_a_place_block_with_two_keys_keeps_the_first_and_logs(caplog):
    """Unlike a safety block, which lists every resource that fits, a card has one address."""
    with caplog.at_level(logging.WARNING, logger="cards"):
        parsed = parse_model_response("<place>career-center, writing-center</place>")
    assert parsed.place_key == "career-center"
    assert "2 keys" in caplog.text


def test_an_empty_place_block_is_logged_rather_than_treated_as_absent(caplog):
    """Reaching for the contract and writing nothing usable is a different fault from absence."""
    with caplog.at_level(logging.WARNING, logger="cards"):
        parsed = parse_model_response("<place>  </place>\n\nHere you go.")
    assert parsed.place_key is None
    assert "empty place block" in caplog.text.lower()


def test_the_fallback_scrub_removes_a_place_block_whole():
    """The zero-card fallback rebuilds from the raw reply, so it has to drop the key too."""
    assert strip_card_tags("Go here.\n\n<place>career-center</place>") == "Go here."


def test_the_preview_stops_at_a_place_tag():
    """The streamed preview must never type a tag onto the screen, `place` included."""
    assert cards.preview_safe_prefix("Head to Clark Hall. <place>career") == (
        "Head to Clark Hall. "
    )


# --- The id map ---------------------------------------------------------------------------


def test_the_model_is_never_shown_a_url():
    """The reason an invented URL is unrepresentable rather than merely detectable."""
    turn = TurnSources()
    options = turn.add_chunks([_chunk()], limit=6)
    payload = source_options_for_tool(options)

    assert set(payload[0]) == {"id", "title", "text"}
    assert "sjsu.edu" not in str(payload)


def test_sources_are_deduplicated_by_url_across_searches():
    """A page found by two searches keeps its first id, so one page is never two numbers."""
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
    """A card's entire job is provenance, so an unlinkable source has nothing to offer one."""
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
    """Quoting, case and incidental whitespace vary run to run, and none is a contract break."""
    parsed = parse_model_response(raw)

    assert len(parsed.cards) == 1
    assert parsed.cards[0].ref_id == 1
    assert parsed.cards[0].title == "T"
    assert parsed.cards[0].desc == "D"


# --- preview_safe_prefix: a stop rule, not a parser ---------------------------------------


def test_the_preview_is_everything_before_the_first_card_tag():
    """The model writes one text stream, so this is what makes a streamed preview safe."""
    text = 'Two places can help.\n\n<card ref="1"><title>Writing Center</title></card>'
    assert cards.preview_safe_prefix(text) == "Two places can help.\n\n"


def test_a_partial_tag_at_the_end_of_a_chunk_is_held_back():
    """Deltas straddle tag boundaries constantly, and an emitted `<ca` cannot be taken back."""
    for partial in ("<", "<c", "<ca", "<car", "<card", "<card ref=", "</", "</ca"):
        assert cards.preview_safe_prefix(f"Try this. {partial}") == "Try this. "


def test_a_less_than_sign_that_is_not_ours_streams_intact():
    """The guarantee needed is only that OUR tags never surface."""
    for text in ("Take <15 units to stay part time.", "a < b", "5<6 and 7>6"):
        assert cards.preview_safe_prefix(text) == text


def test_every_tag_in_the_contract_stops_the_preview():
    """One vocabulary, shared with the scrub: an unknown tag would reach a student raw."""
    for tag in ("card", "title", "desc", "followup", "safety", "escalate_to_human"):
        assert cards.preview_safe_prefix(f"prose <{tag}>x</{tag}>") == "prose "
        assert cards.preview_safe_prefix(f"prose </{tag}>") == "prose "
    assert cards.preview_safe_prefix("prose <SAFETY>crisis-988</SAFETY>") == "prose "


def test_the_prefix_only_ever_grows_as_the_reply_does():
    """Append-only is what makes it safe to type out: every push is a suffix."""
    reply = 'Here is the answer.\n\n<card ref="1"><title>T</title></card>\n\nAnything else?'
    seen = ""
    for length in range(len(reply) + 1):
        prefix = cards.preview_safe_prefix(reply[:length])
    # Each prefix EXTENDS the last: never shorter, never a rewrite of what was sent.
        assert prefix.startswith(seen), (length, prefix, seen)
        seen = prefix


# --- card_block_started: the one honest answer to "are cards coming?" ---------------------


def test_a_card_opening_is_what_says_cards_are_coming():
    """The lead-in ends where the first card block begins."""
    assert cards.card_block_started('Two places.\n\n<card ref="2">')
    assert cards.card_block_started("Two places.\n\n<CARD REF='2'>")


def test_prose_alone_never_says_cards_are_coming():
    """About one reply in ten is prose only, and a false promise is worse than silence."""
    for text in ("", "The deadline is 4pm.", "Take <15 units to stay part time.", "a < b"):
        assert not cards.card_block_started(text)


def test_a_partial_opening_is_not_an_answer_yet():
    """The preview stops on a partial; this waits for the whole opening."""
    for partial in ("Try this. <", "Try this. <c", "Try this. <ca", "Try this. <car"):
        assert not cards.card_block_started(partial)
    assert cards.card_block_started("Try this. <card")


def test_another_tag_of_the_contract_is_not_a_card():
    """The other tags stop the preview too, and none of them puts a card group on screen."""
    for tag in ("title", "desc", "followup", "escalate_to_human"):
        assert not cards.card_block_started(f"prose <{tag}>x</{tag}>")


def test_a_safety_tag_takes_it_back_to_no():
    """Safety turns drop their cards by contract, so that tag has no card group to announce."""
    assert not cards.card_block_started("Please call now.\n\n<safety>caps</safety>")
    assert not cards.card_block_started(
        'Please call now.\n\n<safety>caps</safety>\n<card ref="1">'
    )


def test_it_never_takes_back_an_answer_as_the_cards_are_written():
    """Once true it stays true for the rest of the blocks, so a caller can send one frame."""
    reply = 'Here.\n\n<card ref="1"><title>T</title></card>\n<card ref="2"></card>'
    first = next(
        length for length in range(len(reply) + 1) if cards.card_block_started(reply[:length])
    )
    assert all(cards.card_block_started(reply[:length]) for length in range(first, len(reply) + 1))


def test_the_preview_does_not_cap_or_normalise_anything():
    """Both belong to the finished reply: a substitution here could rewrite text on screen."""
    long_text = "x" * 5000
    assert cards.preview_safe_prefix(long_text) == long_text
    assert cards.preview_safe_prefix("a — b") == "a — b"


# --- the escalate-to-human block: prose only, so a chosen recipient is unrepresentable ----


def test_the_escalation_block_is_parsed_and_never_rendered():
    parsed = parse_model_response(
        "That one needs a person.\n\n"
        "<escalate_to_human>Hi, I need help with a registration hold.</escalate_to_human>"
    )

    assert parsed.escalation_prose == "Hi, I need help with a registration hold."
    assert parsed.prose == "That one needs a person."
    assert "escalate_to_human" not in parsed.prose
    assert "registration hold" not in parsed.prose, (
        "the block's content is a draft email, not a second copy of the bubble"
    )


def test_no_escalation_tag_is_none_rather_than_an_empty_offer():
    """None and "" are different states: no tag at all, versus a tag the model left empty."""
    assert parse_model_response("Just an answer.").escalation_prose is None
    assert parse_model_response(
        "<escalate_to_human>  </escalate_to_human>"
    ).escalation_prose == ""


def test_an_escalation_block_under_the_cards_still_counts():
    """Read from BOTH sides of the split, like the safety tag."""
    raw = (
        "Here is what I found.\n\n"
        '<card ref="1"><title>Registrar</title><desc>Holds and enrolment.</desc></card>\n\n'
        "<escalate_to_human>Hi, could someone look at my hold?</escalate_to_human>"
    )

    parsed = parse_model_response(raw)

    assert parsed.escalation_prose == "Hi, could someone look at my hold?"
    assert parsed.trailing_prose == ""
    assert len(parsed.cards) == 1


def test_a_second_escalation_block_is_ignored_and_logged(caplog):
    """One turn, one offer: merging or preferring would put an editorial rule in a parser."""
    raw = (
        "<escalate_to_human>The first draft.</escalate_to_human>\n\n"
        "<escalate_to_human>The second draft.</escalate_to_human>"
    )

    with caplog.at_level(logging.WARNING):
        parsed = parse_model_response(raw)

    assert parsed.escalation_prose == "The first draft."
    assert "2 escalate_to_human blocks" in caplog.text
    assert "The second draft." not in parsed.prose


def test_the_escalation_block_keeps_its_paragraphs():
    """A draft is read by a person, so a paragraph break the model wrote is one they see."""
    raw = (
        "<escalate_to_human>Hi,\n\nI have tried the advising page and the form.\n\n"
        "Could someone help?</escalate_to_human>"
    )

    assert parse_model_response(raw).escalation_prose == (
        "Hi,\n\nI have tried the advising page and the form.\n\nCould someone help?"
    )


def test_a_dash_in_a_draft_is_normalised_like_every_other_display_string():
    """The draft is text a member of staff reads, so it obeys the same display invariant."""
    parsed = parse_model_response(
        "<escalate_to_human>I am stuck — could someone help?</escalate_to_human>"
    )

    assert "—" not in parsed.escalation_prose
    assert parsed.escalation_prose == "I am stuck, could someone help?"


def test_the_fallback_scrub_removes_an_escalation_block_whole():
    """The zero-card fallback rebuilds from the COMPLETE reply, so an unscrubbed block
    would put an unsent email in front of the student."""
    raw = (
        "Sorry, I do not have a page for that.\n"
        "<escalate_to_human>Hi, I am trying to find out about a fee.</escalate_to_human>"
    )

    scrubbed = strip_card_tags(raw)

    assert scrubbed == "Sorry, I do not have a page for that."

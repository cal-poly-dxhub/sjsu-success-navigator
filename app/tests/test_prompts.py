"""The system prompt: the caps in it are the caps the server applies, and the examples obey
them.

What each section is shaped by is in docs/chat-service.md, The system prompt.
"""

import re

from prompts import build_system_prompt
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

_FIELD_RE = {
    name: re.compile(rf"<{name}>(.*?)</{name}>", re.DOTALL)
    for name in ("title", "desc", "followup")
}


def _settings(**overrides):
    return Settings(**{**_SETTINGS.__dict__, **overrides})


_EXAMPLE_BLOCK_RE = re.compile(r"<example>(.*?)</example>", re.DOTALL)
# Mirrors cards._CARD_BLOCK_RE, kept local so this module imports nothing that reaches boto3.
_CARD_BLOCK_RE = re.compile(r"<card\b[^>]*>.*?</card\s*>", re.DOTALL)


def _examples(prompt: str, field: str) -> list[str]:
    """The field's text from every card inside the worked <example> blocks."""
    blocks = _EXAMPLE_BLOCK_RE.findall(prompt)
    assert blocks, "no <example> blocks found in the prompt"
    return [
        match.strip() for block in blocks for match in _FIELD_RE[field].findall(block)
    ]


def test_the_prompt_states_the_caps_it_was_built_with():
    """Not the default values - the values it was HANDED. This is the drift test."""
    prompt = build_system_prompt(
        _settings(
            card_max_cards=3,
            card_title_max_chars=41,
            card_desc_max_chars=317,
            card_followup_max_chars=99,
        )
    )

    assert "At most 3 cards" in prompt
    assert "<title> at most 41 characters. <desc> at most 317. <followup> at most 99." in prompt
    for stale in ("140", "300"):
        assert stale not in prompt, f"a stale literal cap ({stale}) survived in the prompt text"


def test_the_desc_cap_reaches_the_prompt_from_settings():
    """The cap that just moved. cards.py truncates to this same number."""
    assert "<desc> at most 600." in build_system_prompt(_SETTINGS)


def test_every_canonical_example_sits_under_its_cap():
    """The examples are the primary steer on length; one that overruns teaches the overrun."""
    prompt = build_system_prompt(_SETTINGS)

    for field, cap in (
        ("title", _SETTINGS.card_title_max_chars),
        ("desc", _SETTINGS.card_desc_max_chars),
        ("followup", _SETTINGS.card_followup_max_chars),
    ):
        examples = _examples(prompt, field)
        assert examples, f"no <{field}> examples found in the prompt"
        for text in examples:
            assert len(text) <= cap, f"<{field}> example is {len(text)} chars, cap {cap}: {text!r}"


def test_the_card_descriptions_in_the_examples_sit_in_the_stated_band():
    """The examples teach a LENGTH, and the stated target has to move with them."""
    descs = _examples(build_system_prompt(_SETTINGS), "desc")

    for desc in descs:
        assert len(desc) >= 110, f"a thin <desc> example undercuts the weighting: {desc!r}"
        assert len(desc) <= 200, (
            f"a <desc> example at {len(desc)} chars re-teaches the length this band replaced: "
            f"{desc!r}"
        )


def test_the_cards_carry_the_majority_of_each_worked_reply():
    """Shorter replies get shorter by moving text INTO the cards, not by thinning them."""
    for block in _EXAMPLE_BLOCK_RE.findall(build_system_prompt(_SETTINGS)):
        reply = block.split("[your reply]", 1)[1]
        card_text = sum(
            len(text) for field in ("title", "desc") for text in _FIELD_RE[field].findall(reply)
        )
        if not card_text:
            continue
        prose = len(_CARD_BLOCK_RE.sub("", reply).strip())
        assert card_text > prose, (
            f"an example puts more text in its prose ({prose}) than in its cards ({card_text})"
        )


def test_the_prompt_permits_all_four_marks_in_both_places():
    """Four marks, prose and <desc> alike, and the permission has to be explicit in both."""
    prompt = build_system_prompt(_SETTINGS)

    assert "Formatting:" in prompt
    assert "in the prose and inside a <desc> alike" in prompt
    assert "**Bold**" in prompt
    assert "*Italics*" in prompt
    assert 'each line starting with "- "' in prompt
    assert '"1. " or "1) "' in prompt


def test_the_permitted_syntax_is_the_display_parsers_and_not_markdowns():
    """A permission looser than the parser is how the student gets asterisks."""
    prompt = build_system_prompt(_SETTINGS)

    assert "Underscores are not italics" in prompt
    assert 'then "." or ")", then a space' in prompt
    # The single-asterisk form is the one a model can write as `* italics *` and lose.
    assert "no space between the asterisks and the words" in prompt


def test_the_prompt_bans_the_constructs_nothing_renders():
    """The load-bearing half: past the four marks, everything else reaches the student raw."""
    prompt = build_system_prompt(_SETTINGS)

    for banned in ("no headings", "no tables", "no images"):
        assert banned in prompt, f"the formatting ban dropped {banned!r}"
    assert "no links written as bracketed text with a URL after it" in prompt
    # And the ban that lifted stays lifted: numbered lists render now.
    assert "no numbered lists" not in prompt


def test_the_prompt_never_explains_its_own_display_to_the_student():
    """The renderer's reach is internal, and the old ban got recited to a student who asked."""
    prompt = build_system_prompt(_SETTINGS)

    assert "A word about how you are displayed." in prompt
    assert "not a sentence about what your display supports" in prompt


_BANNED_MARKUP = (
    ("a heading", re.compile(r"^\s{0,3}#{1,6}\s", re.MULTILINE)),
    ("a table row", re.compile(r"^\s*\|.*\|", re.MULTILINE)),
    ("an image", re.compile(r"!\[[^\]]*\]\(")),
    ("a typed link", re.compile(r"\[[^\]]+\]\([^)]*\)")),
)


def test_no_worked_example_uses_a_construct_the_prompt_bans():
    """Examples steer harder than instructions, so a ban an example contradicts is not a ban."""
    for block in _EXAMPLE_BLOCK_RE.findall(build_system_prompt(_SETTINGS)):
        for name, pattern in _BANNED_MARKUP:
            match = pattern.search(block)
            assert match is None, f"a worked example models {name}: {match.group(0)!r}"


def test_the_examples_model_bold_and_bullets_rather_than_only_permitting_them():
    """A construct in no example is used at whatever rate training suggests."""
    prompt = build_system_prompt(_SETTINGS)
    descs = _examples(prompt, "desc")

    assert any("**" in desc for desc in descs), "no example description models bold"
    bulleted = [desc for desc in descs if "\n- " in desc]
    assert bulleted, "no example description models a bulleted list"
    assert any("@" in desc for desc in bulleted), (
        "no example carries a contact in its bullets, which is what they are there for"
    )

    # And in the prose, which is the other half of "in both places".
    prose = [
        block.split("[your reply]", 1)[1]
        for block in _EXAMPLE_BLOCK_RE.findall(prompt)
        if "[your reply]" in block
    ]
    assert any("**" in _CARD_BLOCK_RE.sub("", part) for part in prose), (
        "no example models bold in the prose"
    )


def test_the_prompt_never_withholds_cards_because_a_turn_is_a_follow_up():
    """The other half of the follow-up fix: nothing withholds cards because of the widget."""
    prompt = build_system_prompt(_SETTINGS)

    assert "Card follow-up context" not in prompt
    assert "clicked a follow-up" not in prompt
    assert "Do not repeat cards" not in prompt
    # Retrieval turns on whether the answer needs a source, never on the turn's position.
    assert "narrow follow-ups" not in prompt
    assert "Decide by what the answer needs, not by where the question sits" in prompt
    # And says the positive thing, so a rewrite cannot quietly drift back.
    assert "A follow-up is a question like any other." in prompt


def test_the_prompt_states_the_order_the_reply_is_read_in():
    """The other half of the emitted-position change: the prompt has to state the order."""
    prompt = build_system_prompt(_SETTINGS)

    assert "a short lead-in, then the cards, then any question you want to ask" in prompt
    assert "Your reply renders in the order you wrote it" in prompt
    # And that the question is optional, so the order does not become a script.
    assert "a closing question is an option, not a habit" in prompt


def test_the_examples_model_the_order_rather_than_only_stating_it():
    """Examples steer harder than instructions, so the order has to be shown, not just said."""
    blocks = _EXAMPLE_BLOCK_RE.findall(build_system_prompt(_SETTINGS))
    carded = [block for block in blocks if "</card>" in block]
    assert carded, "no worked example emits a card"

    after_last_card = [block.rsplit("</card>", 1)[1].strip() for block in carded]
    assert any(after_last_card), "no example models prose written under the cards"
    assert not all(after_last_card), "no example models a reply that ends on its cards"

    # The lead-in is still there: an example opening with its cards would teach a bare grid.
    for block in carded:
        assert block.split("<card", 1)[0].strip(), "an example opens with its cards"


def test_the_prompt_draws_the_scope_line():
    """Sammy answers campus questions and declines everything else, corpus gaps included."""
    prompt = build_system_prompt(_SETTINGS)

    assert "Scope:" in prompt
    assert "give none of the requested content" in prompt
    # The worked example, because examples steer harder than instructions.
    assert "restaurant picks are outside my lane" in prompt


def test_the_safety_roster_in_the_prompt_is_the_resolvers_table():
    """Every key the model is taught resolves, because the roster IS the resolver's table."""
    import safety

    prompt = build_system_prompt(_SETTINGS)
    roster = safety.safety_roster_for_prompt()
    assert roster
    for key, when in roster:
        assert f"- {key}: when {when}" in prompt

    # The panel owns every number: the section teaching the keys must leak no contact detail.
    safety_section = prompt.split("Safety:")[1].split("Never:")[0]
    assert "408-924" not in safety_section
    assert "<safety>crisis-988, caps</safety>" in prompt


def test_the_prompt_tells_the_model_where_the_answer_goes():
    """The editorial division, asserted as presence rather than wording."""
    prompt = build_system_prompt(_SETTINGS)

    assert "What goes in a card and what goes in the prose" in prompt
    assert "The cards carry the answer. The prose introduces them." in prompt
    # The carve-out that keeps a zero-card turn from becoming a teaser above empty space.
    assert "When you emit no cards, the prose is the whole answer" in prompt


# --- the student's language -------------------------------------------------------------


def _language_section(prompt: str) -> str:
    """The Language block alone, so a carve-out asserted here cannot be satisfied elsewhere."""
    assert "Language:" in prompt
    return prompt.split("Language:")[1].split("What goes in a card")[0]


def test_the_reply_follows_the_language_of_the_latest_message():
    """Both halves, because following a mid-conversation switch does not follow from the first."""
    prompt = build_system_prompt(_SETTINGS)

    assert "Answer in the language of the student's most recent message." in prompt
    assert "the latest message decides, and it decides again each turn" in prompt


def test_the_language_rule_reaches_the_card_fields_by_name():
    """THE REASON THE SECTION EXISTS: a model follows the rule readily in prose and much
    less readily inside a <card> block, where the answer lives."""
    section = _language_section(build_system_prompt(_SETTINGS))

    for field in ("<title>", "<desc>", "<followup>"):
        assert field in section, f"the language rule does not name {field}"


def test_the_language_rule_carves_out_what_a_translation_would_break():
    """Three carve-outs and three distinct breakages, one of which the SERVER reads."""
    section = _language_section(build_system_prompt(_SETTINGS))

    assert "Phone numbers, email addresses, and web addresses, character for character." in section
    assert "The names of offices, buildings, programs and rooms" in section
    assert "the keys inside a <safety> block" in section


def test_the_safety_panel_does_not_move_with_the_language():
    """The panel's contents are the server's either way, so what this pins is the INSTRUCTION."""
    safety_section = build_system_prompt(_SETTINGS).split("Safety:")[1].split("Never:")[0]

    assert "word for word the same in every language" in safety_section
    assert "The keys you write stay in English." in safety_section


# --- the escalation section -----------------------------------------------------------


def test_the_escalation_section_is_absent_when_no_recipient_is_configured():
    """ABSENT, not disabled: teaching a tag whose output the server would drop spends tokens
    every turn to produce an offer no student can see."""
    prompt = build_system_prompt(_SETTINGS)

    assert "escalate_to_human" not in prompt
    assert "escalation" not in prompt.lower()


def test_the_escalation_section_appears_once_a_recipient_exists():
    prompt = build_system_prompt(_settings(escalation_recipient="sjsucares@sjsu.edu"))

    assert "<escalate_to_human>" in prompt
    assert "at most ONCE in a turn" in prompt


def test_the_prompt_never_names_the_recipient():
    """The model does not choose, or know, where a draft goes."""
    prompt = build_system_prompt(_settings(escalation_recipient="sjsucares@sjsu.edu"))

    assert "sjsucares@sjsu.edu" not in prompt


def test_the_escalation_cap_is_interpolated_rather_than_written_out():
    """The same one-value rule the card caps follow: told and enforced are one number."""
    prompt = build_system_prompt(
        _settings(escalation_recipient="a@b.edu", escalation_max_chars=1234)
    )

    assert "1234 characters" in prompt


def test_the_section_states_that_going_over_drops_the_offer():
    """This cap behaves the opposite way to every other cap the model is told about."""
    prompt = build_system_prompt(_settings(escalation_recipient="a@b.edu"))

    assert "dropped entirely rather than shortened" in prompt


def test_the_section_bans_an_offer_on_a_safety_turn():
    """Stated HERE, not with Safety's other exclusions, because this section is gated."""
    prompt = build_system_prompt(_settings(escalation_recipient="a@b.edu"))

    assert "Never offer it on a turn where you emit a safety block." in prompt


def test_the_section_names_all_three_triggers():
    """CAPABILITY, SENSITIVITY, AND BEING ASKED. The first shipped alone and was too narrow."""
    prompt = build_system_prompt(_settings(escalation_recipient="a@b.edu"))

    assert "already tried the destinations you gave them" in prompt
    assert "personal or high-stakes enough that a person should read it" in prompt
    assert "They ASK to talk to a person. Always offer then" in prompt


def test_the_draft_stays_english_when_the_reply_does_not():
    """The one piece of model prose that does not follow the student's language: its reader
    is staff, and a message nobody in that office can read waits."""
    prompt = build_system_prompt(_settings(escalation_recipient="a@b.edu"))

    assert "WRITE THE DRAFT IN ENGLISH" in prompt
    # And the student is not left holding a message they cannot read: the prose around it is
    # in their own language.
    assert "in THEIR language, what the draft says and who it goes to" in prompt


def test_the_english_draft_rule_goes_away_with_the_section():
    assert "WRITE THE DRAFT IN ENGLISH" not in build_system_prompt(_SETTINGS)


def test_asking_for_a_person_also_points_at_the_pill():
    """The one thing the model may name on screen, because the pill is a second way to a person."""
    prompt = build_system_prompt(_settings(escalation_recipient="a@b.edu"))

    assert '"Talk to a person" in the bottom right reaches SJSU Cares' in prompt
    # The display ban is unchanged for everything else, and the exception says so.
    assert "Do not describe the draft, how it opens, or what it looks like on screen" in prompt


def test_the_place_roster_in_the_prompt_is_the_resolvers_table():
    """The location keys are the model's whole vocabulary here, so the roster IS the table."""
    import places

    prompt = build_system_prompt(_SETTINGS)
    roster = places.place_roster_for_prompt()
    assert roster
    for key, when in roster:
        assert f"- {key}: {when}" in prompt


def test_the_prompt_shows_the_place_block_as_a_key_and_nothing_else():
    """The shape the model copies: an attribute in the sketch is how an invented address gets in."""
    prompt = build_system_prompt(_SETTINGS)
    assert "<place>career-center</place>" in prompt
    assert "no address, no room, no map link, no attributes" in prompt


def test_the_prompt_says_an_unlisted_place_gets_no_block():
    """THE LOAD-BEARING SENTENCE: a nearest-key guess resolves, renders, and is wrong."""
    prompt = build_system_prompt(_SETTINGS)
    assert "write no block at all" in prompt
    assert "Not the nearest key" in prompt


def test_the_safety_section_carries_every_safety_turn_exclusion():
    """One list, in the Safety section: a safety turn drops the cards AND the location panel."""
    safety_section = build_system_prompt(_SETTINGS).split("Safety:")[1].split("Never:")[0]

    assert "no cards and no location block" in safety_section


def test_the_place_keys_are_not_translated_with_the_rest_of_the_reply():
    """A translated key resolves to nothing, exactly as a translated safety key does."""
    section = _language_section(build_system_prompt(_SETTINGS))

    assert "the key inside a <place> block" in section
    assert "stay in English" in section


def test_the_prompt_never_writes_an_address_the_model_could_copy():
    """The roster is keys and one-line purposes, never addresses the model could copy."""
    import places

    prompt = build_system_prompt(_SETTINGS)
    for place in places.CAMPUS_PLACES.values():
        assert place.address not in prompt
        assert place.directions_destination not in prompt
        assert "google.com/maps" not in prompt


def test_the_campus_shorthand_glossary_is_read_from_the_data_file():
    """The glossary is interpolated from data/abbreviations.csv, not typed into the template."""
    from campus_data import load_rows
    from prompts import abbreviation_glossary

    rows = load_rows("abbreviations.csv", ("abbreviation", "expansion"))
    assert rows, "the committed glossary must not be empty"

    glossary = abbreviation_glossary()
    assert glossary.split("\n") == [f"- {r['abbreviation']}: {r['expansion']}" for r in rows]

    prompt = build_system_prompt(_SETTINGS)
    assert glossary in prompt
    # Every row reaches the model, in the file's order, under the heading that explains it.
    shorthand = prompt.split("Campus shorthand:")[1]
    assert shorthand.index(f"- {rows[0]['abbreviation']}:") < shorthand.index(
        f"- {rows[-1]['abbreviation']}:"
    )

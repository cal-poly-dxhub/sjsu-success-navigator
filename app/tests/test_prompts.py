"""The system prompt: the caps in it are the caps the server applies, and the examples obey.

Two things are worth a test here, and neither is about wording.

The caps are interpolated from Settings rather than written as literals, so the prompt cannot
drift from cards.py. A literal would not fail anything - the model would simply be briefed on
one budget while the server truncated to another, and the only symptom would be descriptions
quietly losing their tails. Asserting against a NON-default Settings is what makes that real:
a hardcoded 300 would pass a test that used the default and fail this one.

The canonical examples are the primary steer on length (a model copies a shape far more
reliably than it counts characters), which only holds while they actually sit under the caps.
An example that overruns teaches the shape the server then truncates.
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
    """The field's text from every card inside the worked <example> blocks.

    The shape sketch under "How your reply is read" describes what to write rather than
    being an example of it, so it is not held to the caps - it sits outside the <example>
    blocks, which is what scopes this to the examples the model actually copies.
    """
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
    """The examples are the primary steer on length; one that overruns teaches the shape the
    server then truncates."""
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
    """The examples teach a LENGTH, and it is the stated target that moves with them.

    A floor, because the weighting the prompt asks for is cards-hold-the-answer and a
    one-line description is the shape that weighting exists to move away from. A ceiling,
    because the guard cap is several times the target and therefore enforces nothing
    editorial: an example that drifts back to the old length re-teaches it while violating
    nothing. The band is one or two short sentences, plus a contact list on the card that
    has one, which is the outlier at the top of the range."""
    descs = _examples(build_system_prompt(_SETTINGS), "desc")

    for desc in descs:
        assert len(desc) >= 110, f"a thin <desc> example undercuts the weighting: {desc!r}"
        assert len(desc) <= 200, (
            f"a <desc> example at {len(desc)} chars re-teaches the length this band replaced: "
            f"{desc!r}"
        )


def test_the_cards_carry_the_majority_of_each_worked_reply():
    """Shorter replies must get shorter by moving text INTO the cards, not by thinning them.
    Measured on what the student actually reads: titles and descriptions against the prose on
    both sides of the grid. The follow-up is a hidden button prompt, so it is not in the
    count. Examples with no cards are the case where the prose is necessarily the whole
    answer, and they are excluded rather than failed."""
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
    """Four marks, prose and <desc> alike. The permission has to be explicit in both places:
    a model told only that the prose renders markup writes plain descriptions, and the
    descriptions are where most of the reply lives."""
    prompt = build_system_prompt(_SETTINGS)

    assert "Formatting:" in prompt
    assert "in the prose and inside a <desc> alike" in prompt
    assert "**Bold**" in prompt
    assert "*Italics*" in prompt
    assert 'each line starting with "- "' in prompt
    assert '"1. " or "1) "' in prompt


def test_the_permitted_syntax_is_the_display_parsers_and_not_markdowns():
    """A permission looser than the parser is how a model is told a mark works and the student
    gets asterisks. Two places where markdown and messageFormat.ts disagree, so two pins:
    `_underscores_` are not italics there (the prose carries email local parts and snake_case
    ids, where the underscores are the text), and a numbered line counts only with its
    separator AND the space after it - "1." alone is the characters the model typed."""
    prompt = build_system_prompt(_SETTINGS)

    assert "Underscores are not italics" in prompt
    assert 'then "." or ")", then a space' in prompt
    # The single-asterisk form is the one a model can write as `* italics *` and lose.
    assert "no space between the asterisks and the words" in prompt


def test_the_prompt_bans_the_constructs_nothing_renders():
    """The load-bearing half, and the half that did not move: past the four marks, everything
    reaches the student as the literal characters typed. A typed link is worse than unrendered
    - it is a destination nobody resolved, which is the failure the card ref contract exists
    to make unrepresentable."""
    prompt = build_system_prompt(_SETTINGS)

    for banned in ("no headings", "no tables", "no images"):
        assert banned in prompt, f"the formatting ban dropped {banned!r}"
    assert "no links written as bracketed text with a URL after it" in prompt
    # And the ban that lifted stays lifted: numbered lists render now.
    assert "no numbered lists" not in prompt


def test_the_prompt_never_explains_its_own_display_to_the_student():
    """The renderer's reach is internal. A student who asks for italics wants italics, and the
    live site used to answer that request by reciting the prompt's own two-mark ban back at
    them ("isn't something my display supports"), which is a sentence about machinery wearing
    a formatting hat."""
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
    """Examples steer harder than instructions, so a ban contradicted by an example is a ban
    the model will break. Scoped to the <example> blocks: the prose OUTSIDE them describes
    the contract to the model and is not text it copies."""
    for block in _EXAMPLE_BLOCK_RE.findall(build_system_prompt(_SETTINGS)):
        for name, pattern in _BANNED_MARKUP:
            match = pattern.search(block)
            assert match is None, f"a worked example models {name}: {match.group(0)!r}"


def test_the_examples_model_bold_and_bullets_rather_than_only_permitting_them():
    """A construct that appears in no example is one the model uses at whatever rate its
    training suggests. Bullets are modelled where they earn their place - the contact band of
    a routing card, which is the half of the answer the 2026-08-10 eval kept losing.

    Two of the four marks are modelled and two are only permitted, deliberately. A numbered
    list earns its place on a process answer and none of the five worked examples is one, so
    carrying one would mean a sixth example - growth in the file the examples were shortened
    for. Not asserted as an absence either: an example added later that IS a sequence should
    number it."""
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
    """The other half of the follow-up fix. The request path stopped injecting the
    suppression (test_orchestrator), and the system prompt must not restate it: a section
    telling the model to answer a click narrowly, or to skip cards it thinks the student
    already has, reproduces the bug with the wire flag untouched.

    "Do not repeat cards the student already has" is the subtler one and is gone for a
    reason worth keeping written down: history carries prose only, so the model cannot see
    which cards were shown, and an instruction it has no way to evaluate degrades into
    emitting nothing.
    """
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
    """The other half of the emitted-position change. The server now renders prose written
    after the cards BELOW them, so the prompt has to say where a closing question goes -
    otherwise the model keeps putting it above the answer it asks about, which is what it
    read like while the whole reply was one bubble over the grid. Asserted as presence: a
    rewrite may rephrase this, but dropping it leaves the placement to chance."""
    prompt = build_system_prompt(_SETTINGS)

    assert "a short lead-in, then the cards, then any question you want to ask" in prompt
    assert "Your reply renders in the order you wrote it" in prompt
    # And that the question is optional, so the order does not become a script.
    assert "a closing question is an option, not a habit" in prompt


def test_the_examples_model_the_order_rather_than_only_stating_it():
    """Examples steer harder than instructions, so the order has to be shown. One example
    ends with a question under its last card and one ends on its cards: the first teaches
    where a closing question goes, the second that it is optional."""
    blocks = _EXAMPLE_BLOCK_RE.findall(build_system_prompt(_SETTINGS))
    carded = [block for block in blocks if "</card>" in block]
    assert carded, "no worked example emits a card"

    after_last_card = [block.rsplit("</card>", 1)[1].strip() for block in carded]
    assert any(after_last_card), "no example models prose written under the cards"
    assert not all(after_last_card), "no example models a reply that ends on its cards"

    # The lead-in is still there: an example that opened with its cards would teach an
    # empty bubble, which is the one shape the contract cannot render.
    for block in carded:
        assert block.split("<card", 1)[0].strip(), "an example opens with its cards"


def test_the_prompt_draws_the_scope_line():
    """Sammy answers campus questions and declines everything else, including questions it
    could answer correctly - eval/ground-truth.yaml's out-of-scope pairs measure exactly
    this, so the instruction and the example that steers it must both be present."""
    prompt = build_system_prompt(_SETTINGS)

    assert "Scope:" in prompt
    assert "give none of the requested content" in prompt
    # The worked example, because examples steer harder than instructions.
    assert "restaurant picks are outside my lane" in prompt


def test_the_safety_roster_in_the_prompt_is_the_resolvers_table():
    """Safety triage is the model's call and the keys are its whole vocabulary: every key
    the prompt teaches must be one the server resolves. Both halves read app/safety.py's
    table, which is what this pins - a new resource is one table entry away from being
    teachable and resolvable, and an entry removed disappears from both at once."""
    import safety

    prompt = build_system_prompt(_SETTINGS)
    roster = safety.safety_roster_for_prompt()
    assert roster
    for key, when in roster:
        assert f"- {key}: when {when}" in prompt

    # The panel owns every number. The section teaching the keys must not leak contact
    # digits into text the model might copy into prose (key NAMES like crisis-988 are fine).
    safety_section = prompt.split("Safety:")[1].split("Never:")[0]
    assert "408-924" not in safety_section
    assert "<safety>crisis-988, caps</safety>" in prompt


def test_the_prompt_tells_the_model_where_the_answer_goes():
    """The editorial division, asserted as presence rather than wording: destinations and
    retrieved detail belong in cards, the prose is a short intro. If a later rewrite drops
    the division entirely, this fails; if it rephrases it, this is the line to update."""
    prompt = build_system_prompt(_SETTINGS)

    assert "What goes in a card and what goes in the prose" in prompt
    assert "The cards carry the answer. The prose introduces them." in prompt
    # The carve-out that keeps a zero-card turn from becoming a teaser above empty space.
    assert "When you emit no cards, the prose is the whole answer" in prompt


# --- the student's language -------------------------------------------------------------


def _language_section(prompt: str) -> str:
    """The Language block alone, so a carve-out asserted here cannot be satisfied by the
    words happening to appear somewhere else in a prompt this long."""
    assert "Language:" in prompt
    return prompt.split("Language:")[1].split("What goes in a card")[0]


def test_the_reply_follows_the_language_of_the_latest_message():
    """Both halves, because the second does not follow from the first. Which message decides
    is one rule; that a switch part way through a conversation is FOLLOWED rather than read as
    a slip is another, and a prompt saying only "answer in the student's language" leaves a
    model to decide whether the conversation has a language of its own that a single message
    should not overturn."""
    prompt = build_system_prompt(_SETTINGS)

    assert "Answer in the language of the student's most recent message." in prompt
    assert "the latest message decides, and it decides again each turn" in prompt


def test_the_language_rule_reaches_the_card_fields_by_name():
    """THE REASON THE SECTION EXISTS. A model follows "answer in their language" readily in
    prose and much less readily inside a <card> block, where the fields read as metadata
    rather than as speech. The cards carry the answer, so the three fields are named rather
    than left to follow from "the whole reply"."""
    section = _language_section(build_system_prompt(_SETTINGS))

    for field in ("<title>", "<desc>", "<followup>"):
        assert field in section, f"the language rule does not name {field}"


def test_the_language_rule_carves_out_what_a_translation_would_break():
    """Three carve-outs and three distinct breakages: a translated phone number is a wrong
    number, a translated office name is a door the student cannot find, and a translated
    <safety> key resolves to nothing at all. The last is the one with a crisis panel behind
    it - app/safety.py logs an unknown key at WARNING and drops it."""
    section = _language_section(build_system_prompt(_SETTINGS))

    assert "Phone numbers, email addresses, and web addresses, character for character." in section
    assert "The names of offices, buildings, programs and rooms" in section
    assert "the keys inside a <safety> block" in section


def test_the_safety_panel_does_not_move_with_the_language():
    """The panel's contents are the server's either way, so what this pins is the INSTRUCTION
    not to treat them as translatable prose: the model's two lines follow the student, the
    contacts under them are identical in every language. A crisis line is the one thing on
    the screen that a language change must not be able to reach."""
    safety_section = build_system_prompt(_SETTINGS).split("Safety:")[1].split("Never:")[0]

    assert "word for word the same in every language" in safety_section
    assert "The keys you write stay in English." in safety_section


# --- the escalation section -----------------------------------------------------------


def test_the_escalation_section_is_absent_when_no_recipient_is_configured():
    """ABSENT, not disabled. Teaching a tag whose output the server would drop spends
    tokens on every turn to produce an offer no student can see - and the model would be
    holding a contract this deployment cannot honour."""
    prompt = build_system_prompt(_SETTINGS)

    assert "escalate_to_human" not in prompt
    assert "escalation" not in prompt.lower()


def test_the_escalation_section_appears_once_a_recipient_exists():
    prompt = build_system_prompt(_settings(escalation_recipient="sjsucares@sjsu.edu"))

    assert "<escalate_to_human>" in prompt
    assert "at most ONCE in a turn" in prompt


def test_the_prompt_never_names_the_recipient():
    """The model does not choose, or know, where a draft goes. Putting the address in the
    prompt would invite it to write one into prose, where nobody resolved it - the same
    failure the card ref contract exists to prevent."""
    prompt = build_system_prompt(_settings(escalation_recipient="sjsucares@sjsu.edu"))

    assert "sjsucares@sjsu.edu" not in prompt


def test_the_escalation_cap_is_interpolated_rather_than_written_out():
    """The same one-value rule the card caps follow: the number the model is told is the
    number app/escalation.py applies. A literal would drift silently."""
    prompt = build_system_prompt(
        _settings(escalation_recipient="a@b.edu", escalation_max_chars=1234)
    )

    assert "1234 characters" in prompt


def test_the_section_states_that_going_over_drops_the_offer():
    """This cap behaves the opposite way to every other cap the model is told about, so it
    has to say so: the card contract's habit is to write to the ceiling and let the server
    tidy up, and here the tidying would be half a message to a stranger."""
    prompt = build_system_prompt(_settings(escalation_recipient="a@b.edu"))

    assert "dropped entirely rather than shortened" in prompt


def test_the_section_bans_an_offer_on_a_safety_turn():
    """Stated HERE and not with Safety's other exclusions, because this section is gated: a
    deployment with no recipient must not read a sentence about a tag it was never taught.
    That makes it the one duplicate in the prompt that is deliberate."""
    prompt = build_system_prompt(_settings(escalation_recipient="a@b.edu"))

    assert "Never offer it on a turn where you emit a safety block." in prompt


def test_the_section_names_all_three_triggers():
    """CAPABILITY, SENSITIVITY, AND BEING ASKED. The first shipped alone and it was the wrong
    half of the feature's own reason for existing: sponsors asked for a SENSITIVE turn to
    reach a person, and "no page answers this" tells the model not to offer to a student who
    is embarrassed about a bill the website does document."""
    prompt = build_system_prompt(_settings(escalation_recipient="a@b.edu"))

    assert "already tried the destinations you gave them" in prompt
    assert "personal or high-stakes enough that a person should read it" in prompt
    assert "They ASK to talk to a person. Always offer then" in prompt


def test_the_draft_stays_english_when_the_reply_does_not():
    """The one piece of model prose that does not follow the student's language, because it
    is the one piece whose reader is not the student: it opens in a staff inbox at SJSU. The
    rule sits in THIS section rather than in Language so that a deployment with no recipient
    is never told about a tag it cannot use, which is the same absent-not-disabled rule the
    rest of the section follows."""
    prompt = build_system_prompt(_settings(escalation_recipient="a@b.edu"))

    assert "WRITE THE DRAFT IN ENGLISH" in prompt
    # And the student is not left holding a message they cannot read: the prose around it is
    # still theirs, and it says what the draft says.
    assert "in THEIR language, what the draft says and who it goes to" in prompt


def test_the_english_draft_rule_goes_away_with_the_section():
    assert "WRITE THE DRAFT IN ENGLISH" not in build_system_prompt(_SETTINGS)


def test_asking_for_a_person_also_points_at_the_pill():
    """The one thing the model may name on screen, and the reason it is allowed: the pill is
    a SECOND way to reach a human, faster than a message somebody has to read, and a student
    who asked for a person should be given both rather than whichever we prefer."""
    prompt = build_system_prompt(_settings(escalation_recipient="a@b.edu"))

    assert '"Talk to a person" in the bottom right reaches SJSU Cares' in prompt
    # The display ban is unchanged for everything else, and the exception says so in the
    # same breath rather than leaving the two rules to be reconciled by the model.
    assert "Do not describe the draft, how it opens, or what it looks like on screen" in prompt


def test_the_place_roster_in_the_prompt_is_the_resolvers_table():
    """The location keys are the model's whole vocabulary for this feature, so every key the
    prompt teaches must be one the server resolves. Both halves read app/places.py's table,
    which is what this pins: a new place is one table entry away from being teachable and
    resolvable, and an entry removed disappears from both at once."""
    import places

    prompt = build_system_prompt(_SETTINGS)
    roster = places.place_roster_for_prompt()
    assert roster
    for key, when in roster:
        assert f"- {key}: {when}" in prompt


def test_the_prompt_shows_the_place_block_as_a_key_and_nothing_else():
    """The shape the model copies. An attribute or a second field in the sketch is how a
    model learns it may write an address, which is the one thing this contract exists to
    make unrepresentable."""
    prompt = build_system_prompt(_SETTINGS)
    assert "<place>career-center</place>" in prompt
    assert "no address, no room, no map link, no attributes" in prompt


def test_the_prompt_says_an_unlisted_place_gets_no_block():
    """THE LOAD-BEARING SENTENCE. A model that reaches for the nearest key sends a student
    to the wrong building, and no server-side check can catch that - the key resolves, the
    address is real, and it is the wrong one. So the rule has to land in the prompt, and it
    has to be unambiguous rather than a preference."""
    prompt = build_system_prompt(_SETTINGS)
    assert "write no block at all" in prompt
    assert "Not the nearest key" in prompt


def test_the_safety_section_carries_every_safety_turn_exclusion():
    """One list, in the Safety section: a safety turn drops the cards AND the location panel.

    The place ban used to be a second sentence at the foot of the place section. Both are
    enforced server-side anyway (apply_safety_handoff_to_response drops the cards and the
    place card together), so the second statement bought no coverage a rule does not cost -
    and a rule restated in a later section competes with the earlier one rather than
    reinforcing it. Asserted on the section, so it cannot be satisfied by the escalation
    section's own (deliberately duplicated, because gated) ban."""
    safety_section = build_system_prompt(_SETTINGS).split("Safety:")[1].split("Never:")[0]

    assert "no cards and no location block" in safety_section


def test_the_place_keys_are_not_translated_with_the_rest_of_the_reply():
    """A translated key resolves to nothing, exactly as a translated safety key does.

    The carve-out is stated once, in the Language section, where every other
    do-not-translate rule lives and where the tag names and ref ids are already named. It
    used to be repeated inside the place section; the Language rule now names the <place>
    key itself, which is what this asserts. The safety keys keep their second statement,
    and only they do: a dropped place card costs a panel, a dropped safety key costs a
    crisis panel."""
    section = _language_section(build_system_prompt(_SETTINGS))

    assert "the key inside a <place> block" in section
    assert "stay in English" in section


def test_the_prompt_never_writes_an_address_the_model_could_copy():
    """The roster is keys and one-line purposes, never addresses. A building name or a room
    number in the prompt is a specific the model can paste into prose on a turn where no
    panel appears at all, and it would be a hardcoded fact in a file that has none."""
    import places

    prompt = build_system_prompt(_SETTINGS)
    for place in places.CAMPUS_PLACES.values():
        assert place.address not in prompt
        assert place.directions_destination not in prompt
        assert "google.com/maps" not in prompt

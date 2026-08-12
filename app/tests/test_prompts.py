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


def test_the_card_descriptions_in_the_examples_carry_real_substance():
    """The weighting the prompt asks for: cards hold the answer, prose introduces them. A
    one-line description is the shape that weighting exists to move away from, so the
    examples must not model it - they are what the model copies.

    The examples teach a LENGTH, not just an upper bound: two sentences at roughly 150-175
    characters. The cap is a guard sitting far above that, so it holds no floor of its own -
    an example rewritten short would quietly re-teach the one-line card even though nothing
    about it violates a cap. This assertion is the floor."""
    descs = _examples(build_system_prompt(_SETTINGS), "desc")

    for desc in descs:
        assert len(desc) >= 150, f"a thin <desc> example undercuts the weighting: {desc!r}"


def test_the_prompt_permits_bold_and_bulleted_lists_in_both_places():
    """Two marks, prose and <desc> alike. The permission has to be explicit in both places:
    a model told only that the prose renders markup writes plain descriptions, and the
    descriptions are where most of the reply lives."""
    prompt = build_system_prompt(_SETTINGS)

    assert "Formatting:" in prompt
    assert "in the prose and inside a <desc> alike" in prompt
    assert "**Bold**" in prompt
    assert 'each line starting with "- "' in prompt


def test_the_prompt_bans_the_constructs_nothing_renders():
    """The load-bearing half. Only bold and unordered bullets reach the student as anything
    other than the literal characters typed, and a typed link is worse than unrendered: it is
    a destination nobody resolved, which is the failure the card ref contract exists to make
    unrepresentable."""
    prompt = build_system_prompt(_SETTINGS)

    for banned in ("no headings", "no numbered lists", "no tables", "no images"):
        assert banned in prompt, f"the formatting ban dropped {banned!r}"
    assert "no links written as bracketed text with a URL after it" in prompt


_BANNED_MARKUP = (
    ("a heading", re.compile(r"^\s{0,3}#{1,6}\s", re.MULTILINE)),
    ("an ordered list", re.compile(r"^\s{0,3}\d+[.)]\s", re.MULTILINE)),
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


def test_the_examples_model_both_marks_rather_than_only_permitting_them():
    """A construct that appears in no example is one the model uses at whatever rate its
    training suggests. Bullets are modelled where they earn their place - the contact band of
    a routing card, which is the half of the answer the 2026-08-10 eval kept losing."""
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

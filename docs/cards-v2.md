# Card system (v2)

Supersedes camp's response-to-card parsing. Safety cards are NOT part of this
document and do not change.

## What a card is

One retrieved source, presented to the student, carrying the part of the answer
that sends them somewhere. Anything that names a destination, or that we learned
from a source we ingested, belongs in a card: the specifics that make it usable
and the next step. The chat bubble is the intro to them, not the answer.

## One model response per turn

The model emits its whole turn as text: unwrapped prose plus zero or more card
blocks. Nothing else splits or classifies it.

```
prose answer here, unwrapped

<card ref="2">
  <title>short, written for this question</title>
  <desc>one or two sentences on what this source covers</desc>
  <followup>the question to ask if the student wants more</followup>
</card>
```

- Prose outside card blocks becomes the bubble.
- Cards render in emitted order, and in the emitted POSITION: prose written before them
  renders above the grid, prose written after it renders below.
- `ref` is an id from this turn's retrieval list. The model never emits a URL.

## Where the cards sit in the reply

The reply is split ONCE, at the end of the last card block. Everything before it, blocks
removed, is `conversationalText`; everything after is `trailingText`; the grid renders
between them. That is what keeps a closing question under the cards it refers to rather
than over the answer it is asking about, which is how it read while the whole reply was
one bubble above the grid.

One split point rather than a general list of interleaved blocks, because a turn produces
exactly ONE card group: the cards become a single `StatementBatch`, dealt as one deck,
anchored to once. There is no second grid for a third prose block to sit between, so the
only position information that exists is which side of the group each piece of prose was
on, and a block list would put a general structure on the wire and in the turn model to
carry two slots. Prose written BETWEEN two card blocks joins the lead: nothing can render
inside the grid, and it was written to introduce the cards that follow it.

Three cases collapse back to one bubble, each because there is no group left to split
around:

- **A safety turn.** The cards are dropped by contract, so trailing prose would render
  under the contact panel with nothing between them. The panel sits directly under the
  whole message; that placement is a safety property, and it is enforced in
  `apply_safety_handoff_to_response` beside the card drop rather than left to the caller.
- **Zero cards parsed.** The fallback rebuilds the bubble from the complete reply, so
  prose that was under the cards is just the end of the message.
- **No cards emitted at all.** Nothing to split.

`trailingText` is a new key on the wire and the only change to the response shape. It is
absent (`null`) on every one of those cases and on the ordinary reply that ends with its
cards. Frontend history sends both halves as the assistant's turn, because history carries
prose only and a "which one?" is answering the question the model asked under its cards.

On screen the trailing bubble waits for the deal to finish before it appears, then types
like the lead-in does. Two reasons, and the first is not cosmetic: the entrance is
transform-only so nothing reflows while cards are in the air, and a bubble growing under
them would be exactly that reflow. The second is rhythm - lead-in, cards landing, then the
question about them, in the order they are meant to be read.

## Retrieval contract

- The FIRST search of every turn is primed server-side (2026-08-10): the
  orchestrator retrieves on the student's own message before the model is
  called and injects the results as a completed tool exchange, in the exact
  wire shape a real call produces. The common case is one Converse call. The
  tool stays declared as the escape hatch for a sharper second search; a
  priming failure logs and degrades to the model searching itself.
- The retrieval tool returns up to K results, deduplicated by URL before the
  model sees them, each carrying a small integer id, its page title, and its
  WHOLE chunk text. A 500-char excerpt cap used to sit here and it hid the
  facts retrieval had already fetched - the contact band lives at chunk tails,
  so the model kept citing the right page while honestly saying it could not
  see the phone number (2026-08-10 eval). The chunk is bounded upstream by the
  ingestion chunking config; nothing re-cuts it in this layer.
- Ids are per-turn and are not reused across turns. The id-to-URL map is
  server-side turn state.
- The model may cite only ids it was given, and cites each at most once.
- K is 6, the card ceiling is 4, and cards are never required.
- `retrieve_min_score` 0.35 is inherited, not chosen, and needs retuning
  against the eval fixtures.

## The model never authors a source

A card's URL and link label are resolved server-side from the id. A URL the
model invented cannot render because it cannot be expressed.

The model is not shown URLs at all: the tool result carries `id`, `title` and
`text` and nothing else. That is stronger than validating a URL after the fact,
and it is the reason this is a shape the output has no room for rather than an
instruction the model follows or ignores.

## Tell me more

- The button label is fixed. The model authors only the hidden prompt, in
  `<followup>`.
- Clicking it sends that text as an ordinary user turn: it appears in the
  transcript as the student's own message and runs the deterministic safety
  intercept and the input guardrail exactly like typed input. There is no path
  that reaches the model without passing both.
- The prompt is length-capped as a guard on a paid model input. Missing or empty
  means the button is not rendered; the card still shows. Over the cap the
  button stays and the prompt goes through whole - see below.
- "Ordinary user turn" includes the answer: a follow-up can carry cards, and a
  student asking for more detail is asking for the specific destination, so it
  usually should. Nothing in the prompt or the request path withholds them.

The request still carries a `followup` boolean and the frontend still sets it on
a click, but nothing on the backend reads it. It shipped injecting "emit no
cards unless they clearly changed topic" into the user message, with the system
prompt repeating the suppression, which is why a clicked question produced no
cards while the same question typed by hand did. Alongside it went "do not
repeat cards the student already has": history carries prose only, so the model
cannot see which cards were shown, and an instruction it cannot evaluate
collapses into avoiding cards altogether. The flag stays on the wire because a
client-visible field is not worth breaking to delete a branch; if something
later wants to know how a turn was sent, it is already there.

An over-cap follow-up is neither truncated nor dropped. Truncation was never on
the table - a shortened question is a different question, so trimming it would
silently ask something other than what the model wrote. Dropping the button
guarded nothing visible while costing something visible: the text IS displayed
on click, as the student's own turn, where a long question simply wraps like any
typed message. So it goes through whole, button intact, and the overrun is
logged at WARNING - the cap is a runaway guard, and passing it means the prompt
or the model is broken.

## Editorial balance is a prompt knob, not architecture

How much of the answer sits in the prose versus in the card descriptions is set
entirely by the system prompt and its examples. Build the contract once, then
move the weight either way without touching code. These hold whatever the
balance is:

- one model response per turn: prose plus card blocks, nothing else
- every field is within its length cap
- prose is never empty

Where it sits now: in the cards. Anything that sends a student somewhere, or
that tells them about a source we ingested, is a card, and each one carries a
real description rather than a bare source link - the destination, the specifics
that make it usable, the next step. The prose is two or three lines saying what
kinds of options exist and pointing at the cards below.

The first shipped balance was the other one - prose answering directly in a few
sentences, cards carrying per-source detail - and moving it took a prompt
rewrite and one config number. That is the claim above holding up. The one thing
the weighting does not change is a turn with no cards, where the prose is
necessarily the whole answer; the prompt says so explicitly, because a teaser
bubble above an empty space is this balance's failure mode.

## Length caps

Under v1 roughly a third of every card description was lost to silent clipping:
the server capped at 220 characters and a 4-line CSS clamp swallowed the rest
with nothing on screen to show it had happened. That is banned. Text is
shortened where it can be measured, never hidden by the layout.

- Every card field is capped: title, desc, followup.
- The cap is ONE value per field, in config.yaml `cards`. The server enforces it
  and the prompt is built with that number in it. Two literals would drift.
- No cap answers to the layout any more. The card has no fixed height, the
  title wraps, and a long card widens (see Presentation), so the box has no
  opinion about how much text a field may hold. `title_max_chars` was the last
  derived one - one estimated line of the narrowest viewport's text column -
  and the derivation is retired along with the open item to measure Nunito
  Sans's real advance, which fed nothing but that arithmetic.
- The caps are RUNAWAY GUARDS, not editorial budgets. Length steering is the
  prompt's job: it states the editorial target and its canonical examples sit
  at that length, which is the layer that actually moves the model. The cap
  sits far above the steer, so the server truncating (at a word boundary,
  before the card leaves the backend) is a WARNING-logged bug, not a daily
  event - an ellipsis on screen means something is broken, and the frontend
  still renders whatever it receives without clipping.
- Cap violation rate is an eval metric. If the model overruns often, either the
  prompt or the cap is wrong, and the fixture run says which.

### Why desc_max_chars is 600

It has been 140 (the four-line clamp's capacity at a 320px viewport), then 300,
then 180 - each of those a number that tried to say how long a description
SHOULD be, first as a box measurement and then as an editorial budget for the
two-sentence card. The editorial judgement was right and stays; putting it in
the cutter was wrong. Against a real model, output length is a distribution,
not a promise: the model routinely wrote a little past the 180 it was told, the
server cut the text at the cap, and nearly every card ended mid-thought in an
ellipsis. A cap sized AT the steered length converts ordinary variance into
routine truncation, and a card whose last sentence is missing is a worse card
than one that runs a sentence long.

So the two jobs are now in separate places. The prompt still steers to two
sentences - its stated shape and its examples are unchanged in intent - and the
cap moved to roughly 3x that target, where the only thing it can catch is a
runaway response shipping an essay into a card. At 600 an ellipsis means a bug,
and cards.py logs every hit at WARNING so the bug is diagnosable rather than
quietly absorbed by the UI. Title moved 60 to 90 on the same reasoning, its
one-line derivation retired: titles wrap, so the layout was never protecting
anything worth a mid-thought cut.

## Presentation

Cards carry the destinations and the specifics; the prose is a short intro. A
card that is clipped is therefore an answer that is clipped, so the layout has
nowhere left to hide text.

- **The card is as tall as its own text.** No line clamp, no ellipsis, no fixed
  or minimum height, in a grid whose items do not stretch to match their row.
  Cards in a row end at different heights on purpose.
- **The grid is responsive**, `auto-fill` over a 15.5rem floor: multiple columns
  when the panel is wide enough, one column at the 320px viewport floor. The
  floor is measured against this layout, not picked - two tracks plus the gap
  need 509.6px and the panel is 546px at a 1280px viewport.
- **A long card takes its whole row.** Past roughly 280 body characters a card
  spans every column instead of one track. In one track it would set its row's
  height, and the two-line card beside it would float on top of the row's dead
  space; wider, it renders the same text in fewer lines and nothing sits beside
  a hole. A card a long one would strand in a partial row stretches to the end
  of that row for the same reason. Order is never changed to fill a hole -
  cards still render in emitted order, some just wider.
- **The prose stays.** It is never replaced, scaled away or scrolled off by the
  cards. The column grows underneath it and the view anchors to the top of the
  card group, once, when the group first appears.
- **The group enters by dealing off a deck.** Cards leave the deck one at a
  time, top card first, and land in final form; the last card is down inside
  1.5s at the card ceiling of four (0.34s between cards, 0.44s of flight each),
  which is paced to the prose above rather than to itself. There is no reveal button and nothing to
  press - the prose finishing typing is what brings the cards out. The composer
  stays interactive throughout, and a click on a card mid-entrance does nothing
  rather than landing on a card that is about to move out from under it.
- **The entrance is transform-only.** The grid is laid out final-form first and
  each card animated back from a measured stack position, so the column is at
  its finished height from the first frame and nothing below the group reflows
  while cards are in the air. Hover is transform and shadow only, for the same
  reason, and only on a real pointer - touch gets no hover state.
- **`prefers-reduced-motion: reduce` presents the grid directly.** No deck, no
  stagger, no transition. The preference is read on the first render rather than
  in an effect, so there is no animated frame to correct.
- **The group exists only when cards actually parsed**, and safety cards are
  never choreographed: they are on screen, whole, the moment the turn renders.

This replaced `CardStackAnimator`, the `PulseFab` reveal button, the `useRagPhase`
timing machine and the one-at-a-time progress bar, none of which have a caller
left. `RagPhase` is down to `conversational` and `grid`, and there is no longer a
phase in which the prose is off screen.

## Fallback

If zero cards parse, render the entire response as one bubble with any tags
stripped. Content is never dropped and a raw tag never reaches a student.

The scrub is deliberately narrow - only this contract's own tag vocabulary. A
generic `<[^>]+>` sweep would eat content the model may legitimately write, and
the guarantee needed is only that OUR tags never surface.

## What this deletes

- The mechanical text splitter (`_summarize_body`, `build_statement_cards`).
- The `submit_chat_response` tool, and with it the model-authored `sourceUrl`.
- The response-mode classifier (it read only the first statement batch).
- The 23-section preset mapping for statement cards, `app/section_presets.py`.
  The safety handoff destinations were never in that table - they are their own
  fixed list in `app/safety.py` - so it is deleted outright.

## What does not change

The safety intercept stays deterministic, ahead of the guardrail and ahead of
any model call, and emits its card directly. No tags, no model involvement.

The wire contract keeps its camelCase keys, its `StatementCard` shape and its actions
array. `trailingText` is added alongside them (see Where the cards sit in the reply); an
older client that ignores it renders the lead-in and the cards exactly as before, minus the
prose under them.

## Decisions that differ from the original draft

Two, both deliberate, both recorded here because the code comments would
otherwise be their only home.

**An unresolvable `ref` keeps the card, minus its source button.** The draft
dropped the whole card. Decided the other way for observability: a card that
renders without its link is a visible symptom, where a silently dropped card is
a student seeing three cards instead of four and nobody finding out. The event
is logged at WARNING with both the bad ref and the ids that were actually
available, because the UI is the weaker half of that signal.

The cost is real and worth stating: allowing a linkless card now means
tightening the rule later has to fight prompts that learned the loose version.

**The presentation layer was reworked in a second commit.** The tag-contract
commit was backend and data contract only and deliberately left the reveal
button and stack animation in place. The presentation rework the draft called
for then landed on its own, and is described under Presentation below.

## How we know it works

A fixture set of real student questions, asserting for every response: it
parses, every `ref` resolves, no card exceeds the ceiling, and prose is
present. Run before and after the change; the parse rate is the number that
decides whether the contract holds. Not yet built - it needs an account
(docs/build-plan.md).

Until then the guarantee is the unit suite, which covers well-formed output,
missing ref, unknown ref, over-cap text, zero cards and unparseable output.

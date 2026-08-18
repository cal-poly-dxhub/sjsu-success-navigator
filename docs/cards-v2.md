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
that make it usable, the next step. The prose is one or two lines saying what
kinds of options exist and pointing at the cards below, and the balance is
measurable rather than aspirational: in every worked example that emits cards,
the titles and descriptions outweigh the prose on both sides of the grid, and a
unit test asserts it so a rewrite cannot drift the weight back into the bubble.

The first shipped balance was the other one - prose answering directly in a few
sentences, cards carrying per-source detail - and moving it took a prompt
rewrite and one config number. That is the claim above holding up. The one thing
the weighting does not change is a turn with no cards, where the prose is
necessarily the whole answer; the prompt says so explicitly, because a teaser
bubble above an empty space is this balance's failure mode.

## The marks

Four constructs render, in the prose and inside a `<desc>` alike: `**bold**`,
`*italic*`, bulleted lists and numbered lists. Nothing else: no headings, no
tables, no images, no typed links, no raw HTML.

The ban is the half that matters. Only these render, so anything else reaches
the student as the literal characters the model typed, and a typed link is the
sharper case: it is a destination nobody resolved, which is exactly what `ref`
exists to make unrepresentable. No mark the renderer can express carries a URL,
and that is the property a fifth construct has to preserve to be allowed in.

Bullets earn their place on a routing card, where the office's email and phone
go at the foot of the description, because a card that names the right office
and leaves its number to be hunted down is the partial answer the 2026-08-10
eval kept scoring. Numbers earn theirs on a process answer: "how do I apply to
EOP" is a sequence, and a paragraph of it is a sequence the student has to
rebuild before they can follow it.

**What the PROMPT permits is a separate number, and it is now the same four.**
The renderer's set is a ceiling rather than an instruction, so widening the
prompt was its own decision; it was taken (app/prompts.py, Formatting). Before
that the prompt offered two marks and banned numbered lists by name, so numbered
steps and italics reached a student only when the model wrote them regardless -
which it did, which is why they used to arrive as literal `1.` and `*this*`.

Two things the prompt does that this file cannot. It states the syntax in the
parser's terms and not markdown's, `_underscores_` excluded by name and the
ordered marker only counting with its separator and the space after it, because
a permission looser than the parser is how a model is told a mark works and the
student gets asterisks. And it never lets the model describe the display to the
student: the old ban did not just suppress italics, it got recited to a student
who asked for them ("isn't something my display supports"), so what renders is
now internal by rule.

The prompt models two of the four and only permits the other two. A numbered
list earns its place on a process answer, no worked example is one, and the
examples are the length steer, so none was grown to carry one.

`<desc>` keeps its line breaks, which is what lets a list of either kind survive
the parser: a list is lines that start with a marker, and every other field is
still collapsed to one line. See Formatting inside a description.

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
- Which is why lowering the target does NOT lower the cap. The target came down
  to one or two short sentences and the guards stayed where they were, so the
  gap between steer and guard widened from roughly 3x to closer to 5x. That is
  the intended direction: a wider gap absorbs more ordinary variance before
  anything is cut. The pair of numbers that has to stay in step is the stated
  target and the example descriptions, not the target and the cap - the examples
  sat at the old target, so moving the target alone would have left the model
  copying the length it was shown. Both moved in the same commit.
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

So the two jobs are now in separate places. The prompt steers to one or two
short sentences, plus a contact list on the cards that have one, and the cap
sits several times above that, where the only thing it can catch is a runaway
response shipping an essay into a card. At 600 an ellipsis means a bug,
and cards.py logs every hit at WARNING so the bug is diagnosable rather than
quietly absorbed by the UI. Title moved 60 to 90 on the same reasoning, its
one-line derivation retired: titles wrap, so the layout was never protecting
anything worth a mid-thought cut.

## Formatting inside a description

Four marks render in model-authored text, in the prose and inside a `<desc>` alike:
`**bold**`, `*italic*`, a bulleted list (one item per line, each line starting with `- `) and
a numbered list (`1. ` or `1) `, and the first number is the one shown). Nothing else. The
display parser is hand-written and construct-by-construct on purpose rather than a markdown
library with a sanitizer bolted on, because the one construct this path must never gain is a
link: the model is never shown a URL, so a model-authored URL is unrepresentable, and the
renderer should stay unable to express one rather than be taught to and then policed
(`frontend/src/lib/messageFormat.ts`). Unsupported syntax renders as its own characters, so
nothing the model types can silently disappear - an unmatched `**` is a pair of asterisks on
screen, not a run of text that vanished looking for its closer.

This is a prompt knob like the editorial balance is: the tag contract does not change, and
nothing on the wire says whether a description is bulleted.

What the SERVER owes it is one thing, and it is easy to lose: **`<desc>` keeps its line
breaks.** A bullet is a line that starts with a marker, so a description whose newlines were
collapsed into spaces arrives as one paragraph reading "... an advisor has read. - Email: ...
- Walk in: ...", and the list the model wrote is gone with nothing on screen to say so. Every
other field is still collapsed to one line, because every other field IS one line: a title is
a heading and a follow-up is a question sent as the student's next turn. Indentation inside
`<desc>` is still collapsed too - a model that indents its bullets is formatting its XML, not
asking for leading space on screen - and so is a run of blank lines, the same normalisation
the prose either side of the cards already gets. The caps are unchanged and still measured
after that normalisation, newlines included.

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
- **The group enters by dealing off a deck, and the deck is a real thing.** It
  is on screen before the cards are: while the model is still writing them the
  pending exchange shows the stack, face down, CYCLING - four card objects taking
  turns. A BEAT is one nudge and one rest: a card dips 9px out of the bottom of
  the stack and returns to exactly where it started (1140ms, eased off both ends
  so it stops before it comes back, leaning 2.1deg from its own top edge as it
  goes), and then EVERYTHING STOPS for 860ms before the card above it takes its
  turn. It works upward from the bottom through every card, then comes round
  again. NO CARD EVER CHANGES SLOT, which is the structural point rather than a
  simplification. Versions that cycled cards through the stack had to change a
  card's depth mid-move, and a card swapping from behind the deck to in front of
  it is a jump no easing hides - so it needed a dip far enough to clear the whole
  stack first, and that dip was most of the motion. With the order fixed, what is
  left is the small part that was saying something. There is no reveal button and
  nothing to press.
- **The backs are skeletons, and every dealing card carries one.** A blue title
  bar, two grey description lines with the second short, and the source and
  follow-up buttons in their own colours - flat fills at the real card's radius,
  padding and gap, so a card in flight already says what kind of thing it is
  about to be. Solid bars rather than anything cleverer: this was a censor mosaic
  of 4px blocks, which read as a GRID with a pitch of its own that the eye locks
  onto, and then gradient ramps standing in for blurred type, which was better
  but still a drawing of something rather than a placeholder. Nothing is
  filtered - a `blur()` rasterises in the element's own coordinate space, and
  this element is scaled on two axes and turned over in 3D at once, so the radius
  would swim as the card grows. There is no text anywhere in it and nothing to
  read underneath, so it cannot fail in a way that reveals content.
- **The stack is always four; the group is not. The deck compresses to fit.**
  Nobody knows how many cards are coming until the payload lands, so the deck
  waits at four - a row of skeletons that turns into one card is a promise the
  reply then breaks. When the reply arrives it hands the count back, and before
  the swap the SURPLUS CARDS RIPPLE UP AND TUCK IN under the top one: bottom-most
  first, 260ms each on the deck's own 2.1deg lean, 90ms apart so the moves
  overlap into a single gesture travelling up the stack, then a 130ms settle. One
  card sheds three, two sheds two, three sheds one, four sheds none. Spread,
  compress, deal. After the compress the stack's geometry IS the real deck's
  opening pose, which is what turns the hand-off from a swap into a
  continuation - and the beat that used to sit AFTER the swap, on a deck the
  student had never seen move, now sits at the end of the compress on one that
  just did. A pause reads as a breath after motion and as a stall after nothing.
- **The deal is dealt from the BOTTOM, and the top card never moves.** Cards
  leave one at a time, 0.34s apart, each turning over in the back half of its
  flight - they have been face down, so they arrive by being turned up. The card
  on top goes last and its slot IS the deck's position, measured at (0, 0): it
  has nowhere to fly, so it flips where it lies. That is the geometry rather than
  a flourish, and it is why the order reversed.
- **A flight is sized by its distance, not by a fixed duration.** Every card
  leaves the same deck for its own slot, so they do not travel the same way: at a
  1440px viewport the four distances are 21, 218, 514 and 589px. One duration for
  all of them meant one SPEED each, rising with the index - 270, 2316, 5151 and
  6878 px/s measured in Chrome - so the deal accelerated as it went and the last
  card was a blur, then crawled. Duration is now distance over 1150px/s, floored
  at 0.46s (what the top card's turn-over needs) and capped at 0.62s, and the
  ease is a cubic rather than the quintic that put 70% of the distance into the
  first quarter of the flight. The group is down inside ~1.7s at the ceiling of
  four.
- **The deal never starts out of a moving deck, and that is what stops the
  hand-off snapping.** The reply's arrival is HELD until the deck is in one of
  its rests - at most one travel away - and the deck stops there, square, every
  card on a slot. The compress then runs (above), and its closing settle is the
  beat of stillness the first card slides out from under; there is no separate
  freeze after the swap. THE DECK REPORTS THIS ITSELF
  (`lib/waitingDeck.ts`) rather than the caller computing it: two earlier
  versions derived the still moments from keyframe percentages, which is a second
  model of the animation kept in step with the first by hand, and both drifted -
  the caller believed the deck was still while it was mid-move, which is exactly
  what the snap was. Nothing else waits on the hold: the prose is typed and on
  screen already, and a turn that never showed a deck resolves immediately.
- The composer stays interactive throughout, and a click on a card mid-entrance
  does nothing rather than landing on a card that is about to move out from under
  it.
- **The entrance is transform-only.** The grid is laid out final-form first and
  each card animated back from a measured stack position, so the column is at
  its finished height from the first frame and nothing below the group reflows
  while cards are in the air. Hover is transform and shadow only, for the same
  reason, and only on a real pointer - touch gets no hover state.
- **Hover is earned by the pointer MOVING, never by a card arriving under one.**
  The deal ends by giving pointer events back to the group, and a mouse resting
  anywhere over the panel is then suddenly over whichever card stopped beneath
  it: that card took the hover lift on its own, ~6px BACK UP the path it had just
  flown, a second and a half after it started moving and with nothing the student
  did to explain it. It read as the entrance re-firing and was not - measured in
  Chrome, the card under the pointer moved, its neighbours did not, and the
  transform it settled on was the `:hover` rule's exactly. The rule is now gated
  on `card-deck--hover-armed`, set by the first pointer movement after the group
  settles and re-armed per group. Clicking is untouched; only the lift waits.
- **The silence between the prose and the cards says what it is.** The preview
  stops at the first tag, so the reply appears to end and nothing tells the
  student whether anything else is coming. The server sends a `status` frame the
  instant `<card` appears in the model's own output (`app/streaming.py`,
  `CARDS_STAGE`), and the pending exchange answers it with the shuffling deck and
  a line naming what it is. NEVER A TIMER: a reply with no cards emits no frame
  and shows nothing at all, which is the case that matters - about one reply in
  ten is prose only, and an indicator promising resources that never arrive is
  worse than the gap it filled. It is also not shown while prose is still typing;
  the window it fills is exactly the one with nothing in it.
- **The waiting deck and the dealing deck are the same object.** The waiting one
  shows three backs, which is a deck rather than a claim about the count - nobody
  knows yet how many cards the model is writing, and three skeletons where one
  card arrives is a promise the reply breaks. The real cards are then scaled to
  that same 76px stack while they are face down and grow out of it as they are
  dealt, so the student never sees the deck resize into the answer. Their LAYOUT
  boxes are final throughout, which is what keeps the entrance transform-only.
- **`prefers-reduced-motion: reduce` presents the grid directly.** No deck, no
  stagger, no transition. The preference is read on the first render rather than
  in an effect, so there is no animated frame to correct.
- **The group exists only when cards actually parsed**, and safety cards are
  never choreographed: they are on screen, whole, the moment the turn renders.

This replaced `CardStackAnimator`, the `PulseFab` reveal button, the `useRagPhase`
timing machine and the one-at-a-time progress bar, none of which have a caller
left. `RagPhase` is down to `conversational` and `grid`, and there is no longer a
phase in which the prose is off screen.

## The campus location card

Additive: a new block, a new response field, and nothing about the four existing card
types or the safety path changes shape.

```
<place>career-center</place>
```

ONE CATALOGUE KEY, exactly as `ref` is one id and a `<safety>` block is a list of keys. No
attributes, no address, no URL, so a model-authored address is unrepresentable rather than
validated and rejected. The server owns the name, the address and both links
(`app/places.py`), and the catalogue is code - a table like `app/safety.py`'s, not deploy
config, which is why the prompt section is always present rather than gated.

**A place not in the catalogue yields NO card.** Not the nearest key, not a search query
standing in for a location, not the building it is near. An unknown key is dropped with a
WARNING and the reply keeps its prose and its cards. That rule is enforced in two places
because only one of them can actually catch it: the server drops a key it does not know, and
the prompt states, in its own load-bearing sentence, that an unlisted place gets no block -
because a model reaching for a NEIGHBOURING key produces a card that resolves, renders and
is wrong, which no server-side check can see.

**No length cap touches it.** The caps are guards on model-authored text and there is none
on this path, so an address is never shortened. A room number cut at a word boundary is the
exact failure the caps exist to prevent elsewhere.

**No Google Maps API, no key anywhere, and no third-party request at all.**

- **The map is a picture we render and serve ourselves.** `scripts/render_place_maps.py`
  stitches OpenStreetMap tiles around each building's coordinate, draws the pin, bakes in the
  attribution and writes `frontend/public/places/<building>.webp`, which is committed. The
  deployed site serves it from the same distribution as the page, so a student reading an
  answer contacts nobody but us - not on render, not on a press. Google's Static Maps
  endpoint returns exactly this picture and was rejected on both counts: it needs a key and a
  billing account, and its terms forbid storing what it returns, which is the whole idea here.
  The standard OSM style was kept over CARTO's sharper, retina-capable one because CARTO
  renders campus buildings as anonymous blocks, and the building's NAME under the pin is what
  makes the picture useful.
- **The directions link is a Maps URL**, `google.com/maps/dir/?api=1&destination=...`, built
  from a per-entry `directions_destination` the table owns and requested only when a student
  presses it. That string is a curated QUERY rather than the coordinate beside it: the
  coordinate is more precise, but Maps then labels the destination with six decimal places
  instead of the building's name.

**A card with no map is a complete card.** `map_image_url` is None when a catalogue entry's
building has not been rendered, which is where a new entry lands before anybody runs the
script. Name, address and directions answer "where is it?"; the map is what makes it quick.
The frontend treats absence as ordinary, and a test asserts every building in the table has a
committed image and that no image is orphaned - because the images are committed rather than
built, nothing at deploy time would otherwise notice one missing.

**Sixteen offices, five buildings.** Four of these places are inside Clark Hall and five
inside the Student Services Center, so the coordinate and the image belong to the BUILDING
while the room number belongs to the place. That is what stops five near-identical renders and
five chances to mis-key one of them.

**A safety turn carries none**, by the same rule that drops its cards and its email draft,
enforced in the same two places (`orchestrator._response_from_text` for a tagged turn,
`apply_safety_handoff_to_response` for a reply whose prose named a hotline without the tag).

The resolved card is STORED with the turn, like the email draft and unlike the safety panel:
the panel resolves against a fixed crisis roster, where this resolves against an editable
directory, so an office that moves must not rewrite where last month's turn said it was.

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

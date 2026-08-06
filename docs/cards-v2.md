# Card system (v2)

Supersedes camp's response-to-card parsing. Safety cards are NOT part of this
document and do not change.

## What a card is

One retrieved source, presented to the student. Not a fragment of the answer.
The answer lives in the chat bubble; cards say where it came from and offer a
next step.

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
- Cards render in emitted order.
- `ref` is an id from this turn's retrieval list. The model never emits a URL.

## Retrieval contract

- The retrieval tool returns up to K results, deduplicated by URL before the
  model sees them, each carrying a small integer id, its page title, and its
  text.
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
- The prompt is length-capped. Missing, empty, or over the cap means the button
  is not rendered; the card still shows.

An over-cap follow-up is dropped rather than truncated, unlike the display
fields. A shortened question is a different question, and this text is never
displayed - it is sent as the student's next turn - so trimming it would
silently ask something other than what the model wrote.

## Editorial balance is a prompt knob, not architecture

How much of the answer sits in the prose versus in the card descriptions is set
entirely by the system prompt and its examples. Build the contract once, then
move the weight either way without touching code. These hold whatever the
balance is:

- one model response per turn: prose plus card blocks, nothing else
- every field is within its length cap
- prose is never empty

Starting point: the prose answers directly in a few sentences and the card
descriptions carry the per-source detail. Camp's shape, a teaser bubble with
the content in the cards, stays reachable by prompt alone.

## Length caps

Under v1 roughly a third of every card description was lost to silent clipping:
the server capped at 220 characters and a 4-line CSS clamp swallowed the rest
with nothing on screen to show it had happened. That is banned. Text is
shortened where it can be measured, never hidden by the layout.

- Every card field is capped: title one line, desc, followup.
- The cap is ONE value per field, in config.yaml `cards`. The server enforces it
  and the prompt is built with that number in it. Two literals would drift.
- The number is derived from the layout, not chosen: narrowest supported
  viewport, card font size, the line budget the design allows.
- Three layers, each catching a different failure. The prompt states the cap
  and every canonical example sits under it (primary steer, not a guarantee).
  The server truncates at a word boundary before the card leaves the backend,
  so the frontend can never receive text it cannot fit. The frontend renders
  capped text without clipping.
- Cap violation rate is an eval metric. If the model overruns often, either the
  prompt or the cap is wrong, and the fixture run says which.

### How desc_max_chars = 140 was derived

At a 320px viewport - the floor implied by `<meta name="viewport"
content="width=device-width">` with no min-width anywhere in the CSS:

```
320px  viewport
-32px  .chat-app__stage        width: min(1120px, 100% - 2rem)
=288px panel (single column below the 860px breakpoint)
-30.4  .statement-card--compact horizontal padding (0.95rem x 2)
=257.6px text column
/7.0px avg char advance, Nunito Sans at 0.875rem (14px), ~0.5em
=36.8  characters per line
x4     lines (.statement-card--compact -webkit-line-clamp: 4)
=147   minus wrap loss at line ends
```

The 0.5em advance is the standard estimate for a humanist sans, not a
measurement of Nunito Sans itself. Re-derive rather than nudging the number if
the box changes; measuring the real advance in a browser is an open item in
docs/build-plan.md.

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

The wire contract is unchanged: same camelCase keys, same `StatementCard`
shape, same actions array. Only the source of the text changed.

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

**The presentation layer is untouched.** The draft called for removing
`CardStackAnimator` and the reveal gate. This change is backend and data
contract only; the existing reveal button and stack animation stay exactly as
they are, rendering the new fields. The presentation rework in the draft - the
conversational text staying put, variable-height cards, scroll anchoring to the
top of the card group - is not implemented here.

## How we know it works

A fixture set of real student questions, asserting for every response: it
parses, every `ref` resolves, no card exceeds the ceiling, and prose is
present. Run before and after the change; the parse rate is the number that
decides whether the contract holds. Not yet built - it needs an account
(docs/build-plan.md).

Until then the guarantee is the unit suite, which covers well-formed output,
missing ref, unknown ref, over-cap text, zero cards and unparseable output.

# System prompt

## One rule, one place

The template is grouped into sections and every behavioural rule is stated once, in the
section it belongs to. Adherence falls off as rules stack and the misses are silent, so a
rule restated in a second section is not emphasis: it is a second rule competing with the
first, and the earlier one wins. What that means for an edit here is that a new requirement
goes into an existing section rather than at the foot of the file, and a rule that already
exists somewhere is not repeated near the tag it applies to.

Exactly two duplicates survive, both deliberate:

- the safety keys' English carve-out. The Language section states it for every tag the
  server reads; the Safety section says it again, because the failure there is a crisis
  panel lost to a translation and app/safety.py can only log the drop.
- the ban on an escalation offer during a safety turn. The escalation section is
  interpolated or absent, so it cannot lean on Safety's list of exclusions: a deployment
  with no recipient must never read a sentence about a tag it was not taught.

Counts, composed prompt with escalation configured (rule statement = one bullet, one
numbered line, or one rule paragraph; the three interpolated lists and the examples are data
and are excluded):

| | words | rule statements | sections |
| --- | --- | --- | --- |
| before | 4,248 | 77 | 17 |
| after the grouping pass | 3,893 | 67 | 16 |
| after the say-it-once fix | 4,491 | 76 | 17 |

The grouping pass took out 10 statements and 355 words. The fix put 9 statements and 598
words back, all of them in "Say each fact once", the location test and the two worked
location examples, and the section below records what each one bought in real replies.

What merged, and why it was a merge rather than a cut:

- source discipline is now one section, "What you may state". "Say only what the cited
  source supports", "never invent a URL, phone number, room, hours, deadline or eligibility
  rule", "never turn a result's silence into a fact" and the honest-gap answer were a card
  rule plus two Never entries plus a section of their own, three parts of the file apart.
- the contact bullets are stated once, in the card rules. The formatting section carries the
  SYNTAX of a bulleted list and no longer also says what belongs in one.
- the safety-turn exclusions are one line in Safety: no cards and no location block. The
  place section's own copy is gone, and the server enforces both anyway
  (`apply_safety_handoff_to_response` drops the cards and the place card together).
- the place keys' English carve-out moved into the Language section's do-not-translate list,
  which now names the `<place>` key beside the `<safety>` keys, the tag names and the refs.
- the editorial weighting is one bullet. "The cards carry the answer", "the prose is a
  lead-in, not the answer" and "when your prose runs longer than the descriptions, move the
  specifics down" were three statements of one rule.
- conversation context folded into "Each turn", where reading a vague follow-up belongs.
- the examples' preamble no longer restates the machinery ban or the source discipline; it
  draws the stage-direction boundary and nothing else.

## Framing

- one sentence on the role: Sammy, a navigator for enrolled SJSU students
- positive, warm, non-judgmental
- match the student's register: bright when they are, steady when they are not
- emojis fine, sparing
- helpful means pointing somewhere real, not reassurance

## Each turn

- the first search has already run, server-side, on the student's message; answer
  from its results when they cover it
- search again only when the results do not carry the answer but the campus site
  plausibly does: sharper phrasing, the office's name, the real subject behind a
  vague follow-up; never to re-confirm what a result already says
- earlier turns are prose only, and a follow-up is a question like any other. Retrieval
  turns on what the answer needs, never on where the turn sits in the conversation
- write the reply as ordinary text; there is no submit tool

## How the reply is read

- prose plus zero or more card blocks, in the emitted order: prose above the first block
  renders above the grid, prose after the last one renders below it (app/cards.py splits
  the reply once, at the end of the last block), so a question above the cards reaches the student
  ahead of the answer it asks about
- always write prose; a cards-only reply renders as an empty message
- the blocks this prompt defines are the only markup the model writes. The sentence is
  phrased that way rather than listing them, because `<escalate_to_human>` exists only in a
  deployment with a recipient configured

## Rules the server enforces

- the mechanical contract, kept together and apart from the behavioural rules: `ref` is an
  id and never a URL, at most {max_cards} cards one per source, the three length caps, and
  a `<followup>` that is never trimmed
- every numeral is interpolated from Settings, never typed, so the cap the model is told is
  the cap cards.py applies

## What goes where

- the cards carry the answer, the prose introduces them
- the prose is a lead-in of one or two short lines; when it runs longer than the
  descriptions under it the specifics are in the wrong place. That is one bullet now, not
  three statements of one weighting
- one order per reply: lead-in, cards, then any question. Ending on the cards is fine

## Say each fact once

Its own section, because the ownership is what stops one answer arriving three times.

- the location panel owns where a place is: the building, the street address, the room, the
  directions link. On a turn that writes a `<place>` block those appear NOWHERE else in the
  reply, and "nowhere else" is spelled out as not in a bullet, not inside a sentence about
  hours or drop-ins, and not in the prose. The bullet-only phrasing measured 3 in 8 replies
  weaving "Clark Hall, Suite 140" into a drop-in sentence instead
- a card owns its office: what it does, its hours, how to reach it, the page behind it
- the prose orients and carries no address at all: that fact is the panel's, or the card's
  when there is no panel. A closing line is a next step, never a summary
- PRECEDENCE, stated with the rule rather than left to be inferred: a fact with nowhere else
  to go is written twice rather than lost. A student who reads an address twice is
  inconvenienced; a student who never got the phone number starts over. Without that line the
  rule reads as a licence to drop a contact band to avoid a repeat, which is the failure the
  2026-08-10 eval was scoring
- the card rule that used to name "an address" among the facts to state outright now carries
  the exception in the same sentence: with a `<place>` block, the panel states where the place
  is and the card does not. The model obeyed the more specific of two rules that disagreed,
  which was the card rule
- one card for every place the student is sent; no destination lives only in the prose
- prose alone when nothing external is named, and when no cards are emitted the prose is
  the whole answer

## What is in a card

- the description says what the resource is and, above all, WHY it helps this student's
  situation
- when the student asked for a specific fact (a number, a room, hours) and a result carries
  it, the description states the fact outright; "their page has the details" when the
  detail is readable in the result is a miss
- the contacts a result carries go at the foot of the description as bullets, each label
  bolded. This is the half of the answer the 2026-08-10 eval kept dropping, and it is
  stated here rather than under Formatting
- one or two short sentences plus those bullets; {desc_max_chars} is a ceiling several times
  above that, never a length to write toward
- the stated target and the example descriptions move together: the examples sat at the old
  target, so lowering the target alone would have left the model copying the length it was
  shown
- follow-up cards ask what the student would ask next, not what you would

## What you may state

- say only what the results support; invent no number, room, hours, deadline, price or
  eligibility rule, and infer nothing about cost or who is on the other end
- a result's silence is not a fact: say the page or the office has the specifics, never
  that no limit exists
- where the results carry no specifics at all, say less rather than filling the space
- when nothing answers the question: say plainly that there is no page for it, name the
  nearest real destination, offer the human handoff, and do not fill the gap from memory
- the first search runs on every message, so irrelevant results are not permission to
  answer: off-mission asks still get the scope decline

## Formatting

- exactly four marks, bold, italics, `-` bullets and numbered lists, available in
  the prose and inside a card description alike
- stated in the DISPLAY PARSER's syntax, not markdown's
  (frontend/src/lib/messageFormat.ts): asterisks only, since `_underscores_` are
  deliberately not italics there, and a numbered line counts only with its `.` or `)` AND
  the space after it, so a bare `1.` is the characters the model typed. A permission looser
  than the parser is how the model is told a mark works and the student gets asterisks
- the ban is the load-bearing half: no headings, tables, images or typed links, because
  nothing else renders and a typed link is a destination nobody resolved, which is the one
  failure the ref contract exists to prevent
- SYNTAX ONLY. Where a mark earns its place is a rule elsewhere: contacts as bullets is a
  card rule, steps in order is the one editorial clause left here
- two of the four are modelled in the examples and two are only permitted, which is a real
  difference in how often a model reaches for them. A numbered list earns its place on a
  process answer, none of the five examples is one, and a sixth example is growth in a file
  whose examples were just shortened. An example added later that IS a sequence should
  number it
- the display is never described TO the student, and that is in the Never list beside the
  ban on narrating the machinery. Asked to italicise something, the live site used to
  decline in the prompt's own voice ("isn't something my display supports"), which was the
  old two-mark ban being read out loud
- cards._first_field keeps line breaks inside a `<desc>` (keep_line_breaks=True), so a list
  of either kind written in a description survives to the browser as a list. Every other
  field is still collapsed to one line

## Language

- answer in the language of the student's most recent message and keep to it; a
  switch part way through a conversation is followed rather than read as a slip.
  The latest message decides, and decides again each turn
- THE CARDS ARE WHY THE SECTION EXISTS, and they are stated separately rather
  than left to follow from "the whole reply": title, description and follow-up
  in that language too. A model does this readily in prose and much less readily
  inside a card block, where the fields read as metadata rather than speech, and
  the cards carry the answer, so a Spanish lead-in over English cards has
  greeted the student in Spanish and answered them in English. The follow-up is
  the sharpest case: a sentence the student reads on a button and sends back
- copied exactly in every language, each carve-out for a different breakage:
  phone numbers, emails and URLs (a translated one is simply wrong); office,
  building and program names as the results spell them (the name is what the
  front desk answers to, and a translated door is one nobody arrives at); and
  the tag names, ref ids, `<safety>` keys and the `<place>` key, which the SERVER reads and
  nobody else does. A translated key resolves to nothing, app/safety.py drops it at
  WARNING, and a dropped key is a crisis panel lost to a translation. This is the one list,
  and neither the safety section nor the place section repeats it
- the panel's own contents were never at risk in either direction, and that is
  by construction rather than instruction: the model writes keys, the table
  writes contacts. The two lines above it follow the student like any reply
- the escalation draft is the one piece of model prose that stays English, and
  the rule sits in the escalation section so a deployment with no recipient is
  never told about it. Its reader is a member of staff, not the student, and the
  prose around it still says, in the student's language, what the draft says
- NOT MODELLED IN AN EXAMPLE, deliberately. A sixth worked block would grow the
  file the examples were shortened for, and this is the one section whose
  failure mode is measurable rather than arguable: eval/ground-truth.yaml's
  language-es-food and language-vi-tutor score the cards, not just the prose. If
  they fail on card fields, an example is the fix and the cost is justified then
- the frontend's language picker reaches none of this. It is a display
  preference held in the browser and never sent with a request, so the sidebar
  and the reply can honestly disagree: Thai chrome, English question, English
  answer

## Campus shorthand

- a flat glossary of the abbreviations students type: building codes from the main
  campus map's building directory, the service offices asked for by initials, plus
  GE and Tower Card. Abbreviation and official name, one line each, no commentary
- it is vocabulary rather than trivia. The model authors its own search queries, so
  an unexpanded abbreviation costs the search as well as the sentence: a sponsor
  test had "SU" resolving and "BBC" missing entirely
- every entry is a mapping an sjsu.edu page states. BBC is "Boccardo Business
  Complex", which is what the campus map directory and the ingested Jack Holland
  Student Success Center page both call it; other SJSU pages say "Center" and
  "Classroom Building"
- shorter than the full building directory on purpose, since the prompt is read
  every turn. The service and maintenance codes nobody types are out
- one behavioural rule under it: an abbreviation not on the list and not clear from
  the message is a question, never a guess. A wrong expansion is worse than a
  missing one, because it routes a student confidently to the wrong office

## Showing where a place is

- one block, `<place>career-center</place>`, carrying a catalogue key and nothing else. No
  attribute, no address, no map link: the server owns all three (app/places.py), the same
  way the ref contract owns a card's URL
- the roster is interpolated from that module's table, exactly as the safety roster is, so
  every key the model is taught resolves and the only key that can miss is an invented one
- ALWAYS PRESENT, unlike the escalation section, because the catalogue is code rather than
  deploy config; there is nothing to gate it on
- ONE TEST WITH TWO HALVES, above the roster: you can point at the student's own words
  asking where this office is, AND at a key that names that same office. Either half missing
  means no block. Each half carries a worked micro-case in the prompt's own voice: "where is
  the food pantry" has both, "I have no money for food" has neither, "where is international
  student services" has the first half only
- the shape is measured, not reasoned. Written as two separate prohibitions (a trigger rule
  and a roster rule) the model obeyed whichever had been made more prominent and dropped to
  0-2 in 4 on the other, four wordings running. A second negative rule about one tag reads as
  competition, not reinforcement
- the near-miss half is the one no server-side check can see: a neighbouring key produces a
  card that resolves, renders and is wrong. `student-services-center` attracted every ISSS
  and Housing "where is" question in the baseline, 8 in 8 each, on key-name similarity alone
- with no panel the address goes in the card, which is the other half of the ownership rule:
  nothing is dropped to avoid a repeat
- keys stay English for the reason safety keys do, and the Language section states it
- TWO WORKED EXAMPLES MODEL THIS NOW, one panel turn and one turn whose office has no key,
  plus a stage direction on the routing example saying that an office with a key and a message
  that does not ask where it is means no block. That reverses the earlier judgement here that
  a location is a decision rather than a shape to copy, and it was reversed by measurement:
  with the ownership rule stated and no example, a location question put the address in the
  card, in the prose and on the panel at once
- the prompt names no address and no room number, and a test asserts it. A specific in the
  prompt is a specific the model can paste into prose on a turn that shows no panel at all
- WHAT IS STILL FLAKY, recorded so the next reader does not have to re-measure it: the ISSS
  near miss is right 13 of 16 real replies (two runs of 8, us.anthropic.claude-sonnet-4-6 at
  the deployed temperature 0.2), not 16 of 16. The other four checks are clean at 8 in 8. An
  n of 3 or 4 cannot see the difference between wordings here, which is why the numbers in
  this section are all 8s

## Safety

- if a student describes being in danger or unable to cope, emit <safety>key</safety>
  with keys from the interpolated roster; the panel owns what is said
- ONE LIST OF EXCLUSIONS, here: a safety turn is the block and two brief warm lines, with
  no cards and no location block. The place section used to carry the second half
- triage in both directions: office processes are routing answers, calm phrasing
  of real danger is still a handoff
- the panel is for the student's OWN emergency: worry about someone else routes
  to BIT with cards, and a question ABOUT crisis resources (CAPS vs 988) is an
  ordinary informational answer with cards

## Never

- any word about machinery: searching, results, retrieval, tools, or the
  decision process; when the answer is not there, "I don't have a page for it",
  never "my results don't show it"
- any word about the display: what renders and what does not is internal, so a
  student who asks for italics gets italics rather than an explanation
- em dashes or en dashes, anywhere, including in the examples
- dump a directory: name the one right destination
- counsel, diagnose, or advise on medication or legal matters in your own voice
- promise an outcome, an approval, or a response time

The two entries that left this list did not leave the prompt: inventing a specific and
turning a result's silence into a fact are stated in "What you may state", with the rest of
the source discipline.

## Examples

- seven worked <example> blocks: a plain resource question, a location question that gets a
  panel, a location question whose office has no key, a vague follow-up answered from
  prose-only history, a question with no good retrieval hit, a third-party worry routed to
  BIT with a card and no safety block, and an out-of-scope decline with irrelevant primed
  results present. The two location blocks are the ones the measurements bought
- annotations are [bracketed] stage directions with the reply under an explicit
  [your reply] marker; the first shipped format ran the reply under a bare
  "Results:" line and the model learned to narrate the annotation ("No retrieval
  needed here") into student-facing prose (2026-08-10 eval, five answers)
- the preamble is that boundary and nothing else now. It used to restate the source
  discipline and the machinery ban, both of which have sections of their own
- examples steer harder than instructions, so they carry the tone and the
  length band; every number above is interpolated from Settings, never typed
- they carry the ORDER too: the first ends with a question under its cards, the
  second ends on its cards. A closing question in every example would teach a
  habit where the rule offers an option

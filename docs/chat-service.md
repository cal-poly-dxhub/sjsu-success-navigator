# The chat service (app/)

What the code in `app/` does that reading it will not tell you: the AWS behaviours it is
shaped around, the deploy invariants it depends on, the measured numbers behind its
constants, and the security properties that are properties rather than intentions.

Companion docs: `docs/accounts-and-storage.md` (the storage and auth design this
implements), `docs/cards-v2.md` (the card contract), `docs/system-prompt.md` (the prompt).
The code carries one-line pointers into the sections below.

## Settings

`app/settings.py` reads every value out of the environment the CDK stack sets, once, at
module import. There is no pydantic-settings and no `.env`: `config.yaml` is the single
source of truth and the stack is the only thing that turns it into environment variables,
so there is no second place a value can come from.

**Identity has no defaults, behaviour does.** Anything naming an AWS resource - the
knowledge base, the two model ids, the guardrail, the history table - raises `SettingsError`
at import when it is missing. The port's source project defaulted the knowledge-base id and
the model id to literals, so a misconfigured deploy ran happily against whatever those ids
pointed at. Failing at import means the function dies on its first invocation naming the
variable, rather than answering a student's question wrong. Behavioural knobs (top-k,
thresholds, caps) do carry defaults matching `config.yaml`, because a missing tuning value
is not ambiguous.

**`BEDROCK_REGION` exists because `AWS_REGION` is reserved.** Lambda sets `AWS_REGION`
itself and refuses to let a function's environment override it, so the stack passes the
region under its own key.

**`TITLE_MODEL_ID` is separate from `GENERATION_MODEL_ID` on purpose.** The two calls have
nothing in common: one writes a student's answer under a long system prompt, the other
writes four words inside a two-second budget. The small model is both cheaper and the one
that finishes.

**`converse_deadline_seconds` is 22 and the ceiling above it is 29.** An HTTP API
integration's `timeoutInMillis` maxes at 30,000 ms and is not raisable by a quota request,
so the chat Lambda's timeout is 29s by ceiling rather than by choice. The loop's 22s sits
under it with room for the response to be shaped and serialised; the handler narrows it
further using Lambda's own remaining time. If the loop ever does not fit, the fix is
architectural (streaming, or an async job with a poll), not a bigger number.

**Absence is the gate, and it has one spelling.** Two features are switched off by an
omitted environment variable rather than by a flag:

- `daily_message_limit` defaults to **0**, which is the one behavioural default here that
  deliberately does not match `config.yaml`'s value of 60. The stack omits the variable
  entirely when the cap is off, so an unset variable has to mean off. A default of 60 would
  mean a wiring mistake invented a limit nobody configured, and every student would start
  being refused on their 61st message with nothing in `config.yaml` to explain it. The
  opposite mistake, a dropped variable silently disabling the cap, is pinned by the infra
  suite instead (`test_the_chat_function_carries_the_daily_message_limit`).
- `escalation_recipient` defaults to `""`. With no address there is nothing to write to, so
  `app/prompts.py` never tells the model the tag exists and `app/escalation.py` builds no
  draft even if it emits one anyway.

**`rate_limit_exempt_client_ids` is empty by default,** so a missing variable exempts
nobody rather than everybody.

**`max_history_messages` (12) and the two read caps (40, 60) are different numbers for
different jobs.** The first is the window the *model* is shown, and every message in it is
paid for in tokens on every turn, so it is small. The read caps bound a browser read of
stored items, where the cost is one query and the wrong number is a student's own history
truncated in front of them.

**`title_max_chars` is one number for both things that can name a conversation:** the
model's answer (`app/titles.py` rejects anything longer) and the first-message truncation
that is its fallback (`app/history.py`). Two numbers would drift, and the drift would show
as a sidebar whose rows change length depending on which path named them.

**`escalation_max_chars` is the one cap that drops rather than truncates.** See
[Escalation](#escalation).

## The request path

`app/handler.py` serves five routes off one function: `POST /chat`, and `GET`, `PATCH`,
`DELETE` on `/conversations[/{conversationId}]`.

### Order on POST /chat

1. **validate** - reject a missing or oversized query as a clean 400 before anything bills.
2. **identity** - the Cognito `sub` from the JWT the API Gateway authorizer already
   validated. Never from the body.
3. **rate limit** - one atomic conditional write against the day's allowance.
4. **guardrail** - `ApplyGuardrail(source=INPUT)` on the bare query, `PROMPT_ATTACK` only.
5. **the turn** - write the student's message, read the previous N back, call the model,
   write the reply.

Each position is a property somebody argued for, and the WebSocket path
(`app/streaming.py`) repeats the order in full rather than reordering it:

- The rate limit is **before** the guardrail, so a refused turn spends one DynamoDB write
  and nothing billable, not even a guardrail text unit. A check after the guardrail would
  still bill a content-filter unit for every message an over-limit account sent.
- The guardrail is **before** the write, so a blocked message never becomes a turn. Storing
  it would smuggle the attack text into the history the model reads on the *next* turn, past
  the screen that just caught it.
- The student's message is written **before** the model call, so a disclosure that then
  times out is still on record.

### Identity, and why there is no history field

`sub` is read out of `requestContext.authorizer.jwt.claims` (HTTP API payload format 2.0),
where API Gateway has already checked the token's signature, issuer, audience and expiry. It
is the only place a user is identified and it does not read the body.

**`ChatRequest` has no `history` field, and its absence is the point.** Client-supplied
history is a prompt injection vector, not a memory bug: a forged assistant turn lets an
attacker establish rules the model then treats as its own prior commitment, which is a
different order of problem in an app receiving crisis disclosures. There is no sanitising a
forged turn, because a well-formed lie is indistinguishable from a true record. So a
`history` or `messages` key in the body is an unknown field and pydantic **drops** it:
ignored, not sanitised, and no field is left for a later latency optimisation to fill in.

There is no user id on the wire for the same reason turned up a level. A body field would be
the same value with nothing behind it, and every DynamoDB partition key is built from it. The
convenience and the vulnerability are the same line of code. `followup` stays on the wire
with no backend reader (`docs/cards-v2.md`, Tell me more); `sessionId` is gone.

`client_id` is read from the same validated claim set. A Cognito **access** token carries
`client_id` rather than `aud`, and API Gateway validates it against the authorizer's
audience allowlist, which is exactly two entries: the browser's client and the eval
harness's machine client. Its one use is the rate limit's exemption list. It is never an
identity, and nothing keys storage on it. An ID token would carry `aud` and no `client_id`,
so it reads as None and the caller falls under the limit, which is the safe direction.

### Failure postures

The rule across the whole path is that an infrastructure fault must not cost a student in
front of a screen their answer, and the log line is the alarm.

- **A guardrail failure is not a block.** If `ApplyGuardrail` errors, the request continues.
  Bedrock is the harder dependency behind it anyway, and a student who hits a transient
  outage should not be told their question was rejected.
- **A storage failure does not deny an answer.** Each write and read is guarded on its own
  and logs at ERROR. A failed write costs the record of one message; a failed read costs the
  context. If the user write failed ambiguously (landed, response lost), the read picks the
  message up and the orchestrator's consecutive-role merge folds it into the copy it
  appends, so the worst case is one sentence said twice rather than a rejected Converse call.
- **A rendering read failure is the whole response,** so `GET /conversations` returns 502
  rather than an empty list. An empty list would say "you have no conversations", which is a
  worse lie than "this did not load".
- **A stored item that no longer fits a live contract is dropped, not fatal.** The only way
  one gets there is a shape an older version of this code wrote, and refusing to open a
  conversation because one old card lost a field is worse than opening it without that card.

### Status codes

- A request with no `sub` claim is a **401**. The route is authorizer-gated, so it means a
  misconfigured stack or a direct invoke, not a student.
- A malformed conversation id is a **400** on every route that takes one. The value composes
  a DynamoDB sort-key prefix, so an id carrying a `#` would address keys the server did not
  intend. This is validation, not a security boundary: the boundary is the partition key.
- A **well-formed** id that is not the caller's own is a **200 with an empty list**. Not a
  404, which would confirm to a prober which ids exist somewhere, and not a 403, which would
  imply an owner check that could be got wrong. The only partition it can address is the
  caller's own.
- `DELETE` is **idempotent** and never 404s. A delete says what the caller wants the world to
  look like afterwards, and afterwards is the same either way. It also means the route cannot
  be used to ask which ids exist.
- `PATCH` does 404 when there is no such header, and that is not an existence oracle: the
  only header it can address is inside the caller's own partition.
- An **unknown route key is a 404**, never a fall-through to the chat turn. The stack creates
  a fixed set of routes, so an unknown one means somebody added a route and pointed it here,
  and having that quietly run a billable Bedrock turn on a GET is the kind of default that is
  discovered from an invoice. A **missing** route key runs the chat turn, which is a direct
  invoke from the console or a test harness.
- An over-cap title on `PATCH` is a 400 rather than a silent truncation: these are the
  student's own words, and a name shortened without saying so is a name they did not choose.

### Deadlines

Both deadlines are `time.monotonic()` timestamps and both take the **minimum of two
budgets**, because each catches what the other misses. The configured budget is the intent
and applies in a test or a local run with no Lambda context; `get_remaining_time_in_millis()`
is ground truth and already accounts for time this invocation has spent on a slow cold start
or a long guardrail call. Taking the smaller means a slow start shortens the loop's budget
rather than letting it overrun the function.

A reserve is held back from Lambda's remaining time for the work after the deadline's own
step: 3 seconds for the loop (the response still has to be shaped and serialised), 1 second
for titling (only `json.dumps` is left), 6 seconds in the stream worker (the reply is
written, the conversation may be titled, and the final payload is a network push).

**The title deadline is derived after the model call, not alongside the loop's.** Both are
monotonic timestamps, so one computed before a twenty-second model call would already be in
the past by the time the title needed it, and every new conversation would silently keep its
fallback name.

### What a turn writes

The assistant row records **the reply as the model wrote it**, tags and all, plus the
ref-to-URL pairs its cards cited. Two fields are recorded rather than re-derived:

- **the email draft**, because it cannot be re-derived: it was addressed from a recipient in
  deploy config and the address on the token that turn was sent with.
- **the location card**, because the catalogue is a directory that gets edited and an office
  that moves next month must not rewrite where a turn last month said it was.

The **safety panel goes the other way** deliberately: the model's keys are still in the
stored text, so `app/safety.py` resolves them again on the way out. The panel is a fixed
roster of crisis contacts, where a changed number *should* change in the transcript.

On the display read, the question travels with the answer: each assistant message is
rendered with the text of the user message before it, because that is what a card group is
labelled with.

## Storage

`app/history.py`. The design is `docs/accounts-and-storage.md`; what follows is what the
implementation depends on.

**Four sort-key prefixes share one partition, because the partition is already the user.**

| item | pk | sk |
|---|---|---|
| conversation header | `USER#<sub>` | `CONV#<convId>` |
| message | `USER#<sub>` | `MSG#<convId>#<ulid>` |
| daily rate counter | `USER#<sub>` | `RATE#DAY#<YYYY-MM-DD>` |
| open WebSocket connection | `USER#<sub>` | `CONN#<connectionId>` |

The last two are not history and are invisible to every read here, because the conversation
list is `begins_with('CONV#')` and both message reads are `begins_with('MSG#<convId>#')`.
Neither needed a new table or a new IAM grant. The connection record is keyed on the **user**
rather than on the connection id, even though a connection-keyed item sounds more natural:
API Gateway carries the `$connect` authorizer's context onto every later route, so the
message route already has a verified `sub`, and keeping every key derived from a JWT claim
preserves the property the whole table is built on.

**`USER#<sub>` is built from the JWT claim and never from the request body.** That is the
whole isolation story: a request cannot address another student's partition, because the
partition is not something a request can name. There is no owner attribute to filter on and
none to forget.

**ULIDs, not UUID4s, and monotonic within a container.** Messages are ordered by sort key
and nothing else, so the id has to sort lexicographically in write order; uuid4 sorts
arbitrarily and would shuffle a conversation, and a bare timestamp collides. Two ids minted
in the same millisecond would otherwise be ordered by their random halves, which is a coin
toss between a question and its answer, so the container remembers the last value and steps
past it. A Lambda container handles one request at a time; across containers the timestamp
already separates them. The 26-character Crockford base32 shape is pinned by
`models.CONVERSATION_ID_PATTERN`, which is what a client-supplied id is validated against, so
a minted id and an accepted id cannot drift.

**Conditional writes, and what each condition guards.**

- `_touch_header` uses an atomic `ADD` rather than read-modify-write, because two turns in
  flight would each write the count they read. It swallows its own failures: the message is
  already durable, and a drifted count is repairable from the messages while the messages are
  not repairable from anything.
- The header title is written with `if_not_exists`, so the **first** user message names the
  conversation and no later one renames it under the student.
- `set_generated_title` carries two conditions. `attribute_exists(sk)` stops an UpdateItem
  upsert from **creating** a header, which would otherwise mint a titled row with no messages
  under it for a conversation whose writes all failed. `attribute_not_exists(#userTitled)` is
  the promise to a student who renamed a chat: automatic titling can never overwrite a name
  they chose. Today the ordering alone would do it, but that is an accident of when things
  run and this is the property.
- `rename_conversation` is conditional on the header existing, so a rename of a forged or
  deleted id cannot create a titled, empty conversation in the student's own sidebar.

**Reserved words go through `ExpressionAttributeNames`.** DynamoDB's reserved list is long
and unmemorable (`count` is on it), and a bare collision fails the call at runtime rather
than at synth.

**A conditional failure is read off the error code, not caught as botocore's `ClientError`
class.** The code is the contract; botocore's exception classes are generated and their
identity has changed shape before. Catching a narrow named condition also means every other
failure (throttling, a network fault) is re-raised into the handler's 502 rather than being
quietly reported as "no such conversation". A throttled rename that told the student their
chat did not exist would be a lie the logs would not record. `app/streaming.py`'s
`GoneException` check is the same pattern.

**Reads are strongly consistent, and the case is a student sending a turn and reloading.**
For the context read the reason is sharper: two quick turns against an eventually consistent
read can miss the previous assistant reply, which silently loses a turn and lands the model
in exactly the alternation `docs/accounts-and-storage.md` warns about.

**Reads are descending with a Limit, then reversed in memory.** The newest N is the window
that matters, and asking DynamoDB for it directly means a long conversation costs the same
read as a short one. Ascending would page through the whole conversation to reach its end, or
cap a long one at its opening exchanges. The conversation list then re-sorts its page by
`lastActivityAt`, because "most recent" to a student means the last one they typed in, not
the last one they started. What that costs: a long-dormant conversation revived today does
not climb back *into* the page if it fell out of the newest `limit` by creation. Fixing it
needs a secondary index keyed on activity, which is the doc's purely-additive GSI and not
needed at pilot scale.

`recent_messages` fetches `limit + 1` when it is excluding this turn's own user message, so
the window stays the configured size rather than quietly one turn short.

**Delete is hard, paginated, and does messages before the header.** There is no tombstone
attribute: a student who deletes a conversation has said what they want, and a record that is
invisible but present is a promise this code cannot keep, with no `expiresAt` to finish the
job later. The ordering is the subtle part. A failure partway through leaves either some
messages gone with the header still there (an empty but **visible** conversation the student
can delete again, and the retry finishes it) or the header gone with messages still there (an
invisible orphaned transcript nothing lists, nothing can address, and no TTL collects). The
first is recoverable and the second is not. Pagination matters for the same reason: Query
returns at most 1 MB, so stopping at the first page would leave the tail behind as orphaned
data, quietly.

**Type conversions happen once, at the boundary.** A DynamoDB number arrives as a `Decimal`,
which `json.dumps` cannot serialise. DynamoDB map keys are strings and there is no numeric
key type, so source refs are written as `"2"` and read back as `2` in one place rather than
leaving every consumer to remember which side of the wire it is on.

**An unreadable item is skipped, not fatal.** The only way one gets there is a shape this
code did not write.

**Short DynamoDB timeouts (1s connect, 3s read) are not arbitrary:** these calls sit inside
the turn's 29-second budget alongside a Bedrock call that needs most of it, so a stalled
socket has to fail fast enough to leave the student an answer. No explicit region is passed,
because the table lives in the function's own region and Lambda always sets `AWS_REGION`;
passing one from settings would invent the possibility of them differing.

## The daily message cap

`app/ratelimit.py`. This is the first control that bounds what **one account** spends.
Everything else fencing cost bounds the service: the stage throttle bounds invocations
started per second, reserved concurrency bounds invocations running at once, and the loop's
deadline and iteration cap bound a single request. Not one of them can tell two students
apart, so a signed-in account could sit inside all four all day.

**The arithmetic.** An answered message costs roughly **$0.040**, so sixty a day is about
**$2.37** per person. The 60 lives in `config.yaml`; the per-message figure comes from
`eval/measure_usage.py` against the deployed stack (see `docs/eval-harness.md`).

**One atomic conditional write per turn, no read first.** `ADD #count :one` under
`attribute_not_exists(#count) OR #count < :limit`. The read-then-write shape is exactly the
race this has to survive: two turns in flight both read 59, both decide they are under the
limit, and both write 60. DynamoDB serialises updates to a single item and evaluates the
condition against the value it holds at that moment, so the increment and the decision are
one operation. `<` and not `<=`, because the count is how many have already been taken.

**Attempts, not answers, and before the guardrail.** Counting successes would make the
cheapest way to exceed the cap sending requests that fail, which is not a cap.

**A fixed UTC calendar day, not a sliding window.** A sliding window needs a read before the
write and its precision buys nothing here: the question is whether this person has spent a
day's worth of money, not at what instant the sixtieth message landed. UTC rather than a
campus-local midnight because a local boundary needs a timezone database in the function for
a boundary the student never reads. The 429 carries the reset **instant** and the browser
renders it in the student's own clock; the server's own sentence says "midnight UTC" for
callers with no timezone. `Retry-After` is rounded **up** and floored at 1, because the header
is a promise that waiting that long is enough.

**Nothing is written and no usage is returned on a refusal.** A refused turn is not a turn:
no model call, nothing screened, no message. Unlike a guardrail block, which billed a screen
and reports it, there is genuinely nothing to meter.

**It fails open.** A DynamoDB fault that is not a condition failure lets the turn through,
logged at ERROR. Same posture as the guardrail outage and the history writes, for the same
reason: the failure is not attributable to the student in front of the screen, the
service-wide fences are still standing, and the alternative turns a DynamoDB blip into a
total outage of a product that receives crisis disclosures.

**`expiresAt` is written with `if_not_exists`,** so the window's expiry is fixed by the
request that opened it. This is the first thing in the app to write the TTL attribute the
table has had enabled since creation: a counter for a day that has passed is meaningless,
which makes it the one item kind whose retention is not the open policy question the
transcripts are.

**The eval harness is exempt by `client_id`, not by a higher number.** It fires all 82
ground-truth questions as one account at concurrency 3, its machine client is one of exactly
two in the authorizer's audience, and a number would have to be kept above whatever
`ground-truth.yaml` grows to. The exemption is not logged per request: it would be the
loudest line in the log during a run and says nothing the client id does not.

## The Converse loop

`app/orchestrator.py`. One exit, two caps, and a text reply that is the answer on every path.

**Two independent caps, and they are not interchangeable.** `max_converse_iterations`
bounds how many model calls happen; the wall-clock deadline bounds how long they may take.
Six iterations of a slow model, or one retrieval that stalls, exceeds the function's budget
without ever reaching the iteration cap. The deadline is checked **before** each call, never
after: the point is that no Converse request starts which cannot finish inside the remaining
time. Both caps exit through the same response builder with whatever text the model produced;
the alternative is being killed mid-Converse, where the invocation is billed and the student
gets a gateway 504 carrying no response at all.

**Retrieval is primed on every turn and the tool is the escape hatch.** The 2026-08-10 eval:
of the turns that logged, 20 of 30 made exactly one search, 2 made two, and the single wrong
SKIP was a scored failure. Retrieval need is a property of the product, not a per-turn
decision, so the first search runs server-side on the student's own words and lands in the
transcript as a synthetic assistant `toolUse` plus its `toolResult`, in the exact wire shape
a real call produces. The model wakes up holding results and answers in one Converse call in
the common case. Two deliberate degradations: past the deadline nothing runs, and a retrieval
failure logs and returns so the model searches itself, which is the pre-priming behaviour. An
**empty** result set is appended rather than skipped, because results that found nothing are
the honest-gap signal the prompt teaches from. The primed exchange uses a fixed server-authored
`toolUseId`, which keeps a primed turn byte-identical for a given query and makes the
follow-up parity guarantee testable.

**Streaming is the only difference between the two transports, and it is deliberately the
smallest one that could work.** Passing no sink runs `Converse` exactly as `POST /chat`
always has. Passing a sink runs `ConverseStream` and reassembles the event stream into the
same `{"output": {"message": ...}, "stopReason": ..., "usage": ...}` dict, so the tool loop,
the deadline, the iteration cap, the exit and therefore every card, cap, dash and safety
decision are the same code reading the same complete text. There is no second parser and no
second exit, which makes "the streamed turn renders identically to the buffered one" a
property of the structure rather than a thing to keep testing. Token usage is taken from the
stream's own `metadata` event rather than recounted.

Details the stream forces: a tool's arguments arrive as **partial JSON fragments**, so no
prefix of them is valid until the last one lands and they are accumulated as a string and
parsed at the block close. Unparseable arguments become an empty input rather than an
exception, because the tool already defaults a missing query to the student's own message, so
the search still happens where a raise would lose a reply the model had already written.
Content-block indices are the stream's and nothing promises they arrive in order, so blocks
are held in a dict. A block the stream never closed is kept, for the same instinct the
zero-card fallback has. An unknown event is ignored, so a new Bedrock event type cannot fail a
turn. Every push to the sink is wrapped, because the sink writes to a socket the student may
have closed and a broken pipe is not a reason to abandon a reply that is already paid for.

**The model is never shown its own markup.** A stored assistant reply is the model's raw
text, which is what makes a reopened conversation re-renderable, so the card, safety and
escalation tags are still in it. Handing them back would teach the model that a transcript is
a place where tags appear, and a `<safety>` tag copied out of last week's reply is a panel
fired by imitation rather than by triage. The tags come off at the one point history becomes
model input, not at the store, so the record stays whole and every caller gets the same
treatment. **A student's own message goes through untouched:** it is their words, stripping
them would quietly edit a disclosure, and a student who types an angle bracket typed one.

**The new message is appended before the consecutive-role merge.** That ordering is the fix
for the reef `docs/accounts-and-storage.md` names: a turn whose model call failed leaves a
user message with no assistant reply, so the next turn reads a history ending in a user role,
and Converse rejects two user messages in a row outright. Without the merge, one failed turn
would poison every turn after it in that conversation. Merging afterwards folds the dangling
message into this one, so the disclosure that never got an answer is still in front of the
model. Converse also requires the **first** message to be a user turn, and a window that
begins mid-conversation can open on an assistant reply, so those are dropped from the front.

**The clock goes on this turn's message and no other.** One instant is read per turn, so
every Converse call in the loop carries the same time rather than drifting across a long
turn. The history loop never restamps a stored message: what a stored row records is when it
was written, and stamping a read-back message with the current time would tell the model a
message from Tuesday arrived just now.

**Boto3 clients are built once at module scope.** The source project built one per request,
which on Lambda discards the warm container's connection pool on every invocation. The loop's
client has a 25s read timeout, not the source's 120: the function's own budget is 29s, so a
socket that outlives it can only turn a diagnosable timeout into a gateway 504. Adaptive
retries mean a Bedrock throttle is retried rather than surfacing to the student as a 502.

**The one hardcoded reply left on this path** is reachable in exactly one situation: the
model produced no text at all before the loop ran out of time or iterations. An empty bubble
is the alternative, so it is an admission that there is no answer rather than a substitute
for one. The previous design fell through to a mechanical card builder here, which is why a
timeout used to produce cards nobody had written, answering a question nobody had read.

## Cards and the tag contract

`app/cards.py`, against `docs/cards-v2.md`. The parts below are what the code depends on
rather than what the contract says.

**The model cannot express a URL.** A card cites `ref="2"`, an integer from this turn's
retrieval list, and the server resolves it against a map it built itself. The model is never
shown a URL at all: the tool result carries `id`, `title` and `text` and nothing else. "Do
not invent URLs" therefore stops being an instruction that can be followed or ignored and
becomes a shape the output has no room for.

**Ids are per-turn and deduplicated by URL across every retrieval the loop makes,** so a
source returned by two searches keeps the id it was given first and the model is never shown
one page under two numbers. Ids are not stable across turns: a ref from a previous turn is
exactly as unresolvable as one the model invented. What *is* persisted is the finished pairs a
reply cited, which is what lets a stored reply be re-rendered without re-running the search.
Re-running it would resolve the same ref against today's index, which can be a different page
from the one the student saw.

**An unresolvable ref keeps the card, minus its source button.** This is decided against
`docs/cards-v2.md`, which drops the whole card, and the reason is observability: a card that
renders without its link is a visible symptom, where a silently dropped card is a student
seeing three cards instead of four and nobody finding out. The event is logged at WARNING
with both the bad ref and the ids that were available, because the UI is the weaker half of
that signal. The cost is real: allowing a linkless card now means tightening the rule later
has to fight prompts that learned the loose version.

**The tool result carries the whole retrieved chunk, untruncated.** A 500-character excerpt
cap used to sit here. Real chunks run 800 to 3200 characters and the recovered contact band
sits at the document tail, so the model cited the right page while honestly reporting it
could not see the number retrieval had already fetched: the Bursar's line was in six of eight
retrieved chunks, every occurrence past the cut, and CAPS's at character 612 of the top hit.
Nine of the fourteen failures in the 2026-08-10 eval run (60 pass / 14 fail / 8 unsure of 82)
were this shape. The chunk is bounded upstream by the ingestion chunking config, so a second
blind cap in this layer had no job to do; the slice and its constant were removed rather than
resized.

**Every length cap is one value, on Settings, read in exactly two places:** here, where it is
enforced, and `app/prompts.py`, where it is written into the contract the model is given. So
the number the model is told is by construction the number the server applies. The caps are
**runaway guards**, set far above the length the prompt steers toward, so a truncation is a
bug in the prompt or the model rather than routine variance, and every hit is logged at
WARNING. An ellipsis on screen means something is broken.

**A follow-up prompt is never truncated,** and past the cap it keeps its button. A shortened
question is a different question, so trimming would silently ask something other than what
the model wrote; the text is displayed on click as the student's own turn, where a long one
wraps like any typed message. The overrun is still logged, because the cap guards a paid
model input.

**`<desc>` keeps its line breaks; every other field is collapsed to one line.** The display
parser keys on line starts, so a bullet is a line beginning with a marker, and a description
whose newlines were flattened arrives as one paragraph reading `... an advisor has read. -
Email: ... - Walk in: ...` with the list gone and nothing on screen to say so. Every other
field genuinely is one line: a title is a heading, a follow-up is a question. Indentation is
still collapsed, because a model that indents its bullets under `<desc>` is formatting its
XML rather than asking for leading space.

**Em and en dashes are rewritten out of everything the student reads,** before the caps are
measured, so the length checked is the length displayed. An en dash between digits becomes a
hyphen (a range); every other em or en dash with its surrounding whitespace becomes a comma
and a space. The prompt bans them and its examples model the ban; this is the deterministic
backstop, and each substitution is logged at INFO so the model's dash rate stays measurable
from the logs alone. The constants are written as escapes rather than as the characters
themselves, so grepping the repo for a literal dash stays a meaningful check. `app/prompts.py`
and `app/titles.py` are kept dash-free for the same reason: a dash in the prompt would teach
the habit the server then edits, and examples steer harder than prohibitions.

**Tag scrubbing is deliberately narrow.** A generic `<[^>]+>` sweep would eat content the
model may legitimately write about; the guarantee needed is only that *our* tags never
surface. Safety, escalation and place blocks are removed **whole**, content included, before
the tag sweep, because their content is addressed to the server or to a mail client rather
than to the student: a fallback that stripped only the tags would leak `crisis-988, caps` or
`career-center` into the bubble as text, or say an unsent email twice.

**Safety, escalation and place tags are read from both sides of the card split.** The split
point says where prose renders, not which tags count, and a tag written under the cards is
still a tag. Losing a safety tag would cost the panel entirely, which is the one failure this
module cannot afford. They are read with the card blocks already removed, so a stray tag
inside a card body cannot fire a handoff or conjure a card.

**One escalation block and one place block per turn.** A second is ignored and logged: a turn
makes one offer and points at one place, and choosing between two would put an editorial rule
in a parser. A place block carrying several keys keeps the first, unlike the safety panel,
which lists every resource that fits, because a location card has one address on it.

**`preview_safe_prefix` is not a parser and must never become one.** It answers one question
- where does the part that is safe to show a student end - and it answers by stopping, never
by interpreting. Nothing it returns builds a card, resolves a ref, decides a handoff or
applies a cap; all of that comes off the complete reply. It is **append-only**, which is what
makes it safe to stream: the text only grows and this only ever stops earlier or later in it,
so the returned prefix never shrinks and never rewrites what was already sent. A `<` that is
not one of ours does not stop it (`under <15 units` streams intact); a trailing partial
(`...see <ca`) does, because the rest of that tag has not arrived and might. It deliberately
does no dash normalisation and no capping: those belong to the finished reply, and a
substitution near the end of a partial string could rewrite text already on screen.

**`card_block_started` waits for the whole opening where the preview stops on a partial.**
Saying "cards are coming" on a maybe is exactly the promise that must not be made. A
`<safety` anywhere takes it back to no, because a safety turn drops its cards by contract
however many blocks the model wrote.

## Safety

`app/safety.py`. There is no pre-model phrase gate (decision, 2026-08-10). The system prompt
carries the emergency instruction and a keyed resource roster; the model emits
`<safety>key, key</safety>`; this module resolves keys into the fixed contact panel.

**The model decides when and which, the table decides what the student sees.** The table is
the `safety` rows of `data/contacts.csv`, read at import. Label, number and link come only
from it, so a contact the model invented has no way onto the
screen, because keys are the only thing it can say. The prompt's roster is built from the same
dict the resolver reads, so a key the model is taught always resolves and the only key that
can miss is one it invented.

**Failure direction is always toward showing help.** An unknown key is dropped with a
WARNING; a tag whose keys all fail to resolve gets the default crisis set; prose that cites
crisis lines without the tag has the panel attached anyway. No path renders an empty panel.

**Contact facts are drawn from the live SJSU pages, verified 2026-08-10 against
`eval/ground-truth.yaml`, and are never LLM-generated.**

**A safety turn carries no cards, no location card and no escalation offer, and the panel
sits directly under the whole message.** Attaching the panel collapses a split reply back
into one bubble, because the cards it dropped are what trailing prose renders under, and
leaving the split would put half the message below the panel. That placement is a safety
property, so it is enforced beside the card drop rather than left to the caller. The
escalation offer goes for the same reason: a safety turn's answer is the panel, and an email
the student has to write and wait on does not belong between them and a number that answers
now. The location card goes on the same line: a map and a walking route are an errand, and a
turn that attached the panel did so because somebody needs a number now.

There are two routes into a safety turn and both are covered. The orchestrator handles a
tagged turn; `apply_safety_handoff_to_response` handles the other one, where the model thought
it was writing an ordinary reply, named a hotline in prose, and offered to email an office.

**A server-authored sentence introduces a panel with no prose.** It and the escalation
fallback line are the only two sentences the server writes in its own voice, and each exists
because the alternative is a component on screen with nothing saying why it is there.

## Campus location cards

`app/places.py`. The same split a third time: the model writes `<place>career-center</place>`
and the server attaches the name, the address, the map and the directions link. There is
nowhere in the shape to put a model-authored address, so it is unrepresentable rather than
validated and rejected.

**An unlisted place yields no card.** Not the nearest key, not the building it is near, not
the front counter of the building it is in. An unknown key is dropped with a WARNING and the
reply keeps its prose and its cards. The rule is enforced in two places because only one of
them can catch it: the server drops a key it does not know, and the prompt states that an
unlisted place gets no block, because a model reaching for a **neighbouring** key produces a
card that resolves, renders and is wrong, which no server-side check can see.

**Two tables, `data/places.csv` and `data/buildings.csv`, because sixteen offices sit in
five buildings.** Four are inside Clark Hall and
five inside the Student Services Center, so the coordinate and the map belong to the building
while the room number belongs to the place. That is what stops five near-identical renders and
five chances to mis-key one of them.

**No Google Maps API, no key anywhere, and no third-party request at all.** The map is a
picture this project renders and serves itself: `scripts/render_place_maps.py` stitches
OpenStreetMap tiles around each building's coordinate, draws the pin, bakes in the attribution
and writes `frontend/public/places/<building>.webp`, which is committed and served off the
same CloudFront distribution as the page. Google's Static Maps endpoint returns exactly this
picture and was rejected on both counts: it needs a key and a billing account, and its terms
forbid storing what it returns, which is the whole idea. The standard OSM style was kept over
CARTO's sharper one because CARTO renders campus buildings as anonymous blocks, and the
building's name under the pin is what makes the picture useful. The directions link is a plain
keyless Maps URL (`google.com/maps/dir/?api=1&destination=...`) built from a curated
`directions_destination` the table owns, requested only when a student presses it. The curated
query is used rather than the coordinate beside it because Maps labels a coordinate
destination with six decimal places instead of the building's name.

**Anything editing a coordinate has to re-run the render script,** because the committed
image is a picture of that exact point.

**No length cap touches this path.** The caps guard model-authored text and there is none
here; an address cut at a word boundary is a student sent to a room number that stops
mid-way.

**A card with no map is a complete card.** `map_image_url` is None when a building has not
been rendered, which is where a new catalogue entry lands before anybody runs the script.
Name, address and directions answer "where is it?"; the map is what makes it quick. A test
asserts every building in the table has a committed image and that no image is orphaned,
because the images are committed rather than built and nothing at deploy time would otherwise
notice one missing.

**Where the facts come from.** Addresses are `eval/ground-truth.yaml`, read off the live
pages on 2026-08-10, and each entry names its pair id so an address can be traced back to the
line it came from. Coordinates were resolved separately (2026-08-17) and every one was checked
against OpenStreetMap's own data: the named building is within 45 m of the point, and the
rendered tile prints that name under the pin. Two needed a human:

- **The Student Services Center is not at the corner it is advertised at.** SJSU says "the
  ground level of the North Parking Garage at the corner of 9th and San Fernando"; that corner
  is Boyce Gate and has no building on it. The centre is mid-block on South 9th, which is where
  OSM names it, where 60 S 9th St geocodes, and where SJSU's own campus map puts SSC beside the
  North Garage. An earlier pass flagged this coordinate as wrong on the assumption that campus
  stops at San Fernando. It does not.
- **The CPGE building is not a building.** Every SJSU page gives the Accessible Education
  Center's address as "College Professional and Global Education (CPGE) Building, 2nd Floor"
  with no cross street, and no such building appears on SJSU's 2017 or 2020 campus map index,
  in OpenStreetMap, or in any gazetteer. It is the CPGE Suite on the Student Union's second
  floor (confirmed 2026-08-17).

## Escalation

`app/escalation.py`. The model writes the words, the server addresses the mail.

**Nothing sends from here, and that is the design.** There is no SES client in this repo, no
verified sending identity, and no route that posts a message anywhere. What this produces is
a **draft**: three strings the browser hands to the student's own mail client. The student
presses send, from their own address, so a staff reply lands in the mailbox they actually read
instead of in a no-reply nobody owns. That is also why the draft needs no `from` field and the
path needs no verified sending identity.

**The model cannot address an email,** in exactly the way it cannot author a card's URL. The
tag it writes is `<escalate_to_human>` with prose inside it and nothing else: no id, no
attributes, no recipient. The subject comes from deploy config and the address from
`data/contacts.csv`, via the row `escalation.contact` names.

**The draft names no addresses at all.** It used to end with "you can reach me at <the
student's verified email>", read from the JWT claim, which told a member of staff something the
message header already tells them, since it leaves from the student's own mail client and their
own account. The line is gone, and with it every reason this path needed to know who the
student is. A draft is now a function of the model's prose and config, and nothing else. It
carries no id and no conversation reference either: everything in it is text the student reads
on screen before pressing send, so anything that could not be shown to them has no business
being in it.

**The cap drops the offer rather than cutting it.** Every other length cap in this app
truncates at a word boundary and logs; this one refuses. A card that ends in an ellipsis is a
visible symptom in front of the person who can judge it, but a truncated email is a
half-sentence the student may send to a stranger before noticing what went missing. Over the
cap there is no offer, and the WARNING says so. The prompt states this too, because leaving it
implicit would teach the card contract's habit (write to the ceiling, the server will tidy it)
on the one path where the tidying is a half-written message to a stranger.

**The recipient's presence is the whole gate,** read through one function by both the prompt
builder and the assembler, so the model is never taught a tag whose output the server would
discard, including in the one case where the two could have disagreed: a recipient that is
nothing but whitespace. The address is stripped in the assembler as well as in
`load_settings`, because a `Settings` can also be built directly.

**One server-authored line goes on every draft,** saying it was written with the Navigator's
help, because staff open a message written with a machine's help and it should say so.

**The mailto's line breaks are CRLF, and the draft's structure rides on it.** This is
`<desc>` keeps its line breaks (docs/cards-v2.md) one layer further out. The server writes
the draft with real newlines and the panel renders it in a `<pre>`, so the paragraphs and the
model's `-` bullets are on screen. The link that opens the mail client is a URL, though, and
RFC 6068 section 5, like RFC 2368 before it, says a line break in a `mailto` body MUST be
encoded `%0D%0A`. `encodeURIComponent` on its own writes `%0A`, a client is within its rights
to run the lines together, and what arrives is one block of text that the message on screen
was not. `frontend/src/lib/mailtoDraft.ts` converts the line endings at the URL boundary and
nowhere else: the stored bytes, the panel and the clipboard copy are untouched, so what is on
screen is still what gets sent. It costs six encoded characters a break rather than three,
spent out of the same ~2,000 budget, which is why the length that decides between the button
and the copy path is measured after the conversion rather than on the text.

## Streaming

`app/streaming.py` (the WebSocket routes) and `app/stream_worker.py` (the generation half).
There is a second streaming transport in the tree, the FastAPI app under the Lambda Web
Adapter (`app/stream_probe.py`), which runs the same turn as `POST /chat` in one function and
streams it as NDJSON. The frames both transports send, and the rules for what is safe to show
of a half-written reply, live once in `app/preview.py`.

**The HTTP stream is reachable two ways, and that is a measurement rather than a fallback.**
Its Function URL stays IAM-signed and open; the site's CloudFront distribution now also
serves it on a `/api/*` behaviour with origin access control. The open question is whether a
streamed body survives the edge or is buffered there - it is not stated in the CloudFront
developer guide, we could not find it stated anywhere, and it decides both the browser client
and where the auth header can live. It cannot be answered locally: uvicorn streams (checked
with `curl -N`), Lambda's `InvokeMode` is a deploy-time property of the URL, and the edge is a
third hop again. So the pair exists to be curled against each other on one route -
`time_starttransfer` against `time_total`, direct and through the edge - and the direct URL is
the control that must not be taken away.

**The edge behaviour and the app's routes are one string, spelled twice.** CloudFront matches
a behaviour on the viewer's path and forwards that path to the origin unchanged; there is no
prefix-stripping short of a rewrite function. So every route the outside world calls moved
under `/api` (`EDGE_PATH_PREFIX` in `stream_probe.py`, `_STREAM_EDGE_PATH_PREFIX` in the
stack) and a test reads both off disk, because a mismatch synthesizes clean, deploys clean,
and is a 404 from FastAPI served through a distribution behaving exactly as configured. `/`
is the exception and stays at the app's root: it is the adapter's readiness target, polled on
127.0.0.1 before anything is forwarded, and a readiness check answering 404 is a function that
never starts. Leaving the root to the app is also what leaves it to the site at the edge.

**Three settings on that behaviour are load-bearing and none of them fails at synth.** Caching
is disabled, because a cached turn is one student's answer served to another. The origin
request policy is AWS's `AllViewerExceptHostHeader`, because OAC signs each origin request
over the *origin's* host - forwarding the viewer's would make every signature fail to validate
at Lambda. Compression is off against a CDK default of on, because compressing is holding
bytes to compress them and this endpoint exists to measure whether the edge holds bytes.

**OAC needs two invoke actions and CDK writes one.** `FunctionUrlOrigin.withOriginAccessControl`
emits a single `lambda:InvokeFunctionUrl` permission; invoking a Function URL has needed
`lambda:InvokeFunction` alongside it since October 2025, which is why the CloudFront developer
guide's own setup is two `add-permission` calls. With one of them the edge answers 403
AccessDenied, which reads like a signing mistake and is not. The stack writes the second by
hand, scoped by `SourceArn` to its own distribution - `cloudfront.amazonaws.com` is a service
principal every AWS customer shares, so an unconditioned grant is one any distribution in any
account could use.

**Auth is decided in the app, because there is nowhere else to put it.** `POST /api/chat`
used to answer 401 to every caller: behind IAM auth with origin access control the Function
URL's request context carries `authorizer.iam` - the edge's principal - and no `jwt` block at
all, and a Function URL accepts no authorizer. So the app verifies the token itself
(`app/token_auth.py`), and the section below is what that costs and what it buys.

**Why a WebSocket and not response streaming.** Two constraints meet and leave one door. API
Gateway response streaming is REST-API only and this is an HTTP API; Lambda response streaming
supports Node.js and custom runtimes only and this agent loop is Python. In-band streaming
therefore means rewriting the loop in another language. A WebSocket API keeps the loop where it
is and moves the streaming out of band: the model's tokens go out through `ConverseStream` and
`post_to_connection` while the HTTP request that started the turn has already returned.

**The browser reads the HTTP stream, and nothing chooses between transports any more.**
`frontend/src/lib/chatStream.ts` POSTs the turn to `/api/chat` and reads the NDJSON body
off a `fetch` stream reader. `EventSource` was never available: a turn carries a body and
SSE can only issue a GET. The path is RELATIVE, because `/api/*` is a behaviour on the
same distribution that served the bundle - one origin for the site and the stream means no
second hostname, no CORS allowlist to keep in step, and no preflight in front of a request
whose entire value is time to first byte. There is no config key to read and no gate: a
deployment with nothing behind `/api` answers the POST, and the turn falls back to
`POST /chat` on the same line it always did.

**Two headers, and neither of them is `Authorization`.** The access token rides
`AUTH_HEADER_NAME` for the reason [below](#verifying-the-token-in-the-streaming-app), and
the client sends `x-amz-content-sha256`: the hex SHA-256 of the body it is about to send.
Origin access control signs each origin request with SigV4, and Lambda refuses an origin
request whose payload is unsigned, so a client sending a body has to hand the edge the
hash to sign over. It is a HASH and not a signature - it commits the request to its own
bytes, it authenticates nobody on its own, and computing it needs no AWS credentials,
which is why a browser can produce one while holding none. Both are pinned across the
language boundary by infra tests that read the TypeScript constant and the Python one off
disk and compare them, because a disagreement in either is a 404 or a 401 that
synthesizes, deploys and builds clean.

**The `accepted` frame is where the browser learns the conversation id,** and it records
it there rather than off the final payload. `applyChatResponse` still carries the id for
the buffered path, so the two are written with `??` rather than a single condition - a
conversation that learned its id from the first frame must still take its NAME from the
reply that named it.

**The socket is still synthesized and nothing opens it.** `POST /chat` is untouched and
stays the fallback whenever the stream cannot carry a turn. The WebSocket surface is still
gated on one config key and still deployed with it on, but no client reaches it any more -
removing it is the next commit, not this one.

**What streams is a preview.** The deltas are prose only and are never parsed, capped or
trusted. Cards, caps, dash normalisation, the trailing-text split and the safety decision all
derive from the complete reply, in exactly the code `POST /chat` runs. One final message
carries the authoritative payload: the identical `ChatResponse` `POST /chat` would have
returned. The browser throws the preview away and renders that.

**Why the generation is a separate function.** A WebSocket route integration has the same
29-second ceiling every API Gateway integration has, and the agent loop can use most of it. So
the route function does the fast ordered part and hands off asynchronously; the worker is not
behind the gateway at all, which is why the timeout stops applying.

**The async invoke's retry count is zero, set on the function and on the client that calls
it.** Lambda retries a failed asynchronous invocation twice by default, and a retried worker
answers the same question a second and third time, down the same socket, billing the model each
time. There is no idempotency key that would make that safe. The client's own `max_attempts` is
1 for the same reason: a retry racing a response the first call already delivered would start a
second worker.

**The worker's loop budget is deliberately the same number as the buffered path,** even
though it is not behind the gateway ceiling and could be given more. A longer budget would make
a streamed turn answer questions a buffered turn gives up on, and identical rendering for the
same question is the property this feature is held to.

**Frames are batched, not per token, and that is a cost control.** Every push is a billable
API Gateway message, and the browser reveals text at about 108 characters a second while the
model outruns that, so a frame per token would multiply the message count by the token count
to no visible end. Defaults come from `config.yaml`; a dropped variable batches rather than
pushing per token.

**A 410 is the student closing the tab.** It stops the pushing and nothing else: the turn
finishes and is persisted, because the model call is already paid for and because a user
message with no assistant reply is the dangling turn `docs/accounts-and-storage.md` calls a
reef. Coming back to a coherent conversation is worth the writes. One 410 sets a flag so the
rest of the turn's frames stop rather than raising once per delta.

**The sink's offset indexes its own raw accumulated text,** which is why `flush` takes no
argument. Slicing the parsed prose with an offset taken from the raw stream sends a fragment
that begins mid-word, which is the mistake an earlier version made.

**The cards status frame is sent once, after the prose tail is flushed.** The signal is the
model's own output rather than a timer: `<card` in the stream is the same event that stops the
preview, so the frame marks the exact instant the prose ended. A reply that never writes a card
never sends it, which is what stops the browser promising resources that are not coming.
Flushing first is load-bearing twice: the safe prefix cannot grow past this point, so the last
words of the lead-in must not sit in the batcher behind a `min_chars` threshold they will never
reach, and the browser clears the indicator on any arriving prose, so a delta landing after the
frame would take it back off.

**The callback endpoint and the worker's function name come from the stack, never from the
event.** The event carries `domainName` and `stage`, and assembling the URL a reply is sent to
out of request data is how a request ends up choosing its own destination. They are read with
a default of `""` rather than through `settings._required`, because they exist only when the
streaming gate is on and `settings.py` is imported by the chat function too.

**The guardrail screen is repeated rather than imported from `handler.py`,** because importing
that module would pull the whole HTTP request path and its module-scope Bedrock client into a
function that serves neither. The thing that must not drift is the guardrail **identity**, and
that comes from Settings, which both read from the same environment. The same reasoning gives
the streaming path its own `ConversationStore` instance.

**Output guardrail: `sync` is the only mode this can emit, structurally.** `async` releases
text to the student before it has been scanned, which is not a screen, and it does not support
PII masking. There is no configuration that produces it. The feature is off by default and
measured: the only safe stream mode holds the reply back to scan it in chunks, which spends
most of this feature's benefit on a screen today's guardrail cannot fire.

**A turn id is minted per turn and carried on every frame,** so a reply arriving after the
student has moved on, or two turns racing on one connection, lands on the right bubble or is
discarded.

**The `accepted` frame announces the conversation id, and it leads the turn on both
streaming transports.** The server mints the id and an absent one means a new conversation
(`docs/accounts-and-storage.md`), so on a conversation the client could not name, the stream
is the only place a browser learns it - and it needs it to place the sidebar row and to
address its next turn, both of which happen long before a reply finishes. It goes out the
instant the student's message is on record under that id, ahead of the retrieval status and
every delta, and it is sent on a **continuing** conversation too, echoing the id that
arrived: a frame whose presence depended on newness would make the client's own state decide
whether it gets told. On a query the input guardrail blocks there is no frame, because the
screen runs before the write and no id was ever minted.

The frame itself is one method on `PreviewSink` (`app/preview.py`), which is what stops the
two transports growing two spellings of it. The socket sends it from `app/streaming.py`
rather than from the turn, and that is not a second copy of the decision: that path splits a
turn across two functions, so the half that mints the id is the route function and the frame
has to leave before the worker is invoked. The HTTP stream runs the whole turn in one place,
so `app/turn.py` sends it at the same point of the same order. A buffered `POST /chat` has no
sink and sends nothing - it carries the id in the response the caller is already waiting for,
which for a caller that waits is the same instant.

**`$default` is not the message route.** The named `sendMessage` route carries turns;
`$default` catches a frame whose `action` names no route, which is a malformed client rather
than a turn, so it is refused. A billable path should not be the thing an unrecognised request
falls into. The WebSocket route key lives at `requestContext.routeKey`, one level down from
where an HTTP API payload 2.0 event carries it, which is part of why the two handlers are
separate functions: a shared one would have to guess which protocol it was serving, and
guessing wrong on a WebSocket frame would run a billable chat turn.

**Connection records carry a 3-hour TTL against API Gateway's own quotas:** 10 minutes idle,
2 hours hard. Three hours is the hard cap plus an hour of slack, so the only rows TTL ever
collects are the ones `$disconnect` failed to remove. `$disconnect` is best effort by nature,
because API Gateway does not guarantee it fires at all. `expiresAt` is wall clock rather than
`time.monotonic()`, because TTL is an absolute epoch second DynamoDB compares against its own
clock.

### Verifying the token in the streaming app

`app/token_auth.py`. The third implementation of the same decision, and the reason there are
three is that each transport is handed its identity by something different: `POST /chat` by
API Gateway's native JWT authorizer, the socket by `app/ws_authorizer.py` at the handshake,
and the streaming app by nothing at all.

**The token rides its own header, and that is forced rather than preferred.** Origin access
control signs each origin request with SigV4 and the signature lives in `Authorization`, so a
token in that header is a token CloudFront overwrites on its way past. The `/api/*` behaviour
forwards every other viewer header (`AllViewerExceptHostHeader`), so a header of the app's own
arrives intact. `AUTH_HEADER_NAME` is the only place its name is written - the treatment
`EDGE_PATH_PREFIX` gets, for the same reason, and an infra test scans `app/` and `infra/` to
keep it that way. The two credentials are one per layer and neither substitutes for the other:
SigV4 says the request may reach the function, the token says who it is for.

**The checks are the `$connect` authorizer's, for the same Cognito reasons.** Signature
against the pool's JWKS, issuer, expiry, `token_use`, and `client_id` against an allowlist -
`verify_aud` off, because a Cognito access token carries no `aud`; `token_use` checked,
because an ID token does carry one and would otherwise sail past a `client_id` check that
never ran. RS256 is pinned rather than read off the token, which is what refuses `alg: none`
and the HS256-signed-with-the-public-key confusion. It is a separate module rather than an
import of `ws_authorizer.py` because that function's bundle is exactly one file and a test
pins it that way; what must not drift is the decision, and both read the same pool and the
same two clients out of the same stack.

**Two clients pass.** The browser's and the eval harness's machine client both send turns
here, so pinning a single audience would serve every student and 401 every eval run.

**The allowlist arrives as a Parameter Store name, and that is a dependency cycle rather
than a preference.** The obvious spelling - both client ids in the function's environment,
the way the `$connect` authorizer gets them - is a cycle CDK refuses to synth, and a real
one: the browser's app client carries the CloudFront domain as its OAuth callback, the
distribution serves this function's URL on `/api/*`, the URL belongs to the function, and the
function would name the client. Three of those four edges are load-bearing and none is ours
to remove. The ids are the only value in the loop read by *code* rather than by
CloudFormation, so they are the only one that can be deferred past deploy: the stack writes
them into a parameter named after itself, the function carries the name, and a name assembled
from pseudo-parameters references nothing. The IAM grant is written by hand for the same
reason - `parameter.grant_read(fn)` would put the cycle back through the policy.

**Nothing is fetched per request.** The JWKS client is built once per container and caches
signing keys by `kid`; the allowlist is read once on the same cold start. A *failed*
parameter read is deliberately not cached, because a warm container lives for hours and one
throttled call would otherwise 401 every student until it recycled.

**Identity is `sub` and nothing else,** and no route reads a caller-supplied id. The body goes
through the same `ChatRequest` `POST /chat` validates, which drops unknown keys; the only key
the route pulls out by hand is the query. There is no bypass flag and no header that asserts
a `sub` without a token behind it, so every way a token can fail to verify is the same 401
with no detail on the wire - a 401 that distinguished "expired" from "not one of our clients"
would be an oracle, and a browser does the same thing either way.

### The $connect authorizer

`app/ws_authorizer.py`. The HTTP API gates its routes with API Gateway's **native** JWT
authorizer: no code, no cold start, none of our logic in the auth decision. A WebSocket API
has no such thing. The only authorizer type it accepts is a REQUEST (Lambda) authorizer, and
it may only be attached to `$connect`. So this module re-implements what the HTTP API's
authorizer already does: signature against the pool's JWKS, issuer, expiry, and `client_id`
against the same allowlist.

**`$connect` alone is the model, not a limitation.** A WebSocket connection is authorized
once, at the handshake, and every frame after rides the connection that handshake opened. API
Gateway carries this function's `context` onto every later route invocation, `$default` and
`$disconnect` included (verified against a deployed probe), which is what lets the message
route read `sub` from a validated token rather than from anything the client typed. Values in
`context` must be strings; API Gateway silently drops other types.

**The token arrives in the query string, and that is measured rather than preferred.** The
better-looking option is `Sec-WebSocket-Protocol`, keeping it out of URLs entirely, and API
Gateway does accept that header as an identity source. It just never echoes the subprotocol
back in its 101 response, and RFC 6455 says a client whose requested subprotocol is not echoed
must fail the connection. Probed against a real deployed WebSocket API: the 101 came back with
no `Sec-WebSocket-Protocol` header, and both Chrome 151 and Node's WHATWG WebSocket fired
`error` and never opened. The query string is what is left.

**What that costs, and what pays it back.** A token in a URL is a token that can end up in a
log, and this function is the first place it could: the authorizer event carries
`queryStringParameters.token` in full. So **nothing in that file may ever log the event, the
token, or anything derived from it but the claims.** That is why it has no debug logging at
all, why the rejection path re-raises a bare exception rather than one that might carry a
token fragment, and why `test_the_authorizer_never_logs_the_token` pins it. The stack
configures no access logging on the WebSocket API, so there is no access log for it to land in
either.

**A failure is a raise, not a Deny policy.** Raising makes API Gateway answer the handshake
401; returning an explicit Deny makes it 403. 401 is the honest answer to a bad token and is
what the HTTP API's authorizer returns for one.

**`verify_aud` is off and `client_id` is checked by hand,** which is the same documented
Cognito quirk the HTTP API authorizer's audience list relies on: a Cognito **access** token
carries no `aud` claim, it carries `client_id`, so audience verification would reject every
token this app issues. `token_use` is checked because the quirk cuts both ways: an ID token
*does* carry `aud`, so without that line an ID token for the same client would sail through a
`client_id` check that never ran. The HTTP API rejects ID tokens by the same mechanism.

**The policy is scoped to the method ARN that was asked for, never `*`.** A wildcard would be
a policy that outlives the question it was asked.

**The identity source must agree letter for letter with the authorizer's declaration**
(`route.request.querystring.token`). A mismatch does not read as a bug: an identity source
absent from the request means API Gateway never invokes the function at all, so the failure
arrives as a handshake rejection with nothing in these logs to explain it.

**The JWKS client is built once per container** and caches signing keys, so a warm container
validates without a network call, which matters on `$connect` where it sits directly in front
of a student waiting for a socket. An empty `ALLOWED_CLIENT_IDS` admits nobody, which is the
safe direction for a misconfigured deploy.

## Conversation titling

`app/titles.py`. One small model call that names a conversation, and the rules for
distrusting it.

**It is not part of the loop.** A title is a label in a sidebar, not part of the answer, so it
does not belong in the answer's contract: no new output tag, no new sentence in the system
prompt, nothing the card parser has to learn. The tag contract and the prompt are settled, and
a label has no business reopening them.

**It runs after the first exchange is written,** so the model can see the question *and* the
answer. A title drawn from the question alone names what was asked; one that has seen the reply
names what the conversation turned out to be about.

**It must never delay or fail a turn.** Its own short deadline, checked against Lambda's real
remaining time. No retries, because a retry inside a two-second budget is a way to spend the
budget without an answer. Every failure swallowed and logged, because the student already has
their reply by the time this runs. Its boto3 client carries the title budget as its
`read_timeout` and one attempt, where the loop's client retries three times: an answer is worth
waiting for and a label is not.

**The fallback is already written when this runs.** The first user message put a truncated
title on the header on its way past, so every path out that is not a good title leaves the
conversation named rather than nameless. That is what makes a rejection free.

**The output is not trusted, and a rejection is not a failure to repair.** A titling model
happily answers "Sure! Here's a title:" or returns a quoted string, and both are worse than the
fallback: a student reading the model's throat-clearing in their sidebar has been shown it as
if it were their own words. Every rule could be written as a fix (strip the quotes, cut the
preamble, truncate to the cap) and every one of those fixes hands a student a title assembled
out of a reply that ignored the instruction. What each rule catches:

| rule | what it is catching |
|---|---|
| empty | nothing to show |
| multi-line | a title and a commentary about the title |
| quoted or backticked | the model presenting a title rather than writing one |
| preamble | "Sure! Here's a title:" and its relatives |
| ends with a colon | the same thing with the title itself missing |
| angle brackets | markup leaking out of a model that has seen a tag contract |
| over the cap | a sentence, not a title |

Dashes are normalised before the cap is measured, exactly as the card path does it, so the
length checked is the length displayed.

**`maxTokens` is 32:** enough for a six-word title and nothing like enough for a paragraph, so
a model that ignores the instruction runs into it rather than billing for an essay, and the
truncated result fails validation on the way out. The system prompt is deliberately short: a
long prompt for a four-word output costs tokens and latency inside a budget measured in
seconds, and every extra clause is another thing the model can comment on.

**The exchange is sent as one user message rather than as a two-turn transcript.** The model
is not continuing this conversation, it is looking at a finished one, and a transcript shape
invites it to reply to the student instead of naming what they discussed.

**The call is counted in the turn's usage whether or not the title is usable,** because a
rejected title was billed exactly like an accepted one.

## The campus clock

`app/campus_time.py`. Lambda's clock is UTC and nothing in the deployment changes that, so a
naive stamp tells the model it is 3am while the student typing the message is awake at 8pm in
San Jose. Every question a student asks about time is asked in campus time, so the zone is
fixed in code rather than read from the environment: the process timezone is an accident of
where the code runs, and the answer must not be. An IANA name rather than a fixed offset, so
the PDT/PST switch is the tz database's job.

**The line says its own zone.** Weekday, date, time, the abbreviation the student would say
out loud, and the IANA name behind it. A bare "2:53 PM" is a number the model has to guess the
frame of, and the frame is the part that stops it from converting. Weekday first, because "is
the office open" is a weekday question before it is a clock question. `%-d` and `%-I` drop the
leading zero on both glibc and BSD, so the line reads the way a person writes a time.

**A zone that will not resolve costs the line, never the turn.** `zoneinfo` reads the host's
tz database, so a runtime shipped without one raises rather than returning a wrong answer. The
fallback is silence, logged at WARNING. Falling back to UTC would be the exact bug this module
exists to prevent, and it would be invisible: a UTC stamp looks like a working feature right up
until a student asks whether an office is open.

**It is a context projection and only that.** The line is built for the model's copy of the
turn and never for the student's stored message, which stays the words the student typed. It
sits above the message so the student's text is the last thing before the instruction, and so
no prefix of what they wrote is ever run together with server-authored text. It lives in the
user message rather than the system prompt because the prompt is a pure function of Settings
and stays cacheable and testable that way, and because the time is a fact about this turn
rather than a standing instruction.

## What one turn cost

`app/usage.py`. The cost panel used to price every figure from one average of a sample of
questions, including the number it put in front of the student as "this conversation". That
number was never this conversation: it was the sample's mean multiplied by a message count.

**One student message is not one model call.** The Converse loop runs until the model ends its
turn, so a message that triggers a second search bills two invocations, and the second resends
everything before it: system prompt, history, and every retrieved passage already in the
transcript. A conversation with a title also paid for the small titling call. None of that is
guessable from outside the loop, so it is counted inside it.

**It is not one MODEL either, and that was a real miscount.** `chat.title_model_id` is Haiku
and `generation_model_id` is Sonnet, so a turn that names a conversation calls two models at
two prices. Until 2026-08-20 the titling call went through `record_model_call` like any other
and its tokens landed in `input_tokens` and `output_tokens` - the two fields the cost panel
prices at the *generation* rate. `record_title_call` puts them in `title_input_tokens` and
`title_output_tokens` instead, and the panel carries a rate pair for them. The call is still
counted in `model_calls`, because it was still billed as one.

The way that was found is the way to find the next one: `eval/measure_usage.py --audit` runs a
real turn with a real tally attached and captures every Converse and ApplyGuardrail response at
the client, then compares the two **per model**. Comparing the totals would have passed either
way, which is exactly why it did for four months.

**One tally per request, opened before the guardrail screen and mutated in place all the way
down.** It is an argument rather than a return value because every exit is an exit under a
cap: a turn that hits the deadline on its third call still billed three, and a tally riding on
the response would be lost on exactly the paths worth counting. So a turn that exits on the
wall-clock deadline still reports the calls it was billed for, a blocked turn still reports the
screen that blocked it, and the titling call is in there too.

**Everything is read off responses the loop already receives.** Nothing extra is asked of
Bedrock, no request changes shape, and no branch reads these numbers back: the tally is
carried out to the wire and nowhere else. A response that arrives without its `usage` block
still counts as a call, because the call was still billed. A retrieval is counted only once it
has returned: one that raised may or may not have been billed, and a meter that guesses in its
own favour is not a meter.

**Only `contentPolicyUnits` is read off a guardrail response,** and that is not laziness about
the other policy counters. Guardrails bill **per configured policy**, this stack configures
exactly one (`PROMPT_ATTACK`), and the panel carries exactly one guardrail rate. Summing
policies that are not configured into a number priced as content units would invent spend. A
unit covers 1,000 characters of whatever the service decided to screen, which is why the figure
is taken from the guardrail's own reported usage rather than counted off the query length.

**Prompt-cache reads and writes are deliberately not counted.** Nothing in this stack enables
prompt caching and the panel's rate table has no cache rates to price them with, so a field for
them would be a zero that looks like a measurement. If caching is ever turned on, this is the
file that has to learn about it, and the symptom of forgetting would be input tokens that read
low.

**The meter is per conversation and is not stored.** It accrues in the tab from each reply, so
a chat reopened from history reads "nothing metered in this chat yet" rather than presenting a
zero as a measurement. Storing it would put a cost attribute on the message items, which
`docs/accounts-and-storage.md` fixes at three.

**`protected_namespaces` is emptied for `model_calls`.** Pydantic reserves the `model_` prefix
for its own methods and warns on any field that uses it; the field is named for the thing it
counts, and the alternative would be renaming a wire key to avoid a warning about a collision
that does not exist. `ChatResponse.raw_text` is named that way rather than `model_text` for
the same reason.

## The system prompt

`app/prompts.py`, against `docs/system-prompt.md`. The caps are interpolated from Settings
rather than typed, so `app/cards.py` enforces the numbers this file tells the model about. A
literal here would be a second copy and the drift would be invisible from either side: the
model briefed on one budget while the server applied another, and the only symptom
descriptions quietly losing their tails.

**One rule, one place.** Every behavioural rule is stated once, under the heading it belongs
to. Adherence falls off as rules stack and the misses are silent, so a rule restated in a
second section is not emphasis, it is one more rule competing with the first. Exactly two
duplicates survive, both deliberate: the safety keys' English carve-out (the Language section
states it for every tag the server reads, and Safety says it again, because the failure there
is a crisis panel lost to a translation), and the ban on an escalation offer during a safety
turn (the escalation section is gated, so it cannot lean on Safety's list of exclusions).

**The worked examples are the contract, not decoration.** They are the primary steer on tone
and on length, because a model matches the shape it is shown far more reliably than it counts
characters or weighs adjectives. Moving the editorial balance means rewriting the examples,
which is why the parser knows nothing about how much text belongs where. It is also why
shortening the reply is an edit to the examples and not only to the stated target: the
descriptions sat at the length the target named, so a target lowered on its own would leave the
model copying the old one.

**Two sections are interpolated as blocks.** The place section is a **key vocabulary** and is
always present, because the catalogue is a table in code rather than an address somebody has
to configure. The escalation section is **interpolated or absent, never present-but-off**: a
deployment with no recipient has nowhere to send a draft, so teaching the tag would spend
tokens on every turn to produce a block the server then drops. One value gates the section and
the assembler, so the two cannot disagree.

**Measured shapes.** Several things in this prompt are the way they are because a measurement
said so, not because they read better:

- **The location rule is one test with two halves,** not a trigger rule plus a roster rule.
  The two halves fail in different directions (a panel on a turn nobody asked a location
  question on, and a panel keyed to a near miss of the office they did ask about), and written
  as two separate prohibitions the model reliably obeyed whichever had been made more prominent,
  at 0 to 2 in 4 on the other. Sonnet-class attention treats a second negative rule about the
  same tag as competition, not reinforcement. One test with a worked micro-case per half holds
  both. The near-miss half is the one no server-side check can see, because the key resolves,
  the address is real, and it is the wrong building.
- **Two location examples exist against this file's own earlier judgement** that a location is
  a decision rather than a shape to copy. With the ownership rule stated and no example, a
  location question put the address in the card, in the prose and on the panel at once. The
  examples are what moved it.
- **Examples mark annotations as `[bracketed]` stage directions** with the reply under an
  explicit `[your reply]` marker. The first shipped format ran the reply directly under a bare
  "Results:" line, and the model learned that annotating the situation is part of the output:
  five answers in the 2026-08-10 eval opened by narrating the retrieval decision. Examples steer
  harder than rules, so the boundary between stage direction and speech has to be drawn in the
  examples themselves, not just banned in the Never list.
- **The campus shorthand list is vocabulary, not trivia,** which is why it is in code rather
  than in `config.yaml` with the tunables. The model writes its own retrieval queries, so an
  abbreviation it cannot expand costs the turn twice: it searches the campus site for the
  letters and then answers from whatever that returned. A sponsor testing the live app hit
  exactly this, where "SU" resolved to the Student Union and "BBC" did not resolve at all. Every
  entry states a mapping some sjsu.edu page states, because the failure this fixes has a worse
  twin: an invented expansion routes a student confidently to the wrong office.
- **"Say each fact once" ends with a precedence rather than leaving it to be inferred.** A
  fact with nowhere else to go is written twice rather than lost, because a student who reads an
  address twice is inconvenienced and a student who never got the phone number starts over.
  Without that last line the rule reads as a licence to drop a contact band to avoid a repeat,
  which is the failure the 2026-08-10 eval was scoring.

**The four permitted marks are stated in the display parser's own syntax, not markdown's.**
Asterisks only, because `_underscores_` are deliberately not italics there (the prose carries
email local parts and snake_case ids, where the underscores are the text), and a numbered line
counts only with its `.` or `)` and the space the parser requires. A permission looser than the
parser is how a model gets told a mark works and the student gets asterisks. The renderer's set
is a **ceiling** rather than an instruction, so widening the prompt to match it was its own
decision. Two of the four are modelled in examples and two are only permitted, which is a real
difference in how often a model reaches for them.

**Nothing about the display is ever described to the student.** The renderer's reach is an
internal fact, and a student who asks for italics wants italics, not an explanation of what
renders. The old two-mark ban did not just suppress italics, it got recited to a student who
asked for them.

**Nothing suppresses cards on a follow-up, deliberately.** Two instructions used to and both
are gone. "Do not repeat cards the student already has" was unenforceable: history carries prose
only, so the model cannot see which cards were shown, and an instruction it cannot evaluate
collapses into avoiding cards altogether. "If the user message says they clicked a follow-up,
emit no cards" keyed the answer's shape on which widget sent the turn, when a follow-up is
precisely when a student wants the specific destination.

**The frontend's language picker reaches none of this.** It is a display preference held in
the browser and never sent with a request, so the reply's language is decided from the message
and the picker and the reply can honestly disagree: sidebar in Thai, question typed in English,
answer in English. Wiring the picker into the request would let a setting somebody changed once
overrule what they actually just wrote.

**The Language section names the cards separately and in capitals** because a model told to
answer in the student's language does so readily in prose and much less readily inside a
`<card>` block, where the fields read as metadata rather than as speech. The miss is not
cosmetic: the cards carry the answer, so a Spanish lead-in over English cards has greeted the
student in Spanish and answered them in English. The `<followup>` is the sharpest case, because
it is a sentence the student reads on a button and sends back as their next turn.

What does not follow the language: phone numbers, emails and URLs (a translated one is simply
wrong), office and building names (the name is what the person at the front desk answers to),
and the tag names, ref ids and keys inside a `<safety>` or `<place>` block, because the
**server** reads them and a translated key resolves to nothing. The escalation draft stays
English because its reader is staff rather than the student.

## What the suites pin

`app/tests/`. The suite is hermetic by design: the chat path is Bedrock calls all the way
down and none can be exercised without an account, so a run must never depend on boto3 being
installed or on credentials existing. `conftest.py` stubs `boto3` and `botocore` before any app
module is imported, and the stubs raise if a test reaches `boto3.client` or `boto3.resource`.
The stack's identity variables are set with `setdefault` before collection rather than in a
fixture, because `settings.load_settings()` raises without them and `handler.py` calls it at
import.

Tests build events through the conftest helpers so nothing accidentally asserts on a request
the deployed stack could not produce: every route is authorizer-gated, so a request with no
`sub` is a misconfiguration rather than an anonymous student. Events default to the **web**
client id, so an ordinary test event is an ordinary student rather than an exempt machine.

The fake store records the turn's table access **in order**, and that order is the assertion
in most tests that use it. Its `claim_message_allowance` holds a lock so a concurrency test
exercises the guarantee DynamoDB actually provides (compare and increment as one operation)
rather than Python's interpreter happening to switch threads somewhere convenient; that the
real store sends exactly that expression is asserted separately in `test_history.py`. Its
`open_connection` deliberately does **not** raise on `fail_on`, because the real one swallows
its own failures: the record is a record and not a gate.

The daily cap is **off** in the rest of the suite, which is deliberate rather than convenient.
The deployed default reads from an environment variable the stack omits when the feature is
disabled, so a suite that switched it on globally would stop testing the shape every other test
is about.

Some assertions are load-bearing in ways the test name cannot carry:

- **`test_no_gav_specific_resources_were_inherited`** pins the strip of the source project's
  own surface (its catalog bucket, its feedback path, its dual hosting) so a later pull cannot
  reintroduce them, with the deliberately-inherited pieces pinned alongside.
- **`test_the_authorizer_never_logs_the_token`** is the enforcement of the rule that the
  WebSocket token, which travels in a query string, never reaches a log line.
- **The prompt suite asserts the editorial balance directly** (titles and descriptions
  outweigh the prose on both sides of the grid in every worked example that emits cards), so a
  rewrite cannot drift the weight back into the bubble.
- **The place suite asserts every building in the table has a committed image and that no
  image is orphaned,** because the images are committed rather than built and nothing at deploy
  time would notice one missing.
- **The safety suite's roster test** is what makes "a key the model is taught always resolves"
  structural rather than aspirational.
- **`test_token_auth.py` verifies against real signatures,** which is why `pyjwt[crypto]` is in
  `requirements-dev.txt` and not only in the two layers that ship it. The suite generates an RSA
  keypair, mints Cognito-shaped tokens with it and hands the verifier a JWKS client returning the
  matching public key; a stubbed library would pass every one of those tests while verifying
  nothing. It is also the one place in `app/tests/` that reaches a module the FastAPI app
  imports - `token_auth.py` needs no fastapi, which is what keeps it importable here at all.

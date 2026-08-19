# The WebSocket streaming path, as it stands

What is deployed today for a streamed reply, read off the repository on 2026-08-19. It is a
map, not a proposal: nothing here argues for or against replacing it. Every claim names the
file it came from, and anything worked out rather than read is marked **(inferred)**.

Two things to know before the detail.

**Nothing in `docs/build-plan.md` describes this path.** The build plan's bullet list stops
short of it; the only streaming entries in it are two frontend commits (the card-stage
indicator and the waiting deck) that assume the socket already exists. `docs/aws-port-draft.md`
still lists "response streaming" as a v2 item with the note "HTTP API cannot stream; needs
REST API or Function URL". So the docs are behind the code, and this file is the first written
account of the path.

**The path is additive and gated.** POST /chat is untouched and remains the fallback. The gate
is `config.yaml`'s `streaming` block; with it absent or `enabled: false`, `resolve_streaming`
returns `None` (`infra/infra/config.py`) and the stack synthesizes no WebSocket API, no
authorizer, no functions, no layer and no `streamingApiUrl` in `config.json`. The committed
`config.yaml` sets `enabled: true`. Note that `resolve_streaming`'s own docstring still says
"off is the shipped default"; the config file no longer agrees with it, and
`infra/tests/unit/test_config.py::test_streaming_is_off_unless_the_block_says_otherwise`
asserts `load_config()["streaming"]["enabled"] is True` as a sanity check with a comment
recording that the repo now commits it on.

---

## 1. The functions

Four Lambda functions are involved; three of them exist only when streaming is on.

| Construct id | Handler | Module | Exists when |
|---|---|---|---|
| `ChatFunction` | `handler.lambda_handler` | `app/handler.py` | always |
| `ConnectAuthorizerFunction` | `ws_authorizer.lambda_handler` | `app/ws_authorizer.py` | streaming on |
| `ChatStreamRouteFunction` | `streaming.lambda_handler` | `app/streaming.py` | streaming on |
| `ChatStreamWorkerFunction` | `stream_worker.lambda_handler` | `app/stream_worker.py` | streaming on |

All four are `Runtime.PYTHON_3_13` on the architecture pinned as `_LAMBDA_ARCH`
(`infra/infra/infra_stack.py`).

The API in front of them is `apigwv2.WebSocketApi` (`ChatStreamingApi`), with
`route_selection_expression="$request.body.action"`, one stage `ChatStreamingStage` whose
`stage_name` is the module constant `_STREAMING_STAGE_NAME = "stream"`
(`infra/infra/infra_stack.py`). Three routes: `$connect`, `$disconnect`, `sendMessage`. No
`$default` route is created, deliberately - pinned by
`test_the_message_route_is_named_and_nothing_falls_through_to_a_default`.

**Why a WebSocket at all**, stated identically in `app/streaming.py`'s module docstring,
`config.yaml`'s `streaming` header and `infra/infra/infra_stack.py`'s section banner: API
Gateway response streaming is REST-API only and this stack runs an HTTP API; Lambda response
streaming supports Node.js and custom runtimes only and the agent loop is Python. Streaming
in band would mean rewriting the loop in another language, so the stream was moved out of
band - `ConverseStream` on the way in, `post_to_connection` on the way out.

---

## 2. One streamed turn, end to end

### 2.1 Handshake

1. The browser reads `config.json`. `frontend/src/lib/chatStream.ts::isStreamingAvailable`
   returns `Boolean(config.streamingApiUrl)`; `frontend/src/components/ChatApp.tsx` stores
   that as `streamingReady` on mount. False until `config.json` has been read, so the first
   turn on a cold page takes POST /chat.
2. `streamChat` calls `currentAccessToken()` (`frontend/src/lib/auth.ts`), which throws
   `AuthError` if the session is missing or `Date.now() >= session.expiresAt`. This happens
   **before** the socket is opened. The reason is in that function's docstring: a rejected
   handshake gives JavaScript nothing to read - no status, no body, only an `error` event and
   a 1006 close - so a doomed connection is indistinguishable from a blocked port.
3. `streamChat` opens a socket at `config.streamingApiUrl` with the token appended as a
   `?token=` query parameter, URL-encoded.
   **A connection per turn**, not one held open. `chatStream.ts`'s module docstring gives the
   reasons: a fresh token every turn, no reconnect logic, and no interaction with API Gateway's
   10-minute idle and 2-hour hard connection limits.
4. API Gateway invokes `ConnectAuthorizerFunction` with identity source
   `route.request.querystring.token`.
5. On allow, API Gateway invokes `ChatStreamRouteFunction` on the `$connect` route.
   `app/streaming.py::handle_connect` writes the connection record and returns 200. A missing
   `sub` returns 401 and the handshake fails.

The token travels in the query string, and both `app/ws_authorizer.py` and
`frontend/src/lib/auth.ts` record why: a browser gives no way to set `Authorization` on a
WebSocket handshake; the alternative, `Sec-WebSocket-Protocol`, is accepted by API Gateway as
an identity source but never echoed back in the 101 response, and RFC 6455 says a client whose
requested subprotocol is not echoed must fail the connection. Both files record this as probed
against a deployed API on 2026-08-12, with Chrome 151 and Node's WHATWG WebSocket firing
`error` and never opening.

### 2.2 The message frame

`socket.onopen` sends one frame (`chatStream.ts`):

```json
{"action": "sendMessage", "query": "...", "followup": false, "conversationId": "..." }
```

`action` is what the route selection expression reads. API Gateway invokes
`ChatStreamRouteFunction` on `sendMessage`; `app/streaming.py::lambda_handler` dispatches on
`event["requestContext"]["routeKey"]` - one level down from where an HTTP API payload-2.0 event
carries it, which that function's docstring calls out as one reason the two handlers are
separate functions.

`app/streaming.py::handle_message` then runs, in this order:

| Step | What | Failure behaviour |
|---|---|---|
| identity | `identity_from(event)` reads `requestContext.authorizer.sub` | `None` → ack 401 and **no frame at all**: the sink is built after this check, so nothing is pushed (what the browser then does depends on whether the socket closes - see §11) |
| turn id | `new_ulid()` from `app/history.py`, minted here | - |
| parse | `json.loads(event["body"])`, must be a dict | `error` frame, ack 400 |
| query | non-empty string, `<= SETTINGS.max_query_chars` | `error` frame, ack 400 |
| validate | `ChatRequest.model_validate({query, conversationId, followup})` | `error` frame, ack 400 |
| rate limit | `ratelimit.claim_turn` - one atomic conditional `ADD` | refusal → `error` frame with `limit`/`resetAt`/`retryAfterSeconds`, ack 429 |
| guardrail | `apply_input_guardrail(query, usage)` - `ApplyGuardrail(source=INPUT)` | block → one `final` frame, ack 200, no worker started |
| write | `store.append_message(role="user", ...)` | logged at exception level, turn continues |
| tell | `accepted` frame carrying `conversationId` | - |
| hand off | `_invoke_worker(payload)` - `InvocationType="Event"` | - |

The ordering is stated in `handle_message`'s docstring as deliberately the HTTP handler's, in
full: rate limit before guardrail so a refused turn spends one conditional DynamoDB write and
nothing billable; guardrail before the write so a blocked message never becomes a turn and
cannot smuggle attack text into the next turn's context; the student's message written before
the model call so a disclosure that then fails is still on record.

`accepted` is the one frame with no named method on `ConnectionSink` - `handle_message` calls
`sink._post({...})` directly.

Then the route function returns. Its docstring states why: a WebSocket route integration has
the same 29-second ceiling every API Gateway integration has, and the agent loop can use most
of it.

### 2.3 The worker

`_invoke_worker` calls `lambda:Invoke` with `InvocationType="Event"` against
`WORKER_FUNCTION_NAME` (env `STREAM_WORKER_FUNCTION_NAME`), on a boto3 client configured with
`retries={"max_attempts": 1}`, `connect_timeout=2`, `read_timeout=5`. The payload is:

```
connectionId, turnId, userId, conversationId, isNewConversation,
userSortKey, query, followup, usage
```

`app/stream_worker.py::lambda_handler` then:

1. Builds a `ConnectionSink` against `MANAGEMENT_ENDPOINT` (imported from `app/streaming.py`),
   the same `connectionId` and the same `turnId`.
2. Rehydrates the tally: `TurnUsage.model_validate(event["usage"])`, so the guardrail units the
   route function already billed are in the same turn's total.
3. Reads history: `STORE.recent_messages(..., limit=SETTINGS.max_history_messages,
   exclude_sort_key=event["userSortKey"])`. A failure logs and the turn answers with `history = []`.
4. Calls `orchestrator.run_chat(request, SETTINGS, history=history, deadline=_deadline(context),
   usage=usage, stream=sink, guardrail_config=_guardrail_config())`. An exception here sends an
   `error` frame and returns `{"ok": False}`.
5. `sink.flush()` - the tail of the preview.
6. `STORE.append_message(role="assistant", text=response.raw_text, sources=response.sources,
   escalation=..., place=...)`.
7. On `isNewConversation`, `titles.generate_title(...)` under `_title_deadline(context)`, and
   `STORE.set_generated_title`.
8. `sink.final(response.model_dump(by_alias=True))`.
9. One INFO log line: cards, safety, place, escalation, model calls, in/out tokens,
   `sink.frames`, `sink.gone`.

### 2.4 Where the converse loop runs

In `app/orchestrator.py::run_chat`, which is the **same function** POST /chat calls. There is
one loop, not two.

`run_chat`'s only streaming parameter is `stream: StreamSink | None`. `StreamSink` is a
`Protocol` declared in that same file with exactly two methods, `status(stage)` and
`text(accumulated)`, neither returning anything the loop reads back. The branch is one line:

```python
if stream is None:
    response = client.converse(**call)
else:
    response = _converse_streaming(client, call, stream)
```

`_converse_streaming` calls `client.converse_stream(**call)` and reassembles the event stream
into the same dict shape `converse` returns - `{"output": {"message": ...}, "stopReason": ...,
"usage": ...}` - so nothing below that line knows which transport ran. It handles
`contentBlockStart`, `contentBlockDelta`, `contentBlockStop`, `messageStop` and `metadata`, and
ignores any other event type. `toolUse` arguments arrive as partial JSON fragments accumulated
into a string and parsed once in `_finished_block`; unparseable arguments become an empty input
rather than an exception. Text deltas accumulate into `accumulated` and are pushed via
`stream.text(accumulated)` - the whole reply so far, every time, so the sink owns both "how much
is safe to show" and "how much has been sent". `toolUse` deltas are never pushed.

Usage is taken from the stream's own `metadata` event and put where
`usage.record_model_call(response)` already looks.

`_prime_first_search` and `_run_tool` call `_tell(stream, "retrieving")`, which swallows its own
failures.

Everything after the model call - the tool loop, the iteration cap, the deadline, the exit
through `_response_from_text`, and therefore every card, cap, dash normalisation, trailing-text
split, place resolution, escalation draft and safety decision - is the same code on both paths.

### 2.5 What the browser does with it

`ChatApp.tsx::sendTurn` calls `streamed()` when `streamingReady`, otherwise `buffered()`.
`streamed()` passes only `onStatus` and `onPreview` handlers. On resolve, if the last stage seen
was `CARDS_STAGE` and motion is not reduced, it awaits `waitingDeck.settleAndCompress(cardCountOf(next))`,
then clears the preview and calls `applyChatResponse(next, query, previewed || undefined)`.

`applyChatResponse` is the same function the buffered path calls. The only thing the preview
leaves behind is `revealedChars` - how many characters were already typed on screen - so the
finished turn picks up where the preview stopped instead of replaying prose the student has read.
The turn itself is built entirely from the `final` payload.

---

## 3. Where the JWT is validated

Two different mechanisms for the two transports.

**POST /chat and the conversation routes**: API Gateway's native JWT authorizer, no code of
ours. `apigwv2_authorizers.HttpJwtAuthorizer("ChatJwtAuthorizer", issuer, jwt_audience=[web_client, eval_client],
identity_source=["$request.header.Authorization"])` (`infra/infra/infra_stack.py`).
`app/handler.py::_claim` then reads `requestContext.authorizer.jwt.claims`; `user_id_from`
returns `sub`, `client_id_from` returns `client_id`.

**The WebSocket**: `app/ws_authorizer.py`, on `$connect` alone, because a WebSocket API accepts
only a REQUEST (Lambda) authorizer and only on that route. It re-implements what the HTTP
authorizer does:

- `_token_from(event)` - `queryStringParameters.token`, must be a non-blank string.
- `PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True)`, built once per container.
- `jwt.decode(..., algorithms=["RS256"], issuer=_ISSUER, options={"verify_aud": False,
  "verify_exp": True, "verify_signature": True})`. Audience verification is off because a
  Cognito **access** token carries `client_id`, not `aud`.
- `token_use == "access"`, else reject - which is what stops an ID token (which does carry
  `aud`) from sailing through a `client_id` check that never ran.
- `client_id` in `_ALLOWED_CLIENT_IDS` (env `ALLOWED_CLIENT_IDS`, comma-separated; empty admits
  nobody).
- non-blank `sub`.

Every rejection is a `raise`, never a Deny policy, so API Gateway answers the handshake 401
rather than 403.

The returned policy is scoped to `event["methodArn"]`, never `"*"`. The `context` map carries
`{"sub": subject, "clientId": client_id}`, and API Gateway attaches that to every later route
invocation on the connection - `$default` and `$disconnect` included, which both
`app/ws_authorizer.py` and `infra/infra/infra_stack.py` record as verified against a deployed
probe. That is the only reason `app/streaming.py::identity_from` can key a DynamoDB partition on
the caller.

**Nothing is validated per frame.** The connection is authorized once, at the handshake. There
is no re-check of expiry mid-connection; what bounds it is that the frontend opens a connection
per turn.

**`app/ws_authorizer.py` logs nothing at all**, and this is a rule about the file rather than a
style choice: the authorizer event carries the bearer token in full.
`test_the_authorizer_never_logs_the_token` (`infra/tests/unit/test_infra_stack.py`) parses the
module's AST and asserts `logging` is not imported and `print` is not called. The stack also
configures no access logging on the WebSocket API, asserted by
`test_the_streaming_stage_throttles_like_the_http_stage_and_logs_no_access`.

There is no authorizer result cache to configure. `AuthorizerResultTtlInSeconds` is HTTP-API
only, and API Gateway refuses the resource outright on WEBSOCKET with a 400 -
`infra/infra/infra_stack.py` records a real CREATE_FAILED from 2026-08-12, and
`test_the_connect_authorizer_carries_no_result_ttl` asserts the key is absent rather than zero,
because zero was the value that failed. Whether API Gateway caches a WebSocket authorizer's
answer at all is recorded in that same comment as undocumented either way.

---

## 4. Connection state: written, read, expired

One item kind, in the existing chat-history table (`app/history.py`):

```
pk = USER#<sub>    sk = CONN#<connectionId>    connectedAt, expiresAt
```

- **Written** by `ConversationStore.open_connection` (`PutItem`), called from
  `app/streaming.py::handle_connect` on `$connect`.
- **Deleted** by `ConversationStore.close_connection` (`DeleteItem`), called from
  `handle_disconnect` on `$disconnect`. Best effort - API Gateway does not guarantee the route
  fires.
- **Expired** by the table's TTL. `expiresAt` comes from `app/streaming.py::connection_expiry`,
  `time.time() + CONNECTION_TTL_SECONDS` where `CONNECTION_TTL_SECONDS = 3 * 60 * 60`. The
  constant's comment gives the arithmetic: API Gateway ends every connection well inside that
  (10 minutes idle, 2 hours hard), so three hours is the hard cap plus an hour of slack, and the
  only rows TTL ever collects are the ones `$disconnect` failed to remove. The table's
  `time_to_live_attribute` is `"expiresAt"` (`infra/infra/infra_stack.py`).
- **Read by nothing.** `app/history.py`'s module docstring states it is invisible to every read
  in that module - the conversation list is `begins_with('CONV#')` and both message reads are
  `begins_with('MSG#<convId>#')`. Grepping the repo for `CONN#` finds only `open_connection`,
  `close_connection`, the module docstring, and the test doubles in `app/tests/conftest.py`.
- Both writes swallow their own failures and log. `open_connection`'s docstring: the record is a
  record, not a gate - nothing reads it to decide whether a student may stream.
- The partition is the **user**, not the connection, deliberately: keying on
  `CONN#<connectionId>` would make the record addressable by connection id alone, and nothing
  needs that, because the authorizer context already carries a verified `sub`.

This is the fourth sort-key prefix in the user's own partition, alongside `CONV#`, `MSG#` and
`RATE#DAY#` (`app/ratelimit.py`). It needs no new table and no new grant beyond `PutItem` /
`DeleteItem`.

---

## 5. Shared code versus duplicated

### Shared, literally the same code

- **`app/orchestrator.py::run_chat`** - the whole turn, including the tool loop, the deadline,
  the iteration cap, `_response_from_text`, card parsing, safety resolution, place resolution
  and the escalation draft. `stream` is the only parameter that differs.
- **`app/history.py::ConversationStore`** - both paths write the user message, read history and
  write the reply through the same methods. The two functions build **separate instances** at
  module scope rather than importing one; `app/streaming.py` says why - importing `handler.py`
  would pull the whole HTTP request path and its module-scope Bedrock client into a function
  that serves neither.
- **`app/ratelimit.py::claim_turn`** - identical call from both, same store, same settings, same
  `client_id` exemption list.
- **`app/settings.py::load_settings`** and the whole `chat_environment` dict - hoisted out of the
  chat function's constructor in `infra/infra/infra_stack.py` specifically so the streaming
  functions get the same values. The comment there names the risk: two copies would drift, and
  the drift would be a streamed turn answering under a different cap or against a different
  table.
- **`app/models.py`** - `ChatRequest` and `ChatResponse`, so both paths accept the same fields,
  drop the same unknown ones (a `history` array, a user id) and serialise through the same
  camelCase aliases.
- **`app/usage.py::TurnUsage`** - one tally per turn, opened by the route function, carried
  through the invoke payload, mutated by the worker.
- **`app/titles.py::generate_title`** - same function, same settings.
- **`app/cards.py`** - the finished reply goes through the same parser. Streaming additionally
  uses `preview_safe_prefix` and `card_block_started`, which exist only for this path.

### Duplicated on purpose

**`apply_input_guardrail`** is written twice: `app/handler.py::apply_input_guardrail` and
`app/streaming.py::apply_input_guardrail`. The bodies are the same call with the same
`source="INPUT"`, the same `usage.record_guardrail`, the same continue-on-failure posture and
the same "return the replacement text on a block" contract. `app/streaming.py`'s version states
the reason: importing `handler.py` would pull the whole HTTP request path in, and the thing that
must not drift is the guardrail *identity*, which comes from `Settings` and is read from the
same environment by both.

**`_bedrock_client`** is likewise written twice, with the same shape and timeouts
(`retries={"max_attempts": 3, "mode": "adaptive"}`, `read_timeout=10`, `connect_timeout=5`) in
`app/handler.py` and `app/streaming.py`. A third, different one lives in `app/orchestrator.py`
for the model calls (`read_timeout=25`).

**The deadline derivation** is written three times with the same minimum-of-two shape:
`handler.loop_deadline`, `handler.title_deadline`, `stream_worker._deadline`,
`stream_worker._title_deadline`. Same structure, different reserve constants (see §8).

**The turn's step order** is written twice - once as `handler.run_turn` (write, read, model,
write, title) and once split across `streaming.handle_message` (validate, identity, rate limit,
guardrail, write) and `stream_worker.lambda_handler` (read, model, write, title). The
`handle_message` docstring is explicit that this is the HTTP handler's order reproduced, and why
each step's position matters.

**The route dispatch** is written twice: `handler.lambda_handler` reads the top-level `routeKey`
and `streaming.lambda_handler` reads `requestContext.routeKey`. Both refuse an unknown route
rather than falling through. `handler.lambda_handler` additionally runs `post_chat` when the
route key is missing entirely, treating that as a direct invoke; `streaming.lambda_handler` has
no equivalent and returns 404.

**Not shared, and deliberately not**: `app/ws_authorizer.py` imports nothing from the rest of
`app/`. Its bundle is asserted to be exactly `{ws_authorizer.py}`.

**A byproduct of the sharing**: `app/stream_worker.py` imports `ConnectionSink`,
`MANAGEMENT_ENDPOINT`, `DELTA_MIN_CHARS` and `DELTA_MAX_DELAY_MS` from `app/streaming.py`, which
means importing that module executes its module scope on the worker too - including a second
`ConversationStore` the worker never uses (it has its own `STORE`) and a
`WORKER_FUNCTION_NAME` that resolves to `""` there because the worker is not given
`STREAM_WORKER_FUNCTION_NAME`. Harmless: the store connects lazily and the worker never calls
`_invoke_worker`.

---

## 6. The wire contract the frontend depends on

Defined server-side in `app/streaming.py::ConnectionSink` and typed client-side as
`ServerFrame` in `frontend/src/lib/chatStream.ts`. Every frame is JSON, UTF-8 encoded, and every
frame carries `turnId` - `ConnectionSink._post` merges `{"turnId": self._turn_id}` into every
payload.

### Frame shapes

| `type` | Fields | Sent by | When |
|---|---|---|---|
| `accepted` | `conversationId` | route fn | after the student's message is written, before the worker is invoked |
| `status` | `stage` | route fn / worker | `"retrieving"` from `orchestrator._tell`; `"composing_cards"` from `ConnectionSink._announce_cards` |
| `delta` | `text` | worker | a batch of new prose |
| `final` | `payload` | route fn / worker | the authoritative `ChatResponse` |
| `error` | `message`, optional `limit`, `resetAt`, `retryAfterSeconds` | route fn / worker | a definite server-side failure or refusal |

`stage` values are a closed set of two wire strings. `CARDS_STAGE = "composing_cards"` is
declared on both ends - `app/streaming.py` and
`frontend/src/components/PendingExchange.tsx` - and both files note that the two ends of the
string are a contract. `"retrieving"` is a literal in `orchestrator._tell`. The sentences the
student reads are catalogue strings (`t.stageRetrieving`, `t.stageComposingCards`), never the
wire value.

### The deltas

`delta.text` is **append-only prose and nothing else**. `ConnectionSink.text(accumulated)`
receives the whole reply so far, computes `cards.preview_safe_prefix(accumulated)`, and pushes
`safe[self._sent:]` when `len(safe) - self._sent >= min_chars` or `max_delay` has elapsed since
the last push.

`preview_safe_prefix` stops at the first character that begins - or could still begin - one of
the card contract's tags. The tag vocabulary is one tuple in `app/cards.py`:

```python
_TAG_NAMES = ("card", "title", "desc", "followup", "safety", "escalate_to_human", "place")
_TAG_OPENINGS = tuple(f"<{slash}{name}" for name in _TAG_NAMES for slash in ("", "/"))
```

A `<` that is not one of those does not stop the preview ("under <15 units" streams intact); a
trailing partial (`...see <ca`) does, because the rest of that tag might still arrive. That
function's docstring is emphatic that it is not a parser and must never become one: nothing it
returns builds a card, resolves a ref, decides a safety handoff or applies a cap. It applies no
dash normalisation and no capping.

Batching is a **cost control**, not a feel knob, stated in `app/streaming.py`, `config.yaml` and
`infra/infra/config.py`: every push is a billable API Gateway message, and the frontend's
typewriter reveals text at roughly 108 characters a second, which the model outruns. Committed
values: `delta_min_chars: 160`, `delta_max_delay_ms: 250`. `resolve_streaming` bounds them at
16-2000 and 50-5000, rejects booleans (because `true` is `1` in Python, which would be one
message per character) and rejects strings.

### How the browser detects the end of a stream

**One `final` frame, and nothing else.** `chatStream.ts`'s `onmessage` handler resolves the
promise on `case 'final'`, calling `settle()` which clears the silence timer, closes the socket
and resolves with `frame.payload`. There is no sentinel delta, no length header, no close-code
convention.

`final.payload` is `response.model_dump(by_alias=True)` - the same `ChatResponse` POST /chat
returns for that turn, out of the same `run_chat`. `chatStream.ts` and `stream_worker.py` both
state that the browser throws the preview away and renders this.

`socket.onclose` calls `fail(...)`, which is a no-op after `settle()` has run because `settled`
is already true. So an orderly close after `final` is silently absorbed, and a close *before*
`final` is a failure.

### How the browser detects and shows errors

Four distinct outcomes, and the class of the rejection is the whole mechanism:

1. **`error` frame** → `reject(new ChatApiError(frame.message))`, always, regardless of stage.
   The comment is explicit: this is the server saying something definite, so retrying it over
   HTTP would ask a question that has already been answered with "no".
2. **`socket.onerror`** → `fail('Could not reach Sammy over the live connection.')`.
3. **`socket.onclose` before `final`** → `fail('The live connection closed before the answer arrived.')`.
4. **Silence timeout** → `fail('Sammy stopped responding partway through that answer.')`.

`fail(message)` rejects with `ChatApiError` if `accepted` has been seen, `StreamUnavailable`
otherwise. `ChatApp.tsx`'s catch chain then does:

```js
if (!(error instanceof StreamUnavailable)) throw error;   // no fallback
... return buffered();                                     // fallback
```

and the final `.catch(failWith)` renders `error.message` for a `ChatApiError` and the generic
`t.turnFailed` for anything else. A rendered failure becomes an ordinary assistant turn in the
feed (`createConversationTurn(message, { query })`), not a banner.

A frame that fails `JSON.parse` is ignored rather than fatal - the handler returns, on the
grounds that the `final` frame may still arrive.

Two consequences of reading the code, worth stating plainly:

- The `error` frame's `limit`, `resetAt` and `retryAfterSeconds` are declared in `ServerFrame`
  and **never read**. `ChatApp.tsx` uses only `frame.message`. On POST /chat the same refusal
  goes through `chatApi.ts::dailyLimitMessage`, which renders the reset instant in the student's
  own clock. So a rate-limit refusal over the socket shows the server's own sentence, which
  `app/ratelimit.py` writes as "Your limit resets at midnight UTC", where the same refusal over
  HTTP shows a local time.
- `onAccepted` is declared in `StreamHandlers` and **never passed**. `ChatApp.tsx` supplies only
  `onStatus` and `onPreview`. The `accepted` frame still matters - it flips the internal
  `accepted` flag that decides whether a failure may fall back - but its `conversationId` is
  discarded, and the client learns the conversation id from `final.payload.conversationId` in
  `applyChatResponse`.

### Reconnect behaviour

**There is none.** No retry, no backoff, no resume, no reconnect. One socket per turn, opened in
`streamChat` and closed in `settle()`. The recovery mechanism is the HTTP fallback, and it is
conditional:

> **Before `accepted`**: the server has done nothing - nothing written, nothing billed - so any
> failure becomes a POST /chat. This is what a blocked WebSocket port on campus wifi looks like.
> **After `accepted`**: the student's message is on record and a generation worker is running.
> Retrying over HTTP would ask the same question twice, bill it twice and store it twice. So the
> failure is reported instead, and the reply is still written server-side for when they come back.

(paraphrasing `chatStream.ts`'s module docstring, which states this as the file's central rule.)

An expired token throws `AuthError` from `currentAccessToken()` before the socket opens. That is
neither `StreamUnavailable` nor `ChatApiError`, so it does not fall back and renders as
`t.turnFailed`.

### The silence timer

`SILENCE_TIMEOUT_MS = 45_000` in `chatStream.ts`. It is reset by **every** frame - `onopen` and
every `onmessage` - so it is a silence timer, not a total, and a long steadily-streaming reply
cannot trip it. Its docstring sizes it against the server: the agent loop is capped at 22
seconds and the worker gets 60.

### What the browser shows during the stream

`ChatApp.tsx` holds `pendingPrompt`, `pendingPreview` and `pendingStage`;
`PendingExchange.tsx` renders one bubble for the whole wait. `onPreview` clears the stage and
sets the preview, so any arriving prose takes the stage indicator back off. The card-stage
indicator appears only when all three of `stage === CARDS_STAGE`, `Boolean(preview)` and
`!typing` hold - the file spells out that each condition closes a different way of lying.

The server side of that guarantee is `ConnectionSink._announce_cards`: it fires at most once per
turn, only when `cards.card_block_started(accumulated)` is true, and it **flushes the prose tail
first** and then sends the status frame. Both orderings are load-bearing - the safe prefix
cannot grow past that point, and the browser clears the indicator on any arriving prose, so a
delta landing after the frame would take it back off. `card_block_started` returns `False` if
`<safety` appears anywhere, because a safety turn drops its cards by contract.

---

## 7. Every place that names the endpoint, the connection records, or the worker

Found by grepping the repo (excluding `node_modules`, `.git`, `dist`) for `websocket`, `wss://`,
`streamingApiUrl`, `stream_worker`, `ChatStream`, `ws_authorizer`, `CONN#`, `post_to_connection`,
`apigatewaymanagementapi`, `ConverseStream`/`converse_stream`.

### Infrastructure

- `infra/infra/infra_stack.py` - the whole `--- WebSocket streaming API (OPTIONAL, and off by
  default) ---` section, plus `_STREAMING_STAGE_NAME = "stream"` near the top, plus the
  `streamingApiUrl` key in the `config.json` stamping in section 6, plus the `chat_environment`
  dict in section 4 (hoisted for these functions), plus `_chat_turn_statements` /
  `_grant_chat_turn` in section 4 (collected for the worker's role). Resources named:
  `ConnectAuthorizerDepsLayer`, `ConnectAuthorizerLogGroup`, `ConnectAuthorizerFunction`,
  `ChatConnectAuthorizer`, `ChatStreamRouteLogGroup`, `ChatStreamRouteRole`,
  `ChatStreamWorkerLogGroup`, `ChatStreamWorkerRole`, `ChatStreamWorkerFunction`,
  `ChatStreamRouteFunction`, `ChatStreamingApi`, `ChatStreamConnectIntegration`,
  `ChatStreamDisconnectIntegration`, `ChatStreamMessageIntegration`, `ChatStreamingStage`, and
  the `ChatStreamingApiUrl` CfnOutput.
- `infra/infra/config.py` - `resolve_streaming`, and its call inside the whole-config validator.

### Config

- `config.yaml` - the `streaming:` block (`enabled`, `delta_min_chars`, `delta_max_delay_ms`,
  `output_guardrail`) plus its long header, and a passing mention in the `http_api` block's
  comment about the 29-second ceiling.

### Application code

- `app/streaming.py` - routes, `ConnectionSink`, `connection_expiry`, `CARDS_STAGE`,
  `MANAGEMENT_ENDPOINT`, `WORKER_FUNCTION_NAME`, `_invoke_worker`.
- `app/stream_worker.py` - the generation worker.
- `app/ws_authorizer.py` - the `$connect` authorizer.
- `app/history.py` - `open_connection`, `close_connection`, and the `CONN#` line in the module
  docstring's key table.
- `app/orchestrator.py` - `StreamSink` protocol, `_converse_streaming`, `_finished_block`,
  `_tell`, and the `stream=` parameter threaded through `run_chat`, `_prime_first_search` and
  `_run_tool`.
- `app/cards.py` - `preview_safe_prefix`, `card_block_started`, `_TAG_NAMES`/`_TAG_OPENINGS`.
- `app/requirements-authorizer.txt` - `pyjwt[crypto]>=2.8,<3`, its own file so the chat layer
  does not change shape.

### Frontend

- `frontend/src/lib/chatStream.ts` - the whole module.
- `frontend/src/lib/runtimeConfig.ts` - the optional `streamingApiUrl?: string` field.
- `frontend/src/lib/auth.ts` - `currentAccessToken`, and the docstring explaining the query-string
  token.
- `frontend/src/components/ChatApp.tsx` - `streamingReady`, `streamed()`, the fallback catch,
  `pendingPreview` / `pendingStage`.
- `frontend/src/components/PendingExchange.tsx` - `CARDS_STAGE`, `STAGE_LABELS`, `awaitingCards`.
- Fifteen string catalogues under `frontend/src/lib/strings/` carry `stageRetrieving` and
  `stageComposingCards`.

### Tests

- `app/tests/test_streaming.py` - the route function and `ConnectionSink`.
- `app/tests/test_stream_worker.py` - the worker.
- `app/tests/test_orchestrator.py` - streamed-vs-buffered equivalence, stream usage metadata,
  partial-JSON tool arguments, and that the buffered path never opens a stream.
- `app/tests/test_history.py` - the connection record's five tests.
- `app/tests/test_cards.py` - the preview stop rule.
- `app/tests/conftest.py` - the fake store's connection set and the fake management client.
- `infra/tests/unit/test_infra_stack.py` - the `--- WebSocket streaming API ---` section, plus
  the two bundling tests and the `config.json` key test.
- `infra/tests/unit/test_config.py` - four `resolve_streaming` tests.

### Eval

- **Nothing.** `eval/run_eval.py` drives `POST /chat` over `httpx` against the `ChatApiUrl`
  stack output with a `ChatEvalClientId` token. Grepping `eval/` for any streaming term returns
  no matches. The eval harness has never exercised this path.

### Docs

- `docs/cards-v2.md` names `app/streaming.py` and `CARDS_STAGE` in the Presentation section.
- `docs/build-plan.md` names `streaming.CARDS_STAGE` in one frontend bullet and "streaming" as a
  possible architectural answer in the Open section.
- `docs/aws-port-draft.md` still lists response streaming as an unbuilt v2 item.
- No doc before this one describes the path.

---

## 8. Every timeout and deadline

### Set by this stack

| Value | Where | What it bounds |
|---|---|---|
| 29 s | `CHAT_LAMBDA_TIMEOUT_SECONDS`, `infra/infra/config.py` → `ChatFunction.timeout` | the chat function's whole invocation |
| 15 s | `ChatStreamRouteFunction.timeout`, `infra_stack.py` | the route function |
| 60 s | `ChatStreamWorkerFunction.timeout`, `infra_stack.py` | the worker |
| 10 s | `ConnectAuthorizerFunction.timeout`, `infra_stack.py` | one JWKS lookup and one signature verification |
| 22 s | `chat.converse_deadline_seconds`, `config.yaml` | the converse loop's wall clock, **on both paths** |
| 3 s | `chat.title_deadline_seconds`, `config.yaml` | the titling call, on both paths |
| 6 iterations | `chat.max_converse_iterations`, `config.yaml` | model calls per turn, on both paths |
| 3 s | `_POST_LOOP_RESERVE_SECONDS`, `app/handler.py` | held back from Lambda's remaining time for response shaping |
| 1 s | `_POST_TITLE_RESERVE_SECONDS`, `app/handler.py` | held back for `json.dumps` |
| 6 s | `_POST_LOOP_RESERVE_SECONDS`, `app/stream_worker.py` | held back for the write, the title and the final push - larger than the handler's because that last push is a network call |
| 2 s | inline `- 2` in `stream_worker._title_deadline` | held back after the title call |
| 3 h | `CONNECTION_TTL_SECONDS`, `app/streaming.py` | how long a connection record outlives its connection |
| 250 ms | `streaming.delta_max_delay_ms`, `config.yaml` | how long a partial delta batch may wait |
| 45 s | `SILENCE_TIMEOUT_MS`, `frontend/src/lib/chatStream.ts` | silence on the socket before the browser gives up |
| 35 s | `REQUEST_TIMEOUT_S`, `eval/run_eval.py` | one eval HTTP request (buffered path only) |

Botocore client timeouts, all per-file:

| Client | Where | connect / read / retries |
|---|---|---|
| `bedrock-runtime` (Converse) | `app/orchestrator.py::_bedrock_client` | 5 / 25 / 3 adaptive |
| `bedrock-runtime` (guardrail) | `app/handler.py::_bedrock_client` | 5 / 10 / 3 adaptive |
| `bedrock-runtime` (guardrail) | `app/streaming.py::_bedrock_client` | 5 / 10 / 3 adaptive |
| `lambda` (worker invoke) | `app/streaming.py::_lambda_client` | 2 / 5 / **1 attempt** |
| `apigatewaymanagementapi` | `app/streaming.py::management_client` | 2 / 5 / 3 standard |

The one-attempt retry config on the Lambda client is not a tuning choice: the invoke is
asynchronous, so a retry racing a response the first call already delivered would start a second
generation worker - two answers to one question, both billed, both pushed down the same socket.
The same reasoning sets `configure_async_invoke(retry_attempts=0)` on the worker function itself
(`infra_stack.py`), pinned by `test_the_generation_worker_never_retries`.

### Not ours

- API Gateway HTTP API integration `timeoutInMillis`: max 30,000 ms, not raisable by quota
  request. Recorded in `infra_stack.py` and `docs/build-plan.md` as verified against the
  apigatewayv2 CreateIntegration reference on 2026-08-05.
- API Gateway WebSocket connection quotas: 10 minutes idle, 2 hours hard. Cited in
  `app/streaming.py`, `app/history.py` and `frontend/src/lib/chatStream.ts`.

### Which exist only because of the 29-second ceiling

**Directly caused by it:**

- **The route/worker split itself.** `handle_message`'s docstring and `stream_worker.py`'s
  module docstring both give exactly this reason: "a WebSocket route integration has the same
  29-second ceiling every API Gateway integration has, and the agent loop can use most of it".
  Remove the ceiling and there is no reason for `ChatStreamWorkerFunction` to be a separate
  function, and therefore no reason for the async invoke, the zero-retry config, the invoke
  payload, the `lambda:InvokeFunction` grant, or the `usage` round-trip through that payload.
- **`ChatFunction.timeout = 29`.** The constant's comment calls it a ceiling rather than a
  choice, one second under 30,000 ms so the function's own timeout wins and the failure is
  diagnosable in its logs rather than only as a gateway 504.
- **The validator in `infra/infra/config.py`** that rejects `converse_deadline_seconds >=
  CHAT_LAMBDA_TIMEOUT_SECONDS`, and the second one rejecting `deadline + title_deadline >=
  CHAT_LAMBDA_TIMEOUT_SECONDS`. The value 22 is bounded by 29, not chosen against a measurement.
- **`handler._POST_LOOP_RESERVE_SECONDS` and `_POST_TITLE_RESERVE_SECONDS`**, which exist to
  keep the loop and the title inside that 29.

**Bounded by it but not caused by it:**

- `ChatStreamRouteFunction.timeout = 15`. It is under the ceiling, but the stack's comment
  justifies it on work rather than the ceiling: "It writes one or two DynamoDB items and makes
  one guardrail call."

**Explicitly *not* caused by it, and stated as such:**

- `ChatStreamWorkerFunction.timeout = 60` - longer *because* the worker escapes the ceiling.
  Pinned by `test_the_streaming_functions_answer_under_the_same_caps_as_the_buffered_one`, which
  asserts `worker["Timeout"] > 29`.
- **`converse_deadline_seconds` is deliberately unchanged on the worker.**
  `stream_worker._deadline`'s docstring: the worker is not behind the ceiling and could be given
  more, but "a longer budget here would make a streamed turn answer questions a buffered turn
  gives up on, and 'the finished turn renders identically to the buffered one for the same
  question' is the property this whole feature is held to." The same test asserts
  `CONVERSE_DEADLINE_SECONDS`, `MAX_CONVERSE_ITERATIONS`, `MAX_HISTORY_MESSAGES`,
  `CARD_MAX_CARDS`, `CARD_DESC_MAX_CHARS` and `GENERATION_MAX_TOKENS` are byte-identical between
  the chat function and the worker.

**Unrelated to the ceiling:** the connection TTL, the delta batching numbers, the browser's
silence timer, the authorizer's 10 s, and every botocore client timeout.

---

## 9. Per-function IAM

All four functions carry the AWS managed `service-role/AWSLambdaBasicExecutionRole` and an
explicit `logs.LogGroup` with `RetentionDays.THREE_MONTHS` and `RemovalPolicy.DESTROY`.

### `ConnectAuthorizerFunction`

Managed basic execution and **nothing else**. No inline policy at all. The stack's comment: it
reaches exactly one thing, the pool's public JWKS document over https, which needs no
credentials - "an authorizer that could read the history table would be an authorizer that could
be made to." Pinned by `test_the_connect_authorizer_can_reach_nothing_but_its_own_logs`, which
asserts no `AWS::IAM::Policy` names its role.

### `ChatStreamRouteFunction` (`ChatStreamRouteRole`)

| Actions | Resource | Why |
|---|---|---|
| `dynamodb:PutItem`, `dynamodb:DeleteItem` | the chat history table | `open_connection`, `close_connection`, and the student's message |
| `dynamodb:UpdateItem` | the chat history table | the rate limit's conditional `ADD` and the header's `messageCount` |
| `bedrock:ApplyGuardrail` | the input guardrail ARN | the input screen |
| `lambda:InvokeFunction` | the worker (via `grant_invoke`) | the async hand-off |
| `execute-api:ManageConnections` | the WebSocket API (via `grant_manage_connections`) | pushing frames |

No `Query`, no `GetItem`, no `Scan`. `test_the_streaming_route_function_writes_but_never_reads_a_conversation`
asserts the DynamoDB action set is exactly `{PutItem, DeleteItem, UpdateItem}`.

### `ChatStreamWorkerFunction` (`ChatStreamWorkerRole`)

Every statement in `_chat_turn_statements` - the same list object built for the chat function's
role, replayed:

- `bedrock:Retrieve` on the knowledge base ARN.
- `bedrock:InvokeModel*` on the generation inference-profile ARN, the underlying foundation
  model in this region, and `arn:aws:bedrock:*::foundation-model/<base>` for cross-region
  routing.
- `bedrock:GetInferenceProfile`, `bedrock:ListInferenceProfiles` on `*` (no resource-level
  scoping exists for the list call).
- `bedrock:InvokeModel*` for the titling model, present because
  `title_model_id: us.anthropic.claude-haiku-4-5-20251001-v1:0` differs from
  `model_id: us.anthropic.claude-sonnet-4-6`.
- `bedrock:ApplyGuardrail` on the guardrail ARN.
- `dynamodb:Query`, `GetItem`, `PutItem`, `UpdateItem`, `DeleteItem`, `BatchWriteItem`,
  `ConditionCheckItem`, `DescribeTable`, scoped to the table ARN.

Plus `execute-api:ManageConnections`. **Not** `lambda:InvokeFunction` - the worker must not be
able to start another worker, asserted by
`test_both_streaming_functions_can_push_but_only_the_route_can_start_the_worker`.

`test_the_worker_holds_exactly_the_chat_turns_grants` asserts the `dynamodb:` and `bedrock:`
action sets of `ChatStreamWorkerRole` and `ChatFunctionRole` are **equal**, and that
`dynamodb:Scan` is in neither.

### `ChatFunction` (`ChatFunctionRole`)

`_chat_turn_statements`, and nothing else. No `ManageConnections`, no `lambda:InvokeFunction`.

Two notes that apply across the table. The DynamoDB grant is hand-written rather than
`grant_read_write_data` for one action: that helper's read set includes `dynamodb:Scan`, the
only operation that takes no partition key, which is precisely the hole in the isolation the
single-table design exists for. And **reserved concurrency is set only on `ChatFunction`**
(`cfn_chat.reserved_concurrent_executions = http_api_cfg["chat_reserved_concurrency"]`, 20).
Neither streaming function has one; what fences them is the stage throttle
(`rate_limit`/`burst_limit` 10/20, the same numbers as the HTTP stage) and the per-user daily
cap the route function applies.

---

## 10. Bundling mechanics, and the tests that pin them

### Layers

Three pip-built deps layers exist in a streaming-on synth:

| Layer | Requirements file | Carried by |
|---|---|---|
| `ChatDepsLayer` | `app/requirements.txt` (`pydantic>=2,<3`) | `ChatFunction`, `ChatStreamRouteFunction`, `ChatStreamWorkerFunction` |
| `ConnectAuthorizerDepsLayer` | `app/requirements-authorizer.txt` (`pyjwt[crypto]>=2.8,<3`) | `ConnectAuthorizerFunction` |
| `ScraperDepsLayer` | `scraper/requirements.txt` | the scraper (not on this path) |

Both chat-side layers are built with `BundlingOptions(image=_LAMBDA_PYTHON.bundling_image,
local=_PipManylinuxLayerBundler(...), command=["bash","-c","pip install -r <file> --target
/asset-output/python"], platform="linux/amd64")`, with `exclude=["*", ".*", "!<the requirements
file>"]` and `asset_hash=_requirements_hash("<name>", <path>)` under
`AssetHashType.CUSTOM`.

The custom asset hash is load-bearing and the stack carries a long note about why. Verified
against aws-cdk-lib 2.260.0's `asset-staging.js`: an asset's cache key is a sha256 over its
staging props, the staged result comes from `assetCache.obtain(cacheKey, ...)`, and the bundle
directory is `bundling-temp-${cacheKey}` which `bundle()` skips entirely if it already exists.
Layers with the same image, command, platform and one-file `exclude` hash to the same key and
silently reuse each other's bundle. The failure mode: synth is clean, both layers publish, the
deploy succeeds, and the function dies at cold start on `No module named 'pydantic'`, visible
only in CloudWatch behind a 502. `_requirements_hash` folds the layer *name* into the hash, so
the three keys are distinct. The accepted tradeoff of CUSTOM over OUTPUT: the hash tracks the
requirements *file*, so a floating dependency resolving to a newer patch does not by itself
publish a new layer.

`test_the_authorizer_layer_is_its_own_asset_and_carries_the_crypto_wheel` asserts there are
exactly three such layers, that their staged `S3Key`s are three distinct values, that the
authorizer layer stages exactly `["python"]`, and that its `python/` directory contains a
package starting `jwt` and one starting `cryptography`.

The authorizer layer is separate rather than a line in `app/requirements.txt` for two reasons in
the stack's comment: `cryptography` is a large compiled wheel the chat function has no use for,
and POST /chat is under a hard "keeps working unchanged" constraint, so changing its layer would
change its deployed artifact for a feature it does not run.

### Function code assets

Every function uses `Code.from_asset(str(_APP_DIR), exclude=[...])` with an explicit
allowlist - `"*"`, `".*"`, then one `"!module.py"` per file. The `".*"` entry is for the dotfile
trap: without it `app/.venv` and `.pytest_cache` would land inside the deployed function.

| Function | Modules bundled | Count |
|---|---|---|
| `ChatFunction` | handler, settings, models, prompts, tools, retrieve, cards, safety, escalation, places, orchestrator, campus_time, history, titles, usage, ratelimit | 16 |
| `ChatStreamWorkerFunction` | the 16 above minus `handler`, plus `stream_worker` and `streaming` | 17 |
| `ChatStreamRouteFunction` | streaming, settings, history, models, usage, ratelimit, cards, retrieve | 8 |
| `ConnectAuthorizerFunction` | ws_authorizer | 1 |

`retrieve.py` is in the route function's bundle only because `cards.py` imports it at module
scope; the stack's comment records that `cards` is there for `preview_safe_prefix` alone and
nothing on that function parses a card.

### The tests that pin them

**`test_every_streaming_bundle_carries_what_its_handler_imports`** (`infra/tests/unit/test_infra_stack.py`)
is the one that pins the include lists. It parses each `app/*.py` with `ast`, computes the
transitive local-import closure of each handler module, reads the actually-staged `.py` files
off the synthesized asset directory, and asserts **set equality in both directions** for all
four functions:

```
needed <= staged   # else the deployed function dies at cold start on an ImportError
staged <= needed   # else the bundle ships something nothing imports
```

Its docstring is explicit that the expectation is computed from source and never restated, "for
the reason the union test gives: a test that repeats the list is a second copy of the thing that
goes stale."

**`test_every_app_module_reaches_the_staged_lambda_asset`** (the union test above it) asserts the
union of all four bundles equals the set of non-test `*.py` files in `app/`, in both directions,
and separately asserts `per_function["ConnectAuthorizerFunction"] == {"ws_authorizer.py"}` -
"the module that decides who a caller is should not be able to reach the store, the model or the
guardrail." Its docstring records that this test was narrowed rather than weakened when the
streaming path landed, because "every module reaches the chat function" stopped being true.

Both read the staged directories off one real `app.synth()` with streaming forced on
(`_streaming_outdir`), cached with `functools.lru_cache(maxsize=1)` because a second synth costs
another deps-layer bundle and another containerized Astro build.

---

## 11. Ambiguities, and what I could not determine

Marked separately from the read facts above.

- **The route function's return value on `sendMessage`.** `_ack`'s docstring says the body is
  not delivered to the client on `$connect` or `$disconnect` and that only the status code
  decides whether the handshake completes. It says nothing about the message route. No
  `AWS::ApiGatewayV2::RouteResponse` is created anywhere - `infra_stack.py` never calls
  `return_response`, and `test_no_websocket_resource_carries_an_http_only_property` counts
  exactly nine WebSocket resources ("the API, its authorizer, three routes, three integrations
  and the stage"). **(inferred)** the 400/429/401 status codes `handle_message` returns are
  therefore not delivered to the browser at all; the client learns of those outcomes only from
  the `error` or `final` frame sent alongside them. I did not find this stated anywhere in the
  repo. **Also undetermined:** whether a non-2xx return from a message route makes API Gateway
  close the connection. It matters for exactly one case - the missing-`sub` branch of
  `handle_message`, which is the only path that returns without pushing a frame, so the browser
  falls back on either `onclose` (immediate failure) or the 45-second silence timer depending on
  the answer.
- **Whether API Gateway caches the `$connect` authorizer's answer.** `infra_stack.py` records it
  as not documented either way, and there is no knob to set. Undetermined.
- **Whether the streaming path is live in the deployed stack.** `config.yaml` commits
  `enabled: true`, but whether that config has been deployed is not knowable from the repo.
  `resolve_streaming`'s docstring still says "off is the shipped default", which the committed
  config contradicts; `test_streaming_is_off_unless_the_block_says_otherwise` records the change
  and now asserts only that the *resolver* defaults to off.
- **Any measured latency or message-count figure for a real streamed turn.** The only
  measurements in the repo about this path are the output-guardrail latency numbers
  (2026-08-12, us-west-2, claude-sonnet-4-6, n=4: time-to-first-token median 1.12 s → 6.75 s
  with `sync` mode) and the estimate in `config.yaml` that a 1,500-character reply is about ten
  messages at `delta_min_chars: 160`. There is no measurement of end-to-end streamed turn
  latency, no measured message count from a real deployment, and no cost figure for the socket.
- **Whether `converse_stream` reports usage identically to `converse` in every case.**
  `usage.record_model_call` treats a missing `usage` block as a call with zero tokens, and
  `_converse_streaming` returns `None` for usage if no `metadata` event arrived. Whether that
  ever happens in practice is not established anywhere in the repo.
- **Two turns racing on one connection.** `turnId` exists precisely for this
  (`handle_message`'s comment: "two turns racing on one connection - lands on the right bubble
  or is discarded"), and `test_every_frame_names_its_turn` pins that every frame carries it. But
  `chatStream.ts` never compares `frame.turnId` against anything: it opens one socket per turn,
  so within a socket there is only ever one turn. The field is carried and, on the client as
  written, unread. Whether it is meant to become a client-side filter is not stated.
- **The connection record's purpose.** Nothing reads it. Its docstring says it is "a record, not
  a gate". What it is a record *for* - operational visibility, a future feature - is not stated
  anywhere I found.
- **`output_guardrail: true` has never been exercised end to end.** `_guardrail_config()` can
  only emit `sync` mode, `test_the_output_guardrail_can_only_ever_be_synchronous` pins that, and
  the infra test pins that the env var is present when the config says so. What a `sync`-mode
  streamed turn actually does to the preview cadence is described by the measurement above but
  not tested.

---

## 12. What would have to change if the three functions became one

A plain enumeration, no ordering implied and no judgement about difficulty.

**Resources removed or merged (`infra/infra/infra_stack.py`)**

1. `ChatStreamRouteFunction` and `ChatStreamWorkerFunction` collapse to one function, or into
   `ChatFunction`.
2. `ChatStreamRouteRole` and `ChatStreamWorkerRole` collapse; the merged role needs the union -
   the full `_chat_turn_statements` plus `execute-api:ManageConnections` plus the route
   function's `PutItem`/`DeleteItem`/`UpdateItem`, and would no longer need
   `lambda:InvokeFunction`.
3. `ChatStreamRouteLogGroup` and `ChatStreamWorkerLogGroup` collapse to one.
4. `streaming_worker_lambda.grant_invoke(streaming_route_lambda)` goes.
5. `streaming_worker_lambda.configure_async_invoke(retry_attempts=0)` goes, and with it the
   `AWS::Lambda::EventInvokeConfig` resource.
6. `STREAM_WORKER_FUNCTION_NAME` goes from the environment.
7. The `for function in (streaming_route_lambda, streaming_worker_lambda)` loop that grants
   `ManageConnections` and sets `STREAM_CALLBACK_URL`, `STREAM_DELTA_MIN_CHARS` and
   `STREAM_DELTA_MAX_DELAY_MS` becomes a single-target block.
8. The two `node.add_dependency(chat_history_table)` / `add_dependency(knowledge_base)` pairs
   collapse.
9. `ConnectAuthorizerFunction`, `ConnectAuthorizerDepsLayer`, `ConnectAuthorizerLogGroup` and
   `ChatConnectAuthorizer` only survive if the merged design still terminates a WebSocket at API
   Gateway. If it does not, all four go, and with them `app/requirements-authorizer.txt` and the
   third deps layer.
10. `ChatStreamingApi`, `ChatStreamingStage`, the three integrations and the three routes only
    survive on the same condition.
11. `_STREAMING_STAGE_NAME` and the `ChatStreamingApiUrl` CfnOutput.

**Application code**

12. `app/streaming.py` and `app/stream_worker.py` merge into one module, or their contents move
    into whatever the new entrypoint is.
13. `_invoke_worker` and the invoke payload contract (`connectionId`, `turnId`, `userId`,
    `conversationId`, `isNewConversation`, `userSortKey`, `query`, `followup`, `usage`) go. The
    `usage` round-trip through that payload becomes an ordinary in-process object; `userSortKey`
    becomes a local variable rather than a wire field.
14. `_lambda_client` and its one-attempt retry config go.
15. The two deadline reserves (`_POST_LOOP_RESERVE_SECONDS = 3` in the handler, `= 6` in the
    worker, plus the inline `- 2` in `_title_deadline`) become one set of numbers, and the
    reason the worker's is larger - the final push is a network call - has to be re-decided
    against whatever the new function does after the loop.
16. The duplicated `apply_input_guardrail` and `_bedrock_client` in `app/streaming.py` become
    redundant with `app/handler.py`'s, since the import-cycle argument for duplicating them
    (pulling the HTTP path's module scope into a function that serves neither) no longer holds
    if it is one function.
17. The two route dispatchers (`handler.lambda_handler` reading top-level `routeKey`,
    `streaming.lambda_handler` reading `requestContext.routeKey`) become one, which has to
    handle both event shapes or be replaced by whatever the new front door dispatches on.
18. `ConnectionSink` survives only if `post_to_connection` survives. If the transport becomes
    in-band response streaming, `management_client`, `is_gone`, the 410 handling and
    `MANAGEMENT_ENDPOINT` all go, and the batching (`_min_chars`, `_max_delay`) has to be
    re-justified against whatever the new per-chunk cost is - it exists today because every
    `post_to_connection` is a billable API Gateway message.
19. `handle_connect` / `handle_disconnect` and the whole connection-record lifecycle
    (`open_connection`, `close_connection`, `connection_expiry`, `CONNECTION_TTL_SECONDS`) go if
    there is no connection to record. `app/history.py`'s key table loses its `CONN#` row.
20. `orchestrator.StreamSink`, `_converse_streaming`, `_finished_block`, `_tell` and the
    `stream=` parameter threaded through `run_chat`, `_prime_first_search` and `_run_tool` all
    survive unchanged if `ConverseStream` is still the model call. Only the sink's *implementation*
    changes.
21. `cards.preview_safe_prefix` and `cards.card_block_started` survive unchanged for the same
    reason.
22. `handler.run_turn`'s single ordered sequence and the split version across
    `handle_message`/`stream_worker` become one, which means re-deciding where the write of the
    student's message sits relative to the point of no return for the client's HTTP fallback.

**Config**

23. `resolve_streaming` (`infra/infra/config.py`) - `delta_min_chars` and `delta_max_delay_ms`
    survive only if batching survives; `output_guardrail` survives if `ConverseStream` does;
    `enabled` survives if the feature stays gated at all.
24. `config.yaml`'s `streaming:` block, and its header comment which is currently an argument
    for the WebSocket over the alternatives.
25. The `http_api` block's comment about the 29-second ceiling, and
    `CHAT_LAMBDA_TIMEOUT_SECONDS = 29` plus both validators that check
    `converse_deadline_seconds` (and `+ title_deadline_seconds`) against it, if the merged
    function is no longer behind a 29-second integration.

**Frontend**

26. `frontend/src/lib/chatStream.ts` - the transport, the `ServerFrame` union, the
    `StreamUnavailable` class, the `accepted` line that decides fallback eligibility, the
    `SILENCE_TIMEOUT_MS` silence timer, and the connection-per-turn model.
27. `frontend/src/lib/auth.ts::currentAccessToken` and its query-string-token docstring, if the
    token can travel in an `Authorization` header again.
28. `frontend/src/lib/runtimeConfig.ts`'s `streamingApiUrl?: string`, and the `config.json`
    stamping that fills it - which is currently the *only* thing selecting the transport.
29. `frontend/src/components/ChatApp.tsx` - `streamingReady`, `streamed()`, the two-stage
    `.catch` chain, and the `previewed`/`revealedChars` hand-off.
30. `frontend/src/components/PendingExchange.tsx` and `CARDS_STAGE`, if the stage frames change
    shape. The fifteen string catalogues' `stageRetrieving` / `stageComposingCards` keys go with
    them.
31. Whether `accepted`'s `conversationId` and the `error` frame's `limit`/`resetAt`/
    `retryAfterSeconds` are still sent, given nothing currently reads either (§6).

**Tests**

32. `app/tests/test_streaming.py` (31 tests over the route function and `ConnectionSink`)
    and `app/tests/test_stream_worker.py` (14 tests).
33. `app/tests/test_history.py`'s five connection-record tests, and `app/tests/conftest.py`'s
    fake connection set and fake management client.
34. `infra/tests/unit/test_infra_stack.py`'s whole `--- WebSocket streaming API ---` section,
    including `test_no_streaming_resources_exist_when_the_key_is_absent`,
    `test_only_connect_is_authorized_and_it_reads_the_token_from_the_query_string`,
    `test_the_connect_authorizer_carries_no_result_ttl`,
    `test_no_websocket_resource_carries_an_http_only_property`,
    `test_the_generation_worker_never_retries`,
    `test_both_streaming_functions_can_push_but_only_the_route_can_start_the_worker`,
    `test_the_streaming_route_function_writes_but_never_reads_a_conversation`, and
    `test_the_worker_is_told_where_to_push_by_the_stack_not_by_the_request`.
35. `test_the_worker_holds_exactly_the_chat_turns_grants` becomes vacuous if there is one role.
36. `test_the_streaming_functions_answer_under_the_same_caps_as_the_buffered_one` becomes
    vacuous if there is one function reading one environment - and the property it protects
    (a streamed turn answers under the same caps as a buffered one) becomes structural rather
    than asserted.
37. Both bundling tests - `test_every_app_module_reaches_the_staged_lambda_asset` and
    `test_every_streaming_bundle_carries_what_its_handler_imports` - are keyed on the four
    construct-id prefixes and the four entrypoint modules. Both lists change.
38. `test_the_authorizer_layer_is_its_own_asset_and_carries_the_crypto_wheel` asserts exactly
    three deps layers; that count changes if the authorizer layer goes.
39. `infra/tests/unit/test_config.py`'s four `resolve_streaming` tests.
40. `app/tests/test_orchestrator.py`'s streamed-vs-buffered equivalence tests survive unchanged
    if `run_chat`'s sink protocol survives.

**Unaffected either way**

41. `eval/` - the harness has never touched this path (§7).
42. `docs/accounts-and-storage.md`'s turn lifecycle, storage schema and access patterns, except
    for the `CONN#` item kind.
43. `docs/cards-v2.md`'s card contract, except its two references to `app/streaming.py` and
    `CARDS_STAGE`.

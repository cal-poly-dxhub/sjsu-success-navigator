# The eval harness (eval/)

Two on-demand tools that run against the **deployed** stack, plus a renderer. Neither is part
of CI and both cost real money. The code carries one-line pointers into the sections below.

- `run_eval.py` collects the deployed system's answers to `ground-truth.yaml`. No scoring.
- `render_results.py` turns a transcript into one self-contained HTML page.
- `measure_usage.py` measures what a real question costs, for `config.yaml`'s
  `cost_model.measured` block.

This repo pins nothing for `eval/`: `python3 -m pip install boto3 httpx PyYAML`.

## run_eval.py

**It is deliberately judgment-free.** It sends each ground-truth question to the real deployed
endpoint (genuine HTTP through API Gateway with a real Cognito access token, on `POST /chat`)
and records what came back. The browser streams its own turns over `/api/chat` and falls back to
this route, so the eval measures the same turn behind a different door: `app/turn.py` holds the
one copy of the order and both transports run it. Accuracy is decided by humans, and by Claude
reading the transcript, never by this script. The only derived field is
`behavior_fired`, a mechanical classification of the response shape (safety panel present,
cards present, prose only, error) so the rendered page can badge each answer.

Artifacts per run, under `--out-dir`: `eval-<UTC stamp>.json` (run metadata plus every wire
response verbatim) and `eval-<UTC stamp>.html`.

### Auth no longer mirrors the frontend, and cannot

The browser signs in by redirecting to Cognito managed login (authorization code with PKCE),
which needs a browser and a human. This runner is headless, so it uses the pool's **second app
client**, the machine one, with a single unsigned `USER_PASSWORD_AUTH` `InitiateAuth` call, and
the **access** token (not the id token) as the Bearer. Both clients sit in the API's JWT
audience and in the streaming app's own allowlist, so this token is accepted wherever the
browser's is.

**`ChatEvalClientId`, never `ChatWebClientId`.** The web client has no password flow enabled
(its `ExplicitAuthFlows` is exactly `ALLOW_REFRESH_TOKEN_AUTH`), so reaching for its id here
fails with `NotAuthorizedException`.

The eval account is a machine account, not the retired shared login: humans each have their own
account now and reach it through the redirect, which this client cannot serve at all. Its
password comes from `EVAL_PASSWORD` or `--password-file`, never argv and never this repo. A
challenge response instead of a token means the pool user is not in a permanent-password state.

### Endpoint discovery

`ChatApiUrl` and `ChatEvalClientId` are read from the CloudFormation stack outputs, so nothing
is hardcoded; `--api-url` and `--client-id` override for testing against a different deployment.

### Request shape

**Per-request timeout is 35s.** The HTTP API integration cap is 30s, so anything past about 32s
is the gateway timing out rather than the answer still coming.

**One retry on a 429,** because the runner fires the whole set as one account. Concurrency
defaults to 3, which stays well under the stage's 10 rps throttle. The eval's machine client is
exempt from the per-user daily message cap by `client_id` (`app/ratelimit.py`), because the set
is larger than any number the cap could be raised to and would have to be kept above whatever
`ground-truth.yaml` grows to.

Each question is asked as a **fresh single-turn conversation**, so no answer is influenced by
the one before it.

## render_results.py

Presentation only, no scoring: each question gets the golden expectation on the left, straight
out of `ground-truth.yaml`, and the system's real answer on the right, straight out of the
transcript, so a human decides accuracy. The only mechanical annotations are the behaviour
badge, bolding of expected source URLs the response actually cited, and each pair's recorded
verdict chip when a judgments file exists.

Judgments file (optional): `eval/judgments/<transcript stem>.yaml`, either `route-tutor: pass`
or `route-tutor: {verdict: fail, note: "gave the 5910 number"}`.

**`trailingText` renders below the cards,** because that is where it renders for the student. A
closing question shown above the grid on this page would misreport the thing the page exists to
show.

## measure_usage.py

```
python3 measure_usage.py --audit                          # check the meter, measure nothing
EVAL_PASSWORD=... python3 run_eval.py --sample 40         # 40 deployed turns
python3 measure_usage.py --from-eval results/eval-<stamp>.json
python3 measure_usage.py --questions 24                   # the older all-local path
```

`config.yaml`'s `cost_model.measured` block feeds the cost panel, and every number in it has to
be measured rather than guessed, because three properties of this architecture make the obvious
estimate wrong and all three are invisible from outside:

1. **One question is not one model call.** The Converse loop runs until the model ends its
   turn, and every iteration resends the whole accumulated context.
2. **The retrieved passages ride in the input tokens.** The primed first search puts up to
   `cards.max_retrieval_results` chunks in front of the model before it says anything, so input
   tokens are dominated by retrieval rather than by the student's sentence.
3. **History is re-sent every turn,** so question five does not cost what question one costs.
   This measures by position and fits the slope rather than reporting one average.

### --audit checks the meter before anybody reads it

`app/usage.py` is what the cost panel prices a conversation from, and it counts **inside** the
loop, which is the only place those counts exist. Nothing outside the loop can corroborate it,
so `--audit` runs real turns with a real `TurnUsage` attached and captures every Converse and
ApplyGuardrail response at the client, then puts the two side by side.

**It compares per model, never in total.** This stack answers on Sonnet and names conversations
on Haiku. A comparison of summed input tokens passes whether or not the two models' tokens
landed in the right fields, which is how the titling call spent four months being priced at the
generation rate (`app/usage.py`, `record_title_call`). So the audit's rows are
generation-in, generation-out, title-in, title-out, calls and guardrail units, and any one of
them disagreeing is a non-zero exit.

**It keeps drawing questions until it has seen a multi-call turn as well as a single-call one.**
A tally that agrees on one call proves nothing about the loop, because the loop is where the
addition happens: a turn that searches again bills a second full context, and the panel has to
see both.

### --from-eval takes the single-turn half from the deployed endpoint

The questions are asked by `run_eval.py --sample 40` through API Gateway and the chat Lambda,
signed in as the eval machine account, so the numbers describe the deployed system whole (the
handler, the store, the cap, the guardrail) rather than a loop run in a local process
under a developer's credentials. `--sample N` takes an even stride through `ground-truth.yaml`,
which is grouped by behaviour, so the run keeps the set's own mix rather than a head or a
random draw's.

**One wire `usage` block cannot say which model spent what,** and the deployed function is
whatever was last deployed, so a run against it cannot be asked for the split. AWS publishes
it anyway: `AWS/Bedrock` carries `Invocations`, `InputTokenCount` and `OutputTokenCount` per
`ModelId`. Summed over the run's window (`started_utc`/`finished_utc`, recorded in the
transcript for exactly this), those are the same calls the transcript counted, taken apart by
model. `retrieval_query_tokens` stops being an assumption on the same run, because the
embedding model has a counter too.

**The split is reconciled before anything is derived from it,** call for call and token for
token against the run's own wire totals, and a disagreement raises. AWS/Bedrock's finest period
is 60 seconds, so the window is rounded out to whole minutes either side; the reconciliation is
what makes that safe, because both failure modes (a neighbouring minute's traffic, and a tail
the metrics have not published yet) break it loudly instead of producing plausible numbers.

**The depth slope is still measured locally,** because it needs to control the history in front
of each question exactly, which is a thing the server owns and a client cannot ask for. Its
prior turns are the real ones from the same 40-turn run.

### How it reaches the deployed stack, and the one honest limitation

It runs `app/orchestrator.py` **in this process** against the deployed stack's own Bedrock
model, knowledge base, guardrail and prompt, every one of those read out of the deployed chat
Lambda's **environment** (`aws lambda get-function-configuration`), never from `config.yaml` and
never hardcoded. So the model calls, retrievals and guardrail screens are real, billed, and
shaped exactly as production shapes them. The function's physical name is discovered from the
stack's own resources, because it carries CloudFormation's hash and differs per deployment; a
literal would silently measure a different account's function.

**The all-local path does not go through API Gateway and Lambda,** which is what `--from-eval`
above exists to fix; it survives as the way to measure without an eval account. Either way the
Lambda line is the one thing neither can observe: the Bedrock, guardrail and retrieval lines are
**measured**, and that one is not.
`chat_lambda_gb_seconds` is filled from the chat function's real billed durations in CloudWatch
(its `REPORT` lines, over actual invocations, last 30 days) multiplied by its configured memory,
because a local run has no cold start, no VPC attach and no handler work around the model call,
so timing this process would understate the real thing. That line is well under a tenth of a
cent against a roughly 4-cent question, and the panel says which lines are which rather than
blurring them. With no invocation logged, the constant is left alone rather than invented.

**It costs real money and creates nothing.** Every question is a real Bedrock call plus a real
guardrail screen. No DynamoDB turn is stored, because `run_chat` is called directly and the
handler's write path never runs.

### The AWS profile is set process-wide, before anything under app/ is imported

`app/orchestrator.py` and `app/retrieve.py` build their own boto3 clients from the **default**
session, exactly as they do in Lambda, so without this they resolve whatever credentials happen
to be in the environment and quietly measure a different account. That failure is not loud: a
retrieval against an account with no such knowledge base raises inside the loop's own
try/except and degrades to "the model will search itself", which would still produce
plausible-looking token counts for a system nobody deployed.

**A turn that retrieved nothing is a broken measurement, not a cheap question,** and it raises.
The primed search runs on every turn and swallows its own failures by design, so a zero means
the knowledge base was unreachable and the input tokens are missing the passages that dominate
them. Refusing loudly, because the alternative is a plausible number that understates the real
cost.

**Usage is captured by wrapping the bedrock-runtime client rather than editing
`app/orchestrator.py`.** The loop under measurement has to be the loop that runs in production,
byte for byte, or the numbers describe code nobody deployed. Converse already reports usage on
every response; the wrapper just stops throwing the field away.

**The guardrail screen is a real `ApplyGuardrail` call, not arithmetic over `len(query)`.** It
is billed per 1,000-character unit over the bare query, so it is a property of question length
and nothing else, but the unit accounting is the service's to define and this is the number that
appears on a bill.

### The question sample

Real questions from the ground-truth set, spread across its behaviours by an even stride
through the file, rather than a hand-written list. The sample keeps the same mix of RAG answers
and safety triage a real day has, instead of taking the safety block that opens the file.
**Safety questions are not excluded:** there is no pre-model phrase gate, so they cost a full
model call like any other turn and dropping them would flatter the average.

### The depth slope is a controlled experiment

The obvious version, walking a conversation and watching input tokens climb, does not measure
history at all. Every turn asks a **different** question, so it retrieves different passages, and
passage length varies by thousands of tokens where a prior turn is worth tens. The question
effect swamps the history effect and the fitted slope comes out whatever direction the question
order happened to point: a two-turn trial run fitted **-280 tokens per prior turn**, which would
price history as a discount.

So each probe question is asked repeatedly, unchanged, in front of a history that grows:
identical question, identical retrieval, and the only thing moving is the transcript the server
replays. The design is balanced, every probe measured at every depth, so the probes' different
bases cancel out of the slope. The history is **real** prior turns taken from the single-turn
run above rather than synthesized, and it costs no extra model calls to assemble. It is trimmed
to the window the server shows the model, so past the trim the series flattens exactly as
production does.

**The slope is fitted on tokens per call, not per question,** or the loop-length effect (first
questions need a second call far more often than follow-ups) contaminates it.

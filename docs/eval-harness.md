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
endpoint (genuine HTTP through API Gateway with a real Cognito access token, over the same
gated route the browser uses) and records what came back. Accuracy is decided by humans, and by
Claude reading the transcript, never by this script. The only derived field is
`behavior_fired`, a mechanical classification of the response shape (safety panel present,
cards present, prose only, error) so the rendered page can badge each answer.

Artifacts per run, under `--out-dir`: `eval-<UTC stamp>.json` (run metadata plus every wire
response verbatim) and `eval-<UTC stamp>.html`.

### Auth no longer mirrors the frontend, and cannot

The browser signs in by redirecting to Cognito managed login (authorization code with PKCE),
which needs a browser and a human. This runner is headless, so it uses the pool's **second app
client**, the machine one, with a single unsigned `USER_PASSWORD_AUTH` `InitiateAuth` call, and
the **access** token (not the id token) as the Bearer. Both clients are in the API's JWT
audience, so the token this gets is accepted on exactly the same route the browser's is.

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
exempt from the per-user daily message cap by `client_id` (see `docs/chat-service.md`, The
daily message cap), because the set is larger than any number the cap could be raised to and
would have to be kept above whatever `ground-truth.yaml` grows to.

Each question is asked as a **fresh single-turn conversation**, so no answer is influenced by
the one before it.

The run best-effort warms the function first through the ungated `GET /warm` route, which
exists for exactly this.

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

`EVAL_PASSWORD=... python3 measure_usage.py --questions 24`

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

### How it reaches the deployed stack, and the one honest limitation

It runs `app/orchestrator.py` **in this process** against the deployed stack's own Bedrock
model, knowledge base, guardrail and prompt, every one of those read out of the deployed chat
Lambda's **environment** (`aws lambda get-function-configuration`), never from `config.yaml` and
never hardcoded. So the model calls, retrievals and guardrail screens are real, billed, and
shaped exactly as production shapes them. The function's physical name is discovered from the
stack's own resources, because it carries CloudFormation's hash and differs per deployment; a
literal would silently measure a different account's function.

**What it does not do is go through API Gateway and Lambda.** `/chat` now reports its usage on
the wire, so that is no longer the reason. The reasons now are that a run through the API would
store 24 conversations of eval traffic under the runner's account, and that the depth
experiment needs to control the history in front of each question exactly, which is a thing the
server owns and a client cannot ask for. The consequence is precise and small: the Bedrock,
guardrail and retrieval lines are **measured**, and the Lambda line is not.
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

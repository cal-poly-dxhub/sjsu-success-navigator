# Navigator install guide

## 1. In your AWS account

- Credentials that can create IAM, Lambda, S3, DynamoDB, Cognito, CloudFront.
- A US region (both generation models are `us.` profiles).
- Bedrock model access: `amazon.titan-embed-text-v2:0`
- Bedrock model access: `us.anthropic.claude-sonnet-4-6`
- Bedrock model access: `us.anthropic.claude-haiku-4-5-20251001-v1:0`
- Python 3.13, Node.js 22, the CDK CLI, and a running Docker daemon.
- `cdk bootstrap`, once per account and region.

Model access is granted in the Bedrock console, per region. Without the embedding model
nothing indexes and every answer is empty. Without Sonnet every question errors. Without
Haiku conversations silently keep their first-message name.

Docker is not optional: synth builds the site and the scraper's dependency layer in
containers.

## 2. Config values

One value in `config.yaml`, read at synth. Set it before you deploy.

**`okta.metadata_url`** ships pointing at a rehearsal Okta org. Blank it to run on local
Cognito accounts, or point it at your own tenant. Absence is the gate; there is no flag.

`vector_store.vector_bucket_name`, `knowledge_base.name` and `chat_history.table_name` are
names rather than knobs. Change them only for a second install in the same account and
region, where they would collide.

## 3. Deploy

```bash
cd infra
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt

cdk deploy
```

CloudFront takes 15 to 30 minutes. The scraper fires once during the deploy and the deploy
does not wait for it, so the site is up about 12 minutes before the corpus is. A failed
sweep is picked up by the daily schedule.

`cdk destroy` removes everything except the chat history table, which is retained on
purpose. A destroy then redeploy collides on that table's name until you rename or remove it.

## 4. From the outputs

### `ChatCreateUserCommand`

*Gives one person an account.*

Self-signup is off, so every account is issued. Copy the printed command, replace the
placeholder username, run it, then run `ChatSetPasswordCommand` for the same username.

**Keep `--permanent` on the password command.** Without it the account sits in
`FORCE_CHANGE_PASSWORD` and managed login answers the redirect with a forced password change
instead of a code.

No email is sent, so hand the person their password yourself. Another person is the same two
commands again.

### `SiteUrl`

*The app.*

Open it, sign in, and ask where the food pantry is. Cards in the answer mean the knowledge
base indexed your pages; a map under them means the location path is wired. Give the first
scrape its 12 minutes before reading an empty answer as a broken deploy.

### `ChatCreateEvalUserCommand`

*The eval harness's account.*

Run once. `eval/run_eval.py` reads the endpoint and client id from the stack outputs; the
password comes from `EVAL_PASSWORD` or `--password-file`. See
[eval-harness.md](eval-harness.md).

### `StreamEdgeUrl` and `StreamProbeFunctionUrl`

*The streaming endpoint, reachable two ways.*

The same routes through CloudFront and direct off the Function URL. Curl both and compare
`time_starttransfer` against `time_total` to see whether the edge buffers the stream. The
direct URL is the control, so leave it in place.

## 5. Okta

Set `okta.metadata_url` and redeploy. That creates the SAML provider, maps the email claim,
and puts the federated button on managed login. No application change, and local accounts
keep working.

**The provider name is not a knob.** A federated user's id derives from it, so renaming it
mints new identities and orphans every conversation the old ones wrote.

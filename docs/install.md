# Navigator install guide

Doc order:

1. [Check you have the prerequisites](#1-before-you-start)
2. [Deploy](#2-deploy)
3. [Create the first user account](#3-create-the-first-user-account)
4. [Give that account a password](#4-give-that-account-a-password)
5. [Sign in and test](#5-sign-in-and-test)
6. [Connect Okta](#6-connect-okta-optional) - optional, and later

## 1. Before you start

**On your computer:** Python 3.13, Node.js 22, the AWS CDK CLI, and Docker running. Docker is
not optional; the build packages the website and the scraper's dependencies inside containers.

**In your AWS account:**

- Credentials that can create IAM, Lambda, S3, DynamoDB, Cognito and CloudFront resources.
- A US region. Both AI models are US inference profiles, so a non-US region will not work.
- `cdk bootstrap`, once per account and region. If someone already ran it, skip it.

**Turn on the three AI models.** In the AWS console, go to Bedrock, then Model access, and
enable these for your region:

| Model | What breaks without it |
| --- | --- |
| `amazon.titan-embed-text-v2:0` | Nothing indexes. Every answer comes back empty. |
| `us.anthropic.claude-sonnet-4-6` | Every question errors. |
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Conversations keep their first message as their name. |

Model access is per region, and approval is usually instant. This is the single most common
reason a fresh install looks broken, so do it before you deploy rather than after.

**You do not need to edit any configuration to deploy.** The defaults are complete. Sign-in
uses accounts you create by hand, and there is no identity provider connected until you connect
one yourself in step 6.

Three values in `config.yaml` are names rather than settings: `vector_store.vector_bucket_name`,
`knowledge_base.name` and `chat_history.table_name`. Change them only if you are installing a
second copy into the same account and region, where the names would collide.

## 2. Deploy

```bash
cd infra
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt

cdk deploy
```

Type `y` when it asks to approve the security changes.

**This can take up to 15 minutes** depending on your connection.

When it finishes it prints a block of outputs. **Leave that terminal open.** You need three of
them in the next steps, and they are different for every install:

```
Outputs:
SjsuNavigatorStack.SiteUrl = https://d1234abcd5678.cloudfront.net
SjsuNavigatorStack.ChatCreateUserCommand = aws cognito-idp admin-create-user --region us-west-2 --user-pool-id us-west-2_AbC123XyZ --username USERNAME-HERE --message-action SUPPRESS
SjsuNavigatorStack.ChatSetPasswordCommand = aws cognito-idp admin-set-user-password --region us-west-2 --user-pool-id us-west-2_AbC123XyZ --username USERNAME-HERE --password 'CHOOSE-A-PASSWORD' --permanent
...
```

**If you lose that terminal,** print the outputs again at any time:

```bash
aws cloudformation describe-stacks \
  --stack-name SjsuNavigatorStack \
  --query "Stacks[0].Outputs" --output table
```

The website is live before its content is. The scraper runs during the deploy and the deploy
does not wait for it, so give it about 12 minutes after the deploy finishes before you judge an
empty answer as a broken install.

## 3. Create the first user account

Nobody can sign themselves up. Every account is created by you, one command per person.

Copy the `ChatCreateUserCommand` line from your outputs and **replace `USERNAME-HERE` with the
username you want.** Everything else stays exactly as printed.

The username is a plain name, not an email address. Pick whatever your campus uses.

**What you copied:**

```bash
aws cognito-idp admin-create-user --region us-west-2 --user-pool-id us-west-2_AbC123XyZ --username USERNAME-HERE --message-action SUPPRESS
```

**What you run,** here creating an account for a person called Jordan Smith:

```bash
aws cognito-idp admin-create-user \
  --region us-west-2 \
  --user-pool-id us-west-2_AbC123XyZ \
  --username jsmith \
  --message-action SUPPRESS
```

Use the region and user pool id from **your** outputs, not the ones above.

**What you should see** is a block of JSON describing the new account, starting like this:

```json
{
    "User": {
        "Username": "jsmith",
        "UserStatus": "FORCE_CHANGE_PASSWORD"
    }
}
```

`FORCE_CHANGE_PASSWORD` is expected at this point. Step 4 clears it.

`--message-action SUPPRESS` means AWS sends no email. That is deliberate: there is no email
address on the account. You give the person their password yourself.

## 4. Give that account a password

Copy the `ChatSetPasswordCommand` line and replace **two** things: `USERNAME-HERE` with the same
username you just created, and `CHOOSE-A-PASSWORD` with a password.

The password must be at least 12 characters and contain an uppercase letter, a lowercase
letter, a number and a symbol. Anything shorter or simpler is rejected.

```bash
aws cognito-idp admin-set-user-password \
  --region us-west-2 \
  --user-pool-id us-west-2_AbC123XyZ \
  --username jsmith \
  --password 'Pick-Your-Own-Pass-99!' \
  --permanent
```

Choose your own password. Do not use the one printed above.

**Keep `--permanent` on the end.** Without it the account stays in `FORCE_CHANGE_PASSWORD`, and
signing in returns a forced password-change screen instead of letting the person through. This
is the most common thing to get wrong in the whole install.

**What you should see** is no output at all. A silent return means it worked.

Confirm it if you want to be sure:

```bash
aws cognito-idp admin-get-user \
  --region us-west-2 \
  --user-pool-id us-west-2_AbC123XyZ \
  --username jsmith \
  --query UserStatus --output text
```

That should print `CONFIRMED`. If it prints `FORCE_CHANGE_PASSWORD`, you left off `--permanent`;
just run the password command again with it.

**Adding more people later** is these same two commands with a different username. There is no
limit and no other setup.

**Someone forgot their password?** Run the step 4 command again with a new one. There is no
self-service reset, because the accounts carry no email address to send one to.

## 5. Sign in and test

Open the `SiteUrl` from your outputs in a browser. It looks like
`https://d1234abcd5678.cloudfront.net`.

1. You land on a sign-in page.
2. Enter the username and password from steps 3 and 4 (`jsmith`).
3. Ask it: **where is the food pantry?**

**What a healthy install looks like:**

- The answer names a real campus place, not a general apology.
- Small source cards appear under the answer. Cards mean the knowledge base indexed your pages.
- A map picture appears under the cards. That means the location lookup is wired up.

**If the answer is empty or says it does not know:** check the clock. The first scrape needs
about 12 minutes after the deploy finishes. Wait it out and ask again before investigating.

**If every question errors:** it is almost always Bedrock model access from step 1. Open the
Bedrock console for your region and confirm all three models are enabled.

**If the sign-in page rejects a password you know is right:** re-run step 4 with `--permanent`.

That is the install. Everything below is optional.

## 6. Connect Okta (optional)

**The stack ships with no identity provider.** Out of the box, the only way in is the accounts
you created in steps 3 and 4.

Connecting your own Okta means people sign in with their existing campus account instead, and
you stop issuing passwords by hand.

**In Okta,** create a SAML app for this site and copy its **metadata URL**. It looks like:

```
https://your-org.okta.com/app/exk1a2b3c4d5e6f7g8h9/sso/saml/metadata
```

Okta needs to know where to send people back to. Both values come from your stack outputs:
the sign-in domain is `ChatLoginDomain`, and the reply address Okta wants is that domain with
`/saml2/idpresponse` on the end.

**In `config.yaml`,** paste the metadata URL into the `okta` block:

```yaml
okta:
  metadata_url: "https://your-org.okta.com/app/exk1a2b3c4d5e6f7g8h9/sso/saml/metadata"
  email_attribute: email
```

It must start with `https://`. The deploy refuses a plain `http://` URL, because that document
carries the certificate every sign-in is checked against.

`email_attribute` is what your Okta calls the email claim. Leave it as `email` unless your Okta
administrator tells you otherwise; some orgs use a long claim name like
`http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress`.

**Then redeploy:**

```bash
cd infra
source .venv/bin/activate
cdk deploy
```

An Okta button appears on the sign-in page. **The accounts you made by hand keep working**, so
you can federate without locking anyone out on the day.

**To disconnect it again,** set `metadata_url` back to `""` and redeploy.

**Do not rename the provider.** It is fixed at `Okta` in code and is not a setting. A person's
identity is derived from that name, so changing it creates brand new users and orphans every
conversation the old ones saved.

**One thing to know when you redeploy into a new account:** the sign-in domain contains an id
unique to each install, so a new install has a new reply address. If you reuse an Okta app
across installs, update the reply address in Okta to match, or sign-in will fail after the
redirect.

## Other outputs

| Output | What it is |
| --- | --- |
| `SiteUrl` | The app. This is the link you give people. |
| `ChatCreateUserCommand` | Creates one account. Step 3. |
| `ChatSetPasswordCommand` | Sets that account's password. Step 4. |
| `ChatCreateEvalUserCommand` | Run once, only if you use the eval harness. See [eval-harness.md](eval-harness.md). |
| `ChatLoginDomain` | The sign-in page's own address. You need it for Okta in step 6. |
| `ChatApiUrl` / `ConversationsApiUrl` | The API routes the site calls. Nothing to do with them. |
| `ChatUserPoolId` / `ChatWebClientId` / `ChatEvalClientId` | Identifiers for the sign-in setup. |
| `StreamEdgeUrl` / `StreamProbeFunctionUrl` | The streaming endpoint, reachable through CloudFront and directly. Curl both and compare `time_starttransfer` against `time_total` to see whether the edge is buffering. The direct URL is the control, so leave it in place. |

## Tearing it down

```bash
cd infra
source .venv/bin/activate
cdk destroy
```

This removes everything **except the chat history table**, which is kept on purpose because it
holds the only copy of what students said.

That has one consequence worth knowing before you try it: destroying and then redeploying fails
on the leftover table's name until you rename or delete that table by hand.

Destroying takes 15 to 30 minutes, because of CloudFront.

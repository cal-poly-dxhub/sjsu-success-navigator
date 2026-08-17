# Accounts, auth and chat storage

## Auth

Sign-in is a redirect, not a form.

1. App shows a Sign in button.
2. Button navigates the browser to Cognito managed login.
3. User authenticates there.
4. Cognito redirects back with an authorization code.
5. App exchanges the code for tokens.

- Authorization code flow with PKCE. Public client, no client secret.
- API Gateway JWT authorizer validates the token. Lambda reads `sub` from the claims.
- Two app clients, one pool: human client (redirect), machine client (password auth, eval runner).
- Users are identified by the Cognito `sub`. Immutable. Never email or username.
- Cognito holds identity only. App data lives in DynamoDB.

Okta is attached last: add a SAML or OIDC identity provider to the same pool plus
attribute mapping. No application change.
The redirect flow is built first because federated users cannot sign in through the
SDK, only through these hosted endpoints.

## Storage

Single table. On-demand capacity. Point-in-time recovery on.

| Item | Partition key | Sort key |
|---|---|---|
| Conversation header | `USER#<sub>` | `CONV#<convId>` |
| Message | `USER#<sub>` | `MSG#<convId>#<ulid>` |

Header: `title`, `createdAt`, `lastActivityAt`, `messageCount`.
Message: `role`, `text`, `sources`, `escalation`.

`text` on an assistant message is the reply **as the model wrote it**, tags and all. Not the
rendered halves: the card blocks, the safety tag and - critically - which side of the card
group each piece of prose sat on are all still in the string, so reopening a conversation
re-parses it through the same code that rendered it live rather than reassembling it.

Storing it pre-rendered is what broke: the lead-in and the closing line were glued together
with the cards in a second attribute, and nothing in that shape could say where the card
group belonged. A reply written as lead-in, cards, closing question came back as one bubble
with the cards underneath, and the question the student was being asked was no longer under
the cards it referred to. The safety panel had it worse - its keys were parsed out and
discarded, so a reopened crisis turn had no contacts at all.

`sources` is the ref-to-URL map the reply cited. It is the one thing in a reply the model
could not have written, since it never sees a URL, so `<card ref="2">` has to resolve against
pairs the server recorded during the turn. Re-running the retrieval instead would resolve the
same ref against today's index, which can be a different page from the one the student saw.

`escalation` is the assembled email draft, and it is the one field recorded rather than
re-derived, because it is the one that cannot be: it was addressed from a recipient in deploy
config and the address on the token that turn was sent with.

Re-parsing means a stored reply renders under **today's** rules - today's caps, today's
contact roster. That cuts both ways on purpose: a fixed rendering bug fixes every
conversation already on the table, and a changed card contract re-renders history with it.

`cards` is legacy. Messages written before this carry rendered cards beside prose that has
already been through the parser, and the read path serves those as it always did. There is no
backfill: nothing in an old row is wrong, it is only less than a new row knows.

### Access patterns

| Need | Operation |
|---|---|
| List a user's conversations | Query `USER#<sub>`, sort key `begins_with('CONV#')` |
| Load one conversation in order | Query `USER#<sub>`, sort key `begins_with('MSG#<convId>#')` |
| Append a turn | PutItem, plus atomic ADD on the header's `messageCount` |
| Delete everything for one user | Query the partition, batch delete |

Partitioning on the user is a security property: the Lambda derives the partition key
from the JWT `sub`, so a user cannot address another user's data. No filter to forget.

### Rules

- One item per message. Never a whole conversation in one item: 400 KB item cap, full
  rewrite on every reply, lost-update race between concurrent turns.
- ULID in the sort key, not a timestamp. Lexicographically time-ordered, collision-free.
- `messageCount` is a counter attribute, never a key. It is an aggregate, not an identity.
- Reporting needs (usage counts, staff dashboards) are a separate consumer. Serve them
  with a table export or a stream. They never shape the primary key.
- Timestamps are ISO 8601 UTC strings. `expiresAt` is epoch seconds, TTL's required format.
- Enable TTL on `expiresAt` at table creation, leave the attribute unset until the
  retention policy exists. Items without it never expire, so this costs nothing now and
  avoids a migration later.
- No secondary index in v1. Cross-user time queries need one and are purely additive.

## Turn lifecycle

History is server-authoritative. The client never supplies it.

Client-supplied history is a prompt injection vector, not just a memory bug: a forged
assistant turn lets an attacker establish rules the model then treats as its own prior
commitment.

Per turn:

1. Client POSTs `convId` plus the new message text. Nothing else.
2. Server ignores any history in the payload. Ignores, not sanitises.
3. Server writes the user message. Before the model call, so a disclosure that later
   times out is still on record.
4. Server reads the last N messages, one descending query with limit N. N is a Settings
   value, not a literal.
5. Model call.
6. Server writes the assistant message.

No polling, no sync, no other table access during a turn.
On a new conversation the server mints the `convId`. The client never chooses it.
A forged `convId` returns empty, because the partition still comes from the JWT.

Two projections of the same query. Context read: message text, for the model, with the
assistant's own markup stripped at the one point history becomes model input - handing the
tags back would teach the model to write them where they do not belong, and a `<safety>` tag
copied out of last week's reply is a panel fired by imitation rather than by triage. Display
read: the same text re-parsed into prose, cards and a panel, for the browser. Rendered cards
are never fed back to the model.

The browser still holds what is on screen. What is gone is any client store treated as
truth.

### Reefs

- A failed turn leaves a user message with no assistant reply. The next turn then reads
  history ending in a user role and appends another, which Bedrock Converse rejects. The
  context builder must handle this explicitly.
- The context load needs strongly consistent reads. Two quick turns and an eventually
  consistent read can miss the previous assistant message, silently losing a turn.

## Open

Retention window and read access for identifiable transcripts containing crisis
disclosures. A policy question for SJSU, not a technical choice. Needs an answer before
the table exists.

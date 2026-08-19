# Build plan (v1)

Each bullet is one commit. Gate before it lands: `cdk synth` clean, plus a few
`Template.from_stack` assertions on that section's key wiring. Synth alone is a
weak gate on an L1-heavy stack; the assertions catch wiring drift, not invalid
L1 property values. Nothing pre-flights those without an account.

## Planning

- [ ] pick the sjsu pages to crawl, confirming each scrapes as static html so the gav scraper works on them
- [ ] draft aws arch diagram: diagram-as-code plus generated png, committed in repo

## Copy over and morph

- [x] pull gav config skeleton and synth validators, renamed with no hardcoded global names
- [x] pull gav kb section, repointed at our source bucket and chunking values
- [x] pull gav scraper shell, crawling the curated url list from config on a single daily schedule (no tiers); sidecars carry `section` alongside source_url and title, or cards.py deprioritization and follow-ups degrade silently
- [x] pull gav lambda section: bare handler, not fastapi/mangum; keep pydantic in the deps layer, the camelCase wire contract lives in its aliases (the guardrail rides with it; handler is a validate-only stub until the next two bullets)
- [x] pull camp agent loop, tool schemas, system prompt and services as files; main.py and the routers are replaced (the loop also carries a wall-clock deadline, not just the iteration cap)
- [x] pull camp card parsing and the pre-model safety intercept as-is
- [x] ~~map crawl-list sections to card presets (app/section_presets.py, explicit entry per section, no fallthrough); an unretrieved sourceUrl loses its link~~
      SUPERSEDED by the card tag contract below. section_presets.py is deleted: the
      follow-up prompt is authored per card by the model, so a table keyed on
      `section` no longer has anything to answer. `section` still reaches the
      metadata sidecars and is still required of the crawl list - it is read at
      retrieval time - it just no longer picks button text.
- [x] pull gav api gateway: cognito gate on the billable route, our origins, our throttle numbers, route to /chat, Authorization in allow_headers (plus reserved concurrency: rate bounds invocations started, not how many run at once)
- [x] pull gav frontend s3 + cloudfront, stamping config.json with the api url; site domain joins the api cors allowlist as a deploy token (site content is a placeholder until bullet 10; camp's app is MULTI-PAGE, so directory-index rewriting, not an SPA fallback)
- [x] bundle astro in a container at synth (minimal placeholder app; dist/ never committed)
- [x] pull camp frontend ui as astro dist/, mock sidebar fixtures deleted; gav-style InitiateAuth with the token in memory, expiry checked before the fetch because an authorizer 401 carries no cors headers (/login and /auth/callback removed: both existed only for the Hosted UI OAuth redirect)
- [x] strip gav-specific surface: primo tools, catalog bucket, feedback path, dual
      hosting. Nothing to remove - none of the four were ever ported, because each
      section was pulled by hand rather than copied wholesale. Now pinned by
      test_no_gav_specific_resources_were_inherited so a later pull cannot
      reintroduce them, with the deliberately-inherited pieces (PROMPT_ATTACK
      guardrail, Cognito gate, config validators, seed-list layer) pinned alongside.

The api gateway section reopens when the frontend lands, to take the
distribution domain into its cors allowlist. Not frozen after its commit.

## Build ourselves

- [x] cards are parsed from model-emitted tags, not cut out of prose (docs/cards-v2.md).
      The model writes one text reply - prose plus `<card ref="N">` blocks - and cites
      sources by a per-turn integer id it was handed; the server resolves the id to a
      URL. `submit_chat_response` is gone, and so is the mechanical chunk-to-card
      builder that ran on every timeout. Caps live in config.yaml `cards` and reach both
      the parser and the prompt. BREAKING: the wire contract is unchanged, but every
      card's text now comes from the model, so a deploy of this without the matching
      prompt produces zero cards rather than wrong ones.
- [x] the cards carry the answer and the prose introduces them (docs/cards-v2.md).
      cards.desc_max_chars 140 -> 300, and the system prompt says where the substance
      goes: anything that sends a student somewhere, or tells them about a source we
      ingested, is a card with a real description, and the prose is a two-or-three-line
      intro pointing below. The cap stopped being a layout measurement when the
      four-line clamp went - it is an editorial budget now, still one config value
      reaching both the parser and the prompt. Whether the model actually shifts its
      weight is a question for real answers, not the unit suite.
- [x] card presentation: variable-height cards in a responsive grid under the prose,
      dealt in off a deck. The reveal button, the one-at-a-time stack and the 4-line
      clamp are gone, and the prose is never replaced (docs/cards-v2.md, Presentation).
      This is the rework the tag-contract commit deliberately deferred, and the clamp
      it removes is the box the entry above stopped deriving desc_max_chars from.
- [x] one reading rhythm across the turn: the deal is slower (0.1s -> 0.34s between
      cards, so four land in ~1.46s rather than 0.74s), card body text is 1rem rather
      than 0.875rem, and cards.desc_max_chars 300 -> 180 with the prompt re-steered to
      two sentences - the destination plus the one specific that matters. The three move
      together: a larger body at the old cap is a paragraph in a box, and dropping the
      cap without rewriting the prompt's examples truncates cards instead of shortening
      them. Still no clamp, no fixed height, no minimum height, and reduced motion still
      presents the grid directly.
- [x] a follow-up click is an ordinary user turn, so it can carry cards (docs/cards-v2.md,
      Tell me more). The card-suppression note is out of the user message and the matching
      guidance is out of the system prompt, including "do not repeat cards the student
      already has" - unenforceable, since history carries prose only, and it degraded into
      blanket avoidance. Retrieval guidance now turns on whether the answer needs a source
      rather than on the turn's position. `followup` stays on the wire contract with no
      backend reader.
- [x] cards size to their content instead of truncating (docs/cards-v2.md, Length caps).
      Real-model testing put an ellipsis on nearly every card: output length is a
      distribution, and a cap sized AT the two-sentence steer (180) turned ordinary
      variance into daily truncation. The per-field caps become runaway guards far above
      normal output - desc 180 -> 600, title 60 -> 90, follow-up 120 unchanged - each hit
      logged at WARNING, so an ellipsis now means a bug. Length steering stays in the
      prompt. An over-cap follow-up keeps its button and is sent whole (it displays as
      the student's turn and wraps there). A card past ~280 body chars spans its whole
      grid row so a short neighbour never floats on its dead space.
- [x] crawl list covers every office in the sponsor reference sheets (2026-08-10 coverage
      audit): +25 pages to 228. Campus safety (UPD, BIT, Alert SJSU, the Title IX landing page),
      five identity centers, ASPIRE/McNair/Office of Research, study abroad, supported
      instruction, IT, parking, the AS services, wellbeing, off-campus housing, the H&A success
      center (the advising hub lists 7 college centers, not the sheets' 9), and the
      enrollment-management directory. Four new sections: campus-safety, research, study-abroad,
      campus-services; nothing in app code switches on section values. Deliberately excluded:
      spartanrecreation.com, events.sjsu.edu, sjsuspartans.com, one.sjsu.edu (likely JS apps,
      off the three-host set) and the library CAT page (404 at every candidate URL). The sheets
      themselves carry errors - a dead CAPS intake URL, a dead /pridecenter link, conflicting
      CAPS phone / SJSU Cares location / pantry hours between sheets - which matters when they
      become eval ground truth.
- [x] scraper extraction recovers what trafilatura's article model drops (2026-08-10 audit of
      the LIVE corpus: 39 of 203 ingested docs carried any phone number, 52 any email; EOP's and
      AEC's contact info appeared in none). Both causes are static HTML - the JS hypothesis was
      tested and is false, so no browser automation. (1) The www.sjsu.edu CMS puts each office's
      phone/email/hours in a role="complementary" band OUTSIDE main, which an article extractor
      rightly reads as chrome; even contact-us.php pages keep their facts there. (2) Landing-page
      link-tile grids are pruned as link-dense boilerplate, and trafilatura's favor_recall /
      include_links measurably do not bring them back. Fix: a second lxml pass appends the band
      and any content-region block missing from the body, deduplicated on letters-and-digits
      normalization; <br> tails become spaces so "Phone: X<br>Monday" cannot glue into a
      corrupted fact. Verified against all 228 live pages: 0 failures, phones 39/203 -> 192/228,
      emails 52/203 -> 192/228. Every fingerprint changes, so the next deploy re-uploads and
      re-ingests the whole corpus - deliberate; the corpus was the bug.
- [x] ground-truth QA baseline (eval/ground-truth.yaml): 82 pairs known correct
      independently of the program - sponsor sheets first, every load-bearing fact verified
      against the live public pages on 2026-08-10, sheet-vs-sheet conflicts resolved by the
      live site and the sheets' errors recorded in the file header. Covers routing, factual,
      process and disambiguation questions plus safety-intercept cases (the safety panel
      must appear; a fluent reply without it is a FAIL) and five honest-gap probes for
      content deliberately outside the corpus. This is the fixture set the harness below
      consumes.
- [x] safety is the model's triage call, resolved server-side (2026-08-10). The pre-model
      phrase gate is gone: the system prompt carries the emergency instruction and a keyed
      resource roster (built from app/safety.py's table, the same table the resolver reads,
      so a taught key always resolves), the model emits <safety>key, key</safety>, and the
      server turns keys into the fixed contact panel - label, number and link are
      table-authored, never model-authored, the card-ref construction applied to crisis
      contacts. Unknown keys drop at WARNING; nothing valid resolves to the default crisis
      set; prose citing a hotline without the tag still gets the panel. Emergencies only:
      SJSU Cares and other office processes are ordinary routing answers, and the four
      ground-truth pairs that assumed intercepts for housing/family/intake are reclassified
      rag-answer. The roster adds SAS and UPD, so a survivor disclosure can resolve to the
      confidential advocate instead of a generic crisis set.
- [x] cards render in the model's emitted position (2026-08-10, docs/cards-v2.md, Where the
      cards sit in the reply). The reply is split once, at the end of the last card block:
      prose before it renders above the grid, prose after it below, so a closing question stops
      landing over the answer it asks about. One split point, not a general block list - a turn
      makes exactly one card group, so the only position there is to keep is which side of it
      each piece of prose was on. `trailingText` joins the wire contract and is the only shape
      change; it is null on a safety turn, on the zero-card fallback and on the ordinary reply
      that ends with its cards, because each of those has no group left to split around. The
      panel's placement is unchanged by construction: attaching it collapses the reply back into
      one bubble. The trailing bubble waits out the deal before it types, which keeps the
      entrance transform-only.
- [x] the prompt orders the reply: lead-in, cards, questions (2026-08-10, docs/system-prompt.md).
      The parser above made position meaningful, so the prompt states where each part goes and
      the examples model it: the first ends with a question under its cards, the second ends on
      its cards, because a closing question in every example teaches a habit where the rule
      offers an option. Everything else is unchanged, prose-is-never-empty included, and the caps
      are still interpolated from Settings rather than typed.
- [x] the model sees whole retrieved chunks (2026-08-10 eval rerun, 60/14/8 of 82).
      cards.py sliced every retrieval result to 500 chars while real chunks run 800-3200
      and the recovered contact band sits at the document tail, so the model cited the
      right page while honestly reporting it could not see the number retrieval had
      already fetched: Bursar's line was in six of the eight retrieved chunks, every
      occurrence past the cut; CAPS's at char 612 of the top hit. Nine of the run's
      fourteen fails were this shape. The chunk is bounded upstream by the chunking
      config, so a second blind cap in the tool-result layer had no job to do; the
      slice and its constant are gone, not resized.
- [x] retrieval primed on every turn, the tool as the escape hatch (docs/cards-v2.md,
      Retrieval contract), plus the prompt pass the eval demanded. The first search runs
      server-side on the student's message and lands as a completed tool exchange; the
      model answers in one Converse call in the common case and searches again only when
      the results miss but the corpus plausibly has it. Evidence-driven: of eval turns
      that logged, 20/30 made exactly one search, 2 made two, and the single wrong SKIP
      was a scored fail - retrieval need is a property of the product, not a per-turn
      decision. The prompt rewrite fixes what the transcript showed: examples now mark
      annotations as [bracketed] stage directions (the bare "Results:" format taught the
      model to narrate its routing decisions into prose, five answers), third-party worry
      routes to BIT with cards instead of tripping the panel, asked-for facts go in the
      card outright, and a result's silence is never asserted as "no rule exists" (the
      pantry-eligibility fail). Priming degrades honestly: past-deadline skips it,
      retrieval failure logs and falls back to the model searching itself.
- [x] every page introduces itself (2026-08-10 eval rerun): the scraper leads each
      document with the title as an H1 and assembles the contact band FIRST, body and
      link tiles after. Appended, the band put every office's phone in the tail chunk
      with often nothing naming the office - the AEC probe found its contact chunk
      unrankable - and Bedrock embeds only chunk text, never the metadata sidecar.
      Dedup precedence is unchanged, so a band block the body already carries stays in
      the body. Every fingerprint changes; the next deploy re-uploads and re-ingests
      the corpus, the same deliberate move as the extraction fix.
- [x] the chat history table exists before anything writes to it (docs/accounts-and-storage.md,
      Storage). First of five slices building per-user accounts and chat history, and
      INFRASTRUCTURE ONLY on purpose: one DynamoDB table (pk `USER#<sub>`, sk `CONV#`/`MSG#`
      prefixed), on-demand, PITR on, no secondary index, plus the chat Lambda's grant and the
      table name in its environment. No application code reads or writes it yet, so the slice
      that does is a pure app diff with no CDK tangled into it. TTL is enabled on `expiresAt`
      and NOTHING WRITES THAT ATTRIBUTE: the retention window for identifiable transcripts is
      an open question with the university (below), items without the attribute never expire,
      so enabling it now costs nothing and avoids a table-level change later. The grant is
      hand-rolled rather than `grant_read_write_data` for one action - that helper's read set
      includes `dynamodb:Scan`, the only operation that takes no partition key, which is the
      hole in the isolation the single-table design was chosen for. RETAIN on destroy, the one
      place this stack breaks its own one-click-uninstall rule: the table is the only copy of
      what students said, and the cost is a fixed global name that collides loudly on a
      reinstall. Two standing test assertions were NARROWED, not dropped - "no DynamoDB table
      at all" became "exactly one, and it is this one", and the chat role's blanket "no
      dynamodb:" ban became a scoped-to-this-ARN, no-Scan, no-table-management assertion.
- [x] per-user Cognito accounts behind managed login, shared credential retired in the same
      commit (docs/accounts-and-storage.md, Auth). Sign-in is a REDIRECT - authorization code
      with PKCE, public client - not the form the shared login had. Forced by federation, not
      preferred: SJSU's IdP lands in this same pool later, and a federated user cannot
      authenticate through InitiateAuth or any SDK call, only through the hosted endpoints, so
      a form written now would be deleted on the day Okta arrives rather than extended. Okta
      then costs an identity provider plus attribute mapping and no application change.
      TWO APP CLIENTS on one pool, because a client is the unit Cognito applies auth flows to:
      the web client has NO sign-in flow of its own (ExplicitAuthFlows is exactly
      ALLOW_REFRESH_TOKEN_AUTH - left absent, Cognito falls back to legacy defaults that
      include SRP, which is the trap the assertions pin), and a machine client keeps password
      auth for the headless eval runner, which now signs in as `eval-runner` against
      ChatEvalClientId. Both client ids are in the authorizer's audience or one caller 401s
      with no CORS headers. No custom pool attributes - a federated profile refreshes from
      provider claims, so app data belongs in DynamoDB keyed by `sub`. Self-signup stays off;
      accounts are issued per person. Callback and logout URLs come from the same
      cors.allow_origins list the API uses, with the distribution appended at deploy as a
      token; the frontend derives redirect_uri from window.location.origin so one config.json
      is correct on localhost and on CloudFront. Storage and the server-authoritative turn
      lifecycle in that doc are NOT in this commit.
- [x] the chat turn is server-authoritative (docs/accounts-and-storage.md, Turn lifecycle).
      Second of the account slices, and pure application code against the table the last one
      built. THIS IS A PROMPT INJECTION FIX, not a memory feature: client-supplied history
      lets an attacker forge an assistant turn and so establish a rule the model then treats
      as its own prior commitment, which is a different order of problem in an app receiving
      crisis disclosures. `history` is off ChatRequest entirely rather than validated, so a
      posted transcript is an unknown key pydantic drops - ignored, not sanitised, the doc's
      word - and no field is left for a later latency optimisation to fill in. The user id is
      off the wire for the same reason turned up a level: `sub` comes from the JWT claims the
      authorizer already validated, a body field would be the same value with nothing behind
      it, and a request carrying no claim is a 401 rather than an unattributable billable
      Bedrock call. Per turn: write the student's message (BEFORE the model call, so a
      disclosure that then times out is still on record), read the previous
      MAX_HISTORY_MESSAGES back in ONE descending, strongly consistent query that excludes
      the write it just made, call the model, write the reply. The server mints the
      conversation id and returns it; a forged one reads as empty because the partition
      comes from the JWT. Both of the doc's reefs are handled: the strongly consistent read
      is what stops two quick turns from silently losing one, and this turn's message is
      appended BEFORE the consecutive-role merge, so a failed turn's dangling user message
      folds into the next one instead of making Converse reject every turn after it in that
      conversation. Nothing is written on a guardrail block - storing it would smuggle the
      attack text past the screen into the next turn's context - and a DynamoDB failure logs
      at ERROR without denying the student an answer, the same posture as a guardrail
      outage. The header is updated alongside each message write (an atomic ADD on
      `messageCount`, the Storage section's "append a turn"), which is the one thing here
      the Turn lifecycle section's "no other table access" line does not literally cover:
      without it a conversation is invisible to the list access pattern the same doc
      defines, and every conversation created before a list endpoint would need a backfill.
      No read endpoints, so the display projection - stored cards with URLs already
      resolved - is written and not yet read.
- [x] a student can see their own previous conversations and open one
      (docs/accounts-and-storage.md, Storage access patterns). Third of the account slices,
      and the first one a student can see. It OPENS BY FIXING WHAT THE LAST ONE LEFT: the
      server had started minting and returning a `conversationId` and the frontend was still
      posting its own transcript and no id, so every browser turn opened a fresh conversation
      and the model saw an empty history - the server was authoritative and the client was
      not talking to it. The client now sends back the id it was given and nothing else; the
      `history` array and the `sessionId` are gone from the request body, and with them
      `historyFromTurns`. TWO ENDPOINTS, both GET, both on the same Lambda and the same JWT
      authorizer as /chat: `/conversations` lists the caller's headers newest-active-first,
      `/conversations/{conversationId}` returns one conversation in the DISPLAY projection -
      role, text, and the stored cards with their URLs already resolved, which is exactly
      what the context read refuses to fetch. Two projections of the same stored turns, and
      they are separate METHODS on the store rather than one method with a flag, so a caller
      cannot hand the model a rendered card by passing the wrong argument. The reads are
      gated even though they spend no Bedrock tokens, because the `sub` claim IS the
      partition key: an ungated read has nobody to attribute, and the only alternative would
      be a user id off the wire. A forged or foreign id is a 200 with an empty list, not a
      404 (which would confirm to a prober which ids exist somewhere) and not a 403 (which
      would imply an owner check that could be got wrong) - the partition it addresses is
      the caller's own, so there is nothing to authorize. A malformed one is a 400, the same
      validation POST /chat already applies, because the id goes straight into a sort-key
      prefix. Both reads are STRONGLY CONSISTENT for the case the feature exists to serve: a
      student sends a turn and reloads. On the client, `ChatSession` stops holding a
      ChatResponse rebuilt from the turns on screen - a client-side store standing in for
      the record, and a LOSSY one, since a response carries the prose of its last turn only,
      so every earlier turn came back with the student's own question in place of the
      answer. It holds turns fetched from the server, `undefined` until they are. The
      sidebar's "Chat history is mocked for this preview" note is gone because it is no
      longer true.
- [x] the cost panel prices the conversation on screen, from tokens the server counted
      (app/usage.py). /chat now reports what one turn billed - Converse calls with their input
      and output tokens, the guardrail's own reported text units, retrievals - and the panel's
      left half meters the live conversation off it. It used to show the 24-question sample
      average under the heading "one student message": a figure about a sample, sitting where a
      reader takes it as a figure about their chat. ONE TALLY PER REQUEST, opened before the
      guardrail screen and mutated in place all the way down, which is what makes the awkward
      paths count - a turn that exits on the wall-clock deadline still reports the calls it was
      billed for, a blocked turn still reports the screen that blocked it, and the small titling
      call on a new conversation is in there too. The meter is PER CONVERSATION and IS NOT
      STORED: it accrues in the tab from each reply, so a chat reopened from history reads
      "nothing metered in this chat yet" rather than presenting a zero as a measurement (storing
      it would put a cost attribute on the message items, which docs/accounts-and-storage.md
      fixes at three). The right half is now the slider, the monthly total it produces, and the
      two numbers that total is made of - the zero-use floor and the per-message price. The
      itemized rows, the depth note and the rates table came off: an audit of the arithmetic in
      the middle of a demo, and the arithmetic is in costModel.ts where it can be read properly.
- [x] a per-user daily message cap, which is the first control that bounds what ONE account
      spends (config.yaml `rate_limit`, app/ratelimit.py). Everything fencing cost before this
      bounded the SERVICE - the stage throttle bounds invocations started per second, reserved
      concurrency bounds invocations running at once, the loop's deadline and iteration cap
      bound a single request - and not one of them can tell two students apart, so a signed-in
      account could sit inside all four all day. At ~$0.026 a question, 60 a day is ~$1.56 per
      person. Counted against the Cognito `sub` from the validated JWT, in the chat-history
      table's own user partition under a third sort-key prefix (`RATE#DAY#<date>`), expired by
      the TTL attribute the table has had enabled since it was created - so this is the first
      thing in the app to write `expiresAt`, and it adds NO AWS resource and no new IAM grant.
      ONE ATOMIC CONDITIONAL WRITE per turn (`ADD #count :one` under `count < :limit`), no read
      first: a read-then-write loses the race that matters, since two turns in flight would
      both see the same count and both spend a model call on it. ATTEMPTS, NOT ANSWERS, and
      BEFORE the guardrail - a refused turn costs one DynamoDB write and nothing billable, not
      even a guardrail text unit, which is the only ordering that makes it a guard rather than
      a report. Over the limit is a 429 carrying the reset INSTANT, which the browser renders
      in the student's own clock; the server's own sentence says "midnight UTC" for callers
      with no timezone. A FIXED UTC CALENDAR DAY rather than a sliding window (a sliding window
      needs a read and its precision buys nothing here) and rather than a campus-local midnight
      (which needs a timezone database in the function, for a boundary the student never reads
      anyway). The eval harness is EXEMPT, keyed on the validated `client_id` claim rather than
      a username or a higher number: it fires all 82 ground-truth questions as one account at
      concurrency 3, its machine client is one of exactly two in the authorizer's audience, and
      a number would have to be kept above whatever ground-truth.yaml grows to. FAILS OPEN on a
      DynamoDB fault that is not a condition failure, logged at ERROR - the same posture the
      guardrail screen and the history writes already take, because the service-wide fences are
      still up and a blip must not become an outage of a product that receives disclosures. The
      gate is the cost panel's: absent or 0 omits the environment variable entirely, so "off"
      has one spelling.
- [x] Okta federates into the chat user pool, behind one config key
      (docs/accounts-and-storage.md, Auth: "Okta is attached last"). A Cognito SAML identity
      provider created ONLY when `okta.metadata_url` is set - absent or empty and no provider
      is synthesized at all, the human client offers COGNITO alone, and the local accounts an
      administrator issues keep working. THE ABSENCE IS THE GATE, the shape the cost panel
      already uses, and one key covers all three settings: local-only, a rehearsal Okta org,
      SJSU's tenant. THE PROVIDER NAME IS `Okta` AND IS NOT A KNOB (a constant in
      infra/infra/config.py, and a config key that looks like it might reach it is ignored): a
      federated user's Cognito username is `<providerName>_<nameid>`, so a rename mints new
      `sub` values - the DynamoDB partition key - and orphans every conversation the old
      identities wrote, with no update path, since ProviderName is the resource's physical id.
      It names the provider's ROLE, never an org, which is what lets the rehearsal tenant and
      SJSU's share it. A METADATA URL rather than an uploaded file, so Cognito re-fetches it
      and a certificate rotation on the Okta side is not an outage; https enforced at synth
      because Cognito trusts the signing certificate inside that document. ONE attribute
      mapping - the Okta-side name from config (orgs spell it differently; defaults to
      `email`) onto this pool's own `email` - and no username mapping, which Cognito rejects
      anyway because it takes the username from NameID. IdP-initiated sign-in is OFF
      explicitly: an unsolicited assertion is bound to no request this app issued. THE
      FINDING WORTH KEEPING: CDK fills an omitted `SupportedIdentityProviders` from every
      provider registered on the POOL, so creating the provider silently attached Okta to the
      machine client too - both clients now pin the property, and the human client carries an
      explicit DependsOn because it names the provider as a literal string rather than a Ref.
      No frontend change: managed login renders the federated button itself, and
      `beginSignIn` pins no `identity_provider` parameter (checked, unchanged).
- [x] fifteen languages of chrome, and Sammy answers in the student's own language
      (frontend/src/lib/i18n.ts, app/prompts.py). TWO MECHANISMS, deliberately kept apart.
      The CHROME is five more catalogues at full key parity - French, Brazilian Portuguese,
      Russian, Traditional Chinese, Thai - chosen for the scripts and regions the first ten
      missed, because the picker read as regionally narrow in a demo; the first ten stay in
      population order and the five sit after them, because that is what they are. Every file
      is typed as `Strings`, so a missing key is a build error rather than an `undefined`
      rendered at a student (verified by deleting one: TS2741). Still NO right-to-left entry,
      and that is now also why the breadth pass reached for Thai: Arabic, Farsi and Urdu need
      a mirrored layout, and a catalogue without it is the cheap half of the job and a broken
      page. The REPLY is a system prompt section: answer in the language of the latest
      message, follow a mid-conversation switch, and THE CARDS ARE STATED SEPARATELY from the
      prose because that is where a model drops the instruction and where the answer lives -
      a lead-in in Spanish over English cards has answered in English. The carve-outs are the
      load-bearing half: phone numbers, emails, URLs and office names copied character for
      character, and the tag names, ref ids and <safety> keys copied because the SERVER reads
      them - a translated key resolves to nothing, which app/safety.py drops at WARNING, and
      a dropped key is a crisis panel lost to a translation. The panel's contents were never
      at risk either way by construction: the model writes keys, the table writes contacts.
      The escalation draft stays English because its reader is staff rather than the student.
      The last hardcoded English chrome went with it - the safety panel's accessible name,
      the card group's, and the relative timestamp inside that label, which was English in
      all ten languages and is why lib/formatTimestamp.ts now takes the strings and the
      locale (its date branch followed the BROWSER's locale, not the picker). "Sammy's
      answers are not translated yet" is deleted from all fifteen catalogues because it
      stopped being true, and a stale caveat tells a student not to bother asking in Spanish.
      Two eval pairs added and NOT RUN (needs account): a Spanish and a Vietnamese question
      over facts already verified elsewhere in ground-truth.yaml, so a failure there is a
      language failure and never a retrieval one.
- [x] the wait after the prose says what it is, and a landed card stops moving on its own
      (2026-08-12, docs/cards-v2.md, Presentation). TWO FAULTS IN THE SAME SECONDS OF A
      STREAMED TURN. (1) The preview stops at the first tag, so the reply appeared to end and
      the student had nothing to tell them the cards were still being written; the sink now
      pushes the prose tail and one `status` frame the instant `<card` appears in the model's
      own output (`cards.card_block_started`, `preview.CARDS_STAGE`), and the pending
      exchange shows a small indicator until the finished turn replaces it. The signal is the
      reply, not a clock: no card block, no frame, no indicator - which is the prose-only
      reply, roughly one in ten. A `<safety>` anywhere takes it back to no, because that turn
      drops its cards by contract. (2) The card that "jerked backward" was not a re-fired
      entrance and not a remount: the deal ends by restoring pointer events to the group, and
      the `:hover` lift then landed on whichever card had stopped under a mouse nobody moved,
      pulling it ~6px back up the path it had just flown. Measured in Chrome over CDP - the
      card under the pointer moved 5.75px, its three neighbours moved 0.00px, and the
      transform it settled on was `translateY(-4px) scale(1.012)`. Hover is now armed by the
      first pointer movement after the group settles; after the fix, same pointer, 0.00px on
      all four, and an ordinary hover a moment later still lifts. Ten catalogues gain
      `stageComposingCards`.
- [x] the deck is a real thing, and it is dealt from the bottom (2026-08-12, docs/cards-v2.md,
      Presentation). THE ENTRANCE ITSELF WAS THE COMPLAINT, and the measurement bore it out:
      every card leaves the same deck for its own slot, so the four travel 21/218/514/589px -
      but every card got the same 0.44s, which is the same thing as giving each a different
      SPEED, 270/2316/5151/6878 px/s rising with the index. The deal accelerated as it went and
      the last card was a blur. Flights are sized by distance now (1150px/s, floored at 0.46s,
      capped at 0.62s) on a cubic ease rather than the quintic that put 70% of the distance in
      the first quarter. Buffered turns always read better here for a reason worth recording:
      the prose types straight into the deal, where a streamed turn puts dead air between them,
      so the burst lands cold. That air is now the deck - shown face down and shuffling from the
      moment `<card` appears, then dealt from the BOTTOM, each card turning over as it flies,
      with the top card flipping in place because its slot is the deck's own position. THE DECK
      WAITS rather than shuffles - four card objects on a 198px deck at a 7px step, one beat
      each: a 1140ms 9px nudge out of the bottom of the stack and back, leaning 2.1deg from the
      card's top edge, then 860ms in which nothing moves, then the card above. It works upward
      through every card and comes round again. No card changes slot, so no depth ever swaps -
      which is what let the motion shrink to this, since a card changing depth mid-move needs a
      dip clear of the whole stack to hide the swap. THE DECK REPORTS ITS OWN STATE
      (lib/waitingDeck.ts) instead of the caller deriving it from keyframe percentages - two
      versions did that and both drifted out of step with the animation they described, which
      IS the snap.
      THE STACK IS ALWAYS FOUR AND THE DEAL IS NOT, which was the last thing making the
      hand-off read as two objects rather than one: nobody knows how many cards are coming
      until the payload lands, so the deck waits at four and then a group of one came out of
      it. The reply now hands the count back (`settleAndCompress`) and the deck SHEDS THE
      SURPLUS before the swap - the extra cards ripple up bottom-first and tuck in under the
      top one, 260ms each on the same 2.1deg lean, 90ms apart so the moves overlap into one
      gesture, then a 130ms settle. One card sheds three, two sheds two, three sheds one, four
      sheds none. After it, the stack's geometry IS the real deck's opening pose, so the swap
      is a continuation rather than a replacement. Measured at one card: deck square at
      y=[0,7,14,21], surplus away by 367ms, all three at y=0 behind the top card.
      THE 190ms SETTLE THAT USED TO FOLLOW THE SWAP IS GONE. A pause reads as a breath when it
      happens to something that has just moved and as a stall when it happens to something that
      has just appeared - held on the new deck it was dead air, so it moved to the end of the
      compress, on the deck that is already on screen. The deal now begins on the frame the
      real group mounts. Measured after: hand-off shift 0.15px in document coordinates, 0
      dropped frames, 342ms between cards, group down in 1.68s.
      THE CARD BACKS ARE SKELETONS, not blanks - a blue title bar, two grey description lines
      and the two buttons in their own colours, flat fills at the real card's radius and
      padding, so a card in flight says what kind of thing it is about to be. EVERY dealing
      card carries one; only the top of the waiting stack is ever visible, but each card is in
      full view the moment it leaves. The two faces are separated by 1px of `translateZ`
      because coplanar children of a `preserve-3d` element cannot be depth-sorted and fall back
      to paint order, which showed the card's real text through its own back, MIRRORED, for the
      whole flight - `backface-visibility` alone never got a say.
      EVERY NUMBER ABOVE LIVES IN lib/deckTuning.ts, one object, read at use rather than
      captured at import, so a local harness can drive them from sliders. Nothing in the app
      writes to it and the shipped build runs the defaults exactly as it would `const`s.
- [x] a reopened conversation is the same turn that was sent, not a rebuild of one
      (app/history.py, app/orchestrator.py replay_stored_reply). THE RECORD IS NOW THE
      MODEL'S OWN REPLY, tags and all, plus the ref-to-URL pairs its cards cited; `cards` is
      no longer written. It used to be stored pre-rendered - the lead-in and the closing line
      glued together with the cards in a second attribute - and nothing in that shape could
      say WHICH SIDE of the card group a piece of prose sat on, so a three-part reply came
      back as one bubble with the cards below it and the closing question no longer under the
      cards it was asking about. DIAGNOSED FIRST, against the live table: all 74 message rows
      present, every conversation strictly alternating, the log accounting closing exactly
      (64 turns ever completed, 27 in deleted conversations, 37 assistant rows stored), and
      the deployed read path returning every one of them - so this was never a lost write or
      a dropped read, which is what ruled out the three cheaper fixes. THE SAFETY PANEL IS
      THE REASON THIS WAS WORTH A SCHEMA CHANGE rather than one more stored field: its keys
      were parsed out at write time and discarded, so a reopened crisis turn came back as
      prose with no contacts, and no amount of adding fields to the old shape reaches
      something that was never recorded. Now the keys are still in the text and app/safety.py
      resolves them again on the way out. The draft stays RECORDED rather than re-derived,
      the one carve-out, because it was addressed from deploy config and the token that turn
      was sent with. Tags come off at the one point history becomes model input, so the model
      is never handed back its own markup - a `<safety>` tag copied out of last week's reply
      would be a panel fired by imitation. No backfill: a row carrying `cards` is served the
      way it always was, and all 19 of them on the live table were replayed through the new
      read path to prove it.
- [x] every SJSU fact lives in one repo-root `data/` directory, loaded by both languages
      (2026-08-19, data/README.md). THE DUPLICATION WAS ALREADY LOAD-BEARING: the SJSU Cares
      address was written out in app/places.py and again in frontend/src/lib/sjsuCares.ts, and
      its mailbox in config.yaml and again in that same file - so the office moving would have
      put the map card and the "Talk to a person" panel in disagreement inside one app, with
      every test green, because a fact spelled twice in two languages has no test that can see
      both copies. Six CSVs now: urls.csv (moved from url-list.csv, shape unchanged),
      places.csv, buildings.csv, contacts.csv (safety, cares and escalation rows, told apart by
      a `kind` column) and abbreviations.csv, plus the README, which is the deliverable - it is
      written for somebody with a spreadsheet and no Python. Python reads them AT IMPORT
      (app/campus_data.py); the browser never reads them at all - frontend/scripts/
      generate-campus-data.mjs compiles the rows the site shows into a TypeScript module before
      every `npm run build` and `npm run dev`, and that module is gitignored, so a stale or
      hand-edited copy is not a state the repo can be in. The prompt's place roster, its safety
      roster AND its campus-shorthand glossary are now all generated from the tables that
      resolve them. `escalation.recipient` became `escalation.contact`, an id naming a row,
      because an address in config.yaml IS the second copy - the gate is unchanged (blank is
      still off) and the stamped ESCALATION_RECIPIENT is byte-identical.
      EVERY LOADER RAISES, and two of the checks came from damaging the real files rather than
      from reasoning about them: a row with MORE cells than the header (a decimal comma in
      buildings.csv shifted every later cell and loaded a building off the coast of Africa) and
      one with FEWER (contacts.csv cut off mid-row loaded with three crisis contacts missing).
      Every cell either reader looked at was well formed in both cases; the shape was the only
      tell, and both readers now check it. data/ is outside app/ and outside frontend/, so it
      reaches the deployment twice: the ScraperSeedListLayer becomes CampusDataLayer carrying
      the whole directory to the scraper AND the two functions that run the agent loop, and the
      Astro build container gets it as a read-only second mount. Verified byte-identical: the
      built system prompt (both the configured and the no-escalation form) and every safety
      panel the resolver can produce, before and after.
- [x] the reply streams over HTTP and the WebSocket transport is gone (docs/chat-service.md,
      Streaming). The browser POSTs a turn to `/api/chat` on its own origin - a behaviour on
      the distribution that served the bundle, so no second hostname and no CORS story - and
      reads NDJSON frames off a `fetch` stream reader; `EventSource` was never available,
      because a turn carries a body and SSE can only issue a GET. Two headers make it work
      and neither is `Authorization`: the access token rides the app's own header, because
      origin access control's SigV4 signature owns that one, and the client computes
      `x-amz-content-sha256` over its own body, because Lambda refuses an unsigned payload
      from an OAC-signed origin request. It is a hash and not a credential, so the browser
      holds no AWS key. The conversation id arrives on the `accepted` frame, ahead of any
      delta, which is what the socket's early id used to be for.
      WHAT WENT: `app/streaming.py`, `app/stream_worker.py`, `app/ws_authorizer.py`, the
      WebSocket API and stage, both routes, the `$connect` authorizer and its crypto layer,
      the `CONN#` connection records, and `streamingApiUrl` in config.json. `app/turn.py`
      already held the one copy of the turn's order, so nothing about a turn changed - what
      went was a second function to split it across and a second door into the pool.
      `POST /chat` stays: the eval runner uses it and the browser falls back to it on any
      failure before the server took the turn on. `app/stream_probe.py` is
      `app/streaming_app.py` now; the CDK construct ids still say "probe" deliberately,
      because renaming one REPLACES the function and its URL. config.yaml still carries a
      `streaming` block that nothing reads - its knobs were the socket's - and removing it
      is a config-schema change, not part of removing a transport.
- [ ] adapt an eval harness from camp's 9-question cli and gav's harness (needs account)
- [x] ~~measure the real average character advance for Nunito Sans at 0.9375rem (the card
      TITLE size - the only text the estimate still bears on) in a
      browser and re-derive cards.title_max_chars; 60 comes from a 0.5em estimate
      (config.yaml carries the arithmetic), which is standard for a humanist sans but
      is not a measurement of this font. No longer bears on desc_max_chars, which is
      no longer derived from the box.~~
      SUPERSEDED by the card-sizing entry above: title_max_chars is a runaway guard now
      (90), not a one-line derivation, so there is no arithmetic left for a measured
      advance to feed.
- [ ] cap-violation rate as an eval metric: if the model overruns often, either the
      prompt or the cap is wrong, and the fixture run says which
- [ ] retune retrieve_min_score; 0.35 was tuned against a differently shaped corpus (needs account)
- [ ] update the team repo's audit docs, which still say mangum and fan-out scraper

## Open

- [ ] the chat lambda's 29s timeout is a ceiling, not a choice: an HTTP API
      integration's `timeoutInMillis` maxes at 30,000 ms and is not raisable by a
      quota request, so the synthesis item "raise timeout/memory above their
      30s/256MB" is half done on purpose (memory rose to 1024MB, the timeout
      cannot rise). If the agent loop does not fit in 29s the fix is
      architectural - streaming, or an async job with a poll - not a bigger
      number. Unmeasured without an account.
- [ ] retention window and read access for identifiable transcripts containing crisis
      disclosures (docs/accounts-and-storage.md, Open). A policy question for SJSU, not a
      technical choice. The table now exists WITHOUT an answer, which the doc said should
      not happen - the deliberate reading is that TTL enabled on an attribute nothing writes
      keeps every option open at zero cost, so the table's existence does not pre-empt the
      policy. What it does pre-empt is nothing: no retention behaviour is configured, and
      turning one on later is writing `expiresAt`, not a migration.
- [ ] content-hash stability is unmeasured on sjsu.edu: gav's corpus scraped
      byte-identical twice, ours has never been scraped at all (needs account).
      A per-render nonce or a rotating banner inside an extracted body would
      defeat upload gating for that page - visible as a `pages_changed` count
      that never drops to zero.

## Resolved (2026-08-05)

- crawl list transport: a SECOND Lambda layer carrying `url-list.csv`, with only
  the filename in `URL_LIST_FILE`. Forced, not preferred: Lambda caps all
  environment variables at 4 KB in aggregate (hard, not raisable), and the list
  is 19 KB as compact url/section JSON - 2.9 KB even gzipped and base64'd. Gav's
  `SCRAPER_TIERS` env transport does not survive the scale-up. A layer rather
  than the function bundle because the list sits at the repo root and
  `Code.from_asset` takes one directory.
- scraper cadence: DAILY, single schedule, no tiers. Cheap by calculation: 203
  pages scale gav's measured 19-pages-in-25-67s to ~4.5-12 min at 512MB
  (~$0.12/mo of Lambda), a full corpus re-embed is ~190K Titan tokens (<$0.01),
  and change gating means unchanged days pay only the Lambda run. Fits the
  15-min timeout, but the top of the range is close: log run duration from the
  first live run.
- scraper ceiling: 203 pages fits one Lambda (see above); revisit only if runs
  approach the cap.
- billing alarm: deferred to v2 (see aws-port-draft.md).

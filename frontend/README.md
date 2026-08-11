# Frontend

Destination for the camp Astro app (build-plan: "pull camp frontend ui as astro
dist/"): the full Astro + React source moves in here, with the auth pages replaced
by a redirect to Cognito managed login (authorization code + PKCE, access token in
memory, expiry checked before fetch) and the mock sidebar fixtures deleted.

- Source lives in this tree; `dist/` is gitignored and built at synth (CDK
  container bundling - see the site-delivery section of the stack).
- The app reads its API endpoint from `config.json`, stamped with the deployed
  API URL at deploy time. Nothing in the committed source names an account,
  region, or API id.

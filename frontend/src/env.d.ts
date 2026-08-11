/// <reference types="astro/client" />

// No PUBLIC_* declarations, even though the app is back on a Hosted UI redirect. Camp
// declared eight of them (Cognito domain, client id, redirect and logout URIs, identity
// provider...) because its OAuth flow was configured at BUILD time - and that is the part
// that stays gone. The app fetches /config.json at runtime instead
// (src/lib/runtimeConfig.ts), and derives its redirect URI from window.location.origin, so
// one build deploys into any account and is correct on localhost and on the distribution
// without being rebuilt for either.

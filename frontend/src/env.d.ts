/// <reference types="astro/client" />

// No PUBLIC_* declarations. Camp declared eight of them (Cognito domain, client id,
// redirect and logout URIs, identity provider...) because its Hosted UI OAuth flow was
// configured at BUILD time. Every one of those is gone: the app fetches /config.json at
// runtime instead (src/lib/runtimeConfig.ts), which is what lets one build deploy into
// any account without being rebuilt.

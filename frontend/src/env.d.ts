/// <reference types="astro/client" />

// No PUBLIC_* declarations: the app fetches /config.json at runtime and derives its
// redirect URI from window.location.origin, so one build is correct in any account.

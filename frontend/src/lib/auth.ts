/**
 * Sign-in: a REDIRECT to Cognito managed login, then an authorization-code exchange
 * with PKCE. The token is held in memory.
 *
 * WHY A REDIRECT AND NOT A FORM. SJSU's own identity provider gets federated into this
 * same user pool later, as a config-only change. A federated user cannot authenticate
 * through InitiateAuth or any SDK call - only the hosted /oauth2/authorize endpoint can
 * run that round trip - so a username/password form written today would be deleted on the
 * day Okta arrives, and every student's sign-in would change under them. This is the flow
 * that survives that change: when the IdP lands, the only difference here is which buttons
 * Cognito's own page shows.
 *
 * PKCE, NOT A CLIENT SECRET. The app client is public because this is JavaScript in a
 * browser, where a secret is readable by anyone who views source. PKCE replaces it: a
 * random verifier is generated per attempt, only its SHA-256 hash travels in the redirect,
 * and the token exchange must present the original. An intercepted `?code=` is useless
 * without the verifier, which never leaves this origin.
 *
 * THE ACCESS TOKEN LIVES IN A MODULE VARIABLE AND NOWHERE ELSE - no localStorage, no
 * cookie, no persisted refresh token. A reload signs in again, which after the first time
 * is usually silent: the pool session cookie is still live, so Cognito redirects straight
 * back with a fresh code and no prompt.
 *
 * WHAT DOES TOUCH sessionStorage IS THE PKCE VERIFIER AND THE STATE, and it has to. A full
 * page redirect tears down every module variable in this document, so a verifier kept in
 * memory would be gone at exactly the moment the callback needs it. Both are deleted the
 * instant the exchange is attempted, and neither is a credential on its own: the verifier
 * is worthless without the matching code, which Cognito burns on first use.
 *
 * EXPIRY IS CHECKED BEFORE EVERY FETCH, not after a 401 comes back, and that is the
 * load-bearing part. An API Gateway JWT authorizer rejects a request BEFORE it reaches the
 * integration, and CORS headers are added by the integration - so an expired token comes
 * back to `fetch()` as an opaque network failure with no readable status. The browser
 * cannot tell it from a dropped connection. Never sending the doomed request is the only
 * reliable fix; the 401 branch in chatApi is kept because it is correct wherever the
 * response IS readable, and costs one comparison.
 */

import { loadRuntimeConfig, type RuntimeConfig } from './runtimeConfig';

type Session = {
	accessToken: string;
	/** Epoch ms. Compared against Date.now() before each request. */
	expiresAt: number;
	/**
	 * The immutable Cognito `sub`. THE identity - never the username or the email, both of
	 * which a person can change and a federated profile refreshes from provider claims.
	 */
	subject: string;
	/** Cosmetic only: what the sidebar shows. Never used to identify anyone. */
	displayName: string;
};

let session: Session | null = null;

/** Seconds shaved off the token's real lifetime, to cover clock skew and flight time. */
const EXPIRY_MARGIN_SECONDS = 60;

const VERIFIER_KEY = 'ssn.pkce.verifier';
const STATE_KEY = 'ssn.pkce.state';

export class AuthError extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'AuthError';
	}
}

export function isSignedIn(): boolean {
	return session !== null && Date.now() < session.expiresAt;
}

/** The display label for the signed-in person. Cosmetic - see `currentSubject`. */
export function currentUsername(): string | undefined {
	return session?.displayName;
}

/** The immutable `sub` claim. This is the one value that identifies a user. */
export function currentSubject(): string | undefined {
	return session?.subject;
}

/**
 * The redirect URI, derived from the page rather than configured.
 *
 * Cognito matches this against its registered callback list by EXACT STRING, and the same
 * built site is served from localhost and from CloudFront - so deriving it is what keeps
 * one config.json correct in both places. The trailing slash is deliberate and the stack
 * registers the same shape; `origin` never carries one.
 *
 * The callback is the root page, not a dedicated /auth/callback route: index.astro already
 * mounts the gate, so a second page would add a CloudFront routing case for nothing.
 */
function redirectUri(): string {
	return `${window.location.origin}/`;
}

function base64url(bytes: Uint8Array): string {
	let binary = '';
	for (const byte of bytes) binary += String.fromCharCode(byte);
	return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function randomToken(): string {
	return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

async function challengeFor(verifier: string): Promise<string> {
	const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
	return base64url(new Uint8Array(digest));
}

/**
 * The `sub` and a display label out of the ID token.
 *
 * Decoded, NOT verified, and that is safe for exactly one reason: this token came back
 * over TLS from Cognito's own token endpoint in direct response to our exchange, so there
 * is nothing here to spoof. It is read for what to render. The claim that decides
 * anything - who the API thinks you are - is checked by API Gateway against the pool's
 * JWKS on the ACCESS token, server-side, and nothing below can influence it.
 */
function claimsFromIdToken(idToken: string): { sub?: string; label?: string } {
	try {
		const payload = idToken.split('.')[1];
		if (!payload) return {};
		const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
		const claims = JSON.parse(json) as Record<string, unknown>;
		const label = claims.email ?? claims['cognito:username'] ?? claims.username;
		return {
			sub: typeof claims.sub === 'string' ? claims.sub : undefined,
			label: typeof label === 'string' ? label : undefined,
		};
	} catch {
		// A malformed ID token costs the sidebar its label, nothing more.
		return {};
	}
}

/**
 * Leave for Cognito's managed login. This function does not return - the browser navigates
 * away - and the flow resumes in `completeSignInFromRedirect` when it comes back.
 */
export async function beginSignIn(): Promise<void> {
	const config = await loadRuntimeConfig();

	const verifier = randomToken();
	const state = randomToken();
	// Written BEFORE the navigation, or there is no navigation to come back from.
	sessionStorage.setItem(VERIFIER_KEY, verifier);
	sessionStorage.setItem(STATE_KEY, state);

	const params = new URLSearchParams({
		response_type: 'code',
		client_id: config.userPoolClientId,
		redirect_uri: redirectUri(),
		scope: 'openid email profile',
		state,
		code_challenge: await challengeFor(verifier),
		code_challenge_method: 'S256',
	});

	window.location.assign(`${config.loginDomain}/oauth2/authorize?${params.toString()}`);
}

/** True when the current URL is a return trip from managed login. */
export function hasPendingRedirect(): boolean {
	const params = new URLSearchParams(window.location.search);
	return params.has('code') || params.has('error');
}

/**
 * Strip the OAuth parameters from the address bar.
 *
 * Not cosmetic: a code is single-use, so a reload or a shared URL carrying one produces an
 * invalid_grant that reads like a broken app. Removed whether the exchange succeeded or
 * failed, for the same reason.
 */
function clearRedirectParams(): void {
	const url = new URL(window.location.href);
	for (const key of ['code', 'state', 'error', 'error_description']) {
		url.searchParams.delete(key);
	}
	window.history.replaceState({}, '', url.pathname + url.search + url.hash);
}

async function exchangeCode(
	config: RuntimeConfig,
	code: string,
	verifier: string,
): Promise<void> {
	// Form-encoded, not JSON: the OAuth 2.0 token endpoint takes
	// application/x-www-form-urlencoded and Cognito rejects anything else.
	const response = await fetch(`${config.loginDomain}/oauth2/token`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
		body: new URLSearchParams({
			grant_type: 'authorization_code',
			client_id: config.userPoolClientId,
			// Sent again and it must match the authorize call byte for byte - Cognito
			// re-checks it here rather than trusting the code alone.
			redirect_uri: redirectUri(),
			code,
			code_verifier: verifier,
		}),
	});

	if (!response.ok) {
		let detail = 'Sign-in could not be completed.';
		try {
			const body = (await response.json()) as { error?: string; error_description?: string };
			detail = body.error_description ?? body.error ?? detail;
		} catch {
			// Keep the default when the error body is not JSON.
		}
		throw new AuthError(detail);
	}

	const body = (await response.json()) as {
		access_token?: string;
		id_token?: string;
		expires_in?: number;
	};

	// The ACCESS token, deliberately, not the ID token: API Gateway's authorizer is
	// configured with the app client id as its audience, and a Cognito access token
	// carries `client_id` where an ID token carries `aud`.
	if (!body.access_token || !body.expires_in) {
		throw new AuthError('Cognito returned no access token.');
	}

	const claims = body.id_token ? claimsFromIdToken(body.id_token) : {};
	session = {
		accessToken: body.access_token,
		expiresAt: Date.now() + (body.expires_in - EXPIRY_MARGIN_SECONDS) * 1000,
		subject: claims.sub ?? '',
		displayName: claims.label ?? 'Signed in',
	};
}

/**
 * Finish a sign-in that started with `beginSignIn`, if this page load is the return trip.
 *
 * Returns true when a session now exists, false when there was nothing to complete.
 * Throws AuthError when the return trip carried a failure.
 */
export async function completeSignInFromRedirect(): Promise<boolean> {
	const params = new URLSearchParams(window.location.search);
	const error = params.get('error');
	const code = params.get('code');
	if (!error && !code) return false;

	const returnedState = params.get('state');
	const expectedState = sessionStorage.getItem(STATE_KEY);
	const verifier = sessionStorage.getItem(VERIFIER_KEY);
	// Read once and dropped immediately - a verifier that outlives its exchange is a
	// verifier some later attempt could reuse.
	sessionStorage.removeItem(STATE_KEY);
	sessionStorage.removeItem(VERIFIER_KEY);
	clearRedirectParams();

	if (error) {
		throw new AuthError(params.get('error_description') ?? error);
	}

	// STATE IS CHECKED BEFORE THE CODE IS SPENT. Without this, a third party could hand a
	// student a link carrying its own authorization code and sign them into the attacker's
	// account, where anything they typed would be the attacker's to read.
	if (!expectedState || returnedState !== expectedState) {
		throw new AuthError('Sign-in could not be verified. Please try again.');
	}
	if (!verifier) {
		throw new AuthError('Sign-in expired before it finished. Please try again.');
	}

	await exchangeCode(await loadRuntimeConfig(), code as string, verifier);
	return true;
}

/**
 * Sign out through Cognito, not just locally.
 *
 * Dropping the in-memory token is NOT enough on its own: the pool leaves a session cookie
 * on its own domain, so the next sign-in would bounce through /oauth2/authorize and come
 * straight back with a code, never asking who is there. On a shared campus machine that
 * hands the next person the previous student's account. The /logout endpoint clears that
 * cookie and then returns the browser here, signed out for real.
 */
export async function signOut(): Promise<void> {
	session = null;
	const config = await loadRuntimeConfig();
	const params = new URLSearchParams({
		client_id: config.userPoolClientId,
		logout_uri: redirectUri(),
	});
	window.location.assign(`${config.loginDomain}/logout?${params.toString()}`);
}

/**
 * The Authorization header for a gated request, or a thrown AuthError if the token is
 * missing or expired. Called before every /chat fetch - see the expiry note above.
 */
export function authorizationHeader(): Record<string, string> {
	if (!session) {
		throw new AuthError('Not signed in.');
	}
	if (Date.now() >= session.expiresAt) {
		session = null;
		throw new AuthError('Your session expired. Please sign in again.');
	}
	return { Authorization: `Bearer ${session.accessToken}` };
}

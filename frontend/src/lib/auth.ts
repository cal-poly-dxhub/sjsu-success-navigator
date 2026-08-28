/** Sign-in: a redirect to Cognito managed login, then an authorization-code exchange with PKCE. */

import { loadRuntimeConfig, type RuntimeConfig } from './runtimeConfig';

type Session = {
	accessToken: string;
	/** Epoch ms. Compared against Date.now() before each request. */
	expiresAt: number;
	/** The immutable Cognito `sub`. */
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

/** The display label for the signed-in person. Cosmetic, see `currentSubject`. */
export function currentUsername(): string | undefined {
	return session?.displayName;
}

/** The immutable `sub` claim. This is the one value that identifies a user. */
export function currentSubject(): string | undefined {
	return session?.subject;
}

/** The redirect uri, derived from the page rather than configured. */
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

/** The `sub` and a display label out of the ID token. */
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

/** Leave for Cognito's managed login. */
export async function beginSignIn(): Promise<void> {
	const config = await loadRuntimeConfig();

	const verifier = randomToken();
	const state = randomToken();
	// Written before the navigation, or there is no navigation to come back from.
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

/** Strip the OAuth parameters from the address bar. */
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
	// Form-encoded, not JSON: the OAuth 2.0 token endpoint takes application/x-www-form-
	// urlencoded and Cognito rejects anything else.
	const response = await fetch(`${config.loginDomain}/oauth2/token`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
		body: new URLSearchParams({
			grant_type: 'authorization_code',
			client_id: config.userPoolClientId,
			// Sent again and it must match the authorize call byte for byte, Cognito re-checks
			// it here rather than trusting the code alone.
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

	// The access token, not the ID token: the authorizer's audience is the app client id, and
	// an access token carries `client_id` where an ID token carries `aud`.
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

/** Finish a sign-in that started with `beginSignIn`, if this page load is the return trip. */
export async function completeSignInFromRedirect(): Promise<boolean> {
	const params = new URLSearchParams(window.location.search);
	const error = params.get('error');
	const code = params.get('code');
	if (!error && !code) return false;

	const returnedState = params.get('state');
	const expectedState = sessionStorage.getItem(STATE_KEY);
	const verifier = sessionStorage.getItem(VERIFIER_KEY);
	// Read once and dropped immediately, a verifier that outlives its exchange is a verifier
	// some later attempt could reuse.
	sessionStorage.removeItem(STATE_KEY);
	sessionStorage.removeItem(VERIFIER_KEY);
	clearRedirectParams();

	if (error) {
		throw new AuthError(params.get('error_description') ?? error);
	}

	// State is checked before the code is spent.
	if (!expectedState || returnedState !== expectedState) {
		throw new AuthError('Sign-in could not be verified. Please try again.');
	}
	if (!verifier) {
		throw new AuthError('Sign-in expired before it finished. Please try again.');
	}

	await exchangeCode(await loadRuntimeConfig(), code as string, verifier);
	return true;
}

/** Sign out through Cognito, not just locally. */
export async function signOut(): Promise<void> {
	session = null;
	const config = await loadRuntimeConfig();
	const params = new URLSearchParams({
		client_id: config.userPoolClientId,
		logout_uri: redirectUri(),
	});
	window.location.assign(`${config.loginDomain}/logout?${params.toString()}`);
}

/** The Authorization header for a gated request, or a thrown AuthError if the token is missing
 * or expired. */
export function authorizationHeader(): Record<string, string> {
	return { Authorization: `Bearer ${currentAccessToken()}` };
}

/** The access token itself, checked for expiry exactly as the header above is. */
export function currentAccessToken(): string {
	if (!session) {
		throw new AuthError('Not signed in.');
	}
	if (Date.now() >= session.expiresAt) {
		session = null;
		throw new AuthError('Your session expired. Please sign in again.');
	}
	return session.accessToken;
}

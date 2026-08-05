/**
 * Sign-in: ONE unsigned InitiateAuth call to Cognito, token held in memory.
 *
 * This replaces camp's Google OAuth entirely (docs/synthesis.md). Camp used the Hosted UI
 * authorization-code + PKCE flow, which needed a /login page to redirect out to Google and
 * an /auth/callback page to exchange `?code=` for tokens. Both pages are gone: with a
 * single shared pilot login there is nothing to redirect to, so the flow collapses to one
 * fetch and the two routes have no reason to exist.
 *
 * THE TOKEN LIVES IN A MODULE VARIABLE AND NOWHERE ELSE. No localStorage, no
 * sessionStorage, no cookie - camp kept its tokens (including the refresh token) in
 * sessionStorage and its own comment called that a POC. A reload signs in again, which is
 * the intended trade for a shared pilot credential.
 *
 * EXPIRY IS CHECKED BEFORE EVERY FETCH, not after a 401 comes back, and that is the
 * load-bearing part. An API Gateway JWT authorizer rejects a request BEFORE it reaches the
 * integration, and CORS headers are added by the integration - so an expired token comes
 * back to `fetch()` as an opaque network failure with no readable status. The browser
 * cannot tell it from a dropped connection. Never sending the doomed request is the only
 * reliable fix; the 401 branch below is kept because it is correct wherever the response
 * IS readable, and costs one comparison.
 */

import { loadRuntimeConfig } from './runtimeConfig';

type Session = {
	accessToken: string;
	/** Epoch ms. Compared against Date.now() before each request. */
	expiresAt: number;
	username: string;
};

let session: Session | null = null;

/** Seconds shaved off the token's real lifetime, to cover clock skew and flight time. */
const EXPIRY_MARGIN_SECONDS = 60;

export class AuthError extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'AuthError';
	}
}

export function isSignedIn(): boolean {
	return session !== null && Date.now() < session.expiresAt;
}

export function currentUsername(): string | undefined {
	return session?.username;
}

export function signOut(): void {
	session = null;
}

/**
 * Exchange a username and password for an access token.
 *
 * USER_PASSWORD_AUTH rather than SRP: this is a dependency-free browser client with no
 * SDK, and SRP needs big-integer crypto no such client is going to carry. The password
 * crosses the wire inside TLS instead of never leaving the browser - the right trade for
 * a shared pilot login, and NOT a pattern to copy for real student accounts (which are
 * the v2 item).
 */
export async function signIn(username: string, password: string): Promise<void> {
	const config = await loadRuntimeConfig();

	const response = await fetch(`https://cognito-idp.${config.region}.amazonaws.com/`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/x-amz-json-1.1',
			'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
		},
		body: JSON.stringify({
			AuthFlow: 'USER_PASSWORD_AUTH',
			ClientId: config.userPoolClientId,
			AuthParameters: { USERNAME: username, PASSWORD: password },
		}),
	});

	if (!response.ok) {
		// Cognito's own message is shown as-is for the operational failures a deployer
		// needs to see (a pool with no user, a password never set to permanent).
		let detail = 'Sign-in failed.';
		try {
			const body = (await response.json()) as { message?: string; __type?: string };
			detail = body.message ?? body.__type ?? detail;
		} catch {
			// Keep the default when the error body is not JSON.
		}
		throw new AuthError(detail);
	}

	const body = (await response.json()) as {
		AuthenticationResult?: { AccessToken?: string; ExpiresIn?: number };
		ChallengeName?: string;
	};

	if (body.ChallengeName) {
		// The account was created without --permanent, so Cognito wants a new password
		// before it will issue a token. Named explicitly: the symptom is otherwise a
		// successful-looking response carrying no token at all.
		throw new AuthError(
			`Cognito returned the ${body.ChallengeName} challenge. The pilot account's ` +
				'password was set without --permanent.',
		);
	}

	const token = body.AuthenticationResult?.AccessToken;
	const expiresIn = body.AuthenticationResult?.ExpiresIn;
	if (!token || !expiresIn) {
		throw new AuthError('Cognito returned no access token.');
	}

	// The ACCESS token, deliberately, not the ID token: API Gateway's authorizer is
	// configured with the app client id as its audience, and a Cognito access token
	// carries `client_id` where an ID token carries `aud`.
	session = {
		accessToken: token,
		expiresAt: Date.now() + (expiresIn - EXPIRY_MARGIN_SECONDS) * 1000,
		username,
	};
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

/**
 * Runtime configuration, fetched from /config.json.
 *
 * Camp read its endpoint from `import.meta.env.PUBLIC_CHAT_API_URL`, which Astro inlines
 * at BUILD time. That cannot work here: the API URL does not exist until `cdk deploy`
 * creates the HTTP API, so a build-time value would mean build, deploy, read the outputs,
 * rebuild, redeploy - forever, and a fresh install in another account would ship a site
 * pointing at this one's stack.
 *
 * The stack writes config.json into the site bucket at deploy time from CloudFormation
 * tokens (Source.jsonData), with `no-store` so a cached copy can never pin a stale
 * endpoint. Nothing in this repo names an account, a region, an API id or a pool id.
 */

export type RuntimeConfig = {
	chatApiUrl: string;
	/**
	 * Base URL for the history reads: GET here lists the signed-in student's own
	 * conversations, and GET here + "/<conversationId>" opens one. Stamped by the stack
	 * alongside chatApiUrl rather than derived from it - stripping "/chat" and re-appending
	 * would put this stack's route names in a file this stack does not build.
	 */
	conversationsApiUrl: string;
	userPoolId: string;
	/** The WEB app client - authorization code + PKCE. Never the eval client. */
	userPoolClientId: string;
	/**
	 * Base URL of the Cognito managed login domain, e.g.
	 * https://sjsu-navigator-abc123.auth.us-west-2.amazoncognito.com - the origin the
	 * browser is redirected to for /oauth2/authorize, /oauth2/token and /logout.
	 *
	 * There is no redirect URI here on purpose: auth.ts derives it from
	 * window.location.origin, so this one file is correct on localhost and on the
	 * distribution, and the two can never disagree about the exact string Cognito matches.
	 */
	loginDomain: string;
	region: string;
};

let cached: Promise<RuntimeConfig> | null = null;

export function loadRuntimeConfig(): Promise<RuntimeConfig> {
	// Cached per page load, not per call: every fetch would otherwise re-request it, and
	// it cannot change while the page is open.
	if (cached) return cached;

	cached = fetch('/config.json', { cache: 'no-store' })
		.then((response) => {
			if (!response.ok) {
				throw new Error(`config.json returned ${response.status}`);
			}
			return response.json() as Promise<RuntimeConfig>;
		})
		.then((config) => {
			const missing = (
				[
					'chatApiUrl',
					'conversationsApiUrl',
					'userPoolId',
					'userPoolClientId',
					'loginDomain',
					'region',
				] as const
			).filter((key) => !config[key]);
			if (missing.length > 0) {
				throw new Error(`config.json is missing: ${missing.join(', ')}`);
			}
			// Trailing slashes stripped on all three URLs because every use appends a path.
			return {
				...config,
				chatApiUrl: config.chatApiUrl.replace(/\/$/, ''),
				// Trailing slash off for the same reason: the single-conversation URL is
				// this plus "/<id>", and a double slash is a different path to API Gateway.
				conversationsApiUrl: config.conversationsApiUrl.replace(/\/$/, ''),
				loginDomain: config.loginDomain.replace(/\/$/, ''),
			};
		})
		.catch((error: unknown) => {
			// Do not cache a failure: a transient network error at page load would
			// otherwise make the app permanently unusable until a manual reload.
			cached = null;
			throw error;
		});

	return cached;
}

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
	userPoolId: string;
	userPoolClientId: string;
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
				['chatApiUrl', 'userPoolId', 'userPoolClientId', 'region'] as const
			).filter((key) => !config[key]);
			if (missing.length > 0) {
				throw new Error(`config.json is missing: ${missing.join(', ')}`);
			}
			return { ...config, chatApiUrl: config.chatApiUrl.replace(/\/$/, '') };
		})
		.catch((error: unknown) => {
			// Do not cache a failure: a transient network error at page load would
			// otherwise make the app permanently unusable until a manual reload.
			cached = null;
			throw error;
		});

	return cached;
}

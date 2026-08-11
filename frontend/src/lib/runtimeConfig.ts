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
	/**
	 * The cost panel's model, or absent when the panel is off.
	 *
	 * ABSENCE IS THE GATE, and it is why this is optional rather than carrying a `false`
	 * flag: the stack omits the key entirely when config.yaml's `cost_model.enabled` is
	 * false, so a build that never receives it cannot render the control at all. Okta
	 * federation provisions any SJSU student into this pool just in time, and a student
	 * must not be shown operating cost - one config edit takes the whole surface away.
	 *
	 * Not a bill. Every figure is a published AWS list rate multiplied by usage measured
	 * against this stack (eval/measure_usage.py), so nothing here can blend in the spend of
	 * another project sharing the AWS account.
	 */
	costModel?: CostModel;
};

/** Published AWS list prices, all for the stack's own region. */
export type CostRates = {
	generation_input_per_1m: number;
	generation_output_per_1m: number;
	embedding_per_1m: number;
	guardrail_content_per_1k_units: number;
	vector_query_per_1m: number;
	vector_storage_per_gb_month: number;
	vector_put_per_gb: number;
	s3_storage_per_gb_month: number;
	lambda_per_1m_requests: number;
	lambda_per_gb_second: number;
	api_requests_per_1m: number;
	cloudfront_per_1m_requests: number;
	logs_ingest_per_gb: number;
	dynamodb_write_per_1m: number;
	dynamodb_read_per_1m: number;
};

/** What one real question consumed, measured against the deployed stack. */
export type CostMeasured = {
	sample_questions: number;
	model_calls_avg: number;
	context_tokens_per_call_base: number;
	context_tokens_per_call_per_prior_turn: number;
	output_tokens_avg: number;
	retrievals_avg: number;
	guardrail_content_units_avg: number;
	retrieval_query_tokens: number;
	/** Priced from real billed durations and configured memory, not measured by the harness. */
	chat_lambda_gb_seconds: number;
	chat_dynamodb_writes: number;
	chat_dynamodb_reads: number;
};

/** What exists at zero traffic, measured against the deployed resources. */
export type CostBaseline = {
	s3_stored_bytes: number;
	vector_count: number;
	vector_bytes_each: number;
	ingest_embedded_tokens: number;
	scraper_seconds_per_run: number;
	scraper_memory_gb: number;
	scrapes_per_month: number;
	reindexes_per_month: number;
	deploys_per_month: number;
	log_gb_per_month: number;
};

export type CostModel = {
	asOf: string;
	region: string;
	currency: string;
	measuredAt: string;
	rates: CostRates;
	measured: CostMeasured;
	baseline: CostBaseline;
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

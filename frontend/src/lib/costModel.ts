/**
 * The cost panel's arithmetic: published AWS list rates x measured usage.
 *
 * Kept out of the component so the numbers can be read - and argued with - without reading
 * JSX. Nothing here fetches anything; every input arrives in the CostModel that the stack
 * stamped into config.json (see infra/infra/config.py, resolve_cost_model).
 *
 * WHAT THIS IS NOT: a bill. No Cost Explorer call, no billing API, no account spend. That
 * is a correctness property as much as a scoping one - the AWS account this stack lives in
 * also runs other projects, so an account total would silently blend somebody else's spend
 * into a figure labelled as this system's. Rate x measured-usage cannot.
 */

import type { CostBaseline, CostMeasured, CostModel, CostRates } from './runtimeConfig';

/** One message's cost, split into the lines the panel itemizes. */
export type PerMessageCost = {
	inputTokens: number;
	outputTokens: number;
	/** Bedrock Converse input + output. Measured. */
	model: number;
	/** ApplyGuardrail content-filter units. Measured. */
	guardrail: number;
	/** S3 Vectors query + the embedding of the query text. Measured. */
	retrieval: number;
	/** API Gateway, Lambda, CloudFront, DynamoDB. Priced, not measured - see the panel. */
	plumbing: number;
	total: number;
};

export type FixedMonthlyCost = {
	storage: number;
	reindex: number;
	scraper: number;
	logs: number;
	/** Scraper invocations a month, including the assumed deploys that re-fire it. */
	runs: number;
	/** Runs that actually find new content and pay to re-embed it. */
	ingests: number;
	total: number;
};

const PER_MILLION = 1e6;
const BYTES_PER_GB = 1e9;

/**
 * What one message costs, at the depth the sample was measured at.
 *
 * The measured questions were all asked with no prior turns, so this is a first question's
 * price and `perPriorTurn` below states the depth adder separately rather than burying it
 * in an average. Two sliders would let a reader vary depth; the panel deliberately has one,
 * so the effect is disclosed as a line instead of hidden in the headline.
 */
export function perMessage(rates: CostRates, measured: CostMeasured): PerMessageCost {
	const inputTokens = measured.model_calls_avg * measured.context_tokens_per_call_base;
	const outputTokens = measured.output_tokens_avg;

	const model =
		(inputTokens / PER_MILLION) * rates.generation_input_per_1m +
		(outputTokens / PER_MILLION) * rates.generation_output_per_1m;

	// One content-filter screen on the student's bare question. There is no PII policy and
	// no answer-side guardrail on this stack, so this is the whole guardrail line.
	const guardrail =
		(measured.guardrail_content_units_avg / 1000) * rates.guardrail_content_per_1k_units;

	const retrieval =
		measured.retrievals_avg *
		(rates.vector_query_per_1m / PER_MILLION +
			(measured.retrieval_query_tokens / PER_MILLION) * rates.embedding_per_1m);

	const plumbing =
		rates.api_requests_per_1m / PER_MILLION +
		rates.cloudfront_per_1m_requests / PER_MILLION +
		rates.lambda_per_1m_requests / PER_MILLION +
		measured.chat_lambda_gb_seconds * rates.lambda_per_gb_second +
		(measured.chat_dynamodb_writes / PER_MILLION) * rates.dynamodb_write_per_1m +
		(measured.chat_dynamodb_reads / PER_MILLION) * rates.dynamodb_read_per_1m;

	return {
		inputTokens,
		outputTokens,
		model,
		guardrail,
		retrieval,
		plumbing,
		total: model + guardrail + retrieval + plumbing,
	};
}

/**
 * What one prior turn of conversation history adds to a message.
 *
 * Small on purpose, and worth stating for that reason: the server replays only the TEXT of
 * previous turns (app/history.py's context projection), never the retrieved passages behind
 * them, and it stops growing at chat.max_history_messages. A reader who assumes deep
 * conversations get expensive fast is wrong by about two orders of magnitude, and the panel
 * should say so rather than let the assumption stand.
 */
export function perPriorTurn(rates: CostRates, measured: CostMeasured): number {
	return (
		(measured.model_calls_avg * measured.context_tokens_per_call_per_prior_turn) /
			PER_MILLION *
		rates.generation_input_per_1m
	);
}

/** What runs whether or not anybody asks anything. Every line is rate x measured quantity. */
export function fixedMonthly(rates: CostRates, baseline: CostBaseline): FixedMonthlyCost {
	// Two different counts, because the scraper runs far more often than it changes
	// anything. Every run costs Lambda time; only a run that finds new content re-embeds
	// and re-writes vectors. A deploy can re-fire the scraper, so it lands in both.
	const runs = baseline.scrapes_per_month + baseline.deploys_per_month;
	const ingests = baseline.reindexes_per_month + baseline.deploys_per_month;

	const vectorBytes = baseline.vector_count * baseline.vector_bytes_each;

	const storage =
		(baseline.s3_stored_bytes / BYTES_PER_GB) * rates.s3_storage_per_gb_month +
		(vectorBytes / BYTES_PER_GB) * rates.vector_storage_per_gb_month;

	const reindex =
		ingests *
		((baseline.ingest_embedded_tokens / PER_MILLION) * rates.embedding_per_1m +
			(vectorBytes / BYTES_PER_GB) * rates.vector_put_per_gb);

	const scraper =
		runs *
		(baseline.scraper_seconds_per_run * baseline.scraper_memory_gb * rates.lambda_per_gb_second +
			rates.lambda_per_1m_requests / PER_MILLION);

	const logs = baseline.log_gb_per_month * rates.logs_ingest_per_gb;

	return {
		storage,
		reindex,
		scraper,
		logs,
		runs,
		ingests,
		total: storage + reindex + scraper + logs,
	};
}

/**
 * A month at a given message volume: the floor plus the variable part.
 *
 * The two are reported separately rather than blended because they answer different
 * questions - "what does it cost to keep this switched on" and "what does each student
 * question add" - and a single number answers neither.
 */
export function monthlyAt(model: CostModel, messages: number) {
	const per = perMessage(model.rates, model.measured);
	const fixed = fixedMonthly(model.rates, model.baseline);
	const variable = messages * per.total;
	return { per, fixed, variable, total: fixed.total + variable };
}

/**
 * Money, at a precision that fits the magnitude.
 *
 * A message costs fractions of a cent and a month costs dollars; one format cannot show
 * both without either rounding a real line to $0.00 or printing a monthly total to four
 * decimals. The "<" case matters: several real line items round to zero at four decimals,
 * and printing $0.0000 for something that is not zero is the one rounding error worth
 * avoiding on a page whose whole claim is that the figures are checkable.
 */
export function money(value: number, digits: number): string {
	return `$${value.toLocaleString('en-US', {
		minimumFractionDigits: digits,
		maximumFractionDigits: digits,
	})}`;
}

export function smallMoney(value: number): string {
	if (value === 0) return '$0';
	if (value < 0.0001) return '<$0.0001';
	return value >= 0.01 ? money(value, 2) : money(value, 4);
}

export function count(value: number): string {
	return Math.round(value).toLocaleString('en-US');
}

/**
 * The cost panel's arithmetic: published AWS list rates x usage.
 *
 * TWO KINDS OF USAGE, and the panel keeps them apart because they are not equally
 * trustworthy about any particular conversation:
 *
 *   - MEASURED, this conversation. `conversationCost` prices the tokens the server counted
 *     on the turns actually sent in this tab (app/usage.py), so it is the real bill for the
 *     chat in front of the reader.
 *   - MEASURED, a sample. `perMessage` and `fixedMonthly` price a projection from the
 *     24-question average and the deployed baseline in config.yaml, which is the only
 *     honest way to answer "what would a month of this cost".
 *
 * The same rate table prices both, so the two halves of the panel can never disagree about
 * what a token costs.
 *
 * Kept out of the component so the numbers can be read - and argued with - without reading
 * JSX. Nothing here fetches anything; every input arrives in the CostModel that the stack
 * stamped into config.json (see infra/infra/config.py, resolve_cost_model).
 *
 * WHAT THIS IS NOT: a bill. No Cost Explorer call, no billing API, no account spend. That
 * is a correctness property as much as a scoping one - the AWS account this stack lives in
 * also runs other projects, so an account total would silently blend somebody else's spend
 * into a figure labelled as this system's. Rate x usage cannot.
 */

import type { ConversationUsage, TurnUsage } from '../types/chat';
import type { CostBaseline, CostMeasured, CostRates } from './runtimeConfig';

/** One message's cost, at the sample's average shape. */
export type PerMessageCost = {
	inputTokens: number;
	outputTokens: number;
	/** Bedrock Converse input + output. */
	model: number;
	/** ApplyGuardrail content-filter units. */
	guardrail: number;
	/** S3 Vectors query + the embedding of the query text. */
	retrieval: number;
	/** API Gateway, Lambda, CloudFront, DynamoDB. Priced from configured memory and rates. */
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

export const NO_CONVERSATION_USAGE: ConversationUsage = {
	messages: 0,
	modelCalls: 0,
	inputTokens: 0,
	outputTokens: 0,
	guardrailContentUnits: 0,
	retrievals: 0,
};

/**
 * Fold one reply's usage into the conversation's running total.
 *
 * `messages` counts TURNS, not model calls, and the difference is the thing the panel is
 * there to show: a message that made the model search again billed two calls. A blocked
 * turn counts too - it billed a guardrail screen and the student sent it.
 */
export function addTurnUsage(
	current: ConversationUsage | undefined,
	turn: TurnUsage,
): ConversationUsage {
	const base = current ?? NO_CONVERSATION_USAGE;
	return {
		messages: base.messages + 1,
		modelCalls: base.modelCalls + (turn.modelCalls ?? 0),
		inputTokens: base.inputTokens + (turn.inputTokens ?? 0),
		outputTokens: base.outputTokens + (turn.outputTokens ?? 0),
		guardrailContentUnits: base.guardrailContentUnits + (turn.guardrailContentUnits ?? 0),
		retrievals: base.retrievals + (turn.retrievals ?? 0),
	};
}

/**
 * What this conversation has actually cost.
 *
 * Every token term is measured. The per-message plumbing term is not, and cannot be from
 * inside the request: a Lambda's billed duration is reported after the invocation ends, so
 * the panel prices it from the same measured constant the projection uses, multiplied by
 * the number of messages this conversation really sent. That is the one estimated component
 * of an otherwise measured figure, and it is well under a tenth of a cent per message.
 */
export function conversationCost(
	rates: CostRates,
	measured: CostMeasured,
	usage: ConversationUsage,
): number {
	const model =
		(usage.inputTokens / PER_MILLION) * rates.generation_input_per_1m +
		(usage.outputTokens / PER_MILLION) * rates.generation_output_per_1m;

	const guardrail =
		(usage.guardrailContentUnits / 1000) * rates.guardrail_content_per_1k_units;

	const retrieval =
		usage.retrievals *
		(rates.vector_query_per_1m / PER_MILLION +
			(measured.retrieval_query_tokens / PER_MILLION) * rates.embedding_per_1m);

	return model + guardrail + retrieval + usage.messages * plumbingPerMessage(rates, measured);
}

/**
 * The per-request lines that bill on invocation rather than on tokens.
 *
 * Shared by the measured conversation and the projected month, so a message costs the same
 * to plumb on both halves of the panel.
 */
function plumbingPerMessage(rates: CostRates, measured: CostMeasured): number {
	return (
		rates.api_requests_per_1m / PER_MILLION +
		rates.cloudfront_per_1m_requests / PER_MILLION +
		rates.lambda_per_1m_requests / PER_MILLION +
		measured.chat_lambda_gb_seconds * rates.lambda_per_gb_second +
		(measured.chat_dynamodb_writes / PER_MILLION) * rates.dynamodb_write_per_1m +
		(measured.chat_dynamodb_reads / PER_MILLION) * rates.dynamodb_read_per_1m
	);
}

/**
 * What one message costs on average, from the sample.
 *
 * This is what the monthly projection multiplies, and it is deliberately NOT what the panel
 * shows for the conversation on screen: an average over 24 questions answers "what will a
 * month cost", never "what did this chat cost".
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

	const plumbing = plumbingPerMessage(rates, measured);

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
 * Money, at a precision that fits the magnitude.
 *
 * A conversation costs fractions of a cent and a month costs dollars; one format cannot
 * show both without either rounding a real figure to $0.00 or printing a monthly total to
 * four decimals.
 */
export function money(value: number, digits: number): string {
	return `$${value.toLocaleString('en-US', {
		minimumFractionDigits: digits,
		maximumFractionDigits: digits,
	})}`;
}

export function count(value: number): string {
	return Math.round(value).toLocaleString('en-US');
}

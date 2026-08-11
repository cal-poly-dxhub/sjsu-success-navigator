import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
	count,
	fixedMonthly,
	money,
	perMessage,
	perPriorTurn,
	smallMoney,
} from '../lib/costModel';
import type { CostModel } from '../lib/runtimeConfig';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './CostPanel.css';

type CostPanelProps = {
	open: boolean;
	model: CostModel;
	onClose: () => void;
};

/** Slider bounds. SJSU enrolls ~36,000 students, so the top of the range is a whole campus. */
const MESSAGES_MIN = 0;
const MESSAGES_MAX = 200_000;
const MESSAGES_STEP = 1_000;
const MESSAGES_DEFAULT = 20_000;

/**
 * The cost panel: what this system costs to run, for showing a sponsor.
 *
 * ONE SLIDER, not three. The only quantity nobody can measure for you is how many messages
 * students will send; everything else on this panel is measured, so exposing it as a knob
 * would invite a reader to tune numbers that were observed rather than assumed. The two
 * effects a second and third slider would have controlled are stated as lines instead - the
 * per-prior-turn adder under the itemization, and the fixed floor as its own figure.
 *
 * EVERY LINE IS LABELLED measured OR priced, because they are not equally trustworthy. The
 * Bedrock, guardrail and retrieval lines come from real token counts observed against this
 * deployment (eval/measure_usage.py). The plumbing line does not: it is real billed
 * durations times configured memory, plus per-request rates. Blurring the two would make
 * the whole panel exactly as credible as its weakest line, so it says which is which.
 */
export function CostPanel({ open, model, onClose }: CostPanelProps) {
	const [messages, setMessages] = useState(MESSAGES_DEFAULT);
	const panelRef = useRef<HTMLDivElement | null>(null);
	const previousActiveRef = useRef<HTMLElement | null>(null);
	const reduceMotion = usePrefersReducedMotion();

	// Rates and the baseline never change while the page is open, so the floor and the
	// per-message price are computed once rather than on every slider frame.
	const per = useMemo(() => perMessage(model.rates, model.measured), [model]);
	const fixed = useMemo(() => fixedMonthly(model.rates, model.baseline), [model]);
	const depthAdder = useMemo(() => perPriorTurn(model.rates, model.measured), [model]);

	const variable = messages * per.total;

	useEffect(() => {
		if (!open) return;

		previousActiveRef.current =
			document.activeElement instanceof HTMLElement ? document.activeElement : null;
		const previousOverflow = document.body.style.overflow;
		document.body.style.overflow = 'hidden';

		const handleKeyDown = (event: KeyboardEvent) => {
			if (event.key === 'Escape') {
				onClose();
				return;
			}
			// A focus trap, for the same reason the safety modal has one: this panel covers
			// the conversation, so tabbing out of it lands on controls the student cannot
			// see. The slider is a real range input, so arrow keys work without help.
			if (event.key !== 'Tab' || !panelRef.current) return;
			const focusables = Array.from(
				panelRef.current.querySelectorAll<HTMLElement>(
					'a[href], button:not([disabled]), input:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
				),
			);
			if (focusables.length === 0) return;
			const first = focusables[0];
			const last = focusables[focusables.length - 1];
			if (event.shiftKey && document.activeElement === first) {
				event.preventDefault();
				last.focus();
			} else if (!event.shiftKey && document.activeElement === last) {
				event.preventDefault();
				first.focus();
			}
		};

		document.addEventListener('keydown', handleKeyDown);
		return () => {
			document.removeEventListener('keydown', handleKeyDown);
			document.body.style.overflow = previousOverflow;
			previousActiveRef.current?.focus();
		};
	}, [open, onClose]);

	const rateRows: Array<[string, string]> = [
		['Claude Sonnet 4.6 input', `${money(model.rates.generation_input_per_1m, 2)} / 1M tokens`],
		['Claude Sonnet 4.6 output', `${money(model.rates.generation_output_per_1m, 2)} / 1M tokens`],
		['Titan Text Embeddings V2', `${money(model.rates.embedding_per_1m, 2)} / 1M tokens`],
		[
			'Guardrails, content filters',
			`${money(model.rates.guardrail_content_per_1k_units, 2)} / 1k units`,
		],
		['S3 Vectors queries', `${money(model.rates.vector_query_per_1m, 2)} / 1M`],
		['S3 Vectors storage', `${money(model.rates.vector_storage_per_gb_month, 2)} / GB-month`],
		['S3 Standard storage', `${money(model.rates.s3_storage_per_gb_month, 3)} / GB-month`],
		['Lambda compute', `$${model.rates.lambda_per_gb_second} / GB-second`],
		['HTTP API requests', `${money(model.rates.api_requests_per_1m, 2)} / 1M`],
		['CloudFront requests', `${money(model.rates.cloudfront_per_1m_requests, 2)} / 1M`],
		['DynamoDB writes', `${money(model.rates.dynamodb_write_per_1m, 3)} / 1M`],
		['CloudWatch Logs', `${money(model.rates.logs_ingest_per_gb, 2)} / GB`],
	];

	return (
		<AnimatePresence>
			{open ? (
				<>
					<motion.button
						type="button"
						className="cost-panel__scrim"
						aria-label="Close cost analysis"
						onClick={onClose}
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						exit={{ opacity: 0 }}
						transition={{ duration: reduceMotion ? 0 : 0.18 }}
					/>
					<div className="cost-panel__layer">
					<motion.div
						className="cost-panel"
						role="dialog"
						aria-modal="true"
						aria-labelledby="cost-panel-title"
						ref={panelRef}
						initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.98 }}
						animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
						exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.99 }}
						transition={{ duration: reduceMotion ? 0 : 0.2 }}
					>
						<div className="cost-panel__head">
							<h2 id="cost-panel-title">
								What this costs to run <span className="cost-panel__tag">Estimate</span>
							</h2>
							<button
								type="button"
								className="cost-panel__close"
								onClick={onClose}
								aria-label="Close"
							>
								<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
									<path
										d="M6 6l12 12M18 6L6 18"
										stroke="currentColor"
										strokeWidth="2.2"
										strokeLinecap="round"
									/>
								</svg>
							</button>
						</div>

						<div className="cost-panel__body">
							<section className="cost-card">
								<h3>One student message</h3>
								<p className="cost-card__big">{money(per.total, 4)}</p>
								<p className="cost-card__sub">
									Measured over {count(model.measured.sample_questions)} real questions on{' '}
									{model.measuredAt}, at {model.measured.model_calls_avg} model calls and{' '}
									{count(per.inputTokens)} input tokens each.
								</p>

								<ul className="cost-rows">
									<li>
										<span className="cost-rows__k">
											Model, input <em>measured</em>
										</span>
										<span className="cost-rows__v">
											{smallMoney(
												(per.inputTokens / 1e6) * model.rates.generation_input_per_1m,
											)}
										</span>
									</li>
									<li>
										<span className="cost-rows__k">
											Model, output <em>measured</em>
										</span>
										<span className="cost-rows__v">
											{smallMoney(
												(per.outputTokens / 1e6) * model.rates.generation_output_per_1m,
											)}
										</span>
									</li>
									<li>
										<span className="cost-rows__k">
											Guardrail screen <em>measured</em>
										</span>
										<span className="cost-rows__v">{smallMoney(per.guardrail)}</span>
									</li>
									<li>
										<span className="cost-rows__k">
											Retrieval <em>measured</em>
										</span>
										<span className="cost-rows__v">{smallMoney(per.retrieval)}</span>
									</li>
									<li>
										<span className="cost-rows__k">
											Gateway, Lambda, storage <em className="cost-rows__priced">priced</em>
										</span>
										<span className="cost-rows__v">{smallMoney(per.plumbing)}</span>
									</li>
								</ul>

								<p className="cost-note">
									<strong>Retrieved pages are the cost.</strong> Input tokens are{' '}
									{Math.round(
										(((per.inputTokens / 1e6) * model.rates.generation_input_per_1m) /
											per.total) *
											100,
									)}
									% of a message, because every answer carries the campus pages it was
									grounded in. Each earlier turn a student has already sent adds only{' '}
									{money(depthAdder, 5)} more, since the server replays previous messages as
									text and never re-sends the pages behind them.
								</p>
							</section>

							<section className="cost-card">
								<h3>A month of use</h3>

								<label className="cost-slider__label" htmlFor="cost-messages">
									Student messages a month
									<output className="cost-slider__value" htmlFor="cost-messages">
										{count(messages)}
									</output>
								</label>
								<input
									id="cost-messages"
									className="cost-slider"
									type="range"
									min={MESSAGES_MIN}
									max={MESSAGES_MAX}
									step={MESSAGES_STEP}
									value={messages}
									onChange={(event) => setMessages(Number(event.target.value))}
								/>

								<div className="cost-figures">
									<div>
										<p className="cost-figures__label">Runs at zero use</p>
										<p className="cost-card__big cost-card__big--sm">
											{money(fixed.total, 2)}
										</p>
										<p className="cost-card__sub">Every month, nobody asking</p>
									</div>
									<div>
										<p className="cost-figures__label">Messages</p>
										<p className="cost-card__big cost-card__big--sm">{money(variable, 2)}</p>
										<p className="cost-card__sub">
											{count(messages)} &times; {money(per.total, 4)}
										</p>
									</div>
								</div>

								<p className="cost-total">
									<span>Monthly total</span>
									<strong>{money(fixed.total + variable, 2)}</strong>
								</p>

								<ul className="cost-rows">
									<li>
										<span className="cost-rows__k">Stored data (S3 + vector index)</span>
										<span className="cost-rows__v">{smallMoney(fixed.storage)}</span>
									</li>
									<li>
										<span className="cost-rows__k">Re-scrape compute</span>
										<span className="cost-rows__v">{smallMoney(fixed.scraper)}</span>
									</li>
									<li>
										<span className="cost-rows__k">Re-indexing changed pages</span>
										<span className="cost-rows__v">{smallMoney(fixed.reindex)}</span>
									</li>
									<li>
										<span className="cost-rows__k">CloudWatch logs</span>
										<span className="cost-rows__v">{smallMoney(fixed.logs)}</span>
									</li>
								</ul>

								<p className="cost-note">
									The floor is what runs whether or not anyone asks anything: stored bytes,
									and <strong>{fixed.runs.toFixed(1)} scraper runs a month</strong> (daily,
									plus {model.baseline.deploys_per_month} assumed deploys). Re-scraping is
									nearly free because a run whose pages have not changed re-indexes nothing
									&mdash; only about <strong>{fixed.ingests.toFixed(1)} re-indexes a month</strong>{' '}
									are assumed to actually happen. CloudFront, API Gateway and Lambda bill per
									use, so they contribute nothing at zero traffic.
								</p>

								<details className="cost-rates">
									<summary>
										Rates used &mdash; AWS list prices, {model.region}, as of {model.asOf}
									</summary>
									<table>
										<thead>
											<tr>
												<th>Item</th>
												<th>Rate</th>
											</tr>
										</thead>
										<tbody>
											{rateRows.map(([item, rate]) => (
												<tr key={item}>
													<td>{item}</td>
													<td>{rate}</td>
												</tr>
											))}
										</tbody>
									</table>
								</details>
							</section>
						</div>

						<p className="cost-panel__foot">
							<strong>These are estimates, not a bill.</strong> They are published AWS list
							prices multiplied by token usage measured against this deployment. No billing or
							account-spend data is read, which is also why these figures describe only this
							system and not anything else running in the same AWS account. Lines marked{' '}
							<em>priced</em> come from configured memory and real invocation durations rather
							than from measured tokens. Nothing here accounts for taxes, credits, free-tier
							allowances, or support plans.
						</p>
					</motion.div>
					</div>
				</>
			) : null}
		</AnimatePresence>
	);
}

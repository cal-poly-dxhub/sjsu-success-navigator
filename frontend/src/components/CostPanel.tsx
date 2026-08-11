import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
	NO_CONVERSATION_USAGE,
	conversationCost,
	count,
	fixedMonthly,
	money,
	perMessage,
} from '../lib/costModel';
import type { CostModel } from '../lib/runtimeConfig';
import type { ConversationUsage } from '../types/chat';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './CostPanel.css';

type CostPanelProps = {
	open: boolean;
	model: CostModel;
	/** What the conversation on screen has billed so far. Undefined until a reply arrives. */
	usage?: ConversationUsage;
	onClose: () => void;
};

/** Slider bounds. SJSU enrolls ~36,000 students, so the top of the range is a whole campus. */
const MESSAGES_MIN = 0;
const MESSAGES_MAX = 200_000;
const MESSAGES_STEP = 1_000;
const MESSAGES_DEFAULT = 20_000;

/**
 * The cost panel: what this conversation cost, and what a month of them would.
 *
 * THE LEFT HALF IS MEASURED, NOT MODELLED. It prices the tokens the server counted on the
 * turns this student actually sent (app/usage.py, reported on every reply), so it answers
 * "what did that cost" about the conversation in front of the reader. It used to show the
 * 24-question sample average under the heading "one student message", which was a figure
 * about a sample presented where a reader would read it as a figure about their chat.
 *
 * THE RIGHT HALF IS A PROJECTION, and it is one slider and three numbers: the total a
 * month at that volume costs, the floor it never drops below, and what one message adds.
 * The itemizations and the rate table came off deliberately - they were an audit of the
 * arithmetic sitting in the middle of a demo, and the arithmetic is in costModel.ts where
 * it can be read properly.
 */
export function CostPanel({ open, model, usage, onClose }: CostPanelProps) {
	const [messages, setMessages] = useState(MESSAGES_DEFAULT);
	const panelRef = useRef<HTMLDivElement | null>(null);
	const previousActiveRef = useRef<HTMLElement | null>(null);
	const reduceMotion = usePrefersReducedMotion();

	// Rates and the baseline never change while the page is open, so the floor and the
	// per-message price are computed once rather than on every slider frame.
	const per = useMemo(() => perMessage(model.rates, model.measured), [model]);
	const fixed = useMemo(() => fixedMonthly(model.rates, model.baseline), [model]);

	const variable = messages * per.total;

	const conversation = usage ?? NO_CONVERSATION_USAGE;
	const conversationTotal = conversationCost(model.rates, model.measured, conversation);

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
								<h3>This conversation</h3>
								<p className="cost-card__big">{money(conversationTotal, 4)}</p>
								<p className="cost-card__sub">
									{conversation.messages > 0
										? `${count(conversation.messages)} ${
												conversation.messages === 1 ? 'message' : 'messages'
											} so far, priced from the tokens they actually used.`
										: 'Nothing metered in this chat yet. It counts from the first message you send here.'}
								</p>

								<ul className="cost-rows">
									<li>
										<span className="cost-rows__k">Messages sent</span>
										<span className="cost-rows__v">{count(conversation.messages)}</span>
									</li>
									<li>
										{/* Not the same number as the one above, and that is the point: the
										    loop calls the model again after a search, and each call resends
										    everything before it. */}
										<span className="cost-rows__k">Model calls</span>
										<span className="cost-rows__v">{count(conversation.modelCalls)}</span>
									</li>
									<li>
										<span className="cost-rows__k">Input tokens</span>
										<span className="cost-rows__v">{count(conversation.inputTokens)}</span>
									</li>
									<li>
										<span className="cost-rows__k">Output tokens</span>
										<span className="cost-rows__v">{count(conversation.outputTokens)}</span>
									</li>
									<li>
										<span className="cost-rows__k">Cost per message</span>
										<span className="cost-rows__v">
											{conversation.messages > 0
												? money(conversationTotal / conversation.messages, 4)
												: '-'}
										</span>
									</li>
								</ul>
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

								<p className="cost-card__big">{money(fixed.total + variable, 2)}</p>
								<p className="cost-card__sub">A month at that volume</p>

								<div className="cost-figures">
									<div>
										<p className="cost-figures__label">Runs at no use</p>
										<p className="cost-card__big cost-card__big--sm">
											{money(fixed.total, 2)}
										</p>
										<p className="cost-card__sub">Every month, nobody asking</p>
									</div>
									<div>
										<p className="cost-figures__label">Cost per message</p>
										<p className="cost-card__big cost-card__big--sm">
											{money(per.total, 4)}
										</p>
										<p className="cost-card__sub">What one message adds</p>
									</div>
								</div>
							</section>
						</div>

						<p className="cost-panel__foot">
							<strong>These are estimates, not a bill.</strong> Published AWS list prices,
							multiplied by measured token use.
						</p>
					</motion.div>
					</div>
				</>
			) : null}
		</AnimatePresence>
	);
}

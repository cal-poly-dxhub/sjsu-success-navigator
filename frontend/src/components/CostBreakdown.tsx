import { useMemo, useState } from 'react';
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
import { useStrings } from '../lib/i18n';
import './CostBreakdown.css';

type CostBreakdownProps = {
	model: CostModel;
	/** What the conversation on screen has billed so far. Undefined until a reply arrives. */
	usage?: ConversationUsage;
};

/** Slider bounds. SJSU enrolls ~36,000 students, so the top of the range is a whole campus. */
const MESSAGES_MIN = 0;
const MESSAGES_MAX = 200_000;
const MESSAGES_STEP = 1_000;
const MESSAGES_DEFAULT = 20_000;

/** The cost breakdown: what this conversation cost, and what a month of them would. */
export function CostBreakdown({ model, usage }: CostBreakdownProps) {
	const t = useStrings();
	const [messages, setMessages] = useState(MESSAGES_DEFAULT);

	// Rates and the baseline never change while the page is open, so the floor and the per-
	// message price are computed once rather than on every slider frame.
	const per = useMemo(() => perMessage(model.rates, model.measured), [model]);
	const fixed = useMemo(() => fixedMonthly(model.rates, model.baseline), [model]);

	const variable = messages * per.total;

	const conversation = usage ?? NO_CONVERSATION_USAGE;
	const conversationTotal = conversationCost(model.rates, model.measured, conversation);

	return (
		<>
			<div className="cost-breakdown">
				<section className="cost-card">
					<h3>{t.costThisConversation}</h3>
					<p className="cost-card__big">{money(conversationTotal, 4)}</p>
					<p className="cost-card__sub">
						{conversation.messages > 0
							? t.costMessagesSoFar(count(conversation.messages), conversation.messages !== 1)
							: t.costNothingMetered}
					</p>

					<ul className="cost-rows">
						<li>
							<span className="cost-rows__k">{t.costMessagesSent}</span>
							<span className="cost-rows__v">{count(conversation.messages)}</span>
						</li>
						<li>
							{/* Not the number above: a second search is a second call, and each
							 * call resends everything. */}
							<span className="cost-rows__k">{t.costModelCalls}</span>
							<span className="cost-rows__v">{count(conversation.modelCalls)}</span>
						</li>
						<li>
							{/* Both models' tokens, because the row says what the conversation
							 * used. */}
							<span className="cost-rows__k">{t.costInputTokens}</span>
							<span className="cost-rows__v">
								{count(conversation.inputTokens + conversation.titleInputTokens)}
							</span>
						</li>
						<li>
							<span className="cost-rows__k">{t.costOutputTokens}</span>
							<span className="cost-rows__v">
								{count(conversation.outputTokens + conversation.titleOutputTokens)}
							</span>
						</li>
						<li>
							<span className="cost-rows__k">{t.costPerMessage}</span>
							<span className="cost-rows__v">
								{conversation.messages > 0
									? money(conversationTotal / conversation.messages, 4)
									: '-'}
							</span>
						</li>
					</ul>
				</section>

				<section className="cost-card">
					<h3>{t.costMonthOfUse}</h3>

					<label className="cost-slider__label" htmlFor="cost-messages">
						{t.costMessagesAMonth}
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
					<p className="cost-card__sub">{t.costMonthAtVolume}</p>

					<div className="cost-figures">
						<div>
							<p className="cost-figures__label">{t.costRunsAtNoUse}</p>
							<p className="cost-card__big cost-card__big--sm">{money(fixed.total, 2)}</p>
							<p className="cost-card__sub">{t.costNobodyAsking}</p>
						</div>
						<div>
							<p className="cost-figures__label">{t.costPerMessage}</p>
							<p className="cost-card__big cost-card__big--sm">{money(per.total, 4)}</p>
							<p className="cost-card__sub">{t.costWhatOneAdds}</p>
						</div>
					</div>
				</section>
			</div>

			<p className="cost-breakdown__foot">
				<strong>{t.costFootLead}</strong> {t.costFootRest}
			</p>
		</>
	);
}

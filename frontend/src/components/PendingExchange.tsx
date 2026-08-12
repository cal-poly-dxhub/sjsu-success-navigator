import { motion } from 'motion/react';
import { UserPrompt } from './UserPrompt';
import { ThinkingBubble } from './ThinkingBubble';
import { ConversationalBubble } from './ConversationalBubble';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './ConversationTurnView.css';

type PendingExchangeProps = {
	prompt: string;
	/**
	 * The reply so far, when it is arriving over a socket. Prose only, and a PREVIEW: the
	 * authoritative payload replaces this whole exchange when the turn finishes, cards and
	 * all. Empty on the buffered path, where there is nothing to show until the reply lands.
	 */
	preview?: string;
	/**
	 * What the server says it is doing while no text is arriving - "retrieving" today. It
	 * lets this say a true thing during the second or two of silence before the first token,
	 * rather than a dot animation that means nothing in particular.
	 */
	stage?: string | null;
};

/** What the status line says. Server stages are a closed set; anything else says nothing. */
const STAGE_LABELS: Record<string, string> = {
	retrieving: 'Looking through campus resources…',
};

export function PendingExchange({ prompt, preview = '', stage = null }: PendingExchangeProps) {
	const reduceMotion = usePrefersReducedMotion();
	const layoutTransition = reduceMotion
		? { duration: 0 }
		: { type: 'spring' as const, stiffness: 320, damping: 32 };

	const label = stage ? STAGE_LABELS[stage] : undefined;

	return (
		<motion.article
			className="conversation-exchange conversation-exchange--active conversation-exchange--pending"
			id="active-conversation-turn"
			layout={!reduceMotion ? 'position' : false}
			transition={layoutTransition}
			aria-busy="true"
			aria-label="Waiting for Sammy's response"
		>
			<UserPrompt text={prompt} />
			<div className="conversation-exchange__response conversation-exchange__response--pending">
				{preview ? (
					// Once prose is arriving it types in place, in the bubble the finished turn
					// will occupy. No intro delay: the student has been waiting already.
					<ConversationalBubble text={preview} introDelayMs={0} />
				) : (
					<div className="conversational-bubble-wrap">
						<div className="conversational-bubble">
							<ThinkingBubble />
						</div>
					</div>
				)}
				{!preview && label ? (
					<p className="conversation-exchange__stage" aria-live="polite">
						{label}
					</p>
				) : null}
			</div>
		</motion.article>
	);
}

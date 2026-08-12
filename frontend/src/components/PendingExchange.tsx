import { motion } from 'motion/react';
import { UserPrompt } from './UserPrompt';
import { ThinkingBubble } from './ThinkingBubble';
import { ConversationalBubble } from './ConversationalBubble';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { useStrings, type Strings } from '../lib/i18n';
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

/**
 * What the indicator says instead of "Thinking". Server stages are a closed set; anything
 * else leaves the default alone. No trailing ellipsis on any of these, in any language: the
 * indicator animates its own dots, and a stage that brought its own would read as two.
 *
 * A function of the catalogue rather than a constant, because the STAGE is the server's word
 * and the SENTENCE is ours: `retrieving` is a wire value that never gets translated, and what
 * the student reads about it does.
 */
const STAGE_LABELS = (t: Strings): Record<string, string> => ({
	retrieving: t.stageRetrieving,
});

export function PendingExchange({ prompt, preview = '', stage = null }: PendingExchangeProps) {
	const t = useStrings();
	const reduceMotion = usePrefersReducedMotion();
	const layoutTransition = reduceMotion
		? { duration: 0 }
		: { type: 'spring' as const, stiffness: 320, damping: 32 };

	const label = stage ? STAGE_LABELS(t)[stage] : undefined;

	return (
		<motion.article
			className="conversation-exchange conversation-exchange--active conversation-exchange--pending"
			id="active-conversation-turn"
			layout={!reduceMotion ? 'position' : false}
			transition={layoutTransition}
			aria-busy="true"
			aria-label={t.waitingForSammy}
		>
			<UserPrompt text={prompt} />
			<div className="conversation-exchange__response conversation-exchange__response--pending">
				{/*
				 * ONE BUBBLE FOR THE WHOLE WAIT. It opens holding the indicator and ends
				 * holding the reply: the prose types in place, where the finished turn's own
				 * bubble will be, instead of a second bubble arriving under the first. No
				 * intro delay - the student has been waiting already.
				 */}
				<ConversationalBubble
					text={preview}
					introDelayMs={0}
					placeholder={<ThinkingBubble label={label} />}
				/>
			</div>
		</motion.article>
	);
}

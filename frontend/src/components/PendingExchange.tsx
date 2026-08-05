import { motion } from 'motion/react';
import { UserPrompt } from './UserPrompt';
import { ThinkingBubble } from './ThinkingBubble';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './ConversationTurnView.css';

type PendingExchangeProps = {
	prompt: string;
};

export function PendingExchange({ prompt }: PendingExchangeProps) {
	const reduceMotion = usePrefersReducedMotion();
	const layoutTransition = reduceMotion
		? { duration: 0 }
		: { type: 'spring' as const, stiffness: 320, damping: 32 };

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
				<div className="conversational-bubble-wrap">
					<div className="conversational-bubble">
						<ThinkingBubble />
					</div>
				</div>
			</div>
		</motion.article>
	);
}

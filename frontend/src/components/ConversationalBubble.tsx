import { motion } from 'motion/react';
import { Typewriter } from './Typewriter';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './ConversationalBubble.css';

const BUBBLE_SPRING = { type: 'spring' as const, stiffness: 420, damping: 22 };
const BUBBLE_EASE = [0.34, 1.45, 0.64, 1] as const;

type ConversationalBubbleProps = {
	text: string;
	introDelayMs?: number;
	onTypingChange?: (typing: boolean) => void;
	onTypingComplete?: () => void;
};

/**
 * The prose. It arrives, it stays. Nothing replaces it and nothing scales it away - the
 * card group opens underneath it and the column grows.
 */
export function ConversationalBubble({
	text,
	introDelayMs = 0,
	onTypingChange,
	onTypingComplete,
}: ConversationalBubbleProps) {
	const reduceMotion = usePrefersReducedMotion();

	const bubbleMotion = reduceMotion
		? { initial: false as const, animate: { scale: 1, opacity: 1 } }
		: {
				initial: { scale: 0, opacity: 0 },
				animate: { scale: 1, opacity: 1 },
				transition: { ...BUBBLE_SPRING, ease: BUBBLE_EASE },
			};

	return (
		<div className="conversational-bubble-wrap">
			<motion.div className="conversational-bubble" {...bubbleMotion}>
				<Typewriter
					text={text}
					introDelayMs={introDelayMs}
					onTypingChange={onTypingChange}
					onComplete={onTypingComplete}
				/>
			</motion.div>
		</div>
	);
}

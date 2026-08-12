import type { ReactNode } from 'react';
import { motion } from 'motion/react';
import { Typewriter } from './Typewriter';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './ConversationalBubble.css';

const BUBBLE_SPRING = { type: 'spring' as const, stiffness: 420, damping: 22 };
const BUBBLE_EASE = [0.34, 1.45, 0.64, 1] as const;

type ConversationalBubbleProps = {
	text: string;
	introDelayMs?: number;
	/** Characters already typed out by a streamed preview - see Typewriter's `startAt`. */
	startAt?: number;
	/**
	 * What the bubble holds until there is prose to type: the live indicator. It lives IN
	 * here, and that is what makes the first token a change of contents rather than a second
	 * bubble - the element that animates is mounted once, when the exchange opens.
	 */
	placeholder?: ReactNode;
	/**
	 * Whether this bubble is appearing for the first time.
	 *
	 * False when it is taking over from one already on screen - the finished turn replacing
	 * the preview it was just streaming - where the entrance would replay over prose the
	 * student has already read and look like the reply had reloaded. The turn is still
	 * arriving either way; what this says is that its bubble has already made its entrance.
	 */
	animateIn?: boolean;
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
	startAt = 0,
	placeholder = null,
	animateIn = true,
	onTypingChange,
	onTypingComplete,
}: ConversationalBubbleProps) {
	const reduceMotion = usePrefersReducedMotion();

	const bubbleMotion =
		reduceMotion || !animateIn
			? { initial: false as const, animate: { scale: 1, opacity: 1 } }
			: {
					initial: { scale: 0, opacity: 0 },
					animate: { scale: 1, opacity: 1 },
					transition: { ...BUBBLE_SPRING, ease: BUBBLE_EASE },
				};

	return (
		<div className="conversational-bubble-wrap">
			<motion.div className="conversational-bubble" {...bubbleMotion}>
				{text ? (
					<Typewriter
						text={text}
						introDelayMs={introDelayMs}
						startAt={startAt}
						onTypingChange={onTypingChange}
						onComplete={onTypingComplete}
					/>
				) : (
					placeholder
				)}
			</motion.div>
		</div>
	);
}

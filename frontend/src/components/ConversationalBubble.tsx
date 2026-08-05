import { motion } from 'motion/react';
import { Typewriter } from './Typewriter';
import { PulseFab } from './PulseFab';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './ConversationalBubble.css';

const BUBBLE_SPRING = { type: 'spring' as const, stiffness: 420, damping: 22 };
const BUBBLE_EASE = [0.34, 1.45, 0.64, 1] as const;

type ConversationalBubbleProps = {
	text: string;
	introDelayMs?: number;
	active?: boolean;
	showFab?: boolean;
	exiting?: boolean;
	onTypingChange?: (typing: boolean) => void;
	onTypingComplete?: () => void;
	onContinue?: () => void;
};

export function ConversationalBubble({
	text,
	introDelayMs = 0,
	active = true,
	showFab = false,
	exiting = false,
	onTypingChange,
	onTypingComplete,
	onContinue,
}: ConversationalBubbleProps) {
	const reduceMotion = usePrefersReducedMotion();

	if (!active && !exiting) {
		return null;
	}

	const bubbleMotion = reduceMotion
		? { initial: false, animate: { scale: exiting ? 0 : 1, opacity: exiting ? 0 : 1 } }
		: {
				initial: { scale: 0, opacity: 0 },
				animate: {
					scale: exiting ? 0 : 1,
					opacity: exiting ? 0 : 1,
				},
				transition: exiting
					? { duration: 0.28, ease: [0.4, 0, 1, 1] }
					: { ...BUBBLE_SPRING, ease: BUBBLE_EASE },
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
			{showFab && !exiting && onContinue ? (
				<motion.div
					className="conversational-bubble__fab"
					initial={reduceMotion ? false : { scale: 0, opacity: 0 }}
					animate={{ scale: 1, opacity: 1 }}
					transition={
						reduceMotion
							? { duration: 0 }
							: { ...BUBBLE_SPRING, delay: 0.08, ease: BUBBLE_EASE }
					}
				>
					<PulseFab onClick={onContinue} ariaLabel="View campus resources" />
				</motion.div>
			) : null}
		</div>
	);
}

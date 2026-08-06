import { motion } from 'motion/react';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './TalkToPersonPill.css';

type TalkToPersonPillProps = {
	onClick: () => void;
};

/**
 * Deliberately not a PressableButton: the handoff to a human is the one control on the page
 * that stands for an SJSU office rather than for the assistant, so it carries its own
 * identity (blue seal, gold ring, SJSU Cares attribution) instead of the app's button styling.
 */
export function TalkToPersonPill({ onClick }: TalkToPersonPillProps) {
	const reduceMotion = usePrefersReducedMotion();

	return (
		<motion.button
			type="button"
			className="talk-pill"
			onClick={onClick}
			aria-label="Talk to a person at SJSU Cares"
			whileTap={reduceMotion ? undefined : { scale: 0.97 }}
			transition={{ type: 'spring', stiffness: 600, damping: 28 }}
		>
			<span className="talk-pill__seal" aria-hidden="true">
				<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" focusable="false">
					<path d="M12 12a4.2 4.2 0 1 0 0-8.4 4.2 4.2 0 0 0 0 8.4Zm0 1.8c-3.7 0-7.2 1.9-7.2 4.3v1.5c0 .5.4.8.9.8h12.6c.5 0 .9-.3.9-.8v-1.5c0-2.4-3.5-4.3-7.2-4.3Z" />
				</svg>
			</span>
			<span className="talk-pill__copy">
				<span className="talk-pill__kicker">SJSU Cares</span>
				<span className="talk-pill__label">Talk to a person</span>
			</span>
		</motion.button>
	);
}

import { useEffect, useState } from 'react';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { useStrings } from '../lib/i18n';
import './ThinkingBubble.css';

const THINKING_MAX_DOTS = 6;
const THINKING_DOT_COUNT = THINKING_MAX_DOTS + 1;
const THINKING_FRAME_MS = 420;

type ThinkingBubbleProps = {
	/**
	 * What the indicator says. Defaults to the model working; when the server says what it
	 * is doing instead, that REPLACES this in the same slot rather than arriving as a second
	 * line under the bubble. One indicator, whose words change.
	 *
	 * Undefined rather than a literal default, because the default is a translated string
	 * and the catalogue is only readable from inside the render.
	 */
	label?: string;
	/**
	 * Wear it small, under a bubble that already holds prose.
	 *
	 * Same words, same dots, same announcement - the chrome comes off because a second
	 * speech bubble below the first would read as Sammy saying two things, where this is
	 * one thing still being said.
	 */
	inline?: boolean;
};

export function ThinkingBubble({ label, inline = false }: ThinkingBubbleProps) {
	const t = useStrings();
	const reduceMotion = usePrefersReducedMotion();
	const [dotCount, setDotCount] = useState(0);

	useEffect(() => {
		if (reduceMotion) return;
		const intervalId = window.setInterval(() => {
			setDotCount((current) => (current + 1) % THINKING_DOT_COUNT);
		}, THINKING_FRAME_MS);
		return () => window.clearInterval(intervalId);
	}, [reduceMotion]);

	const dots = '.'.repeat(reduceMotion ? THINKING_MAX_DOTS : dotCount);

	return (
		// Polite rather than hidden: this is the only place the server's account of what it
		// is doing now appears, and it used to be announced by the status line this replaces.
		// The dots stay hidden, so a screen reader is not read a new sentence every 420ms.
		<div
			className={`thinking-bubble${inline ? ' thinking-bubble--inline' : ''}`}
			aria-live="polite"
		>
			<p className="thinking-bubble__label">
				{label ?? t.thinking}
				<span className="thinking-bubble__dots" aria-hidden="true">
					{dots}
				</span>
			</p>
		</div>
	);
}

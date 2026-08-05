import { useEffect, useState } from 'react';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './ThinkingBubble.css';

const THINKING_MAX_DOTS = 6;
const THINKING_DOT_COUNT = THINKING_MAX_DOTS + 1;
const THINKING_FRAME_MS = 420;

export function ThinkingBubble() {
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
		<div className="thinking-bubble" aria-hidden="true">
			<p className="thinking-bubble__label">
				Thinking
				<span className="thinking-bubble__dots" aria-hidden="true">
					{dots}
				</span>
			</p>
		</div>
	);
}

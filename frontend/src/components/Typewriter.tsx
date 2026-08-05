import { useEffect, useRef, useState } from 'react';
import { FormattedMessage } from './FormattedMessage';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './Typewriter.css';

export const TYPEWRITER_CPS = 108;

type TypewriterProps = {
	text: string;
	cps?: number;
	/** Quiet beat before intro typing begins (ms). */
	introDelayMs?: number;
	onTypingChange?: (typing: boolean) => void;
	onComplete?: () => void;
};

export function Typewriter({
	text,
	cps = TYPEWRITER_CPS,
	introDelayMs = 1100,
	onTypingChange,
	onComplete,
}: TypewriterProps) {
	const reduceMotion = usePrefersReducedMotion();
	const [visible, setVisible] = useState('');
	const [waiting, setWaiting] = useState(true);
	const completeRef = useRef(onComplete);
	const typingRef = useRef(onTypingChange);
	completeRef.current = onComplete;
	typingRef.current = onTypingChange;

	useEffect(() => {
		let intervalId: number | undefined;
		let delayId: number | undefined;

		const cleanup = () => {
			if (intervalId !== undefined) window.clearInterval(intervalId);
			if (delayId !== undefined) window.clearTimeout(delayId);
			typingRef.current?.(false);
		};

		if (!text) {
			setVisible('');
			setWaiting(false);
			typingRef.current?.(false);
			return cleanup;
		}

		if (reduceMotion) {
			setWaiting(false);
			setVisible(text);
			typingRef.current?.(false);
			completeRef.current?.();
			return cleanup;
		}

		setVisible('');
		setWaiting(true);
		typingRef.current?.(false);

		delayId = window.setTimeout(() => {
			setWaiting(false);
			typingRef.current?.(true);
			let i = 0;
			const intervalMs = Math.max(8, Math.round(1000 / cps));
			intervalId = window.setInterval(() => {
				i += 1;
				setVisible(text.slice(0, i));
				if (i >= text.length) {
					if (intervalId !== undefined) window.clearInterval(intervalId);
					typingRef.current?.(false);
					completeRef.current?.();
				}
			}, intervalMs);
		}, Math.max(0, introDelayMs));

		return cleanup;
	}, [text, cps, introDelayMs, reduceMotion]);

	return (
		<div
			className={`speech-bubble${waiting ? ' speech-bubble--waiting' : ''}`}
			aria-live="polite"
			aria-hidden={waiting}
		>
			<div className="speech-bubble__text">
				<FormattedMessage text={visible} trimPartial />
				{!waiting && visible.length < text.length ? (
					<span className="speech-bubble__caret" aria-hidden="true" />
				) : null}
			</div>
			<span className="speech-bubble__tail" aria-hidden="true" />
		</div>
	);
}

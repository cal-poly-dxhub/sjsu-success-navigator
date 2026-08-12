import { useEffect, useMemo, useRef, useState } from 'react';
import { FormattedMessage } from './FormattedMessage';
import { renderedLength } from '../lib/messageFormat';
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
	/**
	 * The count is of RENDERED characters, not of the source string, and that is the whole
	 * reason the reveal is a number rather than a growing slice of `text`. Typing a slice
	 * puts the model's own markup on screen: a lone asterisk, then a second one, then the
	 * pair vanishing as the run closes and the words behind it turn bold. Here the message
	 * is parsed whole and then uncovered, so what arrives is already formatted - bold text
	 * types in bold, and a bullet's marker is there before its first word.
	 */
	const [revealed, setRevealed] = useState(0);
	const [waiting, setWaiting] = useState(true);
	const total = useMemo(() => renderedLength(text), [text]);
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

		// Nothing to type covers both the empty string and text that renders as no
		// characters at all, which would otherwise start an interval with no end condition.
		if (!text || !total) {
			setRevealed(0);
			setWaiting(false);
			typingRef.current?.(false);
			return cleanup;
		}

		if (reduceMotion) {
			setWaiting(false);
			setRevealed(total);
			typingRef.current?.(false);
			completeRef.current?.();
			return cleanup;
		}

		setRevealed(0);
		setWaiting(true);
		typingRef.current?.(false);

		delayId = window.setTimeout(() => {
			setWaiting(false);
			typingRef.current?.(true);
			let i = 0;
			const intervalMs = Math.max(8, Math.round(1000 / cps));
			intervalId = window.setInterval(() => {
				i += 1;
				setRevealed(i);
				if (i >= total) {
					if (intervalId !== undefined) window.clearInterval(intervalId);
					typingRef.current?.(false);
					completeRef.current?.();
				}
			}, intervalMs);
		}, Math.max(0, introDelayMs));

		return cleanup;
	}, [text, total, cps, introDelayMs, reduceMotion]);

	return (
		<div
			className={`speech-bubble${waiting ? ' speech-bubble--waiting' : ''}`}
			aria-live="polite"
			aria-hidden={waiting}
		>
			<div className="speech-bubble__text">
				<FormattedMessage text={text} reveal={revealed} />
				{!waiting && revealed < total ? (
					<span className="speech-bubble__caret" aria-hidden="true" />
				) : null}
			</div>
			<span className="speech-bubble__tail" aria-hidden="true" />
		</div>
	);
}

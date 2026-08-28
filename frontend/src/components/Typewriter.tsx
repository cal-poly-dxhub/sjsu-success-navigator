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
	/** Characters to treat as already on screen. */
	startAt?: number;
	onTypingChange?: (typing: boolean) => void;
	onComplete?: () => void;
};

export function Typewriter({
	text,
	cps = TYPEWRITER_CPS,
	introDelayMs = 1100,
	startAt = 0,
	onTypingChange,
	onComplete,
}: TypewriterProps) {
	const reduceMotion = usePrefersReducedMotion();
	/** The count is of rendered characters, not of the source string, and that is the whole
	 * reason the reveal is a number rather than a growing slice of `text`. */
	const [revealed, setRevealed] = useState(startAt);
	const [waiting, setWaiting] = useState(startAt === 0);
	const total = useMemo(() => renderedLength(text), [text]);
	const completeRef = useRef(onComplete);
	const typingRef = useRef(onTypingChange);
	completeRef.current = onComplete;
	typingRef.current = onTypingChange;

	/** How far the reveal has got, readable without making the effect depend on it. */
	const revealedRef = useRef(revealed);
	revealedRef.current = revealed;

	/** Bumped when the text is replaced rather than extended. */
	const previousText = useRef(text);
	const [generation, setGeneration] = useState(0);
	useEffect(() => {
		const grew = text.startsWith(previousText.current);
		previousText.current = text;
		if (!grew) {
			setRevealed(0);
			setGeneration((current) => current + 1);
		}
	}, [text]);

	// The quiet beat before typing begins is observed once per message, not again every time a
	// delta extends it.
	const introObserved = useRef(startAt > 0);
	useEffect(() => {
		introObserved.current = startAt > 0;
	}, [generation, startAt]);

	useEffect(() => {
		let intervalId: number | undefined;
		let delayId: number | undefined;

		const cleanup = () => {
			if (intervalId !== undefined) window.clearInterval(intervalId);
			if (delayId !== undefined) window.clearTimeout(delayId);
		};

		// Nothing to type covers both the empty string and text that renders as no characters
		// at all, which would otherwise start an interval with no end condition.
		if (!text || !total) {
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

		// Already caught up, so no interval starts. The next delta re-runs this with more text.
		if (revealedRef.current >= total) {
			setWaiting(false);
			typingRef.current?.(false);
			completeRef.current?.();
			return cleanup;
		}

		const run = () => {
			introObserved.current = true;
			setWaiting(false);
			typingRef.current?.(true);
			intervalId = window.setInterval(() => {
				setRevealed((current) => {
					const next = current + 1;
					if (next >= total) {
						window.clearInterval(intervalId);
						intervalId = undefined;
						typingRef.current?.(false);
						completeRef.current?.();
					}
					return Math.min(next, total);
				});
			}, Math.max(8, Math.round(1000 / cps)));
		};

		if (introObserved.current) {
			run();
		} else {
			setWaiting(true);
			typingRef.current?.(false);
			delayId = window.setTimeout(run, Math.max(0, introDelayMs));
		}

		return cleanup;
	}, [generation, total, cps, introDelayMs, reduceMotion, text]);

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

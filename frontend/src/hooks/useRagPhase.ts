import { useCallback, useEffect, useRef, useState } from 'react';
import type { RagPhase } from '../types/chat';

const STACK_PAUSE_MS = 1000;
const BUBBLE_EXIT_MS = 280;

type UseRagPhaseOptions = {
	phase: RagPhase | 'done';
	cardCount: number;
	reduceMotion?: boolean;
	onRequestPhase: (phase: RagPhase | 'done') => void;
};

export function useRagPhase({
	phase,
	cardCount,
	reduceMotion = false,
	onRequestPhase,
}: UseRagPhaseOptions) {
	const [typingComplete, setTypingComplete] = useState(false);
	const [bubbleExiting, setBubbleExiting] = useState(false);
	const [frontIndex, setFrontIndex] = useState(0);
	const timerRef = useRef<number | undefined>(undefined);

	const clearTimer = useCallback(() => {
		if (timerRef.current !== undefined) {
			window.clearTimeout(timerRef.current);
			timerRef.current = undefined;
		}
	}, []);

	useEffect(() => {
		if (phase === 'scroll') {
			setFrontIndex(0);
		}
	}, [phase]);

	const markTypingComplete = useCallback(() => {
		setTypingComplete(true);
	}, []);

	const advanceFromConversational = useCallback(() => {
		if (cardCount === 0) return;
		if (reduceMotion) {
			onRequestPhase('grid');
			return;
		}
		setBubbleExiting(true);
		timerRef.current = window.setTimeout(() => {
			setBubbleExiting(false);
			onRequestPhase('scroll');
		}, BUBBLE_EXIT_MS);
	}, [cardCount, reduceMotion, onRequestPhase]);

	useEffect(() => {
		if (phase !== 'scroll' || cardCount === 0) return;

		clearTimer();

		if (reduceMotion) {
			onRequestPhase('grid');
			return;
		}

		const isLast = frontIndex >= cardCount - 1;

		timerRef.current = window.setTimeout(() => {
			if (isLast) {
				onRequestPhase('grid');
				return;
			}
			setFrontIndex((index) => index + 1);
		}, STACK_PAUSE_MS);

		return clearTimer;
	}, [phase, frontIndex, cardCount, reduceMotion, clearTimer, onRequestPhase]);

	useEffect(() => clearTimer, [clearTimer]);

	const progressStep = phase === 'grid' ? cardCount : frontIndex + 1;
	const progressLabel =
		phase === 'grid' ? `All ${cardCount}` : `${frontIndex + 1} / ${cardCount}`;
	const progressRatio = cardCount > 0 ? progressStep / cardCount : 0;

	return {
		typingComplete,
		bubbleExiting,
		frontIndex,
		progressStep,
		progressLabel,
		progressRatio,
		stackLandMs: 400,
		markTypingComplete,
		advanceFromConversational,
	};
}

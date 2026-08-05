import { useCallback, useEffect, useRef } from 'react';
import { motion } from 'motion/react';
import { FormattedMessage } from './FormattedMessage';
import type { ConversationTurn, RagPhase } from '../types/chat';
import { useRagPhase } from '../hooks/useRagPhase';
import { ConversationalBubble } from './ConversationalBubble';
import { CardStackAnimator } from './CardStackAnimator';
import { RagGrid, RagProgress } from './StatementStack';
import { SafetyHandoff } from './SafetyHandoff';
import { UserPrompt } from './UserPrompt';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './ConversationTurnView.css';

type ConversationTurnViewProps = {
	turn: ConversationTurn;
	isActive: boolean;
	introDelayMs?: number;
	onTypingChange?: (typing: boolean) => void;
	onPhaseChange: (turnId: string, phase: RagPhase | 'done') => void;
	onFollowup: (prompt: string) => void;
};

export function ConversationTurnView({
	turn,
	isActive,
	introDelayMs = 0,
	onTypingChange,
	onPhaseChange,
	onFollowup,
}: ConversationTurnViewProps) {
	const reduceMotion = usePrefersReducedMotion();
	const hasRag = turn.cards.length > 0;
	const hasSafety = Boolean(turn.safetyHandoff);

	const handleRequestPhase = useCallback(
		(phase: RagPhase | 'done') => {
			onPhaseChange(turn.id, phase);
		},
		[onPhaseChange, turn.id],
	);

	const rag = useRagPhase({
		phase: isActive ? turn.phase : turn.phase === 'done' ? 'done' : turn.phase,
		cardCount: turn.cards.length,
		reduceMotion,
		onRequestPhase: isActive && hasRag ? handleRequestPhase : () => {},
	});

	const phase = turn.phase;
	const prevPhaseRef = useRef(phase);
	const animateGridIn = prevPhaseRef.current === 'scroll' && phase === 'grid';

	useEffect(() => {
		prevPhaseRef.current = phase;
	}, [phase]);

	const showArchivedGrid = !isActive && hasRag && phase === 'done';
	const showActiveGrid = isActive && hasRag && phase === 'grid';
	const showScroll = isActive && hasRag && phase === 'scroll';
	const showConversational =
		isActive && !hasSafety && hasRag && (phase === 'conversational' || rag.bubbleExiting);
	const showTalkBubble = isActive && !hasRag && !hasSafety;
	const staticBubble = !isActive && !hasRag && !hasSafety;

	const layoutTransition = reduceMotion
		? { duration: 0 }
		: { type: 'spring' as const, stiffness: 320, damping: 32 };

	return (
		<motion.article
			className={`conversation-exchange${isActive ? ' conversation-exchange--active' : ' conversation-exchange--archived'}`}
			id={isActive ? 'active-conversation-turn' : undefined}
			layout={!reduceMotion ? 'position' : false}
			transition={layoutTransition}
		>
			{turn.query ? <UserPrompt text={turn.query} /> : null}

			<div className="conversation-exchange__response">
				{showArchivedGrid ? (
					<RagGrid
						cards={turn.cards}
						onFollowup={onFollowup}
						createdAt={turn.createdAt}
						archived
					/>
				) : null}

				{staticBubble ? (
					<div className="conversation-turn__static-bubble">
						<FormattedMessage text={turn.text} />
					</div>
				) : null}

				{hasSafety && turn.safetyHandoff ? (
					<div className="conversation-turn__safety">
						{isActive ? (
							<ConversationalBubble
								text={turn.text}
								introDelayMs={introDelayMs}
								active
								onTypingChange={onTypingChange}
							/>
						) : (
							<div className="conversation-turn__static-bubble">
								<FormattedMessage text={turn.text} />
							</div>
						)}
						<SafetyHandoff handoff={turn.safetyHandoff} />
					</div>
				) : null}

				{showConversational ? (
					<ConversationalBubble
						text={turn.text}
						introDelayMs={introDelayMs}
						active
						exiting={rag.bubbleExiting}
						showFab={rag.typingComplete}
						onTypingChange={onTypingChange}
						onTypingComplete={rag.markTypingComplete}
						onContinue={rag.advanceFromConversational}
					/>
				) : null}

				{showTalkBubble ? (
					<ConversationalBubble
						text={turn.text}
						introDelayMs={introDelayMs}
						active
						onTypingChange={onTypingChange}
					/>
				) : null}

				{showScroll ? (
					<div className="conversation-turn__scroll">
						<RagProgress
							label={rag.progressLabel}
							ratio={rag.progressRatio}
							total={turn.cards.length}
							step={rag.progressStep}
						/>
						<CardStackAnimator
							cards={turn.cards}
							frontIndex={rag.frontIndex}
							landDurationMs={rag.stackLandMs}
							onFollowup={onFollowup}
						/>
					</div>
				) : null}

				{showActiveGrid ? (
					<RagGrid
						cards={turn.cards}
						onFollowup={onFollowup}
						createdAt={turn.createdAt}
						animateIn={animateGridIn && !reduceMotion}
					/>
				) : null}
			</div>
		</motion.article>
	);
}

import { useCallback, useEffect, useRef } from 'react';
import { motion } from 'motion/react';
import { FormattedMessage } from './FormattedMessage';
import type { ConversationTurn, RagPhase } from '../types/chat';
import { ConversationalBubble } from './ConversationalBubble';
import { scrollElementToTop } from '../lib/scrollAnchor';
import { RagGrid } from './StatementStack';
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
	const phase = turn.phase;
	const cardGroupRef = useRef<HTMLDivElement>(null);
	const anchoredRef = useRef(false);

	/**
	 * The prose finishing typing is what brings the cards out - there is no reveal button
	 * and nothing for the student to press. The prose stays exactly where it is; the
	 * column grows underneath it.
	 */
	const revealCards = useCallback(() => {
		if (!isActive || !hasRag || phase !== 'conversational') return;
		onPhaseChange(turn.id, 'grid');
	}, [isActive, hasRag, phase, onPhaseChange, turn.id]);

	// A turn with cards but no prose has nothing to finish typing, so nothing would ever
	// call revealCards. The contract says prose is never empty; this is the belt.
	useEffect(() => {
		if (turn.text.trim()) return;
		revealCards();
	}, [turn.text, revealCards]);

	const showCards = hasRag && (isActive ? phase === 'grid' : true);

	// Anchor to the top of the card group the first time it appears, once. Re-anchoring on
	// every render would fight the student's own scrolling.
	useEffect(() => {
		if (!showCards || !isActive || anchoredRef.current) return;
		const group = cardGroupRef.current;
		if (!group) return;
		anchoredRef.current = true;
		window.requestAnimationFrame(() => scrollElementToTop(group, reduceMotion));
	}, [showCards, isActive, reduceMotion]);

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
				{turn.text && isActive ? (
					<ConversationalBubble
						text={turn.text}
						introDelayMs={introDelayMs}
						onTypingChange={onTypingChange}
						onTypingComplete={revealCards}
					/>
				) : null}

				{turn.text && !isActive ? (
					<div className="conversation-turn__static-bubble">
						<FormattedMessage text={turn.text} />
					</div>
				) : null}

				{/* Safety is deterministic and never choreographed: it is on screen, whole,
				    the moment the turn renders. */}
				{turn.safetyHandoff ? <SafetyHandoff handoff={turn.safetyHandoff} /> : null}

				{showCards ? (
					<div className="conversation-turn__cards" ref={cardGroupRef}>
						<RagGrid
							cards={turn.cards}
							onFollowup={onFollowup}
							createdAt={turn.createdAt}
							deal={isActive}
							archived={!isActive}
						/>
					</div>
				) : null}
			</div>
		</motion.article>
	);
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { FormattedMessage } from './FormattedMessage';
import type { ConversationTurn, RagPhase } from '../types/chat';
import { ConversationalBubble } from './ConversationalBubble';
import { dealDurationMs } from './CardDeck';
import { scrollElementToTop } from '../lib/scrollAnchor';
import { RagGrid } from './StatementStack';
import { EscalationDraft } from './EscalationDraft';
import { PlaceCard } from './PlaceCard';
import { SafetyHandoff } from './SafetyHandoff';
import { UserPrompt } from './UserPrompt';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './ConversationTurnView.css';

type ConversationTurnViewProps = {
	turn: ConversationTurn;
	isActive: boolean;
	/** Whether this turn is taking the pending exchange's place, and therefore inherits a bubble
	 * that is already on screen rather than opening one. */
	continuesPending?: boolean;
	introDelayMs?: number;
	onTypingChange?: (typing: boolean) => void;
	onPhaseChange: (turnId: string, phase: RagPhase | 'done') => void;
	onFollowup: (prompt: string) => void;
	/** Whether this deployment configured a recipient, see ConversationFeed. */
	escalationEnabled?: boolean;
};

export function ConversationTurnView({
	turn,
	isActive,
	continuesPending = false,
	introDelayMs = 0,
	onTypingChange,
	onPhaseChange,
	onFollowup,
	escalationEnabled = false,
}: ConversationTurnViewProps) {
	const reduceMotion = usePrefersReducedMotion();
	const hasRag = turn.cards.length > 0;
	const phase = turn.phase;
	const cardGroupRef = useRef<HTMLDivElement>(null);
	const anchoredRef = useRef(false);
	const trailingText = turn.trailingText?.trim() ? turn.trailingText : '';
	/** Two gates, and the second one is the belt. */
	const escalation =
		escalationEnabled && !turn.safetyHandoff ? turn.escalation : undefined;
	/** The same belt, one gate shorter. */
	const place = turn.safetyHandoff ? undefined : turn.place;

	/** Is this turn arriving, and therefore the one thing in this component that decides whether
	 * anything animates. */
	const isArriving = isActive && turn.live;

	// The deal is over for anything not currently arriving, so nothing waits out a timer for
	// cards already on the table.
	const [dealt, setDealt] = useState(!isArriving || reduceMotion);

	/** The prose finishing typing is what brings the cards out, there is no reveal button and
	 * nothing for the student to press. */
	const revealCards = useCallback(() => {
		if (!isArriving || !hasRag || phase !== 'conversational') return;
		onPhaseChange(turn.id, 'grid');
	}, [isArriving, hasRag, phase, onPhaseChange, turn.id]);

	// A turn with cards but no prose has nothing to finish typing, so nothing would ever call
	// revealCards.
	useEffect(() => {
		if (turn.text.trim()) return;
		revealCards();
	}, [turn.text, revealCards]);

	// A turn that is not arriving shows its cards outright.
	const showCards = hasRag && (isArriving ? phase === 'grid' : true);

	// The prose under the cards waits for the group to finish landing, so the turn reads top to
	// bottom at one pace: lead-in, cards, then the question about them.
	useEffect(() => {
		if (dealt || !showCards) return;
		const timer = window.setTimeout(() => setDealt(true), dealDurationMs(turn.cards.length));
		return () => window.clearTimeout(timer);
	}, [dealt, showCards, turn.cards.length]);

	// With no group to sit under there is nothing to wait for.
	const showTrailing = Boolean(trailingText) && (!hasRag || (showCards && dealt));

	// The offer waits for what the trailing prose waits for: a draft appearing under a landing
	// group is the one reflow the deal exists to avoid.
	const showEscalation = Boolean(escalation) && (!hasRag || (showCards && dealt));

	// The location waits on exactly what the draft waits on, and for the same reason: a panel
	// appearing under a group still in the air is the reflow the deal was built to avoid.
	const showPlace = Boolean(place) && (!hasRag || (showCards && dealt));

	// Anchor to the top of the card group the first time it appears, once.
	useEffect(() => {
		if (!showCards || !isActive || anchoredRef.current) return;
		const group = cardGroupRef.current;
		if (!group) return;
		anchoredRef.current = true;
		window.requestAnimationFrame(() => scrollElementToTop(group, reduceMotion));
	}, [showCards, isActive, reduceMotion]);

	/* No layout animation on the exchange, and its absence is the fix for the previous answer
	 * flying up the screen once the feed reached its cap. */
	return (
		<article
			className={`conversation-exchange${isActive ? ' conversation-exchange--active' : ' conversation-exchange--archived'}`}
			id={isActive ? 'active-conversation-turn' : undefined}
		>
			{turn.query ? <UserPrompt text={turn.query} /> : null}

			<div className="conversation-exchange__response">
				{turn.text && isArriving ? (
					<ConversationalBubble
						text={turn.text}
						// A previewed turn has been on screen for seconds, so it resumes rather
						// than replays.
						introDelayMs={turn.revealedChars ? 0 : introDelayMs}
						startAt={turn.revealedChars ?? 0}
						// The same bubble the student has been watching, so it does not open a
						// second time.
						animateIn={!continuesPending}
						onTypingChange={onTypingChange}
						onTypingComplete={revealCards}
					/>
				) : null}

				{turn.text && !isArriving ? (
					<div className="conversation-turn__static-bubble">
						<FormattedMessage text={turn.text} />
					</div>
				) : null}

				{/* Safety is deterministic and never choreographed: it is on screen, whole, the
				 * moment the turn renders. */}
				{turn.safetyHandoff ? <SafetyHandoff handoff={turn.safetyHandoff} /> : null}

				{showCards ? (
					<div className="conversation-turn__cards" ref={cardGroupRef}>
						<RagGrid
							cards={turn.cards}
							onFollowup={onFollowup}
							createdAt={turn.createdAt}
							deal={isArriving}
							onLanded={() => setDealt(true)}
							archived={!isActive}
						/>
					</div>
				) : null}

				{/* Directly under the cards that named it: part of the answer, so above the
				 * offer, not below. */}
				{showPlace && place ? (
					<div className="conversation-turn__place">
						<PlaceCard place={place} />
					</div>
				) : null}

				{/* Prose the model wrote after its cards, rendered after them.  */}
				{/* Under everything: the prose, the cards it names, and any closing question. */}
				{showEscalation && escalation ? (
					<div className="conversation-turn__escalation">
						<EscalationDraft draft={escalation} />
					</div>
				) : null}

				{showTrailing ? (
					<div className="conversation-turn__trailing">
						{isArriving ? (
							<ConversationalBubble
								text={trailingText}
								introDelayMs={0}
								onTypingChange={onTypingChange}
							/>
						) : (
							<div className="conversation-turn__static-bubble">
								<FormattedMessage text={trailingText} />
							</div>
						)}
					</div>
				) : null}
			</div>
		</article>
	);
}

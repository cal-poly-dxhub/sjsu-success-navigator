import { useCallback, useEffect, useRef, useState } from 'react';
import { motion } from 'motion/react';
import { FormattedMessage } from './FormattedMessage';
import type { ConversationTurn, RagPhase } from '../types/chat';
import { ConversationalBubble } from './ConversationalBubble';
import { dealDurationMs } from './CardDeck';
import { scrollElementToTop } from '../lib/scrollAnchor';
import { RagGrid } from './StatementStack';
import { EscalationDraft } from './EscalationDraft';
import { SafetyHandoff } from './SafetyHandoff';
import { UserPrompt } from './UserPrompt';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './ConversationTurnView.css';

type ConversationTurnViewProps = {
	turn: ConversationTurn;
	isActive: boolean;
	/**
	 * Whether this turn is taking the pending exchange's place, and therefore inherits a
	 * bubble that is already on screen rather than opening one. See ConversationalBubble's
	 * `animateIn`.
	 */
	continuesPending?: boolean;
	introDelayMs?: number;
	onTypingChange?: (typing: boolean) => void;
	onPhaseChange: (turnId: string, phase: RagPhase | 'done') => void;
	onFollowup: (prompt: string) => void;
	/** Whether this deployment configured a recipient - see ConversationFeed. */
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
	/**
	 * TWO GATES, AND THE SECOND ONE IS THE BELT. The server does not put a draft on a turn
	 * in a deployment with no recipient, and it never puts one on a safety turn - but this
	 * component is the last thing between a stored draft and a student's screen, so it
	 * checks both rather than trusting a payload to have been built by today's server. The
	 * safety half is the one worth being certain about: a draft under a contact panel puts a
	 * message somebody has to write, and then wait on, between them and a number that
	 * answers now.
	 */
	const escalation =
		escalationEnabled && !turn.safetyHandoff ? turn.escalation : undefined;

	/**
	 * IS THIS TURN ARRIVING, and therefore the one thing in this component that decides
	 * whether anything animates.
	 *
	 * Two conditions, and both are needed. `turn.live` is the turn's own account of where it
	 * came from: a reply the browser just received, or a finished one read back from storage
	 * or from the sidebar's cache. `isActive` is position - only the newest turn in the feed
	 * is ever mid-arrival, everything above it has been overtaken.
	 *
	 * The bug this replaces was reading position ALONE. Being the newest turn in a
	 * conversation the student just reopened is not the same as being a new turn, so every
	 * reopened conversation re-typed its last answer and re-dealt its cards - a performance
	 * of something that finished days ago.
	 */
	const isArriving = isActive && turn.live;

	// The deal is over for anything that is not currently arriving, so an archived turn, a
	// reopened one and a reduced-motion one all start here rather than waiting out a timer
	// for cards that are already on the table.
	const [dealt, setDealt] = useState(!isArriving || reduceMotion);

	/**
	 * The prose finishing typing is what brings the cards out - there is no reveal button
	 * and nothing for the student to press. The prose stays exactly where it is; the
	 * column grows underneath it.
	 */
	const revealCards = useCallback(() => {
		if (!isArriving || !hasRag || phase !== 'conversational') return;
		onPhaseChange(turn.id, 'grid');
	}, [isArriving, hasRag, phase, onPhaseChange, turn.id]);

	// A turn with cards but no prose has nothing to finish typing, so nothing would ever
	// call revealCards. The contract says prose is never empty; this is the belt.
	useEffect(() => {
		if (turn.text.trim()) return;
		revealCards();
	}, [turn.text, revealCards]);

	// A turn that is not arriving shows its cards outright. Only a live turn withholds them,
	// and only until its prose has finished typing - waiting on a phase transition that will
	// never come is how a reopened conversation loses the card group under its last answer.
	const showCards = hasRag && (isArriving ? phase === 'grid' : true);

	// The prose under the cards waits for the group to finish landing, so the turn reads
	// top to bottom at one pace: lead-in, cards, then the question about them. It also
	// keeps the entrance transform-only - a bubble growing under cards still in the air is
	// the reflow the deal was built to avoid.
	useEffect(() => {
		if (dealt || !showCards) return;
		const timer = window.setTimeout(() => setDealt(true), dealDurationMs(turn.cards.length));
		return () => window.clearTimeout(timer);
	}, [dealt, showCards, turn.cards.length]);

	// With no group to sit under there is nothing to wait for. That state is unreachable
	// from the server, which only splits a reply around cards, but rendering the text is
	// the right answer to being handed it anyway: prose is never dropped.
	const showTrailing = Boolean(trailingText) && (!hasRag || (showCards && dealt));

	// The offer is the last thing in the turn, so it waits for the same thing the trailing
	// prose waits for: a draft appearing while cards are still in the air would be the one
	// reflow under a landing group that the deal was built to avoid. An archived or reopened
	// turn has nothing in flight and shows it immediately.
	const showEscalation = Boolean(escalation) && (!hasRag || (showCards && dealt));

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
				{turn.text && isArriving ? (
					<ConversationalBubble
						text={turn.text}
						// A turn that was previewed over the socket has already been on screen
						// for seconds, so it observes no opening beat and resumes where the
						// preview stopped instead of replaying it.
						introDelayMs={turn.revealedChars ? 0 : introDelayMs}
						startAt={turn.revealedChars ?? 0}
						// The same reasoning for the bubble as for the prose inside it: it is
						// the same bubble the student has been watching, so it does not open
						// a second time.
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

				{/* Safety is deterministic and never choreographed: it is on screen, whole,
				    the moment the turn renders. */}
				{turn.safetyHandoff ? <SafetyHandoff handoff={turn.safetyHandoff} /> : null}

				{showCards ? (
					<div className="conversation-turn__cards" ref={cardGroupRef}>
						<RagGrid
							cards={turn.cards}
							onFollowup={onFollowup}
							createdAt={turn.createdAt}
							deal={isArriving}
							archived={!isActive}
						/>
					</div>
				) : null}

				{/* Prose the model wrote after its cards, rendered after them. Same two
				    treatments as the lead-in: typed while the turn is arriving, static
				    once it is finished. */}
				{/* Under everything: the prose, the cards it names, and any closing question.
				    A message to a person is what is left when the pages did not do it, so it
				    reads last rather than competing with the answer above it. */}
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
		</motion.article>
	);
}

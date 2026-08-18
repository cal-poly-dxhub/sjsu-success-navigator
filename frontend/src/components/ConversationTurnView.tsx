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
	 * The same belt, one gate shorter. There is no deploy switch on the location catalogue -
	 * it is a table in the server's own code, not an address somebody configures - so the
	 * only check left is the one that matters: a turn carrying a contact panel shows nothing
	 * else. The server drops it before this component sees it; this is the last thing
	 * between a stored card and the screen, and a map above a crisis number would put an
	 * errand in front of a call.
	 */
	const place = turn.safetyHandoff ? undefined : turn.place;

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
	//
	// THE DECK REPORTS ITS OWN LANDING and this timer is the backstop, because a flight is
	// sized by a distance measured inside the group: the timer can only work from the card
	// count and the longest flight a card could have (dealDurationMs), which is an upper
	// bound and would leave a beat of dead air on most turns. Whichever arrives first wins,
	// so a deck that never reports - a dropped animation event - still ends the wait.
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

	// The location waits on exactly what the draft waits on, and for the same reason: a panel
	// appearing under a group still in the air is the reflow the deal was built to avoid.
	const showPlace = Boolean(place) && (!hasRag || (showCards && dealt));

	// Anchor to the top of the card group the first time it appears, once. Re-anchoring on
	// every render would fight the student's own scrolling.
	useEffect(() => {
		if (!showCards || !isActive || anchoredRef.current) return;
		const group = cardGroupRef.current;
		if (!group) return;
		anchoredRef.current = true;
		window.requestAnimationFrame(() => scrollElementToTop(group, reduceMotion));
	}, [showCards, isActive, reduceMotion]);

	/*
	 * NO LAYOUT ANIMATION ON THE EXCHANGE, and its absence is the fix for the previous
	 * answer flying up the screen once the feed reached its cap.
	 *
	 * This was a `motion.article` with `layout="position"` on the avatar column's spring.
	 * The only position change it ever saw was a turn LEAVING THE HEAD of the feed -
	 * `appendConversationTurn` slices to MAX_FEED_TURNS - and that happens in the same
	 * commit that applies the reply, so it landed on the frame the deal starts.
	 *
	 * That change is not one to animate. Dropping a turn shortens the document ABOVE the
	 * viewport and the browser clamps scrollY by exactly the same amount, so nothing moves
	 * on screen. Motion measures in PAGE coordinates, so it sees every surviving article
	 * move up by the dropped article's outer height, and holds each one at its old page
	 * position before springing it home. Measured in Chrome at 180, 1510 and 1199px over
	 * ~35 frames, the translate matching the scroll clamp to the pixel every time: on
	 * screen, the previous answer teleporting into the middle of the viewport underneath
	 * the cards being dealt, then jetting out through the top.
	 *
	 * Nothing is lost with it gone. Across seven turns those head-drops were the ONLY
	 * layout events these articles ever produced, and each one was a change the browser
	 * had already cancelled.
	 */
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
							onLanded={() => setDealt(true)}
							archived={!isActive}
						/>
					</div>
				) : null}

				{/* Where to go, directly under the cards that named it: it is part of the
				    answer, not an afterthought to it, so it sits above the offer to write to
				    a person rather than under it. */}
				{showPlace && place ? (
					<div className="conversation-turn__place">
						<PlaceCard place={place} />
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
		</article>
	);
}

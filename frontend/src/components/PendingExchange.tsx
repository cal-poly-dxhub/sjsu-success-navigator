import { useEffect, useRef, useState } from 'react';
import { UserPrompt } from './UserPrompt';
import { CardDeckPlaceholder } from './CardDeck';
import { ThinkingBubble } from './ThinkingBubble';
import { ConversationalBubble } from './ConversationalBubble';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { scrollElementToTop } from '../lib/scrollAnchor';
import { useStrings, type Strings } from '../lib/i18n';
import './ConversationTurnView.css';

type PendingExchangeProps = {
	prompt: string;
	/** The reply so far, while it is still arriving. */
	preview?: string;
	/** What the server says it is doing while no text is arriving, "retrieving" today. */
	stage?: string | null;
};

/** The stage the server sends once the model has begun writing card blocks (app/streaming.py,
 * CARDS_STAGE). */
export const CARDS_STAGE = 'composing_cards';

/** What the indicator says instead of "Thinking". */
const STAGE_LABELS = (t: Strings): Record<string, string> => ({
	retrieving: t.stageRetrieving,
	[CARDS_STAGE]: t.stageComposingCards,
});

export function PendingExchange({ prompt, preview = '', stage = null }: PendingExchangeProps) {
	const t = useStrings();
	const reduceMotion = usePrefersReducedMotion();

	const label = stage ? STAGE_LABELS(t)[stage] : undefined;

	/** Whether the bubble is mid-sentence. */
	const [typing, setTyping] = useState(false);

	/** The silent window: the lead-in has finished typing and the cards are still being written. */
	const awaitingCards = stage === CARDS_STAGE && Boolean(preview) && !typing;

	/** Anchor to the deck the once, when it appears. */
	const deckRef = useRef<HTMLDivElement>(null);
	const anchoredRef = useRef(false);
	useEffect(() => {
		if (!awaitingCards || anchoredRef.current) return;
		const deck = deckRef.current;
		if (!deck) return;
		anchoredRef.current = true;
		window.requestAnimationFrame(() => scrollElementToTop(deck, reduceMotion));
	}, [awaitingCards, reduceMotion]);

	return (
		// No layout animation: all it measured was a turn leaving the head of the feed, which
		// the browser's own scroll clamp already absorbs.
		<article
			className="conversation-exchange conversation-exchange--active conversation-exchange--pending"
			id="active-conversation-turn"
			aria-busy="true"
			aria-label={t.waitingForSammy}
		>
			<UserPrompt text={prompt} />
			<div className="conversation-exchange__response conversation-exchange__response--pending">
				{/* ONE BUBBLE FOR THE WHOLE WAIT.  */}
				<ConversationalBubble
					text={preview}
					introDelayMs={0}
					onTypingChange={setTyping}
					placeholder={<ThinkingBubble label={label} />}
				/>
				{/* THE DECK SAYS IT, so nothing needs to be written.  */}
				{awaitingCards ? (
					<div className="waiting-deck-row" ref={deckRef}>
						<CardDeckPlaceholder />
					</div>
				) : null}
			</div>
		</article>
	);
}

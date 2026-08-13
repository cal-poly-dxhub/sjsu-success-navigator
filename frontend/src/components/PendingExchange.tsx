import { useEffect, useRef, useState } from 'react';
import { motion } from 'motion/react';
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
	/**
	 * The reply so far, when it is arriving over a socket. Prose only, and a PREVIEW: the
	 * authoritative payload replaces this whole exchange when the turn finishes, cards and
	 * all. Empty on the buffered path, where there is nothing to show until the reply lands.
	 */
	preview?: string;
	/**
	 * What the server says it is doing while no text is arriving - "retrieving" today. It
	 * lets this say a true thing during the second or two of silence before the first token,
	 * rather than a dot animation that means nothing in particular.
	 */
	stage?: string | null;
};

/**
 * The stage the server sends once the model has begun writing card blocks (app/streaming.py,
 * CARDS_STAGE). It is the ONE thing that knows cards are coming while the student can see
 * nothing at all, and the two ends of this string are a wire contract.
 */
export const CARDS_STAGE = 'composing_cards';

/**
 * What the indicator says instead of "Thinking". Server stages are a closed set; anything
 * else leaves the default alone. No trailing ellipsis on any of these, in any language: the
 * indicator animates its own dots, and a stage that brought its own would read as two.
 *
 * A function of the catalogue rather than a constant, because the STAGE is the server's word
 * and the SENTENCE is ours: `retrieving` is a wire value that never gets translated, and what
 * the student reads about it does.
 */
const STAGE_LABELS = (t: Strings): Record<string, string> => ({
	retrieving: t.stageRetrieving,
	[CARDS_STAGE]: t.stageComposingCards,
});

export function PendingExchange({ prompt, preview = '', stage = null }: PendingExchangeProps) {
	const t = useStrings();
	const reduceMotion = usePrefersReducedMotion();
	const layoutTransition = reduceMotion
		? { duration: 0 }
		: { type: 'spring' as const, stiffness: 320, damping: 32 };

	const label = stage ? STAGE_LABELS(t)[stage] : undefined;

	/**
	 * Whether the bubble is mid-sentence. Read off the typewriter rather than timed, because
	 * the only question below is whether there is still prose arriving on screen.
	 */
	const [typing, setTyping] = useState(false);

	/**
	 * THE SILENT WINDOW, and the whole reason this indicator exists: the lead-in has finished
	 * typing, the cards are still being written, and until now the screen said nothing while
	 * the student waited to find out whether anything else was coming.
	 *
	 * Three conditions and each one closes a different way of lying. THE STAGE is the
	 * server's, sent when `<card` appeared in the model's own output, so this can never
	 * promise resources to a reply that has none - roughly one in ten - and it is not a
	 * timer, so it cannot fire on a turn that is simply slow. PROSE ON SCREEN keeps it out
	 * of the pre-text wait, which already has its own indicator inside the bubble. NOT
	 * TYPING keeps it out of the reply itself: the deltas stop at the first tag, so a bubble
	 * that has caught up has nothing further to say.
	 *
	 * It clears by unmounting: this whole exchange is replaced by the finished turn the
	 * moment the payload lands, and that turn's prose has already been typed, so its cards
	 * are revealed in the render that follows.
	 */
	const awaitingCards = stage === CARDS_STAGE && Boolean(preview) && !typing;

	/**
	 * Anchor to the deck the once, when it appears.
	 *
	 * The finished turn anchors to its card group the same way (ConversationTurnView), and
	 * that group lands at exactly this position - so doing it HERE, while the deck is still
	 * waiting, leaves the turn's own anchor with nowhere to scroll, and the hand-off no
	 * longer drags the page out from under the stack. That was the largest of the three
	 * things making that moment jump.
	 */
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
		<motion.article
			className="conversation-exchange conversation-exchange--active conversation-exchange--pending"
			id="active-conversation-turn"
			layout={!reduceMotion ? 'position' : false}
			transition={layoutTransition}
			aria-busy="true"
			aria-label={t.waitingForSammy}
		>
			<UserPrompt text={prompt} />
			<div className="conversation-exchange__response conversation-exchange__response--pending">
				{/*
				 * ONE BUBBLE FOR THE WHOLE WAIT. It opens holding the indicator and ends
				 * holding the reply: the prose types in place, where the finished turn's own
				 * bubble will be, instead of a second bubble arriving under the first. No
				 * intro delay - the student has been waiting already.
				 */}
				<ConversationalBubble
					text={preview}
					introDelayMs={0}
					onTypingChange={setTyping}
					placeholder={<ThinkingBubble label={label} />}
				/>
				{awaitingCards ? (
					<div className="waiting-deck-row" ref={deckRef}>
						<CardDeckPlaceholder />
						<ThinkingBubble label={t.stageComposingCards} inline />
					</div>
				) : null}
			</div>
		</motion.article>
	);
}

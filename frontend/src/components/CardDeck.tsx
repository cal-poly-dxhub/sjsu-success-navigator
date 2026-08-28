import {
	useCallback,
	useEffect,
	useLayoutEffect,
	useRef,
	useState,
	type CSSProperties,
} from 'react';
import { motion } from 'motion/react';
import type { StatementCard as StatementCardData } from '../types/chat';
import { StatementCard } from './StatementCard';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { waitingDeck } from '../lib/waitingDeck';
import { deckTuning } from '../lib/deckTuning';
import './CardDeck.css';

/** Entrance budget: last card lands at (n, 1) * Stagger + its own flight. */

/** Ease-out, but not a quintic one. */
const DEAL_EASE = [0.33, 1, 0.68, 1] as const;

/** How long one card's flight takes, from how far it has to go. */
function flightDurationS(distancePx: number): number {
	return Math.min(
		deckTuning.dealMaxDurationS,
		Math.max(deckTuning.dealMinDurationS, distancePx / deckTuning.dealSpeedPxS),
	);
}

/** When the last card is certainly down, in ms. */
export function dealDurationMs(cardCount: number): number {
	if (cardCount <= 0) return 0;
	return Math.round(((cardCount - 1) * deckTuning.dealStaggerS + deckTuning.dealMaxDurationS) * 1000);
}


/** A card whose body runs past this is long and takes its whole grid row rather than one track. */
const WIDE_CARD_MIN_CHARS = 280;

/** How many card objects the waiting deck holds. */
const WAITING_DECK_DEPTH = 4;

/** The cycle: one card nudges out of the stack and back, then the next one up, then a rest. */

type DeckPose = { y: number; lean: number };

/** The compress: the deck shedding the cards the reply turned out not to need. */

/** Ease in and out. The card leaves from rest and comes back to rest. */
function easeInOut(t: number): number {
	return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

/** Out and back: 0 at both ends, 1 in the middle. The lean, and the idle nudge, share it. */
function outAndBack(t: number): number {
	return t < 0.5 ? easeInOut(t * 2) : 1 - easeInOut((t - 0.5) * 2);
}

/** Where every card is during the compress, and whether it has finished. */
function compressFrame(elapsed: number, target: number): { poses: DeckPose[]; done: boolean } {
	const poses: DeckPose[] = Array.from({ length: WAITING_DECK_DEPTH }, (_, index) => ({
		y: index * deckTuning.deckStepPx,
		lean: 0,
	}));

	// Bottom of the stack first, working up to the last card the reply does not need.
	const surplus: number[] = [];
	for (let index = WAITING_DECK_DEPTH - 1; index >= target; index--) surplus.push(index);

	for (const [order, index] of surplus.entries()) {
		const through = (elapsed - order * deckTuning.rippleStaggerMs) / deckTuning.rippleMs;
		const t = Math.min(1, Math.max(0, through));
		poses[index].y = index * deckTuning.deckStepPx * (1 - easeInOut(t));
		poses[index].lean = deckTuning.cycleTiltDeg * outAndBack(t);
	}

	const rippleMs = surplus.length ? (surplus.length - 1) * deckTuning.rippleStaggerMs + deckTuning.rippleMs : 0;
	return { poses, done: elapsed >= rippleMs + deckTuning.compressSettleMs };
}

/** Where every card is at a given moment, and whether any of it is moving. */
function deckFrame(elapsed: number): { poses: DeckPose[]; moving: boolean } {
	const beatMs = deckTuning.cyclePopMs + deckTuning.cyclePauseMs;
	const beat = Math.floor(elapsed / beatMs);
	const through = Math.min(1, Math.max(0, (elapsed % beatMs) / deckTuning.cyclePopMs));

	// Bottom slot first, working up through every card, then round again.
	const mover = WAITING_DECK_DEPTH - 1 - (beat % WAITING_DECK_DEPTH);

	// Out and back, each half eased off its own end, so it comes to a stop before returning.
	const out = outAndBack(through);

	const poses = Array.from({ length: WAITING_DECK_DEPTH }, (_, index) => ({
		y: index * deckTuning.deckStepPx + (index === mover ? deckTuning.cyclePopPx * out : 0),
		lean: index === mover ? deckTuning.cycleTiltDeg * out : 0,
	}));
	return { poses, moving: through < 1 };
}

/** A card withheld: the silhouette of a real one, redacted into a mosaic. */
function CardGhost() {
	return (
		<div className="card-ghost" aria-hidden="true">
			{/* Counter-scales the deck's own squash, so everything inside is written in real
			 * pixels. */}
			<div className="card-ghost__inner">
				<span className="card-ghost__bar card-ghost__title" />
				<span className="card-ghost__bar card-ghost__line" />
				<span className="card-ghost__bar card-ghost__line card-ghost__line--short" />
				{/* Empty, and no `card-ghost__bar` on them: a control is not a line of type, and
				 * a label drawn on a button is a placeholder on top of a placeholder. */}
				<div className="card-ghost__actions">
					<span className="card-ghost__button card-ghost__button--source" />
					<span className="card-ghost__button card-ghost__button--followup" />
				</div>
			</div>
		</div>
	);
}

/** The deck itself, cycling, while the model is still writing the cards. */
export function CardDeckPlaceholder() {
	const reduceMotion = usePrefersReducedMotion();
	const deckRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		const deck = deckRef.current;
		if (!deck) return;
		const cards = Array.from(deck.children) as HTMLElement[];

		const write = (poses: DeckPose[]) => {
			cards.forEach((card, index) => {
				const pose = poses[index];
				card.style.transform =
					`translate3d(0, ${pose.y.toFixed(2)}px, 0) rotate(${pose.lean.toFixed(2)}deg)`;
			});
		};

		// Reduced motion gets the deck standing square and never moving, which is the same
		// posture the grid itself takes: present, not performed.
		if (reduceMotion) {
			write(deckFrame(0).poses);
			return;
		}

		waitingDeck.attach(WAITING_DECK_DEPTH);
		const started = performance.now();
		let frame = 0;
		let stopped = false;
		/** When the compress began, and to what. Null until the reply says how many it needs. */
		let compressAt: number | null = null;
		let compressTo: number | null = null;

		/* Three states, and the loop runs until the deck unmounts rather than stopping at the
		 * first of them. */
		const tick = () => {
			if (stopped) return;
			const now = performance.now();

			// 1. Compressing. Started once, on the frame the count first appears.
			const asked = waitingDeck.compressTarget();
			if (asked !== null && compressTo === null) {
				compressTo = asked;
				compressAt = now;
			}
			if (compressTo !== null) {
				const { poses, done } = compressFrame(now - (compressAt ?? now), compressTo);
				write(poses);
				if (done) {
					// Square at the new count: report it, then hold, because the real deck is
					// next.
					compressTo = null;
					waitingDeck.compressDone();
				}
				frame = window.requestAnimationFrame(tick);
				return;
			}

			// 2. Held: settled and square, waiting to be told the count. Nothing to paint.
			if (!waitingDeck.isHeld()) {
				// 3. Idle: the cycle, on its own clock.
				const { poses, moving } = deckFrame(now - started);
				write(poses);
				if (moving) waitingDeck.beginMove();
				else waitingDeck.endMove();
			}
			frame = window.requestAnimationFrame(tick);
		};
		frame = window.requestAnimationFrame(tick);

		return () => {
			stopped = true;
			window.cancelAnimationFrame(frame);
			waitingDeck.detach();
		};
	}, [reduceMotion]);

	return (
		<div className="waiting-deck" ref={deckRef} aria-hidden="true">
			{Array.from({ length: WAITING_DECK_DEPTH }, (_, index) => (
				// Depth is fixed, no card ever changes its place in the stack, so it is set
				// once here rather than written every frame.
				<div
					key={index}
					className="waiting-deck__card"
					style={{ zIndex: WAITING_DECK_DEPTH - index }}
				>
					{/* Only the card on top is ever seen; the rest are edges behind it. */}
					{index === 0 ? <CardGhost /> : null}
				</div>
			))}
		</div>
	);
}

/** Where a card sits while it is still in the deck: the translation back to the stack, and the
 * scale that makes its full-size box present as one card of that stack. */
type DeckOffset = { x: number; y: number; sx: number; sy: number; deckWidth: number };

/** Grid placement for each card, as a 1-based start line for `grid-column: <start> / -1`, or
 * null for ordinary auto placement in one track. */
function computeColumnStarts(bodyLengths: number[], columns: number): (number | null)[] {
	const starts: (number | null)[] = bodyLengths.map(() => null);
	if (columns <= 1 || !bodyLengths.some((length) => length >= WIDE_CARD_MIN_CHARS)) {
		return starts;
	}

	let column = 0;
	bodyLengths.forEach((length, index) => {
		if (length >= WIDE_CARD_MIN_CHARS) {
			if (column > 0) starts[index - 1] = column;
			starts[index] = 1;
			column = 0;
		} else {
			column = (column + 1) % columns;
		}
	});
	if (column > 0 && bodyLengths.length > 1) starts[bodyLengths.length - 1] = column;

	return starts;
}

type CardDeckProps = {
	cards: StatementCardData[];
	onFollowup: (prompt: string) => void;
	/** Deal from the deck once, on mount. */
	deal?: boolean;
	/** Called once the last card is down. See `handleLanded`. */
	onLanded?: () => void;
};

/** The card group: a grid whose items are sized by their own content, entered by dealing off a
 * deck. */
export function CardDeck({ cards, onFollowup, deal = false, onLanded }: CardDeckProps) {
	const reduceMotion = usePrefersReducedMotion();
	const dealing = deal && !reduceMotion && cards.length > 0;
	const gridRef = useRef<HTMLDivElement>(null);
	const measuredRef = useRef(false);
	const [offsets, setOffsets] = useState<DeckOffset[] | null>(null);
	const [landed, setLanded] = useState(false);
	const [columns, setColumns] = useState(1);
	/** Whether the cards have started leaving the stack. */
	const [dealStarted, setDealStarted] = useState(false);
	useEffect(() => {
		if (!dealing || !offsets || dealStarted) return;
		const frame = window.requestAnimationFrame(() => setDealStarted(true));
		return () => window.cancelAnimationFrame(frame);
	}, [dealing, offsets, dealStarted]);

	// Reported rather than timed: a flight is sized by a distance only this component measures.
	const handleLanded = useCallback(() => {
		setLanded(true);
		onLanded?.();
	}, [onLanded]);

	// How many tracks the auto-fill grid actually resolved to, kept current across resizes.
	useLayoutEffect(() => {
		const grid = gridRef.current;
		if (!grid) return;

		const read = () =>
			setColumns(getComputedStyle(grid).gridTemplateColumns.split(' ').length);
		read();
		const observer = new ResizeObserver(read);
		observer.observe(grid);
		return () => observer.disconnect();
	}, []);

	useLayoutEffect(() => {
		// Measured once: a re-render mid-flight would restart the entrance from mid-air.
		if (!dealing || measuredRef.current) return;
		const grid = gridRef.current;
		if (!grid) return;

		// The offsets must be measured against the spanned layout.
		if (getComputedStyle(grid).gridTemplateColumns.split(' ').length !== columns) {
			return;
		}

		const items = Array.from(grid.children) as HTMLElement[];
		const first = items[0]?.getBoundingClientRect();
		if (!first) return;

		// One track wide, not one slot: the first card's slot can span the whole row, which
		// would make the stack twice the width the waiting deck stood at.
		const gridRect = grid.getBoundingClientRect();
		const trackWidth =
			parseFloat(getComputedStyle(grid).gridTemplateColumns.split(' ')[0]) || gridRect.width;
		const deck = {
			left: gridRect.left,
			top: first.top,
			width: trackWidth,
		};

		measuredRef.current = true;
		setOffsets(
			items.map((item) => {
				const rect = item.getBoundingClientRect();
				return {
					// Centre to centre, top to top, because that is where the item's transform-
					// origin sits.
					x: deck.left + deck.width / 2 - (rect.left + rect.width / 2),
					y: deck.top - rect.top,
					// One track wide and a deck's height tall, whatever size this card finishes
					// at.
					sx: rect.width ? deck.width / rect.width : 1,
					sy: rect.height ? deckTuning.waitingDeckHeightPx / rect.height : 1,
					deckWidth: deck.width,
				};
			}),
		);
	}, [dealing, columns]);

	const columnStarts = computeColumnStarts(
		cards.map((card) => card.body.length),
		columns,
	);

	const inFlight = dealing && !landed;

	/** Whether a card may lift under the pointer yet. */
	const [hoverArmed, setHoverArmed] = useState(false);
	useEffect(() => {
		if (inFlight) return;
		setHoverArmed(false);
		const arm = () => setHoverArmed(true);
		window.addEventListener('pointermove', arm, { once: true, passive: true });
		return () => window.removeEventListener('pointermove', arm);
	}, [inFlight]);

	return (
		<div
			ref={gridRef}
			className={`card-deck${inFlight ? ' card-deck--dealing' : ''}${
				hoverArmed ? ' card-deck--hover-armed' : ''
			}`}
			data-dealing={inFlight ? 'true' : undefined}
		>
			{cards.map((card, index) => {
				const offset = offsets?.[index];
				const start = columnStarts[index];
				const gridColumn = start === null ? undefined : `${start} / -1`;
				// Dealt from the bottom.
				const dealDelay = (cards.length - 1 - index) * deckTuning.dealStaggerS;
				const flight = offset ? flightDurationS(Math.hypot(offset.x, offset.y)) : 0;

				/* The turn over, as three numbers the two faces and the rotation all read from,
				 * rather than three copies of the same arithmetic that drift apart. */
				const flipDelay = dealDelay + flight * deckTuning.flipStartFraction;
				const flipDuration = Math.max(
					deckTuning.flipMinS,
					flight * deckTuning.flipDurationFraction,
				);

				// First pass with `dealing` renders the finished layout so it can be measured.
				if (dealing && !offset) {
					return (
						<div
							key={card.id}
							className="card-deck__item"
							style={{ visibility: 'hidden', gridColumn }}
						>
							<StatementCard card={card} onFollowup={onFollowup} />
						</div>
					);
				}

				if (!offset) {
					return (
						<div key={card.id} className="card-deck__item" style={{ gridColumn }}>
							<StatementCard card={card} onFollowup={onFollowup} />
						</div>
					);
				}

				// Face down and deck-sized: the two are the same object as far as the student
				// is concerned.
				const inDeck = {
					x: offset.x,
					// No lift off the first slot, and no lean.
					y: offset.y + index * deckTuning.deckStepPx,
					scaleX: offset.sx,
					scaleY: offset.sy,
					rotateY: 180,
				};

				return (
					<motion.div
						key={card.id}
						className="card-deck__item"
						style={{ zIndex: landed ? undefined : cards.length - index, gridColumn }}
						initial={inDeck}
						// Held in the stack until the deal begins, which is its own decision,
						// see `dealStarted`.
						animate={
							dealStarted
								? { x: 0, y: 0, scaleX: 1, scaleY: 1, rotateY: 0 }
								: inDeck
						}
						transition={{
							// This card's own flight, sized by its own distance.
							duration: flight,
							ease: DEAL_EASE,
							delay: dealDelay,
							// The turn over, in the back half of the flight.
							rotateY: {
								duration: flipDuration,
								ease: 'easeInOut',
								delay: flipDelay,
							},
						}}
						onAnimationComplete={
							/* The card on top is dealt last, so its landing is the group's, but
							 * only once the deal has actually started. */
							index === 0 && dealStarted ? handleLanded : undefined
						}
					>
						{/* THE TWO FACES.  */}
						<div
							className="card-deck__back"
							aria-hidden="true"
							style={
								{
									'--deck-sx': offset.sx,
									'--deck-sy': offset.sy,
									'--deck-w': `${offset.deckWidth}px`,
									'--deck-h': `${deckTuning.waitingDeckHeightPx}px`,
								} as CSSProperties
							}
						>
							<CardGhost />
						</div>
						<div className="card-deck__front">
							<StatementCard card={card} onFollowup={onFollowup} />
						</div>
					</motion.div>
				);
			})}
		</div>
	);
}

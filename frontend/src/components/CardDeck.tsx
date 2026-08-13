import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { motion } from 'motion/react';
import type { StatementCard as StatementCardData } from '../types/chat';
import { StatementCard } from './StatementCard';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { waitingDeck } from '../lib/waitingDeck';
import './CardDeck.css';

/**
 * Entrance budget: last card lands at (n - 1) * stagger + its own flight. At the card
 * ceiling of four that is ~1.53s.
 *
 * The stagger is paced against the PROSE, not against itself. The bubble types in at a
 * reading speed and the cards used to arrive at 0.1s apart, so the turn changed gear
 * halfway through: unhurried above, dealt out below. 0.34s is one card per beat, close
 * enough to the typing cadence that the whole turn reads as one rhythm.
 *
 * EVERY CARD FLIES FROM THE SAME DECK, so they do not fly the same distance - the first
 * card is already in its slot and the last crosses the whole group. One fixed duration for
 * all of them therefore meant one SPEED for each of them, rising with the index: measured
 * at a 1440px viewport, 270 / 2316 / 5151 / 6878 px per second across four cards. The deal
 * accelerated as it went and the last card was a blur, which is what "the cards enter
 * really quick" was. Duration is derived from the distance instead, so every card travels
 * at about the same speed and the group lands at one pace.
 *
 * The floor keeps a short hop from being over before it registers; the ceiling keeps the
 * longest flight from dragging, and is what `dealDurationMs` can bound the group by without
 * knowing any distances.
 */
const DEAL_STAGGER_S = 0.34;
const DEAL_SPEED_PX_S = 1150;
// The floor is what the card on TOP gets: it does not travel at all, so this is the time
// its turn-over has to read in.
const DEAL_MIN_DURATION_S = 0.46;
const DEAL_MAX_DURATION_S = 0.62;

/**
 * Ease-out, but not a quintic one. `[0.22, 1, 0.36, 1]` put ~70% of the distance into the
 * first quarter of the flight, so a long card left the deck at a speed nothing else in the
 * turn moves at and then crawled the rest of the way. This is the cubic: the same settling
 * shape, opening at roughly half the velocity.
 */
const DEAL_EASE = [0.33, 1, 0.68, 1] as const;

/**
 * The beat between the deck standing square and the first card leaving it.
 *
 * The deal already begins on a square deck - the reply's arrival is held for one
 * (lib/waitingDeck.ts) - but "square for one frame" is not something an eye reads as
 * settled. This is the pause that lets it.
 */
const DEAL_SETTLE_MS = 190;

/** How long one card's flight takes, from how far it has to go. */
function flightDurationS(distancePx: number): number {
	return Math.min(
		DEAL_MAX_DURATION_S,
		Math.max(DEAL_MIN_DURATION_S, distancePx / DEAL_SPEED_PX_S),
	);
}

/**
 * When the last card is certainly down, in ms. Exported because the prose the model wrote
 * UNDER the cards waits for it: the group has to finish arriving before the next thing to
 * read appears, and mounting that bubble mid-flight would also put a growing element below
 * cards that are still animating.
 *
 * An UPPER BOUND now rather than the exact figure, because flights are sized by distances
 * this function is not given - the caller has a card count and no layout. It uses the
 * ceiling, so it can be early by at most the difference between the longest flight and the
 * real one (~0.1s at four cards) and is never late, which is the direction that matters:
 * being late costs a beat, being early puts a growing bubble under a card still in the air.
 */
export function dealDurationMs(cardCount: number): number {
	if (cardCount <= 0) return 0;
	return Math.round(((cardCount - 1) * DEAL_STAGGER_S + DEAL_MAX_DURATION_S) * 1000);
}

/** Per-card depth in the stack, waiting and dealing alike. */
const DECK_STEP_PX = 7;

/**
 * A card whose body runs past this is LONG and takes its whole grid row rather than one
 * track. The prompt steers descriptions to roughly two sentences (~150-175 chars) and the
 * server cap is a far-off runaway guard, so a long card is the exception - and squeezing
 * one into a 15.5rem track beside a two-line neighbour makes the row as tall as the long
 * card, with the neighbour floating on top of its dead space. Width instead of a clamp:
 * every character still renders, in fewer lines.
 */
const WIDE_CARD_MIN_CHARS = 280;

/**
 * How many card objects the waiting deck holds.
 *
 * NOT A CLAIM ABOUT THE COUNT, which is why it is a constant rather than anything read off
 * the turn: nobody knows yet how many cards the model is writing, and a row of skeletons
 * that turns into one card is a promise the reply then breaks. A deck is a deck at any
 * thickness. Four, because that is enough edges to read as a stack rather than as one card
 * with a shadow under it.
 */
const WAITING_DECK_DEPTH = 4;

/**
 * How tall the deck stands. Kept in step with `.waiting-deck`'s own height in CardDeck.css,
 * because the real cards are scaled to it: the deck the cards come out of has to be the deck
 * that was sitting there.
 */
const WAITING_DECK_HEIGHT_PX = 76;

/**
 * THE CYCLE: one card nudges out of the stack and back, then the next one up, then a rest.
 *
 * A BEAT is one nudge and one rest. The card dips a little way out of the bottom of the stack
 * and returns to exactly where it started; the deck then stands still before the card above it
 * takes its turn. It works UPWARD - bottom, then second from bottom, then second from top -
 * and THE TOP CARD NEVER MOVES, which is what stops the whole thing reading as restless: there
 * is always a fixed object at the front for the eye to hold on to.
 *
 * NO CARD EVER CHANGES SLOT, and that is the structural point rather than a simplification.
 * Earlier versions cycled cards through the stack, which meant depth changing mid-move - and a
 * card swapping from behind the deck to in front of it is a jump no easing can hide, so it
 * needed a dip far enough to clear the whole stack first, and that dip was most of the motion.
 * Here the order is fixed, the depths are constant, and what is left is the small part that
 * was actually saying something.
 *
 * PURELY VERTICAL AND NEVER TILTED. No arc, no lean, no swing: each was decoration that made
 * the motion harder to read rather than easier.
 *
 * A CONTINUOUS FUNCTION OF THE CLOCK, not a keyframe list. `deckFrame` answers "where is
 * everything, and is any of it moving?" for any instant, which is why there is nothing here to
 * keep in step with anything else.
 */
const CYCLE_POP_MS = 380;
const CYCLE_PAUSE_MS = 520;
/**
 * How far the card nudges. Small on purpose: all it has to do is grow the sliver of itself
 * showing below the card in front of it, which at a 7px step is a few pixels of edge to start.
 */
const CYCLE_POP_PX = 12;

/** Ease in and out. The card leaves from rest and comes back to rest. */
function easeInOut(t: number): number {
	return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

/**
 * Where every card is at a given moment, and whether any of it is moving.
 *
 * Card `i` is always on slot `i`; the only question is which one is out and how far. The
 * movers are the slots below the top, taken from the bottom up, so a beat picks one of them
 * and everything else sits still. At rest every card is exactly on its slot, which is also
 * the pose the real deck is built in - see the hand-off.
 */
function deckFrame(elapsed: number): { poses: number[]; moving: boolean } {
	const beatMs = CYCLE_POP_MS + CYCLE_PAUSE_MS;
	const beat = Math.floor(elapsed / beatMs);
	const through = Math.min(1, Math.max(0, (elapsed % beatMs) / CYCLE_POP_MS));

	// Bottom slot first, working up, and never the top one.
	const movers = WAITING_DECK_DEPTH - 1;
	const mover = WAITING_DECK_DEPTH - 1 - (beat % movers);

	// Out and back, each half eased off its own end, so it comes to a stop before returning.
	const out = through < 0.5 ? easeInOut(through * 2) : 1 - easeInOut((through - 0.5) * 2);

	const poses = Array.from(
		{ length: WAITING_DECK_DEPTH },
		(_, index) => index * DECK_STEP_PX + (index === mover ? CYCLE_POP_PX * out : 0),
	);
	return { poses, moving: through < 1 };
}

/**
 * The deck itself, cycling, while the model is still writing the cards.
 *
 * The entrance has always been described as dealing off a deck, and the deck was the one part
 * of it that never existed: a position measured just above the first grid slot, used as an
 * origin and never drawn. This draws it, in the window where the reply has gone quiet and the
 * student has nothing to look at - so the cards then come off something that has been on
 * screen, rather than materialising out of an empty column.
 *
 * FOUR CARD OBJECTS AND ONE CLOCK. Each element is handed a phase a quarter-circuit apart and
 * its pose is read off `deckPose` every frame, which is why there is no keyframe list here and
 * nothing to keep in step with one. Transform only, so none of it touches layout.
 */
export function CardDeckPlaceholder() {
	const reduceMotion = usePrefersReducedMotion();
	const deckRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		const deck = deckRef.current;
		if (!deck) return;
		const cards = Array.from(deck.children) as HTMLElement[];

		const paint = (elapsed: number) => {
			const { poses, moving } = deckFrame(elapsed);
			cards.forEach((card, index) => {
				card.style.transform = `translate3d(0, ${poses[index].toFixed(2)}px, 0)`;
			});
			return moving;
		};

		// Reduced motion gets the deck standing square and never moving, which is the same
		// posture the grid itself takes: present, not performed.
		if (reduceMotion) {
			paint(0);
			return;
		}

		waitingDeck.release();
		const started = performance.now();
		let frame = 0;
		let stopped = false;

		const tick = () => {
			if (stopped) return;
			// HELD means the hand-off is imminent. The deck is in a pause when that happens -
			// that is what the hand-off waited for - so it simply stops where it stands, which
			// is the pose the real deck is about to be built in.
			if (waitingDeck.isHeld()) {
				stopped = true;
				return;
			}
			if (paint(performance.now() - started)) waitingDeck.beginMove();
			else waitingDeck.endMove();
			frame = window.requestAnimationFrame(tick);
		};
		frame = window.requestAnimationFrame(tick);

		return () => {
			stopped = true;
			window.cancelAnimationFrame(frame);
			waitingDeck.release();
		};
	}, [reduceMotion]);

	return (
		<div className="waiting-deck" ref={deckRef} aria-hidden="true">
			{Array.from({ length: WAITING_DECK_DEPTH }, (_, index) => (
				// Depth is FIXED - no card ever changes its place in the stack - so it is set
				// once here rather than written every frame. Without it the paint order is DOM
				// order, which puts the bottom card in front of the deck.
				<div
					key={index}
					className="waiting-deck__card"
					style={{ zIndex: WAITING_DECK_DEPTH - index }}
				/>
			))}
		</div>
	);
}

/**
 * Where a card sits while it is still in the deck: the translation back to the stack, and
 * the scale that makes its full-size box present as one card of that stack. The scale is
 * what lets the deck stay deck-sized while the grid underneath is already at its finished
 * dimensions, and it is invisible on the way out because the card is face down until the
 * back half of its flight - a squashed box with a flat pattern on it is just a card back.
 */
type DeckOffset = { x: number; y: number; sx: number; sy: number };

/**
 * Grid placement for each card, as a 1-based start line for `grid-column: <start> / -1`,
 * or null for ordinary auto placement in one track.
 *
 * A long card spans the full row. That can strand the card before it in a partial row, so
 * the stranded card stretches to the end of its row instead of sitting beside a hole, and
 * the same goes for a trailing partial row once any card in the deck is long. Order is
 * never changed to fill a hole - a stretched card is still the same card in the same
 * place. A lone SHORT card keeps one track (the auto-fill decision in CardDeck.css); a
 * lone long card is exactly the case the span exists for.
 */
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
	/**
	 * Deal from the deck once, on mount. Archived groups and reduced motion present in
	 * the grid directly.
	 */
	deal?: boolean;
	/** Called once the last card is down. See `handleLanded`. */
	onLanded?: () => void;
};

/**
 * The card group: a grid whose items are sized by their own content, entered by dealing
 * off a deck.
 *
 * The deal is a FLIP: the grid is laid out final-form first, every item measured against
 * the first slot, and each card then animated FROM that stacked position back to zero.
 * Transform only - the column's height is the finished height from the first frame, so
 * nothing below the group reflows while cards are still in the air.
 */
export function CardDeck({ cards, onFollowup, deal = false, onLanded }: CardDeckProps) {
	const reduceMotion = usePrefersReducedMotion();
	const dealing = deal && !reduceMotion && cards.length > 0;
	const gridRef = useRef<HTMLDivElement>(null);
	const measuredRef = useRef(false);
	const [offsets, setOffsets] = useState<DeckOffset[] | null>(null);
	const [landed, setLanded] = useState(false);
	const [columns, setColumns] = useState(1);
	/**
	 * Whether the cards have started leaving the stack.
	 *
	 * A BEAT OF STILLNESS FIRST, and never a card pulled out of a deck that is mid-shuffle.
	 * The group is only mounted at a rest point in the cycle - the turn's arrival is held
	 * back for it (components/ChatApp.tsx, lib/waitingDeck.ts) - so the stack this waits on is
	 * already motionless; what the pause buys is the moment an eye needs to read it as
	 * settled before the first card slides out from underneath.
	 */
	const [dealStarted, setDealStarted] = useState(false);
	useEffect(() => {
		if (!dealing || !offsets || dealStarted) return;
		const timer = window.setTimeout(() => setDealStarted(true), DEAL_SETTLE_MS);
		return () => window.clearTimeout(timer);
	}, [dealing, offsets, dealStarted]);

	// The group is down. Reported rather than timed, because a flight is sized by a distance
	// this component measures and nothing upstairs can derive - see dealDurationMs.
	const handleLanded = useCallback(() => {
		setLanded(true);
		onLanded?.();
	}, [onLanded]);

	// How many tracks the auto-fill grid actually resolved to, kept current across
	// resizes. ResizeObserver delivers before paint, so a span computed for the old track
	// count is corrected before it is ever seen.
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
		// Measured once. A re-render mid-flight must never re-derive offsets, or the
		// entrance would restart from wherever the cards had got to.
		if (!dealing || measuredRef.current) return;
		const grid = gridRef.current;
		if (!grid) return;

		// The offsets must be measured against the SPANNED layout. On the first commit
		// this effect runs before the track count above has reached the render, so wait
		// for the re-render that applies it rather than measuring a layout about to move.
		if (getComputedStyle(grid).gridTemplateColumns.split(' ').length !== columns) {
			return;
		}

		const items = Array.from(grid.children) as HTMLElement[];
		const first = items[0]?.getBoundingClientRect();
		if (!first) return;

		// THE DECK IS ONE TRACK WIDE, not one SLOT wide, and the difference is not academic:
		// the first card's slot spans the whole row whenever a long card would otherwise
		// strand it (see computeColumnStarts), so measuring the deck off that box makes the
		// stack twice the width the waiting deck was standing at. A track is the width the
		// waiting deck resolves to as well, being laid out on these same tracks.
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
					// CENTRE TO CENTRE horizontally, top to top vertically, because that is
					// where the item's transform-origin sits: a card scaled or turned over
					// about that point stays put, where a left-edge measurement would let
					// both of those drag it sideways.
					x: deck.left + deck.width / 2 - (rect.left + rect.width / 2),
					y: deck.top - rect.top,
					// One track wide and a deck's height tall, whatever size this card
					// finishes at. A long card spans its whole row, so without this the
					// stack would be a row-wide slab rather than a deck.
					sx: rect.width ? deck.width / rect.width : 1,
					sy: rect.height ? WAITING_DECK_HEIGHT_PX / rect.height : 1,
				};
			}),
		);
	}, [dealing, columns]);

	const columnStarts = computeColumnStarts(
		cards.map((card) => card.body.length),
		columns,
	);

	const inFlight = dealing && !landed;

	/**
	 * Whether a card may lift under the pointer yet.
	 *
	 * HOVER IS EARNED BY THE POINTER MOVING, never by a card arriving underneath one that
	 * has not moved. The deal ends by handing pointer events back to the group (see
	 * `card-deck--dealing`), and a mouse resting anywhere over the panel is then suddenly
	 * over whichever card stopped beneath it: that card takes the hover lift on its own,
	 * about 6px BACK UP the path it just flew, a second and a half after it started moving
	 * and with nothing the student did to explain it. Measured in Chrome - the card under
	 * the pointer moved, its neighbours did not, and the transform it settled on was the
	 * `:hover` rule's exactly.
	 *
	 * So the rule is gated on this class instead of on the pointer merely being somewhere.
	 * Clicking is untouched - only the lift waits - and the wait is over at the first real
	 * movement, which is every use of a mouse that was going anywhere. Re-armed per group,
	 * because the next reply deals a new one under a pointer that has stopped again.
	 */
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
				// DEALT FROM THE BOTTOM. The card at the bottom of the stack goes first and
				// travels furthest; each one above it follows a beat later; and the card on
				// top - the one the student has been watching, face down, since before the
				// reply finished - is last. Its slot IS the deck's own position, measured at
				// (0, 0), so it has nowhere to fly. It turns over where it lies.
				const dealDelay = (cards.length - 1 - index) * DEAL_STAGGER_S;
				const flight = offset ? flightDurationS(Math.hypot(offset.x, offset.y)) : 0;

				// First pass with `dealing` renders the finished layout so it can be
				// measured. It is hidden rather than unmounted because the measurement
				// needs its real box, and a layout effect resolves before paint, so this
				// pass is never on screen.
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

				// FACE DOWN IN THE STACK, and the same size the waiting deck was: the two
				// are the same object as far as the student is concerned, so the finished
				// turn must not arrive as a sudden card-sized version of it. The item's
				// LAYOUT box is its final one either way, which is what keeps the column at
				// its finished height and the entrance transform-only.
				const inDeck = {
					x: offset.x,
					// No lift off the first slot, and no lean. The stack sits exactly where the
					// waiting deck was standing and in exactly its pose, which is what stops
					// the whole thing jumping the instant the reply arrives.
					y: offset.y + index * DECK_STEP_PX,
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
						// Held in the stack until the deal begins, which is its own
						// decision - see `dealStarted`. Handing motion the same values it
						// started with runs no animation, so the card simply waits.
						animate={
							dealStarted
								? { x: 0, y: 0, scaleX: 1, scaleY: 1, rotateY: 0 }
								: inDeck
						}
						transition={{
							// This card's own flight, sized by its own distance. A card
							// crossing the whole group takes longer than one settling into
							// the top slot, so the two travel at the same speed.
							duration: flight,
							ease: DEAL_EASE,
							delay: dealDelay,
							// THE TURN OVER, in the back half of the flight. Held until the
							// card is most of the way to its slot so it lands face up rather
							// than arriving as a spinning object, and eased in and out
							// because a flip has to start and stop on its own axis.
							rotateY: {
								duration: Math.max(0.26, flight * 0.6),
								ease: 'easeInOut',
								delay: dealDelay + flight * 0.4,
							},
						}}
						onAnimationComplete={
							// The card on top is dealt LAST, so its landing is the group's.
							index === 0 ? handleLanded : undefined
						}
					>
						<div className="card-deck__back" aria-hidden="true" />
						<div className="card-deck__front">
							<StatementCard card={card} onFollowup={onFollowup} />
						</div>
					</motion.div>
				);
			})}
		</div>
	);
}

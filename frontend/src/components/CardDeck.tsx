import { useLayoutEffect, useRef, useState } from 'react';
import { motion } from 'motion/react';
import type { StatementCard as StatementCardData } from '../types/chat';
import { StatementCard } from './StatementCard';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './CardDeck.css';

/**
 * Entrance budget: last card lands at (n - 1) * stagger + duration. At the card ceiling of
 * four that is 1.46s.
 *
 * The stagger is paced against the PROSE, not against itself. The bubble types in at a
 * reading speed and the cards used to arrive at 0.1s apart, so the turn changed gear
 * halfway through: unhurried above, dealt out below. 0.34s is one card per beat, close
 * enough to the typing cadence that the whole turn reads as one rhythm. The duration is
 * unchanged - a card's own flight was never the problem, the gap between them was - and
 * nothing is gated behind the deal, so the extra 0.7s costs the student nothing.
 */
const DEAL_STAGGER_S = 0.34;
const DEAL_DURATION_S = 0.44;
const DEAL_EASE = [0.22, 1, 0.36, 1] as const;

/** The deck sits just above the first grid slot, so even the top card travels. */
const DECK_LIFT_PX = 12;
/** Per-card depth in the stack. */
const DECK_STEP_PX = 5;
const DECK_TILT_DEG = [-1.6, 1.3, -0.9, 1.7];

/**
 * A card whose body runs past this is LONG and takes its whole grid row rather than one
 * track. The prompt steers descriptions to roughly two sentences (~150-175 chars) and the
 * server cap is a far-off runaway guard, so a long card is the exception - and squeezing
 * one into a 15.5rem track beside a two-line neighbour makes the row as tall as the long
 * card, with the neighbour floating on top of its dead space. Width instead of a clamp:
 * every character still renders, in fewer lines.
 */
const WIDE_CARD_MIN_CHARS = 280;

type DeckOffset = { x: number; y: number };

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
export function CardDeck({ cards, onFollowup, deal = false }: CardDeckProps) {
	const reduceMotion = usePrefersReducedMotion();
	const dealing = deal && !reduceMotion && cards.length > 0;
	const gridRef = useRef<HTMLDivElement>(null);
	const measuredRef = useRef(false);
	const [offsets, setOffsets] = useState<DeckOffset[] | null>(null);
	const [landed, setLanded] = useState(false);
	const [columns, setColumns] = useState(1);

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
		const deck = items[0]?.getBoundingClientRect();
		if (!deck) return;

		measuredRef.current = true;
		setOffsets(
			items.map((item) => {
				const rect = item.getBoundingClientRect();
				return { x: deck.left - rect.left, y: deck.top - rect.top };
			}),
		);
	}, [dealing, columns]);

	const columnStarts = computeColumnStarts(
		cards.map((card) => card.body.length),
		columns,
	);

	const inFlight = dealing && !landed;

	return (
		<div
			ref={gridRef}
			className={`card-deck${inFlight ? ' card-deck--dealing' : ''}`}
			data-dealing={inFlight ? 'true' : undefined}
		>
			{cards.map((card, index) => {
				const offset = offsets?.[index];
				const start = columnStarts[index];
				const gridColumn = start === null ? undefined : `${start} / -1`;

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

				return (
					<motion.div
						key={card.id}
						className="card-deck__item"
						style={{ zIndex: landed ? undefined : cards.length - index, gridColumn }}
						initial={{
							x: offset.x,
							y: offset.y - DECK_LIFT_PX + index * DECK_STEP_PX,
							rotate: DECK_TILT_DEG[index % DECK_TILT_DEG.length],
							scale: 0.98 - index * 0.02,
						}}
						animate={{ x: 0, y: 0, rotate: 0, scale: 1 }}
						transition={{
							duration: DEAL_DURATION_S,
							ease: DEAL_EASE,
							delay: index * DEAL_STAGGER_S,
						}}
						onAnimationComplete={
							index === cards.length - 1 ? () => setLanded(true) : undefined
						}
					>
						<StatementCard card={card} onFollowup={onFollowup} />
					</motion.div>
				);
			})}
		</div>
	);
}

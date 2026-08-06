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

type DeckOffset = { x: number; y: number };

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

	useLayoutEffect(() => {
		// Measured once. A re-render mid-flight must never re-derive offsets, or the
		// entrance would restart from wherever the cards had got to.
		if (!dealing || measuredRef.current) return;
		const grid = gridRef.current;
		if (!grid) return;

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
	}, [dealing]);

	const inFlight = dealing && !landed;

	return (
		<div
			ref={gridRef}
			className={`card-deck${inFlight ? ' card-deck--dealing' : ''}`}
			data-dealing={inFlight ? 'true' : undefined}
		>
			{cards.map((card, index) => {
				const offset = offsets?.[index];

				// First pass with `dealing` renders the finished layout so it can be
				// measured. It is hidden rather than unmounted because the measurement
				// needs its real box, and a layout effect resolves before paint, so this
				// pass is never on screen.
				if (dealing && !offset) {
					return (
						<div key={card.id} className="card-deck__item" style={{ visibility: 'hidden' }}>
							<StatementCard card={card} onFollowup={onFollowup} />
						</div>
					);
				}

				if (!offset) {
					return (
						<div key={card.id} className="card-deck__item">
							<StatementCard card={card} onFollowup={onFollowup} />
						</div>
					);
				}

				return (
					<motion.div
						key={card.id}
						className="card-deck__item"
						style={{ zIndex: landed ? undefined : cards.length - index }}
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

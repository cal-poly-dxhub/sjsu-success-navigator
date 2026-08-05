import { motion } from 'motion/react';
import type { StatementCard as StatementCardData } from '../types/chat';
import { formatBatchTimestamp } from '../lib/formatTimestamp';
import { StatementCard } from './StatementCard';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './StatementStack.css';

const GRID_STAGGER = 0.12;
const BUBBLE_SPRING = { type: 'spring' as const, stiffness: 420, damping: 22 };
const BUBBLE_EASE = [0.34, 1.45, 0.64, 1] as const;

function gridClassFor(count: number): string {
	if (count >= 4) return 'statement-stack__grid--2x2';
	if (count === 3) return 'statement-stack__grid--3';
	if (count === 2) return 'statement-stack__grid--2';
	return 'statement-stack__grid--1';
}

type RagGridProps = {
	cards: StatementCardData[];
	onFollowup: (prompt: string) => void;
	createdAt: number;
	animateIn?: boolean;
	archived?: boolean;
};

export function RagGrid({
	cards,
	onFollowup,
	createdAt,
	animateIn = false,
	archived = false,
}: RagGridProps) {
	const reduceMotion = usePrefersReducedMotion();
	const list = cards.slice(0, 4);
	const n = list.length;
	const timestampLabel = formatBatchTimestamp(createdAt);

	if (!n) return null;

	return (
		<section
			className={`statement-stack statement-stack--grid${archived ? ' statement-stack--archived' : ''}`}
			aria-label={archived ? `Campus resources from ${timestampLabel}` : 'All campus resources'}
		>
			{archived ? (
				<time className="statement-stack__timestamp" dateTime={new Date(createdAt).toISOString()}>
					{timestampLabel}
				</time>
			) : (
				<p className="statement-stack__review-hint">All resources — pick a next step</p>
			)}
			<div className={`statement-stack__grid ${gridClassFor(n)}`}>
				{list.map((card, index) => (
					<div key={card.id} className="statement-stack__grid-item">
						{animateIn && !reduceMotion ? (
							<motion.div
								className="statement-stack__grid-bubble"
								initial={{ scale: 0, opacity: 0 }}
								animate={{ scale: 1, opacity: 1 }}
								transition={{
									...BUBBLE_SPRING,
									delay: index * GRID_STAGGER,
									ease: BUBBLE_EASE,
								}}
								style={{ transformOrigin: 'center center' }}
							>
								<StatementCard card={card} compact onFollowup={onFollowup} />
							</motion.div>
						) : (
							<StatementCard card={card} compact onFollowup={onFollowup} />
						)}
					</div>
				))}
			</div>
		</section>
	);
}

type RagProgressProps = {
	label: string;
	ratio: number;
	total: number;
	step: number;
};

export function RagProgress({ label, ratio, total, step }: RagProgressProps) {
	return (
		<div
			className="statement-stack__progress statement-stack__progress--phase"
			role="progressbar"
			aria-valuemin={1}
			aria-valuemax={total}
			aria-valuenow={step}
			aria-label={`Resource card ${step} of ${total}`}
		>
			<div className="statement-stack__progress-track">
				<div
					className="statement-stack__progress-fill statement-stack__progress-fill--static"
					style={{ transform: `scaleX(${ratio})` }}
				/>
			</div>
			<span className="statement-stack__progress-label" aria-hidden="true">
				{label}
			</span>
		</div>
	);
}

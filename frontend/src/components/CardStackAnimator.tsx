import { motion } from 'motion/react';
import type { StatementCard as StatementCardData } from '../types/chat';
import { StatementCard } from './StatementCard';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './CardStackAnimator.css';

const LAND_EASE = [0.4, 0, 0.2, 1] as const;

type CardStackAnimatorProps = {
	cards: StatementCardData[];
	frontIndex: number;
	landDurationMs: number;
	onFollowup: (prompt: string) => void;
};

export function CardStackAnimator({
	cards,
	frontIndex,
	landDurationMs,
	onFollowup,
}: CardStackAnimatorProps) {
	const reduceMotion = usePrefersReducedMotion();
	const visible = cards.slice(0, frontIndex + 1);

	return (
		<div className="card-stack-animator" aria-label="Campus resource cards">
			{visible.map((card, index) => {
				const depth = frontIndex - index;
				const isFront = depth === 0;
				const behindScale = Math.max(0.9, 1 - depth * 0.035);
				const behindY = -depth * 10;
				const shouldLand = isFront && frontIndex > 0;

				return (
					<div
						key={card.id}
						className={`card-stack-animator__layer${isFront ? ' card-stack-animator__layer--front' : ''}`}
						style={{
							zIndex: index + 1,
							transform: isFront ? undefined : `translateY(${behindY}px) scale(${behindScale})`,
						}}
					>
						{shouldLand && !reduceMotion ? (
							<motion.div
								className="card-stack-animator__card"
								key={`land-${frontIndex}`}
								initial={{ y: 44, scale: 0.96, opacity: 0.88 }}
								animate={{ y: 0, scale: 1, opacity: 1 }}
								transition={{ duration: landDurationMs / 1000, ease: LAND_EASE }}
							>
								<StatementCard card={card} onFollowup={onFollowup} />
							</motion.div>
						) : (
							<div className="card-stack-animator__card">
								<StatementCard card={card} onFollowup={onFollowup} />
							</div>
						)}
					</div>
				);
			})}
		</div>
	);
}

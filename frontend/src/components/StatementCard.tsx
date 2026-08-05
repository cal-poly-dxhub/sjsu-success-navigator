import type { StatementCard as StatementCardData } from '../types/chat';
import { PressableButton } from './PressableButton';
import './StatementCard.css';

type StatementCardProps = {
	card: StatementCardData;
	onFollowup: (prompt: string) => void;
	compact?: boolean;
};

export function StatementCard({ card, onFollowup, compact = false }: StatementCardProps) {
	return (
		<article className={`statement-card${compact ? ' statement-card--compact' : ''}`}>
			<header className="statement-card__header">
				<h2 className="statement-card__title">{card.title}</h2>
			</header>
			<p className="statement-card__body">{card.body}</p>
			<div className="statement-card__actions">
				{card.actions.map((action, index) => {
					if (action.type === 'source') {
						return (
							<PressableButton
								key={`${card.id}-source-${index}`}
								variant="secondary"
								href={card.sourceUrl}
								aria-label={`${action.label}: ${card.title}`}
							>
								{action.label}
							</PressableButton>
						);
					}
					return (
						<PressableButton
							key={`${card.id}-followup-${index}`}
							variant="primary"
							onClick={() => onFollowup(action.prompt)}
						>
							{action.label}
						</PressableButton>
					);
				})}
			</div>
		</article>
	);
}

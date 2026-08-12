import type { StatementCard as StatementCardData } from '../types/chat';
import { FormattedMessage } from './FormattedMessage';
import { PressableButton } from './PressableButton';
import './StatementCard.css';

type StatementCardProps = {
	card: StatementCardData;
	onFollowup: (prompt: string) => void;
};

export function StatementCard({ card, onFollowup }: StatementCardProps) {
	return (
		<article className="statement-card">
			<header className="statement-card__header">
				<h2 className="statement-card__title">{card.title}</h2>
			</header>
			{/* The description is model-authored, so it formats like the prose around it.
			    The title is not: it is one line, and a heading has nothing to bold. */}
			<div className="statement-card__body">
				<FormattedMessage text={card.body} />
			</div>
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

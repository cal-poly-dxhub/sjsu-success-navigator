import type { StatementCard as StatementCardData } from '../types/chat';
import { formatBatchTimestamp } from '../lib/formatTimestamp';
import { CardDeck } from './CardDeck';
import './StatementStack.css';

/** Card ceiling, per docs/cards-v2.md. */
const MAX_CARDS = 4;

type RagGridProps = {
	cards: StatementCardData[];
	onFollowup: (prompt: string) => void;
	createdAt: number;
	/** Deal the group in from a deck. Off for archived turns. */
	deal?: boolean;
	archived?: boolean;
};

export function RagGrid({
	cards,
	onFollowup,
	createdAt,
	deal = false,
	archived = false,
}: RagGridProps) {
	const list = cards.slice(0, MAX_CARDS);
	const timestampLabel = formatBatchTimestamp(createdAt);

	// The group exists only when cards actually parsed. Zero cards is a plain prose turn.
	if (!list.length) return null;

	return (
		<section
			className={`statement-stack${archived ? ' statement-stack--archived' : ''}`}
			aria-label={archived ? `Campus resources from ${timestampLabel}` : 'Campus resources'}
		>
			{archived ? (
				<time className="statement-stack__timestamp" dateTime={new Date(createdAt).toISOString()}>
					{timestampLabel}
				</time>
			) : null}
			<CardDeck cards={list} onFollowup={onFollowup} deal={deal} />
		</section>
	);
}

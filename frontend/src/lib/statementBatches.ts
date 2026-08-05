import type { StatementBatch, StatementCard } from '../types/chat';

export const MAX_STATEMENT_BATCHES = 5;

export function createStatementBatch(
	cards: StatementCard[],
	options?: { query?: string; id?: string; createdAt?: number },
): StatementBatch {
	return {
		id: options?.id ?? `batch-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
		cards: cards.slice(0, 4),
		query: options?.query,
		createdAt: options?.createdAt ?? Date.now(),
	};
}

export function appendStatementBatch(
	batches: StatementBatch[],
	cards: StatementCard[],
	options?: { query?: string; id?: string; createdAt?: number },
): StatementBatch[] {
	const next = [...batches, createStatementBatch(cards, options)];
	return next.length > MAX_STATEMENT_BATCHES ? next.slice(-MAX_STATEMENT_BATCHES) : next;
}

export function batchesFromCards(
	cards: StatementCard[],
	options?: { query?: string; createdAt?: number },
): StatementBatch[] {
	if (!cards.length) return [];
	return [createStatementBatch(cards, { ...options, id: 'batch-initial' })];
}

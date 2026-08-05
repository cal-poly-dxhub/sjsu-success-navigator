import type {
	ChatHistoryMessage,
	ChatResponse,
	ConversationTurn,
	StatementCard,
} from '../types/chat';

const MAX_HISTORY_TURNS = 6;
const MAX_HISTORY_MESSAGES = MAX_HISTORY_TURNS * 2;

/** Spoken user/assistant lines from completed turns (excludes the in-flight prompt). */
export function historyFromTurns(turns: ConversationTurn[]): ChatHistoryMessage[] {
	const messages: ChatHistoryMessage[] = [];

	for (const turn of turns) {
		const query = turn.query?.trim();
		if (query) {
			messages.push({ role: 'user', text: query });
		}

		const text = turn.text.trim();
		if (text) {
			messages.push({ role: 'assistant', text });
		}
	}

	return messages.slice(-MAX_HISTORY_MESSAGES);
}
import { createStatementBatch } from './statementBatches';

export function createConversationTurn(
	text: string,
	options?: {
		cards?: StatementCard[];
		safetyHandoff?: ChatResponse['safetyHandoff'];
		query?: string;
		id?: string;
		createdAt?: number;
		phase?: ConversationTurn['phase'];
	},
): ConversationTurn {
	const cards = options?.cards?.slice(0, 4) ?? [];
	const hasRag = cards.length > 0;
	return {
		id: options?.id ?? `turn-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
		text,
		cards,
		safetyHandoff: options?.safetyHandoff,
		query: options?.query,
		createdAt: options?.createdAt ?? Date.now(),
		phase: options?.phase ?? (hasRag ? 'conversational' : 'conversational'),
	};
}

/** Mark all prior turns with cards as done (grid archive). */
export function archiveActiveTurns(turns: ConversationTurn[]): ConversationTurn[] {
	return turns.map((turn) =>
		turn.cards.length > 0 && turn.phase !== 'done' ? { ...turn, phase: 'done' } : turn,
	);
}

export function appendConversationTurn(
	turns: ConversationTurn[],
	turn: ConversationTurn,
	max = 5,
): ConversationTurn[] {
	const archived = archiveActiveTurns(turns);
	const next = [...archived, turn];
	return next.length > max ? next.slice(-max) : next;
}

/** Hydrate turns from fixture / session response (all RAG batches shown as done grids). */
export function turnsFromResponse(response: ChatResponse): ConversationTurn[] {
	const batches = response.statementBatches ?? [];
	if (batches.length === 0) {
		if (response.safetyHandoff) {
			return [
				createConversationTurn(response.conversationalText, {
					safetyHandoff: response.safetyHandoff,
					phase: 'conversational',
				}),
			];
		}
		return [
			createConversationTurn(response.conversationalText, {
				phase: 'conversational',
			}),
		];
	}

	return batches.map((batch, index) =>
		createConversationTurn(
			index === batches.length - 1 ? response.conversationalText : batch.query ?? '',
			{
				cards: batch.cards,
				query: batch.query,
				id: batch.id,
				createdAt: batch.createdAt,
				phase: index === batches.length - 1 ? 'conversational' : 'done',
			},
		),
	);
}

export function batchesFromTurns(turns: ConversationTurn[]) {
	return turns
		.filter((turn) => turn.cards.length > 0)
		.map((turn) =>
			createStatementBatch(turn.cards, {
				query: turn.query,
				id: turn.id,
				createdAt: turn.createdAt,
			}),
		);
}

export function responseFromTurns(
	turns: ConversationTurn[],
	base?: Partial<ChatResponse>,
): ChatResponse {
	const last = turns[turns.length - 1];
	return {
		conversationalText: last?.text ?? '',
		statementBatches: batchesFromTurns(turns),
		safetyHandoff: last?.safetyHandoff ?? base?.safetyHandoff,
		talkToPersonAvailable: base?.talkToPersonAvailable ?? true,
	};
}

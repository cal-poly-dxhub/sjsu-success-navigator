import type {
	ChatResponse,
	ConversationTurn,
	StatementCard,
	StoredMessage,
} from '../types/chat';

/**
 * How many turns the feed holds, live or reopened.
 *
 * ONE number for both paths deliberately: a conversation reopened from the server shows
 * exactly as much as one typed in this tab, so the sidebar is a way back into a
 * conversation rather than a second, differently-shaped view of it. The server sends more
 * than this (its own cap is messages, not turns) and the tail is what survives - the end of
 * a conversation being what a student came back for.
 */
export const MAX_FEED_TURNS = 5;

export function createConversationTurn(
	text: string,
	options?: {
		cards?: StatementCard[];
		trailingText?: string;
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
		trailingText: options?.trailingText,
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
	max = MAX_FEED_TURNS,
): ConversationTurn[] {
	const archived = archiveActiveTurns(turns);
	const next = [...archived, turn];
	return next.length > max ? next.slice(-max) : next;
}

/**
 * Stored messages (GET /conversations/{id}) as feed turns.
 *
 * The stored shape is one item per MESSAGE and the feed's unit is an exchange, so this
 * pairs them: a user message opens a turn, the assistant message after it closes one. Both
 * halves come from the server - the question as the student typed it and the reply with its
 * cards - so nothing here is reconstructed from a client-side store.
 *
 * A user message with no reply after it is kept as a turn with no prose. That is a turn
 * that failed after the student's message was written, which is the ordering the server
 * chose on purpose (a disclosure that then times out is still on record), and dropping it
 * here would hide from the student that they ever said it.
 *
 * Earlier turns come back ARCHIVED ('done'), exactly as they would be after being scrolled
 * past live. The LAST one is left in the phase a finished turn ends in, which for a turn
 * with cards is 'grid' - the feed treats its final turn as the active one and hides the
 * card group of an active turn that has not reached that phase, so a reopened conversation
 * whose last answer had cards would otherwise come back without them.
 *
 * Turn ids are namespaced by the conversation: two conversations both starting at index 0
 * would otherwise hand React the same key for different content when the student switches
 * between them.
 */
export function turnsFromStoredMessages(
	messages: StoredMessage[],
	conversationId: string,
): ConversationTurn[] {
	const turns: ConversationTurn[] = [];
	let pendingQuery: string | undefined;
	let pendingAt: number | undefined;

	const push = (text: string, options: { cards?: StatementCard[]; createdAt?: number }) => {
		turns.push(
			createConversationTurn(text, {
				cards: options.cards,
				query: pendingQuery,
				createdAt: pendingAt ?? options.createdAt,
				id: `${conversationId}-${turns.length}`,
				phase: 'done',
			}),
		);
		pendingQuery = undefined;
		pendingAt = undefined;
	};

	for (const message of messages) {
		const at = message.createdAt ? Date.parse(message.createdAt) : NaN;
		const createdAt = Number.isNaN(at) ? undefined : at;

		if (message.role === 'user') {
			// Two user messages in a row means the first one's turn never got a reply.
			if (pendingQuery !== undefined) push('', {});
			pendingQuery = message.text;
			pendingAt = createdAt;
			continue;
		}

		push(message.text, { cards: message.cards, createdAt });
	}

	if (pendingQuery !== undefined) push('', {});

	const kept = turns.length > MAX_FEED_TURNS ? turns.slice(-MAX_FEED_TURNS) : turns;
	const last = kept[kept.length - 1];
	if (last) {
		kept[kept.length - 1] = {
			...last,
			phase: last.cards.length > 0 ? 'grid' : 'conversational',
		};
	}
	return kept;
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
				trailingText: response.trailingText,
				phase: 'conversational',
			}),
		];
	}

	return batches.map((batch, index) =>
		createConversationTurn(
			index === batches.length - 1 ? response.conversationalText : batch.query ?? '',
			{
				cards: batch.cards,
				// The response carries the prose of its LAST turn only, so only that turn
				// can own the half of it that sits under the cards.
				trailingText: index === batches.length - 1 ? response.trailingText : undefined,
				query: batch.query,
				id: batch.id,
				createdAt: batch.createdAt,
				phase: index === batches.length - 1 ? 'conversational' : 'done',
			},
		),
	);
}

/*
 * batchesFromTurns and responseFromTurns are GONE, and their absence is the point of this
 * slice on the client side. They rebuilt a ChatResponse out of the turns already on screen,
 * which the sidebar then stored as its copy of a conversation - a client-side store standing
 * in for the record. It was also LOSSY in a way that only showed up once real history
 * existed: a response carries the prose of its last turn only, so every earlier turn came
 * back out of that round-trip with the student's own question in place of the answer.
 *
 * A conversation is now read from the server (turnsFromStoredMessages above), which is the
 * only place that ever held all of it.
 */

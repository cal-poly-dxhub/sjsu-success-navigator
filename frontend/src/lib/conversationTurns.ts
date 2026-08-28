import type {
	ChatResponse,
	ConversationTurn,
	StatementCard,
	StoredMessage,
} from '../types/chat';

/** How many turns the feed holds, live or reopened. */
export const MAX_FEED_TURNS = 5;

export function createConversationTurn(
	text: string,
	options?: {
		cards?: StatementCard[];
		trailingText?: string;
		safetyHandoff?: ChatResponse['safetyHandoff'];
		escalation?: ChatResponse['escalation'];
		place?: ChatResponse['place'];
		query?: string;
		id?: string;
		createdAt?: number;
		phase?: ConversationTurn['phase'];
		live?: boolean;
		/** Prose a streamed preview already typed out, see ConversationTurn.revealedChars. */
		revealedChars?: number;
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
		escalation: options?.escalation,
		place: options?.place,
		query: options?.query,
		revealedChars: options?.revealedChars,
		createdAt: options?.createdAt ?? Date.now(),
		phase: options?.phase ?? (hasRag ? 'conversational' : 'conversational'),
		// Live by default because the ordinary reason to build a turn is that one just arrived,
		// A reply, an error, the welcome.
		live: options?.live ?? true,
	};
}

/** The same turns, finished. */
export function settleTurns(turns: ConversationTurn[]): ConversationTurn[] {
	return turns.map((turn) => (turn.live ? { ...turn, live: false } : turn));
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

/** Stored messages (GET /conversations/{id}) as feed turns. */
export function turnsFromStoredMessages(
	messages: StoredMessage[],
	conversationId: string,
): ConversationTurn[] {
	const turns: ConversationTurn[] = [];
	let pendingQuery: string | undefined;
	let pendingAt: number | undefined;

	const push = (
		text: string,
		options: {
			cards?: StatementCard[];
			trailingText?: string;
			safetyHandoff?: StoredMessage['safetyHandoff'];
			escalation?: StoredMessage['escalation'];
			place?: StoredMessage['place'];
			createdAt?: number;
		},
	) => {
		turns.push(
			createConversationTurn(text, {
				cards: options.cards,
				// The half of the reply that was written under the cards, so it renders under
				// them again.
				trailingText: options.trailingText,
				// Server-authored from the stored reply's own keys, exactly as a live turn gets
				// it.
				safetyHandoff: options.safetyHandoff,
				// The draft as it was assembled when the turn happened, straight off the stored
				// record.
				escalation: options.escalation,
				// The location as it resolved then, never re-resolved: a reopened conversation
				// must show where the office was when the student asked.
				place: options.place,
				query: pendingQuery,
				createdAt: pendingAt ?? options.createdAt,
				id: `${conversationId}-${turns.length}`,
				phase: 'done',
				live: false,
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

		push(message.text, {
			cards: message.cards,
			trailingText: message.trailingText,
			safetyHandoff: message.safetyHandoff,
			escalation: message.escalation,
			place: message.place,
			createdAt,
		});
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
				escalation: response.escalation,
				place: response.place,
				phase: 'conversational',
			}),
		];
	}

	return batches.map((batch, index) =>
		createConversationTurn(
			index === batches.length - 1 ? response.conversationalText : batch.query ?? '',
			{
				cards: batch.cards,
				// The response carries the prose of its last turn only, so only that turn can
				// own the half of it that sits under the cards.
				trailingText: index === batches.length - 1 ? response.trailingText : undefined,
				// Same rule as the trailing prose: the response carries one turn's offer, and
				// it belongs to the turn that owns the prose.
				escalation: index === batches.length - 1 ? response.escalation : undefined,
				// And the location, by the same rule: one turn's panel, on the turn that owns
				// the prose.
				place: index === batches.length - 1 ? response.place : undefined,
				query: batch.query,
				id: batch.id,
				createdAt: batch.createdAt,
				phase: index === batches.length - 1 ? 'conversational' : 'done',
			},
		),
	);
}

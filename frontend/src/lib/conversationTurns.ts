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
		escalation?: ChatResponse['escalation'];
		place?: ChatResponse['place'];
		query?: string;
		id?: string;
		createdAt?: number;
		phase?: ConversationTurn['phase'];
		live?: boolean;
		/** Prose a streamed preview already typed out - see ConversationTurn.revealedChars. */
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
		// Live by default because the ordinary reason to build a turn is that one just
		// arrived - a reply, an error, the welcome. The one caller that is reconstructing
		// something already finished says so (turnsFromStoredMessages).
		live: options?.live ?? true,
	};
}

/**
 * The same turns, finished.
 *
 * A turn animates once, while it is arriving. This is what the sidebar's cache stores, so
 * that leaving a conversation and coming back to it shows what was said rather than
 * performing it again - the student already watched that reply type itself out. It is the
 * in-tab twin of `turnsFromStoredMessages` returning turns that were never live.
 */
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

/**
 * Stored messages (GET /conversations/{id}) as feed turns.
 *
 * The stored shape is one item per MESSAGE and the feed's unit is an exchange, so this
 * pairs them: a user message opens a turn, the assistant message after it closes one. Both
 * halves come from the server - the question as the student typed it and the reply already
 * split around its cards - so nothing here is reconstructed from a client-side store, and
 * nothing here decides what a reply looks like. A reopened turn and a live one are built
 * from the same fields because the server renders them with the same code.
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
 *
 * NONE OF THESE IS LIVE. They were answered days ago and are being read back, so they render
 * finished - whole prose, cards already dealt - rather than replaying an arrival that already
 * happened. See ConversationTurn.live.
 */
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
				// The half of the reply that was written UNDER the cards, so it renders under
				// them again. The server splits it; this only has to carry it, which is the
				// whole of the difference between a reopened turn and the turn that was sent.
				trailingText: options.trailingText,
				// Server-authored from the stored reply's own keys, exactly as a live turn
				// gets it. Nothing here decides that a turn is a safety turn.
				safetyHandoff: options.safetyHandoff,
				// The draft as it was assembled when the turn happened, straight off the
				// stored record. Never rebuilt here: a conversation reopened next month must
				// show where its message was actually going, not where config points today.
				escalation: options.escalation,
				// The location as it resolved when the turn happened, straight off the
				// stored record and never re-resolved: a conversation reopened next month
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
				// The response carries the prose of its LAST turn only, so only that turn
				// can own the half of it that sits under the cards.
				trailingText: index === batches.length - 1 ? response.trailingText : undefined,
				// Same rule as the trailing prose: the response carries ONE turn's offer, and
				// it belongs to the turn that owns the prose.
				escalation: index === batches.length - 1 ? response.escalation : undefined,
				// And the location, by the same rule: one turn's panel, on the turn that
				// owns the prose.
				place: index === batches.length - 1 ? response.place : undefined,
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

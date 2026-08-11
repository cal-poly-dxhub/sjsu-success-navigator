export type SourceAction = {
	type: 'source';
	label: string;
};

export type FollowupAction = {
	type: 'followup';
	label: string;
	prompt: string;
};

export type StatementAction = SourceAction | FollowupAction;

export type StatementCard = {
	id: string;
	title: string;
	body: string;
	sourceUrl: string;
	actions: StatementAction[];
};

export type StatementBatch = {
	id: string;
	cards: StatementCard[];
	query?: string;
	/** Fixed when this response batch was created. */
	createdAt: number;
};

export type SafetyContact = {
	id: string;
	label: string;
	detail: string;
	href: string;
};

export type SafetyHandoff = {
	headline: string;
	body: string;
	contacts: SafetyContact[];
};

export type ChatResponse = {
	/**
	 * The conversation this turn belongs to. THE SERVER MINTS IT and the client's only job
	 * is to send it back on the next turn (docs/accounts-and-storage.md, Turn lifecycle) -
	 * without that, every turn opens a new conversation and the model sees no history.
	 * Absent when no turn was recorded, which is the guardrail block.
	 */
	conversationId?: string;
	/**
	 * The name the server gave this conversation, present ONLY on the turn that created it.
	 * The sidebar shows it instead of the placeholder it wrote itself, so a student sees the
	 * real title without waiting for a reload. Absent on every later turn, and absent when
	 * the server's titling produced nothing usable - in which case the conversation already
	 * has its first-message title and the row's placeholder is close enough to it.
	 */
	title?: string;
	conversationalText: string;
	/**
	 * Prose the model wrote after its cards. Renders BELOW the card group, which is what
	 * keeps a closing question under the answer instead of over it. Absent on the ordinary
	 * reply that ends with its cards, and always absent on a safety turn.
	 */
	trailingText?: string;
	statementBatches?: StatementBatch[];
	safetyHandoff?: SafetyHandoff;
	talkToPersonAvailable?: boolean;
};

/** One row of GET /conversations: a stored conversation header. */
export type ConversationSummary = {
	conversationId: string;
	title: string;
	createdAt?: string;
	lastActivityAt?: string;
	messageCount: number;
};

/**
 * One stored message from GET /conversations/{id} - the DISPLAY projection. Its cards are
 * the stored ones with their URLs already resolved, which is what the browser renders and
 * exactly what the model is never handed back.
 */
export type StoredMessage = {
	role: 'user' | 'assistant';
	text: string;
	cards: StatementCard[];
	createdAt?: string;
};

/**
 * A conversation as the sidebar holds it. `turns` is UNDEFINED until it has been fetched -
 * that is "not loaded yet", not "empty" - and a chat the student started in this tab has no
 * `conversationId` until the server's first reply mints one.
 *
 * This is a VIEW of server state, never the truth: nothing is written here that the server
 * was not told, and a reload rebuilds all of it from the read endpoints.
 */
export type ChatSession = {
	/** Stable React key. Deliberately not the conversation id: a new chat has no id yet. */
	id: string;
	conversationId?: string;
	title: string;
	turns?: ConversationTurn[];
};

/**
 * RAG content phases on the left panel. `conversational` is prose alone; `grid` is prose
 * plus the card group under it. There is no phase in which the prose is not on screen.
 */
export type RagPhase = 'conversational' | 'grid';

export type ConversationTurn = {
	id: string;
	text: string;
	/** Prose emitted after the cards; renders under the grid, never above it. */
	trailingText?: string;
	cards: StatementCard[];
	safetyHandoff?: SafetyHandoff;
	/** Active RAG flow phase; talk-only turns stay conversational. */
	phase: RagPhase | 'done';
	/**
	 * Whether this turn is still ARRIVING, and the one thing that decides whether it
	 * animates.
	 *
	 * THE INVARIANT: animating is a property of a turn arriving live, not of a turn being
	 * rendered. A reply typing itself out is Sammy answering in front of the student; a
	 * conversation reopened from the sidebar is finished, so it renders finished - whole
	 * text, cards already on the table, first frame.
	 *
	 * True for a turn built from a reply the browser just received, false for one read back
	 * out of storage (turnsFromStoredMessages) or out of the sidebar's cache of a turn it
	 * has already played (settleTurns). Nothing downstream re-derives this from position in
	 * the feed: being the newest turn is not the same as being a new turn.
	 */
	live: boolean;
	query?: string;
	createdAt: number;
};

export type FixtureId = 'rag' | 'ragAlt' | 'conversation' | 'safety';

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

/** One campus location, resolved server-side from a catalogue key the model wrote. */
export type PlaceCard = {
	key: string;
	name: string;
	address: string;
	directionsUrl: string;
	mapImageUrl?: string | null;
};

/** A message to a human, assembled server-side and sent by the student themselves. */
export type EmailDraft = {
	to: string;
	subject: string;
	body: string;
};

/** What one turn billed, as the server counted it (app/usage.py). */
export type TurnUsage = {
	/** Converse invocations, which is not one per message: a second search bills a second call. */
	modelCalls: number;
	inputTokens: number;
	outputTokens: number;
	/** The titling call's tokens, kept apart from the two above because a different and cheaper
	 * model wrote them (app/usage.py, record_title_call). */
	titleInputTokens: number;
	titleOutputTokens: number;
	guardrailContentUnits: number;
	retrievals: number;
};

/** A conversation's running total: every turn's usage, plus how many turns there were. */
export type ConversationUsage = TurnUsage & { messages: number };

export type ChatResponse = {
	/** The conversation this turn belongs to. */
	conversationId?: string;
	/** The name the server gave this conversation, present only on the turn that created it. */
	title?: string;
	conversationalText: string;
	/** Prose the model wrote after its cards. */
	trailingText?: string;
	statementBatches?: StatementBatch[];
	safetyHandoff?: SafetyHandoff;
	/** The campus location this turn points at, or absent, which is every turn the model named
	 * no place on, every key that was not in the catalogue, and every safety turn. */
	place?: PlaceCard;
	/** The email draft this turn offers to send to a human, or absent. */
	escalation?: EmailDraft;
	talkToPersonAvailable?: boolean;
	/** What this turn cost in billable units. */
	usage?: TurnUsage;
};

/** One row of GET /conversations: a stored conversation header. */
export type ConversationSummary = {
	conversationId: string;
	title: string;
	createdAt?: string;
	lastActivityAt?: string;
	messageCount: number;
};

/** One stored message from GET /conversations/{id}, the display projection. */
export type StoredMessage = {
	role: 'user' | 'assistant';
	text: string;
	/** Prose the model wrote below its card group, so a closing question comes back under the
	 * cards it refers to rather than folded into the lead-in above them. */
	trailingText?: string;
	cards: StatementCard[];
	/** Resolved from the stored reply's own keys, so a reopened crisis turn keeps its contacts. */
	safetyHandoff?: SafetyHandoff;
	/** The draft this turn offered, as it was assembled then, not rebuilt from live config. */
	escalation?: EmailDraft;
	/** The location this turn showed, as it resolved then, not re-resolved from the key. */
	place?: PlaceCard;
	createdAt?: string;
};

/** A conversation as the sidebar holds it. */
/** The title an unsent chat carries until it has been sent or the server has named it. */
export const UNSENT_CHAT_TITLE = 'New chat';

export type ChatSession = {
	/** Stable React key. Deliberately not the conversation id: a new chat has no id yet. */
	id: string;
	conversationId?: string;
	title: string;
	turns?: ConversationTurn[];
	/** What this conversation has billed since it was opened in this tab, accrued from the
	 * `usage` on each reply. */
	usage?: ConversationUsage;
};

/** RAG content phases on the left panel. */
export type RagPhase = 'conversational' | 'grid';

export type ConversationTurn = {
	id: string;
	text: string;
	/** The greeting a new chat opens with, and the only turn the frontend wrote itself. */
	welcome?: boolean;
	/** Prose emitted after the cards; renders under the grid, never above it. */
	trailingText?: string;
	cards: StatementCard[];
	safetyHandoff?: SafetyHandoff;
	/** The email draft this turn offered. */
	escalation?: EmailDraft;
	/** The campus location this turn pointed at. */
	place?: PlaceCard;
	/** Active RAG flow phase; talk-only turns stay conversational. */
	phase: RagPhase | 'done';
	/** Whether this turn is still arriving, and the one thing that decides whether it animates. */
	live: boolean;
	/** How much of this turn's prose a streamed preview already typed out. */
	revealedChars?: number;
	query?: string;
	createdAt: number;
};

export type FixtureId = 'rag' | 'ragAlt' | 'conversation' | 'safety';

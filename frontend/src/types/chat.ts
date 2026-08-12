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

/**
 * A message to a human, assembled server-side and sent by the student themselves.
 *
 * THESE ARE THE EXACT BYTES that go to the mail client, and they are the bytes on screen:
 * the component renders `to`, `subject` and `body` as selectable text and hands the same
 * three strings to `mailtoDraft`. Nothing in the browser writes, edits or extends a draft -
 * a preview that differed from what was sent would be worse than no preview.
 *
 * There is no `from`: the message leaves from whatever address the student's mail client is
 * signed in as, which is why a staff reply reaches them and why this path needs no verified
 * sending identity anywhere in the stack.
 */
export type EmailDraft = {
	to: string;
	subject: string;
	body: string;
};

/**
 * What ONE turn billed, as the server counted it (app/usage.py).
 *
 * Present on every reply the handler produces, including a guardrail block, which billed a
 * screen and nothing else. This is measurement, not estimate: the token counts come from
 * what Bedrock reported on each Converse call this turn actually made.
 */
export type TurnUsage = {
	/** Converse invocations, which is NOT one per message: a second search bills a second call. */
	modelCalls: number;
	inputTokens: number;
	outputTokens: number;
	guardrailContentUnits: number;
	retrievals: number;
};

/**
 * A conversation's running total: every turn's usage, plus how many turns there were.
 *
 * Accrued IN THIS TAB from the replies as they arrive. Nothing stores it, so a conversation
 * reopened from the sidebar starts empty rather than showing what it cost when it happened -
 * the panel says so rather than presenting a zero as a measurement.
 */
export type ConversationUsage = TurnUsage & { messages: number };

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
	/**
	 * The email draft this turn offers to send to a human, or absent.
	 *
	 * Absent is every turn the model did not tag, every deployment with no recipient
	 * configured, and every safety turn - the panel is the handoff there, and it owns the
	 * whole message under it.
	 */
	escalation?: EmailDraft;
	talkToPersonAvailable?: boolean;
	/**
	 * What this turn cost in billable units. Optional because a deployment older than the
	 * handler that reports it simply does not send one, and the meter then stays at zero
	 * rather than the panel breaking.
	 */
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

/**
 * One stored message from GET /conversations/{id} - the DISPLAY projection. Its cards are
 * the stored ones with their URLs already resolved, which is what the browser renders and
 * exactly what the model is never handed back.
 */
export type StoredMessage = {
	role: 'user' | 'assistant';
	text: string;
	cards: StatementCard[];
	/** The draft this turn offered, as it was assembled then - not rebuilt from live config. */
	escalation?: EmailDraft;
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
/**
 * The title an unsent chat carries until it has been sent or the server has named it.
 *
 * A SENTINEL, not a label: the sidebar renders it through the string catalogue, so the row
 * reads "Chat nuevo" in Spanish while the value compared against here stays one fixed
 * string. Storing the translated word instead would make "is this chat still unsent?" a
 * question about what language the student had chosen when they opened the tab.
 */
export const UNSENT_CHAT_TITLE = 'New chat';

export type ChatSession = {
	/** Stable React key. Deliberately not the conversation id: a new chat has no id yet. */
	id: string;
	conversationId?: string;
	title: string;
	turns?: ConversationTurn[];
	/**
	 * What this conversation has billed since it was opened in this tab, accrued from the
	 * `usage` on each reply. Undefined until a reply arrives, which is what the cost panel
	 * renders as "nothing metered yet" - honest for both a fresh chat and one reopened from
	 * history, since stored messages carry no usage.
	 */
	usage?: ConversationUsage;
};

/**
 * RAG content phases on the left panel. `conversational` is prose alone; `grid` is prose
 * plus the card group under it. There is no phase in which the prose is not on screen.
 */
export type RagPhase = 'conversational' | 'grid';

export type ConversationTurn = {
	id: string;
	text: string;
	/**
	 * The greeting a new chat opens with, and the only turn the frontend wrote itself.
	 *
	 * It is flagged because it is the one piece of prose whose language this app still owns:
	 * while the chat is untouched the greeting follows the language picker, and the moment a
	 * message is sent it is frozen into whatever it said at that point and never changes
	 * again (components/ChatApp.tsx). Every other turn is the model's and is left alone.
	 */
	welcome?: boolean;
	/** Prose emitted after the cards; renders under the grid, never above it. */
	trailingText?: string;
	cards: StatementCard[];
	safetyHandoff?: SafetyHandoff;
	/**
	 * The email draft this turn offered. Rendered under everything else, and never on a
	 * turn that carries a safetyHandoff - the server drops one before the other can exist.
	 */
	escalation?: EmailDraft;
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
	/**
	 * How much of this turn's prose a streamed preview already typed out.
	 *
	 * The hand-off at the end of a streamed turn: the preview types the lead-in while the
	 * reply is being written, then the authoritative payload arrives carrying the same
	 * prose. Without this the bubble would re-type from the top text the student has
	 * already read. Absent on the buffered path, where nothing was shown early.
	 */
	revealedChars?: number;
	query?: string;
	createdAt: number;
};

export type FixtureId = 'rag' | 'ragAlt' | 'conversation' | 'safety';

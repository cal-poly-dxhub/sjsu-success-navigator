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
	conversationalText: string;
	statementBatches?: StatementBatch[];
	safetyHandoff?: SafetyHandoff;
	talkToPersonAvailable?: boolean;
};

export type ChatHistoryMessage = {
	role: 'user' | 'assistant';
	text: string;
};

export type ChatSession = {
	id: string;
	title: string;
	response: ChatResponse;
};

/** RAG content phases on the left panel. */
export type RagPhase = 'conversational' | 'scroll' | 'grid';

export type ConversationTurn = {
	id: string;
	text: string;
	cards: StatementCard[];
	safetyHandoff?: SafetyHandoff;
	/** Active RAG flow phase; talk-only turns stay conversational. */
	phase: RagPhase | 'done';
	query?: string;
	createdAt: number;
};

export type FixtureId = 'rag' | 'ragAlt' | 'conversation' | 'safety';

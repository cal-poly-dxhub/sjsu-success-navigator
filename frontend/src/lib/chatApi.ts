import type {
	ChatResponse,
	ConversationSummary,
	StatementCard,
	StoredMessage,
} from '../types/chat';
import { authorizationHeader } from './auth';
import { loadRuntimeConfig } from './runtimeConfig';

export class ChatApiError extends Error {
	status?: number;

	constructor(message: string, status?: number) {
		super(message);
		this.name = 'ChatApiError';
		this.status = status;
	}
}

/** When a daily-limit refusal lifts, in the reader's own time. */
function localResetTime(resetAt: string): string | null {
	const reset = new Date(resetAt);
	if (Number.isNaN(reset.getTime())) return null;

	const time = reset.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
	// "at 5:00 PM" is only unambiguous if it is today.
	const sameDay = reset.toDateString() === new Date().toDateString();
	if (sameDay) return `at ${time}`;

	// The preposition travels with the phrase rather than sitting in the sentence template,
	// because the two halves need different ones: "at 5:03 PM" but "on Thursday at 5:03 PM".
	const day = reset.toLocaleDateString(undefined, { weekday: 'long' });
	return `on ${day} at ${time}`;
}

/** The daily-limit refusal, rewritten with a time the student can act on. */
function dailyLimitMessage(body: { limit?: number; resetAt?: string }): string | null {
	if (typeof body.limit !== 'number' || typeof body.resetAt !== 'string') return null;

	const when = localResetTime(body.resetAt);
	if (when === null) return null;

	return `You have reached your daily limit of ${body.limit} messages. You can ask Sammy again ${when}.`;
}

type PostChatOptions = {
	query: string;
	followup?: boolean;
	/** The conversation this turn continues, as the server named it on the previous reply. */
	conversationId?: string;
};

/** Throw a ChatApiError carrying whatever the failed response can be made to say. */
async function failureFrom(response: Response): Promise<ChatApiError> {
	let detail = response.statusText;
	try {
		// The handler returns {"error": ...}; camp's FastAPI returned {"detail": ...}.
		const body = (await response.json()) as {
			error?: string;
			detail?: string;
			limit?: number;
			resetAt?: string;
		};
		detail = body.error ?? body.detail ?? detail;
		// The per-user daily cap (app/ratelimit.py).
		if (response.status === 429) detail = dailyLimitMessage(body) ?? detail;
	} catch {
		// Keep statusText when error body is not JSON.
	}
	// Kept although an authorizer 401 is usually unreadable (see auth.ts): it is correct
	// wherever the response is readable, and costs one comparison.
	return new ChatApiError(detail, response.status);
}

/** One turn. */
export async function postChat(options: PostChatOptions): Promise<ChatResponse> {
	const config = await loadRuntimeConfig();
	// Throws before the request when the token is missing or expired.
	const auth = authorizationHeader();

	const response = await fetch(config.chatApiUrl, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', ...auth },
		body: JSON.stringify({
			query: options.query,
			followup: options.followup ?? false,
			conversationId: options.conversationId,
		}),
	});

	if (!response.ok) throw await failureFrom(response);

	return response.json() as Promise<ChatResponse>;
}

/** The signed-in student's own conversations, most recently active first. */
export async function fetchConversations(): Promise<ConversationSummary[]> {
	const config = await loadRuntimeConfig();
	const auth = authorizationHeader();

	const response = await fetch(config.conversationsApiUrl, { headers: auth });
	if (!response.ok) throw await failureFrom(response);

	const body = (await response.json()) as { conversations?: ConversationSummary[] };
	return body.conversations ?? [];
}

/** One conversation's stored messages, oldest first, with their cards already resolved. */
export async function fetchConversation(conversationId: string): Promise<StoredMessage[]> {
	const config = await loadRuntimeConfig();
	const auth = authorizationHeader();

	const response = await fetch(
		`${config.conversationsApiUrl}/${encodeURIComponent(conversationId)}`,
		{ headers: auth },
	);
	if (!response.ok) throw await failureFrom(response);

	const body = (await response.json()) as { messages?: StoredMessage[] };
	return body.messages ?? [];
}

/** Rename one conversation. */
export async function renameConversation(
	conversationId: string,
	title: string,
): Promise<string> {
	const config = await loadRuntimeConfig();
	const auth = authorizationHeader();

	const response = await fetch(
		`${config.conversationsApiUrl}/${encodeURIComponent(conversationId)}`,
		{
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json', ...auth },
			body: JSON.stringify({ title }),
		},
	);
	if (!response.ok) throw await failureFrom(response);

	const body = (await response.json()) as { title?: string };
	return body.title ?? title;
}

/** Delete one conversation and every message under it. */
export async function deleteConversation(conversationId: string): Promise<void> {
	const config = await loadRuntimeConfig();
	const auth = authorizationHeader();

	const response = await fetch(
		`${config.conversationsApiUrl}/${encodeURIComponent(conversationId)}`,
		{ method: 'DELETE', headers: auth },
	);
	if (!response.ok) throw await failureFrom(response);
}

export function incomingBatchFromResponse(response: ChatResponse): StatementCard[] {
	const batch = response.statementBatches?.[0];
	return batch?.cards?.slice(0, 4) ?? [];
}

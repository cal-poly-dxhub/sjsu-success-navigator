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

type PostChatOptions = {
	query: string;
	followup?: boolean;
	/**
	 * The conversation this turn continues, as the SERVER named it on the previous reply.
	 * Omitted on the first turn, which is how the server is asked to mint one.
	 */
	conversationId?: string;
};

/** Throw a ChatApiError carrying whatever the failed response can be made to say. */
async function failureFrom(response: Response): Promise<ChatApiError> {
	let detail = response.statusText;
	try {
		// The handler returns {"error": ...}; camp's FastAPI returned {"detail": ...}.
		// Both are read so a stale deployment does not surface as a blank message.
		const body = (await response.json()) as { error?: string; detail?: string };
		detail = body.error ?? body.detail ?? detail;
	} catch {
		// Keep statusText when error body is not JSON.
	}
	// Kept although an authorizer 401 is usually unreadable (see auth.ts): it is
	// correct wherever the response IS readable, and costs one comparison.
	return new ChatApiError(detail, response.status);
}

/**
 * One turn.
 *
 * THE BODY IS TWO FIELDS AND A FLAG. There is no `history` here and there is no session id:
 * the transcript is the server's (docs/accounts-and-storage.md, Turn lifecycle), and a
 * client-supplied one is a prompt injection vector rather than a memory optimisation - a
 * forged assistant turn lets an attacker establish rules the model then treats as its own
 * prior commitment. What the client sends back is the id the server gave it, and nothing
 * about a previous turn's content.
 */
export async function postChat(options: PostChatOptions): Promise<ChatResponse> {
	const config = await loadRuntimeConfig();
	// Throws BEFORE the request when the token is missing or expired. That ordering is
	// the point: an API Gateway authorizer rejects before the integration runs, so its
	// 401 carries no CORS headers and reaches the browser as an opaque failure with no
	// readable status. A request we know will fail is never sent.
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

/**
 * The signed-in student's own conversations, most recently active first.
 *
 * There is nothing to pass. The server reads the user out of the validated JWT, so this
 * cannot be asked for anybody else's list - which is also why the sidebar never needs to
 * know who it is looking at.
 */
export async function fetchConversations(): Promise<ConversationSummary[]> {
	const config = await loadRuntimeConfig();
	const auth = authorizationHeader();

	const response = await fetch(config.conversationsApiUrl, { headers: auth });
	if (!response.ok) throw await failureFrom(response);

	const body = (await response.json()) as { conversations?: ConversationSummary[] };
	return body.conversations ?? [];
}

/**
 * One conversation's stored messages, oldest first, with their cards already resolved.
 *
 * An id that is not the caller's own comes back as an empty list rather than an error: the
 * partition the server reads is built from the JWT, so a foreign id addresses nothing.
 */
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

/**
 * Rename one conversation. Returns the title as the server STORED it.
 *
 * The stored value is echoed back and used rather than the string that was typed, because
 * the server normalises it (whitespace, and the em and en dashes the whole app rewrites into
 * commas). Rendering the typed string instead would show a name the sidebar and a reload
 * disagree about.
 */
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

/**
 * Delete one conversation and every message under it. A hard delete, server side.
 *
 * There is nothing to send but the id, and the id is in the path: what gets deleted is
 * whatever that id names INSIDE the caller's own partition, which the server builds from the
 * JWT. So this cannot be pointed at anybody else's conversation, and it is idempotent - a
 * second click removes nothing and still succeeds.
 */
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

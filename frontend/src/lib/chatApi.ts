import type { ChatHistoryMessage, ChatResponse, StatementCard } from '../types/chat';
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
	sessionId?: string;
	history?: ChatHistoryMessage[];
};

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
			sessionId: options.sessionId,
			history: options.history?.length ? options.history : undefined,
		}),
	});

	if (!response.ok) {
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
		throw new ChatApiError(detail, response.status);
	}

	return response.json() as Promise<ChatResponse>;
}

export function incomingBatchFromResponse(response: ChatResponse): StatementCard[] {
	const batch = response.statementBatches?.[0];
	return batch?.cards?.slice(0, 4) ?? [];
}

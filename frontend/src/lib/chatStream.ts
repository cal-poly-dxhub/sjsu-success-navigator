/**
 * One turn over an ordinary HTTP response, read as it arrives, so the reply appears as it
 * is written. Only a 404 falls back to POST /chat, and only before the `accepted` frame. */

import type { ChatResponse } from '../types/chat';
import { ChatApiError } from './chatApi';
import { currentAccessToken } from './auth';

/** One string in three files: here, `_STREAM_EDGE_PATH_PREFIX` in infra_stack.py, and
 * `EDGE_PATH_PREFIX` in app/streaming_app.py. The infra suite reads all three off disk. */
export const STREAM_PATH_PREFIX = '/api';

/** Not `Authorization`, which the edge's own SigV4 signature owns. Spelled here and in
 * app/token_auth.py, and the infra suite compares the two. */
export const AUTH_HEADER_NAME = 'x-sjsu-authorization';

/** The body hash header the edge signs over. Named here for the same reason. */
const BODY_HASH_HEADER_NAME = 'x-amz-content-sha256';

/** The stream could not carry this turn, and the server had not taken it on yet. */
export class StreamUnavailable extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'StreamUnavailable';
	}
}

/** Frames the server sends, one per NDJSON line. */
type ServerFrame =
	| { type: 'accepted'; conversationId: string }
	| { type: 'status'; stage: string }
	| { type: 'delta'; text: string }
	| { type: 'final'; payload: ChatResponse }
	| {
			type: 'error';
			message: string;
			limit?: number;
			resetAt?: string;
			retryAfterSeconds?: number;
	  };

export type StreamHandlers = {
	/** The server has taken the turn on, under this conversation id. */
	onAccepted?: (conversationId: string) => void;
	/** Something is happening that produces no text yet, a retrieval, or cards starting. */
	onStatus?: (stage: string) => void;
	/** The reply so far, as prose. Append-only: each call is the whole preview to date. */
	onPreview?: (preview: string) => void;
};

export type StreamChatOptions = {
	query: string;
	followup?: boolean;
	conversationId?: string;
};

/** How long to wait with nothing arriving before giving up on a turn. */
const SILENCE_TIMEOUT_MS = 45_000;

/** The hex SHA-256 of what we are about to send. */
async function bodyHash(body: string): Promise<string> {
	const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body));
	return Array.from(new Uint8Array(digest))
		.map((byte) => byte.toString(16).padStart(2, '0'))
		.join('');
}

/** One turn, streamed. */
export async function streamChat(
	options: StreamChatOptions,
	handlers: StreamHandlers = {},
): Promise<ChatResponse> {
	// Throws before the request when the token is missing or expired, for the same reason
	// postChat checks first: a request we know will be refused is never sent.
	const token = currentAccessToken();

	const body = JSON.stringify({
		query: options.query,
		followup: options.followup ?? false,
		conversationId: options.conversationId,
	});

	// One turn's worth of state, so `accepted` can decide which error class a failure is:
	// Before it the caller may retry over POST /chat, after it it may not.
	let accepted = false;
	const failure = (message: string): Error =>
		accepted ? new ChatApiError(message) : new StreamUnavailable(message);

	// The silence timer aborts the fetch rather than merely rejecting, so a stalled response
	// releases its connection instead of being left to the tab.
	const abort = new AbortController();
	let silenceTimer = 0;
	let silent = false;
	const resetSilenceTimer = () => {
		window.clearTimeout(silenceTimer);
		silenceTimer = window.setTimeout(() => {
			silent = true;
			abort.abort();
		}, SILENCE_TIMEOUT_MS);
	};

	try {
		let response: Response;
		try {
			resetSilenceTimer();
			response = await fetch(`${STREAM_PATH_PREFIX}/chat`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					[BODY_HASH_HEADER_NAME]: await bodyHash(body),
					[AUTH_HEADER_NAME]: `Bearer ${token}`,
				},
				body,
				signal: abort.signal,
			});
		} catch (error) {
			// Nothing left this browser, or nothing came back, so the turn was never taken on.
			throw new StreamUnavailable(
				silent
					? 'Sammy did not answer in time.'
					: `Could not reach Sammy: ${String(error)}`,
			);
		}

		// The one status that falls back, because only a 404 means there is no door.
		if (response.status === 404) {
			throw new StreamUnavailable('There is no streaming route at this deployment.');
		}
		if (!response.ok) {
			throw new ChatApiError(
				`The live connection answered ${response.status}. Is the streaming endpoint healthy?`,
				response.status,
			);
		}
		if (!response.body) {
			// A browser with no streams, or a response the runtime buffered away.
			throw new StreamUnavailable('This browser cannot read a streamed reply.');
		}

		const reader = response.body.getReader();
		const decoder = new TextDecoder();
		// Whatever of the last line has arrived.
		let pending = '';
		let preview = '';

		const handle = (frame: ServerFrame): ChatResponse | null => {
			switch (frame.type) {
				case 'accepted':
					accepted = true;
					handlers.onAccepted?.(frame.conversationId);
					return null;
				case 'status':
					handlers.onStatus?.(frame.stage);
					return null;
				case 'delta':
					// Append-only by construction on the server, so this only ever grows.
					preview += frame.text;
					handlers.onPreview?.(preview);
					return null;
				case 'final':
					// The authoritative payload. Everything above it was a preview and is
					// thrown away.
					return frame.payload;
				case 'error':
					// The server saying something definite, a daily-limit refusal, a failed
					// loop.
					throw new ChatApiError(frame.message);
			}
			// A frame type this client does not know.
			return null;
		};

		const consume = (line: string): ChatResponse | null => {
			if (!line.trim()) return null;
			let frame: ServerFrame;
			try {
				frame = JSON.parse(line) as ServerFrame;
			} catch {
				// A line we cannot read is not a reason to abandon a turn: the final one may
				// still arrive and is the only one that decides anything.
				return null;
			}
			return handle(frame);
		};

		for (;;) {
			let chunk: ReadableStreamReadResult<Uint8Array>;
			try {
				chunk = await reader.read();
			} catch (error) {
				throw failure(
					silent
						? 'Sammy stopped responding partway through that answer.'
						: `The live connection dropped: ${String(error)}`,
				);
			}
			resetSilenceTimer();

			if (chunk.done) break;

			pending += decoder.decode(chunk.value, { stream: true });
			// Split on the last newline: everything before it is whole lines, what is left is
			// the start of the next frame.
			let newline = pending.indexOf('\n');
			while (newline !== -1) {
				const line = pending.slice(0, newline);
				pending = pending.slice(newline + 1);
				const payload = consume(line);
				if (payload) {
					// The final frame is the end of the turn, so the reader is released here.
					void reader.cancel().catch(() => {
						/* Already closed. Nothing to do and nothing to report. */
					});
					return payload;
				}
				newline = pending.indexOf('\n');
			}
		}

		// The body ended. A last line without its newline is still a frame.
		const payload = consume(pending + decoder.decode());
		if (payload) return payload;

		// A stream that ended without a `final` frame.
		throw failure('The live connection closed before the answer arrived.');
	} finally {
		window.clearTimeout(silenceTimer);
	}
}

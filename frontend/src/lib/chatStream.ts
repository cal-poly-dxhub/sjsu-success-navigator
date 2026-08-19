/**
 * One turn over an ordinary HTTP response, read as it arrives, so the reply appears as it
 * is written.
 *
 * A POST AND A STREAM READER, NOT `EventSource`. The turn carries a body - the query, the
 * follow-up flag and the conversation id - and `EventSource` can only issue a GET with no
 * body, so SSE was never available here whatever its framing is worth. `fetch` gives back
 * a `ReadableStream` that yields bytes as the edge forwards them, which is the same thing
 * an `onmessage` handler was doing and one fewer protocol.
 *
 * THE ROUTES ARE ON THIS PAGE'S OWN ORIGIN. `/api/*` is a behaviour on the same CloudFront
 * distribution that served this bundle, so a relative path is the whole address: no second
 * hostname, no CORS allowlist to keep in step, and no preflight in front of a request
 * whose entire value is time to first byte. STREAM_PATH_PREFIX is this side of a string
 * spelled once per language - see the constant.
 *
 * TWO HEADERS THAT ARE NOT `Authorization`, and neither is a preference:
 *
 * - `x-amz-content-sha256` is the SHA-256 of the body we are about to send, in hex. The
 *   edge signs each origin request with SigV4 (origin access control), and Lambda refuses
 *   an origin request whose payload is unsigned, so a client sending a body has to hand
 *   the edge the hash to sign over. It is a HASH, not a signature: it authenticates
 *   nothing on its own, it commits the request to its own bytes, and computing it needs no
 *   AWS credentials - which is why a browser can compute it and never holds any.
 * - the token rides `x-sjsu-authorization` because that SigV4 signature lives in
 *   `Authorization`, so a token in that header is a token CloudFront overwrites on the way
 *   past. The `/api/*` behaviour forwards every other viewer header, so a header of the
 *   app's own arrives intact and `app/token_auth.py` verifies it in process.
 *
 * WHAT ARRIVES IS A PREVIEW, AND IT IS NOT THE ANSWER. The `delta` frames carry prose only
 * - the server stops them at the first tag of the card contract - and nothing here parses
 * them, caps them or keeps them. The turn ends with ONE `final` frame carrying exactly the
 * payload POST /chat would have returned, and that is what the caller renders. The preview
 * is thrown away.
 *
 * FALLING BACK IS NOT UNCONDITIONAL, and the line is `accepted`. Before it, the server has
 * done nothing - nothing written, nothing billed - so any failure can safely become a
 * POST /chat: a 404 from a deployment that has no `/api` behind it, a 403 from the edge, a
 * dropped connection. AFTER it, the student's message is on record and the turn is running;
 * retrying over HTTP would ask the same question twice, bill it twice and store it twice.
 * So a failure there is reported instead, and the reply is still written server-side for
 * when they come back.
 */

import type { ChatResponse } from '../types/chat';
import { ChatApiError } from './chatApi';
import { currentAccessToken } from './auth';

/**
 * The prefix the edge routes at this app, and the frontend's half of a string spelled once
 * per language.
 *
 * CloudFront matches a behaviour on the viewer's path and forwards that path to the origin
 * UNCHANGED - there is no prefix-stripping short of a rewrite function - so this, the
 * distribution's path pattern (`_STREAM_EDGE_PATH_PREFIX` in infra_stack.py) and the
 * FastAPI router's prefix (`EDGE_PATH_PREFIX` in app/stream_probe.py) are one string in
 * three files. A mismatch synthesizes clean, deploys clean, and is a 404 from FastAPI
 * served through a distribution behaving exactly as configured, so the infra suite reads
 * all three off disk and compares them.
 */
export const STREAM_PATH_PREFIX = '/api';

/**
 * The header the streaming app reads the Cognito access token off, and the browser's half
 * of the same kind of contract: `AUTH_HEADER_NAME` in app/token_auth.py is the only other
 * place this string is written, and the infra suite compares the two.
 *
 * It is not `Authorization` because origin access control's SigV4 signature owns that
 * header on the origin request - see the module docstring.
 */
export const AUTH_HEADER_NAME = 'x-sjsu-authorization';

/** The body hash header the edge signs over. Named here for the same reason. */
const BODY_HASH_HEADER_NAME = 'x-amz-content-sha256';

/**
 * The stream could not carry this turn, and the server had not taken it on yet. The caller
 * answers this by asking the same question over POST /chat.
 */
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
	/**
	 * The server has taken the turn on, under this conversation id. THE FIRST FRAME, ahead
	 * of the retrieval status and every delta. Past this point there is no falling back.
	 */
	onAccepted?: (conversationId: string) => void;
	/** Something is happening that produces no text yet - a retrieval, or cards starting. */
	onStatus?: (stage: string) => void;
	/** The reply so far, as prose. Append-only: each call is the WHOLE preview to date. */
	onPreview?: (preview: string) => void;
};

export type StreamChatOptions = {
	query: string;
	followup?: boolean;
	conversationId?: string;
};

/**
 * How long to wait with nothing arriving before giving up on a turn.
 *
 * Generous against the server's own budget: the agent loop is capped at 22 seconds and the
 * function gets 60, so anything past this is a turn that is not coming rather than a slow
 * one. It is a SILENCE timer, reset by every chunk, not a total - a long reply that is
 * streaming steadily must never trip it.
 */
const SILENCE_TIMEOUT_MS = 45_000;

/**
 * The hex SHA-256 of what we are about to send.
 *
 * `crypto.subtle` needs a secure context, which https and localhost both are and which the
 * only two places this app runs both are. Encoded to UTF-8 bytes FIRST rather than hashing
 * the string, because the hash has to be over the bytes the fetch will actually put on the
 * wire - a body with an accented character in it hashes differently otherwise, and the
 * failure would be an edge signature that does not validate on exactly the turns whose
 * text is not ASCII.
 */
async function bodyHash(body: string): Promise<string> {
	const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body));
	return Array.from(new Uint8Array(digest))
		.map((byte) => byte.toString(16).padStart(2, '0'))
		.join('');
}

/**
 * One turn, streamed.
 *
 * Resolves with the authoritative `ChatResponse` off the `final` frame. Rejects with
 * `StreamUnavailable` while a fall back to POST /chat is still safe, and with
 * `ChatApiError` once it is not.
 */
export async function streamChat(
	options: StreamChatOptions,
	handlers: StreamHandlers = {},
): Promise<ChatResponse> {
	// Throws BEFORE the request when the token is missing or expired, for the same reason
	// postChat checks first: a request we know will be refused is never sent.
	const token = currentAccessToken();

	const body = JSON.stringify({
		query: options.query,
		followup: options.followup ?? false,
		conversationId: options.conversationId,
	});

	// One turn's worth of state, so `accepted` can decide which error class a failure is:
	// before it the caller may retry over POST /chat, after it it may not.
	let accepted = false;
	const failure = (message: string): Error =>
		accepted ? new ChatApiError(message) : new StreamUnavailable(message);

	// The silence timer aborts the fetch rather than merely rejecting, so a stalled
	// response releases its connection instead of being left to the tab.
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
			// Nothing left this browser, or nothing came back. Either way the server never
			// saw the turn, so this is always the retryable class.
			throw new StreamUnavailable(
				silent
					? 'Sammy did not answer in time.'
					: `Could not reach Sammy: ${String(error)}`,
			);
		}

		// EVERY NON-2xx IS RETRYABLE, and that is a property of where the refusals live
		// rather than a hopeful default: the app answers a status code only for things it
		// decides BEFORE the first byte of the body - a malformed body, an over-long query,
		// an unverifiable token - and everything the server commits to (the daily cap, a
		// failed loop) arrives as an `error` FRAME inside a 200. So a status here means no
		// turn was taken on, and asking POST /chat the same question is free of consequence.
		if (!response.ok) {
			throw new StreamUnavailable(
				`The live connection answered ${response.status}.`,
			);
		}
		if (!response.body) {
			// A browser with no streams, or a response the runtime buffered away. Neither is
			// a turn the server started.
			throw new StreamUnavailable('This browser cannot read a streamed reply.');
		}

		const reader = response.body.getReader();
		const decoder = new TextDecoder();
		// Whatever of the last line has arrived. NDJSON is newline-delimited and a chunk
		// boundary lands wherever the network put it, so a frame routinely arrives in two
		// pieces and is only parseable once its newline does.
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
					// THE AUTHORITATIVE PAYLOAD. Everything above it was a preview.
					return frame.payload;
				case 'error':
					// The server saying something definite - a daily-limit refusal, a failed
					// loop. NOT a transport failure, so it is a ChatApiError whatever stage
					// we are at: retrying it over HTTP would ask a question that has already
					// been answered with "no".
					throw new ChatApiError(frame.message);
			}
			// A frame type this client does not know. Ignored rather than fatal, for the
			// same reason the server ignores an unknown Bedrock event: a new frame type must
			// not be able to fail a turn that is otherwise going fine.
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
			// Split on the LAST newline: everything before it is whole lines, what is left
			// is the start of the next frame.
			let newline = pending.indexOf('\n');
			while (newline !== -1) {
				const line = pending.slice(0, newline);
				pending = pending.slice(newline + 1);
				const payload = consume(line);
				if (payload) {
					// The final frame is the end of the turn. Cancel rather than drain: the
					// server sends nothing after it, and a reader left open holds the
					// connection until the function returns.
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

		// A stream that ended without a `final` frame. Before `accepted` this is a
		// deployment with nothing behind `/api`; after it, a turn that died mid-flight.
		throw failure('The live connection closed before the answer arrived.');
	} finally {
		window.clearTimeout(silenceTimer);
	}
}

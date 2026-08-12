/**
 * One turn over a WebSocket, so the reply appears as it is written.
 *
 * THE TRANSPORT IS CHOSEN BY CONFIG, NOT BY CODE. `streamingApiUrl` is stamped into
 * config.json only when the stack synthesized a WebSocket API, so a deployment with
 * streaming off gives the browser no URL to open and this module is never called. There is
 * no flag to read and no branch to get wrong - see `isStreamingAvailable`.
 *
 * WHAT ARRIVES IS A PREVIEW, AND IT IS NOT THE ANSWER. The `delta` frames carry prose only
 * - the server stops them at the first tag of the card contract - and nothing here parses
 * them, caps them or keeps them. The turn ends with ONE `final` frame carrying exactly the
 * payload POST /chat would have returned, and that is what the caller renders. The preview
 * is thrown away.
 *
 * A CONNECTION PER TURN, deliberately, rather than one held open. It costs a handshake
 * (~100ms against a wait this feature exists to cut from ~9 seconds), and it buys: a fresh
 * token every turn instead of one aging inside a long-lived socket, no reconnect logic, and
 * no interaction at all with API Gateway's 10-minute idle and 2-hour hard limits.
 *
 * FALLING BACK IS NOT UNCONDITIONAL, and the line is `accepted`. Before it, the server has
 * done nothing - nothing written, nothing billed - so any failure can safely become a
 * POST /chat, which is what a blocked WebSocket port on campus wifi looks like. AFTER it,
 * the student's message is on record and a generation worker is running; retrying over HTTP
 * would ask the same question twice, bill it twice and store it twice. So a failure there
 * is reported instead, and the reply is still written server-side for when they come back.
 */

import type { ChatResponse } from '../types/chat';
import { ChatApiError } from './chatApi';
import { currentAccessToken } from './auth';
import { loadRuntimeConfig } from './runtimeConfig';

/**
 * The socket could not carry this turn, and the server had not taken it on yet. The caller
 * answers this by asking the same question over POST /chat.
 */
export class StreamUnavailable extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'StreamUnavailable';
	}
}

/** Frames the server sends. `turnId` is on every one of them. */
type ServerFrame =
	| { type: 'accepted'; turnId: string; conversationId: string }
	| { type: 'status'; turnId: string; stage: string }
	| { type: 'delta'; turnId: string; text: string }
	| { type: 'final'; turnId: string; payload: ChatResponse }
	| {
			type: 'error';
			turnId: string;
			message: string;
			limit?: number;
			resetAt?: string;
			retryAfterSeconds?: number;
	  };

export type StreamHandlers = {
	/** The server has taken the turn on. Past this point there is no falling back. */
	onAccepted?: (conversationId: string) => void;
	/** Something is happening that produces no text yet - a retrieval. */
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
 * worker gets 60, so anything past this is a turn that is not coming rather than a slow
 * one. It is a SILENCE timer, reset by every frame, not a total - a long reply that is
 * streaming steadily must never trip it.
 */
const SILENCE_TIMEOUT_MS = 45_000;

/** Whether this deployment has a socket at all. The absence of the URL is the gate. */
export async function isStreamingAvailable(): Promise<boolean> {
	try {
		const config = await loadRuntimeConfig();
		return Boolean(config.streamingApiUrl);
	} catch {
		// config.json is unreadable, which the chat path surfaces on its own. No socket.
		return false;
	}
}

export async function streamChat(
	options: StreamChatOptions,
	handlers: StreamHandlers = {},
): Promise<ChatResponse> {
	const config = await loadRuntimeConfig();
	if (!config.streamingApiUrl) {
		throw new StreamUnavailable('This deployment has no streaming endpoint.');
	}
	// Throws BEFORE the socket is opened when the token is missing or expired, for the same
	// reason postChat checks first: a rejected handshake is unreadable from JavaScript.
	const token = currentAccessToken();

	const url = `${config.streamingApiUrl}?token=${encodeURIComponent(token)}`;

	return new Promise<ChatResponse>((resolve, reject) => {
		let socket: WebSocket;
		try {
			socket = new WebSocket(url);
		} catch (error) {
			reject(new StreamUnavailable(`Could not open a socket: ${String(error)}`));
			return;
		}

		let accepted = false;
		let settled = false;
		let preview = '';
		let silenceTimer = 0;

		const settle = (finish: () => void) => {
			if (settled) return;
			settled = true;
			window.clearTimeout(silenceTimer);
			try {
				socket.close();
			} catch {
				/* Already closing. Nothing to do and nothing to report. */
			}
			finish();
		};

		/**
		 * Give up on this turn. Which error class it is decides whether the caller may
		 * retry over HTTP, so this is the one place the `accepted` line is drawn.
		 */
		const fail = (message: string) =>
			settle(() =>
				reject(
					accepted
						? new ChatApiError(message)
						: new StreamUnavailable(message),
				),
			);

		const resetSilenceTimer = () => {
			window.clearTimeout(silenceTimer);
			silenceTimer = window.setTimeout(
				() => fail('Sammy stopped responding partway through that answer.'),
				SILENCE_TIMEOUT_MS,
			);
		};

		socket.onopen = () => {
			resetSilenceTimer();
			socket.send(
				JSON.stringify({
					// The API's route selection expression is `$request.body.action`, so this
					// field is what picks the message route rather than falling to $default.
					action: 'sendMessage',
					query: options.query,
					followup: options.followup ?? false,
					conversationId: options.conversationId,
				}),
			);
		};

		socket.onmessage = (event: MessageEvent) => {
			resetSilenceTimer();
			let frame: ServerFrame;
			try {
				frame = JSON.parse(String(event.data)) as ServerFrame;
			} catch {
				// A frame we cannot read is not a reason to abandon a turn: the final one may
				// still arrive and is the only one that decides anything.
				return;
			}

			switch (frame.type) {
				case 'accepted':
					accepted = true;
					handlers.onAccepted?.(frame.conversationId);
					return;
				case 'status':
					handlers.onStatus?.(frame.stage);
					return;
				case 'delta':
					// Append-only by construction on the server, so this only ever grows.
					preview += frame.text;
					handlers.onPreview?.(preview);
					return;
				case 'final':
					// THE AUTHORITATIVE PAYLOAD. Everything above it was a preview.
					settle(() => resolve(frame.payload));
					return;
				case 'error':
					// The server saying something definite - a daily-limit refusal, a failed
					// loop. NOT a socket failure, so it is a ChatApiError whatever stage we
					// are at: retrying it over HTTP would ask a question that has already
					// been answered with "no".
					settle(() => reject(new ChatApiError(frame.message)));
					return;
			}
		};

		socket.onerror = () => {
			// A browser deliberately tells JavaScript nothing about why a socket failed, so
			// there is no status to read and nothing more specific to say here.
			fail('Could not reach Sammy over the live connection.');
		};

		socket.onclose = () => {
			// Ordinary after `final` - settle() closes it - and a failure before one.
			fail('The live connection closed before the answer arrived.');
		};
	});
}

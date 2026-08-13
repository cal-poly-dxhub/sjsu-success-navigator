import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LayoutGroup, motion } from 'motion/react';
import { UNSENT_CHAT_TITLE } from '../types/chat';
import type { ChatResponse, ChatSession, ConversationTurn, RagPhase } from '../types/chat';
import {
	ChatApiError,
	deleteConversation,
	fetchConversation,
	fetchConversations,
	incomingBatchFromResponse,
	postChat,
	renameConversation,
} from '../lib/chatApi';
import { StreamUnavailable, streamChat } from '../lib/chatStream';
import {
	appendConversationTurn,
	archiveActiveTurns,
	createConversationTurn,
	settleTurns,
	turnsFromResponse,
	turnsFromStoredMessages,
} from '../lib/conversationTurns';
import { CARDS_STAGE } from './PendingExchange';
import { waitingDeck } from '../lib/waitingDeck';
import { Composer } from './Composer';
import { ConversationFeed } from './ConversationFeed';
import { SammyStage } from './SammyStage';
import { SettingsPanel } from './SettingsPanel';
import { SideNav } from './SideNav';
import { SjsuCaresModal } from './SjsuCaresModal';
import { TalkToPersonPill } from './TalkToPersonPill';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { addTurnUsage } from '../lib/costModel';
import { strings, useStrings } from '../lib/i18n';
import { currentUsername, signOut } from '../lib/auth';
import { loadRuntimeConfig } from '../lib/runtimeConfig';
import type { CostModel } from '../lib/runtimeConfig';
import { inferSjsuCaresServiceTheme } from '../lib/sjsuCares';
import './ChatApp.css';

const CHAT_FADE_OUT_MS = 160;
const CHAT_FADE_IN_MS = 640;
const CHAT_FADE_IN_HOLD_MS = Math.round(620 * 0.5);
const CHAT_WIPE_MS = 620;
const SPEECH_INTRO_DELAY_MS = 1100;
const BG_TILE_PX = 480;

/**
 * Whether the desktop rail is collapsed, remembered across reloads.
 *
 * A UI preference and nothing else - it is the one thing in this app that is allowed in
 * localStorage. The access token deliberately is not (lib/auth.ts), and this is not a
 * loophole in that: it says how wide a panel is, it is not read by anything that talks to
 * the server, and a shared campus machine handing the next student a narrow sidebar is
 * not a disclosure. A read failure (Safari private mode throws on access) means the rail
 * simply starts open.
 */
const NAV_COLLAPSED_KEY = 'ssn.nav.collapsed';

function readNavCollapsed(): boolean {
	if (typeof window === 'undefined') return false;
	try {
		return window.localStorage.getItem(NAV_COLLAPSED_KEY) === '1';
	} catch {
		return false;
	}
}

type TransitionDirection = 'left' | 'right';

type ChatTransition = {
	id: number;
	direction: TransitionDirection;
	fromX: number;
	toX: number;
};

function tileAlignedSlideDistance(): number {
	if (typeof window === 'undefined') return BG_TILE_PX;
	const vw = window.innerWidth;
	return Math.max(BG_TILE_PX, Math.ceil(vw / BG_TILE_PX) * BG_TILE_PX);
}

/**
 * The opening greeting. This is REAL UI COPY, not fixture data - it was the one thing in
 * camp's chatFixtures.ts worth keeping, so it moved here when that file was deleted along
 * with the four fake conversations it also held. Being UI copy is exactly why it lives in
 * the string catalogue now and is read at the moment a chat is created rather than being a
 * module constant fixed at import.
 */
function welcomeResponse(): ChatResponse {
	return {
		conversationalText: strings().welcome,
		talkToPersonAvailable: true,
		statementBatches: [],
	};
}

/**
 * The unsent chat at the top of the sidebar. It holds the welcome turn and NO conversation
 * id: the server mints one on the first reply (docs/accounts-and-storage.md, Turn
 * lifecycle), and until then there is nothing stored to point at.
 *
 * Camp shipped four canned conversations (MOCK_CHAT_SESSIONS) whose fabricated answers were
 * indistinguishable from real ones. The rest of this list now comes from the server, which
 * is the only thing that ever knew what a student actually asked.
 */
function newChatSession(): ChatSession {
	return {
		id: `new-${Date.now()}`,
		title: UNSENT_CHAT_TITLE,
		// Flagged so the feed can keep re-rendering it in the chosen language until this
		// chat is used - see `shownTurns` below, which is where that actually happens.
		turns: turnsFromResponse(welcomeResponse()).map((turn) => ({ ...turn, welcome: true })),
	};
}

export default function ChatApp() {
	const t = useStrings();
	const reduceMotion = usePrefersReducedMotion();
	const [initialChat] = useState(newChatSession);
	const [turns, setTurns] = useState<ConversationTurn[]>(() => initialChat.turns ?? []);
	const [isTalking, setIsTalking] = useState(false);
	const [chats, setChats] = useState<ChatSession[]>([initialChat]);
	const [activeChatId, setActiveChatId] = useState(initialChat.id);
	const [historyLoading, setHistoryLoading] = useState(true);
	const [historyError, setHistoryError] = useState<string | null>(null);
	const [openingChatId, setOpeningChatId] = useState<string | null>(null);
	const [navOpen, setNavOpen] = useState(false);
	const [navCollapsed, setNavCollapsed] = useState(readNavCollapsed);
	const [contentVisible, setContentVisible] = useState(true);
	const [isShocked, setIsShocked] = useState(false);
	const [chatTransition, setChatTransition] = useState<ChatTransition | null>(null);
	const [bgOffsetX, setBgOffsetX] = useState(0);
	const [speechUsesIntro, setSpeechUsesIntro] = useState(true);
	const [panelSwappedHidden, setPanelSwappedHidden] = useState(false);
	const [isLoading, setIsLoading] = useState(false);
	const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
	/**
	 * The reply as it arrives over the socket, and what the server says it is doing while
	 * none has yet. Both are PREVIEW state: they live only for the length of one pending
	 * exchange and are cleared the moment the authoritative payload lands, which is what
	 * builds the turn. Nothing is ever rendered from them after that.
	 */
	const [pendingPreview, setPendingPreview] = useState('');
	const [pendingStage, setPendingStage] = useState<string | null>(null);
	/**
	 * The turn that replaced a pending exchange, so the feed knows its bubble is not a new
	 * one. A pending exchange holds a bubble from the moment it opens - the indicator lives
	 * in it - and the turn built from the reply renders in its place, in the same commit. The
	 * turn is still arriving and still types itself out; what this records is that its bubble
	 * has already made its entrance and must not make another over prose already on screen.
	 *
	 * Set for every turn that resolves one, streamed or buffered, including a failure. Never
	 * set for the welcome turn, which opens the only bubble on a fresh screen.
	 */
	const [continuedTurnId, setContinuedTurnId] = useState<string | null>(null);
	/**
	 * Whether this deployment has a socket at all - i.e. whether the stack stamped
	 * `streamingApiUrl` into config.json. False until config.json has been read, so the
	 * first turn on a cold page uses POST /chat rather than waiting on a fetch to find out.
	 */
	const [streamingReady, setStreamingReady] = useState(false);
	/**
	 * Whether this deployment has a mailbox to escalate to (config.json's
	 * escalationRecipient). False until config.json has been read, and false for good in a
	 * deployment that configured none - which is what keeps the component out of the page
	 * rather than out of view. The server holds the other end of the same gate: with no
	 * recipient the system prompt never mentions the tag, so no reply carries a draft.
	 */
	const [escalationEnabled, setEscalationEnabled] = useState(false);
	const [showSjsuCaresModal, setShowSjsuCaresModal] = useState(false);
	const [showSettings, setShowSettings] = useState(false);
	/**
	 * The cost model, or null when the stack did not stamp one.
	 *
	 * Loaded on mount rather than at build time for the same reason the API URL is: it does
	 * not exist until `cdk deploy` writes config.json. Null covers both "the section is off"
	 * and "config.json could not be read at all" - the cost section simply does not render
	 * inside settings, which is the right outcome for both. A failure here must never break
	 * the chat, so the fetch swallows its error: this is a demo instrument, not part of
	 * answering a student. Settings itself does not wait on any of it.
	 */
	const [costModel, setCostModel] = useState<CostModel | null>(null);

	useEffect(() => {
		let cancelled = false;
		void loadRuntimeConfig()
			.then((config) => {
				if (cancelled) return;
				setCostModel(config.costModel ?? null);
				// THE ABSENCE OF THE URL IS THE GATE. With streaming off the stack stamps no
				// key, so there is nothing here to open and every turn takes POST /chat.
				setStreamingReady(Boolean(config.streamingApiUrl));
				// And again for the escalation path. The recipient itself is not used to
				// address anything here - a draft carries its own destination, as it was
				// addressed when the turn happened - so its PRESENCE is all this reads.
				setEscalationEnabled(Boolean(config.escalationRecipient));
			})
			.catch(() => {
				/* No cost panel and no socket. The chat surfaces its own config failures. */
			});
		return () => {
			cancelled = true;
		};
	}, []);
	// Straight off the last reply's `talkToPersonAvailable`, defaulting to shown. It used to
	// be read back out of a ChatResponse this component rebuilt from its own turns, which
	// could only ever return the default - the turns never carried the field.
	const [talkToPersonAvailable, setTalkToPersonAvailable] = useState(true);
	const [lastUserQuery, setLastUserQuery] = useState<string | null>(null);
	const [userEmail] = useState(() => currentUsername());
	const transitionId = useRef(0);

	const isTransitioning = Boolean(chatTransition);
	const hasContent = turns.length > 0;
	const activeChat = chats.find((chat) => chat.id === activeChatId);

	/**
	 * The conversation list, on load. This is the whole reason the sidebar is not a lie any
	 * more: it is read from the server under the signed-in student's own JWT, so it lists
	 * their conversations and cannot be asked for anyone else's.
	 */
	useEffect(() => {
		let cancelled = false;

		fetchConversations()
			.then((conversations) => {
				if (cancelled) return;
				setChats((current) => [
					// The unsent chats stay at the top - they are not stored yet, so a fetch
					// cannot have returned them, and dropping them would take the welcome
					// screen out from under the student mid-read.
					...current.filter((chat) => !chat.conversationId),
					...conversations.map((conversation) => ({
						id: conversation.conversationId,
						conversationId: conversation.conversationId,
						title: conversation.title,
					})),
				]);
			})
			.catch((error: unknown) => {
				if (cancelled) return;
				setHistoryError(
					error instanceof ChatApiError
						? strings().chatsLoadFailedWith(error.message)
						: strings().chatsLoadFailed,
				);
			})
			.finally(() => {
				if (!cancelled) setHistoryLoading(false);
			});

		return () => {
			cancelled = true;
		};
	}, []);

	/**
	 * Landing vs active chat, and the only thing the mobile layout switches on. A turn
	 * carries a `query` only once the student has actually asked something, so this reads
	 * the real conversation rather than a flag anyone has to remember to set - including
	 * after "New chat", which returns to the welcome turn and therefore to the landing.
	 */
	const conversationStarted = Boolean(pendingPrompt) || turns.some((turn) => Boolean(turn.query));

	/**
	 * The turns as they are shown, which differs from what is stored in exactly one case: an
	 * UNUSED chat's greeting is rendered in the language chosen right now. Switching to
	 * Tiếng Việt on the landing screen should change the sentence on the landing screen -
	 * it is the app's own copy and nobody has asked anything yet.
	 *
	 * Once `conversationStarted` is true this passes `turns` straight through, so the frozen
	 * text written by beginPendingExchange is what stays on screen. The Typewriter treats a
	 * REPLACED string as a new message and re-types it (components/Typewriter.tsx), which is
	 * the right behaviour here: the greeting is being said again, in another language.
	 */
	const shownTurns = useMemo(
		() =>
			conversationStarted
				? turns
				: turns.map((turn) => (turn.welcome ? { ...turn, text: t.welcome } : turn)),
		[conversationStarted, t, turns],
	);

	const settleBackground = useCallback((landedId: number, toX: number) => {
		setChatTransition((current) => {
			if (!current || current.id !== landedId) return current;
			setBgOffsetX(toX);
			return null;
		});
	}, []);

	const scrollTop = useCallback(() => {
		window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
	}, [reduceMotion]);

	/**
	 * Keep the sidebar's copy of a conversation in step with the feed.
	 *
	 * This is a CACHE of what is on screen, not a record: it exists so switching away and
	 * back does not re-fetch, and every line of it is either something the server sent or
	 * something the server was just told. A reload throws all of it away and asks again.
	 *
	 * What it stores is SETTLED, and that is the whole difference between the feed and the
	 * cache. The feed holds a turn that is arriving and types it out; the cache holds the
	 * same turn once it has arrived, so coming back to this conversation shows the answer
	 * rather than performing it a second time. A turn read from here is in exactly the state
	 * one read from the server is (turnsFromStoredMessages), which is why the two ways back
	 * into a conversation look the same.
	 */
	const updateChat = useCallback((id: string, nextTurns: ConversationTurn[]) => {
		const settled = settleTurns(nextTurns);
		setChats((current) =>
			current.map((chat) => (chat.id === id ? { ...chat, turns: settled } : chat)),
		);
	}, []);

	const handlePhaseChange = useCallback(
		(turnId: string, phase: RagPhase | 'done') => {
			setTurns((current) => {
				const next = current.map((turn) => (turn.id === turnId ? { ...turn, phase } : turn));
				updateChat(activeChatId, next);
				return next;
			});
		},
		[activeChatId, updateChat],
	);

	const showChat = useCallback(
		(chat: ChatSession) => {
			const nextTurns = chat.turns ?? [];
			setSpeechUsesIntro(true);
			setActiveChatId(chat.id);
			setTurns(nextTurns);
			setIsTalking(false);
			setPendingPrompt(null);
			setNavOpen(false);
			scrollTop();
		},
		[scrollTop],
	);

	const beginPendingExchange = useCallback(
		(query: string) => {
			setTurns((current) => {
				// THE GREETING IS FROZEN HERE, in the language it was being read in, because
				// this is the instant the chat stops being new. After this the conversation
				// has a language of its own - the student's first message set it and the
				// model answers in kind - and a greeting that kept following the picker
				// would be the one line in the thread rewriting itself under them.
				const archived = archiveActiveTurns(current).map((turn) =>
					turn.welcome ? { ...turn, text: strings().welcome } : turn,
				);
				updateChat(activeChatId, archived);
				return archived;
			});
			setPendingPrompt(query);
			setSpeechUsesIntro(false);
		},
		[activeChatId, updateChat],
	);

	const applyChatResponse = useCallback(
		(next: ChatResponse, query: string, revealedChars?: number) => {
			const incomingCards = incomingBatchFromResponse(next);
			const turn = createConversationTurn(next.conversationalText, {
				cards: incomingCards,
				trailingText: next.trailingText,
				safetyHandoff: next.safetyHandoff,
				// The draft the server assembled for THIS turn, carried straight onto it. The
				// component renders these bytes; nothing here builds, edits or addresses one.
				escalation: next.escalation,
				query,
				// What a streamed preview already typed out, so the finished turn picks up
				// where it stopped instead of replaying prose the student has read. This is
				// the ONLY thing the preview leaves behind: the turn itself is built from the
				// authoritative payload, exactly as it is on the buffered path.
				revealedChars,
			});

			// THE ID THE SERVER MINTED, kept so the NEXT turn can say which conversation it
			// belongs to. Without this the client posts no id, the server mints a fresh one
			// every time, and the model is handed an empty history on every message - which
			// is exactly the bug this replaces. It is recorded once and never overwritten:
			// the server echoes the same id back for the life of the conversation, and it is
			// absent on a guardrail block, where no turn was recorded to belong to.
			// One pass over the sidebar for both things this reply can add to the active
			// chat: the id the server minted, and what the turn billed. Usage is folded in
			// whatever else the reply carried - a guardrail block has no conversation id and
			// still spent money on the screen that blocked it.
			setChats((current) =>
				current.map((chat) => {
					if (chat.id !== activeChatId) return chat;

					// THE CONVERSATION'S RUNNING METER, accrued from what the server counted
					// on each reply (app/usage.py). Held per chat rather than per tab, so
					// switching conversations switches the figure with it, and never written
					// anywhere: a chat reopened from history has no meter, because stored
					// messages do not carry what they cost.
					const metered = next.usage
						? { ...chat, usage: addTurnUsage(chat.usage, next.usage) }
						: chat;

					// THE ID THE SERVER MINTED, kept so the NEXT turn can say which
					// conversation it belongs to. Without this the client posts no id, the
					// server mints a fresh one every time, and the model is handed an empty
					// history on every message - which is exactly the bug this replaces. It
					// is recorded once and never overwritten: the server echoes the same id
					// back for the life of the conversation, and it is absent on a guardrail
					// block, where no turn was recorded to belong to.
					if (!next.conversationId || metered.conversationId) return metered;
					return {
						...metered,
						conversationId: next.conversationId,
						// THE SERVER'S NAME FOR THIS CONVERSATION, present only on the turn
						// that created it. It replaces the placeholder this component wrote
						// from the same message a moment ago, so the row says what a reload
						// would say. Absent when the server's titling produced nothing usable,
						// in which case the placeholder and the stored fallback title are the
						// same truncation of the same sentence.
						title: next.title ?? metered.title,
					};
				}),
			);

			setTalkToPersonAvailable(next.talkToPersonAvailable ?? true);
			setPendingPrompt(null);
			setContinuedTurnId(turn.id);
			setTurns((current) => {
				const nextTurns = appendConversationTurn(current, turn);
				updateChat(activeChatId, nextTurns);
				return nextTurns;
			});
		},
		[activeChatId, updateChat],
	);

	const transitionToChat = useCallback(
		async (chat: ChatSession, direction: TransitionDirection) => {
			if (chat.id === activeChatId || isTransitioning) {
				setNavOpen(false);
				return;
			}

			if (reduceMotion) {
				showChat(chat);
				return;
			}

			const id = ++transitionId.current;
			const fromX = bgOffsetX;
			const toX =
				direction === 'left'
					? fromX - tileAlignedSlideDistance()
					: fromX + tileAlignedSlideDistance();
			setNavOpen(false);
			setIsShocked(true);
			setChatTransition({ id, direction, fromX, toX });
			setPanelSwappedHidden(false);
			setContentVisible(false);

			await new Promise((resolve) => window.setTimeout(resolve, CHAT_FADE_OUT_MS));
			if (transitionId.current !== id) return;
			showChat(chat);
			setPanelSwappedHidden(true);

			await new Promise((resolve) => window.setTimeout(resolve, CHAT_FADE_IN_HOLD_MS));
			if (transitionId.current !== id) return;
			setPanelSwappedHidden(false);
			setContentVisible(true);

			await new Promise((resolve) =>
				window.setTimeout(resolve, CHAT_WIPE_MS - CHAT_FADE_IN_HOLD_MS),
			);
			if (transitionId.current !== id) return;
			settleBackground(id, toX);
			setIsShocked(false);
		},
		[activeChatId, bgOffsetX, isTransitioning, reduceMotion, settleBackground, showChat],
	);

	/**
	 * One turn. The request carries the query, the follow-up flag and THE CONVERSATION ID
	 * THE SERVER GAVE US - and nothing else. No transcript: the server holds that
	 * (docs/accounts-and-storage.md, Turn lifecycle), and a client-supplied one would be a
	 * way to put words in a previous turn's mouth rather than a memory shortcut.
	 *
	 * TWO TRANSPORTS, ONE OUTCOME. When the stack stamped a WebSocket URL into config.json
	 * the turn goes over a socket and the prose arrives as it is written; otherwise, and on
	 * any socket failure the server had not yet taken responsibility for, it goes over
	 * POST /chat exactly as it always has. What gets RENDERED is the same object either
	 * way - the streamed turn ends in one final payload that is byte-for-byte what
	 * POST /chat would have returned - so nothing below this function knows which ran.
	 */
	const sendTurn = (query: string, options?: { followup?: boolean }) => {
		if (isLoading || isTransitioning || openingChatId) return;

		setLastUserQuery(query);
		const conversationId = activeChat?.conversationId;
		beginPendingExchange(query);
		setIsLoading(true);

		const failWith = (error: unknown) => {
			const message =
				error instanceof ChatApiError
					? error.message
					: t.turnFailed;
			setPendingPrompt(null);
			setPendingPreview('');
			setPendingStage(null);
			const turn = createConversationTurn(message, { query });
			setContinuedTurnId(turn.id);
			setTurns((current) => {
				const nextTurns = appendConversationTurn(current, turn);
				updateChat(activeChatId, nextTurns);
				return nextTurns;
			});
		};

		const buffered = () =>
			postChat({ query, followup: options?.followup, conversationId }).then((next) =>
				applyChatResponse(next, query),
			);

		const streamed = () => {
			// Held in locals rather than read back out of state: the final payload arrives
			// in the same tick as the last delta, and a state read there would be stale.
			let previewed = 0;
			let stage: string | null = null;
			return streamChat(
				{ query, followup: options?.followup, conversationId },
				{
					onStatus: (next) => {
						stage = next;
						setPendingStage(next);
					},
					onPreview: (preview) => {
						previewed = preview.length;
						stage = null;
						setPendingStage(null);
						setPendingPreview(preview);
					},
				},
			).then(async (next) => {
				// THE HAND-OFF WAITS FOR THE DECK TO STAND SQUARE. While the model was
				// writing its cards the pending exchange has been showing them as a cycling
				// stack, and this swap is the moment that stack becomes the real one and is
				// dealt from. Landing it mid-cycle pulls a card out of a deck that is
				// halfway round, which is exactly the snap this is here to prevent - so the
				// reply is held for the few hundred milliseconds until the deck is square,
				// and held there (lib/waitingDeck.ts). Nothing else waits on it: the prose
				// is already typed and on screen, and a turn that never showed a deck
				// resolves immediately.
				if (stage === CARDS_STAGE && !reduceMotion) {
					await waitingDeck.settleAndHold();
				}
				setPendingPreview('');
				setPendingStage(null);
				applyChatResponse(next, query, previewed || undefined);
			});
		};

		void (streamingReady ? streamed() : buffered())
			.catch((error: unknown) => {
				// FALLING BACK IS NOT UNCONDITIONAL. StreamUnavailable means the socket
				// failed BEFORE the server took the turn on - nothing written, nothing
				// billed - which is what a blocked WebSocket port on campus wifi looks like,
				// and asking the same question over HTTP is free of consequence. Anything
				// else is the server having said something definite, or having already
				// started work; retrying that would ask a question twice and bill it twice.
				if (!(error instanceof StreamUnavailable)) throw error;
				setPendingPreview('');
				setPendingStage(null);
				return buffered();
			})
			.catch(failWith)
			.finally(() => setIsLoading(false));
	};

	const handleSubmit = (query: string) => {
		if (isLoading || isTransitioning || openingChatId) return;

		// The sidebar's own label for an unsent chat, so the row stops saying "New chat"
		// while the first reply is in flight. The server titles the conversation from the
		// same message (its own first-message title, 80 characters), so this is the same
		// name arriving sooner rather than a second source of truth.
		setChats((current) =>
			current.map((chat) =>
				chat.id === activeChatId && chat.title === UNSENT_CHAT_TITLE
					? { ...chat, title: query.length > 36 ? `${query.slice(0, 36).trim()}…` : query }
					: chat,
			),
		);

		sendTurn(query);
	};

	const handleFollowup = (prompt: string) => {
		sendTurn(prompt, { followup: true });
	};

	/**
	 * Open a conversation from the sidebar.
	 *
	 * Its messages are FETCHED THE FIRST TIME, from the server, and only then is the
	 * transition started - the panel wipes onto real content instead of onto a blank one
	 * that fills in later. A conversation already opened in this tab is in `turns` and is
	 * shown straight away.
	 */
	const handleSelectChat = (id: string) => {
		if (openingChatId || isLoading) return;
		const chat = chats.find((item) => item.id === id);
		if (!chat) return;

		const currentIndex = chats.findIndex((item) => item.id === activeChatId);
		const nextIndex = chats.findIndex((item) => item.id === id);
		const direction: TransitionDirection = nextIndex > currentIndex ? 'left' : 'right';

		const conversationId = chat.conversationId;
		if (chat.turns || !conversationId) {
			void transitionToChat(chat, direction);
			return;
		}

		setHistoryError(null);
		setOpeningChatId(id);
		void fetchConversation(conversationId)
			.then((messages) => {
				const loaded: ChatSession = {
					...chat,
					turns: turnsFromStoredMessages(messages, conversationId),
				};
				setChats((current) => current.map((item) => (item.id === id ? loaded : item)));
				setOpeningChatId(null);
				return transitionToChat(loaded, direction);
			})
			.catch((error: unknown) => {
				setOpeningChatId(null);
				setHistoryError(
					error instanceof ChatApiError
						? t.chatOpenFailedWith(error.message)
						: t.chatOpenFailed,
				);
			});
	};

	const handleSignOut = () => {
		// Dropping the in-memory token is NOT the whole sign-out, which is why this
		// redirects instead of reloading. Cognito keeps its own session cookie on the
		// managed login domain, so a reload would bounce through /oauth2/authorize and
		// come back signed in as the same person without ever asking - on a shared campus
		// machine, handing the next student the previous one's account. signOut() goes to
		// /logout, which clears that cookie and returns the browser here.
		void signOut();
	};

	/**
	 * Rename one conversation. The sidebar's copy is updated only AFTER the server agrees,
	 * and to the title the server stored rather than the one that was typed: the server
	 * normalises it, so rendering the typed string would put a name on screen that a reload
	 * disagrees with.
	 */
	const handleRenameChat = async (id: string, title: string) => {
		const chat = chats.find((item) => item.id === id);
		if (!chat?.conversationId) return;

		const stored = await renameConversation(chat.conversationId, title);
		setChats((current) =>
			current.map((item) => (item.id === id ? { ...item, title: stored } : item)),
		);
	};

	/**
	 * Delete one conversation, server first.
	 *
	 * If the deleted chat was the one on screen there is nothing left to show, so this lands
	 * on a fresh welcome chat rather than an emptied one. The transition is skipped
	 * deliberately: the row the animation would slide away from no longer exists.
	 */
	const handleDeleteChat = async (id: string) => {
		const chat = chats.find((item) => item.id === id);
		if (!chat?.conversationId) return;

		await deleteConversation(chat.conversationId);

		const replacement = chat.id === activeChatId ? newChatSession() : null;
		setChats((current) => {
			const remaining = current.filter((item) => item.id !== id);
			return replacement ? [replacement, ...remaining] : remaining;
		});
		if (replacement) showChat(replacement);
	};

	const setNavCollapsedPersisted = useCallback((next: boolean) => {
		setNavCollapsed(next);
		try {
			window.localStorage.setItem(NAV_COLLAPSED_KEY, next ? '1' : '0');
		} catch {
			/* Private mode. The rail still moves; it just will not be remembered. */
		}
	}, []);

	const handleNewChat = () => {
		if (openingChatId) return;
		const chat = newChatSession();
		setChats((current) => [chat, ...current]);
		void transitionToChat(chat, 'right');
	};

	const layoutSpring = reduceMotion
		? { duration: 0 }
		: { type: 'spring' as const, stiffness: 360, damping: 34 };

	return (
		<div
			className={`chat-app${conversationStarted ? ' chat-app--active' : ' chat-app--landing'}${
				navCollapsed ? ' chat-app--nav-collapsed' : ''
			}`}
		>
			<motion.div
				className="chat-app__background"
				aria-hidden="true"
				initial={false}
				animate={{
					backgroundPositionX: chatTransition
						? [`${chatTransition.fromX}px`, `${chatTransition.toX}px`]
						: `${bgOffsetX}px`,
				}}
				transition={{
					duration: chatTransition ? CHAT_WIPE_MS / 1000 : 0,
					ease: [0.65, 0, 0.35, 1],
				}}
				onAnimationComplete={() => {
					if (chatTransition) {
						settleBackground(chatTransition.id, chatTransition.toX);
					}
				}}
			/>

			<SideNav
				chats={chats}
				activeChatId={activeChatId}
				open={navOpen}
				busy={isTransitioning}
				historyLoading={historyLoading}
				openingChatId={openingChatId}
				historyError={historyError}
				userEmail={userEmail}
				onLogout={handleSignOut}
				// Unconditional. The gear used to be handed this only when a cost model was
				// stamped, so a deployment with the cost panel off had no gear; settings now
				// holds the language picker, which every student has a reason to open.
				onOpenSettings={() => setShowSettings(true)}
				collapsed={navCollapsed}
				onExpand={() => setNavCollapsedPersisted(false)}
				onCollapse={() => setNavCollapsedPersisted(true)}
				onClose={() => setNavOpen(false)}
				onNewChat={handleNewChat}
				onSelectChat={handleSelectChat}
				onRenameChat={handleRenameChat}
				onDeleteChat={handleDeleteChat}
			/>

			{/* Mobile only (display:none above the breakpoint): the two header controls are
			    fixed over a scrolling thread, so the band behind them fades the paper out
			    rather than letting prose run under a floating button. */}
			<div className="chat-app__header-veil" aria-hidden="true" />

			<button
				type="button"
				className="chat-app__nav-toggle"
				onClick={() => setNavOpen(true)}
				aria-label={t.openChatHistory}
			>
				<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
					<path d="M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z" fill="currentColor" />
				</svg>
			</button>

			<main
				className="chat-app__content"
				aria-busy={isTransitioning || isLoading ? 'true' : undefined}
			>
				<LayoutGroup>
					<div className="chat-app__stage chat-app__stage--split">
						<motion.div
							className={`chat-app__panel${panelSwappedHidden ? ' chat-app__panel--swapped-hidden' : ''}`}
							initial={{ opacity: 0 }}
							animate={{ opacity: contentVisible ? 1 : 0 }}
							transition={{
								opacity: {
									duration: reduceMotion
										? 0
										: (contentVisible ? CHAT_FADE_IN_MS : CHAT_FADE_OUT_MS) / 1000,
									ease: contentVisible ? [0.16, 1, 0.3, 1] : 'easeIn',
								},
							}}
						>
							{hasContent || pendingPrompt ? (
								<ConversationFeed
									turns={shownTurns}
									pendingPrompt={pendingPrompt}
									pendingPreview={pendingPreview}
									pendingStage={pendingStage}
									continuedTurnId={continuedTurnId}
									introDelayMs={speechUsesIntro ? SPEECH_INTRO_DELAY_MS : 0}
									onTypingChange={setIsTalking}
									onPhaseChange={handlePhaseChange}
									onFollowup={handleFollowup}
									escalationEnabled={escalationEnabled}
								/>
							) : null}
						</motion.div>

						<motion.div
							className="chat-app__sammy-col"
							layout={!reduceMotion ? 'position' : false}
							transition={layoutSpring}
						>
							<div className="chat-app__sammy-stack">
								<SammyStage
									isTalking={isTalking && !isShocked}
									isShocked={isShocked}
									isThinking={isLoading}
								/>
							</div>
						</motion.div>
					</div>
				</LayoutGroup>
			</main>

			<div className="chat-app__dock">
				<div className="chat-app__dock-inner">
					<Composer onSubmit={handleSubmit} disabled={isLoading || isTransitioning} />
				</div>
			</div>

			{talkToPersonAvailable ? (
				<TalkToPersonPill onClick={() => setShowSjsuCaresModal(true)} />
			) : null}

			<SjsuCaresModal
				open={showSjsuCaresModal}
				onClose={() => setShowSjsuCaresModal(false)}
				highlightedServiceTheme={inferSjsuCaresServiceTheme(lastUserQuery)}
			/>

			{/* Always rendered, cost model or not - the language picker inside is the reason
			    the gear exists now. `costModel` being null is what removes the cost section
			    within it, and nothing else changes. */}
			<SettingsPanel
				open={showSettings}
				costModel={costModel}
				// The meter for the conversation on screen, not for the tab: opening a
				// different chat shows what that one has billed here.
				usage={activeChat?.usage}
				onClose={() => setShowSettings(false)}
			/>
		</div>
	);
}

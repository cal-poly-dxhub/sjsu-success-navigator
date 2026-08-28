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
import { MAX_CARDS } from './StatementStack';
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

/** How many cards this reply will actually put on screen, which is what the waiting deck
 * compresses to. */
function cardCountOf(response: ChatResponse): number {
	const batches = response.statementBatches ?? [];
	const last = batches[batches.length - 1];
	return Math.min(last?.cards.length ?? 0, MAX_CARDS);
}

const CHAT_FADE_OUT_MS = 160;
const CHAT_FADE_IN_MS = 640;
const CHAT_FADE_IN_HOLD_MS = Math.round(620 * 0.5);
const CHAT_WIPE_MS = 620;
const SPEECH_INTRO_DELAY_MS = 1100;
const BG_TILE_PX = 480;

/** Whether the desktop rail is collapsed, remembered across reloads. */
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

/** The opening greeting. */
function welcomeResponse(): ChatResponse {
	return {
		conversationalText: strings().welcome,
		talkToPersonAvailable: true,
		statementBatches: [],
	};
}

/** The unsent chat at the top of the sidebar. */
function newChatSession(): ChatSession {
	return {
		id: `new-${Date.now()}`,
		title: UNSENT_CHAT_TITLE,
		// Flagged so the feed can keep re-rendering it in the chosen language until this chat
		// is used, see `shownTurns` below, which is where that actually happens.
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
	/** The reply as it arrives over the stream, and what the server says it is doing while none
	 * has yet. */
	const [pendingPreview, setPendingPreview] = useState('');
	const [pendingStage, setPendingStage] = useState<string | null>(null);
	/** The turn that replaced a pending exchange, so the feed knows its bubble is not a new one. */
	const [continuedTurnId, setContinuedTurnId] = useState<string | null>(null);
	/** Whether this deployment has a mailbox to escalate to (config.json's escalationRecipient). */
	const [escalationEnabled, setEscalationEnabled] = useState(false);
	const [showSjsuCaresModal, setShowSjsuCaresModal] = useState(false);
	const [showSettings, setShowSettings] = useState(false);
	/** The cost model, or null when the stack did not stamp one. */
	const [costModel, setCostModel] = useState<CostModel | null>(null);

	useEffect(() => {
		let cancelled = false;
		void loadRuntimeConfig()
			.then((config) => {
				if (cancelled) return;
				setCostModel(config.costModel ?? null);
				// The absence of the key is the gate for the escalation path.
				setEscalationEnabled(Boolean(config.escalationRecipient));
			})
			.catch(() => {
				/* No cost panel. The chat surfaces its own config failures. */
			});
		return () => {
			cancelled = true;
		};
	}, []);
	// Straight off the last reply's `talkToPersonAvailable`, defaulting to shown.
	const [talkToPersonAvailable, setTalkToPersonAvailable] = useState(true);
	const [lastUserQuery, setLastUserQuery] = useState<string | null>(null);
	const [userEmail] = useState(() => currentUsername());
	const transitionId = useRef(0);

	const isTransitioning = Boolean(chatTransition);
	const hasContent = turns.length > 0;
	const activeChat = chats.find((chat) => chat.id === activeChatId);

	/** The conversation list, on load. */
	useEffect(() => {
		let cancelled = false;

		fetchConversations()
			.then((conversations) => {
				if (cancelled) return;
				setChats((current) => [
					// The unsent chats stay on top: a fetch cannot return them, and dropping
					// one takes the welcome screen out from under the student mid-read.
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

	/** Landing vs active chat, and the only thing the mobile layout switches on. */
	const conversationStarted = Boolean(pendingPrompt) || turns.some((turn) => Boolean(turn.query));

	/** The turns as they are shown, which differs from what is stored in exactly one case: an
	 * unused chat's greeting is rendered in the language chosen right now. */
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

	/** Keep the sidebar's copy of a conversation in step with the feed. */
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
				// The greeting is frozen here, in the language it was being read in, because
				// this is the instant the chat stops being new.
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
				// The draft the server assembled for this turn, carried straight onto it.
				escalation: next.escalation,
				// The location the server resolved for this turn, carried straight onto it.
				place: next.place,
				query,
				// What a streamed preview already typed out, so the finished turn picks up
				// where it stopped instead of replaying prose the student has read.
				revealedChars,
			});

			// The ID the server minted, kept so the next turn can say which conversation it
			// belongs to.
			setChats((current) =>
				current.map((chat) => {
					if (chat.id !== activeChatId) return chat;

					// The conversation's running meter, accrued from what the server counted on
					// each reply (app/usage.py).
					const metered = next.usage
						? { ...chat, usage: addTurnUsage(chat.usage, next.usage) }
						: chat;

					// The ID the server minted, kept so the next turn can say which
					// conversation it belongs to.
					if (!next.conversationId) return metered;
					return {
						...metered,
						conversationId: metered.conversationId ?? next.conversationId,
						// The server's name for this conversation, present only on the turn
						// that created it.
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

	/** One turn. */
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
			// Held in locals rather than read back out of state: the final payload arrives in
			// the same tick as the last delta, and a state read there would be stale.
			let previewed = 0;
			let stage: string | null = null;
			return streamChat(
				{ query, followup: options?.followup, conversationId },
				{
					// The first frame, and the ID on it.
					onAccepted: (id) => {
						setChats((current) =>
							current.map((chat) =>
								chat.id === activeChatId && !chat.conversationId
									? { ...chat, conversationId: id }
									: chat,
							),
						);
					},
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
				// The hand-off waits for the deck to stand square, and at the right count.
				if (stage === CARDS_STAGE && !reduceMotion) {
					await waitingDeck.settleAndCompress(cardCountOf(next));
				}
				setPendingPreview('');
				setPendingStage(null);
				applyChatResponse(next, query, previewed || undefined);
			});
		};

		void streamed()
			.catch((error: unknown) => {
				// Falling back is not unconditional, and it is narrow on purpose.
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

		// The sidebar's own label for an unsent chat, so the row stops saying "New chat" while
		// the first reply is in flight.
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

	/** Open a conversation from the sidebar. */
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
		// Dropping the in-memory token is not the whole sign-out, which is why this redirects
		// instead of reloading.
		void signOut();
	};

	/** Rename one conversation. */
	const handleRenameChat = async (id: string, title: string) => {
		const chat = chats.find((item) => item.id === id);
		if (!chat?.conversationId) return;

		const stored = await renameConversation(chat.conversationId, title);
		setChats((current) =>
			current.map((item) => (item.id === id ? { ...item, title: stored } : item)),
		);
	};

	/** Delete one conversation, server first. */
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
				// Unconditional, because settings holds the language picker as well as the cost
				// panel.
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

			{/* Mobile only: the two header controls are fixed over a scrolling thread, so the
			 * band behind them fades the paper out. */}
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

			{/* Always rendered: the language picker inside is why the gear exists, cost model or
			 * not. */}
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

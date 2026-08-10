import { useCallback, useRef, useState } from 'react';
import { LayoutGroup, motion } from 'motion/react';
import type { ChatResponse, ChatSession, ConversationTurn, RagPhase } from '../types/chat';
import { ChatApiError, incomingBatchFromResponse, postChat } from '../lib/chatApi';
import {
	appendConversationTurn,
	archiveActiveTurns,
	createConversationTurn,
	historyFromTurns,
	responseFromTurns,
	turnsFromResponse,
} from '../lib/conversationTurns';
import { Composer } from './Composer';
import { ConversationFeed } from './ConversationFeed';
import { SammyStage } from './SammyStage';
import { SideNav } from './SideNav';
import { SjsuCaresModal } from './SjsuCaresModal';
import { TalkToPersonPill } from './TalkToPersonPill';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { currentUsername, signOut } from '../lib/auth';
import { inferSjsuCaresServiceTheme } from '../lib/sjsuCares';
import './ChatApp.css';

const CHAT_FADE_OUT_MS = 160;
const CHAT_FADE_IN_MS = 640;
const CHAT_FADE_IN_HOLD_MS = Math.round(620 * 0.5);
const CHAT_WIPE_MS = 620;
const SPEECH_INTRO_DELAY_MS = 1100;
const BG_TILE_PX = 480;

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
 * The opening greeting. This is REAL UI copy, not fixture data - it was the one thing in
 * camp's chatFixtures.ts worth keeping, so it moved here when that file was deleted along
 * with the four fake conversations it also held.
 */
const WELCOME_RESPONSE: ChatResponse = {
	conversationalText:
		"Hi! I'm Sammy. Ask me anything about SJSU campus resources: tutoring, advising, wellness, housing help, and more.",
	talkToPersonAvailable: true,
	statementBatches: [],
};

/**
 * The sidebar starts with ONE real conversation. Camp shipped four canned ones
 * (MOCK_CHAT_SESSIONS) whose canned answers were indistinguishable from real ones - with
 * no persistence layer there is nothing true to put there, so selecting a "past chat"
 * loaded fabricated content.
 */
const INITIAL_CHATS: ChatSession[] = [
	{ id: 'new-chat', title: 'New chat', response: WELCOME_RESPONSE },
];

export default function ChatApp() {
	const reduceMotion = usePrefersReducedMotion();
	const [turns, setTurns] = useState<ConversationTurn[]>(() => turnsFromResponse(WELCOME_RESPONSE));
	const [isTalking, setIsTalking] = useState(false);
	const [chats, setChats] = useState<ChatSession[]>(INITIAL_CHATS);
	const [activeChatId, setActiveChatId] = useState(INITIAL_CHATS[0].id);
	const [navOpen, setNavOpen] = useState(false);
	const [contentVisible, setContentVisible] = useState(true);
	const [isShocked, setIsShocked] = useState(false);
	const [chatTransition, setChatTransition] = useState<ChatTransition | null>(null);
	const [bgOffsetX, setBgOffsetX] = useState(0);
	const [speechUsesIntro, setSpeechUsesIntro] = useState(true);
	const [panelSwappedHidden, setPanelSwappedHidden] = useState(false);
	const [isLoading, setIsLoading] = useState(false);
	const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
	const [showSjsuCaresModal, setShowSjsuCaresModal] = useState(false);
	const [lastUserQuery, setLastUserQuery] = useState<string | null>(null);
	const [userEmail] = useState(() => currentUsername());
	const transitionId = useRef(0);

	const isTransitioning = Boolean(chatTransition);
	const response = responseFromTurns(turns);
	const hasContent = turns.length > 0;

	/**
	 * Landing vs active chat, and the only thing the mobile layout switches on. A turn
	 * carries a `query` only once the student has actually asked something, so this reads
	 * the real conversation rather than a flag anyone has to remember to set - including
	 * after "New chat", which returns to the welcome turn and therefore to the landing.
	 */
	const conversationStarted = Boolean(pendingPrompt) || turns.some((turn) => Boolean(turn.query));

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

	const updateChat = useCallback(
		(id: string, nextTurns: ConversationTurn[]) => {
			setChats((current) =>
				current.map((chat) =>
					chat.id === id ? { ...chat, response: responseFromTurns(nextTurns) } : chat,
				),
			);
		},
		[],
	);

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
			const nextTurns = turnsFromResponse(chat.response);
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
				const archived = archiveActiveTurns(current);
				updateChat(activeChatId, archived);
				return archived;
			});
			setPendingPrompt(query);
			setSpeechUsesIntro(false);
		},
		[activeChatId, updateChat],
	);

	const applyChatResponse = useCallback(
		(next: ChatResponse, query: string) => {
			const incomingCards = incomingBatchFromResponse(next);
			const turn = createConversationTurn(next.conversationalText, {
				cards: incomingCards,
				trailingText: next.trailingText,
				safetyHandoff: next.safetyHandoff,
				query,
			});

			setPendingPrompt(null);
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

	const handleSubmit = (query: string) => {
		if (isLoading || isTransitioning) return;

		setLastUserQuery(query);
		setChats((current) =>
			current.map((chat) =>
				chat.id === activeChatId && chat.title === 'New chat'
					? { ...chat, title: query.length > 36 ? `${query.slice(0, 36).trim()}…` : query }
					: chat,
			),
		);

		const history = historyFromTurns(turns);
		beginPendingExchange(query);
		setIsLoading(true);
		void postChat({ query, sessionId: activeChatId, history })
			.then((next) => applyChatResponse(next, query))
			.catch((error: unknown) => {
				const message =
					error instanceof ChatApiError
						? error.message
						: 'Something went wrong reaching Sammy. Is the chat API running?';
				setPendingPrompt(null);
				const turn = createConversationTurn(message, { query });
				setTurns((current) => {
					const nextTurns = appendConversationTurn(current, turn);
					updateChat(activeChatId, nextTurns);
					return nextTurns;
				});
			})
			.finally(() => setIsLoading(false));
	};

	const handleFollowup = (prompt: string) => {
		if (isLoading || isTransitioning) return;

		setLastUserQuery(prompt);
		const history = historyFromTurns(turns);
		beginPendingExchange(prompt);
		setIsLoading(true);
		void postChat({ query: prompt, followup: true, sessionId: activeChatId, history })
			.then((next) => applyChatResponse(next, prompt))
			.catch((error: unknown) => {
				const message =
					error instanceof ChatApiError
						? error.message
						: 'Something went wrong reaching Sammy. Is the chat API running?';
				setPendingPrompt(null);
				const turn = createConversationTurn(message, { query: prompt });
				setTurns((current) => {
					const nextTurns = appendConversationTurn(current, turn);
					updateChat(activeChatId, nextTurns);
					return nextTurns;
				});
			})
			.finally(() => setIsLoading(false));
	};

	const handleSelectChat = (id: string) => {
		const chat = chats.find((item) => item.id === id);
		if (!chat) return;
		const currentIndex = chats.findIndex((item) => item.id === activeChatId);
		const nextIndex = chats.findIndex((item) => item.id === id);
		const direction: TransitionDirection = nextIndex > currentIndex ? 'left' : 'right';
		void transitionToChat(chat, direction);
	};

	const handleSignOut = () => {
		// The token lives in a module variable only, so dropping it IS the sign-out; a
		// reload then returns to the gate. Nothing to clear from storage - camp kept its
		// tokens in sessionStorage, this deliberately keeps none.
		signOut();
		window.location.reload();
	};

	const handleNewChat = () => {
		const chat: ChatSession = {
			id: `chat-${Date.now()}`,
			title: 'New chat',
			response: WELCOME_RESPONSE,
		};
		setChats((current) => [chat, ...current]);
		void transitionToChat(chat, 'right');
	};

	const layoutSpring = reduceMotion
		? { duration: 0 }
		: { type: 'spring' as const, stiffness: 360, damping: 34 };

	return (
		<div
			className={`chat-app${conversationStarted ? ' chat-app--active' : ' chat-app--landing'}`}
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
				userEmail={userEmail}
				onLogout={handleSignOut}
				onClose={() => setNavOpen(false)}
				onNewChat={handleNewChat}
				onSelectChat={handleSelectChat}
			/>

			{/* Mobile only (display:none above the breakpoint): the two header controls are
			    fixed over a scrolling thread, so the band behind them fades the paper out
			    rather than letting prose run under a floating button. */}
			<div className="chat-app__header-veil" aria-hidden="true" />

			<button
				type="button"
				className="chat-app__nav-toggle"
				onClick={() => setNavOpen(true)}
				aria-label="Open chat history"
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
									turns={turns}
									pendingPrompt={pendingPrompt}
									introDelayMs={speechUsesIntro ? SPEECH_INTRO_DELAY_MS : 0}
									onTypingChange={setIsTalking}
									onPhaseChange={handlePhaseChange}
									onFollowup={handleFollowup}
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

			{(response.talkToPersonAvailable ?? true) ? (
				<TalkToPersonPill onClick={() => setShowSjsuCaresModal(true)} />
			) : null}

			<SjsuCaresModal
				open={showSjsuCaresModal}
				onClose={() => setShowSjsuCaresModal(false)}
				highlightedServiceTheme={inferSjsuCaresServiceTheme(lastUserQuery)}
			/>
		</div>
	);
}

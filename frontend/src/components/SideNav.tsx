import { useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { UNSENT_CHAT_TITLE } from '../types/chat';
import type { ChatSession } from '../types/chat';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { useStrings } from '../lib/i18n';
import { PressableButton } from './PressableButton';
import './SideNav.css';

/** The longest name a student may give a conversation. */
const TITLE_MAX_CHARS = 80;

/** Sammy's face, cut out of the same artboard the chat stage animates (public/sammy.riv) and
 * shipped as a still. */
function SammyMark({ className }: { className: string }) {
	// Above the early return, or the second render of a failed image would call one hook fewer
	// than the first.
	const t = useStrings();
	const [failed, setFailed] = useState(false);
	if (failed) return null;
	return (
		<img
			className={className}
			src="/sammy-head.png"
			alt={t.sammyAlt}
			// The intrinsic size, so the box is reserved from the first frame and the title
			// does not jump sideways when the picture arrives.
			width={234}
			height={256}
			decoding="async"
			onError={() => setFailed(true)}
		/>
	);
}

/** The rail control, and the collapse control. */
function PanelIcon() {
	return (
		<svg
			viewBox="0 0 24 24"
			width="18"
			height="18"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.9"
			strokeLinecap="round"
			strokeLinejoin="round"
			aria-hidden="true"
		>
			<rect x="3" y="4" width="18" height="16" rx="3" />
			<path d="M9.5 4v16" />
			{/* Inset to the inner edge of the stroke with its own corner radius, so it hugs the
			 * frame. */}
			<path
				d="M4 7.1A2.1 2.1 0 0 1 6.1 5h2.5v14H6.1A2.1 2.1 0 0 1 4 16.9z"
				fill="currentColor"
				stroke="none"
			/>
		</svg>
	);
}

type SideNavProps = {
	chats: ChatSession[];
	activeChatId: string;
	open: boolean;
	busy?: boolean;
	/** True while the conversation list itself is being fetched (page load). */
	historyLoading?: boolean;
	/** The chat whose stored messages are being fetched, if any. */
	openingChatId?: string | null;
	/** A failed list or open, said plainly rather than as an empty sidebar. */
	historyError?: string | null;
	userEmail?: string;
	onLogout?: () => void;
	/** Opens settings. */
	onOpenSettings: () => void;
	/** Desktop only. */
	collapsed?: boolean;
	/** Idempotent on purpose. */
	onExpand?: () => void;
	/** Absent on the mobile drawer, which is what keeps the collapse control off it. */
	onCollapse?: () => void;
	onClose: () => void;
	onNewChat: () => void;
	onSelectChat: (id: string) => void;
	/** Rename and delete, both resolving only once the server has agreed. */
	onRenameChat: (id: string, title: string) => Promise<void>;
	onDeleteChat: (id: string) => Promise<void>;
};

function NavContent({
	chats,
	activeChatId,
	busy = false,
	historyLoading = false,
	openingChatId = null,
	historyError = null,
	userEmail,
	onLogout,
	onOpenSettings,
	collapsed = false,
	onExpand,
	onCollapse,
	onNewChat,
	onSelectChat,
	onRenameChat,
	onDeleteChat,
}: Omit<SideNavProps, 'open' | 'onClose'>) {
	const t = useStrings();
	// Which row is mid-rename, mid-delete-confirm, or waiting on the server.
	const [editingId, setEditingId] = useState<string | null>(null);
	const [draft, setDraft] = useState('');
	const [confirmingId, setConfirmingId] = useState<string | null>(null);
	const [pendingId, setPendingId] = useState<string | null>(null);
	const [rowError, setRowError] = useState<string | null>(null);

	const closeRowUi = () => {
		setEditingId(null);
		setConfirmingId(null);
		setDraft('');
	};

	const startRename = (chat: ChatSession) => {
		setRowError(null);
		setConfirmingId(null);
		setEditingId(chat.id);
		setDraft(chat.title);
	};

	const commitRename = (chat: ChatSession) => {
		const title = draft.trim();
		// An unchanged or emptied name is not a rename.
		if (!title || title === chat.title) {
			closeRowUi();
			return;
		}
		setPendingId(chat.id);
		void onRenameChat(chat.id, title)
			.then(closeRowUi)
			.catch((error: unknown) => {
				setRowError(error instanceof Error ? error.message : t.renameFailed);
			})
			.finally(() => setPendingId(null));
	};

	const confirmDelete = (chat: ChatSession) => {
		setPendingId(chat.id);
		void onDeleteChat(chat.id)
			.then(closeRowUi)
			.catch((error: unknown) => {
				setRowError(error instanceof Error ? error.message : t.deleteFailed);
			})
			.finally(() => setPendingId(null));
	};

	// A chat with no conversation id was started in this tab and never sent, so it is not
	// history, and "no past chats" beneath it would be wrong the moment the student sends.
	const stored = chats.filter((chat) => chat.conversationId);

	/** The collapsed rail: his face at the top, the signed-in student at the bottom, nothing
	 * else. */
	if (collapsed) {
		return (
			<div className="side-nav__rail">
				<button
					type="button"
					className="side-nav__rail-brand"
					onClick={onExpand}
					aria-label={t.expandSidebar}
					aria-expanded={false}
					title={t.expandSidebar}
				>
					{/* Both live in one grid cell and cross-fade, so the mark is the button rather than
					    a button appearing beside it. */}
					<SammyMark className="side-nav__mark side-nav__mark--rail" />
					<span className="side-nav__rail-icon">
						<PanelIcon />
					</span>
				</button>

				{userEmail ? (
					// Not a button: the rail's own click expands, so a second control adds a
					// tab stop.
					<span className="side-nav__rail-avatar" title={userEmail} aria-hidden="true">
						{userEmail.trim().charAt(0).toUpperCase()}
					</span>
				) : null}
			</div>
		);
	}

	return (
		<>
			<div className="side-nav__header">
				<div className="side-nav__brand">
					{/* Sammy, then the name.  */}
					<SammyMark className="side-nav__mark" />
					<span className="side-nav__brand-copy">
						<strong>{t.brandName}</strong>
					</span>
				</div>

				{onCollapse ? (
					<button
						type="button"
						className="side-nav__collapse"
						onClick={onCollapse}
						aria-label={t.collapseSidebar}
						aria-expanded
						title={t.collapseSidebar}
					>
						<PanelIcon />
					</button>
				) : null}
			</div>

			<button type="button" className="side-nav__new-chat" onClick={onNewChat} disabled={busy}>
				<svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true">
					<path d="M11 5h2v14h-2zM5 11h14v2H5z" fill="currentColor" />
				</svg>
				<span>{t.newChat}</span>
			</button>

			<nav className="side-nav__history" aria-label={t.chatHistory}>
				<p className="side-nav__eyebrow">{t.recentChats}</p>
				<ul className="side-nav__list">
					{chats.map((chat) => {
						const active = chat.id === activeChatId;
						const opening = chat.id === openingChatId;
						const pending = chat.id === pendingId;
						// A chat with no conversation id has never been sent, so there is
						// nothing on the server to rename or delete.
						const stored = Boolean(chat.conversationId);

						if (chat.id === editingId) {
							return (
								<li key={chat.id} className="side-nav__row">
									<form
										className="side-nav__rename"
										onSubmit={(event) => {
											event.preventDefault();
											commitRename(chat);
										}}
									>
										<input
											className="side-nav__rename-input"
											value={draft}
											maxLength={TITLE_MAX_CHARS}
											autoFocus
											aria-label={t.renameChat(chat.title)}
											disabled={pending}
											onChange={(event) => setDraft(event.target.value)}
											onKeyDown={(event) => {
												// Escape abandons the edit: backing out
												// should not cost a click.
												if (event.key === 'Escape') closeRowUi();
											}}
										/>
										<button type="submit" className="side-nav__row-action" disabled={pending}>
											{pending ? t.saving : t.save}
										</button>
										<button
											type="button"
											className="side-nav__row-action"
											disabled={pending}
											onClick={closeRowUi}
										>
											{t.cancel}
										</button>
									</form>
								</li>
							);
						}

						if (chat.id === confirmingId) {
							return (
								<li key={chat.id} className="side-nav__row">
									<div className="side-nav__confirm" role="group" aria-label={t.deleteChat(chat.title)}>
										{/* Named, and named as permanent.  */}
										<p className="side-nav__confirm-copy">{t.deleteConfirm(chat.title)}</p>
										<div className="side-nav__confirm-actions">
											<button
												type="button"
												className="side-nav__row-action side-nav__row-action--danger"
												disabled={pending}
												onClick={() => confirmDelete(chat)}
											>
												{pending ? t.deleting : t.delete}
											</button>
											<button
												type="button"
												className="side-nav__row-action"
												disabled={pending}
												onClick={closeRowUi}
											>
												{t.cancel}
											</button>
										</div>
									</div>
								</li>
							);
						}

						return (
							<li key={chat.id} className="side-nav__row">
								<button
									type="button"
									className={`side-nav__chat${active ? ' side-nav__chat--active' : ''}`}
									onClick={() => onSelectChat(chat.id)}
									disabled={busy || openingChatId !== null || pendingId !== null}
									aria-current={active ? 'page' : undefined}
									aria-busy={opening ? 'true' : undefined}
								>
									{/* The placeholder is chrome, not a stored name, so it reads
									 * in the student's language. */}
									<span>{chat.title === UNSENT_CHAT_TITLE ? t.newChat : chat.title}</span>
									{opening ? <span className="side-nav__chat-status">{t.opening}</span> : null}
								</button>

								{stored && !opening ? (
									// Revealed on hover and on focus-within, so both controls
									// are reachable by keyboard rather than only by mouse.
									<div className="side-nav__row-actions">
										<button
											type="button"
											className="side-nav__row-icon"
											aria-label={t.renameChat(chat.title)}
											disabled={busy || pendingId !== null}
											onClick={() => startRename(chat)}
										>
											<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
												<path
													d="M4 20h4L18 10l-4-4L4 16v4zm13.7-13.3 1.6-1.6a1.4 1.4 0 0 0 0-2l-2-2a1.4 1.4 0 0 0-2 0l-1.6 1.6 4 4z"
													fill="currentColor"
												/>
											</svg>
										</button>
										<button
											type="button"
											className="side-nav__row-icon side-nav__row-icon--danger"
											aria-label={t.deleteChat(chat.title)}
											disabled={busy || pendingId !== null}
											onClick={() => {
												setRowError(null);
												setEditingId(null);
												setConfirmingId(chat.id);
											}}
										>
											<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
												<path
													d="M7 21h10a1 1 0 0 0 1-1V7H6v13a1 1 0 0 0 1 1zM9 4V3h6v1h5v2H4V4h5z"
													fill="currentColor"
												/>
											</svg>
										</button>
									</div>
								) : null}
							</li>
						);
					})}
				</ul>

				{historyLoading ? (
					<p className="side-nav__history-note">{t.loadingChats}</p>
				) : null}

				{!historyLoading && !historyError && stored.length === 0 ? (
					<p className="side-nav__history-note">{t.noStoredChats}</p>
				) : null}

				{historyError ? (
					<p className="side-nav__history-note side-nav__history-note--error" role="status">
						{historyError}
					</p>
				) : null}

				{/* A failed rename or delete, said where it happened.  */}
				{rowError ? (
					<p className="side-nav__history-note side-nav__history-note--error" role="status">
						{rowError}
					</p>
				) : null}
			</nav>

			{userEmail ? (
				<div className="side-nav__account">
					<p className="side-nav__account-label">{t.signedIn}</p>
					<p className="side-nav__account-email" title={userEmail}>
						{userEmail}
					</p>
					{onLogout ? (
						<div className="side-nav__account-actions">
							<PressableButton
								variant="ghost"
								className="side-nav__logout"
								onClick={onLogout}
								disabled={busy}
							>
								{t.signOut}
							</PressableButton>
							{/* A gear, and now it means what a gear means.  */}
							<button
								type="button"
								className="side-nav__settings"
								onClick={onOpenSettings}
								aria-label={t.settings}
								title={t.settings}
							>
								<svg
									viewBox="0 0 24 24"
									width="17"
									height="17"
									fill="none"
									stroke="currentColor"
									strokeWidth="1.7"
									strokeLinecap="round"
									strokeLinejoin="round"
									aria-hidden="true"
								>
									<circle cx="12" cy="12" r="3" />
									<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1Z" />
								</svg>
							</button>
						</div>
					) : null}
				</div>
			) : null}

		</>
	);
}

/** Past this far left, or thrown this fast, the drag is a dismissal rather than a nudge. */
const SWIPE_CLOSE_PX = 56;
const SWIPE_CLOSE_VELOCITY = 380;

/** The standard modal drawer: a scrim over the remaining sliver of chat, dismissed by tapping it
 * or by dragging left. */
export function SideNav(props: SideNavProps) {
	const t = useStrings();
	const reduceMotion = usePrefersReducedMotion();
	const slide = reduceMotion
		? { duration: 0 }
		: { type: 'spring' as const, stiffness: 420, damping: 38 };

	return (
		<>
			{/* THE SECOND WAY TO EXPAND.  */}
			<aside
				className={`side-nav side-nav--desktop${props.collapsed ? ' side-nav--collapsed' : ''}`}
				onClick={props.collapsed ? props.onExpand : undefined}
			>
				<NavContent {...props} />
			</aside>

			<AnimatePresence>
				{props.open ? (
					<>
						<motion.button
							type="button"
							className="side-nav__backdrop"
							aria-label={t.closeNavigation}
							onClick={props.onClose}
							initial={{ opacity: 0 }}
							animate={{ opacity: 1 }}
							exit={{ opacity: 0 }}
							transition={{ duration: reduceMotion ? 0 : 0.2 }}
						/>
						<motion.aside
							className="side-nav side-nav--mobile"
							initial={reduceMotion ? { opacity: 0 } : { x: '-100%' }}
							animate={reduceMotion ? { opacity: 1 } : { x: 0 }}
							exit={reduceMotion ? { opacity: 0 } : { x: '-100%' }}
							transition={slide}
							drag="x"
							dragDirectionLock
							dragMomentum={false}
							// Left of the open position is free travel; right of it is pinned,
							// so the drawer cannot be dragged wider than it is.
							dragConstraints={{ left: 0, right: 0 }}
							dragElastic={{ left: 1, right: 0 }}
							onDragEnd={(_event, info) => {
								if (info.offset.x < -SWIPE_CLOSE_PX || info.velocity.x < -SWIPE_CLOSE_VELOCITY) {
									props.onClose();
								}
							}}
						>
							{/* Never collapsed, and no collapse control: this one is already a
							 * panel you open and dismiss, and the rail is a desktop idea. */}
							<NavContent {...props} collapsed={false} onCollapse={undefined} />
						</motion.aside>
					</>
				) : null}
			</AnimatePresence>
		</>
	);
}

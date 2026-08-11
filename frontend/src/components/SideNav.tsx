import { useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import type { ChatSession } from '../types/chat';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { PressableButton } from './PressableButton';
import './SideNav.css';

/**
 * The longest name a student may give a conversation. The SERVER's cap
 * (chat.title_max_chars) is the real one and rejects anything longer with a 400; this is the
 * same number spelled where the input can stop them reaching it, so the ordinary way to hit
 * the limit is a field that stops accepting characters rather than an error after the fact.
 */
const TITLE_MAX_CHARS = 80;

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
	/**
	 * Opens the cost panel. Absent unless the stack stamped a cost model into config.json,
	 * and its absence is what hides the control entirely - see lib/runtimeConfig.ts.
	 */
	onOpenCost?: () => void;
	onClose: () => void;
	onNewChat: () => void;
	onSelectChat: (id: string) => void;
	/**
	 * Rename, resolving only once the SERVER has agreed. It is async because the row stays
	 * disabled until then: a title that appeared instantly and then reverted would be the
	 * sidebar lying about what is stored, which is the one thing this component is not
	 * allowed to do.
	 */
	onRenameChat: (id: string, title: string) => Promise<void>;
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
	onOpenCost,
	onNewChat,
	onSelectChat,
	onRenameChat,
}: Omit<SideNavProps, 'open' | 'onClose'>) {
	// Which row is mid-rename, mid-delete-confirm, or waiting on the server. Local to the
	// sidebar because none of it is data: it is what this panel is currently showing, and it
	// is thrown away the moment the server answers.
	const [editingId, setEditingId] = useState<string | null>(null);
	const [draft, setDraft] = useState('');
	const [pendingId, setPendingId] = useState<string | null>(null);
	const [rowError, setRowError] = useState<string | null>(null);

	const closeRowUi = () => {
		setEditingId(null);
		setDraft('');
	};

	const startRename = (chat: ChatSession) => {
		setRowError(null);
		setEditingId(chat.id);
		setDraft(chat.title);
	};

	const commitRename = (chat: ChatSession) => {
		const title = draft.trim();
		// An unchanged or emptied name is not a rename. Closing without a request is the
		// honest outcome: nothing was asked for, so nothing is claimed.
		if (!title || title === chat.title) {
			closeRowUi();
			return;
		}
		setPendingId(chat.id);
		void onRenameChat(chat.id, title)
			.then(closeRowUi)
			.catch((error: unknown) => {
				setRowError(error instanceof Error ? error.message : 'Could not rename that chat.');
			})
			.finally(() => setPendingId(null));
	};


	// A chat with no conversation id is one started in this tab that has not been sent yet,
	// so it is not "history" - it is the only thing in the list on a first visit, and saying
	// "no past chats" beneath it would be wrong the moment the student presses send.
	const stored = chats.filter((chat) => chat.conversationId);

	return (
		<>
			<div className="side-nav__header">
				<div className="side-nav__brand" aria-label="Student Success Navigator">
					{/* The name alone. A rounded blue tile holding an "S" used to sit here; it stood
					    for nothing and read as a placeholder logo, and the product name does the job
					    the tile was pretending to do. The aria-label stays because the two lines are
					    separate elements and it fixes them into a single reading. */}
					<span className="side-nav__brand-copy">
						<strong>Student Success</strong>
						<span>Navigator</span>
					</span>
				</div>
			</div>

			<button type="button" className="side-nav__new-chat" onClick={onNewChat} disabled={busy}>
				<svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true">
					<path d="M11 5h2v14h-2zM5 11h14v2H5z" fill="currentColor" />
				</svg>
				<span>New chat</span>
			</button>

			<nav className="side-nav__history" aria-label="Chat history">
				<p className="side-nav__eyebrow">Recent chats</p>
				<ul className="side-nav__list">
					{chats.map((chat) => {
						const active = chat.id === activeChatId;
						const opening = chat.id === openingChatId;
						const pending = chat.id === pendingId;
						// A chat with no conversation id has never been sent, so there is nothing
						// on the server to rename or delete. That row is the welcome screen, and it
						// stops being one the moment the student asks something.
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
											aria-label={`Rename ${chat.title}`}
											disabled={pending}
											onChange={(event) => setDraft(event.target.value)}
											onKeyDown={(event) => {
												// Escape abandons the edit. Backing out of a rename is a
												// small, reversible thing and should not cost a click.
												if (event.key === 'Escape') closeRowUi();
											}}
										/>
										<button type="submit" className="side-nav__row-action" disabled={pending}>
											{pending ? 'Saving…' : 'Save'}
										</button>
										<button
											type="button"
											className="side-nav__row-action"
											disabled={pending}
											onClick={closeRowUi}
										>
											Cancel
										</button>
									</form>
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
									<span>{chat.title}</span>
									{opening ? <span className="side-nav__chat-status">Opening…</span> : null}
								</button>

								{stored && !opening ? (
									// Revealed on hover and on FOCUS-WITHIN, so the control is reachable
									// by keyboard rather than only by mouse. It sits over the end of the
									// title rather than beside it: a column reserved for it would narrow
									// every row for the sake of a button most rows never show.
									<div className="side-nav__row-actions">
										<button
											type="button"
											className="side-nav__row-icon"
											aria-label={`Rename ${chat.title}`}
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
									</div>
								) : null}
							</li>
						);
					})}
				</ul>

				{historyLoading ? (
					<p className="side-nav__history-note">Loading your chats…</p>
				) : null}

				{!historyLoading && !historyError && stored.length === 0 ? (
					<p className="side-nav__history-note">
						Chats you send are saved here, and stay on your account.
					</p>
				) : null}

				{historyError ? (
					<p className="side-nav__history-note side-nav__history-note--error" role="status">
						{historyError}
					</p>
				) : null}

				{/* A failed rename, said where it happened. The row is left as it was rather
				    than optimistically changed and reverted, so this note is the only thing
				    that changes: what is on screen still matches what is stored. */}
				{rowError ? (
					<p className="side-nav__history-note side-nav__history-note--error" role="status">
						{rowError}
					</p>
				) : null}
			</nav>

			{userEmail ? (
				<div className="side-nav__account">
					<p className="side-nav__account-label">Signed in</p>
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
								Sign out
							</PressableButton>
							{/*
							  A gear, not a currency symbol. The panel behind it is a demo instrument
							  for sponsors, and a dollar sign in a student's sidebar advertises that
							  this app has a price - the opposite of what this surface should say to
							  the student it is for. Discreet at a glance; the label names it for
							  anyone who looks, hovers, or is listening to a screen reader.
							*/}
							{onOpenCost ? (
								<button
									type="button"
									className="side-nav__cost"
									onClick={onOpenCost}
									aria-label="Cost analysis"
									title="Cost analysis"
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
							) : null}
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

/**
 * The mobile surface is the standard modal navigation drawer: it covers four fifths of the
 * viewport, the remaining sliver of chat stays visible under a scrim, and it is dismissed
 * by tapping that sliver or by dragging left. There is no close button - the drawer's own
 * mock does not have one, and the scrim is the affordance.
 *
 * Reduced motion is read from the OS query only (there is no in-app motion setting): the
 * slide and the scrim fade both collapse to zero duration, so the drawer is simply there
 * on the first frame. The DRAG is left alone under that preference on purpose - it is the
 * student's own finger moving the panel, which is direct manipulation rather than motion
 * the interface decided to play at them.
 */
export function SideNav(props: SideNavProps) {
	const reduceMotion = usePrefersReducedMotion();
	const slide = reduceMotion
		? { duration: 0 }
		: { type: 'spring' as const, stiffness: 420, damping: 38 };

	return (
		<>
			<aside className="side-nav side-nav--desktop">
				<NavContent {...props} />
			</aside>

			<AnimatePresence>
				{props.open ? (
					<>
						<motion.button
							type="button"
							className="side-nav__backdrop"
							aria-label="Close navigation"
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
							// Left of the open position is free travel; right of it is pinned, so
							// the drawer cannot be dragged wider than it is.
							dragConstraints={{ left: 0, right: 0 }}
							dragElastic={{ left: 1, right: 0 }}
							onDragEnd={(_event, info) => {
								if (info.offset.x < -SWIPE_CLOSE_PX || info.velocity.x < -SWIPE_CLOSE_VELOCITY) {
									props.onClose();
								}
							}}
						>
							<NavContent {...props} />
						</motion.aside>
					</>
				) : null}
			</AnimatePresence>
		</>
	);
}

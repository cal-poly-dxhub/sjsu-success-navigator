import { useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { UNSENT_CHAT_TITLE } from '../types/chat';
import type { ChatSession } from '../types/chat';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { useStrings } from '../lib/i18n';
import { PressableButton } from './PressableButton';
import './SideNav.css';

/**
 * The longest name a student may give a conversation. The SERVER's cap
 * (chat.title_max_chars) is the real one and rejects anything longer with a 400; this is the
 * same number spelled where the input can stop them reaching it, so the ordinary way to hit
 * the limit is a field that stops accepting characters rather than an error after the fact.
 */
const TITLE_MAX_CHARS = 80;

/**
 * Sammy's face, cut out of the SAME artboard the chat stage animates (public/sammy.riv)
 * and shipped as a still. The .riv is a 960 KB vector animation that needs the Rive
 * runtime and a canvas to show anything; the header wants a 24 KB picture, so the crop
 * was taken once, offline, from a rendered frame - the pixels are his own line art rather
 * than a redrawing of it, and the header costs no Rive at runtime.
 *
 * A FAILED LOAD REMOVES HIM FROM THE LAYOUT. Left alone, a broken <img> is a torn-page
 * glyph and the alt text sitting where the face should be, which is worse than never
 * having put a picture there. Rendering nothing puts the header back to exactly the text
 * it used to be.
 */
function SammyMark({ className }: { className: string }) {
	// Above the early return, or the second render of a failed image would call one hook
	// fewer than the first.
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

/**
 * The rail control, and the collapse control. A panel whose NARROW PANE IS FILLED, drawn
 * once and mirrored between the two states (see .side-nav__collapse in the stylesheet):
 * the solid bar sits on the side the sidebar is about to be, so the glyph says which way
 * the click goes rather than being the same picture twice.
 */
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
			{/* Inset to the INNER edge of the 1.9 stroke (x 4, y 5 to 19) with its own 2.1
			    corner radius rather than the outer 3, so it hugs the frame instead of
			    leaving a sliver of paper in the curve. It runs to the divider's centre
			    line, which paints over the join. */}
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
	/**
	 * Opens settings. NOT OPTIONAL, and that is the change: the gear used to be handed an
	 * `onOpenCost` that was undefined unless the stack stamped a cost model into config.json,
	 * so a deployment with the cost panel off had no gear at all. Settings now holds the
	 * language picker, which is for the student rather than for a sponsor, so it is always
	 * there; what the cost model's absence hides is the section inside it (SettingsPanel).
	 */
	onOpenSettings: () => void;
	/**
	 * DESKTOP ONLY. The rail is collapsed to its icon width; the mobile drawer is always
	 * given `false`, because it is already a thing you open and dismiss and a drawer that
	 * opened to a 3.5rem strip would be a drawer that opened to nothing.
	 */
	collapsed?: boolean;
	/**
	 * Idempotent on purpose. Both ways of expanding - the brand button and a click anywhere
	 * on the rail - call this, and the button's click bubbles to the rail, so "expand" has
	 * to survive being asked for twice in one gesture. A toggle would collapse straight
	 * back again.
	 */
	onExpand?: () => void;
	/** Absent on the mobile drawer, which is what keeps the collapse control off it. */
	onCollapse?: () => void;
	onClose: () => void;
	onNewChat: () => void;
	onSelectChat: (id: string) => void;
	/**
	 * Rename and delete, both resolving only once the SERVER has agreed. They are async
	 * because the row stays disabled until then: a title that appeared instantly and then
	 * reverted, or a row that vanished and came back, would be the sidebar lying about what
	 * is stored - which is the one thing this component is not allowed to do.
	 */
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
	// Which row is mid-rename, mid-delete-confirm, or waiting on the server. Local to the
	// sidebar because none of it is data: it is what this panel is currently showing, and it
	// is thrown away the moment the server answers.
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

	// A chat with no conversation id is one started in this tab that has not been sent yet,
	// so it is not "history" - it is the only thing in the list on a first visit, and saying
	// "no past chats" beneath it would be wrong the moment the student presses send.
	const stored = chats.filter((chat) => chat.conversationId);

	/**
	 * The collapsed rail: his face at the top, the signed-in student at the bottom, nothing
	 * else. This is a SEPARATE TREE rather than the full sidebar with most of it hidden -
	 * a rail built by display:none would still be laying out chat titles inside a 3.5rem
	 * strip, and every one of them would flash as an ellipsis on the way open.
	 */
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
					{/* Both live in one grid cell and cross-fade: hovering ANYWHERE on the rail
					    turns his face into the control (see SideNav.css), which is the whole
					    trick - the mark is the button, rather than a button appearing next to a
					    mark and making the rail feel crowded. */}
					<SammyMark className="side-nav__mark side-nav__mark--rail" />
					<span className="side-nav__rail-icon">
						<PanelIcon />
					</span>
				</button>

				{userEmail ? (
					// Not a button. The rail's own click expands, so a second control here would
					// be a second thing to tab to that does what the first one does; the initial
					// is identity, and the title says whose.
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
					{/* Sammy, then the name. A rounded blue tile holding an "S" used to sit here; it
					    stood for nothing and read as a placeholder logo, and his actual face is the
					    thing the tile was pretending to be. The wrapper's aria-label is gone with
					    the second line it existed to join: the image carries its own alt and the
					    name is one string, so there is nothing left to fix into a single reading. */}
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
											aria-label={t.renameChat(chat.title)}
											disabled={pending}
											onChange={(event) => setDraft(event.target.value)}
											onKeyDown={(event) => {
												// Escape abandons the edit. Backing out of a rename is a
												// small, reversible thing and should not cost a click.
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
										{/* Named, and named as permanent. The server hard deletes the
										    conversation and every message under it, so this sentence is
										    the last point at which that is still a choice. */}
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
									{/* The unsent chat's placeholder is chrome, not a name the server
									    gave anything, so it reads in the student's language. Every
									    other title here was typed by a student or written by the
									    server and is left exactly as it is stored. */}
									<span>{chat.title === UNSENT_CHAT_TITLE ? t.newChat : chat.title}</span>
									{opening ? <span className="side-nav__chat-status">{t.opening}</span> : null}
								</button>

								{stored && !opening ? (
									// Revealed on hover and on FOCUS-WITHIN, so both controls are reachable
									// by keyboard rather than only by mouse. They sit over the end of the
									// title rather than beside it: a column reserved for them would narrow
									// every row for the sake of two buttons most rows never show.
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

				{/* A failed rename or delete, said where it happened. The row is left as it
				    was rather than optimistically changed and reverted, so this note is the
				    only thing that changes: what is on screen still matches what is stored. */}
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
							{/*
							  A gear, and now it means what a gear means. It was drawn as one already
							  - a dollar sign in a student's sidebar advertises that this app has a
							  price, which is the opposite of what this surface should say to the
							  student it is for - and behind it there is a settings panel rather than
							  a single sponsor instrument. It is UNCONDITIONAL: the language picker
							  inside belongs to the student, so a deployment with the cost model
							  switched off still has settings, minus that section.
							*/}
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
	const t = useStrings();
	const reduceMotion = usePrefersReducedMotion();
	const slide = reduceMotion
		? { duration: 0 }
		: { type: 'spring' as const, stiffness: 420, damping: 38 };

	return (
		<>
			{/*
			  THE SECOND WAY TO EXPAND. The whole collapsed strip is a click target, which is
			  the behaviour a thin rail invites - you aim at the bar, not at the 28px picture
			  on it. It is deliberately mouse-only affordance layered over a real control: the
			  brand button inside is what a keyboard reaches, and it does the same thing, so
			  nothing here is reachable only by pointer. onExpand is idempotent, which is why
			  the button's click bubbling into this handler is harmless.
			*/}
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
							{/* Never collapsed, and no collapse control: this one is already a panel
							    you open and dismiss, and the rail is a desktop idea. */}
							<NavContent {...props} collapsed={false} onCollapse={undefined} />
						</motion.aside>
					</>
				) : null}
			</AnimatePresence>
		</>
	);
}

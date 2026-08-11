import { AnimatePresence, motion } from 'motion/react';
import type { ChatSession } from '../types/chat';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { PressableButton } from './PressableButton';
import './SideNav.css';
type SideNavProps = {
	chats: ChatSession[];
	activeChatId: string;
	open: boolean;
	busy?: boolean;
	userEmail?: string;
	onLogout?: () => void;
	onClose: () => void;
	onNewChat: () => void;
	onSelectChat: (id: string) => void;
};

function NavContent({
	chats,
	activeChatId,
	busy = false,
	userEmail,
	onLogout,
	onNewChat,
	onSelectChat,
}: Omit<SideNavProps, 'open' | 'onClose'>) {	return (
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
						return (
							<li key={chat.id}>
								<button
									type="button"
									className={`side-nav__chat${active ? ' side-nav__chat--active' : ''}`}
									onClick={() => onSelectChat(chat.id)}
									disabled={busy}
									aria-current={active ? 'page' : undefined}
								>
									<span>{chat.title}</span>
								</button>
							</li>
						);
					})}
				</ul>
			</nav>

			{userEmail ? (
				<div className="side-nav__account">
					<p className="side-nav__account-label">Signed in</p>
					<p className="side-nav__account-email" title={userEmail}>
						{userEmail}
					</p>
					{onLogout ? (
						<PressableButton
							variant="ghost"
							className="side-nav__logout"
							onClick={onLogout}
							disabled={busy}
						>
							Sign out
						</PressableButton>
					) : null}
				</div>
			) : null}

			<p className="side-nav__mock-note">Chat history is mocked for this preview.</p>		</>
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

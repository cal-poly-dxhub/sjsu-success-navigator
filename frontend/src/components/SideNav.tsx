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
	onClose,
	onNewChat,
	onSelectChat,
}: Omit<SideNavProps, 'open'>) {	return (
		<>
			<div className="side-nav__header">
				<div className="side-nav__brand" aria-label="Student Success Navigator">
					<span className="side-nav__brand-mark" aria-hidden="true">S</span>
					<span className="side-nav__brand-copy">
						<strong>Student Success</strong>
						<span>Navigator</span>
					</span>
				</div>
				<button type="button" className="side-nav__close" onClick={onClose} aria-label="Close navigation">
					<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
						<path d="m6.7 5.3 12 12-1.4 1.4-12-12 1.4-1.4Z" fill="currentColor" />
						<path d="m17.3 5.3 1.4 1.4-12 12-1.4-1.4 12-12Z" fill="currentColor" />
					</svg>
				</button>
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

export function SideNav(props: SideNavProps) {
	const reduceMotion = usePrefersReducedMotion();

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
							transition={{ type: 'spring', stiffness: 420, damping: 38 }}
						>
							<NavContent {...props} />
						</motion.aside>
					</>
				) : null}
			</AnimatePresence>
		</>
	);
}

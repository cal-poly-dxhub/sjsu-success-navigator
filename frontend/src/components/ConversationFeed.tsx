import { useEffect, useRef } from 'react';
import { LayoutGroup } from 'motion/react';
import type { ConversationTurn, RagPhase } from '../types/chat';
import { ConversationTurnView } from './ConversationTurnView';
import { PendingExchange } from './PendingExchange';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './ConversationFeed.css';

type ConversationFeedProps = {
	turns: ConversationTurn[];
	pendingPrompt?: string | null;
	introDelayMs?: number;
	onTypingChange?: (typing: boolean) => void;
	onPhaseChange: (turnId: string, phase: RagPhase | 'done') => void;
	onFollowup: (prompt: string) => void;
};

export function ConversationFeed({
	turns,
	pendingPrompt = null,
	introDelayMs = 0,
	onTypingChange,
	onPhaseChange,
	onFollowup,
}: ConversationFeedProps) {
	const reduceMotion = usePrefersReducedMotion();
	const activeId = turns[turns.length - 1]?.id;
	const scrollKeyRef = useRef<string | null>(null);

	useEffect(() => {
		const scrollKey = pendingPrompt ? `pending:${pendingPrompt}` : activeId;
		if (!scrollKey || scrollKeyRef.current === scrollKey) return;
		scrollKeyRef.current = scrollKey;
		window.requestAnimationFrame(() => scrollToActiveTurn(reduceMotion));
	}, [activeId, pendingPrompt, reduceMotion]);

	if (!turns.length && !pendingPrompt) return null;

	return (
		<LayoutGroup>
			<div className="conversation-feed">
				{turns.map((turn) => (
					<ConversationTurnView
						key={turn.id}
						turn={turn}
						isActive={!pendingPrompt && turn.id === activeId}
						introDelayMs={turn.id === activeId && !pendingPrompt ? introDelayMs : 0}
						onTypingChange={
							turn.id === activeId && !pendingPrompt ? onTypingChange : undefined
						}
						onPhaseChange={onPhaseChange}
						onFollowup={onFollowup}
					/>
				))}
				{pendingPrompt ? <PendingExchange prompt={pendingPrompt} /> : null}
			</div>
		</LayoutGroup>
	);
}

export function scrollToActiveTurn(reduceMotion: boolean) {
	const target = document.getElementById('active-conversation-turn');
	if (!target) return;
	const top = window.scrollY + target.getBoundingClientRect().top - 5.25 * 16;
	window.scrollTo({
		top: Math.max(0, top),
		behavior: reduceMotion ? 'auto' : 'smooth',
	});
}

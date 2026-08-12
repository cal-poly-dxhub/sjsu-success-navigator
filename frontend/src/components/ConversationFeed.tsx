import { useEffect, useRef } from 'react';
import { LayoutGroup } from 'motion/react';
import type { ConversationTurn, RagPhase } from '../types/chat';
import { ConversationTurnView } from './ConversationTurnView';
import { PendingExchange } from './PendingExchange';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { scrollToActiveTurn } from '../lib/scrollAnchor';
import './ConversationFeed.css';

type ConversationFeedProps = {
	turns: ConversationTurn[];
	pendingPrompt?: string | null;
	/** Streamed prose for the pending exchange. A preview; the finished turn replaces it. */
	pendingPreview?: string;
	/** What the server says it is doing while the pending exchange has no prose yet. */
	pendingStage?: string | null;
	/**
	 * The turn that just took the pending exchange's place, if any. Its bubble is the one
	 * that was already on screen a frame ago, so it does not play an entrance.
	 */
	continuedTurnId?: string | null;
	introDelayMs?: number;
	onTypingChange?: (typing: boolean) => void;
	onPhaseChange: (turnId: string, phase: RagPhase | 'done') => void;
	onFollowup: (prompt: string) => void;
	/**
	 * Whether this deployment has anywhere to escalate to (config.json's
	 * escalationRecipient). Passed down rather than read here so one fetch at the top of the
	 * app decides it for every turn, exactly as the cost panel's presence is decided.
	 */
	escalationEnabled?: boolean;
};

export function ConversationFeed({
	turns,
	pendingPrompt = null,
	pendingPreview = '',
	pendingStage = null,
	continuedTurnId = null,
	introDelayMs = 0,
	onTypingChange,
	onPhaseChange,
	onFollowup,
	escalationEnabled = false,
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
						continuesPending={turn.id === continuedTurnId}
						introDelayMs={turn.id === activeId && !pendingPrompt ? introDelayMs : 0}
						onTypingChange={
							turn.id === activeId && !pendingPrompt ? onTypingChange : undefined
						}
						onPhaseChange={onPhaseChange}
						onFollowup={onFollowup}
						escalationEnabled={escalationEnabled}
					/>
				))}
				{pendingPrompt ? (
					<PendingExchange
						prompt={pendingPrompt}
						preview={pendingPreview}
						stage={pendingStage}
					/>
				) : null}
			</div>
		</LayoutGroup>
	);
}

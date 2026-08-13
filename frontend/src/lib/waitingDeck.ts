/**
 * The waiting deck's state, as one object the rest of the app can ask questions of.
 *
 * WHY THIS EXISTS AT ALL. The deck shuffles while the model writes its cards, and the deal
 * that follows must never start out of a stack that is mid-move - a card cannot slide out
 * from under a stack that is halfway through re-ordering itself. The deck belongs to the
 * pending exchange and the deal to the finished turn, so the two never meet: something has
 * to carry "is it between moves?" across that boundary, and this is it.
 *
 * It replaces arithmetic. The first attempt derived the still moments from the percentages
 * of a CSS keyframe circuit and had the caller wait out a computed delay - which is a second
 * model of the animation, kept in step with the first by hand, and wrong the moment either
 * changed. The deck now says when it is between moves because it is the thing doing the
 * moving.
 *
 * HELD, not just settled. `settleAndHold` resolves between moves AND stops the next one
 * starting, because the caller's next act is to replace this deck with the real one - a move
 * beginning in that gap would be a move the hand-off has already stopped watching for.
 */
class WaitingDeck {
	private moving = false;
	private held = false;
	private waiters: Array<() => void> = [];

	/** Whether the deck has been asked to stop. The component checks this between moves. */
	isHeld(): boolean {
		return this.held;
	}

	beginMove(): void {
		this.moving = true;
	}

	endMove(): void {
		this.moving = false;
		if (this.waiters.length === 0) return;
		// Somebody is waiting for exactly this instant, so the deck stops here.
		this.held = true;
		const waiting = this.waiters.splice(0);
		for (const resolve of waiting) resolve();
	}

	/** A fresh deck is free to move again. Called when one mounts. */
	release(): void {
		this.held = false;
		this.moving = false;
	}

	/**
	 * Resolve at the next moment the deck is still, and keep it still.
	 *
	 * Resolves immediately when nothing is moving, which covers both "between moves" and
	 * "there is no deck on screen at all" - a turn that never showed one must not wait.
	 */
	settleAndHold(): Promise<void> {
		if (!this.moving) {
			this.held = true;
			return Promise.resolve();
		}
		return new Promise((resolve) => this.waiters.push(resolve));
	}
}

export const waitingDeck = new WaitingDeck();

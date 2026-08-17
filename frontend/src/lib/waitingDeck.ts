/**
 * The waiting deck's state, as one object the rest of the app can ask questions of.
 *
 * WHY THIS EXISTS AT ALL. The deck cycles while the model writes its cards, and the deal that
 * follows must never start out of a stack that is mid-move - a card cannot slide out from
 * under a stack that is halfway through re-ordering itself. The deck belongs to the pending
 * exchange and the deal to the finished turn, so the two never meet: something has to carry
 * "is it between moves?" across that boundary, and this is it.
 *
 * It replaces arithmetic. The first attempt derived the still moments from the percentages of
 * a CSS keyframe circuit and had the caller wait out a computed delay - which is a second
 * model of the animation, kept in step with the first by hand, and wrong the moment either
 * changed. The deck now says when it is between moves because it is the thing doing the
 * moving.
 *
 * IT ALSO CARRIES THE COUNT, which is the second thing that has to cross that boundary. The
 * deck waits at four cards because nobody knows yet how many are coming; the reply then
 * arrives knowing exactly. `settleAndCompress` hands that number back so the deck can shed
 * its surplus before the swap, and holds the reply until it has - see CardDeck.tsx for the
 * ripple itself. Without it the stack is four and the group is one, and the hand-off is two
 * different objects pretending to be one.
 */
class WaitingDeck {
	/**
	 * Whether a deck is on screen at all. Everything below is a no-op without one, which is
	 * what makes a turn that never showed a deck resolve instantly rather than wait for a
	 * report from a component that does not exist.
	 */
	private attached = false;
	/** How many card objects the mounted deck has. The deck says so; nothing assumes 4. */
	private depth = 0;
	private moving = false;
	private held = false;
	private restWaiters: Array<() => void> = [];
	private compressTo: number | null = null;
	private compressWaiters: Array<() => void> = [];

	/** A deck has mounted and is free to move. */
	attach(depth: number): void {
		this.attached = true;
		this.depth = depth;
		this.held = false;
		this.moving = false;
		this.compressTo = null;
	}

	/**
	 * The deck has gone.
	 *
	 * DRAINS EVERY WAITER, and that is not tidiness. A caller parked on one of these promises
	 * is a reply that has not been applied yet: strand it and the turn never arrives at all,
	 * and the student is left looking at a deck that is no longer there. Resolving early is
	 * always recoverable - the worst case is a deal that starts a beat sooner than it would
	 * have - and hanging never is.
	 */
	detach(): void {
		this.attached = false;
		this.depth = 0;
		this.compressTo = null;
		this.drain(this.restWaiters);
		this.drain(this.compressWaiters);
	}

	private drain(list: Array<() => void>): void {
		const waiting = list.splice(0);
		for (const resolve of waiting) resolve();
	}

	/** Whether the deck has been asked to stop cycling. The component checks this each frame. */
	isHeld(): boolean {
		return this.held;
	}

	beginMove(): void {
		this.moving = true;
	}

	endMove(): void {
		this.moving = false;
		if (this.restWaiters.length === 0) return;
		// Somebody is waiting for exactly this instant, so the deck stops here.
		this.held = true;
		this.drain(this.restWaiters);
	}

	/**
	 * How many cards the deck should be left showing, or null when there is nothing to do.
	 * Read by the component every frame; it starts the ripple the first time this is set.
	 */
	compressTarget(): number | null {
		return this.compressTo;
	}

	/** The deck reporting that its ripple has finished and it is square at the new count. */
	compressDone(): void {
		this.compressTo = null;
		this.drain(this.compressWaiters);
	}

	/**
	 * Resolve once the deck is standing square AT `count` CARDS, having shed the rest.
	 *
	 * Two waits, in order, because they are two different things. First for a REST - the deck
	 * cycles on its own clock and a card cannot be pulled out of a stack that is mid-move.
	 * Then for the COMPRESS - the surplus cards tucking up under the top one, so the stack the
	 * deal comes out of is the size the group is going to be.
	 *
	 * A count of zero is not compressed to nothing: a deck that evaporates reads as a failure
	 * rather than as an answer, and the only way to get here with none is a safety turn, whose
	 * cards were dropped after the fact. That settles and holds as it always did, and the
	 * exchange takes the deck with it when it goes.
	 */
	settleAndCompress(count: number): Promise<void> {
		if (!this.attached) return Promise.resolve();
		return this.settle().then(() => {
			// Gone while we waited, or nothing to compress to.
			if (!this.attached || count <= 0) return;
			this.compressTo = Math.min(Math.max(1, Math.round(count)), this.depth);
			return new Promise<void>((resolve) => this.compressWaiters.push(resolve));
		});
	}

	/**
	 * Resolve at the next moment the deck is still, and keep it still.
	 *
	 * Resolves immediately when nothing is moving, which covers both "between moves" and
	 * "there is no deck on screen at all" - a turn that never showed one must not wait.
	 */
	private settle(): Promise<void> {
		if (!this.moving) {
			this.held = true;
			return Promise.resolve();
		}
		return new Promise((resolve) => this.restWaiters.push(resolve));
	}
}

export const waitingDeck = new WaitingDeck();

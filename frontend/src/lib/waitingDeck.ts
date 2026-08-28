/** The waiting deck's state, as one object the rest of the app can ask questions of. */
class WaitingDeck {
	/** Whether a deck is on screen at all. */
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

	/** The deck has gone. */
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

	/** How many cards the deck should be left showing, or null when there is nothing to do. */
	compressTarget(): number | null {
		return this.compressTo;
	}

	/** The deck reporting that its ripple has finished and it is square at the new count. */
	compressDone(): void {
		this.compressTo = null;
		this.drain(this.compressWaiters);
	}

	/** Resolve once the deck is standing square at `count` cards, having shed the rest. */
	settleAndCompress(count: number): Promise<void> {
		if (!this.attached) return Promise.resolve();
		return this.settle().then(() => {
			// Gone while we waited, or nothing to compress to.
			if (!this.attached || count <= 0) return;
			this.compressTo = Math.min(Math.max(1, Math.round(count)), this.depth);
			return new Promise<void>((resolve) => this.compressWaiters.push(resolve));
		});
	}

	/** Resolve at the next moment the deck is still, and keep it still. */
	private settle(): Promise<void> {
		if (!this.moving) {
			this.held = true;
			return Promise.resolve();
		}
		return new Promise((resolve) => this.restWaiters.push(resolve));
	}
}

export const waitingDeck = new WaitingDeck();

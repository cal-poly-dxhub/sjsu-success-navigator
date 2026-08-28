/** Every number that decides how the deck moves, in one mutable object. */
export type DeckTuning = {
	/** One nudge, out and back. */
	cyclePopMs: number;
	/** Everything stands still for this long before the card above takes its turn. */
	cyclePauseMs: number;
	/** How far the card nudges out of the stack. */
	cyclePopPx: number;
	/** How far it leans at full extension, pivoting from its top edge. */
	cycleTiltDeg: number;
	/** Per-card depth in the stack, waiting and dealing alike. */
	deckStepPx: number;
	/** How tall the deck stands. Mirrored into CSS as `--waiting-deck-h`. */
	waitingDeckHeightPx: number;

	/** One surplus card's trip up under the top card. */
	rippleMs: number;
	/** Gap between one surplus card starting and the next. Shorter than rippleMs, so they
	 * overlap. */
	rippleStaggerMs: number;
	/** The beat after the last one lands, before the deal is allowed to begin. */
	compressSettleMs: number;

	/** Between one card leaving the deck and the next. */
	dealStaggerS: number;
	/** Cards are timed by distance so they all travel at about this speed. */
	dealSpeedPxS: number;
	/** Floor, so a short hop is not over before it registers. This is what the top card gets. */
	dealMinDurationS: number;
	/** Ceiling, so the longest flight does not drag. */
	dealMaxDurationS: number;
	/** How far into a card's flight its turn-over starts, as a fraction of that flight. */
	flipStartFraction: number;
	/** How long the turn-over takes, as a fraction of the flight. */
	flipDurationFraction: number;
	/** Floor on the turn-over, so a short flight still reads as a flip. */
	flipMinS: number;
};

export const DECK_TUNING_DEFAULTS: Readonly<DeckTuning> = {
	cyclePopMs: 1140,
	cyclePauseMs: 860,
	cyclePopPx: 9,
	cycleTiltDeg: 2.1,
	deckStepPx: 7,
	waitingDeckHeightPx: 198,

	rippleMs: 260,
	rippleStaggerMs: 90,
	compressSettleMs: 130,

	dealStaggerS: 0.34,
	dealSpeedPxS: 1150,
	dealMinDurationS: 0.46,
	dealMaxDurationS: 0.62,
	flipStartFraction: 0.4,
	flipDurationFraction: 0.6,
	flipMinS: 0.26,
};

export const deckTuning: DeckTuning = { ...DECK_TUNING_DEFAULTS };

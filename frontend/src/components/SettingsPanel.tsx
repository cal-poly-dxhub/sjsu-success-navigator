import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useRef } from 'react';
import type { CostModel } from '../lib/runtimeConfig';
import type { ConversationUsage } from '../types/chat';
import { LANGUAGES, useLanguage, useStrings, type Language } from '../lib/i18n';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import { CostBreakdown } from './CostBreakdown';
import './SettingsPanel.css';

type SettingsPanelProps = {
	open: boolean;
	/**
	 * The cost model, or null when the stack stamped none. NULL IS THE GATE and it is the
	 * only thing it gates now: settings itself is always available, because the language
	 * choice inside it is for the student and has nothing to do with what a demo costs.
	 * With no model the cost section is not rendered at all - not disabled, not empty.
	 */
	costModel: CostModel | null;
	/** What the conversation on screen has billed. Only ever read by the cost section. */
	usage?: ConversationUsage;
	onClose: () => void;
};

/**
 * Settings: the panel behind the gear in the sidebar.
 *
 * IT EXISTS FOR THE LANGUAGE PICKER. SJSU's sponsor asked that students be met in their own
 * language, and the picker is the one control here a student has any reason to touch. The
 * cost breakdown - which used to BE this dialog, opened directly by the gear - is now a
 * collapsed section inside it, closed until a sponsor opens it, because it is an instrument
 * for a demo audience and a student opening settings to change language should not be shown
 * what their questions cost.
 *
 * The dialog behaviour is the cost panel's, moved up a level and unchanged: Escape closes,
 * the body stops scrolling underneath, focus is trapped while it is open and returned to the
 * gear when it closes. `summary` is in the focusable query because the collapsed section's
 * own toggle is one, and a Tab that skipped it could not reach the cost figures at all.
 */
export function SettingsPanel({ open, costModel, usage, onClose }: SettingsPanelProps) {
	const t = useStrings();
	const [language, setLanguage] = useLanguage();
	const panelRef = useRef<HTMLDivElement | null>(null);
	const previousActiveRef = useRef<HTMLElement | null>(null);
	const reduceMotion = usePrefersReducedMotion();

	useEffect(() => {
		if (!open) return;

		previousActiveRef.current =
			document.activeElement instanceof HTMLElement ? document.activeElement : null;
		const previousOverflow = document.body.style.overflow;
		document.body.style.overflow = 'hidden';

		const handleKeyDown = (event: KeyboardEvent) => {
			if (event.key === 'Escape') {
				onClose();
				return;
			}
			// A focus trap, for the same reason the safety modal has one: this panel covers
			// the conversation, so tabbing out of it lands on controls the student cannot
			// see. The select and the slider are real form controls, so arrow keys work
			// without help.
			if (event.key !== 'Tab' || !panelRef.current) return;
			const focusables = Array.from(
				panelRef.current.querySelectorAll<HTMLElement>(
					'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
				),
			);
			if (focusables.length === 0) return;
			const first = focusables[0];
			const last = focusables[focusables.length - 1];
			if (event.shiftKey && document.activeElement === first) {
				event.preventDefault();
				last.focus();
			} else if (!event.shiftKey && document.activeElement === last) {
				event.preventDefault();
				first.focus();
			}
		};

		document.addEventListener('keydown', handleKeyDown);
		return () => {
			document.removeEventListener('keydown', handleKeyDown);
			document.body.style.overflow = previousOverflow;
			previousActiveRef.current?.focus();
		};
	}, [open, onClose]);

	const chosen = LANGUAGES.find((option) => option.code === language);

	return (
		<AnimatePresence>
			{open ? (
				<>
					<motion.button
						type="button"
						className="settings-panel__scrim"
						aria-label={t.settingsClose}
						onClick={onClose}
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						exit={{ opacity: 0 }}
						transition={{ duration: reduceMotion ? 0 : 0.18 }}
					/>
					<div className="settings-panel__layer">
						<motion.div
							className="settings-panel"
							role="dialog"
							aria-modal="true"
							aria-labelledby="settings-panel-title"
							ref={panelRef}
							initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.98 }}
							animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
							exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.99 }}
							transition={{ duration: reduceMotion ? 0 : 0.2 }}
						>
							<div className="settings-panel__head">
								<h2 id="settings-panel-title">{t.settings}</h2>
								<button
									type="button"
									className="settings-panel__close"
									onClick={onClose}
									aria-label={t.close}
								>
									<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
										<path
											d="M6 6l12 12M18 6L6 18"
											stroke="currentColor"
											strokeWidth="2.2"
											strokeLinecap="round"
										/>
									</svg>
								</button>
							</div>

							<div className="settings-panel__body">
								<section className="settings-field">
									<label className="settings-field__label" htmlFor="settings-language">
										{t.languageLabel}
									</label>
									{/*
									  A native select rather than a segmented control, and it is the
									  right shape rather than the cheap one: this list gets Hindi
									  next (the sponsor named it alongside Spanish), and every
									  language after that is one line in i18n.ts with nothing here
									  to re-lay-out. It also arrives already keyboard-operable and
									  already spoken correctly by a screen reader.
									*/}
									<select
										id="settings-language"
										className="settings-field__select"
										value={language}
										onChange={(event) => setLanguage(event.target.value as Language)}
									>
										{LANGUAGES.map((option) => (
											// Labelled in the language itself, so someone who cannot
											// read the current one can still find their own.
											<option key={option.code} value={option.code}>
												{option.label}
											</option>
										))}
									</select>
									<p className="settings-field__hint">{t.languageHint}</p>
									{/* Said to the student, not just recorded in the file: these
									    strings have not been read by a Spanish speaker yet. */}
									{chosen && !chosen.reviewed ? (
										<p className="settings-field__hint settings-field__hint--flag">
											{t.languageUnreviewed}
										</p>
									) : null}
								</section>

								{costModel ? (
									// Native details/summary: the open state is the browser's, the
									// toggle is a real focusable control, and there is no state
									// here to get out of step with what is on screen.
									<details className="settings-section">
										{/* The title alone. An amber "ESTIMATE" pill used to sit beside it;
										    the caveat is already made properly at the foot of the
										    breakdown ("These are estimates, not a bill"), and a warning
										    badge on a closed row read as something being wrong rather
										    than as a note about precision. */}
										<summary className="settings-section__summary">
											<span>{t.costSection}</span>
										</summary>
										<div className="settings-section__body">
											<CostBreakdown model={costModel} usage={usage} />
										</div>
									</details>
								) : null}
							</div>
						</motion.div>
					</div>
				</>
			) : null}
		</AnimatePresence>
	);
}

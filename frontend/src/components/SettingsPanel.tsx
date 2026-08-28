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
	/** The cost model, or null when the stack stamped none. */
	costModel: CostModel | null;
	/** What the conversation on screen has billed. Only ever read by the cost section. */
	usage?: ConversationUsage;
	onClose: () => void;
};

/** Settings: the panel behind the gear in the sidebar. */
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
			// A focus trap, for the same reason the safety modal has one: this panel covers the
			// conversation, so tabbing out of it lands on controls the student cannot see.
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
									{/* A native select, so another language is one line in
									 * i18n.ts with nothing to re-lay-out. */}
									<select
										id="settings-language"
										className="settings-field__select"
										value={language}
										onChange={(event) => setLanguage(event.target.value as Language)}
									>
										{LANGUAGES.map((option) => (
											// Labelled in the language itself, so it is legible
											// to the person choosing it.
											<option key={option.code} value={option.code}>
												{option.label}
											</option>
										))}
									</select>
									<p className="settings-field__hint">{t.languageHint}</p>
									{/* Said to the student, not just recorded in the file: these
									 * strings have not been read by a Spanish speaker yet. */}
									{chosen && !chosen.reviewed ? (
										<p className="settings-field__hint settings-field__hint--flag">
											{t.languageUnreviewed}
										</p>
									) : null}
								</section>

								{costModel ? (
									// Native details/summary, so there is no open state here to
									// get out of step.
									<details className="settings-section">
										{/* The title alone.  */}
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

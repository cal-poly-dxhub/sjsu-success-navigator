import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useRef } from 'react';
import {
	findSjsuCaresService,
	SJSU_CARES_CONTACT_PAGE,
	SJSU_CARES_EMAIL,
	SJSU_CARES_HOURS,
	SJSU_CARES_LOCATION,
	SJSU_CARES_NOTE,
	SJSU_CARES_OVERVIEW,
	SJSU_CARES_PHONE,
	SJSU_CARES_REQUEST_FORM,
	SJSU_CARES_SERVICES_INDEX,
} from '../lib/sjsuCares';
import type { SjsuCaresService } from '../lib/sjsuCares';
import { PressableButton } from './PressableButton';
import './SjsuCaresModal.css';

type SjsuCaresModalProps = {
	open: boolean;
	onClose: () => void;
	highlightedServiceTheme?: SjsuCaresService['theme'] | null;
};

const TEL_HREF = `tel:${SJSU_CARES_PHONE.replaceAll('.', '')}`;

export function SjsuCaresModal({
	open,
	onClose,
	highlightedServiceTheme = null,
}: SjsuCaresModalProps) {
	const panelRef = useRef<HTMLElement | null>(null);
	const previousActiveRef = useRef<HTMLElement | null>(null);
	const recommended = findSjsuCaresService(highlightedServiceTheme);

	useEffect(() => {
		if (!open) return;

		previousActiveRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
		const previousOverflow = document.body.style.overflow;
		document.body.style.overflow = 'hidden';

		const handleKeyDown = (event: KeyboardEvent) => {
			if (event.key === 'Escape') {
				onClose();
				return;
			}

			if (event.key !== 'Tab' || !panelRef.current) return;
			const focusables = Array.from(
				panelRef.current.querySelectorAll<HTMLElement>(
					'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
				),
			).filter((element) => !element.hasAttribute('hidden'));
			if (focusables.length === 0) return;

			const first = focusables[0];
			const last = focusables[focusables.length - 1];
			const activeElement = document.activeElement;

			if (event.shiftKey && activeElement === first) {
				event.preventDefault();
				last.focus();
			} else if (!event.shiftKey && activeElement === last) {
				event.preventDefault();
				first.focus();
			}
		};

		window.setTimeout(() => {
			if (!panelRef.current) return;
			const firstFocusable = panelRef.current.querySelector<HTMLElement>(
				'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
			);
			firstFocusable?.focus();
		}, 0);

		window.addEventListener('keydown', handleKeyDown);

		return () => {
			document.body.style.overflow = previousOverflow;
			window.removeEventListener('keydown', handleKeyDown);
			previousActiveRef.current?.focus();
		};
	}, [open, onClose]);

	return (
		<AnimatePresence>
			{open ? (
				<div className="cares" role="dialog" aria-modal="true" aria-labelledby="cares-title">
					<motion.button
						type="button"
						className="cares__backdrop"
						aria-label="Close SJSU Cares information"
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						exit={{ opacity: 0 }}
						transition={{ duration: 0.18 }}
						onClick={onClose}
					/>

					<motion.section
						ref={panelRef}
						className="cares__panel"
						initial={{ opacity: 0, y: 24, scale: 0.98 }}
						animate={{ opacity: 1, y: 0, scale: 1 }}
						exit={{ opacity: 0, y: 16, scale: 0.98 }}
						transition={{ type: 'spring', stiffness: 320, damping: 28 }}
					>
						<header className="cares__masthead">
							<div className="cares__identity">
								<p className="cares__eyebrow">San José State University</p>
								<h2 id="cares-title" className="cares__title">
									SJSU Cares
								</h2>
							</div>

							<button
								type="button"
								className="cares__close"
								aria-label="Close SJSU Cares information"
								onClick={onClose}
							>
								<span aria-hidden="true">×</span>
							</button>
						</header>

						<div className="cares__body">
							<p className="cares__intro">{SJSU_CARES_OVERVIEW}</p>

							<PressableButton
								variant="secondary"
								className="cares__primary"
								href={SJSU_CARES_REQUEST_FORM}
							>
								<span className="cares__primary-label">Request assistance</span>
								<span className="cares__primary-hint">The fastest way to reach a case manager</span>
							</PressableButton>

							<div className="cares__direct">
								<PressableButton variant="ghost" className="cares__direct-link" href={TEL_HREF}>
									Call {SJSU_CARES_PHONE}
								</PressableButton>
								<PressableButton
									variant="ghost"
									className="cares__direct-link"
									href={`mailto:${SJSU_CARES_EMAIL}`}
								>
									Email {SJSU_CARES_EMAIL}
								</PressableButton>
							</div>

							<dl className="cares__facts">
								<div className="cares__fact">
									<dt>Hours</dt>
									<dd>{SJSU_CARES_HOURS}</dd>
								</div>
								<div className="cares__fact">
									<dt>Office</dt>
									<dd>{SJSU_CARES_LOCATION}</dd>
								</div>
							</dl>

							{recommended ? (
								<PressableButton
									variant="ghost"
									className="cares__service"
									href={recommended.href}
								>
									<span className="cares__service-badge">Recommended for your question</span>
									<span className="cares__service-title">{recommended.title}</span>
									<span className="cares__service-desc">{recommended.description}</span>
								</PressableButton>
							) : null}

							<div className="cares__more">
								<a href={SJSU_CARES_SERVICES_INDEX} target="_blank" rel="noopener noreferrer">
									All SJSU Cares services
								</a>
								<a href={SJSU_CARES_CONTACT_PAGE} target="_blank" rel="noopener noreferrer">
									Staff directory and full contact list
								</a>
							</div>

							<p className="cares__note">{SJSU_CARES_NOTE}</p>
						</div>
					</motion.section>
				</div>
			) : null}
		</AnimatePresence>
	);
}

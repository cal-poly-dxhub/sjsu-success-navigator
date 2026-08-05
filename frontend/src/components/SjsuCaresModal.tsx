import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
	OFFICIAL_CAMPUS_CONTACTS,
	SJSU_CARES_ACTIONS,
	SJSU_CARES_CONTACTS,
	SJSU_CARES_LOCATION,
	SJSU_CARES_NOTE,
	SJSU_CARES_OVERVIEW,
	SJSU_CARES_SERVICES,
	SJSU_CARES_STEPS,
} from '../lib/sjsuCares';
import type { SjsuCaresService } from '../lib/sjsuCares';
import { PressableButton } from './PressableButton';
import './SjsuCaresModal.css';

type SjsuCaresModalProps = {
	open: boolean;
	onClose: () => void;
	highlightedServiceTheme?: SjsuCaresService['theme'] | null;
};

export function SjsuCaresModal({
	open,
	onClose,
	highlightedServiceTheme = null,
}: SjsuCaresModalProps) {
	const [expandedContactEmail, setExpandedContactEmail] = useState<string | null>(
		SJSU_CARES_CONTACTS[0]?.email ?? null,
	);
	const panelRef = useRef<HTMLElement | null>(null);
	const previousActiveRef = useRef<HTMLElement | null>(null);

	const orderedServices = useMemo(() => {
		if (!highlightedServiceTheme) return SJSU_CARES_SERVICES;
		return [...SJSU_CARES_SERVICES].sort((left, right) => {
			if (left.theme === highlightedServiceTheme && right.theme !== highlightedServiceTheme) return -1;
			if (right.theme === highlightedServiceTheme && left.theme !== highlightedServiceTheme) return 1;
			return 0;
		});
	}, [highlightedServiceTheme]);

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

	useEffect(() => {
		if (!open) return;
		const matchingContact = SJSU_CARES_CONTACTS.find(
			(contact) => contact.serviceTheme === highlightedServiceTheme,
		);
		setExpandedContactEmail(matchingContact?.email ?? SJSU_CARES_CONTACTS[0]?.email ?? null);
	}, [highlightedServiceTheme, open]);

	return (
		<AnimatePresence>
			{open ? (
				<div className="sjsu-cares-modal" role="dialog" aria-modal="true" aria-labelledby="sjsu-cares-title">
					<motion.button
						type="button"
						className="sjsu-cares-modal__backdrop"
						aria-label="Close SJSU Cares information"
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						exit={{ opacity: 0 }}
						transition={{ duration: 0.18 }}
						onClick={onClose}
					/>

					<motion.section
						ref={panelRef}
						className="sjsu-cares-modal__panel"
						initial={{ opacity: 0, y: 28, scale: 0.97 }}
						animate={{ opacity: 1, y: 0, scale: 1 }}
						exit={{ opacity: 0, y: 18, scale: 0.98 }}
						transition={{ type: 'spring', stiffness: 320, damping: 28 }}
					>
						<div className="sjsu-cares-modal__header">
							<div>
								<p className="sjsu-cares-modal__eyebrow">Talk to a person</p>
								<h2 id="sjsu-cares-title" className="sjsu-cares-modal__title">
									Connect with SJSU Cares
								</h2>
								<p className="sjsu-cares-modal__intro">{SJSU_CARES_OVERVIEW}</p>
							</div>

							<button
								type="button"
								className="sjsu-cares-modal__close"
								aria-label="Close SJSU Cares information"
								onClick={onClose}
							>
								<span aria-hidden="true">×</span>
							</button>
						</div>

						<div className="sjsu-cares-modal__hero">
							<div className="sjsu-cares-modal__callout">
								<p className="sjsu-cares-modal__callout-label">Best first step</p>
								<h3>Submit a request for assistance</h3>
								<p>
									SJSU Cares can follow up with next steps, eligibility details, and the campus or community
									resources that fit your situation.
								</p>
								<div className="sjsu-cares-modal__priority-actions">
									{SJSU_CARES_ACTIONS.slice(0, 2).map((action) => (
										<PressableButton
											key={action.label}
											href={action.href}
											variant={action.variant}
											className="sjsu-cares-modal__priority-action"
										>
											{action.label}
										</PressableButton>
									))}
								</div>
							</div>

							<div className="sjsu-cares-modal__essentials">
								<div className="sjsu-cares-modal__essential">
									<span className="sjsu-cares-modal__essential-label">Main line</span>
									<a href="tel:4089241234">408.924.1234</a>
								</div>
								<div className="sjsu-cares-modal__essential">
									<span className="sjsu-cares-modal__essential-label">Location</span>
									<p>{SJSU_CARES_LOCATION}</p>
								</div>
								<div className="sjsu-cares-modal__essential">
									<span className="sjsu-cares-modal__essential-label">Helpful note</span>
									<p>{SJSU_CARES_NOTE}</p>
								</div>
								<div className="sjsu-cares-modal__micro-actions">
									{SJSU_CARES_ACTIONS.slice(2).map((action) => (
										<PressableButton
											key={action.label}
											href={action.href}
											variant={action.variant}
											className="sjsu-cares-modal__micro-action"
										>
											{action.label}
										</PressableButton>
									))}
								</div>
							</div>
						</div>

						<div className="sjsu-cares-modal__grid">
							<section className="sjsu-cares-modal__section">
								<div className="sjsu-cares-modal__section-header">
									<p className="sjsu-cares-modal__eyebrow">Services</p>
									<h3>How SJSU Cares can help</h3>
								</div>
								<div className="sjsu-cares-modal__services">
									{orderedServices.map((service) => (
										<a
											key={service.title}
											className={`sjsu-cares-modal__service-card sjsu-cares-modal__service-card--${service.theme}${highlightedServiceTheme === service.theme ? ' sjsu-cares-modal__service-card--highlighted' : ''}`}
											href={service.href}
											target="_blank"
											rel="noopener noreferrer"
										>
											{highlightedServiceTheme === service.theme ? (
												<span className="sjsu-cares-modal__service-badge">Recommended for your question</span>
											) : null}
											<div className="sjsu-cares-modal__service-topline">
												<span className="sjsu-cares-modal__service-mark" aria-hidden="true">
													{service.theme === 'food'
														? 'F'
														: service.theme === 'housing'
															? 'H'
															: service.theme === 'financial'
																? '$'
																: 'P'}
												</span>
												<p>{service.kicker}</p>
											</div>
											<h4>{service.title}</h4>
											<p>{service.description}</p>
											<span className="sjsu-cares-modal__service-link">Open service</span>
										</a>
									))}
								</div>
							</section>

							<section className="sjsu-cares-modal__section">
								<div className="sjsu-cares-modal__section-header">
									<p className="sjsu-cares-modal__eyebrow">What to expect</p>
									<h3>What happens after you reach out</h3>
								</div>
								<div className="sjsu-cares-modal__steps">
									{SJSU_CARES_STEPS.map((step, index) => (
										<article key={step.title} className="sjsu-cares-modal__step-card">
											<span className="sjsu-cares-modal__step-number">0{index + 1}</span>
											<div>
												<h4>{step.title}</h4>
												<p>{step.description}</p>
											</div>
										</article>
									))}
								</div>
							</section>
						</div>

						<section className="sjsu-cares-modal__section sjsu-cares-modal__people-section">
							<div className="sjsu-cares-modal__section-header">
								<p className="sjsu-cares-modal__eyebrow">People</p>
								<h3>Reach the SJSU Cares team</h3>
								<p className="sjsu-cares-modal__section-intro">
									Choose the team member whose area feels closest to what you need, or start with the main
									request form if you are unsure.
								</p>
							</div>
							<div className="sjsu-cares-modal__contacts">
								{SJSU_CARES_CONTACTS.map((contact) => {
									const expanded = expandedContactEmail === contact.email;
									return (
										<article
											key={contact.email}
											className={`sjsu-cares-modal__contact-card${expanded ? ' sjsu-cares-modal__contact-card--expanded' : ''}`}
										>
											<button
												type="button"
												className="sjsu-cares-modal__contact-toggle"
												onClick={() =>
													setExpandedContactEmail((current) =>
														current === contact.email ? null : contact.email,
													)
												}
												aria-expanded={expanded}
											>
												<div className="sjsu-cares-modal__contact-copy">
													<p className="sjsu-cares-modal__contact-role">{contact.role}</p>
													<h4>{contact.name}</h4>
													<p>{contact.focus}</p>
												</div>
												<span className="sjsu-cares-modal__contact-chevron" aria-hidden="true">
													{expanded ? '−' : '+'}
												</span>
											</button>

											<AnimatePresence initial={false}>
												{expanded ? (
													<motion.div
														className="sjsu-cares-modal__contact-details"
														initial={{ height: 0, opacity: 0 }}
														animate={{ height: 'auto', opacity: 1 }}
														exit={{ height: 0, opacity: 0 }}
														transition={{ duration: 0.22, ease: 'easeOut' }}
													>
														<div className="sjsu-cares-modal__contact-details-inner">
															<div className="sjsu-cares-modal__contact-actions">
																<a href={`mailto:${contact.email}`}>{contact.email}</a>
																{contact.phone ? (
																	<a href={`tel:${contact.phone.replaceAll('.', '')}`}>{contact.phone}</a>
																) : null}
																{contact.appointmentHref ? (
																	<a
																		href={contact.appointmentHref}
																		target="_blank"
																		rel="noopener noreferrer"
																	>
																		Make appointment
																	</a>
																) : null}
															</div>
														</div>
													</motion.div>
												) : null}
											</AnimatePresence>
										</article>
									);
								})}
							</div>
						</section>

						<section className="sjsu-cares-modal__section sjsu-cares-modal__directory-section">
							<div className="sjsu-cares-modal__section-header">
								<p className="sjsu-cares-modal__eyebrow">More contacts</p>
								<h3>Official contacts from other SJSU resource pages</h3>
								<p className="sjsu-cares-modal__section-intro">
									These are broader campus contacts pulled from the official SJSU pages currently indexed in
									this project, so the directory is not limited to SJSU Cares alone.
								</p>
							</div>

							<div className="sjsu-cares-modal__directory">
								{OFFICIAL_CAMPUS_CONTACTS.map((office) => (
									<article
										key={`${office.office}-${office.sourceUrl}`}
										className={`sjsu-cares-modal__directory-card sjsu-cares-modal__directory-card--${office.theme}`}
									>
										<div className="sjsu-cares-modal__directory-topline">
											<span className="sjsu-cares-modal__directory-kicker">{office.sourceLabel}</span>
											<a
												className="sjsu-cares-modal__directory-source"
												href={office.sourceUrl}
												target="_blank"
												rel="noopener noreferrer"
											>
												Open source
											</a>
										</div>
										<h4>{office.office}</h4>
										<p>{office.summary}</p>
										<div className="sjsu-cares-modal__directory-methods">
											{office.methods.map((method) => (
												<a key={method.label} href={method.href} className="sjsu-cares-modal__directory-method">
													{method.label}
												</a>
											))}
										</div>
									</article>
								))}
							</div>
						</section>
					</motion.section>
				</div>
			) : null}
		</AnimatePresence>
	);
}

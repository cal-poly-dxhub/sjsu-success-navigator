import type { SafetyHandoff as SafetyHandoffData } from '../types/chat';
import { useStrings } from '../lib/i18n';
import { PressableButton } from './PressableButton';
import './SafetyHandoff.css';

type SafetyHandoffProps = {
	handoff: SafetyHandoffData;
};

/**
 * THE LABEL IS THE ONLY THING THIS COMPONENT SAYS. Everything inside - the headline, the
 * body, and every contact's label, detail and href - arrives resolved from the server, where
 * app/safety.py holds the numbers in a table. That split is deliberate and it is why the
 * language work stops at the region's name: a crisis line is the one thing on this screen
 * that must be identical in every language, and there is no code path here that could reword
 * one even by accident.
 */
export function SafetyHandoff({ handoff }: SafetyHandoffProps) {
	const t = useStrings();

	return (
		<section className="safety-handoff" aria-label={t.safetyContactsAria}>
			<h2 className="safety-handoff__headline">{handoff.headline}</h2>
			<p className="safety-handoff__body">{handoff.body}</p>
			<ul className="safety-handoff__list">
				{handoff.contacts.map((contact) => (
					<li key={contact.id} className="safety-handoff__item">
						<PressableButton variant="safety" href={contact.href} className="safety-handoff__btn">
							<span className="safety-handoff__label">{contact.label}</span>
							<span className="safety-handoff__detail">{contact.detail}</span>
						</PressableButton>
					</li>
				))}
			</ul>
		</section>
	);
}

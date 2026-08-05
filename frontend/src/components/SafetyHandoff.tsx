import type { SafetyHandoff as SafetyHandoffData } from '../types/chat';
import { PressableButton } from './PressableButton';
import './SafetyHandoff.css';

type SafetyHandoffProps = {
	handoff: SafetyHandoffData;
};

export function SafetyHandoff({ handoff }: SafetyHandoffProps) {
	return (
		<section className="safety-handoff" aria-label="Safety contacts">
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

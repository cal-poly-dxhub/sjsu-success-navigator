import { useStrings } from '../lib/i18n';
import { PressableButton } from './PressableButton';
import './TalkToPersonPill.css';

type TalkToPersonPillProps = {
	onClick: () => void;
};

/**
 * The handoff to a human stands for an SJSU office rather than for the assistant, so it
 * keeps its own identity - the blue seal in a gold ring, and the SJSU Cares attribution
 * above the label. That identity now sits inside the app's shared button shape instead of
 * replacing it: this used to be a bare floating pill, which read as a control borrowed
 * from somewhere else.
 */
export function TalkToPersonPill({ onClick }: TalkToPersonPillProps) {
	const t = useStrings();
	return (
		<PressableButton
			variant="ghost"
			className="talk-pill"
			onClick={onClick}
			aria-label={t.talkToPersonAria}
		>
			<span className="talk-pill__seal" aria-hidden="true">
				<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" focusable="false">
					<path d="M12 12a4.2 4.2 0 1 0 0-8.4 4.2 4.2 0 0 0 0 8.4Zm0 1.8c-3.7 0-7.2 1.9-7.2 4.3v1.5c0 .5.4.8.9.8h12.6c.5 0 .9-.3.9-.8v-1.5c0-2.4-3.5-4.3-7.2-4.3Z" />
				</svg>
			</span>
			<span className="talk-pill__copy">
				<span className="talk-pill__kicker">SJSU Cares</span>
				<span className="talk-pill__label">{t.talkToPerson}</span>
			</span>
		</PressableButton>
	);
}

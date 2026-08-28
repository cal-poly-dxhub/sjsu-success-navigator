import { useStrings } from '../lib/i18n';
import './UserPrompt.css';

type UserPromptProps = {
	text: string;
};

export function UserPrompt({ text }: UserPromptProps) {
	const t = useStrings();
	return (
		<div className="user-prompt" aria-label={t.yourMessage}>
			<p className="user-prompt__text">{text}</p>
		</div>
	);
}

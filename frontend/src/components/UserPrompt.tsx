import './UserPrompt.css';

type UserPromptProps = {
	text: string;
};

export function UserPrompt({ text }: UserPromptProps) {
	return (
		<div className="user-prompt" aria-label="Your message">
			<p className="user-prompt__text">{text}</p>
		</div>
	);
}

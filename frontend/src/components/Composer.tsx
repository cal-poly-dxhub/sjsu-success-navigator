import { useState, type FormEvent } from 'react';
import { PressableButton } from './PressableButton';
import './Composer.css';

type ComposerProps = {
	disabled?: boolean;
	onSubmit: (query: string) => void;
};

export function Composer({ disabled = false, onSubmit }: ComposerProps) {
	const [value, setValue] = useState('');

	const handleSubmit = (event: FormEvent) => {
		event.preventDefault();
		const next = value.trim();
		if (!next || disabled) return;
		onSubmit(next);
		setValue('');
	};

	return (
		<form className="composer" onSubmit={handleSubmit}>
			<label className="composer__label" htmlFor="chat-input">
				Ask Sammy
			</label>
			<div className="composer__row">
				<input
					id="chat-input"
					className="composer__input"
					type="text"
					value={value}
					onChange={(e) => setValue(e.target.value)}
					placeholder="Ask about tutoring, advising, wellness…"
					autoComplete="off"
					disabled={disabled}
				/>
				<PressableButton type="submit" variant="primary" disabled={disabled || !value.trim()}>
					Send
				</PressableButton>
			</div>
		</form>
	);
}

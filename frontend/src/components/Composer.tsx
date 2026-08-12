import { useState, type FormEvent } from 'react';
import { useStrings } from '../lib/i18n';
import { PressableButton } from './PressableButton';
import './Composer.css';

type ComposerProps = {
	disabled?: boolean;
	onSubmit: (query: string) => void;
};

export function Composer({ disabled = false, onSubmit }: ComposerProps) {
	const t = useStrings();
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
				{t.askSammy}
			</label>
			<div className="composer__row">
				<input
					id="chat-input"
					className="composer__input"
					type="text"
					value={value}
					onChange={(e) => setValue(e.target.value)}
					placeholder={t.composerPlaceholder}
					autoComplete="off"
					disabled={disabled}
				/>
				<PressableButton type="submit" variant="primary" disabled={disabled || !value.trim()}>
					{t.send}
				</PressableButton>
			</div>
		</form>
	);
}

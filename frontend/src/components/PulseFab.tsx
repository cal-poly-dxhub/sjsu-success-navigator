import './PulseFab.css';

type PulseFabProps = {
	onClick: () => void;
	ariaLabel?: string;
	className?: string;
};

export function PulseFab({
	onClick,
	ariaLabel = 'Continue to resources',
	className = '',
}: PulseFabProps) {
	return (
		<button
			type="button"
			className={`pulse-fab${className ? ` ${className}` : ''}`}
			onClick={onClick}
			aria-label={ariaLabel}
		>
			<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">
				<path
					fill="currentColor"
					d="M12 16.5 4.5 9l1.4-1.4L12 13.7l6.1-6.1L19.5 9 12 16.5z"
				/>
			</svg>
		</button>
	);
}

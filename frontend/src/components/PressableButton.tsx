import { motion } from 'motion/react';
import type { MouseEvent, ReactNode } from 'react';
import { usePrefersReducedMotion } from '../hooks/usePrefersReducedMotion';
import './PressableButton.css';

export type PressableVariant = 'primary' | 'secondary' | 'safety' | 'ghost';

type PressableButtonProps = {
	children: ReactNode;
	variant?: PressableVariant;
	className?: string;
	disabled?: boolean;
	type?: 'button' | 'submit' | 'reset';
	href?: string;
	onClick?: (event: MouseEvent<HTMLElement>) => void;
	'aria-label'?: string;
	'aria-pressed'?: boolean | 'true' | 'false' | 'mixed';
};

export function PressableButton({
	children,
	variant = 'primary',
	className = '',
	disabled = false,
	type = 'button',
	href,
	onClick,
	...rest
}: PressableButtonProps) {
	const reduceMotion = usePrefersReducedMotion();
	const classes = `pressable pressable--${variant} ${className}`.trim();

	const motionProps = {
		className: classes,
		whileTap: reduceMotion || disabled ? undefined : { y: 4 },
		transition: { type: 'spring' as const, stiffness: 600, damping: 28 },
		onClick,
		...rest,
	};

	if (href) {
		return (
			<motion.a
				href={href}
				target={href.startsWith('http') ? '_blank' : undefined}
				rel={href.startsWith('http') ? 'noopener noreferrer' : undefined}
				{...motionProps}
			>
				<span className="pressable__face">{children}</span>
			</motion.a>
		);
	}

	return (
		<motion.button type={type} disabled={disabled} {...motionProps}>
			<span className="pressable__face">{children}</span>
		</motion.button>
	);
}

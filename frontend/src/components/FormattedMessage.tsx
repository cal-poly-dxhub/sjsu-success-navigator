import type { ReactNode } from 'react';
import './FormattedMessage.css';

/** Break inline numbered steps onto their own lines for readability. */
export function normalizeMessageLayout(text: string): string {
	return text
		.replace(/:\s+(\d+)\.\s+/g, ':\n\n$1. ')
		.replace(/([.!?])\s+(\d+)\.\s+/g, '$1\n\n$2. ');
}

/** Drop trailing partial markdown tokens while the typewriter is mid-token. */
export function trimPartialMarkdown(text: string): string {
	let safe = text;
	if ((safe.match(/\*\*/g) ?? []).length % 2 === 1) {
		safe = safe.replace(/\*\*([^*]*)$/, '$1');
	}
	const linkStart = safe.lastIndexOf('[');
	if (linkStart !== -1 && !/\]\([^)]*\)/.test(safe.slice(linkStart))) {
		safe = safe.slice(0, linkStart);
	}
	return safe;
}

function parseInline(text: string, keyPrefix: string): ReactNode[] {
	const parts: ReactNode[] = [];
	const pattern = /\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)]+)\)/g;
	let lastIndex = 0;
	let match: RegExpExecArray | null;
	let tokenIndex = 0;

	while ((match = pattern.exec(text)) !== null) {
		if (match.index > lastIndex) {
			parts.push(text.slice(lastIndex, match.index));
		}
		const key = `${keyPrefix}-${tokenIndex}`;
		if (match[1] !== undefined) {
			parts.push(<strong key={key}>{match[1]}</strong>);
		} else {
			parts.push(
				<a key={key} href={match[3]} target="_blank" rel="noopener noreferrer">
					{match[2]}
				</a>,
			);
		}
		lastIndex = match.index + match[0].length;
		tokenIndex += 1;
	}

	if (lastIndex < text.length) {
		parts.push(text.slice(lastIndex));
	}

	return parts.length ? parts : [text];
}

type FormattedMessageProps = {
	text: string;
	/** When true, hide incomplete markdown at the end (for typewriter). */
	trimPartial?: boolean;
};

export function FormattedMessage({ text, trimPartial = false }: FormattedMessageProps) {
	const source = trimPartial ? trimPartialMarkdown(text) : text;
	const normalized = normalizeMessageLayout(source);
	const blocks = normalized.split(/\n\n+/).filter(Boolean);

	if (!blocks.length) return null;

	return (
		<div className="formatted-message">
			{blocks.map((block, index) => {
				const step = block.match(/^(\d+)\.\s+([\s\S]+)/);
				if (step) {
					return (
						<p key={index} className="formatted-message__step">
							<span className="formatted-message__step-num">{step[1]}.</span>
							<span>{parseInline(step[2], `step-${index}`)}</span>
						</p>
					);
				}
				return (
					<p key={index} className="formatted-message__para">
						{parseInline(block, `para-${index}`)}
					</p>
				);
			})}
		</div>
	);
}

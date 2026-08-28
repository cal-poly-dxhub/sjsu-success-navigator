import { Fragment, useMemo } from 'react';
import type { MessageSpan } from '../lib/messageFormat';
import { parseMessage, revealMessage } from '../lib/messageFormat';
import './FormattedMessage.css';

/** Model-authored reply text on screen: the prose above and below the cards, and a card's
 * description. */

function Spans({ spans }: { spans: MessageSpan[] }) {
	return (
		<>
			{spans.map((span, index) => {
				// Both marks nest: dropping either renders text the model marked up as text it
				// did not.
				const text = span.italic ? <em>{span.text}</em> : span.text;
				return span.bold ? (
					<strong key={index}>{text}</strong>
				) : (
					<Fragment key={index}>{text}</Fragment>
				);
			})}
		</>
	);
}

type FormattedMessageProps = {
	text: string;
	/** Show only the first N rendered characters. */
	reveal?: number;
};

export function FormattedMessage({ text, reveal }: FormattedMessageProps) {
	const blocks = useMemo(() => parseMessage(text), [text]);
	const shown = reveal === undefined ? blocks : revealMessage(blocks, reveal);

	if (!shown.length) return null;

	return (
		<div className="formatted-message">
			{shown.map((block, index) => {
				if (block.kind === 'paragraph') {
					return (
						<p key={index} className="formatted-message__para">
							<Spans spans={block.spans} />
						</p>
					);
				}

				const items = block.items.map((item, itemIndex) => (
					<li key={itemIndex} className="formatted-message__item">
						<Spans spans={item} />
					</li>
				));

				// A real <ol>, so a wrapped step keeps its number and a reader announces a list
				// of four.
				return block.ordered ? (
					<ol
						key={index}
						start={block.start}
						className="formatted-message__list formatted-message__list--ordered"
					>
						{items}
					</ol>
				) : (
					<ul key={index} className="formatted-message__list">
						{items}
					</ul>
				);
			})}
		</div>
	);
}

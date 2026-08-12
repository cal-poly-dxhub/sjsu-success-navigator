import { Fragment, useMemo } from 'react';
import type { MessageSpan } from '../lib/messageFormat';
import { parseMessage, revealMessage } from '../lib/messageFormat';
import './FormattedMessage.css';

/**
 * Model-authored reply text on screen: the prose above and below the cards, and a card's
 * description. Bold, italics, bullets and numbered steps render; everything else is its own
 * characters (docs/cards-v2.md and lib/messageFormat.ts for what the parser knows).
 *
 * Every piece of model text below reaches the DOM as a React text node, so it is escaped
 * before it is anything else and a reply containing markup is read, not run. There is no
 * href and no src in this file, which is the display half of "the model never authors a
 * source" - it cannot write a link because there is nothing here that would render one.
 */

function Spans({ spans }: { spans: MessageSpan[] }) {
	return (
		<>
			{spans.map((span, index) => {
				// Both marks nest rather than picking one: `**bold *and* italic**` is a
				// phrase inside an emphasised phrase, and dropping either would render text
				// the model marked up as text it did not.
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
	/**
	 * Show only the first N RENDERED characters. The typewriter passes a rising count and
	 * gets formatted output at every step; left off, the whole message renders.
	 */
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

				// A real <ol>, so the numbers are the browser's: they stay in step when a
				// step wraps, and a screen reader announces a list of four rather than four
				// sentences that happen to open with a digit. `start` carries the model's
				// own first number for the same reason it is parsed at all.
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

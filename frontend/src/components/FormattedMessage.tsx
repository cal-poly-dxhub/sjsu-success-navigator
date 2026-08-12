import { Fragment, useMemo } from 'react';
import type { MessageSpan } from '../lib/messageFormat';
import { parseMessage, revealMessage } from '../lib/messageFormat';
import './FormattedMessage.css';

/**
 * Model-authored reply text on screen: the prose above and below the cards, and a card's
 * description. Bold and unordered bullets render; everything else is its own characters
 * (docs/cards-v2.md and lib/messageFormat.ts for why the parser is two constructs wide).
 *
 * Every piece of model text below reaches the DOM as a React text node, so it is escaped
 * before it is anything else and a reply containing markup is read, not run. There is no
 * href and no src in this file, which is the display half of "the model never authors a
 * source" - it cannot write a link because there is nothing here that would render one.
 */

function Spans({ spans }: { spans: MessageSpan[] }) {
	return (
		<>
			{spans.map((span, index) =>
				span.bold ? (
					<strong key={index}>{span.text}</strong>
				) : (
					<Fragment key={index}>{span.text}</Fragment>
				),
			)}
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
			{shown.map((block, index) =>
				block.kind === 'list' ? (
					<ul key={index} className="formatted-message__list">
						{block.items.map((item, itemIndex) => (
							<li key={itemIndex} className="formatted-message__item">
								<Spans spans={item} />
							</li>
						))}
					</ul>
				) : (
					<p key={index} className="formatted-message__para">
						<Spans spans={block.spans} />
					</p>
				),
			)}
		</div>
	);
}

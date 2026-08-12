/**
 * The two constructs model-authored reply text may use: **bold** and unordered bullet
 * lists. Nothing else - no headings, tables, links, images, blockquotes or ordered lists.
 *
 * WHY A HAND-WRITTEN PARSER AND NOT A MARKDOWN LIBRARY. A general parser renders
 * everything, so it arrives needing a sanitizer to take back the constructs this path must
 * not have - and the one that matters is links. The model is never shown a URL (it sees
 * integer ids and the server resolves them, docs/cards-v2.md), so a model-authored URL is
 * unrepresentable by construction, and the display path should stay unable to express one
 * rather than being taught to and then policed. Two constructs is a page of code; a parser
 * plus an allowlist is a dependency whose default is "render it" and a config file standing
 * between that default and this rule.
 *
 * WHY A BLOCK MODEL AND NOT AN HTML STRING. This produces data that FormattedMessage turns
 * into React elements, so every character of model text lands in a text node and is escaped
 * by React before it is anything else. There is no stage at which model text is HTML: a
 * reply containing <script> or <img onerror=...> is a paragraph that reads
 * "<script>". The rule is structural rather than a scrubbing step that could be skipped,
 * and no element this file can describe carries an href or a src.
 *
 * WHAT UNSUPPORTED SYNTAX DOES. It renders as its own characters. `1. step` is a paragraph
 * beginning "1.", `# heading` a paragraph beginning "#", an unclosed `**` a pair of
 * asterisks. A visible oddity beats content that silently disappears, so nothing here
 * drops input it does not understand.
 */

export type MessageSpan = {
	text: string;
	bold: boolean;
};

export type MessageBlock =
	| { kind: 'paragraph'; spans: MessageSpan[] }
	| { kind: 'list'; items: MessageSpan[][] };

/**
 * A bullet: up to three spaces, one of `-`, `*` or `+`, then a space and something to say.
 *
 * The trailing `(?=\S)` is what makes an empty marker literal rather than an empty bullet,
 * and it is also why `**bold** at the start of a line` is not a list: the marker has to be
 * followed by a space, and the second asterisk of `**` is not one.
 */
const BULLET = /^ {0,3}[-*+][ \t]+(?=\S)/;

/**
 * `**bold**`. The content may not open or close on whitespace, which is what keeps
 * `2 ** 3` and a stray `** ` as their own characters instead of opening emphasis that then
 * swallows the rest of the sentence looking for a closer.
 */
const BOLD = /\*\*(?=\S)([\s\S]*?\S)\*\*/g;

type RawBlock = { kind: 'paragraph'; lines: string[] } | { kind: 'list'; items: string[] };

/**
 * Lines into blocks. A blank line ends whatever is open; a bullet line opens or extends a
 * list; anything else is prose.
 *
 * A non-bullet line inside a list joins the item above it rather than ending the list,
 * because a wrapped bullet is far likelier than a paragraph deliberately tucked under one,
 * and the other reading breaks a list in half at its longest item.
 */
function toRawBlocks(text: string): RawBlock[] {
	const blocks: RawBlock[] = [];
	let paragraph: string[] | null = null;
	let list: string[] | null = null;

	const closeParagraph = () => {
		if (paragraph) blocks.push({ kind: 'paragraph', lines: paragraph });
		paragraph = null;
	};
	const closeList = () => {
		if (list) blocks.push({ kind: 'list', items: list });
		list = null;
	};

	for (const line of text.split('\n')) {
		if (!line.trim()) {
			closeParagraph();
			closeList();
			continue;
		}

		const marker = line.match(BULLET);
		if (marker) {
			closeParagraph();
			if (!list) list = [];
			list.push(line.slice(marker[0].length).trim());
			continue;
		}

		if (list) {
			list[list.length - 1] = `${list[list.length - 1]} ${line.trim()}`;
			continue;
		}

		if (!paragraph) paragraph = [];
		paragraph.push(line.trim());
	}

	closeParagraph();
	closeList();
	return blocks;
}

/** One line of text into bold and plain runs. */
function toSpans(text: string): MessageSpan[] {
	const spans: MessageSpan[] = [];
	const pattern = new RegExp(BOLD.source, 'g');
	let lastIndex = 0;
	let match: RegExpExecArray | null;

	while ((match = pattern.exec(text)) !== null) {
		if (match.index > lastIndex) {
			spans.push({ text: text.slice(lastIndex, match.index), bold: false });
		}
		spans.push({ text: match[1], bold: true });
		lastIndex = match.index + match[0].length;
	}

	if (lastIndex < text.length) {
		spans.push({ text: text.slice(lastIndex), bold: false });
	}

	return spans;
}

export function parseMessage(text: string): MessageBlock[] {
	return toRawBlocks(text).map((block): MessageBlock => {
		if (block.kind === 'list') {
			return { kind: 'list', items: block.items.map(toSpans) };
		}
		// Lines within one paragraph join with a space: a single newline is a wrap in the
		// source, not a break the student is meant to see.
		return { kind: 'paragraph', spans: toSpans(block.lines.join(' ')) };
	});
}

function spanLength(spans: MessageSpan[]): number {
	return spans.reduce((total, span) => total + span.text.length, 0);
}

/**
 * How many characters this text RENDERS as, which is not how long it is: the asterisks
 * around a bold run and a bullet's marker are markup, and the typewriter paces itself by
 * what appears on screen rather than by what the model wrote.
 */
export function renderedLength(text: string): number {
	return parseMessage(text).reduce((total, block) => {
		if (block.kind === 'list') {
			return total + block.items.reduce((sum, item) => sum + spanLength(item), 0);
		}
		return total + spanLength(block.spans);
	}, 0);
}

function takeSpans(spans: MessageSpan[], budget: number): MessageSpan[] {
	const taken: MessageSpan[] = [];
	let left = budget;

	for (const span of spans) {
		if (left <= 0) break;
		const text = span.text.slice(0, left);
		left -= text.length;
		taken.push({ text, bold: span.bold });
	}

	return taken;
}

/**
 * The first `chars` RENDERED characters, still as blocks.
 *
 * This is what the typewriter reveals, and it is why a live turn never shows its own
 * source: the text is parsed whole and then uncovered, so a half-typed bold run is already
 * bold and a bullet arrives with its marker rather than as an asterisk that later becomes
 * one. An item with no characters yet is left out entirely - an empty bullet a beat before
 * its text is a flicker, not a reveal.
 */
export function revealMessage(blocks: MessageBlock[], chars: number): MessageBlock[] {
	const revealed: MessageBlock[] = [];
	let left = chars;

	for (const block of blocks) {
		if (left <= 0) break;

		if (block.kind === 'paragraph') {
			const spans = takeSpans(block.spans, left);
			left -= spanLength(spans);
			if (spans.length) revealed.push({ kind: 'paragraph', spans });
			continue;
		}

		const items: MessageSpan[][] = [];
		for (const item of block.items) {
			if (left <= 0) break;
			const spans = takeSpans(item, left);
			left -= spanLength(spans);
			if (spans.length) items.push(spans);
		}
		if (items.length) revealed.push({ kind: 'list', items });
	}

	return revealed;
}

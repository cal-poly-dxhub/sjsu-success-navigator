/** The four constructs model-authored reply text may use: `**bold**`, `*italic*`, unordered
 * bullet lists and numbered lists. */

export type MessageSpan = {
	text: string;
	bold: boolean;
	italic: boolean;
};

export type MessageBlock =
	| { kind: 'paragraph'; spans: MessageSpan[] }
	/** `start` is the model's own: a list opening at "2." is a step the student is sent back to. */
	| { kind: 'list'; ordered: boolean; start: number; items: MessageSpan[][] };

/** A bullet: up to three spaces, one of `-`, `*` or `+`, then a space and something to say. */
const BULLET = /^ {0,3}[-*+][ \t]+(?=\S)/;

/** A numbered item: the same leading room, then digits, then `.` or `)`, then a space and
 * something to say. */
const ORDERED = /^ {0,3}(\d{1,9})[.)][ \t]+(?=\S)/;

/** Emphasis, longest marker first so `***both***` is read as one run rather than as bold with a
 * stray asterisk hanging off it. */
const EMPHASIS =
	/\*\*\*(?=\S)([\s\S]*?\S)\*\*\*|\*\*(?=\S)([\s\S]*?\S)\*\*|\*(?=[^\s*])([\s\S]*?[^\s*])\*(?!\*)/g;

type RawList = { kind: 'list'; ordered: boolean; start: number; items: string[] };
type RawBlock = { kind: 'paragraph'; lines: string[] } | RawList;

/** Lines into blocks. */
function toRawBlocks(text: string): RawBlock[] {
	const blocks: RawBlock[] = [];
	let paragraph: string[] | null = null;
	let list: RawList | null = null;

	const closeParagraph = () => {
		if (paragraph) blocks.push({ kind: 'paragraph', lines: paragraph });
		paragraph = null;
	};
	const closeList = () => {
		if (list) blocks.push(list);
		list = null;
	};

	for (const line of text.split('\n')) {
		if (!line.trim()) {
			closeParagraph();
			closeList();
			continue;
		}

		const bullet = line.match(BULLET);
		const numbered = bullet ? null : line.match(ORDERED);
		const marker = bullet ?? numbered;

		if (marker) {
			const ordered = numbered !== null;
			closeParagraph();
			if (list && list.ordered !== ordered) closeList();
			if (!list) {
				list = {
					kind: 'list',
					ordered,
					start: numbered ? Number(numbered[1]) : 1,
					items: [],
				};
			}
			list.items.push(line.slice(marker[0].length).trim());
			continue;
		}

		if (list) {
			const items = list.items;
			items[items.length - 1] = `${items[items.length - 1]} ${line.trim()}`;
			continue;
		}

		if (!paragraph) paragraph = [];
		paragraph.push(line.trim());
	}

	closeParagraph();
	closeList();
	return blocks;
}

/** One line of text into emphasised and plain runs. */
function toSpans(text: string, bold = false, italic = false): MessageSpan[] {
	const spans: MessageSpan[] = [];
	const pattern = new RegExp(EMPHASIS.source, 'g');
	let lastIndex = 0;
	let match: RegExpExecArray | null;

	while ((match = pattern.exec(text)) !== null) {
		if (match.index > lastIndex) {
			spans.push({ text: text.slice(lastIndex, match.index), bold, italic });
		}

		const [, both, strong, emphasised] = match;
		if (both !== undefined) spans.push(...toSpans(both, true, true));
		else if (strong !== undefined) spans.push(...toSpans(strong, true, italic));
		else spans.push(...toSpans(emphasised, bold, true));

		lastIndex = match.index + match[0].length;
	}

	if (lastIndex < text.length) {
		spans.push({ text: text.slice(lastIndex), bold, italic });
	}

	return spans;
}

export function parseMessage(text: string): MessageBlock[] {
	return toRawBlocks(text).map((block): MessageBlock => {
		if (block.kind === 'list') {
			return {
				kind: 'list',
				ordered: block.ordered,
				start: block.start,
				items: block.items.map((item) => toSpans(item)),
			};
		}
		// Lines within one paragraph join with a space: a single newline is a wrap in the
		// source, not a break the student is meant to see.
		return { kind: 'paragraph', spans: toSpans(block.lines.join(' ')) };
	});
}

function spanLength(spans: MessageSpan[]): number {
	return spans.reduce((total, span) => total + span.text.length, 0);
}

/** What this renders as, not how long it is: the typewriter paces itself by what appears on
 * screen rather than by the markup around it. */
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
		taken.push({ ...span, text });
	}

	return taken;
}

/** The first `chars` rendered characters, still as blocks. */
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
		if (items.length) {
			revealed.push({ kind: 'list', ordered: block.ordered, start: block.start, items });
		}
	}

	return revealed;
}

/**
 * The four constructs model-authored reply text may use: `**bold**`, `*italic*`, unordered
 * bullet lists and numbered lists. Nothing else - no headings, tables, links, images or
 * blockquotes.
 *
 * WHY A HAND-WRITTEN PARSER AND NOT A MARKDOWN LIBRARY. A general parser renders
 * everything, so it arrives needing a sanitizer to take back the constructs this path must
 * not have - and the one that matters is links. The model is never shown a URL (it sees
 * integer ids and the server resolves them, docs/cards-v2.md), so a model-authored URL is
 * unrepresentable by construction, and the display path should stay unable to express one
 * rather than being taught to and then policed. Four constructs is a page of code; a parser
 * plus an allowlist is a dependency whose default is "render it" and a config file standing
 * between that default and this rule. Growing this file is adding a construct at a time and
 * arguing for each; the ban on URL-bearing syntax is not one of the arguments available.
 *
 * WHY A BLOCK MODEL AND NOT AN HTML STRING. This produces data that FormattedMessage turns
 * into React elements, so every character of model text lands in a text node and is escaped
 * by React before it is anything else. There is no stage at which model text is HTML: a
 * reply containing <script> or <img onerror=...> is a paragraph that reads
 * "<script>". The rule is structural rather than a scrubbing step that could be skipped,
 * and no element this file can describe carries an href or a src.
 *
 * WHAT UNSUPPORTED SYNTAX DOES. It renders as its own characters. `# heading` is a
 * paragraph beginning "#", `[label](url)` is a paragraph with brackets in it, an unclosed
 * `**` a pair of asterisks. A visible oddity beats content that silently disappears, so
 * nothing here drops input it does not understand.
 */

export type MessageSpan = {
	text: string;
	bold: boolean;
	italic: boolean;
};

export type MessageBlock =
	| { kind: 'paragraph'; spans: MessageSpan[] }
	/**
	 * `ordered` picks the element and `start` the first number, which is the model's own:
	 * a list that opens at "2." is a step the student is being sent back to, not a list to
	 * silently renumber from one.
	 */
	| { kind: 'list'; ordered: boolean; start: number; items: MessageSpan[][] };

/**
 * A bullet: up to three spaces, one of `-`, `*` or `+`, then a space and something to say.
 *
 * The trailing `(?=\S)` is what makes an empty marker literal rather than an empty bullet,
 * and it is also why `**bold** at the start of a line` is not a list: the marker has to be
 * followed by a space, and the second asterisk of `**` is not one.
 */
const BULLET = /^ {0,3}[-*+][ \t]+(?=\S)/;

/**
 * A numbered item: the same leading room, then digits, then `.` or `)`, then a space and
 * something to say. The number is captured because the first one starts the list.
 *
 * Nine digits at most, so a line that opens with a long figure is prose rather than a list
 * item numbered eight billion. Same trailing `(?=\S)` as the bullet: `1.` alone on a line
 * is the characters the model typed.
 */
const ORDERED = /^ {0,3}(\d{1,9})[.)][ \t]+(?=\S)/;

/**
 * Emphasis, longest marker first so `***both***` is read as one run rather than as bold
 * with a stray asterisk hanging off it.
 *
 * Content may not open or close on whitespace, which is what keeps `2 ** 3` and a stray
 * `** ` as their own characters instead of opening emphasis that then swallows the rest of
 * the sentence looking for a closer. The single-asterisk form additionally refuses to open
 * or close on an asterisk, so an unmatched `**` cannot be read as italics around a run that
 * begins with the leftover one, and its closer skips a `**` so `*italic **bold** here*`
 * closes at the end rather than in the middle.
 *
 * `_underscores_` are deliberately NOT italics. The prose carries emails and occasional
 * identifiers, and there the underscores are the text: making them markup would italicise
 * half an address and delete the marks around it. An asterisk has no such second job.
 */
const EMPHASIS =
	/\*\*\*(?=\S)([\s\S]*?\S)\*\*\*|\*\*(?=\S)([\s\S]*?\S)\*\*|\*(?=[^\s*])([\s\S]*?[^\s*])\*(?!\*)/g;

type RawList = { kind: 'list'; ordered: boolean; start: number; items: string[] };
type RawBlock = { kind: 'paragraph'; lines: string[] } | RawList;

/**
 * Lines into blocks. A blank line ends whatever is open; a marker line opens or extends a
 * list; anything else is prose.
 *
 * A marker of the other kind ends the list and opens a new one, so a bulleted line under
 * three numbered steps is its own block rather than a fourth step drawn with a bullet.
 *
 * A markerless line inside a list joins the item above it rather than ending the list,
 * because a wrapped item is far likelier than a paragraph deliberately tucked under one,
 * and the other reading breaks a list in half at its longest item.
 */
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

/**
 * One line of text into emphasised and plain runs.
 *
 * Recursive on the content of a run, which is how `**bold *and* italic**` arrives as one
 * bold span and one bold-italic span: the inherited flags ride down, the inner marker adds
 * its own. Content is always shorter than the match that produced it - the markers are
 * gone - so the recursion has nowhere to run away to.
 */
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
		taken.push({ ...span, text });
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
 *
 * A numbered list keeps its `start` while it types. Items only ever fall off the END, so
 * the number beside the first visible one is the number it will still have when the last
 * has landed.
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
		if (items.length) {
			revealed.push({ kind: 'list', ordered: block.ordered, start: block.start, items });
		}
	}

	return revealed;
}

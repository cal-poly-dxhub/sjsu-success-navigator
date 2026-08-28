/** The mailto link, and the budget that decides whether there is one. */

import type { EmailDraft } from '../types/chat';

/** The hard ceiling on the encoded URL. */
export const MAILTO_MAX_CHARS = 2000;

/** What a draft is meant to fit inside. */
export const MAILTO_TARGET_CHARS = 1500;

export type MailtoDraft = {
	/** The mailto URL, or null when the encoded length is past the ceiling. */
	href: string | null;
	/** The encoded length, whether or not it fit. What the budget is measured on. */
	encodedLength: number;
};

/** The body, encoded with the line breaks a mail client is required to honour. */
function encodeBody(body: string): string {
	return encodeURIComponent(body.replace(/\r\n|\r|\n/g, '\r\n'));
}

/** One draft as a mailto URL, with the budget already applied. */
export function mailtoDraft(draft: EmailDraft): MailtoDraft {
	const href =
		`mailto:${draft.to}` +
		`?subject=${encodeURIComponent(draft.subject)}` +
		`&body=${encodeBody(draft.body)}`;

	if (href.length > MAILTO_MAX_CHARS) {
		// Neither thrown nor silent: the copy path still works, and the log line is how a dead
		// button gets noticed before a student reports it.
		console.warn(
			`Escalation draft is ${href.length} encoded characters, past the ${MAILTO_MAX_CHARS} ` +
				'mailto ceiling. Offering the copy path only. Lower escalation.max_chars in config.yaml.',
		);
		return { href: null, encodedLength: href.length };
	}

	if (href.length > MAILTO_TARGET_CHARS) {
		console.warn(
			`Escalation draft is ${href.length} encoded characters, past the ${MAILTO_TARGET_CHARS} ` +
				`aim and inside the ${MAILTO_MAX_CHARS} ceiling. It still opens; the margin for ` +
				'non-ASCII characters is gone.',
		);
	}

	return { href, encodedLength: href.length };
}

/** Record that the student asked for their mail client. */
export function logEscalationIntent(detail: { encodedLength: number }): void {
	console.info('escalation:intent:mail-client-opened', {
		encodedLength: detail.encodedLength,
	});
}

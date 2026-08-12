/**
 * The mailto link, and the budget that decides whether there is one.
 *
 * WHAT THIS IS. The server assembled a finished message (app/escalation.py) and the student
 * has read it on screen. This turns those three strings into the URL that opens their own
 * mail client with the draft already in it. Nothing here sends anything, and nothing here
 * edits the draft: the bytes below are the bytes the server stored.
 *
 * THE BUDGET IS MEASURED ON THE PERCENT-ENCODED URL, which is the only length that matters
 * and is not the length of the text. Outlook on Windows fails SILENTLY past roughly 2,000
 * characters - no error, no mail window, nothing at all happens when the button is pressed -
 * and the encoding is where a draft gets long: every newline costs three characters (%0A),
 * every space three in a query value (%20), and one emoji or a smart quote can cost nine.
 * So a 1,300-character message can be a 2,100-character URL.
 *
 * OVER BUDGET THERE IS NO LINK AT ALL. Not a truncated one - a truncated draft is a message
 * the student can send without noticing what went missing - and not a button that does
 * nothing, which is the exact failure Outlook already produces. The component falls back to
 * the copy path, which has no length limit and works in every mail client.
 *
 * The aim is 1,500 and the ceiling is 2,000. The gap is deliberate slack for the encoding,
 * and it is why the server's own cap on the model's prose (config.yaml escalation.max_chars)
 * sits at 1,200 rather than at the ceiling: the two lines the server adds, the subject, and
 * the encoding all have to fit under this number with room to spare.
 */

import type { EmailDraft } from '../types/chat';

/**
 * The hard ceiling on the encoded URL. Past this, Outlook on Windows opens nothing and says
 * nothing, so a link is worse than no link: the student presses a button and concludes the
 * app is broken rather than reaching for the copy they can paste anywhere.
 */
export const MAILTO_MAX_CHARS = 2000;

/**
 * What a draft is meant to fit inside. Not a gate - a link between here and the ceiling
 * still opens - but a draft in that band is one encoding accident away from having no
 * button at all, and the fix is a lower `escalation.max_chars`, so it is worth saying out
 * loud rather than discovering when a student hits the ceiling.
 */
export const MAILTO_TARGET_CHARS = 1500;

export type MailtoDraft = {
	/** The mailto URL, or null when the encoded length is past the ceiling. */
	href: string | null;
	/** The encoded length, whether or not it fit. What the budget is measured on. */
	encodedLength: number;
};

/**
 * One draft as a mailto URL, with the budget already applied.
 *
 * The recipient is NOT percent-encoded: it is a plain address validated at synth
 * (infra/config.py, resolve_escalation) to be one mailbox with no whitespace, and mail
 * clients read `mailto:name%40host` less reliably than the address as written. Everything
 * that came from the model or from config text IS encoded, because a subject with an `&` in
 * it would otherwise start a new field.
 */
export function mailtoDraft(draft: EmailDraft): MailtoDraft {
	const href =
		`mailto:${draft.to}` +
		`?subject=${encodeURIComponent(draft.subject)}` +
		`&body=${encodeURIComponent(draft.body)}`;

	if (href.length > MAILTO_MAX_CHARS) {
		// Not a thrown error and not a silent null: this is a real state a real draft can
		// reach, the copy path still works, and the console line is how it gets noticed
		// before a student reports a button that does nothing.
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

/**
 * Record that the student ASKED FOR THEIR MAIL CLIENT. Never that a message was sent.
 *
 * THE NAME IS THE POINT. Nothing in this system can observe delivery: the mail client opens
 * outside the page, the student edits the draft, and they send it - or close the window - in
 * an application we cannot see. An event called anything like "sent" or "delivered" would be
 * a number somebody later reports to a sponsor as messages received, and it would be wrong
 * in the direction that matters.
 *
 * The console is the sink because this app has no analytics pipeline. When one arrives, this
 * is the one function that changes, and the same rule applies to whatever it posts to: the
 * event is an intent.
 */
export function logEscalationIntent(detail: { encodedLength: number }): void {
	console.info('escalation:intent:mail-client-opened', {
		encodedLength: detail.encodedLength,
	});
}

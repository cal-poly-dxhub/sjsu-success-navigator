import { useCallback, useEffect, useRef, useState } from 'react';
import type { EmailDraft } from '../types/chat';
import { PressableButton } from './PressableButton';
import { useStrings } from '../lib/i18n';
import { logEscalationIntent, mailtoDraft } from '../lib/mailtoDraft';
import './EscalationDraft.css';

/**
 * The escalate-to-human draft, presented as the email it is.
 *
 * AN EMAIL, NOT A CARD, and that shape is the honest one. A card is a destination we are
 * sending the student to; this is a message that will go out over their name, from their
 * address, and it is the only thing in this app whose output leaves the app. So it is laid
 * out the way their mail client will lay it out - a To line, a subject line, a body - and
 * every part of it is selectable text rather than a summary of itself.
 *
 * WHAT IS ON SCREEN IS WHAT GETS SENT. The three strings rendered here are the three strings
 * handed to the mailto and the three the server stored with the turn. Nothing is generated,
 * shortened or prettified on this side; a preview that differed from the message would be
 * worse than no preview, because the student is being asked to vouch for it.
 *
 * TWO WAYS OUT, AND ONE OF THEM ALWAYS WORKS. The button opens their mail client with the
 * draft in it. The copy action puts the same text on the clipboard for anyone whose mail
 * lives in a tab, or on a machine with no mail client registered, or whose draft is past the
 * mailto ceiling - past it, the button is not rendered at all rather than rendered dead
 * (see lib/mailtoDraft.ts: Outlook on Windows fails silently, so a link that cannot work
 * looks exactly like an app that is broken).
 *
 * NOBODY HERE SENDS ANYTHING. There is no request in this component. The student presses
 * send in their own mail client, having read and edited the message, which is the whole
 * reason this design needs no verified sending identity and why a reply comes back to them.
 *
 * TWO LANGUAGES ON ONE PANEL, AND THE SPLIT IS THE CONTRACT (lib/i18n.ts). The chrome - the
 * heading, the note, the two field labels, the buttons, the two fallback hints - is page
 * chrome and comes from the catalogues, so it follows the student's chosen language. The
 * three values do NOT: the address, the subject and the body are the draft the server
 * assembled and stored with the turn, and they are shown exactly as stored. Translating
 * those here would put a message on screen that differs from the one the mailto carries,
 * which is the one thing this component promises never to do.
 */

type EscalationDraftProps = {
	draft: EmailDraft;
};

/** How long the copy button stays in its "Copied" state before saying its name again. */
const COPIED_FEEDBACK_MS = 2200;

export function EscalationDraft({ draft }: EscalationDraftProps) {
	const t = useStrings();
	const [copied, setCopied] = useState(false);
	const [copyFailed, setCopyFailed] = useState(false);
	const bodyRef = useRef<HTMLPreElement>(null);
	const resetTimer = useRef<number | null>(null);

	// Recomputed per render rather than memoised: it is a string concatenation over a draft
	// that never changes, and a stale memo here would be a link to an older message.
	const { href, encodedLength } = mailtoDraft(draft);

	useEffect(() => {
		return () => {
			if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
		};
	}, []);

	const handleCopy = useCallback(() => {
		// The same two labels the panel shows above, so a copy from the button reads as the
		// copy from the page - and the three values between them stay the server's bytes.
		const text = `${t.escalationTo}: ${draft.to}\n${t.escalationSubject}: ${draft.subject}\n\n${draft.body}`;

		const settle = (ok: boolean) => {
			setCopied(ok);
			setCopyFailed(!ok);
			if (!ok) {
				// The text is on screen and selectable, so a clipboard the browser will not
				// give us is a smaller failure than it looks: select it for them and say so.
				const body = bodyRef.current;
				if (body) {
					const range = document.createRange();
					range.selectNodeContents(body);
					const selection = window.getSelection();
					selection?.removeAllRanges();
					selection?.addRange(range);
				}
				return;
			}
			if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
			resetTimer.current = window.setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS);
		};

		// `navigator.clipboard` is undefined on an insecure origin, so this is a feature
		// check rather than a try/catch around a call that may not exist.
		if (!navigator.clipboard?.writeText) {
			settle(false);
			return;
		}
		navigator.clipboard.writeText(text).then(
			() => settle(true),
			() => settle(false),
		);
	}, [draft, t.escalationTo, t.escalationSubject]);

	return (
		<section className="escalation-draft" aria-label={t.escalationAria}>
			<header className="escalation-draft__head">
				<h2 className="escalation-draft__headline">{t.escalationHeadline}</h2>
				<p className="escalation-draft__note">{t.escalationNote}</p>
			</header>

			<dl className="escalation-draft__fields">
				<div className="escalation-draft__field">
					<dt className="escalation-draft__label">{t.escalationTo}</dt>
					{/* The address as the server addressed it, selectable: somebody whose mail
					    lives in a tab needs to be able to take it from here. */}
					<dd className="escalation-draft__value">{draft.to}</dd>
				</div>
				<div className="escalation-draft__field">
					<dt className="escalation-draft__label">{t.escalationSubject}</dt>
					<dd className="escalation-draft__value">{draft.subject}</dd>
				</div>
			</dl>

			{/* A <pre>, so the paragraph breaks the draft was written with survive exactly as
			    they will arrive, and so a copy from the page matches a copy from the button.
			    Not a textarea: this is not editable HERE - it is editable in the mail client,
			    where the edit is the student's own and lands in the message they send. */}
			<pre className="escalation-draft__body" ref={bodyRef}>
				{draft.body}
			</pre>

			<div className="escalation-draft__actions">
				{href ? (
					<PressableButton
						variant="secondary"
						href={href}
						className="escalation-draft__open"
						onClick={() => logEscalationIntent({ encodedLength })}
					>
						{t.escalationOpen}
					</PressableButton>
				) : null}

				<PressableButton
					variant="ghost"
					className="escalation-draft__copy"
					onClick={handleCopy}
				>
					{copied ? t.escalationCopied : t.escalationCopy}
				</PressableButton>
			</div>

			{/* Both of these are states a real draft reaches, so both say what to do next
			    rather than what went wrong. `aria-live` because the first one appears in
			    response to a press and the reader is looking at the button, not at this. */}
			<p className="escalation-draft__hint" aria-live="polite">
				{copyFailed ? t.escalationClipboardBlocked : null}
				{!href && !copyFailed ? t.escalationTooLong : null}
			</p>
		</section>
	);
}

import { useCallback, useEffect, useRef, useState } from 'react';
import type { EmailDraft } from '../types/chat';
import { PressableButton } from './PressableButton';
import { useStrings } from '../lib/i18n';
import { logEscalationIntent, mailtoDraft } from '../lib/mailtoDraft';
import './EscalationDraft.css';

/** The escalate-to-human draft, presented as the email it is. */

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
		// copy from the page, and the three values between them stay the server's bytes.
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

		// `navigator.clipboard` is undefined on an insecure origin, so this is a feature check
		// rather than a try/catch around a call that may not exist.
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
					 * lives in a tab needs to be able to take it from here. */}
					<dd className="escalation-draft__value">{draft.to}</dd>
				</div>
				<div className="escalation-draft__field">
					<dt className="escalation-draft__label">{t.escalationSubject}</dt>
					<dd className="escalation-draft__value">{draft.subject}</dd>
				</div>
			</dl>

			{/* A <pre>, so the paragraph breaks the draft was written with survive exactly as
			 * they will arrive, and so a copy from the page matches a copy from the button. */}
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

			{/* Both of these are states a real draft reaches, so both say what to do next rather
			 * than what went wrong. */}
			<p className="escalation-draft__hint" aria-live="polite">
				{copyFailed ? t.escalationClipboardBlocked : null}
				{!href && !copyFailed ? t.escalationTooLong : null}
			</p>
		</section>
	);
}

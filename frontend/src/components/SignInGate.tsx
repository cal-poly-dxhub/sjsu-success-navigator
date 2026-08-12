import { useEffect, useState } from 'react';
import {
	AuthError,
	beginSignIn,
	completeSignInFromRedirect,
	hasPendingRedirect,
	isSignedIn,
} from '../lib/auth';
import ChatApp from './ChatApp';
import { strings, useStrings } from '../lib/i18n';
import { PressableButton } from './PressableButton';
import './SignInGate.css';

/**
 * The sign-in gate: a button that leaves for Cognito managed login, and the other half of
 * the round trip when the browser comes back with a code.
 *
 * There is no username or password field here, and that is the point rather than a
 * simplification. SJSU's identity provider is federated into this pool later as a
 * config-only change, and a federated user cannot authenticate through InitiateAuth at
 * all - only the hosted endpoints can. A form built now would be thrown away then, and
 * every student's sign-in would move under them. What they see today is what they will
 * see after Okta lands; only the buttons on Cognito's own page change.
 *
 * This is NOT the security boundary and does not pretend to be: API Gateway's JWT
 * authorizer rejects an unauthenticated POST /chat regardless of what the browser renders.
 * What it does is get a real token before the first request, so a student sees a sign-in
 * page rather than an unexplained failure.
 *
 * ONE PAGE STILL. The callback is the root route - `?code=` is handled here on mount - so
 * the redirect flow adds no /login or /auth/callback page and no CloudFront routing case.
 */
export default function SignInGate() {
	const t = useStrings();
	const [signedIn, setSignedIn] = useState(() => isSignedIn());
	const [error, setError] = useState<string | null>(null);
	// Starts true on a return trip so the sign-in button never flashes over a sign-in that
	// is already half-finished.
	const [busy, setBusy] = useState(() => hasPendingRedirect());

	useEffect(() => {
		if (!hasPendingRedirect()) return;
		let cancelled = false;
		completeSignInFromRedirect()
			.then((completed) => {
				if (!cancelled && completed) setSignedIn(true);
			})
			.catch((err: unknown) => {
				if (cancelled) return;
				setError(
					err instanceof AuthError || err instanceof Error
						? err.message
						: strings().signInNotCompleted,
				);
			})
			.finally(() => {
				if (!cancelled) setBusy(false);
			});
		return () => {
			cancelled = true;
		};
	}, []);

	if (signedIn) {
		return <ChatApp />;
	}

	const handleSignIn = () => {
		setBusy(true);
		setError(null);
		// Navigates away on success, so `busy` is only ever cleared by a failure to get
		// that far - a config.json that will not load, say.
		beginSignIn().catch((err: unknown) => {
			setError(
				err instanceof AuthError || err instanceof Error
					? err.message
					: t.signInNotStarted,
			);
			setBusy(false);
		});
	};

	return (
		<main className="sign-in">
			<div className="sign-in__card">
				<h1 className="sign-in__title">{t.appName}</h1>
				<p className="sign-in__subtitle">{t.signInSubtitle}</p>

				{error ? (
					<p className="sign-in__error" role="alert">
						{error}
					</p>
				) : null}

				<PressableButton
					className="sign-in__submit"
					type="button"
					onClick={handleSignIn}
					disabled={busy}
				>
					{busy ? t.signingIn : t.signIn}
				</PressableButton>
			</div>
		</main>
	);
}

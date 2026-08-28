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

/** The sign-in gate: a button that leaves for Cognito managed login, and the other half of the
 * round trip when the browser comes back with a code. */
export default function SignInGate() {
	const t = useStrings();
	const [signedIn, setSignedIn] = useState(() => isSignedIn());
	const [error, setError] = useState<string | null>(null);
	// Starts true on a return trip so the sign-in button never flashes over a sign-in that is
	// already half-finished.
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
		// Navigates away on success, so `busy` is only ever cleared by a failure to get that
		// far, a config.json that will not load, say.
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

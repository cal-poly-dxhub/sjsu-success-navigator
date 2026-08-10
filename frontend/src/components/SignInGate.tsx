import { useState, type FormEvent } from 'react';
import { AuthError, isSignedIn, signIn } from '../lib/auth';
import ChatApp from './ChatApp';
import { PressableButton } from './PressableButton';
import './SignInGate.css';

/**
 * The sign-in gate, replacing camp's ProtectedChatApp.
 *
 * Camp's version read sessionStorage and redirected to /login if it was empty - a
 * cosmetic check, trivially bypassed, in front of a backend that had no auth at all.
 * This one is not the security boundary either, and does not pretend to be: API Gateway's
 * JWT authorizer rejects an unauthenticated POST /chat regardless of what the browser
 * renders. What this does is get a real token before the first request, so the student
 * sees a sign-in form rather than an unexplained failure.
 *
 * One page, no redirect. The Hosted UI flow camp used needed /login and /auth/callback to
 * bounce through Google; a single InitiateAuth call needs neither.
 */
export default function SignInGate() {
	const [signedIn, setSignedIn] = useState(() => isSignedIn());
	const [username, setUsername] = useState('');
	const [password, setPassword] = useState('');
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	if (signedIn) {
		return <ChatApp />;
	}

	const handleSubmit = async (event: FormEvent) => {
		event.preventDefault();
		setBusy(true);
		setError(null);
		try {
			await signIn(username.trim(), password);
			setSignedIn(true);
		} catch (err: unknown) {
			setError(
				err instanceof AuthError || err instanceof Error
					? err.message
					: 'Sign-in failed.',
			);
		} finally {
			setBusy(false);
		}
	};

	return (
		<main className="sign-in">
			<form className="sign-in__card" onSubmit={handleSubmit}>
				<h1 className="sign-in__title">Student Success Navigator</h1>
				<p className="sign-in__subtitle">Sign in to continue.</p>

				<label className="sign-in__label" htmlFor="sign-in-username">
					Username
				</label>
				<input
					id="sign-in-username"
					className="sign-in__input"
					value={username}
					onChange={(event) => setUsername(event.target.value)}
					autoComplete="username"
					required
				/>

				<label className="sign-in__label" htmlFor="sign-in-password">
					Password
				</label>
				<input
					id="sign-in-password"
					className="sign-in__input"
					type="password"
					value={password}
					onChange={(event) => setPassword(event.target.value)}
					autoComplete="current-password"
					required
				/>

				{error ? (
					<p className="sign-in__error" role="alert">
						{error}
					</p>
				) : null}

				<PressableButton className="sign-in__submit" type="submit" disabled={busy}>
					{busy ? 'Signing in…' : 'Sign in'}
				</PressableButton>
			</form>
		</main>
	);
}

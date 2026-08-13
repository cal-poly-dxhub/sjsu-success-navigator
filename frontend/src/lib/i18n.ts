import { useSyncExternalStore } from 'react';
import { en, type Strings } from './strings/en';
import { es } from './strings/es';
import { vi } from './strings/vi';
import { zhHans } from './strings/zhHans';
import { hi } from './strings/hi';
import { pa } from './strings/pa';
import { te } from './strings/te';
import { tl } from './strings/tl';
import { ko } from './strings/ko';
import { ja } from './strings/ja';
import { fr } from './strings/fr';
import { ptBR } from './strings/ptBR';
import { ru } from './strings/ru';
import { zhHant } from './strings/zhHant';
import { th } from './strings/th';

/**
 * The frontend's own language: which one is chosen, where that choice lives, and how a
 * component gets the strings for it. The strings themselves are one file per language under
 * lib/strings/, which is the unit SJSU can hand to one reviewer.
 *
 * WHAT THIS IS AND IS NOT. It translates PAGE CHROME - buttons, labels, headings, empty
 * states, the frontend's own error sentences, and the greeting a new chat opens with. It
 * does not touch the model's replies or the cards, and it is NOT how those became
 * multilingual: the system prompt tells the model to answer in the language of the student's
 * own message, cards included (app/prompts.py). Nothing here is sent anywhere - the chat
 * request is unchanged, and the server still cannot tell which language the sidebar is in.
 *
 * SO THE TWO CAN DISAGREE, and that is the honest behaviour rather than a defect to paper
 * over. A student can read the sidebar in Thai and type in English, and they will get an
 * English answer under Thai chrome, because the picker is a display preference for this
 * browser and the message is evidence about the person typing it. Wiring the picker into the
 * request would make the sidebar overrule what somebody actually wrote.
 *
 * A PLAIN OBJECT PER LANGUAGE, NOT A LIBRARY. Fifteen languages of UI copy do not need i18next,
 * ICU message parsing or a runtime dependency to keep pinned; they need files a speaker can
 * read end to end. Interpolation is a function per string, which TypeScript checks for arity
 * in a way a `{{name}}` placeholder never would, and every translation is typed as `Strings`,
 * so a missing key fails the typecheck rather than rendering `undefined` at a student.
 */

export type Language =
	| 'en'
	| 'es'
	| 'vi'
	| 'zh-Hans'
	| 'hi'
	| 'pa'
	| 'te'
	| 'tl'
	| 'ko'
	| 'ja'
	| 'fr'
	| 'pt-BR'
	| 'ru'
	| 'zh-Hant'
	| 'th';

export type { Strings };

export type LanguageOption = {
	code: Language;
	/** Endonym: the language's name in itself, so it is legible to the person choosing it. */
	label: string;
	/**
	 * Has a speaker of this language read the strings? False ships anyway - a machine-
	 * translated sidebar serves a student better than an English one they cannot read - but
	 * it is recorded rather than assumed, the settings panel says so to the student, and
	 * every file carries the same warning at the top for whoever reviews it.
	 */
	reviewed: boolean;
};

/**
 * The languages offered, and the order they are offered in.
 *
 * THIS LIST IS SJSU'S TO EDIT and adding to it is one line here plus one file in strings/.
 *
 * IT IS IN TWO PARTS, chosen on different grounds, so the order says so rather than blending
 * them. The FIRST TEN are the population the product serves: the sponsor's two - English and
 * Spanish were named directly, Hindi with them - plus the languages most spoken by SJSU
 * students and by the families around the campus. Nobody here is qualified to rank those, so
 * their order is the population rather than anything about the software. The LAST FIVE were
 * added for BREADTH, for a sponsor watching this picker open in a demo: French, Brazilian
 * Portuguese, Russian, Traditional Chinese and Thai each bring a script or a region the first
 * ten did not, and the list read as regionally narrow with only the ten in it. They sit last
 * because that is what they are; a campus-population argument for any of them would move it
 * up the list rather than be answered by reshuffling.
 *
 * NO RIGHT-TO-LEFT LANGUAGE IS IN THIS LIST, and that is still deliberate rather than an
 * oversight about who is on campus - it is also why a breadth pass reached for Thai and not
 * for the languages a breadth pass reaches for first. Arabic, Farsi and Urdu need `dir="rtl"`
 * and a layout that mirrors with it - the sidebar, the dock, the card grid, every
 * `margin-left` in the stylesheets - which is a layout job, not a catalogue entry. A
 * catalogue for one of them is the cheap half of that work and would render a broken page
 * rather than a translated one, which serves a student worse than being absent does.
 */
export const LANGUAGES: LanguageOption[] = [
	{ code: 'en', label: 'English', reviewed: true },
	{ code: 'es', label: 'Español', reviewed: false },
	{ code: 'vi', label: 'Tiếng Việt', reviewed: false },
	{ code: 'zh-Hans', label: '简体中文', reviewed: false },
	{ code: 'hi', label: 'हिन्दी', reviewed: false },
	{ code: 'pa', label: 'ਪੰਜਾਬੀ', reviewed: false },
	{ code: 'te', label: 'తెలుగు', reviewed: false },
	{ code: 'tl', label: 'Tagalog', reviewed: false },
	{ code: 'ko', label: '한국어', reviewed: false },
	{ code: 'ja', label: '日本語', reviewed: false },
	{ code: 'fr', label: 'Français', reviewed: false },
	// The endonym carries the region because the catalogue does: this is Brazilian usage,
	// not Portuguese with a flag on it, and a student from Lisbon should be able to see
	// that before choosing it (strings/ptBR.ts).
	{ code: 'pt-BR', label: 'Português (Brasil)', reviewed: false },
	{ code: 'ru', label: 'Русский', reviewed: false },
	{ code: 'zh-Hant', label: '繁體中文', reviewed: false },
	{ code: 'th', label: 'ไทย', reviewed: false },
];

const CATALOGUE: Record<Language, Strings> = {
	en,
	es,
	vi,
	'zh-Hans': zhHans,
	hi,
	pa,
	te,
	tl,
	ko,
	ja,
	fr,
	'pt-BR': ptBR,
	ru,
	'zh-Hant': zhHant,
	th,
};

/**
 * WHERE THE CHOICE LIVES: localStorage, under this key, and nowhere else. It is a display
 * preference belonging to this browser - it is never sent with a chat request, never stored
 * against the account, and a student on a shared campus machine who signs out leaves it
 * behind with no way to tell whose it was.
 */
const STORAGE_KEY = 'ssn.language';

const DEFAULT_LANGUAGE: Language = 'en';

function isLanguage(value: unknown): value is Language {
	return LANGUAGES.some((option) => option.code === value);
}

function readStoredLanguage(): Language {
	// Every localStorage call is guarded: Safari throws on read in some private modes, and a
	// language preference is not worth a blank page.
	try {
		const stored = window.localStorage.getItem(STORAGE_KEY);
		return isLanguage(stored) ? stored : DEFAULT_LANGUAGE;
	} catch {
		return DEFAULT_LANGUAGE;
	}
}

/**
 * A module-level store rather than a React context, deliberately. The language is one value
 * for the whole document with no tree structure to it, and a context would mean a provider
 * wrapping the island plus a prop drilled through SignInGate to reach a component that only
 * wants a noun. `useSyncExternalStore` gives every component the same subscription with no
 * plumbing at all, and works the same in the sign-in gate as it does five levels down.
 */
let current: Language = typeof window === 'undefined' ? DEFAULT_LANGUAGE : readStoredLanguage();
const listeners = new Set<() => void>();

/**
 * `<html lang>`, kept in step with the choice. Not decoration: it is what a screen reader
 * reads the page's pronunciation rules from, and what a browser's own translate offer keys
 * on. A Spanish page still claiming lang="en" is read aloud in an English accent.
 */
function stampDocumentLanguage(language: Language) {
	if (typeof document === 'undefined') return;
	document.documentElement.lang = language;
}

stampDocumentLanguage(current);

export function currentLanguage(): Language {
	return current;
}

export function setLanguage(next: Language) {
	if (next === current) return;
	current = next;
	try {
		window.localStorage.setItem(STORAGE_KEY, next);
	} catch {
		/* The choice still applies to this page; it just will not survive the reload. */
	}
	stampDocumentLanguage(next);
	for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
	listeners.add(listener);
	return () => {
		listeners.delete(listener);
	};
}

/** The chosen language, and the setter, for the one control that changes it. */
export function useLanguage(): [Language, (next: Language) => void] {
	const language = useSyncExternalStore(
		subscribe,
		() => current,
		() => DEFAULT_LANGUAGE,
	);
	return [language, setLanguage];
}

/** Every string, in the chosen language. The one import a component that renders text needs. */
export function useStrings(): Strings {
	const [language] = useLanguage();
	return CATALOGUE[language];
}

/**
 * The same strings, read outside a render.
 *
 * For the places that FORMAT A SENTENCE AND KEEP IT - a failed fetch whose message goes into
 * state, and the welcome turn at the moment a chat stops being new - where the alternative is
 * capturing the hook's value in an effect that runs once on mount and would then be stale for
 * the rest of the page's life. Reading `current` at the moment it happens is the honest
 * version of what those call sites want.
 */
export function strings(): Strings {
	return CATALOGUE[current];
}

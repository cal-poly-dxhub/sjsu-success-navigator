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

/** The frontend's own language: which one is chosen, where that choice lives, and how a
 * component gets the strings for it. */

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
	/** Has a speaker of this language read the strings? */
	reviewed: boolean;
};

/** The languages offered, and the order they are offered in. */
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
	// The endonym carries the region because the catalogue does: this is Brazilian usage.
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

/** Where the choice lives: localStorage, under this key, and nowhere else. */
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

/** A module-level store rather than a React context, deliberately. */
let current: Language = typeof window === 'undefined' ? DEFAULT_LANGUAGE : readStoredLanguage();
const listeners = new Set<() => void>();

/** `<html lang>`, kept in step with the choice. */
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

/** The same strings, read outside a render. */
export function strings(): Strings {
	return CATALOGUE[current];
}

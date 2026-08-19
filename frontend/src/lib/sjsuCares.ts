import { CAMPUS_CONTACTS, SJSU_CARES_ADDRESS } from './generated/campusData';

/** The four kinds of help SJSU Cares publishes, and the only thing routed on. */
export type SjsuCaresTheme = 'food' | 'housing' | 'financial' | 'parenting';

// NOT ONE FACT IS TYPED IN THIS FILE. Every constant below is read out of the repo-root data/
// directory at build time (frontend/scripts/generate-campus-data.mjs writes ./generated/
// campusData.ts from data/places.csv and data/contacts.csv, before astro starts). The values
// used to be transcribed here from the SJSU Cares contact page - and the same two facts were
// transcribed AGAIN into app/places.py and config.yaml, with nothing comparing the copies. The
// office could move and this panel and the map card would disagree inside one app, with every
// test still green. Now there is one row per fact and both languages read it.
//
// WHAT IS A FACT AND WHAT IS COPY, unchanged: the facts are the number to ring, the address to
// walk to, the URL to open, and they read the same in every language. The sentences ABOUT them
// (the overview, the note, the hours, and each service's name and description) are copy, so
// they live in lib/i18n.ts and switch with the rest of the interface.
//
// Anything SJSU already publishes as a list (staff directory, full service catalogue) is still
// linked rather than reproduced - a row we do not keep is a row that cannot go stale.

// The trailing full stop is PRESENTATION, and it is the one thing added here: the modal prints
// this line as a sentence in a definition list, and data/places.csv holds the address the
// location card prints under a heading, where a full stop would be wrong.
export const SJSU_CARES_LOCATION = `${SJSU_CARES_ADDRESS}.`;

export const SJSU_CARES_PHONE = CAMPUS_CONTACTS['sjsu-cares-phone'].detail;

// The `escalation` row, not a second cares row: this is the mailbox an escalation draft is
// addressed to as well, and one mailbox is one row (data/README.md, contacts.csv).
export const SJSU_CARES_EMAIL = CAMPUS_CONTACTS['sjsu-cares'].detail;

export const SJSU_CARES_REQUEST_FORM = CAMPUS_CONTACTS['sjsu-cares-request-form'].href;

export const SJSU_CARES_CONTACT_PAGE = CAMPUS_CONTACTS['sjsu-cares-contact-page'].href;

// SJSU's own index of every assistance category, kept current by SJSU rather than by us.
export const SJSU_CARES_SERVICES_INDEX = CAMPUS_CONTACTS['sjsu-cares-services-index'].href;

/** SJSU's page for each theme. English pages, because that is what SJSU publishes. */
export const SJSU_CARES_SERVICE_HREFS: Record<SjsuCaresTheme, string> = {
	food: CAMPUS_CONTACTS['sjsu-cares-food'].href,
	housing: CAMPUS_CONTACTS['sjsu-cares-housing'].href,
	financial: CAMPUS_CONTACTS['sjsu-cares-financial'].href,
	parenting: CAMPUS_CONTACTS['sjsu-cares-parenting'].href,
};

/**
 * The words that route a question to a theme, and they are ENGLISH ONLY today.
 *
 * That is a real limit and not a hidden one: a student who asks about food in Spanish gets
 * the modal with no recommendation rather than a wrong one, which is the same thing that
 * happens for any question this list does not recognise. Translating the model's side of the
 * conversation is the job that makes a Spanish keyword list worth having.
 */
const SERVICE_KEYWORDS: Record<SjsuCaresTheme, string[]> = {
	food: [
		'food',
		'hungry',
		'grocery',
		'groceries',
		'meal',
		'meals',
		'pantry',
		'calfresh',
		'eat',
	],
	housing: [
		'housing',
		'homeless',
		'rent',
		'evict',
		'eviction',
		'sleep',
		'couch',
		'apartment',
		'roommate',
		'shelter',
	],
	financial: [
		'money',
		'financial',
		'bill',
		'bills',
		'tuition',
		'expense',
		'expenses',
		'grant',
		'debt',
		'pay',
		'paid',
		'cost',
	],
	parenting: [
		'parent',
		'parenting',
		'child',
		'children',
		'kid',
		'kids',
		'baby',
		'pregnant',
		'pregnancy',
		'caretaking',
	],
};

export function inferSjsuCaresServiceTheme(query?: string | null): SjsuCaresTheme | null {
	if (!query) return null;
	const normalized = query.toLowerCase();
	for (const [theme, keywords] of Object.entries(SERVICE_KEYWORDS) as Array<
		[SjsuCaresTheme, string[]]
	>) {
		if (keywords.some((keyword) => normalized.includes(keyword))) {
			return theme;
		}
	}
	return null;
}

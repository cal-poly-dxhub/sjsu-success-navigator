import { CAMPUS_CONTACTS, SJSU_CARES_ADDRESS } from './generated/campusData';

/** The four kinds of help SJSU Cares publishes, and the only thing routed on. */
export type SjsuCaresTheme = 'food' | 'housing' | 'financial' | 'parenting';

// Not one fact is typed in this file.
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

/** The words that route a question to a theme, and they are english only today. */
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

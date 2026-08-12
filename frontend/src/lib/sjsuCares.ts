/** The four kinds of help SJSU Cares publishes, and the only thing routed on. */
export type SjsuCaresTheme = 'food' | 'housing' | 'financial' | 'parenting';

// Every contact detail below is transcribed from the SJSU Cares contact page, verified 2026-08-06.
// Deliberately short: each hardcoded fact is one that can go stale, so anything SJSU already
// publishes as a list (staff directory, full service catalogue) is linked, not reproduced here.
//
// WHAT IS A FACT AND WHAT IS COPY. This file holds the facts - the number to ring, the address
// to walk to, the URL to open - and they read the same in every language. The sentences ABOUT
// them (the overview, the note, the hours, and each service's name and description) are copy,
// so they live in lib/i18n.ts and switch with the rest of the interface. The building name is
// a fact and stays here: nobody is looking for a translated sign.

export const SJSU_CARES_LOCATION =
	'Diaz Compean Student Union West, entrance across from the Engineering Building.';

export const SJSU_CARES_PHONE = '408.924.1234';

export const SJSU_CARES_EMAIL = 'sjsucares@sjsu.edu';

export const SJSU_CARES_REQUEST_FORM =
	'https://cm.maxient.com/reportingform.php?SanJoseStateUniv&layout_id=12';

export const SJSU_CARES_CONTACT_PAGE =
	'https://www.sjsu.edu/sjsucares/about/contact-us.php';

// SJSU's own index of every assistance category, kept current by SJSU rather than by us.
export const SJSU_CARES_SERVICES_INDEX =
	'https://www.sjsu.edu/sjsucares/get-assistance/index.php';

/** SJSU's page for each theme. English pages, because that is what SJSU publishes. */
export const SJSU_CARES_SERVICE_HREFS: Record<SjsuCaresTheme, string> = {
	food: 'https://www.sjsu.edu/sjsucares/get-assistance/food-assistance/index.php',
	housing: 'https://www.sjsu.edu/sjsucares/get-assistance/housing-assistance/index.php',
	financial: 'https://www.sjsu.edu/sjsucares/get-assistance/financial-assistance/index.php',
	parenting: 'https://www.sjsu.edu/sjsucares/resources/parenting-students/index.php',
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

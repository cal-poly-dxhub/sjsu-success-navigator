export type SjsuCaresService = {
	title: string;
	kicker: string;
	description: string;
	href: string;
	theme: 'food' | 'housing' | 'financial' | 'parenting';
};

// Every contact detail below is transcribed from the SJSU Cares contact page, verified 2026-08-06.
// Deliberately short: each hardcoded fact is one that can go stale, so anything SJSU already
// publishes as a list (staff directory, full service catalogue) is linked, not reproduced here.
export const SJSU_CARES_OVERVIEW =
	'SJSU Cares helps students facing basic-needs challenges with case management, referrals, and follow-up.';

export const SJSU_CARES_NOTE = 'Include your student ID when you reach out.';

export const SJSU_CARES_LOCATION =
	'Diaz Compean Student Union West, entrance across from the Engineering Building.';

export const SJSU_CARES_PHONE = '408.924.1234';

export const SJSU_CARES_EMAIL = 'sjsucares@sjsu.edu';

export const SJSU_CARES_HOURS = 'Monday - Friday, 10 am - 4 pm';

export const SJSU_CARES_REQUEST_FORM =
	'https://cm.maxient.com/reportingform.php?SanJoseStateUniv&layout_id=12';

export const SJSU_CARES_CONTACT_PAGE =
	'https://www.sjsu.edu/sjsucares/about/contact-us.php';

// SJSU's own index of every assistance category, kept current by SJSU rather than by us.
export const SJSU_CARES_SERVICES_INDEX =
	'https://www.sjsu.edu/sjsucares/get-assistance/index.php';

export const SJSU_CARES_SERVICES: SjsuCaresService[] = [
	{
		title: 'Food assistance',
		kicker: 'If food is the main stressor',
		description: 'Spartan Food Pantry access and CalFresh application help.',
		href: 'https://www.sjsu.edu/sjsucares/get-assistance/food-assistance/index.php',
		theme: 'food',
	},
	{
		title: 'Housing assistance',
		kicker: 'If you need a safer place to stay',
		description: 'Emergency housing, rehousing programs, and housing search support.',
		href: 'https://www.sjsu.edu/sjsucares/get-assistance/housing-assistance/index.php',
		theme: 'housing',
	},
	{
		title: 'Financial assistance',
		kicker: 'If unexpected costs are piling up',
		description: 'Emergency grants and financial coaching for unexpected expenses.',
		href: 'https://www.sjsu.edu/sjsucares/get-assistance/financial-assistance/index.php',
		theme: 'financial',
	},
	{
		title: 'Parenting students',
		kicker: 'If you are balancing school and caregiving',
		description: 'Registration support, rights guidance, and campus accommodations.',
		href: 'https://www.sjsu.edu/sjsucares/resources/parenting-students/index.php',
		theme: 'parenting',
	},
];

const SERVICE_KEYWORDS: Record<SjsuCaresService['theme'], string[]> = {
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

export function inferSjsuCaresServiceTheme(
	query?: string | null,
): SjsuCaresService['theme'] | null {
	if (!query) return null;
	const normalized = query.toLowerCase();
	for (const [theme, keywords] of Object.entries(SERVICE_KEYWORDS) as Array<
		[SjsuCaresService['theme'], string[]]
	>) {
		if (keywords.some((keyword) => normalized.includes(keyword))) {
			return theme;
		}
	}
	return null;
}

export function findSjsuCaresService(
	theme?: SjsuCaresService['theme'] | null,
): SjsuCaresService | null {
	if (!theme) return null;
	return SJSU_CARES_SERVICES.find((service) => service.theme === theme) ?? null;
}

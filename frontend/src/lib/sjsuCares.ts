export type SjsuCaresAction = {
	label: string;
	href: string;
	variant?: 'primary' | 'secondary' | 'ghost';
};

export type SjsuCaresService = {
	title: string;
	kicker: string;
	description: string;
	href: string;
	theme: 'food' | 'housing' | 'financial' | 'parenting';
};

export type SjsuCaresContact = {
	name: string;
	role: string;
	focus: string;
	email: string;
	phone?: string;
	appointmentHref?: string;
	serviceTheme?: SjsuCaresService['theme'];
};

export type SjsuCaresStep = {
	title: string;
	description: string;
};

export type CampusContactMethod = {
	label: string;
	href: string;
};

export type CampusContactOffice = {
	office: string;
	summary: string;
	sourceLabel: string;
	sourceUrl: string;
	theme: 'academic' | 'basic-needs' | 'wellness' | 'safety';
	methods: CampusContactMethod[];
};

export const SJSU_CARES_OVERVIEW =
	'SJSU Cares supports students with basic-needs challenges through personalized case management, referrals, and ongoing care.';

export const SJSU_CARES_NOTE = 'Always include your student ID when reaching out.';

// Every value below is transcribed from the SJSU Cares contact page, verified 2026-08-06.
export const SJSU_CARES_LOCATION =
	'Diaz Compean Student Union West, entrance across from the Engineering Building.';

export const SJSU_CARES_PHONE = '408.924.1234';

export const SJSU_CARES_EMAIL = 'sjsucares@sjsu.edu';

export const SJSU_CARES_HOURS = 'Monday - Friday, 10 am - 4 pm';

export const SJSU_CARES_CONTACT_PAGE =
	'https://www.sjsu.edu/sjsucares/about/contact-us.php';

export const SJSU_CARES_ACTIONS: SjsuCaresAction[] = [
	{
		label: 'Request assistance',
		href: 'https://cm.maxient.com/reportingform.php?SanJoseStateUniv&layout_id=12',
		variant: 'primary',
	},
	{
		label: 'Call SJSU Cares',
		href: 'tel:4089241234',
		variant: 'secondary',
	},
	{
		label: 'Email SJSU Cares',
		href: `mailto:${SJSU_CARES_EMAIL}`,
		variant: 'ghost',
	},
	{
		label: 'Open contact page',
		href: SJSU_CARES_CONTACT_PAGE,
		variant: 'ghost',
	},
	{
		label: 'Open campus map',
		href: 'https://map.sjsu.edu/?id=2189#!ce/86354?ct/86185,86186,86187,86189,86190,86191,86271,86354?m/999683?s/',
		variant: 'ghost',
	},
];

export const SJSU_CARES_SERVICES: SjsuCaresService[] = [
	{
		title: 'Food assistance',
		kicker: 'If food is the main stressor',
		description:
			'Food pantry support, produce and groceries, plus CalFresh application help to stretch your monthly budget.',
		href: 'https://www.sjsu.edu/sjsucares/get-assistance/food-assistance/index.php',
		theme: 'food',
	},
	{
		title: 'Housing assistance',
		kicker: 'If you need a safer place to stay',
		description:
			'One-on-one support for housing instability, emergency housing options, and long-term housing planning.',
		href: 'https://www.sjsu.edu/sjsucares/get-assistance/housing-assistance/index.php',
		theme: 'housing',
	},
	{
		title: 'Financial assistance',
		kicker: 'If unexpected costs are piling up',
		description:
			'Emergency financial support, grants, and coaching that helps students navigate unexpected expenses.',
		href: 'https://www.sjsu.edu/sjsucares/get-assistance/financial-assistance/index.php',
		theme: 'financial',
	},
	{
		title: 'Parenting students',
		kicker: 'If you are balancing school and caregiving',
		description:
			'Support for parenting students, including accommodations, rights guidance, and campus resources.',
		href: 'https://www.sjsu.edu/sjsucares/resources/parenting-students/index.php',
		theme: 'parenting',
	},
];

export const SJSU_CARES_STEPS: SjsuCaresStep[] = [
	{
		title: 'Share what is going on',
		description:
			'Start with the request form, phone call, or email so the team can understand what kind of support you need.',
	},
	{
		title: 'Get matched to the right help',
		description:
			'SJSU Cares helps connect you to the right programs, offices, and next steps based on your situation.',
	},
	{
		title: 'Build a plan forward',
		description:
			'You can get ongoing guidance, referrals, and practical support instead of being bounced from office to office.',
	},
];

export const SJSU_CARES_CONTACTS: SjsuCaresContact[] = [
	{
		name: 'Kednel Jean',
		role: 'Director of Case Management',
		focus: 'General case management leadership and complex student support needs',
		email: 'kednel.jean@sjsu.edu',
		phone: '408.924.1153',
	},
	{
		name: 'Nancy Taylor',
		role: 'SJSU Cares Case Manager',
		focus: 'General student support and case management follow-up',
		email: 'nancy.taylor@sjsu.edu',
		phone: '408.924.6097',
	},
	{
		name: 'Scheanelle J. Green',
		role: 'SJSU Cares Case Manager',
		focus: 'General student support and one-on-one case guidance',
		email: 'scheanelle.green@sjsu.edu',
		phone: '408.924.2847',
	},
	{
		name: 'Brenj Cuneta',
		role: 'Senior Basic Needs & Community Engagement Coordinator',
		focus: 'Basic needs programs and the Spartan Food Pantry',
		email: 'brenjielyn.cuneta@sjsu.edu',
		phone: '408.924.4208',
	},
	{
		name: 'Gisselle Muñoz',
		role: 'Basic Needs Coordinator - Housing',
		focus: 'Housing instability, emergency housing options, and housing navigation',
		email: 'gisselle.munoz@sjsu.edu',
		phone: '408.924.4205',
		appointmentHref: 'https://scheduler.zoom.us/gisselle-munoz/gisselle-munoz',
		serviceTheme: 'housing',
	},
	{
		name: 'Sonia Lizama-Orduña',
		role: 'Basic Needs Coordinator - Benefits',
		focus: 'CalFresh and public benefits support',
		email: 'sonia.lizama-orduna@sjsu.edu',
		phone: '408.924.5565',
		appointmentHref: 'https://scheduler.zoom.us/sonia-lizama-orduna/calfresh',
		serviceTheme: 'food',
	},
];

export const OFFICIAL_CAMPUS_CONTACTS: CampusContactOffice[] = [
	{
		office: 'Peer Connections',
		summary: 'General tutoring, mentoring, appointments, and front-door office support.',
		sourceLabel: 'Peer Connections appointment page',
		sourceUrl: 'https://www.sjsu.edu/peerconnections/about/appointment.php',
		theme: 'academic',
		methods: [
			{ label: 'Call 408.924.2587', href: 'tel:4089242587' },
			{ label: 'Email peerconnections@sjsu.edu', href: 'mailto:peerconnections@sjsu.edu' },
		],
	},
	{
		office: 'Peer Connections professional staff',
		summary: 'Official staff-directory contacts published on the professional staff page.',
		sourceLabel: 'Peer Connections professional staff',
		sourceUrl: 'https://www.sjsu.edu/peerconnections/programs/meet/prostaff.php',
		theme: 'academic',
		methods: [
			{ label: 'Genevieve Lau', href: 'mailto:genevieve.lau@sjsu.edu' },
			{ label: 'Greg Garcia', href: 'mailto:greg.garcia@sjsu.edu' },
			{ label: 'Liliana Fuerte', href: 'mailto:liliana.fuertecalderon@sjsu.edu' },
			{ label: 'Andrea Smith-Landucci', href: 'mailto:andrea.smith@sjsu.edu' },
		],
	},
	{
		office: 'Learning Assistant Program',
		summary: 'Program-specific support contact from the Learning Assistant Program page.',
		sourceLabel: 'Learning Assistant Program',
		sourceUrl: 'https://www.sjsu.edu/peerconnections/programs/learning-assistance-program.php',
		theme: 'academic',
		methods: [{ label: 'Email ashley.defensor@sjsu.edu', href: 'mailto:ashley.defensor@sjsu.edu' }],
	},
	{
		office: 'CalFresh support',
		summary: 'Questions about the SJSU CalFresh support form and benefits help.',
		sourceLabel: 'CalFresh food page',
		sourceUrl: 'https://www.sjsu.edu/sjsucares/get-assistance/food-assistance/calfresh.php',
		theme: 'basic-needs',
		methods: [{ label: 'Email calfresh@sjsu.edu', href: 'mailto:calfresh@sjsu.edu' }],
	},
	{
		office: 'Spartan Food Pantry',
		summary: 'Food pantry donations, volunteering, and pantry support contacts.',
		sourceLabel: 'Pantry donation and volunteer pages',
		sourceUrl: 'https://www.sjsu.edu/sjsucares/get-involved/pantry-donation-guide.php',
		theme: 'basic-needs',
		methods: [
			{ label: 'Email spartanfoodpantry@sjsu.edu', href: 'mailto:spartanfoodpantry@sjsu.edu' },
			// 408.924.1234 is the SJSU Cares main line, which the pantry pages reuse. It is not a
			// pantry-specific number and must not be labelled as one.
			{ label: `Call SJSU Cares ${SJSU_CARES_PHONE}`, href: 'tel:4089241234' },
		],
	},
	{
		office: 'CAPS same-day support',
		summary: 'Counseling and Psychological Services same-day and urgent support line.',
		sourceLabel: 'Counseling & Psychological Services',
		sourceUrl: 'https://www.sjsu.edu/wellness/access-services/counseling/index.php',
		theme: 'wellness',
		methods: [{ label: 'Call 408.924.5678', href: 'tel:4089245678' }],
	},
	{
		office: 'Survivor Advocacy Services',
		summary: 'Confidential support for survivors, with appointment by phone or email.',
		sourceLabel: 'Survivor Advocacy Services page',
		sourceUrl: 'https://www.sjsu.edu/wellness/access-services/survivor-advocacy-services.php',
		theme: 'wellness',
		methods: [
			{ label: 'Call 408.924.7300', href: 'tel:4089247300' },
			{ label: 'Email survivoradvocate@sjsu.edu', href: 'mailto:survivoradvocate@sjsu.edu' },
		],
	},
	{
		office: 'Emergency and crisis support',
		summary: 'Official urgent support options listed on the SJSU emergency and crisis page.',
		sourceLabel: 'Emergency & crisis page',
		sourceUrl: 'https://www.sjsu.edu/wellness/access-services/emergency-crisis.php',
		theme: 'safety',
		methods: [
			{ label: 'Call 408.924.5678', href: 'tel:4089245678' },
			{ label: 'Call or text 988', href: 'https://988lifeline.org/' },
		],
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

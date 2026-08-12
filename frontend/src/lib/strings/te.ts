import type { Strings } from './en';

/**
 * Telugu. MACHINE-AUTHORED, NOT REVIEWED BY A TELUGU SPEAKER. See es.ts for why a file in
 * this state ships; it is marked `reviewed: false` in lib/i18n.ts and said plainly to the
 * student in the settings panel.
 *
 * REVIEWER'S NOTES. "మీరు" throughout - the polite form a campus service would use with a
 * student. Proper nouns are left in Latin script on purpose: SJSU Cares, Spartan Food Pantry,
 * CalFresh, Sammy and the product name are what the student will see on signs and on SJSU's
 * own pages.
 */
export const te: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: 'కొనసాగించడానికి మీ SJSU ఖాతాతో సైన్ ఇన్ చేయండి.',
	signIn: 'సైన్ ఇన్',
	signingIn: 'సైన్ ఇన్ అవుతోంది…',
	signInNotCompleted: 'సైన్ ఇన్ పూర్తి కాలేదు.',
	signInNotStarted: 'సైన్ ఇన్ ప్రారంభం కాలేదు.',

	welcome:
		'నమస్కారం! నేను Sammy. SJSU క్యాంపస్ వనరుల గురించి నన్ను ఏదైనా అడగండి: ట్యూటరింగ్, అడ్వైజింగ్, ఆరోగ్యం, వసతి సహాయం, ఇంకా చాలా.',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Sammy, SJSU Spartans శుభంకరం',
	expandSidebar: 'సైడ్‌బార్‌ను విస్తరించండి',
	collapseSidebar: 'సైడ్‌బార్‌ను కుదించండి',

	newChat: 'కొత్త చాట్',
	chatHistory: 'చాట్ చరిత్ర',
	recentChats: 'ఇటీవలి చాట్‌లు',
	renameChat: (title: string) => `${title} పేరు మార్చండి`,
	deleteChat: (title: string) => `${title} తొలగించండి`,
	deleteConfirm: (title: string) => `“${title}” తొలగించాలా? దీన్ని తిరిగి తేలేరు.`,
	save: 'సేవ్ చేయండి',
	saving: 'సేవ్ అవుతోంది…',
	cancel: 'రద్దు చేయండి',
	delete: 'తొలగించండి',
	deleting: 'తొలగిస్తోంది…',
	opening: 'తెరుస్తోంది…',
	loadingChats: 'మీ చాట్‌లు లోడ్ అవుతున్నాయి…',
	noStoredChats: 'మీరు పంపే చాట్‌లు ఇక్కడ సేవ్ అవుతాయి, మీ ఖాతాలోనే ఉంటాయి.',
	renameFailed: 'ఆ చాట్ పేరు మార్చలేకపోయాం.',
	deleteFailed: 'ఆ చాట్‌ను తొలగించలేకపోయాం.',
	signedIn: 'సైన్ ఇన్ అయ్యారు',
	signOut: 'సైన్ అవుట్',
	settings: 'సెట్టింగ్‌లు',
	closeNavigation: 'నావిగేషన్ మూసివేయండి',
	openChatHistory: 'చాట్ చరిత్రను తెరవండి',

	askSammy: 'Sammy ని అడగండి',
	composerPlaceholder: 'ట్యూటరింగ్, అడ్వైజింగ్, ఆరోగ్యం గురించి అడగండి…',
	send: 'పంపండి',
	yourMessage: 'మీ సందేశం',
	thinking: 'ఆలోచిస్తోంది',
	waitingForSammy: 'Sammy సమాధానం కోసం వేచి ఉంది',
	stageRetrieving: 'క్యాంపస్ వనరులలో వెతుకుతోంది',

	chatsLoadFailedWith: (message: string) => `మీ చాట్‌లను లోడ్ చేయలేకపోయాం: ${message}`,
	chatsLoadFailed: 'మీ చాట్‌లను లోడ్ చేయలేకపోయాం.',
	chatOpenFailedWith: (message: string) => `ఆ చాట్‌ను తెరవలేకపోయాం: ${message}`,
	chatOpenFailed: 'ఆ చాట్‌ను తెరవలేకపోయాం.',
	turnFailed: 'Sammy ని చేరుకోవడంలో ఏదో తప్పు జరిగింది. చాట్ API నడుస్తోందా?',

	talkToPerson: 'ఒక వ్యక్తితో మాట్లాడండి',
	talkToPersonAria: 'SJSU Cares లోని ఒక వ్యక్తితో మాట్లాడండి',
	university: 'San José State University',
	caresClose: 'SJSU Cares సమాచారాన్ని మూసివేయండి',
	caresOverview:
		'ప్రాథమిక అవసరాల విషయంలో ఇబ్బంది పడుతున్న విద్యార్థులకు SJSU Cares కేస్ మేనేజ్‌మెంట్, సిఫారసులు, తదుపరి సహాయంతో అండగా నిలుస్తుంది.',
	caresRequest: 'సహాయం కోరండి',
	caresRequestHint: 'కేస్ మేనేజర్‌ను చేరుకోవడానికి అత్యంత వేగవంతమైన మార్గం',
	caresCall: (phone: string) => `${phone} కి కాల్ చేయండి`,
	caresEmail: (email: string) => `${email} కి ఇమెయిల్ చేయండి`,
	caresHoursLabel: 'సమయం',
	caresHoursValue: 'సోమవారం - శుక్రవారం, ఉదయం 10 - సాయంత్రం 4',
	caresOfficeLabel: 'కార్యాలయం',
	caresRecommended: 'మీ ప్రశ్నకు సిఫారసు',
	caresAllServices: 'SJSU Cares అన్ని సేవలు',
	caresDirectory: 'సిబ్బంది డైరెక్టరీ, పూర్తి సంప్రదింపు జాబితా',
	caresNote: 'సంప్రదించేటప్పుడు మీ విద్యార్థి ఐడీని కూడా తెలియజేయండి.',
	caresServices: {
		food: {
			title: 'ఆహార సహాయం',
			description: 'Spartan Food Pantry అందుబాటు, CalFresh దరఖాస్తులో సహాయం.',
		},
		housing: {
			title: 'వసతి సహాయం',
			description: 'అత్యవసర వసతి, పునరావాస కార్యక్రమాలు, ఇల్లు వెతకడంలో సహాయం.',
		},
		financial: {
			title: 'ఆర్థిక సహాయం',
			description: 'ఊహించని ఖర్చులకు అత్యవసర గ్రాంట్లు, ఆర్థిక సలహా.',
		},
		parenting: {
			title: 'పిల్లలున్న విద్యార్థులు',
			description: 'నమోదులో సహాయం, హక్కులపై మార్గదర్శనం, క్యాంపస్‌లో సదుపాయాలు.',
		},
	},

	escalationAria: 'ఒక వ్యక్తికి పంపడానికి ఇమెయిల్ ముసాయిదా',
	escalationHeadline: 'దీన్ని ఒక వ్యక్తికి పంపండి',
	escalationNote:
		'ఇది మీ సొంత ఇమెయిల్ యాప్‌లో తెరుచుకుంటుంది, కాబట్టి జవాబు నేరుగా మీకే వస్తుంది. ఇది మీ కళాశాల చిరునామా నుండి పంపబడుతోందో లేదో సరిచూసుకోండి.',
	escalationTo: 'ఎవరికి',
	escalationSubject: 'విషయం',
	escalationOpen: 'నా ఇమెయిల్ యాప్‌లో తెరవండి',
	escalationCopied: 'కాపీ అయింది',
	escalationCopy: 'సందేశాన్ని కాపీ చేయండి',
	escalationClipboardBlocked:
		'మీ బ్రౌజర్ క్లిప్‌బోర్డ్‌ను వాడనివ్వలేదు, అందుకే సందేశం ఎంపిక చేయబడింది: దాన్ని కాపీ చేసి కొత్త ఇమెయిల్‌లో పేస్ట్ చేయండి.',
	escalationTooLong:
		'ఈ ముసాయిదా చాలా పొడవుగా ఉంది, మీ ఇమెయిల్ యాప్ దానంతట అదే తెరుచుకోలేదు. దాన్ని కాపీ చేసి కొత్త ఇమెయిల్‌లో పేస్ట్ చేయండి.',

	settingsClose: 'సెట్టింగ్‌లను మూసివేయండి',
	close: 'మూసివేయండి',
	languageLabel: 'భాష',
	languageHint:
		'ఇది ఈ యాప్ లేబుళ్లను, బటన్లను మారుస్తుంది. Sammy సమాధానాలు ఇంకా అనువదించబడవు.',
	languageUnreviewed: 'యంత్ర అనువాదం. SJSU ఇంకా ఈ పదజాలాన్ని సమీక్షించలేదు.',

	costSection: 'దీన్ని నడపడానికి అయ్యే ఖర్చు',
	costThisConversation: 'ఈ సంభాషణ',
	costMessagesSoFar: (messages: string) =>
		`ఇప్పటివరకు ${messages} సందేశాలు, నిజంగా వాడిన టోకెన్ల ఆధారంగా.`,
	costNothingMetered:
		'ఈ చాట్‌లో ఇంకా ఏదీ లెక్కించలేదు. మీరు ఇక్కడ పంపే మొదటి సందేశం నుంచి లెక్క మొదలవుతుంది.',
	costMessagesSent: 'పంపిన సందేశాలు',
	costModelCalls: 'మోడల్ కాల్‌లు',
	costInputTokens: 'ఇన్‌పుట్ టోకెన్లు',
	costOutputTokens: 'అవుట్‌పుట్ టోకెన్లు',
	costPerMessage: 'ఒక్కో సందేశానికి ఖర్చు',
	costMonthOfUse: 'ఒక నెల వినియోగం',
	costMessagesAMonth: 'నెలకు విద్యార్థుల సందేశాలు',
	costMonthAtVolume: 'ఆ స్థాయిలో ఒక నెల',
	costRunsAtNoUse: 'వాడకం లేకున్నా',
	costNobodyAsking: 'ప్రతి నెలా, ఎవరూ అడగకపోయినా',
	costWhatOneAdds: 'ఒక సందేశం ఎంత కలుపుతుంది',
	costFootLead: 'ఇవి అంచనాలు, బిల్లు కాదు.',
	costFootRest: 'AWS ప్రచురించిన ధరలు, కొలిచిన టోకెన్ వినియోగంతో గుణించబడ్డాయి.',
};

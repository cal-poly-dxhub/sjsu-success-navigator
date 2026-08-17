import type { Strings } from './en';

/**
 * Tagalog. MACHINE-AUTHORED, NOT REVIEWED BY A TAGALOG SPEAKER. See es.ts for why a file in
 * this state ships; it is marked `reviewed: false` in lib/i18n.ts and said plainly to the
 * student in the settings panel.
 *
 * REVIEWER'S NOTES. Conversational Tagalog with the English loanwords a Bay Area student
 * actually uses ("chat", "email", "tutoring") rather than coined equivalents nobody says.
 * Proper nouns are left in English on purpose: SJSU Cares, Spartan Food Pantry, CalFresh,
 * Sammy and the product name are what the student will see on signs and on SJSU's own pages.
 */
export const tl: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: 'Mag-sign in gamit ang iyong SJSU account para magpatuloy.',
	signIn: 'Mag-sign in',
	signingIn: 'Nagsa-sign in…',
	signInNotCompleted: 'Hindi natapos ang pag-sign in.',
	signInNotStarted: 'Hindi nasimulan ang pag-sign in.',

	welcome:
		'Kumusta! Ako si Sammy. Itanong mo sa akin ang kahit ano tungkol sa mga campus resource ng SJSU: tutoring, advising, wellness, tulong sa tirahan, at marami pang iba.',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Si Sammy, ang mascot ng SJSU Spartans',
	expandSidebar: 'Palawakin ang sidebar',
	collapseSidebar: 'Itiklop ang sidebar',

	newChat: 'Bagong chat',
	chatHistory: 'Kasaysayan ng chat',
	recentChats: 'Mga kamakailang chat',
	renameChat: (title: string) => `Palitan ang pangalan ng ${title}`,
	deleteChat: (title: string) => `Burahin ang ${title}`,
	deleteConfirm: (title: string) => `Burahin ang “${title}”? Hindi na ito maibabalik.`,
	save: 'I-save',
	saving: 'Sine-save…',
	cancel: 'Kanselahin',
	delete: 'Burahin',
	deleting: 'Binubura…',
	opening: 'Binubuksan…',
	loadingChats: 'Nilo-load ang iyong mga chat…',
	noStoredChats: 'Ang mga chat na ipinapadala mo ay naka-save dito at nananatili sa iyong account.',
	renameFailed: 'Hindi napalitan ang pangalan ng chat na iyon.',
	deleteFailed: 'Hindi nabura ang chat na iyon.',
	signedIn: 'Naka-sign in',
	signOut: 'Mag-sign out',
	settings: 'Mga setting',
	closeNavigation: 'Isara ang navigation',
	openChatHistory: 'Buksan ang kasaysayan ng chat',

	askSammy: 'Tanungin si Sammy',
	composerPlaceholder: 'Magtanong tungkol sa tutoring, advising, wellness…',
	send: 'Ipadala',
	yourMessage: 'Ang iyong mensahe',
	thinking: 'Nag-iisip',
	waitingForSammy: 'Naghihintay ng sagot ni Sammy',
	stageRetrieving: 'Naghahanap sa mga campus resource',
	stageComposingCards: 'Tinitipon ang mga campus resource',

	campusResources: 'Mga campus resource',
	campusResourcesFrom: (timestamp: string) => `Mga campus resource mula ${timestamp}`,
	timeJustNow: 'Ngayon lang',
	timeMinutesAgo: (minutes: number) => `${minutes} min ang nakalipas`,
	timeHoursAgo: (hours: number) => `${hours} oras ang nakalipas`,

	chatsLoadFailedWith: (message: string) => `Hindi ma-load ang iyong mga chat: ${message}`,
	chatsLoadFailed: 'Hindi ma-load ang iyong mga chat.',
	chatOpenFailedWith: (message: string) => `Hindi mabuksan ang chat na iyon: ${message}`,
	chatOpenFailed: 'Hindi mabuksan ang chat na iyon.',
	turnFailed: 'May nagkamali sa pagkonekta kay Sammy. Gumagana ba ang chat API?',

	safetyContactsAria: 'Mga kontak para sa emergency',

	talkToPerson: 'Makipag-usap sa isang tao',
	talkToPersonAria: 'Makipag-usap sa isang tao sa SJSU Cares',
	university: 'San José State University',
	caresClose: 'Isara ang impormasyon ng SJSU Cares',
	caresOverview:
		'Tumutulong ang SJSU Cares sa mga estudyanteng nahihirapan sa mga pangunahing pangangailangan, sa pamamagitan ng case management, referral, at follow-up.',
	caresRequest: 'Humingi ng tulong',
	caresRequestHint: 'Ang pinakamabilis na paraan para maabot ang isang case manager',
	caresCall: (phone: string) => `Tawagan ang ${phone}`,
	caresEmail: (email: string) => `Mag-email sa ${email}`,
	caresHoursLabel: 'Oras',
	caresHoursValue: 'Lunes - Biyernes, 10 am - 4 pm',
	caresOfficeLabel: 'Opisina',
	caresRecommended: 'Inirerekomenda para sa tanong mo',
	caresAllServices: 'Lahat ng serbisyo ng SJSU Cares',
	caresDirectory: 'Direktoryo ng staff at buong listahan ng contact',
	caresNote: 'Isama ang iyong student ID kapag nakipag-ugnayan ka.',
	caresServices: {
		food: {
			title: 'Tulong sa pagkain',
			description: 'Access sa Spartan Food Pantry at tulong sa aplikasyon sa CalFresh.',
		},
		housing: {
			title: 'Tulong sa tirahan',
			description:
				'Emergency housing, mga rehousing program, at tulong sa paghahanap ng matitirhan.',
		},
		financial: {
			title: 'Tulong pinansyal',
			description: 'Emergency grant at financial coaching para sa mga di-inaasahang gastos.',
		},
		parenting: {
			title: 'Mga estudyanteng may anak',
			description:
				'Tulong sa pagpapa-enroll, gabay sa iyong mga karapatan, at accommodation sa campus.',
		},
	},

	escalationAria: 'Draft ng email para sa isang tao',
	escalationHeadline: 'Ipadala ito sa isang tao',
	escalationNote:
		'Bubuksan ito sa sarili mong email app, kaya diretso sa iyo babalik ang sagot. Tiyaking galing ito sa email address mo sa paaralan.',
	escalationTo: 'Para kay',
	escalationSubject: 'Paksa',
	escalationOpen: 'Buksan sa aking email app',
	escalationCopied: 'Nakopya',
	escalationCopy: 'Kopyahin ang mensahe',
	escalationClipboardBlocked:
		'Hindi kami pinayagan ng browser mo na gamitin ang clipboard, kaya naka-select na ang mensahe: kopyahin ito at i-paste sa bagong email.',
	escalationTooLong:
		'Masyadong mahaba ang draft na ito para kusang buksan ang email app mo. Kopyahin ito at i-paste sa bagong email.',

	placeAria: 'Lokasyon sa campus',
	placeDirections: 'Kunin ang direksyon',
	placeDirectionsFor: (name: string) => `Kunin ang direksyon papuntang ${name}`,
	placeMapCredit: 'Datos ng mapa © mga kontribyutor ng OpenStreetMap',

	settingsClose: 'Isara ang mga setting',
	close: 'Isara',
	languageLabel: 'Wika',
	languageHint: 'Binabago nito ang mga label at button ng app na ito.',
	languageUnreviewed: 'Isinalin ng makina. Hindi pa nasusuri ng SJSU ang pananalitang ito.',

	costSection: 'Gastos sa pagpapatakbo nito',
	costThisConversation: 'Ang usapang ito',
	costMessagesSoFar: (messages: string, plural: boolean) =>
		`${messages} ${plural ? 'na mensahe' : 'na mensahe'} sa ngayon, presyong batay sa mga token na talagang nagamit.`,
	costNothingMetered:
		'Wala pang nasusukat sa chat na ito. Magsisimula ang bilang sa unang mensaheng ipapadala mo rito.',
	costMessagesSent: 'Mga mensaheng naipadala',
	costModelCalls: 'Mga tawag sa model',
	costInputTokens: 'Input token',
	costOutputTokens: 'Output token',
	costPerMessage: 'Gastos kada mensahe',
	costMonthOfUse: 'Isang buwan ng paggamit',
	costMessagesAMonth: 'Mga mensahe ng estudyante kada buwan',
	costMonthAtVolume: 'Isang buwan sa dami na iyon',
	costRunsAtNoUse: 'Gumagana kahit walang gumagamit',
	costNobodyAsking: 'Bawat buwan, kahit walang nagtatanong',
	costWhatOneAdds: 'Kung magkano ang idinadagdag ng isang mensahe',
	costFootLead: 'Mga tantiya ito, hindi bill.',
	costFootRest: 'Mga nakalathalang list price ng AWS, minultiply sa nasukat na paggamit ng token.',
};

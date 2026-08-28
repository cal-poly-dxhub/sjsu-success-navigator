import type { Strings } from './en';

/** Punjabi (Gurmukhi). */
export const pa: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: 'ਜਾਰੀ ਰੱਖਣ ਲਈ ਆਪਣੇ SJSU ਖਾਤੇ ਨਾਲ ਸਾਈਨ ਇਨ ਕਰੋ।',
	signIn: 'ਸਾਈਨ ਇਨ ਕਰੋ',
	signingIn: 'ਸਾਈਨ ਇਨ ਹੋ ਰਿਹਾ ਹੈ…',
	signInNotCompleted: 'ਸਾਈਨ ਇਨ ਪੂਰਾ ਨਹੀਂ ਹੋ ਸਕਿਆ।',
	signInNotStarted: 'ਸਾਈਨ ਇਨ ਸ਼ੁਰੂ ਨਹੀਂ ਹੋ ਸਕਿਆ।',

	welcome:
		'ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ Sammy ਹਾਂ। SJSU ਕੈਂਪਸ ਦੇ ਸਰੋਤਾਂ ਬਾਰੇ ਮੈਨੂੰ ਕੁਝ ਵੀ ਪੁੱਛੋ: ਟਿਊਟਰਿੰਗ, ਸਲਾਹ, ਸਿਹਤ, ਰਿਹਾਇਸ਼ ਵਿੱਚ ਮਦਦ, ਅਤੇ ਹੋਰ ਬਹੁਤ ਕੁਝ।',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Sammy, SJSU Spartans ਦਾ ਸ਼ੁਭੰਕਰ',
	expandSidebar: 'ਸਾਈਡਬਾਰ ਫੈਲਾਓ',
	collapseSidebar: 'ਸਾਈਡਬਾਰ ਸਮੇਟੋ',

	newChat: 'ਨਵੀਂ ਗੱਲਬਾਤ',
	chatHistory: 'ਗੱਲਬਾਤ ਦਾ ਇਤਿਹਾਸ',
	recentChats: 'ਹਾਲੀਆ ਗੱਲਬਾਤਾਂ',
	renameChat: (title: string) => `${title} ਦਾ ਨਾਂ ਬਦਲੋ`,
	deleteChat: (title: string) => `${title} ਮਿਟਾਓ`,
	deleteConfirm: (title: string) => `“${title}” ਮਿਟਾਉਣੀ ਹੈ? ਇਹ ਵਾਪਸ ਨਹੀਂ ਲਿਆਂਦੀ ਜਾ ਸਕਦੀ।`,
	save: 'ਸੰਭਾਲੋ',
	saving: 'ਸੰਭਾਲਿਆ ਜਾ ਰਿਹਾ ਹੈ…',
	cancel: 'ਰੱਦ ਕਰੋ',
	delete: 'ਮਿਟਾਓ',
	deleting: 'ਮਿਟਾਇਆ ਜਾ ਰਿਹਾ ਹੈ…',
	opening: 'ਖੋਲ੍ਹੀ ਜਾ ਰਹੀ ਹੈ…',
	loadingChats: 'ਤੁਹਾਡੀਆਂ ਗੱਲਬਾਤਾਂ ਲੋਡ ਹੋ ਰਹੀਆਂ ਹਨ…',
	noStoredChats: 'ਜੋ ਗੱਲਬਾਤਾਂ ਤੁਸੀਂ ਭੇਜਦੇ ਹੋ, ਉਹ ਇੱਥੇ ਸੰਭਾਲੀਆਂ ਜਾਂਦੀਆਂ ਹਨ ਅਤੇ ਤੁਹਾਡੇ ਖਾਤੇ ਵਿੱਚ ਰਹਿੰਦੀਆਂ ਹਨ।',
	renameFailed: 'ਉਸ ਗੱਲਬਾਤ ਦਾ ਨਾਂ ਨਹੀਂ ਬਦਲਿਆ ਜਾ ਸਕਿਆ।',
	deleteFailed: 'ਉਹ ਗੱਲਬਾਤ ਮਿਟਾਈ ਨਹੀਂ ਜਾ ਸਕੀ।',
	signedIn: 'ਸਾਈਨ ਇਨ ਹੈ',
	signOut: 'ਸਾਈਨ ਆਊਟ ਕਰੋ',
	settings: 'ਸੈਟਿੰਗਾਂ',
	closeNavigation: 'ਨੈਵੀਗੇਸ਼ਨ ਬੰਦ ਕਰੋ',
	openChatHistory: 'ਗੱਲਬਾਤ ਦਾ ਇਤਿਹਾਸ ਖੋਲ੍ਹੋ',

	askSammy: 'Sammy ਨੂੰ ਪੁੱਛੋ',
	composerPlaceholder: 'ਟਿਊਟਰਿੰਗ, ਸਲਾਹ, ਸਿਹਤ ਬਾਰੇ ਪੁੱਛੋ…',
	send: 'ਭੇਜੋ',
	yourMessage: 'ਤੁਹਾਡਾ ਸੁਨੇਹਾ',
	thinking: 'ਸੋਚ ਰਿਹਾ ਹਾਂ',
	waitingForSammy: 'Sammy ਦੇ ਜਵਾਬ ਦੀ ਉਡੀਕ ਹੈ',
	stageRetrieving: 'ਕੈਂਪਸ ਦੇ ਸਰੋਤਾਂ ਵਿੱਚ ਲੱਭਿਆ ਜਾ ਰਿਹਾ ਹੈ',
	stageComposingCards: 'ਕੈਂਪਸ ਦੇ ਸਰੋਤ ਇਕੱਠੇ ਕੀਤੇ ਜਾ ਰਹੇ ਹਨ',

	campusResources: 'ਕੈਂਪਸ ਦੇ ਸਰੋਤ',
	campusResourcesFrom: (timestamp: string) => `${timestamp} ਦੇ ਕੈਂਪਸ ਸਰੋਤ`,
	timeJustNow: 'ਹੁਣੇ',
	timeMinutesAgo: (minutes: number) => `${minutes} ਮਿੰਟ ਪਹਿਲਾਂ`,
	timeHoursAgo: (hours: number) => `${hours} ਘੰਟੇ ਪਹਿਲਾਂ`,

	chatsLoadFailedWith: (message: string) => `ਤੁਹਾਡੀਆਂ ਗੱਲਬਾਤਾਂ ਲੋਡ ਨਹੀਂ ਹੋ ਸਕੀਆਂ: ${message}`,
	chatsLoadFailed: 'ਤੁਹਾਡੀਆਂ ਗੱਲਬਾਤਾਂ ਲੋਡ ਨਹੀਂ ਹੋ ਸਕੀਆਂ।',
	chatOpenFailedWith: (message: string) => `ਉਹ ਗੱਲਬਾਤ ਨਹੀਂ ਖੁੱਲ੍ਹ ਸਕੀ: ${message}`,
	chatOpenFailed: 'ਉਹ ਗੱਲਬਾਤ ਨਹੀਂ ਖੁੱਲ੍ਹ ਸਕੀ।',
	turnFailed: 'Sammy ਤੱਕ ਪਹੁੰਚਣ ਵਿੱਚ ਕੁਝ ਗੜਬੜ ਹੋ ਗਈ। ਕੀ ਚੈਟ API ਚੱਲ ਰਿਹਾ ਹੈ?',

	safetyContactsAria: 'ਐਮਰਜੈਂਸੀ ਸੰਪਰਕ',

	talkToPerson: 'ਕਿਸੇ ਵਿਅਕਤੀ ਨਾਲ ਗੱਲ ਕਰੋ',
	talkToPersonAria: 'SJSU Cares ਦੇ ਕਿਸੇ ਵਿਅਕਤੀ ਨਾਲ ਗੱਲ ਕਰੋ',
	university: 'San José State University',
	caresClose: 'SJSU Cares ਦੀ ਜਾਣਕਾਰੀ ਬੰਦ ਕਰੋ',
	caresOverview:
		'SJSU Cares ਉਨ੍ਹਾਂ ਵਿਦਿਆਰਥੀਆਂ ਦੀ ਮਦਦ ਕਰਦਾ ਹੈ ਜਿਨ੍ਹਾਂ ਨੂੰ ਮੁੱਢਲੀਆਂ ਲੋੜਾਂ ਪੂਰੀਆਂ ਕਰਨ ਵਿੱਚ ਔਖ ਆ ਰਹੀ ਹੈ - ਕੇਸ ਮੈਨੇਜਮੈਂਟ, ਰੈਫ਼ਰਲ ਅਤੇ ਅਗਲੀ ਦੇਖ-ਰੇਖ ਨਾਲ।',
	caresRequest: 'ਮਦਦ ਲਈ ਬੇਨਤੀ ਕਰੋ',
	caresRequestHint: 'ਕੇਸ ਮੈਨੇਜਰ ਤੱਕ ਪਹੁੰਚਣ ਦਾ ਸਭ ਤੋਂ ਤੇਜ਼ ਤਰੀਕਾ',
	caresCall: (phone: string) => `${phone} 'ਤੇ ਕਾਲ ਕਰੋ`,
	caresEmail: (email: string) => `${email} 'ਤੇ ਈਮੇਲ ਕਰੋ`,
	caresHoursLabel: 'ਸਮਾਂ',
	caresHoursValue: 'ਸੋਮਵਾਰ - ਸ਼ੁੱਕਰਵਾਰ, ਸਵੇਰੇ 10 ਵਜੇ - ਸ਼ਾਮ 4 ਵਜੇ',
	caresOfficeLabel: 'ਦਫ਼ਤਰ',
	caresRecommended: 'ਤੁਹਾਡੇ ਸਵਾਲ ਲਈ ਸੁਝਾਇਆ ਗਿਆ',
	caresAllServices: 'SJSU Cares ਦੀਆਂ ਸਾਰੀਆਂ ਸੇਵਾਵਾਂ',
	caresDirectory: 'ਸਟਾਫ਼ ਡਾਇਰੈਕਟਰੀ ਅਤੇ ਪੂਰੀ ਸੰਪਰਕ ਸੂਚੀ',
	caresNote: 'ਸੰਪਰਕ ਕਰਦੇ ਸਮੇਂ ਆਪਣਾ ਵਿਦਿਆਰਥੀ ਆਈਡੀ ਜ਼ਰੂਰ ਦੱਸੋ।',
	caresServices: {
		food: {
			title: 'ਭੋਜਨ ਸਹਾਇਤਾ',
			description: 'Spartan Food Pantry ਤੱਕ ਪਹੁੰਚ ਅਤੇ CalFresh ਅਰਜ਼ੀ ਵਿੱਚ ਮਦਦ।',
		},
		housing: {
			title: 'ਰਿਹਾਇਸ਼ ਸਹਾਇਤਾ',
			description: 'ਐਮਰਜੈਂਸੀ ਰਿਹਾਇਸ਼, ਮੁੜ-ਵਸੇਬਾ ਪ੍ਰੋਗਰਾਮ ਅਤੇ ਘਰ ਲੱਭਣ ਵਿੱਚ ਸਹਾਇਤਾ।',
		},
		financial: {
			title: 'ਆਰਥਿਕ ਸਹਾਇਤਾ',
			description: 'ਅਚਾਨਕ ਖਰਚਿਆਂ ਲਈ ਐਮਰਜੈਂਸੀ ਗ੍ਰਾਂਟ ਅਤੇ ਵਿੱਤੀ ਸਲਾਹ।',
		},
		parenting: {
			title: 'ਬੱਚਿਆਂ ਵਾਲੇ ਵਿਦਿਆਰਥੀ',
			description: 'ਰਜਿਸਟਰੇਸ਼ਨ ਵਿੱਚ ਸਹਾਇਤਾ, ਹੱਕਾਂ ਬਾਰੇ ਜਾਣਕਾਰੀ ਅਤੇ ਕੈਂਪਸ ਵਿੱਚ ਸਹੂਲਤਾਂ।',
		},
	},

	escalationAria: 'ਕਿਸੇ ਵਿਅਕਤੀ ਨੂੰ ਭੇਜਣ ਲਈ ਈਮੇਲ ਦਾ ਖਰੜਾ',
	escalationHeadline: 'ਇਹ ਕਿਸੇ ਵਿਅਕਤੀ ਨੂੰ ਭੇਜੋ',
	escalationNote:
		'ਇਹ ਤੁਹਾਡੇ ਆਪਣੇ ਈਮੇਲ ਐਪ ਵਿੱਚ ਖੁੱਲ੍ਹੇਗਾ, ਇਸ ਲਈ ਜਵਾਬ ਸਿੱਧਾ ਤੁਹਾਡੇ ਕੋਲ ਆਵੇਗਾ। ਜਾਂਚ ਲਵੋ ਕਿ ਇਹ ਤੁਹਾਡੇ ਕਾਲਜ ਵਾਲੇ ਪਤੇ ਤੋਂ ਭੇਜਿਆ ਜਾ ਰਿਹਾ ਹੈ।',
	escalationTo: 'ਵੱਲ',
	escalationSubject: 'ਵਿਸ਼ਾ',
	escalationOpen: 'ਮੇਰੇ ਈਮੇਲ ਐਪ ਵਿੱਚ ਖੋਲ੍ਹੋ',
	escalationCopied: 'ਕਾਪੀ ਹੋ ਗਿਆ',
	escalationCopy: 'ਸੁਨੇਹਾ ਕਾਪੀ ਕਰੋ',
	escalationClipboardBlocked:
		'ਤੁਹਾਡੇ ਬ੍ਰਾਊਜ਼ਰ ਨੇ ਕਲਿੱਪਬੋਰਡ ਵਰਤਣ ਨਹੀਂ ਦਿੱਤਾ, ਇਸ ਲਈ ਸੁਨੇਹਾ ਚੁਣ ਦਿੱਤਾ ਗਿਆ ਹੈ: ਇਸਨੂੰ ਕਾਪੀ ਕਰਕੇ ਨਵੀਂ ਈਮੇਲ ਵਿੱਚ ਪੇਸਟ ਕਰ ਦਿਓ।',
	escalationTooLong:
		'ਇਹ ਖਰੜਾ ਇੰਨਾ ਲੰਮਾ ਹੈ ਕਿ ਤੁਹਾਡਾ ਈਮੇਲ ਐਪ ਆਪਣੇ ਆਪ ਨਹੀਂ ਖੁੱਲ੍ਹ ਸਕਦਾ। ਇਸਨੂੰ ਕਾਪੀ ਕਰਕੇ ਨਵੀਂ ਈਮੇਲ ਵਿੱਚ ਪੇਸਟ ਕਰ ਦਿਓ।',

	placeAria: 'ਕੈਂਪਸ ਵਿੱਚ ਥਾਂ',
	placeDirections: 'ਰਾਹ ਵੇਖੋ',
	placeDirectionsFor: (name: string) => `${name} ਤੱਕ ਦਾ ਰਾਹ ਵੇਖੋ`,
	placeMapCredit: 'ਨਕਸ਼ਾ ਡਾਟਾ © OpenStreetMap ਯੋਗਦਾਨੀ',

	settingsClose: 'ਸੈਟਿੰਗਾਂ ਬੰਦ ਕਰੋ',
	close: 'ਬੰਦ ਕਰੋ',
	languageLabel: 'ਭਾਸ਼ਾ',
	languageHint: 'ਇਸ ਨਾਲ ਇਸ ਐਪ ਦੇ ਆਪਣੇ ਲੇਬਲ ਅਤੇ ਬਟਨ ਬਦਲਦੇ ਹਨ।',
	languageUnreviewed: 'ਮਸ਼ੀਨੀ ਅਨੁਵਾਦ। SJSU ਨੇ ਅਜੇ ਇਸ ਸ਼ਬਦਾਵਲੀ ਦੀ ਜਾਂਚ ਨਹੀਂ ਕੀਤੀ।',

	costSection: 'ਇਸਨੂੰ ਚਲਾਉਣ ਦੀ ਲਾਗਤ',
	costThisConversation: 'ਇਹ ਗੱਲਬਾਤ',
	costMessagesSoFar: (messages: string) =>
		`ਹੁਣ ਤੱਕ ${messages} ਸੁਨੇਹੇ, ਅਸਲ ਵਿੱਚ ਵਰਤੇ ਗਏ ਟੋਕਨਾਂ ਦੇ ਹਿਸਾਬ ਨਾਲ।`,
	costNothingMetered:
		'ਇਸ ਗੱਲਬਾਤ ਵਿੱਚ ਅਜੇ ਕੁਝ ਨਹੀਂ ਮਾਪਿਆ ਗਿਆ। ਗਿਣਤੀ ਇੱਥੇ ਭੇਜੇ ਪਹਿਲੇ ਸੁਨੇਹੇ ਤੋਂ ਸ਼ੁਰੂ ਹੁੰਦੀ ਹੈ।',
	costMessagesSent: 'ਭੇਜੇ ਗਏ ਸੁਨੇਹੇ',
	costModelCalls: 'ਮਾਡਲ ਕਾਲਾਂ',
	costInputTokens: 'ਇਨਪੁੱਟ ਟੋਕਨ',
	costOutputTokens: 'ਆਊਟਪੁੱਟ ਟੋਕਨ',
	costPerMessage: 'ਪ੍ਰਤੀ ਸੁਨੇਹਾ ਲਾਗਤ',
	costMonthOfUse: 'ਇੱਕ ਮਹੀਨੇ ਦੀ ਵਰਤੋਂ',
	costMessagesAMonth: 'ਹਰ ਮਹੀਨੇ ਵਿਦਿਆਰਥੀਆਂ ਦੇ ਸੁਨੇਹੇ',
	costMonthAtVolume: 'ਉਸ ਮਾਤਰਾ ਉੱਤੇ ਇੱਕ ਮਹੀਨਾ',
	costRunsAtNoUse: 'ਬਿਨਾਂ ਵਰਤੋਂ ਦੇ ਵੀ',
	costNobodyAsking: 'ਹਰ ਮਹੀਨੇ, ਭਾਵੇਂ ਕੋਈ ਨਾ ਪੁੱਛੇ',
	costWhatOneAdds: 'ਇੱਕ ਸੁਨੇਹਾ ਕਿੰਨਾ ਜੋੜਦਾ ਹੈ',
	costFootLead: 'ਇਹ ਅੰਦਾਜ਼ੇ ਹਨ, ਬਿੱਲ ਨਹੀਂ।',
	costFootRest: 'AWS ਦੀਆਂ ਪ੍ਰਕਾਸ਼ਿਤ ਸੂਚੀ ਦਰਾਂ, ਮਾਪੀ ਗਈ ਟੋਕਨ ਵਰਤੋਂ ਨਾਲ ਗੁਣਾ ਕੀਤੀਆਂ।',
};

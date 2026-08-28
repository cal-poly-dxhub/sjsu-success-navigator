/** The English strings, and the shape every other language is typed against, so a missing key is
 * A build error rather than an `undefined` in the sidebar. */
export const en = {
	// Sign-in gate.
	appName: 'Student Success Navigator',
	signInSubtitle: 'Sign in with your SJSU account to continue.',
	signIn: 'Sign in',
	signingIn: 'Signing in…',
	signInNotCompleted: 'Sign-in could not be completed.',
	signInNotStarted: 'Sign-in could not be started.',

	/** The greeting a new chat opens with. */
	welcome:
		"Hi! I'm Sammy. Ask me anything about SJSU campus resources: tutoring, advising, wellness, housing help, and more.",

	// Sidebar chrome: the header, and the rail it collapses to.
	brandName: 'SJSU Student Success',
	// Read aloud in place of his face, so it says who he is rather than naming a file.
	sammyAlt: 'Sammy, the SJSU Spartans mascot',
	// Both the accessible name and the tooltip on the one control, which is why each of these
	// is used twice.
	expandSidebar: 'Expand sidebar',
	collapseSidebar: 'Collapse sidebar',

	// Sidebar.
	newChat: 'New chat',
	chatHistory: 'Chat history',
	recentChats: 'Recent chats',
	renameChat: (title: string) => `Rename ${title}`,
	deleteChat: (title: string) => `Delete ${title}`,
	deleteConfirm: (title: string) => `Delete “${title}”? This cannot be undone.`,
	save: 'Save',
	saving: 'Saving…',
	cancel: 'Cancel',
	delete: 'Delete',
	deleting: 'Deleting…',
	opening: 'Opening…',
	loadingChats: 'Loading your chats…',
	noStoredChats: 'Chats you send are saved here, and stay on your account.',
	renameFailed: 'Could not rename that chat.',
	deleteFailed: 'Could not delete that chat.',
	signedIn: 'Signed in',
	signOut: 'Sign out',
	settings: 'Settings',
	closeNavigation: 'Close navigation',
	openChatHistory: 'Open chat history',

	// The conversation surface.
	askSammy: 'Ask Sammy',
	composerPlaceholder: 'Ask about tutoring, advising, wellness…',
	send: 'Send',
	yourMessage: 'Your message',
	thinking: 'Thinking',
	waitingForSammy: "Waiting for Sammy's response",
	stageRetrieving: 'Looking through campus resources',
	stageComposingCards: 'Finding campus resources',

	// The card group under a reply, named for a screen reader rather than left as an unlabelled
	// region.
	campusResources: 'Campus resources',
	campusResourcesFrom: (timestamp: string) => `Campus resources from ${timestamp}`,
	// The timestamp inside that label, and on screen above the group.
	timeJustNow: 'Just now',
	timeMinutesAgo: (minutes: number) => `${minutes}m ago`,
	timeHoursAgo: (hours: number) => `${hours}h ago`,

	// Failures the frontend says on its own behalf. Not replies: Sammy did not write these.
	chatsLoadFailedWith: (message: string) => `Could not load your chats: ${message}`,
	chatsLoadFailed: 'Could not load your chats.',
	chatOpenFailedWith: (message: string) => `Could not open that chat: ${message}`,
	chatOpenFailed: 'Could not open that chat.',
	turnFailed: 'Something went wrong reaching Sammy. Is the chat API running?',

	// The crisis panel's accessible name, and the only string of that panel in this file.
	safetyContactsAria: 'Safety contacts',

	// The handoff to a human, and the panel behind it.
	talkToPerson: 'Talk to a person',
	talkToPersonAria: 'Talk to a person at SJSU Cares',
	university: 'San José State University',
	caresClose: 'Close SJSU Cares information',
	caresOverview:
		'SJSU Cares helps students facing basic-needs challenges with case management, referrals, and follow-up.',
	caresRequest: 'Request assistance',
	caresRequestHint: 'The fastest way to reach a case manager',
	caresCall: (phone: string) => `Call ${phone}`,
	caresEmail: (email: string) => `Email ${email}`,
	caresHoursLabel: 'Hours',
	caresHoursValue: 'Monday - Friday, 10 am - 4 pm',
	caresOfficeLabel: 'Office',
	caresRecommended: 'Recommended for your question',
	caresAllServices: 'All SJSU Cares services',
	caresDirectory: 'Staff directory and full contact list',
	caresNote: 'Include your student ID when you reach out.',
	caresServices: {
		food: {
			title: 'Food assistance',
			description: 'Spartan Food Pantry access and CalFresh application help.',
		},
		housing: {
			title: 'Housing assistance',
			description: 'Emergency housing, rehousing programs, and housing search support.',
		},
		financial: {
			title: 'Financial assistance',
			description: 'Emergency grants and financial coaching for unexpected expenses.',
		},
		parenting: {
			title: 'Parenting students',
			description: 'Registration support, rights guidance, and campus accommodations.',
		},
	},

	// The escalate-to-human draft.
	escalationAria: 'Email draft for a person',
	escalationHeadline: 'Send this to a person',
	escalationNote:
		"This opens in your own email app, so a reply comes straight back to you. Double check it's being sent from your school address.",
	// The two mail headers, labelling the server's values beside them.
	escalationTo: 'To',
	escalationSubject: 'Subject',
	escalationOpen: 'Open in my email app',
	escalationCopied: 'Copied',
	escalationCopy: 'Copy the message',
	// Both are states a real draft reaches, so both say what to do next rather than what broke:
	// The clipboard the browser refused, and the draft too long for a mailto link.
	escalationClipboardBlocked:
		'Your browser would not let us use the clipboard, so the message is selected instead: copy it and paste it into a new email.',
	escalationTooLong:
		'This draft is too long to open your email app automatically. Copy it and paste it into a new email instead.',

	// The campus location panel.
	placeAria: 'Campus location',
	placeDirections: 'Get directions',
	placeDirectionsFor: (name: string) => `Get directions to ${name}`,
	placeMapCredit: 'Map data © OpenStreetMap contributors',

	// Settings.
	settingsClose: 'Close settings',
	close: 'Close',
	languageLabel: 'Language',
	// One sentence, and it is about this control rather than about the app's languages.
	languageHint: "Changes this app's own labels and buttons.",
	languageUnreviewed: 'Machine translated. SJSU has not reviewed this wording yet.',

	// The cost breakdown, nested inside settings.
	costSection: 'What this costs to run',
	costThisConversation: 'This conversation',
	costMessagesSoFar: (messages: string, plural: boolean) =>
		`${messages} ${plural ? 'messages' : 'message'} so far, priced from the tokens they actually used.`,
	costNothingMetered:
		'Nothing metered in this chat yet. It counts from the first message you send here.',
	costMessagesSent: 'Messages sent',
	costModelCalls: 'Model calls',
	costInputTokens: 'Input tokens',
	costOutputTokens: 'Output tokens',
	costPerMessage: 'Cost per message',
	costMonthOfUse: 'A month of use',
	costMessagesAMonth: 'Student messages a month',
	costMonthAtVolume: 'A month at that volume',
	costRunsAtNoUse: 'Runs at no use',
	costNobodyAsking: 'Every month, nobody asking',
	costWhatOneAdds: 'What one message adds',
	costFootLead: 'These are estimates, not a bill.',
	costFootRest: 'Published AWS list prices, multiplied by measured token use.',
};

export type Strings = typeof en;

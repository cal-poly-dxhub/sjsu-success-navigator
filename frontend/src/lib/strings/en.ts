/**
 * The English strings, and THE SHAPE every other language is checked against: each
 * translation is typed as `Strings`, so a missing or misspelled key is a build-time error
 * rather than an `undefined` rendered into the sidebar.
 *
 * WHAT LIVES HERE IS PAGE CHROME - buttons, labels, headings, empty states, the frontend's
 * own error sentences, and the greeting an unused chat opens with. Sammy's replies and the
 * cards are not here: they come off the server in whatever language the server produced
 * them, and switching those is a separate job with a separate contract.
 *
 * Interpolation is a function per string, which TypeScript checks for arity in a way a
 * `{{name}}` placeholder never would.
 */
export const en = {
	// Sign-in gate.
	appName: 'Student Success Navigator',
	signInSubtitle: 'Sign in with your SJSU account to continue.',
	signIn: 'Sign in',
	signingIn: 'Signing in…',
	signInNotCompleted: 'Sign-in could not be completed.',
	signInNotStarted: 'Sign-in could not be started.',

	/**
	 * The greeting a new chat opens with.
	 *
	 * It is the ONE piece of bubble prose in this file, and it is here because it is the
	 * app's own words rather than the model's - nothing was asked yet, so nothing has been
	 * answered. It follows the picker only while the chat is untouched; the moment a student
	 * sends a message that chat keeps the language it was in, and every reply after it is
	 * the model's to decide (components/ChatApp.tsx).
	 */
	welcome:
		"Hi! I'm Sammy. Ask me anything about SJSU campus resources: tutoring, advising, wellness, housing help, and more.",

	// Sidebar chrome: the header, and the rail it collapses to.
	//
	// `brandName` is the product's name and stays in English in every file, the same way
	// `appName` above does. It is a key rather than a literal in the component so that
	// staying English is a decision a reviewer can see and reverse per language, which is
	// not true of a string baked into the JSX.
	brandName: 'SJSU Student Success',
	// Read aloud in place of his face, so it says who he is rather than naming a file. The
	// two proper nouns stay as SJSU publishes them; the sentence around them does not.
	sammyAlt: 'Sammy, the SJSU Spartans mascot',
	// Both the accessible name and the tooltip on the one control, which is why each of
	// these is used twice. They name what the click DOES, not what the sidebar is now.
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

	// Failures the frontend says on its own behalf. Not replies: Sammy did not write these.
	chatsLoadFailedWith: (message: string) => `Could not load your chats: ${message}`,
	chatsLoadFailed: 'Could not load your chats.',
	chatOpenFailedWith: (message: string) => `Could not open that chat: ${message}`,
	chatOpenFailed: 'Could not open that chat.',
	turnFailed: 'Something went wrong reaching Sammy. Is the chat API running?',

	// The handoff to a human, and the panel behind it. SJSU Cares, the phone number, the
	// email and the building name are proper nouns and stay as SJSU publishes them.
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

	// The escalate-to-human draft. The OTHER way to a person: where the panel above hands
	// over a phone number, this hands over a message the student sends themselves, from
	// their own address. Nothing here is sent by the app, and none of these strings is the
	// draft - the To line, the subject and the body come off the server with the turn and
	// are shown in whatever language the model wrote them in, exactly as stored.
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
	// Both are states a real draft reaches, so both say what to do next rather than what
	// broke: the clipboard the browser refused, and the draft too long for a mailto link.
	escalationClipboardBlocked:
		'Your browser would not let us use the clipboard, so the message is selected instead: copy it and paste it into a new email.',
	escalationTooLong:
		'This draft is too long to open your email app automatically. Copy it and paste it into a new email instead.',

	// Settings.
	settingsClose: 'Close settings',
	close: 'Close',
	languageLabel: 'Language',
	languageHint:
		"Changes this app's own labels and buttons. Sammy's answers are not translated yet.",
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

import type { Strings } from './en';

/**
 * Hindi. MACHINE-AUTHORED, NOT REVIEWED BY A HINDI SPEAKER. See es.ts for why a file in this
 * state ships; it is marked `reviewed: false` in lib/i18n.ts and said plainly to the student
 * in the settings panel. SJSU's sponsor named this language alongside Spanish.
 *
 * REVIEWER'S NOTES. "आप" throughout - a campus service addressing a student politely; "तुम"
 * would read as over-familiar from an institution. Proper nouns are left in Latin script on
 * purpose: SJSU Cares, Spartan Food Pantry, CalFresh, Sammy and the product name are what
 * the student will see on signs and on SJSU's own pages, and transliterating them would send
 * someone looking for a name that does not exist.
 */
export const hi: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: 'जारी रखने के लिए अपने SJSU खाते से साइन इन करें।',
	signIn: 'साइन इन करें',
	signingIn: 'साइन इन हो रहा है…',
	signInNotCompleted: 'साइन इन पूरा नहीं हो सका।',
	signInNotStarted: 'साइन इन शुरू नहीं हो सका।',

	welcome:
		'नमस्ते! मैं Sammy हूँ। SJSU कैंपस के संसाधनों के बारे में मुझसे कुछ भी पूछें: ट्यूटरिंग, एडवाइजिंग, स्वास्थ्य, आवास सहायता, और भी बहुत कुछ।',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Sammy, SJSU Spartans का शुभंकर',
	expandSidebar: 'साइडबार विस्तृत करें',
	collapseSidebar: 'साइडबार संक्षिप्त करें',

	newChat: 'नई चैट',
	chatHistory: 'चैट इतिहास',
	recentChats: 'हाल की चैट',
	renameChat: (title: string) => `${title} का नाम बदलें`,
	deleteChat: (title: string) => `${title} हटाएँ`,
	deleteConfirm: (title: string) => `“${title}” हटाएँ? इसे वापस नहीं लाया जा सकता।`,
	save: 'सहेजें',
	saving: 'सहेजा जा रहा है…',
	cancel: 'रद्द करें',
	delete: 'हटाएँ',
	deleting: 'हटाया जा रहा है…',
	opening: 'खोला जा रहा है…',
	loadingChats: 'आपकी चैट लोड हो रही हैं…',
	noStoredChats: 'आप जो चैट भेजते हैं वे यहाँ सहेजी जाती हैं और आपके खाते में रहती हैं।',
	renameFailed: 'उस चैट का नाम नहीं बदला जा सका।',
	deleteFailed: 'उस चैट को हटाया नहीं जा सका।',
	signedIn: 'साइन इन किया हुआ है',
	signOut: 'साइन आउट करें',
	settings: 'सेटिंग्स',
	closeNavigation: 'नेविगेशन बंद करें',
	openChatHistory: 'चैट इतिहास खोलें',

	askSammy: 'Sammy से पूछें',
	composerPlaceholder: 'ट्यूटरिंग, एडवाइजिंग, स्वास्थ्य के बारे में पूछें…',
	send: 'भेजें',
	yourMessage: 'आपका संदेश',
	thinking: 'सोच रहा हूँ',
	waitingForSammy: 'Sammy के उत्तर की प्रतीक्षा है',
	stageRetrieving: 'कैंपस संसाधनों में खोजा जा रहा है…',

	chatsLoadFailedWith: (message: string) => `आपकी चैट लोड नहीं हो सकीं: ${message}`,
	chatsLoadFailed: 'आपकी चैट लोड नहीं हो सकीं।',
	chatOpenFailedWith: (message: string) => `वह चैट नहीं खुल सकी: ${message}`,
	chatOpenFailed: 'वह चैट नहीं खुल सकी।',
	turnFailed: 'Sammy तक पहुँचने में कुछ गड़बड़ हो गई। क्या चैट API चल रहा है?',

	talkToPerson: 'किसी व्यक्ति से बात करें',
	talkToPersonAria: 'SJSU Cares के किसी व्यक्ति से बात करें',
	university: 'San José State University',
	caresClose: 'SJSU Cares की जानकारी बंद करें',
	caresOverview:
		'SJSU Cares बुनियादी ज़रूरतों से जूझ रहे विद्यार्थियों की मदद करता है - केस मैनेजमेंट, रेफ़रल और आगे की देखरेख के साथ।',
	caresRequest: 'सहायता के लिए अनुरोध करें',
	caresRequestHint: 'केस मैनेजर तक पहुँचने का सबसे तेज़ तरीका',
	caresCall: (phone: string) => `${phone} पर कॉल करें`,
	caresEmail: (email: string) => `${email} पर ईमेल करें`,
	caresHoursLabel: 'समय',
	caresHoursValue: 'सोमवार - शुक्रवार, सुबह 10 बजे - शाम 4 बजे',
	caresOfficeLabel: 'कार्यालय',
	caresRecommended: 'आपके प्रश्न के लिए सुझाया गया',
	caresAllServices: 'SJSU Cares की सभी सेवाएँ',
	caresDirectory: 'स्टाफ़ निर्देशिका और पूरी संपर्क सूची',
	caresNote: 'संपर्क करते समय अपना छात्र आईडी ज़रूर बताएँ।',
	caresServices: {
		food: {
			title: 'भोजन सहायता',
			description: 'Spartan Food Pantry तक पहुँच और CalFresh आवेदन में मदद।',
		},
		housing: {
			title: 'आवास सहायता',
			description: 'आपातकालीन आवास, पुनर्वास कार्यक्रम और घर ढूँढ़ने में सहायता।',
		},
		financial: {
			title: 'आर्थिक सहायता',
			description: 'अप्रत्याशित खर्चों के लिए आपातकालीन अनुदान और वित्तीय परामर्श।',
		},
		parenting: {
			title: 'बच्चों की परवरिश करने वाले विद्यार्थी',
			description: 'पंजीकरण में सहायता, अधिकारों की जानकारी और कैंपस में सुविधाएँ।',
		},
	},

	settingsClose: 'सेटिंग्स बंद करें',
	close: 'बंद करें',
	languageLabel: 'भाषा',
	languageHint:
		'इससे इस ऐप के अपने लेबल और बटन बदलते हैं। Sammy के उत्तरों का अनुवाद अभी नहीं होता।',
	languageUnreviewed: 'मशीन से अनुवादित। SJSU ने अभी इस भाषा की जाँच नहीं की है।',

	costSection: 'इसे चलाने की लागत',
	costThisConversation: 'यह बातचीत',
	costMessagesSoFar: (messages: string) =>
		`अब तक ${messages} संदेश, उनके वास्तव में उपयोग किए गए टोकन के आधार पर।`,
	costNothingMetered:
		'इस चैट में अभी कुछ नहीं मापा गया है। गिनती यहाँ भेजे गए पहले संदेश से शुरू होती है।',
	costMessagesSent: 'भेजे गए संदेश',
	costModelCalls: 'मॉडल कॉल',
	costInputTokens: 'इनपुट टोकन',
	costOutputTokens: 'आउटपुट टोकन',
	costPerMessage: 'प्रति संदेश लागत',
	costMonthOfUse: 'एक महीने का उपयोग',
	costMessagesAMonth: 'हर महीने विद्यार्थियों के संदेश',
	costMonthAtVolume: 'उस मात्रा पर एक महीना',
	costRunsAtNoUse: 'बिना उपयोग के भी',
	costNobodyAsking: 'हर महीने, चाहे कोई न पूछे',
	costWhatOneAdds: 'एक संदेश कितना जोड़ता है',
	costFootLead: 'ये अनुमान हैं, बिल नहीं।',
	costFootRest: 'AWS की प्रकाशित सूची दरें, मापे गए टोकन उपयोग से गुणा की गईं।',
};

import type { Strings } from './en';

/**
 * French. MACHINE-AUTHORED, NOT REVIEWED BY A FRENCH SPEAKER. See es.ts for why a file in
 * this state ships; it is marked `reviewed: false` in lib/i18n.ts and said plainly to the
 * student in the settings panel.
 *
 * REVIEWER'S NOTES. "tu" throughout, which is the call es.ts made and for the same reason: a
 * student talking to a campus assistant, not an institution addressing a citizen. This is the
 * likeliest line to be overturned - French university writing reaches for "vous" by default -
 * and overturning it is this file alone. Typographic apostrophes (’) rather than straight
 * ones, which is correct French typography and incidentally what keeps these single-quoted
 * strings free of escapes. Proper nouns stay in English on purpose - SJSU Cares, Spartan Food
 * Pantry, CalFresh, Sammy, the product name - because they are what the student will see on
 * signs and on SJSU's own pages.
 */
export const fr: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: 'Connecte-toi avec ton compte SJSU pour continuer.',
	signIn: 'Se connecter',
	signingIn: 'Connexion…',
	signInNotCompleted: 'La connexion n’a pas pu être terminée.',
	signInNotStarted: 'La connexion n’a pas pu être lancée.',

	welcome:
		'Salut ! Je suis Sammy. Pose-moi toutes tes questions sur les ressources du campus de SJSU : tutorat, conseil pédagogique, bien-être, aide au logement et bien plus encore.',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Sammy, la mascotte des SJSU Spartans',
	expandSidebar: 'Déplier la barre latérale',
	collapseSidebar: 'Replier la barre latérale',

	newChat: 'Nouvelle conversation',
	chatHistory: 'Historique des conversations',
	recentChats: 'Conversations récentes',
	renameChat: (title: string) => `Renommer ${title}`,
	deleteChat: (title: string) => `Supprimer ${title}`,
	deleteConfirm: (title: string) => `Supprimer « ${title} » ? Cette action est irréversible.`,
	save: 'Enregistrer',
	saving: 'Enregistrement…',
	cancel: 'Annuler',
	delete: 'Supprimer',
	deleting: 'Suppression…',
	opening: 'Ouverture…',
	loadingChats: 'Chargement de tes conversations…',
	noStoredChats:
		'Les conversations que tu envoies sont enregistrées ici et restent sur ton compte.',
	renameFailed: 'Impossible de renommer cette conversation.',
	deleteFailed: 'Impossible de supprimer cette conversation.',
	signedIn: 'Connecté',
	signOut: 'Se déconnecter',
	settings: 'Paramètres',
	closeNavigation: 'Fermer la navigation',
	openChatHistory: 'Ouvrir l’historique des conversations',

	askSammy: 'Demander à Sammy',
	composerPlaceholder: 'Pose une question sur le tutorat, le conseil, le bien-être…',
	send: 'Envoyer',
	yourMessage: 'Ton message',
	thinking: 'Réflexion',
	waitingForSammy: 'En attente de la réponse de Sammy',
	stageRetrieving: 'Recherche dans les ressources du campus',

	campusResources: 'Ressources du campus',
	campusResourcesFrom: (timestamp: string) => `Ressources du campus de ${timestamp}`,
	timeJustNow: 'À l’instant',
	timeMinutesAgo: (minutes: number) => `il y a ${minutes} min`,
	timeHoursAgo: (hours: number) => `il y a ${hours} h`,

	chatsLoadFailedWith: (message: string) => `Impossible de charger tes conversations : ${message}`,
	chatsLoadFailed: 'Impossible de charger tes conversations.',
	chatOpenFailedWith: (message: string) => `Impossible d’ouvrir cette conversation : ${message}`,
	chatOpenFailed: 'Impossible d’ouvrir cette conversation.',
	turnFailed: 'Un problème est survenu en contactant Sammy. L’API de chat fonctionne-t-elle ?',

	safetyContactsAria: 'Contacts d’urgence',

	talkToPerson: 'Parler à une personne',
	talkToPersonAria: 'Parler à une personne de SJSU Cares',
	university: 'San José State University',
	caresClose: 'Fermer les informations SJSU Cares',
	caresOverview:
		'SJSU Cares aide les étudiants confrontés à des difficultés de première nécessité, avec un suivi personnalisé, des orientations et un accompagnement dans la durée.',
	caresRequest: 'Demander de l’aide',
	caresRequestHint: 'Le moyen le plus rapide de joindre un gestionnaire de dossier',
	caresCall: (phone: string) => `Appeler le ${phone}`,
	caresEmail: (email: string) => `Écrire à ${email}`,
	caresHoursLabel: 'Horaires',
	caresHoursValue: 'Du lundi au vendredi, de 10 h à 16 h',
	caresOfficeLabel: 'Bureau',
	caresRecommended: 'Recommandé pour ta question',
	caresAllServices: 'Tous les services de SJSU Cares',
	caresDirectory: 'Annuaire du personnel et liste complète des contacts',
	caresNote: 'Indique ton numéro d’étudiant quand tu les contactes.',
	caresServices: {
		food: {
			title: 'Aide alimentaire',
			description: 'Accès au Spartan Food Pantry et aide pour la demande CalFresh.',
		},
		housing: {
			title: 'Aide au logement',
			description:
				'Hébergement d’urgence, programmes de relogement et accompagnement dans la recherche.',
		},
		financial: {
			title: 'Aide financière',
			description: 'Aides d’urgence et accompagnement budgétaire pour les dépenses imprévues.',
		},
		parenting: {
			title: 'Étudiants parents',
			description:
				'Aide à l’inscription, information sur tes droits et aménagements sur le campus.',
		},
	},

	escalationAria: 'Brouillon d’e-mail pour une personne',
	escalationHeadline: 'Envoie ceci à une personne',
	escalationNote:
		'Le message s’ouvre dans ta propre application e-mail, donc la réponse te revient directement. Vérifie qu’il part bien de ton adresse universitaire.',
	escalationTo: 'À',
	escalationSubject: 'Objet',
	escalationOpen: 'Ouvrir dans mon application e-mail',
	escalationCopied: 'Copié',
	escalationCopy: 'Copier le message',
	escalationClipboardBlocked:
		'Ton navigateur ne nous a pas laissés utiliser le presse-papiers, le message est donc sélectionné : copie-le et colle-le dans un nouvel e-mail.',
	escalationTooLong:
		'Ce brouillon est trop long pour ouvrir automatiquement ton application e-mail. Copie-le et colle-le dans un nouvel e-mail.',

	settingsClose: 'Fermer les paramètres',
	close: 'Fermer',
	languageLabel: 'Langue',
	languageHint: 'Change les libellés et les boutons de cette application.',
	languageUnreviewed: 'Traduction automatique. SJSU n’a pas encore relu cette formulation.',

	costSection: 'Ce que coûte le fonctionnement',
	costThisConversation: 'Cette conversation',
	costMessagesSoFar: (messages: string, plural: boolean) =>
		`${messages} message${plural ? 's' : ''} jusqu’ici, facturés d’après les tokens réellement utilisés.`,
	costNothingMetered:
		'Rien n’a encore été mesuré dans cette conversation. Le compteur démarre au premier message que tu envoies ici.',
	costMessagesSent: 'Messages envoyés',
	costModelCalls: 'Appels au modèle',
	costInputTokens: 'Tokens en entrée',
	costOutputTokens: 'Tokens en sortie',
	costPerMessage: 'Coût par message',
	costMonthOfUse: 'Un mois d’utilisation',
	costMessagesAMonth: 'Messages d’étudiants par mois',
	costMonthAtVolume: 'Un mois à ce volume',
	costRunsAtNoUse: 'Coût sans aucune utilisation',
	costNobodyAsking: 'Chaque mois, sans une seule question',
	costWhatOneAdds: 'Ce qu’ajoute un message',
	costFootLead: 'Ce sont des estimations, pas une facture.',
	costFootRest: 'Tarifs publics AWS, multipliés par la consommation de tokens mesurée.',
};

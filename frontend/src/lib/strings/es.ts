import type { Strings } from './en';

/**
 * Spanish. MACHINE-AUTHORED, NOT REVIEWED BY A SPANISH SPEAKER.
 *
 * Every string below was written by a model from the English in en.ts, and no native speaker
 * has read them. They ship in that state deliberately: SJSU's sponsor asked that students be
 * met in their own language, and a machine-translated sidebar is closer to that than an
 * English one while the review happens. What is NOT guessed at is which strings are in that
 * state - LANGUAGES in lib/i18n.ts records `reviewed: false`, this comment says so in the
 * file SJSU will be sent, and the settings panel says so to the student.
 *
 * REVIEWER'S NOTES. Neutral "tú" throughout, on the basis that this is a student talking to
 * a campus assistant rather than an institution addressing a citizen; switch to "usted" if
 * SJSU prefers it, and the change is this file alone. Proper nouns are left in English on
 * purpose - SJSU Cares, Spartan Food Pantry, CalFresh, Sammy, the product name - because
 * they are what the student will see on signs and on SJSU's own pages.
 */
export const es: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: 'Inicia sesión con tu cuenta de SJSU para continuar.',
	signIn: 'Iniciar sesión',
	signingIn: 'Iniciando sesión…',
	signInNotCompleted: 'No se pudo completar el inicio de sesión.',
	signInNotStarted: 'No se pudo iniciar el inicio de sesión.',

	welcome:
		'¡Hola! Soy Sammy. Pregúntame lo que quieras sobre los recursos del campus de SJSU: tutorías, asesoría académica, bienestar, ayuda con la vivienda y más.',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Sammy, la mascota de los SJSU Spartans',
	expandSidebar: 'Expandir la barra lateral',
	collapseSidebar: 'Contraer la barra lateral',

	newChat: 'Chat nuevo',
	chatHistory: 'Historial de chats',
	recentChats: 'Chats recientes',
	renameChat: (title: string) => `Cambiar el nombre de ${title}`,
	deleteChat: (title: string) => `Eliminar ${title}`,
	deleteConfirm: (title: string) => `¿Eliminar «${title}»? Esto no se puede deshacer.`,
	save: 'Guardar',
	saving: 'Guardando…',
	cancel: 'Cancelar',
	delete: 'Eliminar',
	deleting: 'Eliminando…',
	opening: 'Abriendo…',
	loadingChats: 'Cargando tus chats…',
	noStoredChats: 'Los chats que envías se guardan aquí y quedan en tu cuenta.',
	renameFailed: 'No se pudo cambiar el nombre de ese chat.',
	deleteFailed: 'No se pudo eliminar ese chat.',
	signedIn: 'Sesión iniciada',
	signOut: 'Cerrar sesión',
	settings: 'Configuración',
	closeNavigation: 'Cerrar la navegación',
	openChatHistory: 'Abrir el historial de chats',

	askSammy: 'Pregúntale a Sammy',
	composerPlaceholder: 'Pregunta sobre tutorías, asesoría, bienestar…',
	send: 'Enviar',
	yourMessage: 'Tu mensaje',
	thinking: 'Pensando',
	waitingForSammy: 'Esperando la respuesta de Sammy',
	stageRetrieving: 'Buscando en los recursos del campus…',

	chatsLoadFailedWith: (message: string) => `No se pudieron cargar tus chats: ${message}`,
	chatsLoadFailed: 'No se pudieron cargar tus chats.',
	chatOpenFailedWith: (message: string) => `No se pudo abrir ese chat: ${message}`,
	chatOpenFailed: 'No se pudo abrir ese chat.',
	turnFailed: 'Algo salió mal al contactar a Sammy. ¿La API del chat está funcionando?',

	talkToPerson: 'Habla con una persona',
	talkToPersonAria: 'Habla con una persona de SJSU Cares',
	university: 'San José State University',
	caresClose: 'Cerrar la información de SJSU Cares',
	caresOverview:
		'SJSU Cares ayuda a estudiantes con dificultades para cubrir necesidades básicas, con gestión de casos, derivaciones y seguimiento.',
	caresRequest: 'Solicitar ayuda',
	caresRequestHint: 'La forma más rápida de comunicarte con un gestor de casos',
	caresCall: (phone: string) => `Llamar al ${phone}`,
	caresEmail: (email: string) => `Escribir a ${email}`,
	caresHoursLabel: 'Horario',
	caresHoursValue: 'Lunes a viernes, 10 a. m. a 4 p. m.',
	caresOfficeLabel: 'Oficina',
	caresRecommended: 'Recomendado para tu pregunta',
	caresAllServices: 'Todos los servicios de SJSU Cares',
	caresDirectory: 'Directorio del personal y lista completa de contactos',
	caresNote: 'Incluye tu número de estudiante cuando escribas.',
	caresServices: {
		food: {
			title: 'Ayuda con alimentos',
			description: 'Acceso a Spartan Food Pantry y ayuda para solicitar CalFresh.',
		},
		housing: {
			title: 'Ayuda con vivienda',
			description:
				'Vivienda de emergencia, programas de realojamiento y apoyo para buscar vivienda.',
		},
		financial: {
			title: 'Ayuda económica',
			description: 'Becas de emergencia y asesoría financiera para gastos imprevistos.',
		},
		parenting: {
			title: 'Estudiantes con hijos',
			description:
				'Apoyo con la matrícula, orientación sobre tus derechos y adaptaciones en el campus.',
		},
	},

	settingsClose: 'Cerrar la configuración',
	close: 'Cerrar',
	languageLabel: 'Idioma',
	languageHint:
		'Cambia las etiquetas y los botones de esta aplicación. Las respuestas de Sammy todavía no se traducen.',
	languageUnreviewed: 'Traducción automática. SJSU aún no ha revisado esta redacción.',

	costSection: 'Cuánto cuesta funcionar',
	costThisConversation: 'Esta conversación',
	costMessagesSoFar: (messages: string, plural: boolean) =>
		`${messages} ${plural ? 'mensajes' : 'mensaje'} hasta ahora, con precio calculado a partir de los tokens que realmente usaron.`,
	costNothingMetered:
		'Todavía no se ha medido nada en este chat. Empieza a contar desde el primer mensaje que envíes aquí.',
	costMessagesSent: 'Mensajes enviados',
	costModelCalls: 'Llamadas al modelo',
	costInputTokens: 'Tokens de entrada',
	costOutputTokens: 'Tokens de salida',
	costPerMessage: 'Costo por mensaje',
	costMonthOfUse: 'Un mes de uso',
	costMessagesAMonth: 'Mensajes de estudiantes al mes',
	costMonthAtVolume: 'Un mes con ese volumen',
	costRunsAtNoUse: 'Funcionando sin uso',
	costNobodyAsking: 'Cada mes, sin que nadie pregunte',
	costWhatOneAdds: 'Lo que añade un mensaje',
	costFootLead: 'Son estimaciones, no una factura.',
	costFootRest: 'Precios de lista publicados por AWS, multiplicados por el uso medido de tokens.',
};

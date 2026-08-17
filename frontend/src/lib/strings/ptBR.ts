import type { Strings } from './en';

/**
 * Brazilian Portuguese. MACHINE-AUTHORED, NOT REVIEWED BY A PORTUGUESE SPEAKER. See es.ts for
 * why a file in this state ships; it is marked `reviewed: false` in lib/i18n.ts and said
 * plainly to the student in the settings panel.
 *
 * REVIEWER'S NOTES. BRAZILIAN, not European, and the difference is vocabulary rather than
 * spelling: "você" throughout, "celular" over "telemóvel", "aplicativo" over "aplicação",
 * "tela" over "ecrã", "arquivo" over "ficheiro". A European Portuguese deployment would be a
 * separate file and a separate entry, the same way zhHant.ts is separate from zhHans.ts, not
 * a search-and-replace over this one. Proper nouns stay in English on purpose - SJSU Cares,
 * Spartan Food Pantry, CalFresh, Sammy, the product name - because they are what the student
 * will see on signs and on SJSU's own pages.
 */
export const ptBR: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: 'Entre com sua conta da SJSU para continuar.',
	signIn: 'Entrar',
	signingIn: 'Entrando…',
	signInNotCompleted: 'Não foi possível concluir o login.',
	signInNotStarted: 'Não foi possível iniciar o login.',

	welcome:
		'Oi! Eu sou o Sammy. Pode me perguntar qualquer coisa sobre os recursos do campus da SJSU: monitoria, orientação acadêmica, bem-estar, ajuda com moradia e muito mais.',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Sammy, o mascote dos SJSU Spartans',
	expandSidebar: 'Expandir a barra lateral',
	collapseSidebar: 'Recolher a barra lateral',

	newChat: 'Nova conversa',
	chatHistory: 'Histórico de conversas',
	recentChats: 'Conversas recentes',
	renameChat: (title: string) => `Renomear ${title}`,
	deleteChat: (title: string) => `Excluir ${title}`,
	deleteConfirm: (title: string) => `Excluir “${title}”? Isso não pode ser desfeito.`,
	save: 'Salvar',
	saving: 'Salvando…',
	cancel: 'Cancelar',
	delete: 'Excluir',
	deleting: 'Excluindo…',
	opening: 'Abrindo…',
	loadingChats: 'Carregando suas conversas…',
	noStoredChats: 'As conversas que você envia ficam salvas aqui, na sua conta.',
	renameFailed: 'Não foi possível renomear essa conversa.',
	deleteFailed: 'Não foi possível excluir essa conversa.',
	signedIn: 'Conectado',
	signOut: 'Sair',
	settings: 'Configurações',
	closeNavigation: 'Fechar a navegação',
	openChatHistory: 'Abrir o histórico de conversas',

	askSammy: 'Pergunte ao Sammy',
	composerPlaceholder: 'Pergunte sobre monitoria, orientação, bem-estar…',
	send: 'Enviar',
	yourMessage: 'Sua mensagem',
	thinking: 'Pensando',
	waitingForSammy: 'Aguardando a resposta do Sammy',
	stageRetrieving: 'Procurando nos recursos do campus',
	stageComposingCards: 'Reunindo os recursos do campus',

	campusResources: 'Recursos do campus',
	campusResourcesFrom: (timestamp: string) => `Recursos do campus de ${timestamp}`,
	timeJustNow: 'Agora mesmo',
	timeMinutesAgo: (minutes: number) => `há ${minutes} min`,
	timeHoursAgo: (hours: number) => `há ${hours} h`,

	chatsLoadFailedWith: (message: string) => `Não foi possível carregar suas conversas: ${message}`,
	chatsLoadFailed: 'Não foi possível carregar suas conversas.',
	chatOpenFailedWith: (message: string) => `Não foi possível abrir essa conversa: ${message}`,
	chatOpenFailed: 'Não foi possível abrir essa conversa.',
	turnFailed: 'Algo deu errado ao falar com o Sammy. A API do chat está no ar?',

	safetyContactsAria: 'Contatos de emergência',

	talkToPerson: 'Falar com uma pessoa',
	talkToPersonAria: 'Falar com uma pessoa do SJSU Cares',
	university: 'San José State University',
	caresClose: 'Fechar as informações do SJSU Cares',
	caresOverview:
		'O SJSU Cares ajuda estudantes com dificuldades para cobrir necessidades básicas, com acompanhamento de casos, encaminhamentos e retorno depois.',
	caresRequest: 'Pedir ajuda',
	caresRequestHint: 'O jeito mais rápido de falar com um assistente social',
	caresCall: (phone: string) => `Ligar para ${phone}`,
	caresEmail: (email: string) => `Escrever para ${email}`,
	caresHoursLabel: 'Horário',
	caresHoursValue: 'Segunda a sexta, das 10h às 16h',
	caresOfficeLabel: 'Escritório',
	caresRecommended: 'Recomendado para a sua pergunta',
	caresAllServices: 'Todos os serviços do SJSU Cares',
	caresDirectory: 'Lista da equipe e contatos completos',
	caresNote: 'Inclua seu número de matrícula quando entrar em contato.',
	caresServices: {
		food: {
			title: 'Ajuda com alimentação',
			description: 'Acesso ao Spartan Food Pantry e ajuda para solicitar o CalFresh.',
		},
		housing: {
			title: 'Ajuda com moradia',
			description: 'Moradia de emergência, programas de realocação e apoio na busca por moradia.',
		},
		financial: {
			title: 'Ajuda financeira',
			description: 'Auxílios emergenciais e orientação financeira para despesas inesperadas.',
		},
		parenting: {
			title: 'Estudantes com filhos',
			description: 'Apoio na matrícula, orientação sobre seus direitos e adaptações no campus.',
		},
	},

	escalationAria: 'Rascunho de e-mail para uma pessoa',
	escalationHeadline: 'Envie isto para uma pessoa',
	escalationNote:
		'Isso abre no seu próprio aplicativo de e-mail, então a resposta volta direto para você. Confira se está sendo enviado do seu endereço da universidade.',
	escalationTo: 'Para',
	escalationSubject: 'Assunto',
	escalationOpen: 'Abrir no meu aplicativo de e-mail',
	escalationCopied: 'Copiado',
	escalationCopy: 'Copiar a mensagem',
	escalationClipboardBlocked:
		'Seu navegador não deixou a gente usar a área de transferência, então a mensagem ficou selecionada: copie e cole em um e-mail novo.',
	escalationTooLong:
		'Este rascunho é longo demais para abrir seu aplicativo de e-mail automaticamente. Copie e cole em um e-mail novo.',

	settingsClose: 'Fechar as configurações',
	close: 'Fechar',
	languageLabel: 'Idioma',
	languageHint: 'Muda os rótulos e os botões deste aplicativo.',
	languageUnreviewed: 'Tradução automática. A SJSU ainda não revisou este texto.',

	costSection: 'Quanto custa manter no ar',
	costThisConversation: 'Esta conversa',
	costMessagesSoFar: (messages: string, plural: boolean) =>
		`${messages} ${plural ? 'mensagens' : 'mensagem'} até agora, com preço calculado pelos tokens realmente usados.`,
	costNothingMetered:
		'Nada foi medido nesta conversa ainda. A contagem começa na primeira mensagem que você enviar aqui.',
	costMessagesSent: 'Mensagens enviadas',
	costModelCalls: 'Chamadas ao modelo',
	costInputTokens: 'Tokens de entrada',
	costOutputTokens: 'Tokens de saída',
	costPerMessage: 'Custo por mensagem',
	costMonthOfUse: 'Um mês de uso',
	costMessagesAMonth: 'Mensagens de estudantes por mês',
	costMonthAtVolume: 'Um mês nesse volume',
	costRunsAtNoUse: 'Custo sem nenhum uso',
	costNobodyAsking: 'Todo mês, sem ninguém perguntar',
	costWhatOneAdds: 'O que uma mensagem acrescenta',
	costFootLead: 'São estimativas, não uma fatura.',
	costFootRest: 'Preços de tabela publicados pela AWS, multiplicados pelo uso medido de tokens.',
};

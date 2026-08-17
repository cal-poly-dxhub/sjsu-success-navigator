import type { Strings } from './en';

/**
 * Russian. MACHINE-AUTHORED, NOT REVIEWED BY A RUSSIAN SPEAKER. See es.ts for why a file in
 * this state ships; it is marked `reviewed: false` in lib/i18n.ts and said plainly to the
 * student in the settings panel.
 *
 * REVIEWER'S NOTES. "вы" throughout, and this file deliberately breaks with the informal
 * register es.ts and fr.ts chose. Russian does not read "ты" from an institution as friendly
 * the way Spanish reads "tú": from a service addressing an adult it reads as presumptuous,
 * and a warm voice is not worth that. Lower-case "вы", which is the modern convention for
 * addressing one person in an interface, rather than the letter-writing capital.
 *
 * `costMessagesSoFar` DROPS THE PLURAL FLAG, which is not laziness. Russian needs three
 * forms where the flag carries two (сообщение / сообщения / сообщений, on 1, 2-4, 5+), so a
 * boolean would be wrong for most counts. The sentence is rebuilt around a count-first
 * construction that governs the genitive plural for every number instead, which is idiomatic
 * and correct at 1, at 3 and at 21. Interpolation is a function per string precisely so a
 * language can do this; a `{{count}}` placeholder could not.
 *
 * Proper nouns stay in English on purpose - SJSU Cares, Spartan Food Pantry, CalFresh, Sammy,
 * the product name - because they are what the student will see on signs and on SJSU's pages.
 */
export const ru: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: 'Войдите с учётной записью SJSU, чтобы продолжить.',
	signIn: 'Войти',
	signingIn: 'Выполняется вход…',
	signInNotCompleted: 'Не удалось завершить вход.',
	signInNotStarted: 'Не удалось начать вход.',

	welcome:
		'Привет! Я Sammy. Спрашивайте меня о чём угодно, что связано с ресурсами кампуса SJSU: репетиторство, учебные консультации, забота о здоровье, помощь с жильём и многое другое.',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Sammy, талисман команды SJSU Spartans',
	expandSidebar: 'Развернуть боковую панель',
	collapseSidebar: 'Свернуть боковую панель',

	newChat: 'Новый чат',
	chatHistory: 'История чатов',
	recentChats: 'Недавние чаты',
	renameChat: (title: string) => `Переименовать ${title}`,
	deleteChat: (title: string) => `Удалить ${title}`,
	deleteConfirm: (title: string) => `Удалить «${title}»? Это действие нельзя отменить.`,
	save: 'Сохранить',
	saving: 'Сохранение…',
	cancel: 'Отмена',
	delete: 'Удалить',
	deleting: 'Удаление…',
	opening: 'Открытие…',
	loadingChats: 'Загружаем ваши чаты…',
	noStoredChats: 'Отправленные чаты сохраняются здесь и остаются в вашей учётной записи.',
	renameFailed: 'Не удалось переименовать этот чат.',
	deleteFailed: 'Не удалось удалить этот чат.',
	signedIn: 'Вы вошли',
	signOut: 'Выйти',
	settings: 'Настройки',
	closeNavigation: 'Закрыть навигацию',
	openChatHistory: 'Открыть историю чатов',

	askSammy: 'Спросить Sammy',
	composerPlaceholder: 'Спросите о репетиторстве, консультациях, здоровье…',
	send: 'Отправить',
	yourMessage: 'Ваше сообщение',
	thinking: 'Думаю',
	waitingForSammy: 'Ждём ответа Sammy',
	stageRetrieving: 'Ищем среди ресурсов кампуса',
	stageComposingCards: 'Собираем ресурсы кампуса',

	campusResources: 'Ресурсы кампуса',
	campusResourcesFrom: (timestamp: string) => `Ресурсы кампуса от ${timestamp}`,
	timeJustNow: 'Только что',
	timeMinutesAgo: (minutes: number) => `${minutes} мин назад`,
	timeHoursAgo: (hours: number) => `${hours} ч назад`,

	chatsLoadFailedWith: (message: string) => `Не удалось загрузить ваши чаты: ${message}`,
	chatsLoadFailed: 'Не удалось загрузить ваши чаты.',
	chatOpenFailedWith: (message: string) => `Не удалось открыть этот чат: ${message}`,
	chatOpenFailed: 'Не удалось открыть этот чат.',
	turnFailed: 'Не получилось связаться с Sammy. API чата запущен?',

	safetyContactsAria: 'Экстренные контакты',

	talkToPerson: 'Поговорить с человеком',
	talkToPersonAria: 'Поговорить с сотрудником SJSU Cares',
	university: 'San José State University',
	caresClose: 'Закрыть информацию о SJSU Cares',
	caresOverview:
		'SJSU Cares помогает студентам, которым трудно покрыть базовые нужды: ведение случая, направления в нужные службы и дальнейшее сопровождение.',
	caresRequest: 'Запросить помощь',
	caresRequestHint: 'Самый быстрый способ связаться с куратором',
	caresCall: (phone: string) => `Позвонить по номеру ${phone}`,
	caresEmail: (email: string) => `Написать на ${email}`,
	caresHoursLabel: 'Часы работы',
	caresHoursValue: 'Понедельник - пятница, с 10:00 до 16:00',
	caresOfficeLabel: 'Офис',
	caresRecommended: 'Рекомендуем по вашему вопросу',
	caresAllServices: 'Все услуги SJSU Cares',
	caresDirectory: 'Список сотрудников и полные контакты',
	caresNote: 'Указывайте свой студенческий номер, когда обращаетесь.',
	caresServices: {
		food: {
			title: 'Помощь с питанием',
			description: 'Доступ к Spartan Food Pantry и помощь с заявкой на CalFresh.',
		},
		housing: {
			title: 'Помощь с жильём',
			description: 'Экстренное жильё, программы переселения и поддержка в поиске жилья.',
		},
		financial: {
			title: 'Финансовая помощь',
			description: 'Экстренные выплаты и финансовые консультации при непредвиденных расходах.',
		},
		parenting: {
			title: 'Студенты с детьми',
			description: 'Помощь с записью на курсы, разъяснение прав и условия на кампусе.',
		},
	},

	escalationAria: 'Черновик письма сотруднику',
	escalationHeadline: 'Отправьте это сотруднику',
	escalationNote:
		'Письмо откроется в вашем почтовом приложении, поэтому ответ придёт прямо вам. Проверьте, что оно отправляется с вашего университетского адреса.',
	escalationTo: 'Кому',
	escalationSubject: 'Тема',
	escalationOpen: 'Открыть в моём почтовом приложении',
	escalationCopied: 'Скопировано',
	escalationCopy: 'Скопировать сообщение',
	escalationClipboardBlocked:
		'Браузер не разрешил нам воспользоваться буфером обмена, поэтому текст просто выделен: скопируйте его и вставьте в новое письмо.',
	escalationTooLong:
		'Этот черновик слишком длинный, чтобы почтовое приложение открылось автоматически. Скопируйте его и вставьте в новое письмо.',

	settingsClose: 'Закрыть настройки',
	close: 'Закрыть',
	languageLabel: 'Язык',
	languageHint: 'Меняет подписи и кнопки самого приложения.',
	languageUnreviewed: 'Машинный перевод. SJSU пока не проверял эти формулировки.',

	costSection: 'Сколько стоит работа сервиса',
	costThisConversation: 'Этот разговор',
	costMessagesSoFar: (messages: string) =>
		`Сообщений пока: ${messages}. Стоимость рассчитана по фактически использованным токенам.`,
	costNothingMetered:
		'В этом чате пока ничего не измерено. Отсчёт начнётся с первого отправленного здесь сообщения.',
	costMessagesSent: 'Отправлено сообщений',
	costModelCalls: 'Обращений к модели',
	costInputTokens: 'Входные токены',
	costOutputTokens: 'Выходные токены',
	costPerMessage: 'Стоимость сообщения',
	costMonthOfUse: 'Месяц использования',
	costMessagesAMonth: 'Сообщений студентов в месяц',
	costMonthAtVolume: 'Месяц при таком объёме',
	costRunsAtNoUse: 'Расходы без использования',
	costNobodyAsking: 'Каждый месяц, даже если никто не спрашивает',
	costWhatOneAdds: 'Сколько добавляет одно сообщение',
	costFootLead: 'Это оценки, а не счёт.',
	costFootRest: 'Опубликованные прайс-листы AWS, умноженные на измеренный расход токенов.',
};

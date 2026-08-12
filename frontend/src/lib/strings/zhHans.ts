import type { Strings } from './en';

/**
 * Simplified Chinese. MACHINE-AUTHORED, NOT REVIEWED BY A CHINESE SPEAKER. See es.ts for why
 * a file in this state ships; it is marked `reviewed: false` in lib/i18n.ts and said plainly
 * to the student in the settings panel.
 *
 * REVIEWER'S NOTES. Simplified characters only - Traditional would be a separate file and a
 * separate entry, not a variant of this one. Proper nouns are left in English on purpose:
 * SJSU Cares, Spartan Food Pantry, CalFresh, Sammy and the product name are what the student
 * will see on signs and on SJSU's own pages.
 */
export const zhHans: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: '请使用你的 SJSU 账号登录以继续。',
	signIn: '登录',
	signingIn: '正在登录…',
	signInNotCompleted: '登录未能完成。',
	signInNotStarted: '登录未能开始。',

	welcome:
		'你好！我是 Sammy。关于 SJSU 校园资源，你可以随便问我：辅导、学业咨询、身心健康、住房帮助等等。',

	newChat: '新对话',
	chatHistory: '对话记录',
	recentChats: '最近的对话',
	renameChat: (title: string) => `重命名${title}`,
	deleteChat: (title: string) => `删除${title}`,
	deleteConfirm: (title: string) => `删除“${title}”？此操作无法撤销。`,
	save: '保存',
	saving: '正在保存…',
	cancel: '取消',
	delete: '删除',
	deleting: '正在删除…',
	opening: '正在打开…',
	loadingChats: '正在加载你的对话…',
	noStoredChats: '你发送的对话会保存在这里，并留在你的账号中。',
	renameFailed: '无法重命名该对话。',
	deleteFailed: '无法删除该对话。',
	signedIn: '已登录',
	signOut: '退出登录',
	settings: '设置',
	closeNavigation: '关闭导航',
	openChatHistory: '打开对话记录',

	askSammy: '问 Sammy',
	composerPlaceholder: '问问辅导、咨询、身心健康…',
	send: '发送',
	yourMessage: '你的消息',
	thinking: '思考中',
	waitingForSammy: '正在等待 Sammy 的回复',
	stageRetrieving: '正在查找校园资源…',

	chatsLoadFailedWith: (message: string) => `无法加载你的对话：${message}`,
	chatsLoadFailed: '无法加载你的对话。',
	chatOpenFailedWith: (message: string) => `无法打开该对话：${message}`,
	chatOpenFailed: '无法打开该对话。',
	turnFailed: '连接 Sammy 时出错了。聊天 API 正在运行吗？',

	talkToPerson: '与真人交谈',
	talkToPersonAria: '与 SJSU Cares 的工作人员交谈',
	university: 'San José State University',
	caresClose: '关闭 SJSU Cares 信息',
	caresOverview:
		'SJSU Cares 通过个案管理、转介和后续跟进，帮助在基本生活需求上遇到困难的学生。',
	caresRequest: '申请帮助',
	caresRequestHint: '联系个案管理员最快的方式',
	caresCall: (phone: string) => `致电 ${phone}`,
	caresEmail: (email: string) => `发邮件至 ${email}`,
	caresHoursLabel: '办公时间',
	caresHoursValue: '周一至周五，上午 10 点至下午 4 点',
	caresOfficeLabel: '办公室',
	caresRecommended: '为你的问题推荐',
	caresAllServices: 'SJSU Cares 的所有服务',
	caresDirectory: '员工名录和完整联系方式',
	caresNote: '联系时请附上你的学生证号。',
	caresServices: {
		food: {
			title: '食物援助',
			description: '使用 Spartan Food Pantry，并获得申请 CalFresh 的帮助。',
		},
		housing: {
			title: '住房援助',
			description: '紧急住所、重新安置项目以及找房支持。',
		},
		financial: {
			title: '经济援助',
			description: '针对意外开支的紧急补助和财务辅导。',
		},
		parenting: {
			title: '育儿的学生',
			description: '选课注册支持、权益指导以及校园便利安排。',
		},
	},

	settingsClose: '关闭设置',
	close: '关闭',
	languageLabel: '语言',
	languageHint: '更改本应用自身的标签和按钮。Sammy 的回答目前还不会被翻译。',
	languageUnreviewed: '机器翻译。SJSU 尚未审校此措辞。',

	costSection: '运行成本',
	costEstimateTag: '估算',
	costThisConversation: '本次对话',
	costMessagesSoFar: (messages: string) =>
		`目前 ${messages} 条消息，按实际使用的 token 计价。`,
	costNothingMetered: '这次对话还没有计量。从你在这里发出的第一条消息开始计算。',
	costMessagesSent: '已发送消息',
	costModelCalls: '模型调用次数',
	costInputTokens: '输入 token',
	costOutputTokens: '输出 token',
	costPerMessage: '每条消息成本',
	costMonthOfUse: '一个月的使用量',
	costMessagesAMonth: '学生每月的消息数',
	costMonthAtVolume: '该用量下的一个月',
	costRunsAtNoUse: '无人使用时的开销',
	costNobodyAsking: '每个月，即使无人提问',
	costWhatOneAdds: '每多一条消息增加的成本',
	costFootLead: '这些是估算，不是账单。',
	costFootRest: 'AWS 公布的标价，乘以实测的 token 使用量。',
};

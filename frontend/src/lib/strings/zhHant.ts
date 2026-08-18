import type { Strings } from './en';

/**
 * Traditional Chinese. MACHINE-AUTHORED, NOT REVIEWED BY A CHINESE SPEAKER. See es.ts for why
 * a file in this state ships; it is marked `reviewed: false` in lib/i18n.ts and said plainly
 * to the student in the settings panel.
 *
 * REVIEWER'S NOTES. NOT zhHans.ts CONVERTED, which is the whole reason it is its own file.
 * Character conversion alone produces text a Taipei reader finds legible and foreign, because
 * the differences that matter here are vocabulary: 登入 not 登錄, 設定 not 設置, 儲存 not 保存,
 * 訊息 not 消息, 傳送 not 發送, 開啟 not 打開, 複製到剪貼簿 not 剪貼板, 學號 not 學生證號. Taiwan
 * usage throughout; a Hong Kong deployment would want its own pass over this file, and the
 * two Chinese entries are reviewed separately - signing off one says nothing about the other.
 *
 * Proper nouns stay in English on purpose - SJSU Cares, Spartan Food Pantry, CalFresh, Sammy,
 * the product name - because they are what the student will see on signs and on SJSU's pages.
 */
export const zhHant: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: '請使用你的 SJSU 帳號登入以繼續。',
	signIn: '登入',
	signingIn: '正在登入…',
	signInNotCompleted: '登入未能完成。',
	signInNotStarted: '登入未能開始。',

	welcome:
		'你好！我是 Sammy。關於 SJSU 校園資源，你可以隨便問我：課業輔導、學業諮詢、身心健康、住宿協助等等。',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Sammy，SJSU Spartans 的吉祥物',
	expandSidebar: '展開側邊欄',
	collapseSidebar: '收合側邊欄',

	newChat: '新對話',
	chatHistory: '對話紀錄',
	recentChats: '最近的對話',
	renameChat: (title: string) => `重新命名${title}`,
	deleteChat: (title: string) => `刪除${title}`,
	deleteConfirm: (title: string) => `刪除「${title}」？此動作無法復原。`,
	save: '儲存',
	saving: '正在儲存…',
	cancel: '取消',
	delete: '刪除',
	deleting: '正在刪除…',
	opening: '正在開啟…',
	loadingChats: '正在載入你的對話…',
	noStoredChats: '你傳送的對話會儲存在這裡，並留在你的帳號中。',
	renameFailed: '無法重新命名該對話。',
	deleteFailed: '無法刪除該對話。',
	signedIn: '已登入',
	signOut: '登出',
	settings: '設定',
	closeNavigation: '關閉導覽',
	openChatHistory: '開啟對話紀錄',

	askSammy: '問 Sammy',
	composerPlaceholder: '問問課業輔導、諮詢、身心健康…',
	send: '傳送',
	yourMessage: '你的訊息',
	thinking: '思考中',
	waitingForSammy: '正在等待 Sammy 的回覆',
	stageRetrieving: '正在查詢校園資源',
	stageComposingCards: '正在整理校園資源',

	campusResources: '校園資源',
	campusResourcesFrom: (timestamp: string) => `${timestamp}的校園資源`,
	timeJustNow: '剛剛',
	timeMinutesAgo: (minutes: number) => `${minutes} 分鐘前`,
	timeHoursAgo: (hours: number) => `${hours} 小時前`,

	chatsLoadFailedWith: (message: string) => `無法載入你的對話：${message}`,
	chatsLoadFailed: '無法載入你的對話。',
	chatOpenFailedWith: (message: string) => `無法開啟該對話：${message}`,
	chatOpenFailed: '無法開啟該對話。',
	turnFailed: '連線 Sammy 時發生問題。聊天 API 正在執行嗎？',

	safetyContactsAria: '緊急求助聯絡方式',

	talkToPerson: '與真人交談',
	talkToPersonAria: '與 SJSU Cares 的工作人員交談',
	university: 'San José State University',
	caresClose: '關閉 SJSU Cares 資訊',
	caresOverview:
		'SJSU Cares 透過個案管理、轉介與後續追蹤，協助在基本生活需求上遇到困難的學生。',
	caresRequest: '申請協助',
	caresRequestHint: '聯絡個案管理員最快的方式',
	caresCall: (phone: string) => `致電 ${phone}`,
	caresEmail: (email: string) => `寄信至 ${email}`,
	caresHoursLabel: '服務時間',
	caresHoursValue: '週一至週五，上午 10 點至下午 4 點',
	caresOfficeLabel: '辦公室',
	caresRecommended: '為你的問題推薦',
	caresAllServices: 'SJSU Cares 的所有服務',
	caresDirectory: '員工名錄與完整聯絡方式',
	caresNote: '聯絡時請附上你的學號。',
	caresServices: {
		food: {
			title: '食物協助',
			description: '使用 Spartan Food Pantry，並取得申請 CalFresh 的協助。',
		},
		housing: {
			title: '住宿協助',
			description: '緊急住所、重新安置計畫以及找房支援。',
		},
		financial: {
			title: '經濟協助',
			description: '針對突發支出的緊急補助與財務諮詢。',
		},
		parenting: {
			title: '育兒的學生',
			description: '選課註冊協助、權益說明以及校園上的相關安排。',
		},
	},

	escalationAria: '寄給工作人員的郵件草稿',
	escalationHeadline: '把這封信寄給工作人員',
	escalationNote:
		'郵件會在你自己的郵件應用程式中開啟，回覆會直接回到你這裡。請確認是用你的學校信箱寄出的。',
	escalationTo: '收件者',
	escalationSubject: '主旨',
	escalationOpen: '在我的郵件應用程式中開啟',
	escalationCopied: '已複製',
	escalationCopy: '複製這則訊息',
	escalationClipboardBlocked:
		'你的瀏覽器不允許我們使用剪貼簿，所以內容已為你選取：複製後貼到一封新郵件裡。',
	escalationTooLong: '這份草稿太長，無法自動開啟你的郵件應用程式。請複製後貼到一封新郵件裡。',

	placeAria: '校園位置',
	placeDirections: '查看路線',
	placeDirectionsFor: (name: string) => `查看前往 ${name} 的路線`,
	placeMapCredit: '地圖資料 © OpenStreetMap 貢獻者',

	settingsClose: '關閉設定',
	close: '關閉',
	languageLabel: '語言',
	languageHint: '變更本應用程式本身的標籤和按鈕。',
	languageUnreviewed: '機器翻譯。SJSU 尚未審閱此措辭。',

	costSection: '運作成本',
	costThisConversation: '本次對話',
	costMessagesSoFar: (messages: string) => `目前 ${messages} 則訊息，依實際使用的 token 計價。`,
	costNothingMetered: '這次對話還沒有計量。從你在這裡送出的第一則訊息開始計算。',
	costMessagesSent: '已傳送訊息',
	costModelCalls: '模型呼叫次數',
	costInputTokens: '輸入 token',
	costOutputTokens: '輸出 token',
	costPerMessage: '每則訊息成本',
	costMonthOfUse: '一個月的使用量',
	costMessagesAMonth: '學生每月的訊息數',
	costMonthAtVolume: '該用量下的一個月',
	costRunsAtNoUse: '無人使用時的開銷',
	costNobodyAsking: '每個月，即使無人提問',
	costWhatOneAdds: '每多一則訊息增加的成本',
	costFootLead: '這些是估算，不是帳單。',
	costFootRest: 'AWS 公布的定價，乘以實測的 token 使用量。',
};

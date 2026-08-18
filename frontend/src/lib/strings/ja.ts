import type { Strings } from './en';

/**
 * Japanese. MACHINE-AUTHORED, NOT REVIEWED BY A JAPANESE SPEAKER. See es.ts for why a file in
 * this state ships; it is marked `reviewed: false` in lib/i18n.ts and said plainly to the
 * student in the settings panel.
 *
 * REVIEWER'S NOTES. です・ます throughout - polite, but not the 敬語 an office would use in a
 * formal notice, since this is an assistant talking to a student. Proper nouns are left in
 * English on purpose: SJSU Cares, Spartan Food Pantry, CalFresh, Sammy and the product name
 * are what the student will see on signs and on SJSU's own pages.
 */
export const ja: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: '続けるには SJSU のアカウントでサインインしてください。',
	signIn: 'サインイン',
	signingIn: 'サインインしています…',
	signInNotCompleted: 'サインインを完了できませんでした。',
	signInNotStarted: 'サインインを開始できませんでした。',

	welcome:
		'こんにちは！Sammy です。SJSU のキャンパスリソースについて何でも聞いてください。チュータリング、履修相談、健康サポート、住まいの支援など、いろいろ案内できます。',

	brandName: 'SJSU Student Success',
	sammyAlt: 'SJSU Spartans のマスコット、Sammy',
	expandSidebar: 'サイドバーを展開する',
	collapseSidebar: 'サイドバーを折りたたむ',

	newChat: '新しいチャット',
	chatHistory: 'チャット履歴',
	recentChats: '最近のチャット',
	renameChat: (title: string) => `${title} の名前を変更`,
	deleteChat: (title: string) => `${title} を削除`,
	deleteConfirm: (title: string) => `「${title}」を削除しますか？元に戻せません。`,
	save: '保存',
	saving: '保存しています…',
	cancel: 'キャンセル',
	delete: '削除',
	deleting: '削除しています…',
	opening: '開いています…',
	loadingChats: 'チャットを読み込んでいます…',
	noStoredChats: '送信したチャットはここに保存され、アカウントに残ります。',
	renameFailed: 'そのチャットの名前を変更できませんでした。',
	deleteFailed: 'そのチャットを削除できませんでした。',
	signedIn: 'サインイン中',
	signOut: 'サインアウト',
	settings: '設定',
	closeNavigation: 'ナビゲーションを閉じる',
	openChatHistory: 'チャット履歴を開く',

	askSammy: 'Sammy に聞く',
	composerPlaceholder: 'チュータリング、履修相談、健康について聞いてみましょう…',
	send: '送信',
	yourMessage: 'あなたのメッセージ',
	thinking: '考え中',
	waitingForSammy: 'Sammy の返答を待っています',
	stageRetrieving: 'キャンパスのリソースを調べています',
	stageComposingCards: 'キャンパスのリソースをまとめています',

	campusResources: 'キャンパスのリソース',
	campusResourcesFrom: (timestamp: string) => `${timestamp}のキャンパスのリソース`,
	timeJustNow: 'たった今',
	timeMinutesAgo: (minutes: number) => `${minutes}分前`,
	timeHoursAgo: (hours: number) => `${hours}時間前`,

	chatsLoadFailedWith: (message: string) => `チャットを読み込めませんでした: ${message}`,
	chatsLoadFailed: 'チャットを読み込めませんでした。',
	chatOpenFailedWith: (message: string) => `そのチャットを開けませんでした: ${message}`,
	chatOpenFailed: 'そのチャットを開けませんでした。',
	turnFailed: 'Sammy への接続で問題が起きました。チャット API は動いていますか？',

	safetyContactsAria: '緊急時の連絡先',

	talkToPerson: '担当者と話す',
	talkToPersonAria: 'SJSU Cares の担当者と話す',
	university: 'San José State University',
	caresClose: 'SJSU Cares の情報を閉じる',
	caresOverview:
		'SJSU Cares は、生活の基本的なことで困っている学生を、ケースマネジメント・他機関への紹介・その後のフォローで支援しています。',
	caresRequest: '支援を申し込む',
	caresRequestHint: 'ケースマネージャーに一番早く連絡する方法',
	caresCall: (phone: string) => `${phone} に電話する`,
	caresEmail: (email: string) => `${email} にメールする`,
	caresHoursLabel: '受付時間',
	caresHoursValue: '月曜 - 金曜、午前10時 - 午後4時',
	caresOfficeLabel: 'オフィス',
	caresRecommended: 'あなたの質問におすすめ',
	caresAllServices: 'SJSU Cares のすべてのサービス',
	caresDirectory: 'スタッフ一覧と連絡先の全リスト',
	caresNote: '連絡するときは学生 ID を書き添えてください。',
	caresServices: {
		food: {
			title: '食料の支援',
			description: 'Spartan Food Pantry の利用と CalFresh 申請のサポート。',
		},
		housing: {
			title: '住まいの支援',
			description: '緊急の住まい、住み替えプログラム、部屋探しのサポート。',
		},
		financial: {
			title: '経済的な支援',
			description: '予期しない出費のための緊急給付と家計の相談。',
		},
		parenting: {
			title: '子育て中の学生',
			description: '履修登録のサポート、権利についての案内、学内での配慮。',
		},
	},

	escalationAria: '担当者へ送るメールの下書き',
	escalationHeadline: 'これを担当者に送りましょう',
	escalationNote:
		'ご自身のメールアプリで開くので、返信は直接あなたに届きます。学校のメールアドレスから送信されるか確認してください。',
	escalationTo: '宛先',
	escalationSubject: '件名',
	escalationOpen: '自分のメールアプリで開く',
	escalationCopied: 'コピーしました',
	escalationCopy: 'メッセージをコピー',
	escalationClipboardBlocked:
		'ブラウザがクリップボードの使用を許可しなかったため、代わりにメッセージを選択しました。コピーして新しいメールに貼り付けてください。',
	escalationTooLong:
		'この下書きは長すぎるため、メールアプリを自動で開けません。コピーして新しいメールに貼り付けてください。',

	placeAria: 'キャンパス内の場所',
	placeDirections: 'ルートを見る',
	placeDirectionsFor: (name: string) => `${name} までのルートを見る`,
	placeMapCredit: '地図データ © OpenStreetMap コントリビューター',

	settingsClose: '設定を閉じる',
	close: '閉じる',
	languageLabel: '言語',
	languageHint: 'このアプリのラベルとボタンが変わります。',
	languageUnreviewed: '機械翻訳です。SJSU はまだこの表現を確認していません。',

	costSection: '運用にかかる費用',
	costThisConversation: 'この会話',
	costMessagesSoFar: (messages: string) =>
		`これまでに ${messages} 件のメッセージ。実際に使われたトークンから算出しています。`,
	costNothingMetered:
		'このチャットではまだ計測されていません。ここで送る最初のメッセージから数え始めます。',
	costMessagesSent: '送信したメッセージ',
	costModelCalls: 'モデル呼び出し',
	costInputTokens: '入力トークン',
	costOutputTokens: '出力トークン',
	costPerMessage: 'メッセージあたりの費用',
	costMonthOfUse: '1か月の利用',
	costMessagesAMonth: '月あたりの学生のメッセージ数',
	costMonthAtVolume: 'その利用量での1か月',
	costRunsAtNoUse: '利用がなくてもかかる分',
	costNobodyAsking: '誰も質問しない月でも',
	costWhatOneAdds: 'メッセージ1件が増やす分',
	costFootLead: 'これは概算であり、請求書ではありません。',
	costFootRest: 'AWS が公開している定価に、計測したトークン使用量を掛けた値です。',
};

import type { Strings } from './en';

/**
 * Korean. MACHINE-AUTHORED, NOT REVIEWED BY A KOREAN SPEAKER. See es.ts for why a file in
 * this state ships; it is marked `reviewed: false` in lib/i18n.ts and said plainly to the
 * student in the settings panel.
 *
 * REVIEWER'S NOTES. 해요체 throughout - polite but not the stiff 합쇼체 an institution would
 * use in a letter, since this is an assistant talking to a student. Proper nouns are left in
 * English on purpose: SJSU Cares, Spartan Food Pantry, CalFresh, Sammy and the product name
 * are what the student will see on signs and on SJSU's own pages.
 */
export const ko: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: '계속하려면 SJSU 계정으로 로그인하세요.',
	signIn: '로그인',
	signingIn: '로그인 중…',
	signInNotCompleted: '로그인을 완료하지 못했어요.',
	signInNotStarted: '로그인을 시작하지 못했어요.',

	welcome:
		'안녕하세요! 저는 Sammy예요. SJSU 캠퍼스 자원에 대해 무엇이든 물어보세요. 튜터링, 학업 상담, 건강, 주거 지원 등 다양하게 도와드릴게요.',

	brandName: 'SJSU Student Success',
	sammyAlt: 'SJSU Spartans 마스코트 Sammy',
	expandSidebar: '사이드바 펼치기',
	collapseSidebar: '사이드바 접기',

	newChat: '새 대화',
	chatHistory: '대화 기록',
	recentChats: '최근 대화',
	renameChat: (title: string) => `${title} 이름 바꾸기`,
	deleteChat: (title: string) => `${title} 삭제`,
	deleteConfirm: (title: string) => `“${title}”을(를) 삭제할까요? 되돌릴 수 없어요.`,
	save: '저장',
	saving: '저장 중…',
	cancel: '취소',
	delete: '삭제',
	deleting: '삭제 중…',
	opening: '여는 중…',
	loadingChats: '대화를 불러오는 중…',
	noStoredChats: '보낸 대화는 여기에 저장되고 계정에 남아 있어요.',
	renameFailed: '그 대화의 이름을 바꾸지 못했어요.',
	deleteFailed: '그 대화를 삭제하지 못했어요.',
	signedIn: '로그인됨',
	signOut: '로그아웃',
	settings: '설정',
	closeNavigation: '내비게이션 닫기',
	openChatHistory: '대화 기록 열기',

	askSammy: 'Sammy에게 물어보기',
	composerPlaceholder: '튜터링, 상담, 건강에 대해 물어보세요…',
	send: '보내기',
	yourMessage: '내 메시지',
	thinking: '생각 중',
	waitingForSammy: 'Sammy의 답변을 기다리는 중',
	stageRetrieving: '캠퍼스 자원을 찾아보는 중…',

	chatsLoadFailedWith: (message: string) => `대화를 불러오지 못했어요: ${message}`,
	chatsLoadFailed: '대화를 불러오지 못했어요.',
	chatOpenFailedWith: (message: string) => `그 대화를 열지 못했어요: ${message}`,
	chatOpenFailed: '그 대화를 열지 못했어요.',
	turnFailed: 'Sammy에 연결하는 중 문제가 생겼어요. 채팅 API가 실행 중인가요?',

	talkToPerson: '사람과 대화하기',
	talkToPersonAria: 'SJSU Cares의 담당자와 대화하기',
	university: 'San José State University',
	caresClose: 'SJSU Cares 정보 닫기',
	caresOverview:
		'SJSU Cares는 기본적인 생활에 어려움을 겪는 학생을 케이스 관리, 기관 연계, 사후 관리로 지원해요.',
	caresRequest: '지원 요청하기',
	caresRequestHint: '케이스 매니저에게 가장 빠르게 연락하는 방법',
	caresCall: (phone: string) => `${phone}로 전화하기`,
	caresEmail: (email: string) => `${email}로 이메일 보내기`,
	caresHoursLabel: '운영 시간',
	caresHoursValue: '월요일 - 금요일, 오전 10시 - 오후 4시',
	caresOfficeLabel: '사무실',
	caresRecommended: '질문에 맞는 추천',
	caresAllServices: 'SJSU Cares의 모든 서비스',
	caresDirectory: '직원 명단 및 전체 연락처',
	caresNote: '연락할 때 학생 ID를 함께 알려주세요.',
	caresServices: {
		food: {
			title: '식품 지원',
			description: 'Spartan Food Pantry 이용과 CalFresh 신청 도움.',
		},
		housing: {
			title: '주거 지원',
			description: '긴급 주거, 재정착 프로그램, 집 찾기 지원.',
		},
		financial: {
			title: '재정 지원',
			description: '예상치 못한 지출을 위한 긴급 지원금과 재정 상담.',
		},
		parenting: {
			title: '자녀를 둔 학생',
			description: '수강 신청 지원, 권리 안내, 캠퍼스 편의 지원.',
		},
	},

	settingsClose: '설정 닫기',
	close: '닫기',
	languageLabel: '언어',
	languageHint: '이 앱의 라벨과 버튼을 바꿔요. Sammy의 답변은 아직 번역되지 않아요.',
	languageUnreviewed: '기계 번역이에요. SJSU가 아직 이 표현을 검토하지 않았어요.',

	costSection: '운영 비용',
	costThisConversation: '이 대화',
	costMessagesSoFar: (messages: string) =>
		`지금까지 ${messages}개의 메시지, 실제 사용된 토큰 기준으로 계산했어요.`,
	costNothingMetered:
		'이 대화에서는 아직 측정된 것이 없어요. 여기서 보내는 첫 메시지부터 계산돼요.',
	costMessagesSent: '보낸 메시지',
	costModelCalls: '모델 호출',
	costInputTokens: '입력 토큰',
	costOutputTokens: '출력 토큰',
	costPerMessage: '메시지당 비용',
	costMonthOfUse: '한 달 사용량',
	costMessagesAMonth: '월별 학생 메시지 수',
	costMonthAtVolume: '그 사용량일 때의 한 달',
	costRunsAtNoUse: '사용이 없어도 드는 비용',
	costNobodyAsking: '아무도 묻지 않아도 매달',
	costWhatOneAdds: '메시지 하나가 더하는 비용',
	costFootLead: '이것은 추정치이고 청구서가 아니에요.',
	costFootRest: 'AWS가 공개한 정가에 측정된 토큰 사용량을 곱한 값이에요.',
};

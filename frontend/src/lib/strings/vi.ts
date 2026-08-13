import type { Strings } from './en';

/**
 * Vietnamese. MACHINE-AUTHORED, NOT REVIEWED BY A VIETNAMESE SPEAKER. See es.ts for why a
 * file in this state ships at all; the short version is that it is marked everywhere it
 * matters - `reviewed: false` in lib/i18n.ts, this comment, and a line in the settings panel
 * the student reads.
 *
 * REVIEWER'S NOTES. "bạn" throughout for the student, which is the register a campus service
 * would use with an undergraduate rather than the formal "quý vị". Proper nouns are left in
 * English on purpose - SJSU Cares, Spartan Food Pantry, CalFresh, Sammy, the product name -
 * because they are what the student will see on signs and on SJSU's own pages.
 */
export const vi: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: 'Đăng nhập bằng tài khoản SJSU của bạn để tiếp tục.',
	signIn: 'Đăng nhập',
	signingIn: 'Đang đăng nhập…',
	signInNotCompleted: 'Không thể hoàn tất việc đăng nhập.',
	signInNotStarted: 'Không thể bắt đầu việc đăng nhập.',

	welcome:
		'Xin chào! Mình là Sammy. Bạn có thể hỏi mình bất cứ điều gì về các nguồn hỗ trợ trong khuôn viên SJSU: dạy kèm, cố vấn học tập, sức khỏe tinh thần, hỗ trợ chỗ ở và nhiều hơn nữa.',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Sammy, linh vật của SJSU Spartans',
	expandSidebar: 'Mở rộng thanh bên',
	collapseSidebar: 'Thu gọn thanh bên',

	newChat: 'Cuộc trò chuyện mới',
	chatHistory: 'Lịch sử trò chuyện',
	recentChats: 'Trò chuyện gần đây',
	renameChat: (title: string) => `Đổi tên ${title}`,
	deleteChat: (title: string) => `Xóa ${title}`,
	deleteConfirm: (title: string) => `Xóa “${title}”? Thao tác này không thể hoàn tác.`,
	save: 'Lưu',
	saving: 'Đang lưu…',
	cancel: 'Hủy',
	delete: 'Xóa',
	deleting: 'Đang xóa…',
	opening: 'Đang mở…',
	loadingChats: 'Đang tải các cuộc trò chuyện của bạn…',
	noStoredChats:
		'Những cuộc trò chuyện bạn gửi sẽ được lưu ở đây và nằm trong tài khoản của bạn.',
	renameFailed: 'Không thể đổi tên cuộc trò chuyện đó.',
	deleteFailed: 'Không thể xóa cuộc trò chuyện đó.',
	signedIn: 'Đã đăng nhập',
	signOut: 'Đăng xuất',
	settings: 'Cài đặt',
	closeNavigation: 'Đóng thanh điều hướng',
	openChatHistory: 'Mở lịch sử trò chuyện',

	askSammy: 'Hỏi Sammy',
	composerPlaceholder: 'Hỏi về dạy kèm, cố vấn, sức khỏe…',
	send: 'Gửi',
	yourMessage: 'Tin nhắn của bạn',
	thinking: 'Đang suy nghĩ',
	waitingForSammy: 'Đang chờ Sammy trả lời',
	stageRetrieving: 'Đang tìm trong các nguồn hỗ trợ của trường',
	stageComposingCards: 'Đang tập hợp các nguồn hỗ trợ của trường',

	campusResources: 'Nguồn hỗ trợ của trường',
	campusResourcesFrom: (timestamp: string) => `Nguồn hỗ trợ của trường từ ${timestamp}`,
	timeJustNow: 'Vừa xong',
	timeMinutesAgo: (minutes: number) => `${minutes} phút trước`,
	timeHoursAgo: (hours: number) => `${hours} giờ trước`,

	chatsLoadFailedWith: (message: string) =>
		`Không thể tải các cuộc trò chuyện của bạn: ${message}`,
	chatsLoadFailed: 'Không thể tải các cuộc trò chuyện của bạn.',
	chatOpenFailedWith: (message: string) => `Không thể mở cuộc trò chuyện đó: ${message}`,
	chatOpenFailed: 'Không thể mở cuộc trò chuyện đó.',
	turnFailed: 'Đã xảy ra lỗi khi kết nối với Sammy. API trò chuyện có đang chạy không?',

	safetyContactsAria: 'Liên hệ khẩn cấp',

	talkToPerson: 'Nói chuyện với một người',
	talkToPersonAria: 'Nói chuyện với một người ở SJSU Cares',
	university: 'San José State University',
	caresClose: 'Đóng thông tin SJSU Cares',
	caresOverview:
		'SJSU Cares hỗ trợ sinh viên gặp khó khăn về nhu cầu thiết yếu, thông qua quản lý hồ sơ, giới thiệu dịch vụ và theo dõi sau đó.',
	caresRequest: 'Yêu cầu hỗ trợ',
	caresRequestHint: 'Cách nhanh nhất để liên hệ với một chuyên viên phụ trách hồ sơ',
	caresCall: (phone: string) => `Gọi ${phone}`,
	caresEmail: (email: string) => `Gửi email tới ${email}`,
	caresHoursLabel: 'Giờ làm việc',
	caresHoursValue: 'Thứ Hai - Thứ Sáu, 10 giờ sáng - 4 giờ chiều',
	caresOfficeLabel: 'Văn phòng',
	caresRecommended: 'Được đề xuất cho câu hỏi của bạn',
	caresAllServices: 'Tất cả dịch vụ của SJSU Cares',
	caresDirectory: 'Danh bạ nhân viên và danh sách liên hệ đầy đủ',
	caresNote: 'Hãy kèm theo mã số sinh viên của bạn khi liên hệ.',
	caresServices: {
		food: {
			title: 'Hỗ trợ thực phẩm',
			description: 'Sử dụng Spartan Food Pantry và được giúp nộp đơn CalFresh.',
		},
		housing: {
			title: 'Hỗ trợ chỗ ở',
			description: 'Chỗ ở khẩn cấp, chương trình tái định cư và hỗ trợ tìm nhà.',
		},
		financial: {
			title: 'Hỗ trợ tài chính',
			description: 'Trợ cấp khẩn cấp và tư vấn tài chính cho các chi phí bất ngờ.',
		},
		parenting: {
			title: 'Sinh viên đang nuôi con',
			description:
				'Hỗ trợ ghi danh, hướng dẫn về quyền lợi và các điều chỉnh phù hợp trong trường.',
		},
	},

	escalationAria: 'Thư nháp gửi tới một người',
	escalationHeadline: 'Gửi nội dung này tới một người',
	escalationNote:
		'Thư sẽ mở trong ứng dụng email của chính bạn, nên hồi âm sẽ đến thẳng với bạn. Hãy kiểm tra lại rằng thư được gửi từ địa chỉ email trường của bạn.',
	escalationTo: 'Đến',
	escalationSubject: 'Tiêu đề',
	escalationOpen: 'Mở trong ứng dụng email của tôi',
	escalationCopied: 'Đã sao chép',
	escalationCopy: 'Sao chép tin nhắn',
	escalationClipboardBlocked:
		'Trình duyệt của bạn không cho phép dùng bộ nhớ tạm, nên nội dung đã được bôi đen sẵn: hãy sao chép và dán vào một email mới.',
	escalationTooLong:
		'Thư nháp này quá dài để tự động mở ứng dụng email của bạn. Hãy sao chép và dán vào một email mới.',

	settingsClose: 'Đóng cài đặt',
	close: 'Đóng',
	languageLabel: 'Ngôn ngữ',
	languageHint: 'Thay đổi nhãn và nút của ứng dụng này.',
	languageUnreviewed: 'Dịch tự động. SJSU chưa duyệt lại cách diễn đạt này.',

	costSection: 'Chi phí vận hành',
	costThisConversation: 'Cuộc trò chuyện này',
	costMessagesSoFar: (messages: string) =>
		`${messages} tin nhắn cho đến nay, được tính theo số token thực sự đã dùng.`,
	costNothingMetered:
		'Chưa đo được gì trong cuộc trò chuyện này. Việc tính bắt đầu từ tin nhắn đầu tiên bạn gửi ở đây.',
	costMessagesSent: 'Tin nhắn đã gửi',
	costModelCalls: 'Lượt gọi mô hình',
	costInputTokens: 'Token đầu vào',
	costOutputTokens: 'Token đầu ra',
	costPerMessage: 'Chi phí mỗi tin nhắn',
	costMonthOfUse: 'Một tháng sử dụng',
	costMessagesAMonth: 'Số tin nhắn của sinh viên mỗi tháng',
	costMonthAtVolume: 'Một tháng ở mức đó',
	costRunsAtNoUse: 'Chạy khi không ai dùng',
	costNobodyAsking: 'Mỗi tháng, dù không ai hỏi',
	costWhatOneAdds: 'Một tin nhắn làm tăng thêm bao nhiêu',
	costFootLead: 'Đây là ước tính, không phải hóa đơn.',
	costFootRest: 'Giá niêm yết công bố của AWS, nhân với mức sử dụng token đã đo được.',
};

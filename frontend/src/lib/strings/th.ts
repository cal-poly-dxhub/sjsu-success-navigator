import type { Strings } from './en';

/**
 * Thai. MACHINE-AUTHORED, NOT REVIEWED BY A THAI SPEAKER. See es.ts for why a file in this
 * state ships; it is marked `reviewed: false` in lib/i18n.ts and said plainly to the student
 * in the settings panel.
 *
 * REVIEWER'S NOTES. NO POLITE PARTICLES anywhere - no ครับ, no ค่ะ - which is the standard
 * for Thai interface copy and is also the only choice available: the particle is gendered
 * after the speaker, and Sammy is a mascot with no gender to pick one from. Politeness is
 * carried by โปรด and กรุณา on the few strings that ask something of the student instead.
 * Thai writes without spaces between words, so the spaces in these strings are phrase
 * boundaries and are load-bearing for line breaking; do not tidy them away. Sentences take
 * no full stop.
 *
 * Proper nouns stay in English on purpose - SJSU Cares, Spartan Food Pantry, CalFresh, Sammy,
 * the product name - because they are what the student will see on signs and on SJSU's pages.
 */
export const th: Strings = {
	appName: 'Student Success Navigator',
	signInSubtitle: 'เข้าสู่ระบบด้วยบัญชี SJSU ของคุณเพื่อดำเนินการต่อ',
	signIn: 'เข้าสู่ระบบ',
	signingIn: 'กำลังเข้าสู่ระบบ…',
	signInNotCompleted: 'ไม่สามารถเข้าสู่ระบบให้เสร็จสมบูรณ์ได้',
	signInNotStarted: 'ไม่สามารถเริ่มการเข้าสู่ระบบได้',

	welcome:
		'สวัสดี! เราคือ Sammy ถามเราได้ทุกเรื่องเกี่ยวกับแหล่งช่วยเหลือในมหาวิทยาลัย SJSU ทั้งการติวหนังสือ การให้คำปรึกษาด้านการเรียน สุขภาพกายและใจ ความช่วยเหลือเรื่องที่พัก และอีกมากมาย',

	brandName: 'SJSU Student Success',
	sammyAlt: 'Sammy มาสคอตของ SJSU Spartans',
	expandSidebar: 'ขยายแถบด้านข้าง',
	collapseSidebar: 'ย่อแถบด้านข้าง',

	newChat: 'แชทใหม่',
	chatHistory: 'ประวัติการแชท',
	recentChats: 'แชทล่าสุด',
	renameChat: (title: string) => `เปลี่ยนชื่อ ${title}`,
	deleteChat: (title: string) => `ลบ ${title}`,
	deleteConfirm: (title: string) => `ลบ “${title}” ใช่ไหม การกระทำนี้ย้อนกลับไม่ได้`,
	save: 'บันทึก',
	saving: 'กำลังบันทึก…',
	cancel: 'ยกเลิก',
	delete: 'ลบ',
	deleting: 'กำลังลบ…',
	opening: 'กำลังเปิด…',
	loadingChats: 'กำลังโหลดแชทของคุณ…',
	noStoredChats: 'แชทที่คุณส่งจะถูกบันทึกไว้ที่นี่ และอยู่ในบัญชีของคุณ',
	renameFailed: 'ไม่สามารถเปลี่ยนชื่อแชทนั้นได้',
	deleteFailed: 'ไม่สามารถลบแชทนั้นได้',
	signedIn: 'เข้าสู่ระบบแล้ว',
	signOut: 'ออกจากระบบ',
	settings: 'การตั้งค่า',
	closeNavigation: 'ปิดเมนูนำทาง',
	openChatHistory: 'เปิดประวัติการแชท',

	askSammy: 'ถาม Sammy',
	composerPlaceholder: 'ถามเรื่องการติว การให้คำปรึกษา สุขภาพ…',
	send: 'ส่ง',
	yourMessage: 'ข้อความของคุณ',
	thinking: 'กำลังคิด',
	waitingForSammy: 'กำลังรอคำตอบจาก Sammy',
	stageRetrieving: 'กำลังค้นหาในแหล่งช่วยเหลือของมหาวิทยาลัย',
	stageComposingCards: 'กำลังรวบรวมแหล่งช่วยเหลือของมหาวิทยาลัย',

	campusResources: 'แหล่งช่วยเหลือในมหาวิทยาลัย',
	campusResourcesFrom: (timestamp: string) =>
		`แหล่งช่วยเหลือในมหาวิทยาลัย จาก ${timestamp}`,
	timeJustNow: 'เมื่อสักครู่',
	timeMinutesAgo: (minutes: number) => `${minutes} นาทีที่แล้ว`,
	timeHoursAgo: (hours: number) => `${hours} ชั่วโมงที่แล้ว`,

	chatsLoadFailedWith: (message: string) => `ไม่สามารถโหลดแชทของคุณได้: ${message}`,
	chatsLoadFailed: 'ไม่สามารถโหลดแชทของคุณได้',
	chatOpenFailedWith: (message: string) => `ไม่สามารถเปิดแชทนั้นได้: ${message}`,
	chatOpenFailed: 'ไม่สามารถเปิดแชทนั้นได้',
	turnFailed: 'เกิดข้อผิดพลาดขณะติดต่อ Sammy API ของแชททำงานอยู่หรือไม่',

	safetyContactsAria: 'ช่องทางติดต่อฉุกเฉิน',

	talkToPerson: 'คุยกับเจ้าหน้าที่',
	talkToPersonAria: 'คุยกับเจ้าหน้าที่ของ SJSU Cares',
	university: 'San José State University',
	caresClose: 'ปิดข้อมูล SJSU Cares',
	caresOverview:
		'SJSU Cares ช่วยเหลือนักศึกษาที่มีปัญหาด้านปัจจัยพื้นฐาน ด้วยการดูแลเป็นรายกรณี การส่งต่อไปยังหน่วยงานที่เกี่ยวข้อง และการติดตามผล',
	caresRequest: 'ขอความช่วยเหลือ',
	caresRequestHint: 'วิธีที่เร็วที่สุดในการติดต่อผู้ดูแลกรณี',
	caresCall: (phone: string) => `โทร ${phone}`,
	caresEmail: (email: string) => `ส่งอีเมลถึง ${email}`,
	caresHoursLabel: 'เวลาทำการ',
	caresHoursValue: 'จันทร์ - ศุกร์ 10.00 - 16.00 น.',
	caresOfficeLabel: 'สำนักงาน',
	caresRecommended: 'แนะนำสำหรับคำถามของคุณ',
	caresAllServices: 'บริการทั้งหมดของ SJSU Cares',
	caresDirectory: 'รายชื่อเจ้าหน้าที่และช่องทางติดต่อทั้งหมด',
	caresNote: 'โปรดแจ้งรหัสนักศึกษาของคุณเมื่อติดต่อ',
	caresServices: {
		food: {
			title: 'ความช่วยเหลือด้านอาหาร',
			description: 'เข้าใช้ Spartan Food Pantry และรับความช่วยเหลือในการยื่นขอ CalFresh',
		},
		housing: {
			title: 'ความช่วยเหลือด้านที่พัก',
			description: 'ที่พักฉุกเฉิน โครงการจัดหาที่พักใหม่ และการช่วยหาที่พัก',
		},
		financial: {
			title: 'ความช่วยเหลือด้านการเงิน',
			description: 'เงินช่วยเหลือฉุกเฉินและคำปรึกษาด้านการเงินสำหรับค่าใช้จ่ายที่ไม่คาดคิด',
		},
		parenting: {
			title: 'นักศึกษาที่มีบุตร',
			description:
				'ความช่วยเหลือเรื่องการลงทะเบียนเรียน คำแนะนำเรื่องสิทธิ และการอำนวยความสะดวกในมหาวิทยาลัย',
		},
	},

	escalationAria: 'ร่างอีเมลถึงเจ้าหน้าที่',
	escalationHeadline: 'ส่งข้อความนี้ถึงเจ้าหน้าที่',
	escalationNote:
		'ข้อความจะเปิดในแอปอีเมลของคุณเอง คำตอบจึงกลับมาถึงคุณโดยตรง โปรดตรวจสอบว่าส่งจากอีเมลของมหาวิทยาลัย',
	escalationTo: 'ถึง',
	escalationSubject: 'เรื่อง',
	escalationOpen: 'เปิดในแอปอีเมลของฉัน',
	escalationCopied: 'คัดลอกแล้ว',
	escalationCopy: 'คัดลอกข้อความ',
	escalationClipboardBlocked:
		'เบราว์เซอร์ของคุณไม่อนุญาตให้ใช้คลิปบอร์ด ข้อความจึงถูกเลือกไว้ให้แล้ว กรุณาคัดลอกแล้ววางในอีเมลฉบับใหม่',
	escalationTooLong:
		'ร่างนี้ยาวเกินกว่าจะเปิดแอปอีเมลของคุณโดยอัตโนมัติ กรุณาคัดลอกแล้ววางในอีเมลฉบับใหม่',

	settingsClose: 'ปิดการตั้งค่า',
	close: 'ปิด',
	languageLabel: 'ภาษา',
	languageHint: 'เปลี่ยนป้ายกำกับและปุ่มของแอปนี้',
	languageUnreviewed: 'แปลด้วยเครื่อง SJSU ยังไม่ได้ตรวจทานถ้อยคำนี้',

	costSection: 'ค่าใช้จ่ายในการให้บริการ',
	costThisConversation: 'การสนทนานี้',
	costMessagesSoFar: (messages: string) =>
		`${messages} ข้อความจนถึงตอนนี้ คิดราคาจากโทเคนที่ใช้จริง`,
	costNothingMetered: 'ยังไม่มีการวัดค่าใช้จ่ายในแชทนี้ จะเริ่มนับจากข้อความแรกที่คุณส่งที่นี่',
	costMessagesSent: 'ข้อความที่ส่งแล้ว',
	costModelCalls: 'จำนวนครั้งที่เรียกใช้โมเดล',
	costInputTokens: 'โทเคนขาเข้า',
	costOutputTokens: 'โทเคนขาออก',
	costPerMessage: 'ค่าใช้จ่ายต่อข้อความ',
	costMonthOfUse: 'การใช้งานหนึ่งเดือน',
	costMessagesAMonth: 'ข้อความจากนักศึกษาต่อเดือน',
	costMonthAtVolume: 'หนึ่งเดือนที่ปริมาณดังกล่าว',
	costRunsAtNoUse: 'ค่าใช้จ่ายเมื่อไม่มีการใช้งาน',
	costNobodyAsking: 'ทุกเดือน แม้ไม่มีใครถาม',
	costWhatOneAdds: 'ข้อความหนึ่งเพิ่มค่าใช้จ่ายเท่าไร',
	costFootLead: 'ตัวเลขเหล่านี้เป็นการประมาณ ไม่ใช่ใบเรียกเก็บเงิน',
	costFootRest: 'ราคาตามที่ AWS ประกาศ คูณด้วยปริมาณโทเคนที่วัดได้',
};

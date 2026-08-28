/** Gap left above whatever we anchor to, clearing the fixed header band. */
const TOP_GAP_REM = 5.25;

export function scrollElementToTop(target: HTMLElement, reduceMotion: boolean) {
	const remPx =
		Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
	const top = window.scrollY + target.getBoundingClientRect().top - TOP_GAP_REM * remPx;
	window.scrollTo({
		top: Math.max(0, top),
		behavior: reduceMotion ? 'auto' : 'smooth',
	});
}

export function scrollToActiveTurn(reduceMotion: boolean) {
	const target = document.getElementById('active-conversation-turn');
	if (!target) return;
	scrollElementToTop(target, reduceMotion);
}

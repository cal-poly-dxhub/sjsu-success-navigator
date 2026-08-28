import type { Language, Strings } from './i18n';

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/** Hybrid label: relative under 24h, short date otherwise. */
export function formatBatchTimestamp(
	createdAt: number,
	t: Strings,
	language: Language,
	now = Date.now(),
): string {
	const diff = Math.max(0, now - createdAt);

	if (diff < MINUTE_MS) return t.timeJustNow;

	if (diff < HOUR_MS) {
		const minutes = Math.floor(diff / MINUTE_MS);
		return t.timeMinutesAgo(minutes);
	}

	if (diff < DAY_MS) {
		const hours = Math.floor(diff / HOUR_MS);
		return t.timeHoursAgo(hours);
	}

	const date = new Date(createdAt);
	const sameYear = date.getFullYear() === new Date(now).getFullYear();

	return date.toLocaleDateString(language, {
		month: 'short',
		day: 'numeric',
		...(sameYear ? {} : { year: 'numeric' }),
	});
}

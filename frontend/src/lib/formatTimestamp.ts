import type { Language, Strings } from './i18n';

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/**
 * Hybrid label: relative under 24h, short date otherwise.
 *
 * THE STRINGS AND THE LOCALE ARE PARAMETERS, not module-level reads, even though there is
 * exactly one caller and it could have read both itself. Two reasons. The relative half is
 * chrome like any other and belongs in strings/, where a reviewer sees it beside the label it
 * gets interpolated into (StatementStack builds "Campus resources from 5m ago" out of both
 * halves, so a translated wrapper around an English "5m ago" would read worse than leaving
 * both in English). The date half is the browser's own formatter and needs the language
 * explicitly: `toLocaleDateString(undefined, ...)` follows the BROWSER's locale, so a student
 * who picked Thai on an en-US laptop would get a Thai label wrapped around an English date.
 * Passing the chosen language is what makes the picker reach this string at all.
 */
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

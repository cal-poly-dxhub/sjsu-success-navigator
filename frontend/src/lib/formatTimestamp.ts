const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/** Hybrid label: relative under 24h, short date otherwise. */
export function formatBatchTimestamp(createdAt: number, now = Date.now()): string {
	const diff = Math.max(0, now - createdAt);

	if (diff < MINUTE_MS) return 'Just now';

	if (diff < HOUR_MS) {
		const minutes = Math.floor(diff / MINUTE_MS);
		return `${minutes}m ago`;
	}

	if (diff < DAY_MS) {
		const hours = Math.floor(diff / HOUR_MS);
		return `${hours}h ago`;
	}

	const date = new Date(createdAt);
	const sameYear = date.getFullYear() === new Date(now).getFullYear();

	return date.toLocaleDateString(undefined, {
		month: 'short',
		day: 'numeric',
		...(sameYear ? {} : { year: 'numeric' }),
	});
}

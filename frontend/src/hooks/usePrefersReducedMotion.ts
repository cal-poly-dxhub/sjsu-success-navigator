import { useEffect, useState } from 'react';

export function usePrefersReducedMotion(): boolean {
	// Read on the first render, not in the effect. Every consumer is a `client:only`
	// island, so there is no server render to mismatch, and a component that decides
	// whether to animate at mount - the card deck does - would otherwise paint one
	// animated frame before the effect corrected it.
	const [reduced, setReduced] = useState(
		() =>
			typeof window !== 'undefined' &&
			window.matchMedia('(prefers-reduced-motion: reduce)').matches,
	);

	useEffect(() => {
		const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
		const update = () => setReduced(mq.matches);
		update();
		mq.addEventListener('change', update);
		return () => mq.removeEventListener('change', update);
	}, []);

	return reduced;
}

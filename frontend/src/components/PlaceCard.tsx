import type { PlaceCard as PlaceCardData } from '../types/chat';
import { PressableButton } from './PressableButton';
import { useStrings } from '../lib/i18n';
import './PlaceCard.css';

/** Where a place is: a map of it, its name, the line under it, and a way to walk there. */

type PlaceCardProps = {
	place: PlaceCardData;
};

export function PlaceCard({ place }: PlaceCardProps) {
	const t = useStrings();
	const mapImageUrl = place.mapImageUrl?.trim();

	return (
		<section className="place-card" aria-label={t.placeAria}>
			{mapImageUrl ? (
				<figure className="place-card__figure">
					<img
						className="place-card__image"
						src={mapImageUrl}
						/* Empty alt: the address is the next element, so "map of ..." would say
						 * it twice. */
						alt=""
						/* The intrinsic size of the committed render. */
						width={640}
						height={480}
						loading="lazy"
						decoding="async"
					/>
				</figure>
			) : null}

			<div className="place-card__head">
				{/* The name and the address as SJSU publishes them, untranslated and
				 * unabbreviated. */}
				<h2 className="place-card__name">{place.name}</h2>
				<p className="place-card__address">{place.address}</p>
			</div>

			<div className="place-card__actions">
				<PressableButton
					variant="secondary"
					href={place.directionsUrl}
					className="place-card__directions"
					aria-label={t.placeDirectionsFor(place.name)}
				>
					{t.placeDirections}
				</PressableButton>
			</div>

			{/* The credit the tile licence asks for.  */}
			{mapImageUrl ? (
				<p className="place-card__credit">{t.placeMapCredit}</p>
			) : null}
		</section>
	);
}

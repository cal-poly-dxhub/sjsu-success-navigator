import type { PlaceCard as PlaceCardData } from '../types/chat';
import { PressableButton } from './PressableButton';
import { useStrings } from '../lib/i18n';
import './PlaceCard.css';

/**
 * Where a place is: a map of it, its name, the line under it, and a way to walk there.
 *
 * EVERY VALUE ON SCREEN IS THE SERVER'S. The model named a place from a fixed catalogue and
 * wrote nothing else (app/places.py); the name, the address, the map and the link were all
 * attached server-side. Nothing in this file builds, completes or corrects an address - it
 * renders four strings.
 *
 * THE MAP IS OURS, WHICH IS THE WHOLE POINT. It is a picture rendered from OpenStreetMap
 * tiles at build time and committed to frontend/public/places/, served by the same
 * distribution as this page. So a turn arriving makes NO request to any third party - not to
 * Google, not to a tile server, not to anyone - and the map is on screen immediately rather
 * than behind a button the student has to know to press. The earlier design used a
 * click-to-load Google embed for exactly the privacy reason this one does not need.
 *
 * NO GOOGLE MAPS API, ANYWHERE. There is no key in this repo and no Cloud project behind it.
 * The one Google surface left is the directions button, a plain link to google.com/maps that
 * needs no key and requests nothing until it is pressed.
 *
 * A CARD WITHOUT A MAP IS A WHOLE CARD. `mapImageUrl` is absent when a catalogue entry's
 * building has not been rendered yet. The name, the address and the directions link are the
 * answer to "where is it?"; the map is the part that makes it quick. Its absence is not an
 * error state and says nothing to the student.
 *
 * TWO LANGUAGES ON ONE PANEL, the same split EscalationDraft makes. The chrome - the region
 * label, the button, the credit line - comes from the string catalogues and follows the
 * student's chosen language. The NAME and the ADDRESS do not: they are what SJSU puts on the
 * door and on the sign, and a student who repeats a translated building name at a front desk
 * will not be understood (app/prompts.py tells the model the same thing).
 */

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
						/* Empty alt deliberately: the address is the next element in the
						   document, so a screen reader announcing "map of ..." would say the
						   same thing twice. The map is the fast path for people who read it,
						   not a carrier of information the text lacks. */
						alt=""
						/* The intrinsic size of the committed render. Given so the browser
						   reserves the right box before the bytes arrive and nothing below
						   the card jumps when it lands. */
						width={640}
						height={480}
						loading="lazy"
						decoding="async"
					/>
				</figure>
			) : null}

			<div className="place-card__head">
				{/* The name and the address as SJSU publishes them, untranslated and
				    unabbreviated. Selectable, because somebody copies an address. */}
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

			{/* The credit the tile licence asks for. It is drawn into the image as well, so it
			    survives the file being viewed on its own; this copy is the one a screen reader
			    and a text selection can reach. */}
			{mapImageUrl ? (
				<p className="place-card__credit">{t.placeMapCredit}</p>
			) : null}
		</section>
	);
}

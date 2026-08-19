"""The campus location card: the model names a place, the server owns where it is.

THE SAME SPLIT THE CARDS AND THE SAFETY PANEL ALREADY MAKE. A card cites `ref="2"` and the
server attaches the URL; a safety block carries keys and the table attaches the numbers.
Here the model writes `<place>career-center</place>` and this module attaches the name, the
address, the map and the directions link. A model-authored address or map URL is not
validated and rejected - there is nowhere in the shape to put one, because the block's whole
content is a key.

AN UNLISTED PLACE YIELDS NO CARD. Not a guessed one, and not one whose "location" is really
a search for whatever the model typed: a card that sends a student to a door we cannot name
is worse than the prose answer they would have got anyway. An unknown key is dropped with a
WARNING and the reply keeps its cards and its prose. The roster the prompt shows the model
is built from this table (place_roster_for_prompt), the same way the safety roster is, so a
key the model is taught always resolves and the only key that can miss is one it invented.

TWO TABLES, BECAUSE SIXTEEN OFFICES SIT IN FIVE BUILDINGS. Four of these places are inside
Clark Hall and five inside the Student Services Center, so the coordinate and the map belong
to the BUILDING and the room number belongs to the place. That is what stops five near
identical images and five chances to mis-key one of them.

BOTH TABLES ARE data/places.csv AND data/buildings.csv, read at import through
app/campus_data.py. They are not written out here any more, because the SJSU Cares address
was ALSO written out in frontend/src/lib/sjsuCares.ts and nothing compared the two: the map
card and the "Talk to a person" panel could disagree inside one app with every test passing.
The frontend now reads the same rows through a module regenerated on every build. See
data/README.md, which is the file somebody moving an office is meant to open.

NO GOOGLE MAPS API IS INVOLVED, AND NOT ONE REQUEST LEAVES FOR A THIRD PARTY. There is no
key in this repo, no Cloud project and no billing account:

  - The map is a PICTURE WE RENDER AND SERVE OURSELVES, built from OpenStreetMap tiles by
    scripts/render_place_maps.py and committed under frontend/public/places/. It comes off
    the same CloudFront distribution as the app, so a student reading an answer contacts
    nobody but us. Google's Static Maps endpoint returns exactly this picture and was
    rejected on both counts: it requires a key and a billing account, and its terms forbid
    storing what it returns, which is the whole idea here.
  - The directions link is a Maps URL (`google.com/maps/dir/?api=1&destination=...`), which
    is a plain link anybody may build and which only opens when a student presses it.
    `directions_destination` is a CURATED QUERY rather than the coordinate below it: the
    coordinate is more precise, but Maps then labels the destination with six decimal places
    instead of the building's name, and a student checking they are walking to the right
    place should see "Clark Hall".

WHERE THE FACTS COME FROM. Addresses are eval/ground-truth.yaml, the repo's verified record
of what SJSU publishes - read off the live pages on 2026-08-10, never LLM-generated. Each
place names its pair id in `ground_truth_ids`. Coordinates were resolved separately
(2026-08-17) and every one was checked against OpenStreetMap's own data: the named building
is within 45 m of the point, and the rendered tile prints that name under the pin. Two of
them needed a human, and both are written into their row's `note` column, because "the search
engine said so" is how a student ends up at the wrong door.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote_plus

from campus_data import load_keyed, parse_coordinate
from models import PlaceCard

logger = logging.getLogger(__name__)

# The base of a keyless Maps directions link. `api=1` is the documented, stable form; the
# destination is appended percent-encoded. No key parameter exists in this URL and none can
# be added by anything below.
_DIRECTIONS_BASE = "https://www.google.com/maps/dir/?api=1&destination="

# Where the rendered maps sit in the deployed site. A ROOT-RELATIVE PATH, not a URL: the
# images are served by the same CloudFront distribution as the page that shows them, so
# there is no host here to get wrong and no origin for a browser to reach out to.
_MAP_IMAGE_BASE = "/places/"


@dataclass(frozen=True)
class CampusBuilding:
    """One building: what it is called, where it is, and the map rendered from that point.

    `lat`/`lon` are the render point AND the verification record. Anything editing them has
    to re-run scripts/render_place_maps.py, because the committed image is a picture of this
    exact coordinate.
    """

    name: str
    lat: float
    lon: float


# The five buildings the catalogue points at, from data/buildings.csv. Read at import, so a
# malformed row is a cold start that fails with the line number rather than a card that
# renders without its map. `scripts/render_place_maps.py` reads this same table and writes one
# image per key into frontend/public/places/.
_BUILDINGS_FILE = "buildings.csv"


def _load_buildings() -> dict[str, CampusBuilding]:
    rows = load_keyed(_BUILDINGS_FILE, "key", ("name", "lat", "lon"), optional=("note",))
    return {
        key: CampusBuilding(
            name=row["name"],
            lat=parse_coordinate(row["lat"], name=_BUILDINGS_FILE, key=key, column="lat"),
            lon=parse_coordinate(row["lon"], name=_BUILDINGS_FILE, key=key, column="lon"),
        )
        for key, row in rows.items()
    }


CAMPUS_BUILDINGS: dict[str, CampusBuilding] = _load_buildings()


@dataclass(frozen=True)
class CampusPlace:
    """One catalogue entry: what the card says, and which building it is inside.

    `when` is the one-line description the model reads in the roster, exactly as
    SafetyResource.when works. `address` is the WAYFINDING half of the ground-truth line with
    the office's own name lifted out of it, because the card prints the name above the
    address and repeating it there reads as a stutter.
    """

    name: str
    address: str
    building: str
    directions_destination: str
    when: str


# Every place a student asks "where is it?" about that the repo can answer, from
# data/places.csv. `building` is a foreign key into data/buildings.csv and
# test_every_place_names_a_building_that_exists is what keeps it one; `ground_truth_ids` names
# the eval/ground-truth.yaml pair each address was verified against, and `note` carries the
# entries that took a person to settle. Neither reaches the student, so neither is loaded
# here - they are provenance for whoever edits the row next, and the file is where an editor
# will actually see them.
_PLACES_FILE = "places.csv"


def _load_places() -> dict[str, CampusPlace]:
    rows = load_keyed(
        _PLACES_FILE,
        "key",
        ("name", "building", "address", "directions_destination", "when"),
        optional=("ground_truth_ids", "note"),
    )
    return {
        key: CampusPlace(
            name=row["name"],
            address=row["address"],
            building=row["building"],
            directions_destination=row["directions_destination"],
            when=row["when"],
        )
        for key, row in rows.items()
    }


CAMPUS_PLACES: dict[str, CampusPlace] = _load_places()


def place_roster_for_prompt() -> list[tuple[str, str]]:
    """(key, when) pairs for the system prompt, in table order.

    The prompt's key list and the resolver's table are the same dict, the way the safety
    roster and app/safety.py's table are. That is what makes "a key the model is taught
    always resolves" a property rather than a promise.
    """
    return [(key, place.when) for key, place in CAMPUS_PLACES.items()]


def directions_url(place: CampusPlace) -> str:
    """A keyless Google Maps directions link for one catalogue entry.

    No API key, no Cloud project, no billing: this is the public Maps URL form, and the only
    variable part of it is a destination string this file owns. Nothing the model writes
    reaches it, and nothing is requested until a student presses the button.
    """
    return _DIRECTIONS_BASE + quote_plus(place.directions_destination)


def map_image_url(place: CampusPlace) -> str | None:
    """The committed map for this place's building, or None if the building has no entry.

    None is a COMPLETE CARD rather than a broken one: the panel shows the name, the address
    and the directions link, which is the answer to "where is it?". That is the state a new
    catalogue entry lands in before anybody has rendered its building, and the frontend
    treats it as ordinary rather than as an error.
    """
    building = CAMPUS_BUILDINGS.get(place.building)
    if building is None:
        logger.warning(
            "Campus place building %r is not in CAMPUS_BUILDINGS; the card renders without "
            "its map. Add the building and run scripts/render_place_maps.py.",
            place.building,
        )
        return None
    return f"{_MAP_IMAGE_BASE}{place.building}.webp"


def resolve_place(key: str | None) -> PlaceCard | None:
    """One model-emitted key as the card the student sees, or None with a reason logged.

    `key` is None on the ordinary turn where the model emitted no block, and that is the
    only case that returns quietly. An unknown key is a DROPPED CARD and is logged: it means
    the model reached past the roster it was given, which is the one failure this module
    exists to make harmless.
    """
    if key is None:
        return None

    normalized = key.strip().lower()
    place = CAMPUS_PLACES.get(normalized)
    if place is None:
        logger.warning(
            "Unknown campus place key %r from the model; no location card. Known keys: %s",
            key,
            ", ".join(CAMPUS_PLACES),
        )
        return None

    # NO CAP IS APPLIED HERE, and none is applied anywhere else on this path. The card
    # contract's caps are guards on model-authored text, and there is none in a place card:
    # the model wrote a key, and the name and address below are this table's. An address cut
    # at a word boundary is a student sent to "Clark Hall, 1st floor, room..." - see
    # docs/cards-v2.md, Length caps, on shortening only what can be measured.
    return PlaceCard(
        key=normalized,
        name=place.name,
        address=place.address,
        directionsUrl=directions_url(place),
        mapImageUrl=map_image_url(place),
    )

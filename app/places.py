"""The campus location card: the model names a place, the server owns where it is.

An unlisted place yields no card, and no request ever leaves for a third party. Both
tables are data/places.csv and data/buildings.csv, read at import; see
docs/chat-service.md, Campus location cards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote_plus

from campus_data import load_keyed, parse_coordinate
from models import PlaceCard

logger = logging.getLogger(__name__)

_DIRECTIONS_BASE = "https://www.google.com/maps/dir/?api=1&destination="

_MAP_IMAGE_BASE = "/places/"


@dataclass(frozen=True)
class CampusBuilding:
    """One building: its name, and the coordinate its committed map was rendered from."""

    name: str
    lat: float
    lon: float


# The buildings the catalogue points at, from data/buildings.csv, read at import.
# scripts/render_place_maps.py renders one image per key in this table.
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
    """One catalogue entry: what the card says, and which building it is inside."""

    name: str
    address: str
    building: str
    directions_destination: str
    when: str


# The catalogue, from data/places.csv. `building` is a foreign key into data/buildings.csv;
# `ground_truth_ids` and `note` are provenance for the next editor, so neither is loaded.
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
    """Return (key, when) pairs for the system prompt, in table order."""
    return [(key, place.when) for key, place in CAMPUS_PLACES.items()]


def directions_url(place: CampusPlace) -> str:
    """Build the keyless Google Maps directions link for one catalogue entry."""
    return _DIRECTIONS_BASE + quote_plus(place.directions_destination)


def map_image_url(place: CampusPlace) -> str | None:
    """The committed map for this place's building, or None if it has no entry."""
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
    """Resolve one model-emitted key into a card, or None with a reason logged."""
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

    return PlaceCard(
        key=normalized,
        name=place.name,
        address=place.address,
        directionsUrl=directions_url(place),
        mapImageUrl=map_image_url(place),
    )

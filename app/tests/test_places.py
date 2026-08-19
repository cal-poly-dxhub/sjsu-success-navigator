"""The campus location card: what the model may say, and what it can never say.

The property under test is one sentence. THE MODEL WRITES A CATALOGUE KEY AND NOTHING ELSE,
so every address, name, map and link the student reads came out of data/places.csv and
data/buildings.csv, through app/places.py.
That makes the interesting cases the negative ones - a key nobody put in the table, a place
named on a turn that also needs the crisis panel - because each of them is a way a wrong
address could otherwise reach a student who is about to walk somewhere.

Four properties get their own tests here because each is easy to lose silently and expensive
to lose: NO API KEY appears in anything this module produces, NO LENGTH CAP is applied to an
address, every building the catalogue points at HAS A COMMITTED IMAGE on disk, and every
coordinate is ON CAMPUS.
"""

import logging
import re
from pathlib import Path

import pytest

from places import (
    CAMPUS_BUILDINGS,
    CAMPUS_PLACES,
    CampusPlace,
    directions_url,
    map_image_url,
    place_roster_for_prompt,
    resolve_place,
)

# frontend/public/places, reached from app/tests/. The images are committed rather than
# built, so this suite can assert they exist.
_IMAGE_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public" / "places"

# The SJSU main campus and its immediate edge, generously drawn. Anything outside this is a
# coordinate that landed in another part of San Jose, which is the failure mode an automated
# geocode actually produces (an early pass put the Student Services Center in Evergreen).
_CAMPUS_BOX = (37.3300, 37.3410, -121.8880, -121.8750)  # south, north, west, east


# --- the two tables ------------------------------------------------------------------------


def test_every_place_names_a_building_that_exists():
    """The one referential link between the tables. A typo here costs the card its map, and
    the WARNING in map_image_url is the only other thing that would say so."""
    for key, place in CAMPUS_PLACES.items():
        assert place.building in CAMPUS_BUILDINGS, f"{key} points at {place.building!r}"


def test_every_building_is_pointed_at_by_something():
    """The other direction: a building nobody uses is an image being served and rendered for
    nothing, and a sign that a place was deleted without its building."""
    used = {place.building for place in CAMPUS_PLACES.values()}
    assert used == set(CAMPUS_BUILDINGS), f"unused: {set(CAMPUS_BUILDINGS) - used}"


def test_every_entry_has_a_name_an_address_and_a_destination():
    """A card is the name, the line under it, and a way to walk there, so an entry missing
    any of them renders as a gap."""
    for key, place in CAMPUS_PLACES.items():
        assert place.name.strip(), key
        assert place.address.strip(), key
        assert place.directions_destination.strip(), key
        assert place.when.strip(), key


def test_the_address_is_not_just_the_name_again():
    """The panel prints the name above the address, so an address repeating it reads as a
    stutter and tells the student nothing about where to walk."""
    for key, place in CAMPUS_PLACES.items():
        assert place.address.strip().lower() != place.name.strip().lower(), key


def test_no_entry_carries_a_dash_the_display_path_would_rewrite():
    """Em and en dashes are banned in everything the student reads (app/cards.py,
    normalise_dashes) - and nothing normalises THIS text, because it never passes through the
    card path. So the tables have to be clean at the source."""
    for key, place in CAMPUS_PLACES.items():
        for field in (place.name, place.address, place.when):
            assert "—" not in field and "–" not in field, key


def test_the_roster_is_the_resolvers_own_table():
    """The prompt's key list and the resolver's table are the same dict, the way the safety
    roster and app/safety.py's table are. That is what makes "a key the model is taught always
    resolves" a property rather than a promise."""
    roster_keys = [key for key, _ in place_roster_for_prompt()]
    assert roster_keys == list(CAMPUS_PLACES)
    for key in roster_keys:
        assert resolve_place(key) is not None


# --- the coordinates ------------------------------------------------------------------------


def test_every_building_coordinate_is_on_campus():
    """The failure an automated geocode actually produces is a plausible building in the wrong
    part of the city, not a nonsense number. This is the cheap net under that."""
    south, north, west, east = _CAMPUS_BOX
    for key, building in CAMPUS_BUILDINGS.items():
        assert south <= building.lat <= north, f"{key} latitude {building.lat}"
        assert west <= building.lon <= east, f"{key} longitude {building.lon}"


def test_no_two_buildings_share_a_coordinate():
    """Two buildings at one point means one of them was copied and not edited, which renders
    as two offices with the same map and nothing on screen to say so."""
    seen: dict[tuple[float, float], str] = {}
    for key, building in CAMPUS_BUILDINGS.items():
        point = (building.lat, building.lon)
        assert point not in seen, f"{key} and {seen.get(point)} share {point}"
        seen[point] = key


# --- the committed maps ---------------------------------------------------------------------


def test_every_building_has_a_committed_map_image():
    """THE IMAGES ARE COMMITTED, NOT BUILT, so nothing at deploy time would notice one
    missing: the card would simply render a broken image at a student. Adding a building means
    running scripts/render_place_maps.py, and this is what says so out loud."""
    for key in CAMPUS_BUILDINGS:
        path = _IMAGE_DIR / f"{key}.webp"
        assert path.exists(), f"{path} is missing; run scripts/render_place_maps.py"
        assert path.stat().st_size > 2000, f"{path} looks empty ({path.stat().st_size} B)"


def test_no_committed_image_is_orphaned():
    """The other direction: an image with no building is a file being deployed for nothing,
    left behind when a coordinate was renamed."""
    on_disk = {path.stem for path in _IMAGE_DIR.glob("*.webp")}
    assert on_disk == set(CAMPUS_BUILDINGS), f"orphaned: {on_disk - set(CAMPUS_BUILDINGS)}"


def test_the_map_url_is_served_from_our_own_origin():
    """A ROOT-RELATIVE PATH, never a URL. The map coming off the same distribution as the page
    is what makes "a rendered turn contacts no third party" true, and a hostname appearing here
    is exactly how that would stop being true."""
    for key, place in CAMPUS_PLACES.items():
        url = map_image_url(place)
        assert url is not None, key
        assert url.startswith("/places/"), f"{key}: {url}"
        assert "//" not in url, f"{key}: {url}"
        assert "http" not in url, f"{key}: {url}"


def test_a_place_in_an_unrendered_building_still_makes_a_card(caplog):
    """A card with no map is the documented, complete state: name, address, directions. This
    is the state a new catalogue entry lands in before anybody renders its building, and it
    must be ordinary rather than an exception."""
    orphan = CampusPlace(
        name="Somewhere New",
        address="A line the student reads",
        building="not-rendered-yet",
        directions_destination="Clark Hall, San Jose State University, San Jose, CA",
        when="never",
    )
    with caplog.at_level(logging.WARNING, logger="places"):
        assert map_image_url(orphan) is None
    assert "not-rendered-yet" in caplog.text


# --- resolution -----------------------------------------------------------------------------


def test_a_listed_place_resolves_to_the_tables_own_strings():
    card = resolve_place("career-center")
    entry = CAMPUS_PLACES["career-center"]
    assert card is not None
    assert card.key == "career-center"
    assert card.name == entry.name
    # Byte-identical, not merely similar: nothing between the table and the wire edits it.
    assert card.address == entry.address
    assert card.map_image_url == "/places/clark-hall.webp"


def test_offices_in_one_building_share_its_map():
    """Sixteen entries, five buildings. Four offices are inside Clark Hall, and the map
    belongs to the building while the room number belongs to the place."""
    clark = {key for key, place in CAMPUS_PLACES.items() if place.building == "clark-hall"}
    assert clark == {"career-center", "title-ix-office", "eop", "guardian-scholars"}
    images = {resolve_place(key).map_image_url for key in clark}
    assert images == {"/places/clark-hall.webp"}
    # The addresses still differ - that is the half the shared image does not cover.
    assert len({resolve_place(key).address for key in clark}) > 1


def test_an_unlisted_place_yields_no_card_and_says_so(caplog):
    """THE RULE THE WHOLE FEATURE RESTS ON. Not a guessed card, not a card whose location is
    a search for whatever the model typed: no card, and a WARNING naming the key."""
    with caplog.at_level(logging.WARNING, logger="places"):
        assert resolve_place("student-union-bowling-alley") is None
    assert "student-union-bowling-alley" in caplog.text


def test_no_block_is_the_quiet_case():
    """None means the model wrote no place block, which is almost every turn. It is the one
    case that must not log, or the logs stop being a signal."""
    assert resolve_place(None) is None


def test_a_key_resolves_regardless_of_case_and_stray_space():
    assert resolve_place("  Career-Center  ") is not None
    assert resolve_place("CAREER-CENTER").key == "career-center"


# --- the directions link: keyless, and provably so -------------------------------------------


def test_the_directions_link_is_a_plain_maps_url():
    url = directions_url(CAMPUS_PLACES["spartan-food-pantry"])
    assert url.startswith("https://www.google.com/maps/dir/?api=1&destination=")
    # The destination is the table's, percent-encoded. The address the student READS is a
    # different string on purpose - a wayfinding line makes a poor search query.
    assert "Diaz+Compean+Student+Union" in url


@pytest.mark.parametrize("key", sorted(CAMPUS_PLACES))
def test_no_link_anywhere_carries_an_api_key(key):
    """The acceptance criterion, asserted rather than assumed. There is no key in this repo,
    no Cloud project and no billing account: the directions link is the form Google serves
    without one, and this fails the day a parameter says otherwise."""
    lowered = directions_url(CAMPUS_PLACES[key]).lower()
    assert "key=" not in lowered
    assert not re.search(r"aiza[0-9a-z_-]{10}", lowered)
    assert "apikey" not in lowered


def test_the_directions_url_is_built_only_from_the_table():
    """Nothing the model wrote reaches the URL. The key selects a row, the row supplies the
    destination, and there is no third input."""
    fake = CampusPlace(
        name="Somewhere",
        address="A line the student reads",
        building="clark-hall",
        directions_destination="Clark Hall, San Jose State University, San Jose, CA",
        when="never",
    )
    assert directions_url(fake).endswith(
        "Clark+Hall%2C+San+Jose+State+University%2C+San+Jose%2C+CA"
    )


# --- no cap ever touches an address ----------------------------------------------------------


def test_an_address_is_never_shortened_however_long_it_runs(monkeypatch):
    """LENGTH CAPS MUST NEVER TRUNCATE AN ADDRESS, and this is the shape that guarantees it:
    the caps live in cards.py and apply to model-authored fields, and there is no
    model-authored field on this path at all. An address cut at a word boundary is a student
    sent to a room number that stops halfway."""
    long_address = "Clark Hall, " + "first floor past the stairs, " * 60 + "room 140"
    assert len(long_address) > 1000
    monkeypatch.setitem(
        CAMPUS_PLACES,
        "career-center",
        CampusPlace(
            name="Career Center",
            address=long_address,
            building="clark-hall",
            directions_destination="Clark Hall, San Jose State University, San Jose, CA",
            when="the Career Center",
        ),
    )
    card = resolve_place("career-center")
    assert card.address == long_address
    assert "…" not in card.address


# --- the tables are data/places.csv and data/buildings.csv -------------------------------------
#
# Everything above runs against the loaded tables and would pass just as well against two
# hardcoded dicts. These are the tests that say the FILES are what got loaded, and that a bad
# row stops the process rather than dropping an office out of the catalogue.


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    import campus_data

    monkeypatch.setattr(campus_data, "_DATA_DIRS", (tmp_path,))
    return tmp_path


def test_the_catalogue_is_the_committed_csv_row_for_row():
    """A card's name, address and destination come off data/places.csv, in its order. Anything
    that made this pass while the file said something else would be the divergence this whole
    directory exists to remove."""
    from campus_data import load_keyed

    rows = load_keyed(
        "places.csv",
        "key",
        ("name", "building", "address", "directions_destination", "when"),
        optional=("ground_truth_ids", "note"),
    )
    assert list(rows) == list(CAMPUS_PLACES)
    for key, row in rows.items():
        place = CAMPUS_PLACES[key]
        assert (place.name, place.building, place.address, place.when) == (
            row["name"],
            row["building"],
            row["address"],
            row["when"],
        )
        assert place.directions_destination == row["directions_destination"]


def test_the_buildings_are_the_committed_csv_coordinates():
    from campus_data import load_keyed

    rows = load_keyed("buildings.csv", "key", ("name", "lat", "lon"), optional=("note",))
    assert list(rows) == list(CAMPUS_BUILDINGS)
    for key, row in rows.items():
        building = CAMPUS_BUILDINGS[key]
        assert building.name == row["name"]
        assert (building.lat, building.lon) == (float(row["lat"]), float(row["lon"]))


def test_a_coordinate_that_is_not_a_number_stops_the_import(data_dir):
    """The map is rendered from these two cells, so a degree sign pasted in from a web page
    has to be a loud failure and not a building silently missing its point."""
    import places
    from campus_data import CampusDataError

    (data_dir / "buildings.csv").write_text(
        "key,name,lat,lon,note\nclark-hall,Clark Hall,37.336178°,-121.882546,\n",
        encoding="utf-8",
    )
    with pytest.raises(CampusDataError, match="clark-hall"):
        places._load_buildings()


def test_a_place_row_missing_its_building_stops_the_import(data_dir):
    """`building` is the one foreign key between the two files. Empty is not a place with no
    map - it is a row somebody half filled in."""
    import places
    from campus_data import CampusDataError

    (data_dir / "places.csv").write_text(
        "key,name,building,address,directions_destination,when,ground_truth_ids,note\n"
        "career-center,Career Center,,Clark Hall,Clark Hall,resumes,,\n",
        encoding="utf-8",
    )
    with pytest.raises(CampusDataError, match="`building` is empty"):
        places._load_places()

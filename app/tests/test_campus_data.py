"""The reader of repo-root data/: what it does when a file is wrong.

ONE PROPERTY, AND EVERY TEST HERE IS A WAY OF LOSING IT. A bad data file raises, and it raises
at import, so the process dies rather than serving a student a table with rows missing. There
is no path through this module that returns a short list, a partial row, or a default.

That matters more here than the phrasing of any error message, because of what these files
carry. `contacts.csv` truncated by a bad merge is a crisis phone number missing from the panel
a student in danger is shown - and a loader that logged a warning and carried on would render
that panel, look right, and pass every other test in this suite. `urls.csv` truncated is worse
in a different direction: the scraper's prune keys off the list, so a short one deletes the
knowledge base. Neither failure has a symptom until somebody is already harmed by it.

The tests point the reader at a tmp_path directory rather than editing the committed files, so
each one describes exactly one malformation.
"""

import pytest

import campus_data
from campus_data import (
    CampusDataError,
    load_keyed,
    load_rows,
    parse_coordinate,
    parse_flag,
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A stand-in for repo-root data/, and the ONLY place the reader will look."""
    monkeypatch.setattr(campus_data, "_DATA_DIRS", (tmp_path,))
    return tmp_path


def _write(directory, name, text):
    (directory / name).write_text(text, encoding="utf-8")


# --- the file itself ------------------------------------------------------------------------


def test_a_missing_file_names_everywhere_it_looked(data_dir):
    """The message has to be actionable from a CloudWatch line with no repo in front of you:
    a deployed function missing this file means the layer is not attached, and a checkout
    missing it means somebody moved it. Both readings come off the paths."""
    with pytest.raises(CampusDataError) as caught:
        load_rows("places.csv", ("key",))
    assert "places.csv" in str(caught.value)
    assert str(data_dir) in str(caught.value)


def test_the_first_directory_holding_the_file_wins(tmp_path, monkeypatch):
    """/opt is where Lambda extracts the layer and it is checked first, so a deployed function
    reads its layer rather than anything else on the filesystem. The search is for the FILE and
    not the directory on purpose - /opt exists on a developer's Mac and holds no CSV of ours."""
    first, second = tmp_path / "opt", tmp_path / "repo"
    first.mkdir()
    second.mkdir()
    _write(second, "abbreviations.csv", "abbreviation,expansion\nSSC,Student Services Center\n")
    monkeypatch.setattr(campus_data, "_DATA_DIRS", (first, second))
    assert load_rows("abbreviations.csv", ("abbreviation", "expansion"))[0]["abbreviation"] == "SSC"

    _write(first, "abbreviations.csv", "abbreviation,expansion\nSSC,Somewhere Else\n")
    assert load_rows("abbreviations.csv", ("abbreviation", "expansion"))[0]["expansion"] == (
        "Somewhere Else"
    )


# --- the shape of the table -------------------------------------------------------------------


def test_a_missing_column_is_named(data_dir):
    _write(data_dir, "places.csv", "key,name\nsjsu-cares,SJSU Cares\n")
    with pytest.raises(CampusDataError, match="missing required column"):
        load_rows("places.csv", ("key", "name", "address"))


def test_a_missing_OPTIONAL_column_is_also_fatal(data_dir):
    """Optional means "may be EMPTY", never "may be absent". A column the code reads and the
    file does not have is the two drifting apart, which is the whole failure this directory
    exists to prevent - so it fails the same way a required one does."""
    _write(data_dir, "places.csv", "key,name\nsjsu-cares,SJSU Cares\n")
    with pytest.raises(CampusDataError, match="missing required column"):
        load_rows("places.csv", ("key", "name"), optional=("note",))


def test_an_empty_required_cell_names_the_line(data_dir):
    """THE MALFORMATION A PERSON ACTUALLY MAKES: a row inserted in a spreadsheet and half
    filled in. The line number is what makes the message useful to them."""
    _write(
        data_dir,
        "contacts.csv",
        "kind,id\nsafety,caps\nsafety,\n",
    )
    with pytest.raises(CampusDataError, match="line 3: `id` is empty"):
        load_rows("contacts.csv", ("kind", "id"))


def test_a_whitespace_only_cell_counts_as_empty(data_dir):
    """A cell holding spaces looks filled in and is not. Everything is stripped on the way in,
    so "  " cannot be a phone number that renders as a blank."""
    _write(data_dir, "contacts.csv", "kind,id\nsafety,   \n")
    with pytest.raises(CampusDataError, match="`id` is empty"):
        load_rows("contacts.csv", ("kind", "id"))


def test_an_optional_cell_may_be_empty_and_arrives_as_an_empty_string(data_dir):
    _write(data_dir, "places.csv", "key,name,note\nsjsu-cares,SJSU Cares,\n")
    rows = load_rows("places.csv", ("key", "name"), optional=("note",))
    assert rows == [{"key": "sjsu-cares", "name": "SJSU Cares", "note": ""}]


def test_a_header_with_no_rows_is_fatal_rather_than_an_empty_list(data_dir):
    """AN EMPTY TABLE IS NOT A SMALL TABLE. This is the shape a truncating merge or a
    save-the-wrong-sheet produces, and returning [] from it is how a crisis panel comes up
    blank and a knowledge base gets pruned to nothing."""
    _write(data_dir, "contacts.csv", "kind,id\n")
    with pytest.raises(CampusDataError, match="valid header and no rows"):
        load_rows("contacts.csv", ("kind", "id"))


def test_rows_come_back_in_file_order(data_dir):
    """Order is load-bearing twice over: the default crisis panel is the file's order, and so
    is the roster the model reads. A dict or a sort here would silently reorder both."""
    _write(
        data_dir,
        "abbreviations.csv",
        "abbreviation,expansion\nZZZ,Last\nAAA,First\nMMM,Middle\n",
    )
    rows = load_rows("abbreviations.csv", ("abbreviation", "expansion"))
    assert [row["abbreviation"] for row in rows] == ["ZZZ", "AAA", "MMM"]


def test_an_unreadable_file_is_reported_as_such(data_dir):
    """Not decodable as UTF-8: a spreadsheet saved as Latin-1, which is a one-click mistake.
    It has to arrive as a data error rather than a UnicodeDecodeError out of a cold start."""
    (data_dir / "places.csv").write_bytes(b"key,name\nsjsu-cares,\xff\xfe SJSU\n")
    with pytest.raises(CampusDataError, match="could not be read as CSV"):
        load_rows("places.csv", ("key", "name"))


# --- keys ---------------------------------------------------------------------------------------


def test_a_duplicate_key_is_fatal_rather_than_the_last_one_winning(data_dir):
    """A duplicated row is a copy somebody never finished editing. Overwriting would make the
    SECOND one the address every student is given, with nothing on screen or in the logs to
    say a first one existed."""
    _write(
        data_dir,
        "places.csv",
        "key,name\nsjsu-cares,SJSU Cares\nsjsu-cares,SJSU Cares (old)\n",
    )
    with pytest.raises(CampusDataError, match="listed more than once"):
        load_keyed("places.csv", "key", ("name",))


def test_keyed_rows_keep_file_order(data_dir):
    _write(data_dir, "places.csv", "key,name\nb,B\na,A\n")
    assert list(load_keyed("places.csv", "key", ("name",))) == ["b", "a"]


# --- cells with a type ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["37.336178°", "37,336178", "north", ""])
def test_a_coordinate_that_is_not_a_number_names_the_cell(value):
    """A degree sign or a comma decimal separator is what a spreadsheet produces, and a bare
    ValueError out of float() would say nothing about which of five buildings to go and fix."""
    with pytest.raises(CampusDataError, match="clark-hall"):
        parse_coordinate(value, name="buildings.csv", key="clark-hall", column="lat")


def test_a_coordinate_that_is_a_number_comes_back_as_one():
    assert parse_coordinate("-121.882546", name="b.csv", key="k", column="lon") == -121.882546


@pytest.mark.parametrize("value", ["yes", "YES", "Yes", "true", "1"])
def test_a_flag_reads_as_yes(value):
    assert parse_flag(value, name="contacts.csv", key="caps", column="in_default_panel") is True


@pytest.mark.parametrize("value", ["no", "NO", "false", "0", "", "  "])
def test_a_flag_reads_as_no(value):
    assert parse_flag(value, name="contacts.csv", key="caps", column="in_default_panel") is False


@pytest.mark.parametrize("value", ["y", "yes please", "maybe", "x"])
def test_ANYTHING_ELSE_IN_A_FLAG_IS_FATAL(value):
    """The one reader of this is `in_default_panel`, which decides whether a crisis contact is
    on the panel a student gets when no specific resource fits. A typo quietly reading as "no"
    is a number missing from that panel, so a typo stops the process instead."""
    with pytest.raises(CampusDataError, match="Write yes or no"):
        parse_flag(value, name="contacts.csv", key="caps", column="in_default_panel")


# --- the row's shape ----------------------------------------------------------------------------
#
# THESE TWO WERE FOUND BY DAMAGING THE REAL FILES, not by reasoning about them. Both slipped
# through every check above, because every cell either reader looked at was well formed - the
# cells had simply moved, or stopped. The shape is the only tell.


def test_a_row_with_MORE_cells_than_the_header_is_fatal(data_dir):
    """A DECIMAL COMMA. "37,336178" in `lat` shifts every later cell one column left, so
    buildings.csv parsed cleanly with lat=37 and lon=336178 - a building off the coast of
    Africa, loaded without a word. Nothing about those two cells is malformed on its own."""
    _write(
        data_dir,
        "buildings.csv",
        "key,name,lat,lon,note\nclark-hall,Clark Hall,37,336178,-121.882546,\n",
    )
    with pytest.raises(CampusDataError, match="more cells than the header"):
        load_rows("buildings.csv", ("key", "name", "lat", "lon"), optional=("note",))


def test_a_row_with_FEWER_cells_than_the_header_is_fatal(data_dir):
    """A FILE THAT STOPPED PART-WAY, which is what a bad merge or an interrupted save leaves.
    The columns past the cut are not empty - they are absent - and treating that as empty is
    how contacts.csv loaded with three crisis contacts missing and nothing said so."""
    _write(
        data_dir,
        "contacts.csv",
        "kind,id,label,in_default_panel\nsafety,caps,CAPS,yes\nsafety,upd,UPD\n",
    )
    with pytest.raises(CampusDataError, match="fewer cells than the header"):
        load_rows("contacts.csv", ("kind", "id", "label"), optional=("in_default_panel",))


def test_an_absent_cell_is_not_the_same_as_an_empty_one(data_dir):
    """The distinction the two tests above rest on. A trailing comma is a cell that is there
    and empty, which an optional column allows; no comma at all is a row that is short."""
    _write(data_dir, "places.csv", "key,name,note\nsjsu-cares,SJSU Cares,\n")
    assert load_rows("places.csv", ("key", "name"), optional=("note",))[0]["note"] == ""

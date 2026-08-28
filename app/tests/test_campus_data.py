"""The reader of repo-root data/: what it does when a file is wrong.

One property: a bad file raises at import rather than serving a table with rows missing.
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
    """The only place the reader will look."""
    monkeypatch.setattr(campus_data, "_DATA_DIRS", (tmp_path,))
    return tmp_path


def _write(directory, name, text):
    (directory / name).write_text(text, encoding="utf-8")


def test_a_missing_file_names_everywhere_it_looked(data_dir):
    """The message has to be actionable from a CloudWatch line, so it names every path."""
    with pytest.raises(CampusDataError) as caught:
        load_rows("places.csv", ("key",))
    assert "places.csv" in str(caught.value)
    assert str(data_dir) in str(caught.value)


def test_the_first_directory_holding_the_file_wins(tmp_path, monkeypatch):
    """/opt is checked first, and the search is for the file: /opt exists on a Mac too."""
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


def test_a_missing_column_is_named(data_dir):
    _write(data_dir, "places.csv", "key,name\nsjsu-cares,SJSU Cares\n")
    with pytest.raises(CampusDataError, match="missing required column"):
        load_rows("places.csv", ("key", "name", "address"))


def test_a_missing_OPTIONAL_column_is_also_fatal(data_dir):
    """Optional means "may be empty", never "may be absent": an absent one is drift."""
    _write(data_dir, "places.csv", "key,name\nsjsu-cares,SJSU Cares\n")
    with pytest.raises(CampusDataError, match="missing required column"):
        load_rows("places.csv", ("key", "name"), optional=("note",))


def test_an_empty_required_cell_names_the_line(data_dir):
    """The malformation a person actually makes, so the message names the line."""
    _write(
        data_dir,
        "contacts.csv",
        "kind,id\nsafety,caps\nsafety,\n",
    )
    with pytest.raises(CampusDataError, match="line 3: `id` is empty"):
        load_rows("contacts.csv", ("kind", "id"))


def test_a_whitespace_only_cell_counts_as_empty(data_dir):
    """A cell holding spaces looks filled in and is not, so everything is stripped."""
    _write(data_dir, "contacts.csv", "kind,id\nsafety,   \n")
    with pytest.raises(CampusDataError, match="`id` is empty"):
        load_rows("contacts.csv", ("kind", "id"))


def test_an_optional_cell_may_be_empty_and_arrives_as_an_empty_string(data_dir):
    _write(data_dir, "places.csv", "key,name,note\nsjsu-cares,SJSU Cares,\n")
    rows = load_rows("places.csv", ("key", "name"), optional=("note",))
    assert rows == [{"key": "sjsu-cares", "name": "SJSU Cares", "note": ""}]


def test_a_header_with_no_rows_is_fatal_rather_than_an_empty_list(data_dir):
    """An empty table is not a small table: returning [] blanks a panel or prunes a KB."""
    _write(data_dir, "contacts.csv", "kind,id\n")
    with pytest.raises(CampusDataError, match="valid header and no rows"):
        load_rows("contacts.csv", ("kind", "id"))


def test_rows_come_back_in_file_order(data_dir):
    """Order is load-bearing: the default crisis panel and the model's roster are both it."""
    _write(
        data_dir,
        "abbreviations.csv",
        "abbreviation,expansion\nZZZ,Last\nAAA,First\nMMM,Middle\n",
    )
    rows = load_rows("abbreviations.csv", ("abbreviation", "expansion"))
    assert [row["abbreviation"] for row in rows] == ["ZZZ", "AAA", "MMM"]


def test_an_unreadable_file_is_reported_as_such(data_dir):
    """A spreadsheet saved as Latin-1 has to arrive as a data error, not a decode error."""
    (data_dir / "places.csv").write_bytes(b"key,name\nsjsu-cares,\xff\xfe SJSU\n")
    with pytest.raises(CampusDataError, match="could not be read as CSV"):
        load_rows("places.csv", ("key", "name"))


def test_a_duplicate_key_is_fatal_rather_than_the_last_one_winning(data_dir):
    """Overwriting would make the second row the address every student is given, silently."""
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


@pytest.mark.parametrize("value", ["37.336178°", "37,336178", "north", ""])
def test_a_coordinate_that_is_not_a_number_names_the_cell(value):
    """A bare ValueError out of float() would not say which building to go and fix."""
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
    """A typo quietly reading as "no" would drop a contact off the panel."""
    with pytest.raises(CampusDataError, match="Write yes or no"):
        parse_flag(value, name="contacts.csv", key="caps", column="in_default_panel")


# Every cell looked at is well formed in both: the cells moved or stopped, so shape is the tell.


def test_a_row_with_MORE_cells_than_the_header_is_fatal(data_dir):
    """A decimal comma shifts every later cell left, and no single cell is malformed."""
    _write(
        data_dir,
        "buildings.csv",
        "key,name,lat,lon,note\nclark-hall,Clark Hall,37,336178,-121.882546,\n",
    )
    with pytest.raises(CampusDataError, match="more cells than the header"):
        load_rows("buildings.csv", ("key", "name", "lat", "lon"), optional=("note",))


def test_a_row_with_FEWER_cells_than_the_header_is_fatal(data_dir):
    """A file that stopped part-way: the columns past the cut are absent, not empty."""
    _write(
        data_dir,
        "contacts.csv",
        "kind,id,label,in_default_panel\nsafety,caps,CAPS,yes\nsafety,upd,UPD\n",
    )
    with pytest.raises(CampusDataError, match="fewer cells than the header"):
        load_rows("contacts.csv", ("kind", "id", "label"), optional=("in_default_panel",))


def test_an_absent_cell_is_not_the_same_as_an_empty_one(data_dir):
    """A trailing comma is an empty cell, which optional allows; no comma is a short row."""
    _write(data_dir, "places.csv", "key,name,note\nsjsu-cares,SJSU Cares,\n")
    assert load_rows("places.csv", ("key", "name"), optional=("note",))[0]["note"] == ""

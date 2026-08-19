"""The one reader of repo-root `data/`, and the one place a bad row stops the process.

WHY THE FACTS LEFT THE CODE. The SJSU Cares address was written out in app/places.py and
again in frontend/src/lib/sjsuCares.ts, and its mailbox in config.yaml and again in that same
TypeScript file. Nothing compared them. If the office moved, the map card and the "Talk to a
person" panel would disagree inside one app and every test would still pass, because a fact
spelled twice in two languages has no test that can see both copies. So the facts moved to
`data/` at the repo root, and BOTH languages read from there: Python through this module, the
browser through a TypeScript module regenerated from these same files on every frontend build
(frontend/scripts/generate-campus-data.mjs). The captain's other requirement falls out of the
same move - a CSV opens in Excel, so correcting a phone number is not a code change.

RAISING IS THE WHOLE CONTRACT. Every loader here raises `CampusDataError` on a missing file, a
missing column, an empty required cell, a duplicate key, a header with no rows, or a row whose
cell count is not the header's in either direction. It never returns a short list, and the reason is the same one scraper.py gives for the crawl list one
directory over: a partial read is indistinguishable from a small table. A contacts.csv
truncated by a bad merge would drop a crisis phone number off the safety panel and log
nothing; a short urls.csv would hand the scraper's prune an empty expected set and delete the
knowledge base. A process that dies at import is a deploy that fails loudly. A process that
quietly loads four of seven crisis contacts is a student who is not given a number.

WHERE THE FILES ARE AT RUNTIME. `data/` sits at the repo root, OUTSIDE app/, and the chat
Lambda's asset is app/ - `Code.from_asset` takes one directory. So the data travels the way
the crawl list already travelled: as a Lambda layer, whose content Lambda extracts to /opt.
`_data_file` looks in /opt first and falls back to the repo checkout, which is what makes the
tests, `scripts/render_place_maps.py` and a local run read the same bytes the deployed
function reads. It looks for the FILE rather than the directory on purpose - /opt exists on a
developer's Mac and holds no CSV.
"""

from __future__ import annotations

import csv
from pathlib import Path

# /opt is where Lambda extracts layer content (the CampusDataLayer in infra/infra_stack.py
# stages data/ itself, so the CSVs land at /opt/<name>.csv). The repo checkout is second, for
# tests, the map renderer and any local run.
_DATA_DIRS: tuple[Path, ...] = (
    Path("/opt"),
    Path(__file__).resolve().parents[1] / "data",
)


# csv.DictReader's overflow key and its filler for a row that ran out of cells. Sentinels
# rather than None or "", because an EMPTY cell is legitimate in an optional column and "there
# was no cell here at all" has to stay distinguishable from it.
_EXTRA_CELLS = "__extra_cells__"
_NO_CELL = object()


class CampusDataError(RuntimeError):
    """A data file is missing, malformed, or short. Fatal by design - see the module docstring."""


def _data_file(name: str) -> Path:
    for directory in _DATA_DIRS:
        candidate = directory / name
        if candidate.exists():
            return candidate
    raise CampusDataError(
        f"{name} was not found. Looked in: "
        + ", ".join(str(d / name) for d in _DATA_DIRS)
        + ". It ships as a Lambda layer extracted to /opt, so an absent file in a deployed "
        "function means the layer is missing from it; in a checkout it means the file was "
        "moved out of the repo-root data/ directory."
    )


def load_rows(name: str, required: tuple[str, ...], *, optional: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """One data file as a list of row dicts, in file order. Raises rather than returning less.

    `required` columns must be present in the header and non-empty in every row. `optional`
    columns must be present in the header too - a missing one is a file that has drifted from
    the code reading it - but may be empty in any row. Unknown extra columns are ignored, so a
    non-engineer can keep a working note beside a row without breaking the build.
    """
    path = _data_file(name)
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, restkey=_EXTRA_CELLS, restval=_NO_CELL)
            header = reader.fieldnames or []
            missing = [c for c in (*required, *optional) if c not in header]
            if missing:
                raise CampusDataError(
                    f"{name} is missing required column(s): {', '.join(missing)}. "
                    f"Required: {', '.join((*required, *optional))}."
                )
            raw_rows = list(reader)
    except CampusDataError:
        raise
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise CampusDataError(f"{name} could not be read as CSV: {exc}") from exc

    rows: list[dict[str, str]] = []
    for line_number, raw in enumerate(raw_rows, start=2):  # line 1 is the header
        # A ROW WHOSE CELL COUNT IS NOT THE HEADER'S, in either direction. Both are silent
        # corruptions rather than obvious ones, and both were found by damaging the real files
        # rather than by reasoning about them:
        #   too many - a decimal comma. "37,336178" in `lat` shifts every later cell one to
        #     the left, so buildings.csv parsed cleanly with lat=37, lon=336178 and a building
        #     somewhere off the coast of Africa.
        #   too few  - a write that stopped part-way, which is what a bad merge or an
        #     interrupted save leaves. csv reads the unterminated last field happily and the
        #     columns past it simply are not there, so contacts.csv loaded with three crisis
        #     contacts missing and nothing said so.
        # Neither is a cell this module could validate, because each cell it did look at was
        # perfectly well formed. The shape is the only tell.
        if _EXTRA_CELLS in raw:
            raise CampusDataError(
                f"{name} line {line_number}: more cells than the header has columns "
                f"({len(header)}). A stray comma inside a value shifts every cell after it, "
                "so wrap the value in double quotes - and check that a decimal point has not "
                "been saved as a comma."
            )
        short = [column for column in header if raw.get(column) is _NO_CELL]
        if short:
            raise CampusDataError(
                f"{name} line {line_number}: fewer cells than the header has columns - "
                f"nothing at all for {', '.join(short)}. The file looks cut off, which is "
                "what a half-finished save or a bad merge leaves behind."
            )
        row: dict[str, str] = {}
        for column in required:
            value = (raw.get(column) or "").strip()
            if not value:
                raise CampusDataError(
                    f"{name} line {line_number}: `{column}` is empty, and every row needs "
                    f"one. Required columns: {', '.join(required)}."
                )
            row[column] = value
        for column in optional:
            row[column] = (raw.get(column) or "").strip()
        rows.append(row)

    if not rows:
        raise CampusDataError(
            f"{name} has a valid header and no rows. An empty table is not a small table: "
            "see the module docstring on why this is fatal rather than a warning."
        )
    return rows


def load_keyed(
    name: str,
    key_column: str,
    required: tuple[str, ...],
    *,
    optional: tuple[str, ...] = (),
) -> dict[str, dict[str, str]]:
    """`load_rows` keyed by one column, in file order, with duplicates fatal.

    A duplicate key is a row that silently wins over another - two SJSU Cares entries where
    the second's address is the one anybody sees - so it raises instead of overwriting.
    """
    rows = load_rows(name, (key_column, *required), optional=optional)
    keyed: dict[str, dict[str, str]] = {}
    for line_number, row in enumerate(rows, start=2):
        key = row[key_column]
        if key in keyed:
            raise CampusDataError(
                f"{name} line {line_number}: `{key_column}` {key!r} is listed more than "
                "once. One key, one row - a second one would quietly win."
            )
        keyed[key] = row
    return keyed


def parse_coordinate(value: str, *, name: str, key: str, column: str) -> float:
    """One latitude or longitude cell as a float, or a CampusDataError naming the cell.

    Spelled out rather than left to `float()` because the failure this catches is a person
    editing a spreadsheet: a stray degree sign or a comma decimal separator would otherwise
    raise a bare ValueError with nothing in it to say which row to fix.
    """
    try:
        return float(value)
    except ValueError as exc:
        raise CampusDataError(
            f"{name}: {key} has `{column}` = {value!r}, which is not a number. Coordinates "
            "are plain decimal degrees, so 37.336178 and -121.882546, with a dot for the "
            "decimal point and no degree sign."
        ) from exc


def parse_flag(value: str, *, name: str, key: str, column: str) -> bool:
    """A yes/no cell as a bool. Anything else raises rather than reading as "no".

    The one reader of this today is contacts.csv's `in_default_panel`, which decides whether a
    crisis contact appears on the panel a student gets when the model tags an emergency
    without naming resources. A typo silently meaning "no" is a number missing from that
    panel, so a typo is fatal instead.
    """
    lowered = value.strip().lower()
    if lowered in ("yes", "true", "1"):
        return True
    if lowered in ("no", "false", "0", ""):
        return False
    raise CampusDataError(
        f"{name}: {key} has `{column}` = {value!r}. Write yes or no."
    )

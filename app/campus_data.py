"""The one reader of repo-root `data/`, and the one place a bad row stops the process.

Every loader raises rather than returning a short list: a partial read reads as a small table.
"""

from __future__ import annotations

import csv
from pathlib import Path

# /opt is where Lambda extracts layer content; the checkout is the local fallback.
_DATA_DIRS: tuple[Path, ...] = (
    Path("/opt"),
    Path(__file__).resolve().parents[1] / "data",
)


# Sentinels: an empty optional cell is legitimate and must stay distinct from no cell.
_EXTRA_CELLS = "__extra_cells__"
_NO_CELL = object()


class CampusDataError(RuntimeError):
    """A data file is missing, malformed, or short. Fatal by design."""


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
    """All named columns must be in the header; only `required` must be non-empty per row."""
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
        # Every cell this module reads is well formed either way, so the shape is the tell.
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
    """`load_rows` keyed by one column, in file order. A duplicate would silently win."""
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
    try:
        return float(value)
    except ValueError as exc:
        raise CampusDataError(
            f"{name}: {key} has `{column}` = {value!r}, which is not a number. Coordinates "
            "are plain decimal degrees, so 37.336178 and -121.882546, with a dot for the "
            "decimal point and no degree sign."
        ) from exc


def parse_flag(value: str, *, name: str, key: str, column: str) -> bool:
    """A yes/no cell as a bool. Anything else raises: a typo meaning "no" drops a contact."""
    lowered = value.strip().lower()
    if lowered in ("yes", "true", "1"):
        return True
    if lowered in ("no", "false", "0", ""):
        return False
    raise CampusDataError(
        f"{name}: {key} has `{column}` = {value!r}. Write yes or no."
    )

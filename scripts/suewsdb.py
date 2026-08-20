"""Shared helpers for the SUEWS parameter database.

The database is a small relational store. Every table has an integer ``ID``
primary key, and columns that reference another table hold that table's IDs.
The first two digits of an ID identify the owning table (see
``docs/LAYOUT.md``), which is how a bare number such as ``90240002`` in a
``Ref`` column can be resolved without any further context.

The canonical on-disk form is one YAML file per table under ``db/``, each a
dictionary keyed by ID. ``database.xlsx`` is generated from those files and is
retained only because the UMEP QGIS plugins still ship a copy of it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DB_DIR = REPO / "db"
SCHEMA_DIR = REPO / "schema"
XLSX = REPO / "database.xlsx"
TABLES_YML = SCHEMA_DIR / "tables.yml"
PROVENANCE_YML = SCHEMA_DIR / "provenance.yml"

# Sheets are written in workbook order; this is the order the registry and the
# regenerated workbook both use.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def slug(sheet_name: str) -> str:
    """Turn a sheet name into a filename stem.

    ``"Spartacus Surface"`` -> ``spartacus_surface``
    ``"AnthropogenicEmission"`` -> ``anthropogenic_emission``
    ``"OHM"`` -> ``ohm``

    The mapping is cosmetic: the exact sheet name is stored in the table's
    YAML header, so regenerating the workbook never depends on inverting this.
    """
    parts = []
    for word in sheet_name.split():
        parts.extend(_CAMEL.sub("_", word).split("_"))
    return "_".join(p.lower() for p in parts if p)


class _Dumper(yaml.SafeDumper):
    """Dumper that keeps nested blocks indented under their parent key."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def dump_yaml(data, path: Path, header: str = "") -> None:
    text = yaml.dump(
        data,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
    )
    path.write_text(header + text, encoding="utf-8")


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_table(name_or_path) -> dict:
    """Load one table file (``db/<slug>.yml``) by slug or path."""
    path = Path(name_or_path)
    if not path.suffix:
        path = DB_DIR / f"{path.name}.yml"
    return load_yaml(path)


def load_registry() -> dict:
    return load_yaml(TABLES_YML)


def load_database() -> dict:
    """Load every table, keyed by its sheet name."""
    registry = load_registry()
    out = {}
    for entry in registry["tables"]:
        out[entry["sheet"]] = load_table(DB_DIR / entry["file"])
    return out


# --- workbook fingerprinting -------------------------------------------------
#
# The workbook is no longer kept in the repository, so losslessness cannot be
# demonstrated by diffing against a tracked file. Instead the content of the
# original workbook is reduced to a digest over its cell values, recorded in
# schema/provenance.yml, and reproduced from the YAML on demand.
#
# The digest covers values and their types only. Formatting, column widths and
# sheet styling are not part of it -- they carry no information and are not
# preserved when the workbook is regenerated.


def read_workbook_cells(path):
    """Read a workbook as an ordered list of (sheet name, {(row, col): value}).

    Rows that are entirely empty are dropped and the remaining rows are
    renumbered, so that trailing blank rows in the source do not make an
    otherwise identical workbook compare unequal.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    sheets = []
    for ws in wb.worksheets:
        cells = {}
        r_out = 0
        for row in ws.iter_rows(values_only=True):
            if all(c is None for c in row):
                continue
            r_out += 1
            for c_idx, value in enumerate(row, start=1):
                if value is not None:
                    cells[(r_out, c_idx)] = value
        sheets.append((ws.title, cells))
    return sheets


def cell_token(value):
    """Reduce a cell value to a (type tag, exact text) pair.

    The type tag is part of the digest, so an integer that became a float is a
    difference rather than a match.
    """
    if isinstance(value, bool):
        return ["b", "1" if value else "0"]
    if isinstance(value, int):
        return ["i", str(value)]
    if isinstance(value, float):
        return ["f", repr(value)]
    return ["s", str(value)]


def _sheet_payload(cells):
    return [
        [r, c, *cell_token(cells[(r, c)])]
        for (r, c) in sorted(cells)
        if cells[(r, c)] is not None
    ]


def _digest(payload) -> str:
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def workbook_digests(sheets):
    """Return (overall digest, {sheet: digest}, cell count) for a workbook."""
    payload = []
    per_sheet = {}
    n_cells = 0
    for name, cells in sheets:
        rows = _sheet_payload(cells)
        per_sheet[name] = _digest(rows)
        n_cells += len(rows)
        payload.append([name, rows])
    return _digest(payload), per_sheet, n_cells


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

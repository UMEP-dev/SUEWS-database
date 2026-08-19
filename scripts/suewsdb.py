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

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DB_DIR = REPO / "db"
SCHEMA_DIR = REPO / "schema"
XLSX = REPO / "database.xlsx"
TABLES_YML = SCHEMA_DIR / "tables.yml"

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

"""Build database.xlsx from the canonical YAML files.

The workbook is a build product and is not tracked. It exists for consumers
that still want a spreadsheet -- notably the UMEP SUEWS Database Manager and
Prepare QGIS plugins, which ship a bundled copy -- and is attached to releases
so there is a stable URL to fetch it from. Edit the YAML and rebuild; never
edit the spreadsheet.

    make xlsx                     # writes database.xlsx
    python scripts/yaml_to_xlsx.py OUT.xlsx
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from suewsdb import DB_DIR, XLSX, load_registry, load_yaml  # noqa: E402


def build(out_path: Path) -> None:
    registry = load_registry()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    for entry in registry["tables"]:
        table = load_yaml(DB_DIR / entry["file"])
        columns = table["columns"]
        ws = wb.create_sheet(title=table["table"])
        ws.append(columns)
        for key, record in table["entries"].items():
            ws.append([key] + [record.get(c) for c in columns[1:]])

    wb.save(out_path)
    print(f"wrote {out_path} ({len(registry['tables'])} sheets)")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else XLSX)

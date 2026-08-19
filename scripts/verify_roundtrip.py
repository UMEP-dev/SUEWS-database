"""Prove the YAML migration is lossless.

Regenerates a workbook from db/*.yml into a temporary file and compares it
cell by cell against database.xlsx. Type is compared as well as value, so an
integer that became a float counts as a difference.

    make verify
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from suewsdb import XLSX  # noqa: E402
from yaml_to_xlsx import build  # noqa: E402


def read(path: Path) -> dict:
    """Read a workbook into {sheet: {(row, col): value}}, skipping blank rows."""
    wb = openpyxl.load_workbook(path, data_only=True)
    out = {}
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
        out[ws.title] = cells
    return out


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        rebuilt_path = Path(tmp) / "rebuilt.xlsx"
        build(rebuilt_path)
        original = read(XLSX)
        rebuilt = read(rebuilt_path)

    diffs = []

    only_original = sorted(set(original) - set(rebuilt))
    only_rebuilt = sorted(set(rebuilt) - set(original))
    for name in only_original:
        diffs.append(f"sheet missing from rebuild: {name!r}")
    for name in only_rebuilt:
        diffs.append(f"sheet only in rebuild: {name!r}")

    n_cells = 0
    for name in original:
        if name not in rebuilt:
            continue
        a, b = original[name], rebuilt[name]
        n_cells += len(a)
        for pos in sorted(set(a) | set(b)):
            va, vb = a.get(pos), b.get(pos)
            if va != vb or type(va) is not type(vb):
                diffs.append(
                    f"{name}!R{pos[0]}C{pos[1]}: "
                    f"{va!r} ({type(va).__name__}) != {vb!r} ({type(vb).__name__})"
                )

    n_sheets = len(original)
    n_rows = sum(max((r for r, _ in cells), default=0) - 1 for cells in original.values())

    print()
    print(f"sheets compared      : {n_sheets}")
    print(f"data rows compared   : {n_rows}")
    print(f"non-empty cells      : {n_cells}")
    print(f"differences          : {len(diffs)}")
    for d in diffs[:40]:
        print("   ", d)
    if len(diffs) > 40:
        print(f"    ... and {len(diffs) - 40} more")

    if diffs:
        print("\nFAIL: round-trip is not lossless")
        return 1
    print("\nOK: database.xlsx is reproduced exactly from db/*.yml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

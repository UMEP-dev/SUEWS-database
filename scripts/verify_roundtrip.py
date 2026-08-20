"""Prove the YAML still reproduces the workbook it was migrated from.

Regenerates a workbook from db/*.yml and fingerprints its cell values and
types, then compares that against the digest recorded in schema/provenance.yml
when the original database.xlsx was migrated. The original file no longer lives
in the repository -- it is published as a release asset -- so the digest is what
makes losslessness checkable.

    make verify                          # against the recorded digest
    make verify XLSX=path/to/old.xlsx    # and cell by cell against a workbook

Passing a workbook additionally does a direct comparison and names every
differing cell, which is what to reach for when the digest does not match.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from suewsdb import (  # noqa: E402
    PROVENANCE_YML,
    load_yaml,
    read_workbook_cells,
    workbook_digests,
)
from yaml_to_xlsx import build  # noqa: E402


def compare_cells(original, rebuilt):
    """Name every cell that differs in value or in type."""
    diffs = []
    a_names = [n for n, _ in original]
    b_names = [n for n, _ in rebuilt]
    for name in a_names:
        if name not in b_names:
            diffs.append(f"sheet missing from rebuild: {name!r}")
    for name in b_names:
        if name not in a_names:
            diffs.append(f"sheet only in rebuild: {name!r}")

    b_map = dict(rebuilt)
    for name, a_cells in original:
        b_cells = b_map.get(name)
        if b_cells is None:
            continue
        for pos in sorted(set(a_cells) | set(b_cells)):
            va, vb = a_cells.get(pos), b_cells.get(pos)
            if va != vb or type(va) is not type(vb):
                diffs.append(
                    f"{name}!R{pos[0]}C{pos[1]}: "
                    f"{va!r} ({type(va).__name__}) != {vb!r} ({type(vb).__name__})"
                )
    return diffs


def main() -> int:
    reference = None
    for arg in sys.argv[1:]:
        if arg:
            reference = Path(arg)

    if not PROVENANCE_YML.exists():
        raise SystemExit(f"{PROVENANCE_YML} not found; cannot verify")
    recorded = load_yaml(PROVENANCE_YML)["content"]

    with tempfile.TemporaryDirectory() as tmp:
        rebuilt_path = Path(tmp) / "rebuilt.xlsx"
        build(rebuilt_path)
        rebuilt = read_workbook_cells(rebuilt_path)
        original = read_workbook_cells(reference) if reference else None

    digest, per_sheet, n_cells = workbook_digests(rebuilt)

    print()
    print(f"sheets rebuilt   : {len(rebuilt)}  (recorded {recorded['sheets']})")
    print(f"cells rebuilt    : {n_cells}  (recorded {recorded['cells']})")
    print(f"content digest   : {digest}")
    print(f"recorded digest  : {recorded['digest']}")

    ok = digest == recorded["digest"]
    if not ok:
        changed = [
            name
            for name, d in per_sheet.items()
            if recorded["per_sheet"].get(name) != d
        ]
        print(f"\ntables that differ: {', '.join(changed) or 'none (sheet set differs)'}")

    if original is not None:
        diffs = compare_cells(original, rebuilt)
        print(f"\ndirect comparison against {reference}: {len(diffs)} difference(s)")
        for d in diffs[:40]:
            print("   ", d)
        if len(diffs) > 40:
            print(f"    ... and {len(diffs) - 40} more")
        ok = ok and not diffs

    if not ok:
        print("\nFAIL: db/*.yml no longer reproduces the migrated workbook")
        return 1
    print("\nOK: db/*.yml reproduces the migrated workbook exactly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Record the fingerprint of the workbook the database was migrated from.

Run once, against the original database.xlsx, before it leaves the repository.
The result is what `make verify` compares against afterwards, so losslessness
stays provable without keeping a 220 KB binary in git.

    python scripts/record_provenance.py database.xlsx
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from suewsdb import (  # noqa: E402
    PROVENANCE_YML,
    dump_yaml,
    file_sha256,
    read_workbook_cells,
    workbook_digests,
)

HEADER = """\
# Provenance of the migration from Excel.
#
# `source` identifies the exact workbook the YAML was produced from. Its
# sha256 is the identity of the file published as a release asset.
#
# `content` is a digest over cell values and their types, computed by
# scripts/suewsdb.py. Unlike the file hash it is reproducible: regenerating a
# workbook from db/*.yml yields the same content digest, which is what
# `make verify` checks. Formatting and column widths are not covered, because
# they carry no information and are not reproduced.
#
# Do not edit by hand. If a value in db/ changes, the content digest is
# expected to change with it; record the new one deliberately.
"""


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("database.xlsx")
    if not path.exists():
        raise SystemExit(f"{path} not found -- this is a one-off bootstrap step")

    sheets = read_workbook_cells(path)
    overall, per_sheet, n_cells = workbook_digests(sheets)

    dump_yaml(
        {
            "source": {
                "filename": path.name,
                "sha256": file_sha256(path),
                "bytes": path.stat().st_size,
                "note": (
                    "The Excel workbook the database was migrated from, "
                    "published as a release asset. Retrievable from git "
                    "history at the commit preceding its removal."
                ),
            },
            "content": {
                "algorithm": "sha256 over canonical JSON of (row, col, type, text)",
                "sheets": len(sheets),
                "cells": n_cells,
                "digest": overall,
                "per_sheet": per_sheet,
            },
        },
        PROVENANCE_YML,
        header=HEADER,
    )
    print(f"sheets      : {len(sheets)}")
    print(f"cells       : {n_cells}")
    print(f"file sha256 : {file_sha256(path)}")
    print(f"content     : {overall}")
    print(f"-> {PROVENANCE_YML.relative_to(PROVENANCE_YML.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

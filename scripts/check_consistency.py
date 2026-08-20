"""Check the database against its own structure and its parameter groups.

Three classes of check:

  referential  every ID in a reference column resolves to an existing row
  linkage      parameters that schema/parameter_groups.yml marks as tightly
               coupled actually agree with each other
  hygiene      duplicated or malformed descriptive fields

Findings are reported, not fixed. Exit status is 1 if anything is found, so
this can be wired into CI once the known findings are resolved.

    make check
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from suewsdb import DB_DIR, SCHEMA_DIR, load_registry, load_yaml  # noqa: E402


def load_all():
    registry = load_registry()
    tables = {}
    for entry in registry["tables"]:
        tables[entry["sheet"]] = load_yaml(DB_DIR / entry["file"])
    return registry, tables


def check_referential(registry, tables):
    findings = []
    for entry in registry["tables"]:
        sheet = entry["sheet"]
        for column, target in (entry.get("references") or {}).items():
            known = tables[target]["entries"]
            for key, record in tables[sheet]["entries"].items():
                value = record.get(column)
                if value is not None and value not in known:
                    findings.append(
                        f"{sheet} {key}: {column}={value} is not a "
                        f"{target} ID"
                    )
    return findings


def check_lai_equation(tables):
    """A Veg entry must not mix LAI parameters fitted for different LAIEq."""
    findings = []
    lai = tables["Leaf Area Index"]["entries"]
    lgp = tables["Leaf Growth Power"]["entries"]
    for key, record in tables["Veg"]["entries"].items():
        a = lai.get(record.get("Leaf Area Index"), {}).get("LAIEq")
        b = lgp.get(record.get("Leaf Growth Power"), {}).get("LAIEq")
        if a is not None and b is not None and a != b:
            findings.append(
                f"Veg {key} ({record.get('Surface')}, {record.get('Origin')}): "
                f"Leaf Area Index row has LAIEq={a} but Leaf Growth Power row "
                f"has LAIEq={b}"
            )
    return findings


def check_conductance_model(tables):
    """Conductance coefficients are only valid for their own gsModel."""
    findings = []
    by_model = defaultdict(list)
    for key, record in tables["Conductance"]["entries"].items():
        by_model[record.get("gsModel")].append((key, record))
    for model, rows in sorted(by_model.items(), key=lambda x: (x[0] is None, x[0])):
        spans = []
        for name in ("G1", "G2"):
            values = [r.get(name) for _, r in rows if r.get(name) is not None]
            if len(values) > 1 and max(values) > 5 * min(values):
                spans.append(f"{name} spans {min(values)}-{max(values)}")
        if spans:
            findings.append(
                f"Conductance gsModel={model}: {'; '.join(spans)} across "
                f"{len(rows)} entries, so the coefficient sets are not "
                f"interchangeable within one model either"
            )
    return findings


def check_origin_hygiene(tables):
    """Origin is a free-text column; report values that cannot be a place."""
    findings = []
    for sheet, table in tables.items():
        if "Origin" not in table["columns"]:
            continue
        for key, record in table["entries"].items():
            value = record.get("Origin")
            if isinstance(value, str) and len(value) > 40:
                findings.append(
                    f"{sheet} {key}: Origin holds a description, not a place "
                    f"({value[:50]!r})"
                )
    return findings


def main() -> int:
    registry, tables = load_all()
    groups_path = SCHEMA_DIR / "parameter_groups.yml"
    groups = load_yaml(groups_path) if groups_path.exists() else {"groups": {}}

    sections = [
        ("referential integrity", check_referential(registry, tables)),
        ("linkage: leaf_area_index", check_lai_equation(tables)),
        ("linkage: surface_conductance", check_conductance_model(tables)),
        ("hygiene: Origin column", check_origin_hygiene(tables)),
    ]

    total = 0
    for title, findings in sections:
        print(f"\n{title}: {len(findings)} finding(s)")
        for f in findings:
            print(f"    {f}")
        total += len(findings)

    classified = sum(1 for g in groups.get("groups", {}).values() if g.get("linkage"))
    unclassified = len(groups.get("unclassified", []))
    print(
        f"\nparameter groups: {classified} classified, "
        f"{unclassified} tables still unclassified"
    )
    print(f"total findings: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())

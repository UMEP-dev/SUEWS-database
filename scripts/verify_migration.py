#!/usr/bin/env python3
"""Independent reverse verification of the table -> record migration.

The migration census (schema/migration_census.yml) is written by the
migration script itself, so it attests intent, not outcome. This script is
the outcome check: it loads the REMOVED table-format files from git history
and verifies, row by row against the records on disk, that

  coverage    every legacy row ID appears as exactly one record's legacy_id
  values      the multiset of leaf values in each record's parameters +
              legacy blocks equals that row's value cells (envelope and
              pointer cells excluded; -999 sentinels must sit under legacy
              or be absent)
  position    profile hours land on the right hour of the right side
              (hour column h -> side[h+1]) and ESTM layer triples keep
              their order (Surf_thick{i} -> thermal_layers.dz[i-1], etc.)
  pointers    every foreign-key cell resolves, through uses:, to the record
              whose legacy_id is the pointed-at row
  citations   every row's Ref maps to the record's source key via
              db/sources.yml (dangling IDs must be kept as source_legacy_ref)

Usage: python scripts/verify_migration.py [--rev <commit>]
  --rev  the git tree holding the table files (default: the parent of the
         commit that removed them, found automatically)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from check_db import iter_uses, load_all  # noqa: E402

SENTINEL = -999

ENVELOPE = {
    "default": {"ID", "Surface", "Origin", "Name", "Ref"},
    "Conductance": {"ID", "Origin", "Name", "Ref"},
    "Irrigation": {"ID", "Origin", "Name", "Ref"},
    "AnthropogenicEmission": {"ID", "Origin", "Name", "Ref"},
    "Snow": {"ID", "Origin", "Name", "Ref"},
    "OHM": {"ID", "Surface", "Origin", "Name", "Ref", "Season"},
    "Profiles": {"ID", "Profile Type", "Day", "Name", "City", "Country", "Ref"},
    "Spartacus Material": {"ID", "Name", "Material Type", "Color", "Ref"},
    "Region": {"ID", "Region"},
    "Country": {"ID", "Country", "City"},
    "Types": {"ID", "Origin", "Name", "Description", "Author", "Url",
              "imageSource", "ProfileType", "Period"},
}

POINTERS = {
    "NonVeg": {"Albedo", "Emissivity", "Water Storage", "Drainage",
               "Spartacus Surface", "SnowLimPatch", "OHMSummerWet",
               "OHMSummerDry", "OHMWinterWet", "OHMWinterDry"},
    "Veg": {"Albedo", "Emissivity", "Water Storage", "Leaf Area Index",
            "Leaf Growth Power", "Drainage", "Max Vegetation Conductance",
            "Porosity", "Biogen CO2", "SnowLimPatch", "Vegetation Growth",
            "OHMSummerWet", "OHMSummerDry", "OHMWinterWet", "OHMWinterDry"},
    "Water": {"Albedo", "Emissivity", "Water Storage", "Water State",
              "Drainage", "OHMSummerWet", "OHMSummerDry", "OHMWinterWet",
              "OHMWinterDry"},
    "Snow": {"Albedo", "Emissivity"},
    "Region": {"SnowClearingProfWD", "SnowClearingProfWE", "WaterUseProfManuWD",
               "WaterUseProfManuWE", "WaterUseProfAutoWD", "WaterUseProfAutoWE",
               "AnthropogenicCode", "IrrigationCode", "SoilTypeCode", "SnowCode",
               "Conductance", "Paved", "Buildings", "Bare Soil", "Grass",
               "Evergreen Tree", "Deciduous Tree", "Water"},
}
POINTERS["Country"] = POINTERS["Region"] | {
    "TraffProfWD", "TraffProfWE", "EnergyUseProfWD", "EnergyUseProfWE",
    "ActivityProfWD", "ActivityProfWE", "PopProfWD", "PopProfWE",
}
POINTERS["Types"] = {"Buildings", "Paved"}
POINTERS["Spartacus Surface"] = {
    f"{p}{i}Material" for p in ("r", "w") for i in range(1, 6)
}


def is_missing(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "nan", "none"))


def norm(v):
    """Normalise for multiset comparison across YAML round-trips."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 9)
    return v


def leaves(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from leaves(v)
    elif isinstance(node, list):
        for v in node:
            yield from leaves(v)
    elif node is not None:
        yield norm(node)


def git_show(rev, path):
    out = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{rev}:{path}"],
        capture_output=True, text=True, check=True,
    )
    return yaml.safe_load(out.stdout)


def find_pre_removal_rev():
    out = subprocess.run(
        ["git", "-C", str(ROOT), "log", "--format=%H", "--diff-filter=D",
         "--", "db/references.yml"],
        capture_output=True, text=True, check=True,
    )
    removal = out.stdout.split()[0]
    return f"{removal}^"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default=None)
    args = ap.parse_args()
    rev = args.rev or find_pre_removal_rev()

    registry = git_show(rev, "schema/tables.yml")["tables"]
    tables = {t["sheet"]: git_show(rev, "db/" + t["file"])["entries"]
              for t in registry}
    records, sources, places = load_all()

    errors = []

    # --- source-key map: legacy Ref id -> citation key
    ref_key = {}
    for key, src in sources.items():
        if "legacy_id" in src:
            ref_key[src["legacy_id"]] = key
        for rid in src.get("legacy_ids", []):
            ref_key[rid] = key

    # --- coverage: every row ID -> exactly one record
    legacy_map = {}
    for path, rec in records.items():
        ids = rec.get("legacy_id")
        ids = ids if isinstance(ids, list) else [ids]
        for rid in ids:
            if rid is None:
                continue
            if rid in legacy_map:
                errors.append(f"row {rid} claimed by both {legacy_map[rid]} and {path}")
            legacy_map[rid] = path

    all_ids = {rid for sheet, rows in tables.items() if sheet != "References"
               for rid in rows}
    missing = all_ids - set(legacy_map)
    for rid in sorted(missing):
        errors.append(f"row {rid} appears in no record's legacy_id")
    ghosts = set(legacy_map) - all_ids
    for rid in sorted(ghosts):
        errors.append(f"record {legacy_map[rid]} claims nonexistent row {rid}")

    ref_rows = set(tables["References"])
    for rid in ref_rows:
        if rid not in ref_key:
            errors.append(f"References row {rid} reaches no sources.yml key")

    # --- per-row checks
    uses_cache = {}

    def flat_uses(rec):
        key = id(rec)
        if key not in uses_cache:
            refs = set(iter_uses(rec.get("uses", {})))
            rr = rec.get("region_ref")
            if isinstance(rr, str):
                refs.add(rr)
            for side in ("roof", "wall"):
                for layer in rec.get("parameters", {}).get(side, []):
                    if isinstance(layer, dict) and isinstance(layer.get("material"), str):
                        refs.add(layer["material"])
            uses_cache[key] = refs
        return uses_cache[key]

    n_value_cells = 0
    for sheet, rows in tables.items():
        if sheet == "References":
            continue
        env = ENVELOPE.get(sheet, ENVELOPE["default"])
        pointers = POINTERS.get(sheet, set())
        for rid, row in rows.items():
            rec = records.get(legacy_map.get(rid, ""), None)
            if rec is None:
                continue  # already reported as coverage error
            # citations
            ref = row.get("Ref")
            if not is_missing(ref):
                want = ref_key.get(ref)
                got = rec.get("source")
                if want is not None:
                    if got != want:
                        errors.append(f"{sheet} {rid}: source {got!r} != {want!r}")
                else:
                    if got != "unreferenced" or rec.get("source_legacy_ref") != ref:
                        errors.append(
                            f"{sheet} {rid}: dangling Ref {ref} not kept as "
                            f"source_legacy_ref on {legacy_map[rid]}"
                        )
            # pointers resolve to the right record
            for col in pointers:
                val = row.get(col)
                if is_missing(val):
                    continue
                if sheet in ("Region", "Country") and col == "Region":
                    continue
                target = legacy_map.get(val)
                if target is None:
                    errors.append(f"{sheet} {rid}: pointer {col}={val} maps to no record")
                elif target not in flat_uses(rec):
                    errors.append(
                        f"{sheet} {rid}: {col} -> {target} absent from uses of "
                        f"{legacy_map[rid]}"
                    )
            # Country's Region-by-name link
            if sheet == "Country" and not is_missing(row.get("Region")):
                rr = rec.get("region_ref")
                region_rec = records.get(rr) if isinstance(rr, str) else None
                if region_rec is None or region_rec.get("region") != row["Region"]:
                    errors.append(f"Country {rid}: region_ref does not reach "
                                  f"region {row['Region']!r}")
            # Types pointers live in uses when resolvable
            if sheet == "Types":
                for col in ("Buildings", "Paved"):
                    val = row.get(col)
                    if not is_missing(val) and val in legacy_map:
                        if legacy_map[val] not in flat_uses(rec):
                            errors.append(f"Types {rid}: {col} -> {legacy_map[val]} "
                                          "absent from uses")

            # value cells: multiset + positional checks
            if sheet == "Profiles":
                day = str(row.get("Day"))
                side = {"Weekday": "working_day", "Weekend": "holiday",
                        "Holiday": "holiday"}.get(day)
                sides = rec.get("parameters", {})
                if day == "Holiday" and "public_holiday" in sides:
                    side = "public_holiday"
                prof = sides.get(side, {})
                legacy_prof = rec.get("legacy", {}).get(side, {})
                for h in range(24):
                    v = row.get(h)
                    if is_missing(v):
                        continue
                    n_value_cells += 1
                    if v == SENTINEL:
                        if legacy_prof.get(h) != v:
                            errors.append(f"Profiles {rid}: sentinel hour {h} "
                                          f"not under legacy.{side}")
                    elif norm(prof.get(h + 1)) != norm(v):
                        errors.append(
                            f"Profiles {rid}: hour {h} = {v} not at "
                            f"parameters.{side}.{h + 1} of {legacy_map[rid]}"
                        )
                continue
            value_cols = [c for c in row
                          if c not in env and c not in pointers
                          and not is_missing(row.get(c))
                          and not (sheet == "Country" and c == "Region")]
            expected = Counter(norm(row[c]) for c in value_cols)
            got = Counter(leaves(rec.get("parameters", {})))
            got.update(leaves(rec.get("legacy", {})))
            # constructions: material names resolved to paths are pointers,
            # not values; drop strings that are record paths
            got = Counter({k: n for k, n in got.items()
                           if not (isinstance(k, str) and k.startswith("records/"))})
            n_value_cells += sum(expected.values())
            if expected != got:
                miss = expected - got
                extra = got - expected
                errors.append(
                    f"{sheet} {rid} vs {legacy_map[rid]}: value multiset differs "
                    f"(missing {dict(miss)}, extra {dict(extra)})"
                )
            # ESTM positional check on surface layers
            if sheet == "ESTM" and "thermal_layers" in rec.get("parameters", {}):
                tl = rec["parameters"]["thermal_layers"]
                pos = 0
                for i in range(1, 6):
                    t, kk, rc = (row.get(f"Surf_thick{i}"), row.get(f"Surf_k{i}"),
                                 row.get(f"Surf_rhoCp{i}"))
                    vals = [None if (is_missing(x) or x == SENTINEL) else x
                            for x in (t, kk, rc)]
                    if all(x is None for x in (t, kk, rc)) or all(
                        is_missing(x) for x in (t, kk, rc)
                    ):
                        continue
                    for arr, want in (("dz", vals[0]), ("k", vals[1]),
                                      ("rho_cp", vals[2])):
                        have = tl[arr][pos] if pos < len(tl[arr]) else "absent"
                        if norm(have) != norm(want) and want is not None:
                            errors.append(
                                f"ESTM {rid}: layer {i} {arr} = {want} landed as "
                                f"{have} at position {pos}"
                            )
                    pos += 1

    print(f"tables at {rev}: {sum(len(r) for s, r in tables.items())} rows, "
          f"{n_value_cells} value cells checked")
    print(f"records on disk: {len(records)}")
    print(f"errors: {len(errors)}")
    for e in errors[:40]:
        print("  -", e)
    if len(errors) > 40:
        print(f"  ... and {len(errors) - 40} more")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

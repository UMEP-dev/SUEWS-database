#!/usr/bin/env python3
"""Integrity checks for the record-format database.

Structural pass (stdlib + PyYAML only):
  - every record/archetype file parses and its `record:`/`archetype:` path
    matches its location on disk
  - required envelope fields are present (schema_version, target, source on
    evidence records)
  - `source:` keys resolve in db/sources.yml, `place:` slugs in db/places.yml
  - every `uses:` reference resolves to an existing record file
  - material references inside construction records resolve

Model pass (--supy, needs supy importable):
  - every evidence record's parameter fragment validates against the supy
    class its `target:` names (wrapped as RefValue with the record's citation,
    exactly as the exporter emits it)
  - every archetype's assembled fragment validates the same way

Exit code 0 = all checks pass.

Usage:
  python scripts/check_db.py            # structural only
  python scripts/check_db.py --supy     # structural + supy validation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db"

# Repo-local record targets that have no supy class
LOCAL_TARGETS = {"material", "construction", "typology", "ohm_coefficients"}


def load_all():
    records = {}
    for sub in ("records", "archetypes"):
        base = DB / sub
        if not base.exists():
            continue
        for fp in sorted(base.rglob("*.yml")):
            rel = fp.relative_to(DB).with_suffix("")
            records[str(rel)] = yaml.safe_load(fp.read_text())
    sources = yaml.safe_load((DB / "sources.yml").read_text())["sources"]
    places = yaml.safe_load((DB / "places.yml").read_text())["places"]
    return records, sources, places


def iter_uses(uses):
    """Yield every record reference inside a `uses:` block."""
    for v in uses.values():
        if isinstance(v, dict):
            yield from iter_uses(v)
        elif isinstance(v, str):
            yield v


def structural_check(records, sources, places):
    errors = []
    for path, rec in records.items():
        kind = "record" if path.startswith("records/") else "archetype"
        declared = rec.get("record") or rec.get("archetype")
        if declared != path:
            errors.append(f"{path}: declared path {declared!r} != file location")
        if not rec.get("schema_version"):
            errors.append(f"{path}: missing schema_version")
        if not rec.get("target"):
            errors.append(f"{path}: missing target")
        if kind == "record":
            src = rec.get("source")
            if src is None:
                errors.append(f"{path}: missing source")
            elif src not in sources:
                errors.append(f"{path}: source {src!r} not in sources.yml")
        else:
            src = rec.get("source")
            if src is not None and src not in sources:
                errors.append(f"{path}: source {src!r} not in sources.yml")
        place = rec.get("place")
        if place is not None and place not in places:
            errors.append(f"{path}: place {place!r} not in places.yml")
        for ref in iter_uses(rec.get("uses", {})):
            if ref not in records:
                errors.append(f"{path}: uses unresolved reference {ref!r}")
        if rec.get("target") == "construction":
            for side in ("roof", "wall"):
                for layer in rec.get("parameters", {}).get(side, []):
                    mat = layer.get("material")
                    if isinstance(mat, str) and mat not in records:
                        errors.append(f"{path}: material ref {mat!r} unresolved")
        region_ref = rec.get("region_ref")
        if isinstance(region_ref, str) and region_ref.startswith("archetypes/"):
            if region_ref not in records:
                errors.append(f"{path}: region_ref {region_ref!r} unresolved")
    return errors


# ---------------- supy validation ----------------


def wrap_ref(params, ref_info):
    """Wrap each leaf value as a RefValue dict, mirroring the exporter.

    Three shapes stay bare because their supy fields are not FlexibleRefValue:
    WeeklyProfile day fields (daywat/daywatper), the hour dictionaries inside
    profile sides, and any string/bool. A list of scalars wraps as ONE
    RefValue holding the whole list (thermal_layers.dz etc.).
    """
    BARE_CONTAINERS = {"daywat", "daywatper", "working_day", "holiday"}

    def wrap(node, bare=False):
        if isinstance(node, dict):
            return {
                k: wrap(v, bare or k in BARE_CONTAINERS) for k, v in node.items()
            }
        if isinstance(node, list):
            if all(v is None or isinstance(v, (int, float)) for v in node):
                return node if bare else {"value": node, "ref": ref_info}
            return [wrap(v, bare) for v in node]
        if bare or node is None or isinstance(node, (str, bool)):
            return node
        return {"value": node, "ref": ref_info}

    return wrap(params)


def supy_fragment(rec, sources):
    """Build the supy-ready fragment for a record, as the exporter emits it."""
    src_key = rec.get("source")
    src = sources.get(src_key, {}) if src_key else {}
    desc_bits = []
    if rec.get("place"):
        desc_bits.append(rec["place"])
    if rec.get("representativeness"):
        desc_bits.append(rec["representativeness"])
    ref_info = {
        "ID": src_key,
        "DOI": src.get("doi"),
        "desc": ", ".join(desc_bits) or None,
    }
    params = {k: v for k, v in rec.get("parameters", {}).items() if k != "context"}
    return wrap_ref(params, ref_info)


def supy_check(records, sources):
    from supy.data_model.core import human_activity, hydro, ohm, profile, site, surface

    class_for = {
        "land_cover.paved": surface.PavedProperties,
        "land_cover.bldgs": surface.BldgsProperties,
        "land_cover.bsoil": surface.BsoilProperties,
        "land_cover.water": surface.WaterProperties,
        "land_cover.evetr": site.EvetrProperties,
        "land_cover.dectr": site.DectrProperties,
        "land_cover.grass": site.GrassProperties,
        "land_cover.common": surface.SurfaceProperties,
        "snow": site.SnowParams,
        "conductance": site.Conductance,
        "irrigation": human_activity.IrrigationParams,
        "anthropogenic_emissions": human_activity.AnthropogenicEmissions,
        "ohm_coefficients": ohm.OHMCoefficients,
    }
    errors = []
    n_checked = 0
    for path, rec in records.items():
        target = rec.get("target")
        if target in ("site", "typology", "material", "construction"):
            continue
        params = rec.get("parameters", {})
        if not params:
            continue
        if target.startswith("profile."):
            cls = profile.HourlyProfile
            frag = {
                k: v for k, v in params.items() if k in ("working_day", "holiday")
            }
            if not frag:
                continue  # placeholder rows live entirely under legacy
            if len(frag) == 1:
                # one-sided source data; mirror the present side purely to
                # exercise the format check (the record itself stays one-sided)
                only = next(iter(frag.values()))
                frag = {"working_day": only, "holiday": only}
        elif target == "ohm_coefficients":
            cls = class_for[target]
            frag = supy_fragment(rec, sources)
        elif target in class_for:
            cls = class_for[target]
            frag = supy_fragment(rec, sources)
            # site-level observation config is exported separately
            frag.pop("soil_observation", None)
        else:
            errors.append(f"{path}: unknown target {target!r}")
            continue
        try:
            cls.model_validate(frag)
            n_checked += 1
        except Exception as e:  # noqa: BLE001 - report every failure
            msg = str(e).split("\n")[0:4]
            errors.append(f"{path}: {target}: " + " | ".join(msg))
    return errors, n_checked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--supy", action="store_true", help="also validate against supy")
    args = ap.parse_args()

    records, sources, places = load_all()
    errors = structural_check(records, sources, places)
    print(f"structural: {len(records)} files checked, {len(errors)} errors")
    for e in errors[:30]:
        print("  -", e)

    if args.supy:
        supy_errors, n = supy_check(records, sources)
        print(f"supy validation: {n} fragments validated, {len(supy_errors)} errors")
        for e in supy_errors[:30]:
            print("  -", e)
        errors += supy_errors

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

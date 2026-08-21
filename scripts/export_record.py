#!/usr/bin/env python3
"""Export a record or archetype as a model-ready supy YAML fragment.

Every parameter is emitted as a RefValue -- {value, ref: {ID, DOI, desc}} --
carrying the citation of the record it came from, so a fragment assembled
from several records keeps per-value provenance. The output can be pasted
directly into a SUEWS YAML configuration under the path named by `target`
(e.g. sites[].properties.land_cover.paved).

Usage:
  python scripts/export_record.py records/surfaces/grass/helsinki--jarvi2014--phenology
  python scripts/export_record.py archetypes/surfaces/bldgs/helsinki--kumpula
  python scripts/export_record.py --list archetypes/surfaces
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from check_db import (  # noqa: F401
    load_all,
    suews_configuration_fragment,
    wrap_ref,
)

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db"


class PlainDumper(yaml.SafeDumper):
    """No anchors/aliases: repeated citation blocks stay readable."""

    def ignore_aliases(self, data):
        return True


def deep_merge(base, extra):
    """Merge extra into base, extra winning on conflicts."""
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def assemble(path, records, sources, depth=0):
    """Build the SUEWS configuration fragment for a record or archetype path."""
    rec = records[path]
    if depth > 4:
        raise RuntimeError(f"reference chain too deep at {path}")
    fragment = {}
    uses = rec.get("uses", {})
    for slot, ref in uses.items():
        if slot == "ohm":
            coef = {}
            for season, season_ref in ref.items():
                sub = suews_configuration_fragment(records[season_ref], sources)
                coef[season] = sub
            deep_merge(fragment, {"ohm_coef": coef})
        elif slot in ("albedo", "emissivity", "water_storage", "drainage",
                      "water_state", "leaf_area_index", "leaf_growth_power",
                      "max_vegetation_conductance", "porosity", "biogen_co2",
                      "snow_lim_patch", "vegetation_growth"):
            deep_merge(
                fragment, suews_configuration_fragment(records[ref], sources)
            )
        elif slot == "construction":
            # layered fabric feeds vertical_layers, not a flat surface
            # parameter; it stays a reference (visible under uses:) rather
            # than entering the fragment
            continue
        elif isinstance(ref, str) and ref in records:
            deep_merge(
                fragment, {slot: assemble(ref, records, sources, depth + 1)}
            )
        # unresolved references are a data error make check reports; they
        # never enter a fragment
    own = suews_configuration_fragment(rec, sources)
    deep_merge(fragment, own)
    return fragment


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="record/archetype path (as in the file's record: field)")
    ap.add_argument("--list", action="store_true", help="list entries under a prefix")
    args = ap.parse_args()

    records, sources, places = load_all()
    if args.list:
        for p in sorted(records):
            if p.startswith(args.path):
                print(p)
        return 0
    path = args.path.removesuffix(".yml").removeprefix("db/")
    if path not in records:
        print(f"not found: {path}", file=sys.stderr)
        return 1
    rec = records[path]
    fragment = assemble(path, records, sources)
    header = (
        f"# {path}\n"
        f"# target: {rec.get('target')}  (paste under this path in a SUEWS YAML config)\n"
        f"# schema_version: {rec.get('schema_version')}\n"
    )
    print(header + yaml.dump(fragment, Dumper=PlainDumper, sort_keys=False,
                             allow_unicode=True, width=88))
    return 0


if __name__ == "__main__":
    sys.exit(main())

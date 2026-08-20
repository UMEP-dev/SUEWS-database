#!/usr/bin/env python3
"""End-to-end proof that exported fragments feed SUEWS directly.

Takes supy's own sample configuration, merges in two database exports --
the Kumpula buildings archetype and a grass phenology evidence record --
writes the result to a temporary file and loads it with
SUEWSConfig.from_yaml, then prints the values with their citations as supy
sees them.

Needs supy importable:
  uv run --with pyyaml --with supy --no-project python scripts/e2e_sample_config.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_db import load_all  # noqa: E402
from export_record import assemble, deep_merge  # noqa: E402

import supy  # noqa: E402
from supy.data_model.core.config import SUEWSConfig  # noqa: E402

ARCHETYPE = "archetypes/surfaces/bldgs/helsinki--kumpula"
RECORD = "records/surfaces/grass/helsinki--jarvi2014--phenology"


def main():
    sample = Path(supy.__file__).parent / "sample_data" / "sample_config.yml"
    cfg = yaml.safe_load(sample.read_text())
    records, sources, _places = load_all()

    frag = assemble(ARCHETYPE, records, sources)
    frag.pop("_notes", None)
    deep_merge(cfg["sites"][0]["properties"]["land_cover"]["bldgs"], frag)
    deep_merge(
        cfg["sites"][0]["properties"]["land_cover"]["grass"],
        assemble(RECORD, records, sources),
    )

    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False, allow_unicode=True)
        out = fh.name

    config = SUEWSConfig.from_yaml(out)
    b = config.sites[0].properties.land_cover.bldgs
    g = config.sites[0].properties.land_cover.grass
    print("loaded OK via SUEWSConfig.from_yaml")
    print(f"bldgs.alb = {b.alb.value}  ref: {b.alb.ref.ID} doi:{b.alb.ref.DOI}")
    print(f"bldgs.emis = {b.emis.value}  ref: {b.emis.ref.ID}")
    print(f"grass lai.gdd_full = {g.lai.gdd_full.value}  ref: {g.lai.gdd_full.ref.ID}")
    print(f"grass lai.sdd_full = {g.lai.sdd_full.value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

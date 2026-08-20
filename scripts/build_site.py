#!/usr/bin/env python3
"""Build the static database browser site from db/.

Generates a self-contained static site under site/ (gitignored; built and
deployed by CI). The site is a linked graph with a faceted search front end:

  index.html                    faceted browser over every entry: filter by
                                kind, surface, family, place,
                                representativeness and source, plus free
                                text; result cards show parameter values
                                inline; filter state lives in the URL hash
  data/index.json               the search index the browser runs on
  records/<path>.html           per-record page: envelope with clickable
                                place/source/facet chips, parameters,
                                model-ready fragment, "used by" backlinks
                                and "same study, same place" siblings
  archetypes/<path>.html        per-archetype page with resolved uses
  place/<slug>.html             every record at a place, grouped by family
  source/<key>.html             the citation and every record citing it

Design follows the suews.io token palette (dark default).

Usage: python scripts/build_site.py [--out site]
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_db import iter_uses, load_all  # noqa: E402
from export_record import PlainDumper, assemble  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/UMEP-dev/SUEWS-database"
DOCS = "https://docs.suews.io/latest/inputs/yaml"
DOCS_REF = f"{DOCS}/config-reference"

SURFACES = {"paved", "bldgs", "evetr", "dectr", "grass", "bsoil", "water",
            "snow", "common"}

# target -> the config-reference page documenting its fields
TARGET_DOC = {
    "land_cover.paved": "pavedproperties",
    "land_cover.bldgs": "bldgsproperties",
    "land_cover.evetr": "evetrproperties",
    "land_cover.dectr": "dectrproperties",
    "land_cover.grass": "grassproperties",
    "land_cover.bsoil": "bsoilproperties",
    "land_cover.water": "waterproperties",
    "land_cover.common": "surfaceproperties",
    "snow": "snowparams",
    "conductance": "conductance",
    "irrigation": "irrigationparams",
    "anthropogenic_emissions": "anthropogenicemissions",
    "ohm_coefficients": "ohmcoefficients",
}

# nested containers -> the sub-class page holding their leaf fields
CONTAINER_DOC = [
    ("lai.lai_power.", "laipowercoefficients"),
    ("lai.", "laiparams"),
    ("storage_drain_params.", "storagedrainparams"),
    ("thermal_layers.", "thermallayers"),
    ("soil_observation.", "soilobservationconfig"),
    ("ohm_coef.", "ohmcoefficients"),
    ("waterdist.", "waterdistribution"),
    ("heat.", "anthropogenicheat"),
    ("co2.", "co2params"),
    ("daywat.", "weeklyprofile"),
    ("daywatper.", "weeklyprofile"),
]

FAMILY_LABEL = {
    "albedo": "Albedo", "emissivity": "Emissivity",
    "anohm": "AnOHM coefficients", "water-storage": "Water storage",
    "drainage": "Drainage", "water-state": "Water state",
    "snow-lim": "Snowpack limit", "soil": "Soil properties",
    "lai": "Leaf area index", "lai-power": "LAI growth coefficients",
    "phenology": "Phenology thresholds", "max-conductance": "Maximum conductance",
    "porosity": "Porosity", "biogen-co2": "Biogenic CO2",
    "thermal-layers": "Thermal layers", "ohm": "OHM coefficients",
    "conductance": "Surface conductance", "irrigation": "Irrigation",
    "anthropogenic": "Anthropogenic emissions", "materials": "Material",
    "constructions": "Construction", "snow": "Snow parameters",
    "surface": "Surface properties",
}
SURFACE_LABEL = {
    "paved": "Paved", "bldgs": "Buildings", "evetr": "Evergreen trees",
    "dectr": "Deciduous trees", "grass": "Grass", "bsoil": "Bare soil",
    "water": "Water", "snow": "Snow", "common": "Any surface",
}

# land-cover accent colour class (drives card top borders and title tags)
SURFACE_ACC = {
    "grass": "acc-veg", "dectr": "acc-veg", "evetr": "acc-veg",
    "water": "acc-water", "paved": "acc-built", "bldgs": "acc-built",
    "bsoil": "acc-soil", "snow": "acc-snow", "common": "acc-none",
}
# the browser's land-cover facet order: the 7 SUEWS covers, then a divider,
# then cross-surface entries
LC_ORDER = ["paved", "bldgs", "evetr", "dectr", "grass", "bsoil", "water"]


def doc_url(target, dotted):
    """Docs page + anchor for one parameter path, or None for repo-local."""
    if not target or target in ("material", "construction", "typology", "site"):
        return None
    if target.startswith("profile."):
        return f"{DOCS_REF}/hourlyprofile.html"
    base = TARGET_DOC.get(target)
    if base is None:
        return None
    page_slug = base
    for prefix, sub in CONTAINER_DOC:
        if dotted.startswith(prefix):
            page_slug = sub
            break
    leaf = dotted.rsplit(".", 1)[-1]
    if leaf.isdigit() or dotted.startswith("context"):
        return f"{DOCS_REF}/{page_slug}.html"
    return f"{DOCS_REF}/{page_slug}.html#input-option-{leaf}"

CSS = """
:root {
  --sun-gold: #F7B538; --energy-orange: #E85D04; --water-blue: #0077B6;
  --water-blue-light: #48CAE4; --sky-blue: #5DADE2; --veg-green: #09a25c;
  --urban-slate: #2D3142; --bg-primary: #0F1119; --bg-secondary: #1A1D2E;
  --bg-card: rgba(255,255,255,0.03); --bg-card-hover: rgba(255,255,255,0.06);
  --border-light: rgba(255,255,255,0.08); --border-medium: rgba(255,255,255,0.16);
  --text-primary: rgba(255,255,255,0.92); --text-secondary: rgba(255,255,255,0.7);
  --text-muted: rgba(255,255,255,0.55);
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg-primary); color: var(--text-primary);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
a { color: var(--sky-blue); text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap { max-width: 1160px; margin: 0 auto; padding: 1.6rem 1.25rem 4rem; }
header.site { border-bottom: 1px solid var(--border-light); background: var(--bg-secondary); }
header.site .wrap { display: flex; align-items: baseline; gap: 1rem; padding: 1rem 1.25rem; }
header.site h1 { font-size: 1.1rem; margin: 0; white-space: nowrap; }
header.site h1 a { color: var(--text-primary); }
header.site .sub { color: var(--text-muted); font-size: 0.85rem; }
.stats { display: flex; flex-wrap: wrap; gap: 2rem; margin: 1.4rem 0;
  padding: 1rem 1.4rem; border: 1px solid var(--border-light); border-radius: 12px;
  background: var(--bg-card); }
.stat b { display: block; font-size: 1.45rem; color: var(--sun-gold); }
.stat span { color: var(--text-muted); font-size: 0.82rem; }
h2 { margin: 2rem 0 0.8rem; font-size: 1.2rem; }
h3 { margin: 1.5rem 0 0.5rem; font-size: 1rem; color: var(--text-secondary); }
.chip { display: inline-block; padding: 0.12rem 0.65rem; border-radius: 999px;
  font-size: 0.78rem; border: 1px solid var(--border-medium);
  color: var(--text-secondary); margin: 0 0.3rem 0.3rem 0; cursor: pointer;
  background: transparent; font-family: inherit; }
a.chip { color: var(--sky-blue); }
a.chip:hover { text-decoration: none; border-color: var(--sky-blue); }
.chip.on { background: var(--sun-gold); color: #1A1D2E; border-color: var(--sun-gold);
  font-weight: 600; }
.chip .n { opacity: 0.65; font-size: 0.72rem; margin-left: 0.25rem; }
.facet-group { margin: 0.35rem 0; }
.facet-group .fl { display: inline-block; width: 110px; color: var(--text-muted);
  font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em;
  vertical-align: top; padding-top: 0.2rem; }
.facet-body { display: inline-block; max-width: calc(100% - 120px); }
input.search { width: 100%; padding: 0.7rem 1.1rem; border-radius: 10px;
  border: 1px solid var(--border-medium); background: var(--bg-secondary);
  color: var(--text-primary); font-size: 1rem; margin: 0.9rem 0 0.9rem; }
input.search:focus { outline: 2px solid var(--sun-gold); border-color: transparent; }
#nres { color: var(--text-muted); font-size: 0.85rem; margin: 0.4rem 0 0.8rem; }
.results { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 0.7rem; }
.rcard { border: 1px solid var(--border-light); border-radius: 10px;
  background: var(--bg-card); padding: 0.75rem 0.95rem; }
.rcard:hover { background: var(--bg-card-hover); border-color: var(--border-medium); }
.rcard .t { font-weight: 600; font-size: 0.93rem; }
.rcard .meta { margin: 0.25rem 0 0.35rem; }
.rcard .meta .chip { cursor: default; }
.vals { color: var(--text-secondary); font-size: 0.8rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; line-height: 1.7; }
.vals b { color: var(--water-blue-light); font-weight: 500; }
table.kv { border-collapse: collapse; width: 100%; margin: 0.6rem 0 1rem; }
table.kv td, table.kv th { text-align: left; padding: 0.4rem 0.75rem;
  border-bottom: 1px solid var(--border-light); font-size: 0.92rem; vertical-align: top; }
table.kv th { color: var(--text-muted); font-weight: 500; width: 220px; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
code { background: var(--bg-card); padding: 0.1rem 0.35rem; border-radius: 5px;
  font-size: 0.88em; }
pre { background: var(--bg-secondary); border: 1px solid var(--border-light);
  border-radius: 10px; padding: 1rem 1.2rem; overflow-x: auto; font-size: 0.84rem;
  line-height: 1.5; }
.crumbs { color: var(--text-muted); font-size: 0.84rem; margin-bottom: 1rem; }
.crumbs a { color: var(--text-muted); }
.actions { display: flex; gap: 1rem; margin: 1.2rem 0; flex-wrap: wrap; }
.actions a { padding: 0.45rem 0.9rem; border: 1px solid var(--border-medium);
  border-radius: 8px; font-size: 0.88rem; }
ul.linked { list-style: none; padding: 0; margin: 0.4rem 0; }
ul.linked li { margin: 0.22rem 0; font-size: 0.92rem; }
.pill-row { margin: 0.6rem 0 1rem; }
footer { margin-top: 3.5rem; padding-top: 1.2rem; border-top: 1px solid var(--border-light);
  color: var(--text-muted); font-size: 0.82rem; }
.hidden { display: none; }
.headline { margin: 0.2rem 0 0.6rem; font-size: 1.05rem; }
.headline b { color: var(--water-blue-light); font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.headline .v { color: var(--sun-gold); font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.subtitle { color: var(--text-muted); font-size: 0.95rem; font-weight: 400; }
table.params td:first-child { width: 320px; }
table.params .val { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--sun-gold); }
table.params.muted .val { color: var(--text-secondary); }
table.params .hrs { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.8rem; color: var(--text-secondary); word-spacing: 0.3em; }
.copywrap { position: relative; }
.copywrap button.copy { position: absolute; top: 0.55rem; right: 0.55rem;
  padding: 0.3rem 0.8rem; border-radius: 7px; border: 1px solid var(--border-medium);
  background: var(--bg-card); color: var(--text-secondary); cursor: pointer;
  font: inherit; font-size: 0.8rem; }
.copywrap button.copy:hover { border-color: var(--sun-gold); color: var(--text-primary); }
.copywrap button.copy.ok { background: var(--veg-green); color: #fff;
  border-color: var(--veg-green); }
.acc-veg { --acc: var(--veg-green); }
.acc-water { --acc: var(--water-blue-light); }
.acc-built { --acc: var(--energy-orange); }
.acc-soil { --acc: var(--sun-gold); }
.acc-snow { --acc: #cfe8ff; }
.acc-none { --acc: rgba(255,255,255,0.25); }
.hero { padding: 1.6rem 0 0.2rem; }
.hero h2 { margin: 0 0 0.3rem; font-size: 1.75rem; letter-spacing: -0.01em; }
.hero p { margin: 0; color: var(--text-secondary); max-width: 680px; }
.statline { display: flex; flex-wrap: wrap; gap: 1.8rem; margin: 1rem 0 1.4rem;
  color: var(--text-muted); font-size: 0.85rem; }
.statline b { color: var(--sun-gold); font-weight: 600; margin-right: 0.3rem; }
.layout { display: grid; grid-template-columns: 236px minmax(0, 1fr); gap: 1.8rem; }
.rail h4 { margin: 0 0 0.4rem; font-size: 0.72rem; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600; }
.fgroup { margin-bottom: 1.35rem; }
.fitem { display: flex; justify-content: space-between; align-items: baseline;
  gap: 0.4rem; width: 100%; padding: 0.22rem 0.55rem; border: none;
  border-radius: 7px; font-size: 0.88rem; color: var(--text-secondary);
  cursor: pointer; background: transparent; font-family: inherit;
  text-align: left; }
.fitem:hover { background: var(--bg-card-hover); color: var(--text-primary); }
.fitem.on { background: var(--sun-gold); color: #1A1D2E; font-weight: 600; }
.fitem .fv { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.fitem .n { font-size: 0.75rem; opacity: 0.6; }
.fitem .sw { flex: 0 0 8px; width: 8px; height: 8px; border-radius: 2px;
  background: var(--acc); align-self: center; }
.fdiv { border-top: 1px solid var(--border-light); margin: 0.45rem 0.55rem; }
.fmore { padding: 0.22rem 0.55rem; font-size: 0.8rem; color: var(--text-muted); }
.maplink { display: block; padding: 0.22rem 0.55rem; font-size: 0.8rem; }
.results2 { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 0.8rem; }
.card2 { border: 1px solid var(--border-light); border-top: 3px solid var(--acc);
  border-radius: 10px; background: var(--bg-card); padding: 0.75rem 0.95rem 0.85rem; }
.card2:hover { background: var(--bg-card-hover); border-color: var(--border-medium);
  border-top-color: var(--acc); }
.card2 .t { font-weight: 600; font-size: 0.95rem; margin-bottom: 0.15rem; }
.card2 .meta2 { color: var(--text-muted); font-size: 0.8rem; margin-bottom: 0.5rem; }
.pv { display: inline-block; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.76rem; background: rgba(255,255,255,0.04);
  border: 1px solid var(--border-light); border-radius: 6px;
  padding: 0.08rem 0.45rem; margin: 0 0.25rem 0.25rem 0; color: var(--text-secondary); }
.pv b { color: var(--sun-gold); font-weight: 600; }
.heroval { display: flex; gap: 0.8rem; flex-wrap: wrap; margin: 0.8rem 0 1.2rem; }
.hv { border: 1px solid var(--border-light); border-left: 3px solid var(--acc);
  border-radius: 10px; background: var(--bg-card); padding: 0.55rem 1rem 0.6rem; }
.hv .k { display: block; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.72rem; color: var(--text-muted); margin-bottom: 0.1rem; }
.hv .v { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 1.35rem; font-weight: 600; color: var(--sun-gold); }
.cols { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 1.8rem;
  align-items: start; }
.side { border: 1px solid var(--border-light); border-radius: 12px;
  background: var(--bg-card); padding: 0.95rem 1.1rem 1rem; margin-bottom: 1rem; }
.side h4 { margin: 0 0 0.6rem; font-size: 0.72rem; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600; }
.side .prow { display: flex; gap: 0.8rem; padding: 0.3rem 0;
  border-bottom: 1px solid var(--border-light); font-size: 0.88rem; }
.side .prow:last-child { border-bottom: none; }
.side .prow .pk { flex: 0 0 88px; color: var(--text-muted); font-size: 0.8rem;
  padding-top: 0.1rem; }
.side ul.linked li { font-size: 0.86rem; }
.side details summary { cursor: pointer; color: var(--text-muted);
  font-size: 0.83rem; margin: 0.3rem 0; }
.side .allof { display: block; margin-top: 0.5rem; font-size: 0.83rem; }
.stag { display: inline-block; padding: 0.14rem 0.7rem; border-radius: 999px;
  font-size: 0.78rem; background: var(--acc); color: #10131c; font-weight: 600;
  vertical-align: 0.18em; margin-left: 0.55rem; }
.mapwrap { position: relative; border: 1px solid var(--border-light);
  border-radius: 14px; background: var(--bg-secondary); padding: 0.6rem;
  margin: 1rem 0 0.4rem; }
.mapwrap svg { display: block; width: 100%; height: auto; }
.land { fill: rgba(255,255,255,0.10); stroke: rgba(255,255,255,0.16);
  stroke-width: 0.5; }
.dot { fill: var(--sun-gold); fill-opacity: 0.75; stroke: rgba(0,0,0,0.35);
  stroke-width: 0.6; }
a:hover .dot, .dot:hover { fill-opacity: 1; }
.dot.dim { fill-opacity: 0.15; }
.mapcap { color: var(--text-muted); font-size: 0.8rem; margin: 0.2rem 0 1rem; }
.placerows { columns: 3; column-gap: 2rem; margin: 0.6rem 0 1rem; }
.placerows .fitem { break-inside: avoid; }
header.site .nav { margin-left: auto; font-size: 0.85rem; white-space: nowrap; }
.fscroll { max-height: 252px; overflow-y: auto; }
.relfig { width: 100%; max-width: 780px; height: auto; margin: 0.4rem 0 0.8rem; }
.otiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 0.6rem; margin: 0.4rem 0 0.8rem; }
.otile { display: flex; justify-content: space-between; align-items: baseline;
  gap: 0.5rem; border: 1px solid var(--border-light);
  border-left: 3px solid var(--acc); border-radius: 9px;
  background: var(--bg-card); padding: 0.5rem 0.8rem;
  color: var(--text-primary); }
.otile:hover { background: var(--bg-card-hover); text-decoration: none;
  border-color: var(--border-medium); border-left-color: var(--acc); }
.otile b { font-weight: 600; font-size: 0.9rem; }
.otile span { color: var(--text-muted); font-size: 0.78rem; }
.orow { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.8rem; margin: 1.2rem 0 0.6rem; }
.obig { display: block; border: 1px solid var(--border-medium);
  border-radius: 12px; background: var(--bg-card); padding: 0.9rem 1.1rem;
  color: var(--text-primary); }
.obig:hover { background: var(--bg-card-hover); text-decoration: none;
  border-color: var(--sun-gold); }
.obig b { display: block; margin-bottom: 0.2rem; }
.obig span { color: var(--text-muted); font-size: 0.83rem; }
@media (max-width: 900px) {
  .layout, .cols { grid-template-columns: 1fr; }
  .placerows { columns: 1; }
  table.params td:first-child { width: auto; }
}
"""

COPY_JS = """
<script>
document.querySelectorAll('.copywrap').forEach(w => {
  const btn = document.createElement('button');
  btn.className = 'copy'; btn.textContent = 'Copy';
  btn.addEventListener('click', () => {
    navigator.clipboard.writeText(w.querySelector('pre').textContent).then(() => {
      btn.textContent = 'Copied'; btn.classList.add('ok');
      setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('ok'); }, 1500);
    });
  });
  w.appendChild(btn);
});
</script>
"""


def esc(s):
    return html.escape(str(s), quote=True)


def page(title, body, depth=0, script=""):
    rel = "../" * depth
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · SUEWS parameter database</title>
<style>{CSS}</style>
</head><body>
<header class="site"><div class="wrap">
  <h1><a href="{rel}index.html">SUEWS parameter database</a></h1>
  <span class="sub">curated values, linked and searchable, a citation on every one</span>
  <span class="nav"><a href="{rel}index.html">Home</a> ·
  <a href="{rel}map.html">Map</a></span>
</div></header>
<div class="wrap">
{body}
<footer>Data and site: <a href="{REPO_URL}">UMEP-dev/SUEWS-database</a> ·
parameter names follow the
<a href="{DOCS}/index.html">SUEWS YAML input specification</a> ·
per-parameter definitions in the
<a href="{DOCS_REF}/index.html">configuration reference</a> ·
cite via the <a href="{REPO_URL}/releases">archived releases</a>
(Zenodo DOI to follow with the database paper)</footer>
</div>
{script}
</body></html>"""


# ---------------- entry metadata ----------------


def family_of(path):
    """Derive the parameter family from an entry's path."""
    parts = path.split("/")
    if parts[0] == "records":
        if parts[1] == "surfaces":
            slug = parts[-1]
            # "--lai" also matches "--lai-power": LAI and its growth
            # coefficients are one coupled family (they are fitted together)
            for fam in ("albedo", "emissivity", "anohm", "water-storage",
                        "drainage", "water-state", "snow-lim", "soil",
                        "lai", "phenology", "max-conductance", "porosity",
                        "biogen-co2", "thermal-layers"):
                if f"--{fam}" in slug:
                    return fam
            return "surface"
        if parts[1] == "profiles":
            return f"profile: {parts[2]}"
        return parts[1]
    return parts[1]  # archetype group


def geo_of(rec, places):
    """(region, country name, city name) for a record's place."""
    slug = rec.get("place")
    if not slug:
        return None, None, None
    info = places.get(slug) or {}
    if info.get("kind") == "country":
        return info.get("region"), info.get("name", slug), None
    country = info.get("country")
    cinfo = (places.get(country) or {}) if country else {}
    region = info.get("region") or cinfo.get("region")
    country_name = cinfo.get("name", country) if country else None
    city = info.get("name", slug) if country and not info.get("subnational") else None
    return region, country_name, city


def surface_of(path, rec):
    parts = path.split("/")
    if len(parts) > 2 and parts[2] in SURFACES:
        return parts[2]
    target = str(rec.get("target") or "")
    if target.startswith("land_cover."):
        return target.split(".")[1]
    return None


def slugify_ish(text):
    return "".join(c for c in str(text).lower() if c.isalnum() or c == " ")


def is_hour_dict(node):
    return (isinstance(node, dict) and node
            and all(isinstance(k, int) and 1 <= k <= 24 for k in node))


def leaf_pairs(node, prefix=""):
    """Flatten parameters to (dotted path, value) pairs for inline preview.

    24-hour profile dictionaries collapse to one pair so a profile reads as
    one series rather than 24 rows.
    """
    out = []
    if is_hour_dict(node):
        out.append((prefix, " ".join(str(v) for v in node.values())))
    elif isinstance(node, dict):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.extend(leaf_pairs(v, p))
    elif isinstance(node, list):
        out.append((prefix, "[" + ", ".join(str(x) for x in node[:6]) + "]"))
    elif node is not None:
        out.append((prefix, node))
    return out


def params_table(params, target, muted=False, linked=True):
    """Render a parameter block as a table: linked name, value, docs link."""
    rows = []
    for dotted, value in leaf_pairs(params):
        url = doc_url(target, dotted) if linked else None
        name = (f"<a href=\"{esc(url)}\" title=\"definition in the SUEWS "
                f"configuration reference\"><code>{esc(dotted)}</code></a>"
                if url else f"<code>{esc(dotted)}</code>")
        klass = "hrs" if isinstance(value, str) and value.count(" ") > 10 else "val"
        rows.append(f"<tr><td>{name}</td>"
                    f"<td><span class=\"{klass}\">{esc(value)}</span></td></tr>")
    cls = "kv params muted" if muted else "kv params"
    return f"<table class=\"{cls}\">" + "".join(rows) + "</table>"


def build_graph(records):
    """Backlinks: used_by, and same (place, source) sibling clusters."""
    used_by = defaultdict(list)
    cluster = defaultdict(list)  # (place, source) -> [path]
    for path, rec in records.items():
        for ref in set(iter_uses(rec.get("uses", {}))):
            used_by[ref].append(path)
        for side in ("roof", "wall"):
            for layer in rec.get("parameters", {}).get(side, []):
                if isinstance(layer, dict) and isinstance(layer.get("material"), str):
                    used_by[layer["material"]].append(path)
        rr = rec.get("region_ref")
        if isinstance(rr, str) and rr in records:
            used_by[rr].append(path)
        if rec.get("place") and rec.get("source"):
            cluster[(rec["place"], rec["source"])].append(path)
    return used_by, cluster


# ---------------- shared fragments ----------------


def chip_link(href, label, n=None):
    count = f"<span class=\"n\">{n}</span>" if n is not None else ""
    return f"<a class=\"chip\" href=\"{href}\">{esc(label)}{count}</a>"


def entry_link(path, records, depth):
    rec = records[path]
    rel = "../" * depth
    name = rec.get("name") or path.rsplit("/", 1)[-1]
    place = f" <span class=\"chip\">{esc(rec['place'])}</span>" if rec.get("place") else ""
    fam = family_of(path)
    return (f"<li><a href=\"{rel}{esc(path)}.html\">{esc(name)}</a> "
            f"<span class=\"chip\">{esc(fam)}</span>{place}</li>")


def vals_preview(rec, limit=4):
    pairs = leaf_pairs(rec.get("parameters", {}))
    pairs = [(k, v) for k, v in pairs if not k.startswith("context")][:limit]
    if not pairs:
        return ""
    bits = " · ".join(f"<b>{esc(k)}</b> {esc(v)}" for k, v in pairs)
    return f"<div class=\"vals\">{bits}</div>"


# ---------------- per-entry pages ----------------


def record_page(path, rec, records, sources, used_by, cluster):
    depth = path.count("/")
    rel = "../" * depth
    kind = "record" if path.startswith("records/") else "typology"
    src_key = rec.get("source")
    src = sources.get(src_key) if src_key else None
    fam = family_of(path)
    surface = surface_of(path, rec)

    crumbs = (f"<div class=\"crumbs\"><a href=\"{rel}index.html\">browse</a> · "
              f"{esc(path)}.yml</div>")

    # a title that says what the record physically is; the legacy name
    # becomes a qualifier rather than the headline
    fam_label = FAMILY_LABEL.get(fam)
    if fam.startswith("profile:"):
        fam_label = fam.split(":", 1)[1].strip().replace("-", " ").capitalize() \
            + " profile"
    what = fam_label or fam
    if surface and fam not in ("conductance", "irrigation", "anthropogenic"):
        what = f"{SURFACE_LABEL.get(surface, surface)} · {what.lower() if fam_label else what}"
    name = rec.get("name")
    qualifier = (f" <span class=\"subtitle\">— {esc(name)}</span>"
                 if name and slugify_ish(name) not in what.lower() else "")
    title_text = what
    acc = SURFACE_ACC.get(surface, "acc-none")
    headline_pairs = [(k, v) for k, v in leaf_pairs(rec.get("parameters", {}))
                      if not k.startswith("context")
                      and not (isinstance(v, str) and v.count(" ") > 10)][:4]
    headline = ""
    if headline_pairs and kind == "record":
        blocks = "".join(
            f"<div class=\"hv {acc}\"><span class=\"k\">{esc(k)}</span>"
            f"<span class=\"v\">{esc(v)}</span></div>"
            for k, v in headline_pairs)
        if blocks:
            headline = f"<div class=\"heroval\">{blocks}</div>"

    # clickable facet chips under the title
    chips = []
    chips.append(chip_link(f"{rel}index.html#family={esc(fam)}", fam))
    if surface:
        chips.append(chip_link(f"{rel}index.html#surface={esc(surface)}", surface))
    if rec.get("place"):
        chips.append(chip_link(f"{rel}place/{esc(rec['place'])}.html", rec["place"]))
    if rec.get("representativeness"):
        chips.append(chip_link(
            f"{rel}index.html#rep={esc(rec['representativeness'])}",
            rec["representativeness"]))
    if src_key:
        label = src_key
        if src and src.get("year"):
            label = f"{src.get('author', src_key).split(',')[0]} {src['year']}"
        chips.append(chip_link(f"{rel}source/{esc(src_key)}.html", label))
    chip_row = f"<div class=\"pill-row\">{''.join(chips)}</div>"

    prov_rows = []

    def row(k, v):
        if v:
            prov_rows.append(f"<div class=\"prow\"><span class=\"pk\">{esc(k)}"
                             f"</span><span>{v}</span></div>")

    if src:
        doi = src.get("doi")
        doi_html = (f" · <a href=\"https://doi.org/{esc(doi)}\">doi:{esc(doi)}</a>"
                    if doi else "")
        row("Source", f"<a href=\"{rel}source/{esc(src_key)}.html\">"
            f"{esc(src.get('author', src_key))} ({esc(src.get('year', '?'))})</a>"
            f"{doi_html}")
    if rec.get("place"):
        row("Place", f"<a href=\"{rel}place/{esc(rec['place'])}.html\">"
            f"{esc(rec['place'])}</a>")
    row("Scope", esc(rec.get("representativeness")))
    target = rec.get("target")
    target_doc = (f"{DOCS_REF}/hourlyprofile.html" if str(target).startswith("profile.")
                  else f"{DOCS_REF}/{TARGET_DOC[target]}.html"
                  if target in TARGET_DOC else None)
    row("Target", f"<code>{esc(target)}</code>"
        + (f" · <a href=\"{target_doc}\">docs</a>" if target_doc else ""))
    if rec.get("attaches_to"):
        row("Attaches to", f"<code>{esc(rec['attaches_to'])}</code>")
    if rec.get("origin"):
        row("Origin", f"“{esc(rec['origin'])}” (verbatim legacy string)")
    if rec.get("season_label"):
        row("Season", esc(rec["season_label"]))
    schema_bits = esc(rec.get("schema_version"))
    if rec.get("legacy_id"):
        schema_bits += f" · legacy row {esc(rec['legacy_id'])}"
    row("Schema", schema_bits)
    rail = ["<div class=\"side\"><h4>Provenance</h4>"
            + "".join(prov_rows) + "</div>"]

    body = [crumbs,
            f"<h2>{esc(title_text)}{qualifier}"
            + (f"<span class=\"stag {acc}\">{esc(surface)}</span>" if surface else "")
            + f" <span class=\"chip\">{kind}</span></h2>",
            headline, chip_row]
    main = []

    uses = rec.get("uses")
    if uses:
        main.append("<h3>Uses</h3><table class=\"kv\">")

        def use_rows(u, prefix=""):
            for slot, ref in u.items():
                if isinstance(ref, dict):
                    use_rows(ref, prefix + slot + " · ")
                else:
                    if ref in records:
                        target_rec = records[ref]
                        nm = target_rec.get("name") or ref.rsplit("/", 1)[-1]
                        pl = target_rec.get("place")
                        extra = f" <span class=\"chip\">{esc(pl)}</span>" if pl else ""
                        link = f"<a href=\"{rel}{esc(ref)}.html\">{esc(nm)}</a>{extra} " \
                               f"{vals_preview(target_rec, 3)}"
                    else:
                        link = esc(ref)
                    main.append(f"<tr><th>{esc(prefix + slot)}</th><td>{link}</td></tr>")

        use_rows(uses)
        main.append("</table>")

    if rec.get("parameters"):
        params = {k: v for k, v in rec["parameters"].items() if k != "context"}
        context = rec["parameters"].get("context")
        if params:
            main.append("<h3>Parameters</h3>"
                        "<p class=\"crumbs\">Names follow the SUEWS YAML "
                        "specification; click one for its definition in the "
                        "configuration reference.</p>")
            main.append(params_table(params, rec.get("target")))
        if context:
            main.append("<h3>Context</h3>"
                        "<p class=\"crumbs\">Conditions the set was derived "
                        "under; not model inputs themselves.</p>")
            main.append(params_table({"context": context}, None, linked=False))

    if rec.get("legacy"):
        main.append("<h3>Legacy values (no home in the current model; "
                    "kept verbatim under their original column names)</h3>")
        main.append(params_table(rec["legacy"], None, muted=True, linked=False))

    try:
        frag = assemble(path, records, sources)
        if frag:
            main.append("<h3>Model-ready fragment</h3>"
                        "<p class=\"crumbs\">Paste under "
                        f"<code>{esc(rec.get('target'))}</code> in a SUEWS YAML "
                        "configuration; every value carries its citation.</p>")
            main.append("<div class=\"copywrap\"><pre>"
                        + esc(yaml.dump(frag, Dumper=PlainDumper,
                              sort_keys=False, allow_unicode=True, width=80))
                        + "</pre></div>")
    except Exception:
        pass

    def side_list(title, paths_list, cap, tail=""):
        items = [entry_link(p, records, depth) for p in paths_list]
        head = "".join(items[:cap])
        more = ""
        if len(items) > cap:
            more = (f"<details><summary>+{len(items) - cap} more</summary>"
                    f"<ul class=\"linked\">{''.join(items[cap:])}</ul></details>")
        return (f"<div class=\"side\"><h4>{title}</h4>"
                f"<ul class=\"linked\">{head}</ul>{more}{tail}</div>")

    # connections: used by + same-study siblings
    backlinks = sorted(set(used_by.get(path, [])))
    if backlinks:
        n = len(backlinks)
        if kind == "record":
            label = "typology" if n == 1 else "typologies"
        else:
            label = "entry" if n == 1 else "entries"
        rail.append(side_list(f"Used by {n} {label}", backlinks, 12))

    if rec.get("place") and rec.get("source"):
        sibs = sorted(p for p in cluster.get((rec["place"], rec["source"]), [])
                      if p != path)
        if sibs:
            all_link = (f"<a class=\"allof\" href=\"{rel}index.html#place="
                        f"{esc(rec['place'])}&amp;source={esc(src_key)}\">all "
                        f"{len(sibs) + 1} sets from {esc(src_key)} at "
                        f"{esc(rec['place'])} →</a>")
            rail.append(side_list("Same study, same place", sibs, 8, all_link))

    # duplicate-as-new: the GitHub new-file editor prefilled with this
    # record's YAML, so a contributor edits a copy rather than starting blank
    dup_dir = f"db/{path.rsplit('/', 1)[0]}"
    dup_url = f"{REPO_URL}/new/main?filename={quote(dup_dir + '/NEW-RECORD.yml', safe='')}"
    try:
        raw = (ROOT / "db" / (path + ".yml")).read_text()
        prefilled = dup_url + "&value=" + quote(raw, safe="")
        if len(prefilled) <= 7500:
            dup_url = prefilled
    except OSError:
        pass
    rail.append(
        "<div class=\"actions\">"
        f"<a href=\"{REPO_URL}/blob/main/db/{esc(path)}.yml\">View source</a>"
        f"<a href=\"{REPO_URL}/edit/main/db/{esc(path)}.yml\">Propose a change</a>"
        f"<a href=\"{esc(dup_url)}\">Duplicate as a new record</a>"
        "</div>"
    )
    body.append(f"<div class=\"cols\"><div>{''.join(main)}</div>"
                f"<div>{''.join(rail)}</div></div>")
    return page(title_text, "\n".join(body), depth, COPY_JS)


def grouped_list(paths, records, depth):
    groups = defaultdict(list)
    for p in paths:
        groups[family_of(p)].append(p)
    out = []
    for fam in sorted(groups):
        out.append(f"<h3>{esc(fam)} <span class=\"chip\">{len(groups[fam])}</span></h3>"
                   "<ul class=\"linked\">")
        out.extend(entry_link(p, records, depth) for p in sorted(groups[fam]))
        out.append("</ul>")
    return "".join(out)


def place_page(slug, info, paths, records):
    body = [f"<div class=\"crumbs\"><a href=\"../index.html\">browse</a> · place</div>",
            f"<h2>{esc(info.get('name', slug))}</h2>",
            f"<p class=\"crumbs\">{len(paths)} entries at this place · "
            f"<a href=\"../index.html#place={esc(slug)}\">filter the browser "
            "to this place</a></p>",
            grouped_list(paths, records, 1)]
    return page(info.get("name", slug), "\n".join(body), 1)


def source_page(key, src, paths, records):
    title = src.get("title") or src.get("note") or key
    doi = src.get("doi")
    cite = (f"{esc(src.get('author', ''))} ({esc(src.get('year', '?'))}). "
            f"{esc(title)}. <i>{esc(src.get('journal') or '')}</i>")
    if doi:
        cite += f" · <a href=\"https://doi.org/{esc(doi)}\">doi:{esc(doi)}</a>"
    body = [f"<div class=\"crumbs\"><a href=\"../index.html\">browse</a> · source</div>",
            f"<h2><code>{esc(key)}</code></h2>",
            f"<p>{cite}</p>",
            f"<p class=\"crumbs\">{len(paths)} entries cite this source · "
            f"<a href=\"../index.html#source={esc(key)}\">filter the browser "
            "to this source</a></p>",
            grouped_list(paths, records, 1)]
    return page(key, "\n".join(body), 1)


# ---------------- the map ----------------

MAP_W, MAP_LAT_TOP, MAP_LAT_BOT = 1000.0, 85.0, -60.0
MAP_H = round(MAP_W * (MAP_LAT_TOP - MAP_LAT_BOT) / 360.0, 1)

MAP_JS = """
<script>
let zone = null;
document.addEventListener('click', ev => {
  const b = ev.target.closest('button[data-zone]');
  if (!b) return;
  zone = zone === b.dataset.zone ? null : b.dataset.zone;
  document.querySelectorAll('button[data-zone]').forEach(c =>
    c.classList.toggle('on', c.dataset.zone === zone));
  document.querySelectorAll('.dot').forEach(d =>
    d.classList.toggle('dim', !!zone && d.dataset.koppen !== zone));
  document.querySelectorAll('.placerows .fitem').forEach(r => {
    r.style.display = (!zone || r.dataset.koppen === zone) ? '' : 'none';
  });
});
</script>
"""

KOPPEN_LABEL = {
    "Af": "tropical rainforest", "Am": "tropical monsoon", "Aw": "savanna",
    "BSk": "cold semi-arid", "BSh": "hot semi-arid", "BWh": "hot desert",
    "Cfa": "humid subtropical", "Cfb": "temperate oceanic",
    "Csa": "hot-summer mediterranean", "Csb": "warm-summer mediterranean",
    "Dfa": "hot-summer continental", "Dfb": "cold, warm summer",
    "Dwa": "cold, dry winter", "Dfc": "subarctic",
}


def map_xy(lon, lat):
    return ((lon + 180.0) / 360.0 * MAP_W,
            (MAP_LAT_TOP - lat) / (MAP_LAT_TOP - MAP_LAT_BOT) * MAP_H)


def build_map_page(places, by_place):
    """map.html: pick a study place geographically, or by Köppen zone."""
    land_d = (ROOT / "scripts" / "data" / "ne110m_land.svgpath").read_text().strip()

    import math
    mapped = []
    for slug, paths in by_place.items():
        info = places.get(slug) or {}
        if "lat" not in info or "lon" not in info:
            continue
        mapped.append((len(paths), slug, info))
    mapped.sort(reverse=True)

    dots, rows = [], []
    zone_counts = defaultdict(int)
    for n, slug, info in mapped:
        x, y = map_xy(info["lon"], info["lat"])
        r = round(2.2 + 2.1 * math.sqrt(n), 1)
        kz = info.get("koppen", "")
        if kz:
            zone_counts[kz] += n
        name = info.get("name", slug)
        dots.append(
            f"<a href=\"place/{esc(slug)}.html\"><circle class=\"dot\" "
            f"cx=\"{x:.1f}\" cy=\"{y:.1f}\" r=\"{r}\" data-koppen=\"{esc(kz)}\">"
            f"<title>{esc(name)} · {n} entries</title></circle></a>")
        rows.append(
            f"<a class=\"fitem\" href=\"place/{esc(slug)}.html\" "
            f"data-koppen=\"{esc(kz)}\"><span class=\"fv\">{esc(name)}"
            + (f" <span class=\"n\">{esc(kz)}</span>" if kz else "")
            + f"</span><span class=\"n\">{n}</span></a>")
    # big dots first so small neighbours stay hoverable on top
    n_unmapped = len(by_place) - len(mapped)

    zone_chips = "".join(
        f"<button class=\"chip\" data-zone=\"{esc(z)}\">{esc(z)} · "
        f"{esc(KOPPEN_LABEL.get(z, ''))}<span class=\"n\">{n}</span></button>"
        for z, n in sorted(zone_counts.items(), key=lambda kv: -kv[1]))

    body = f"""<div class="hero"><h2>Browse by place</h2>
<p>Every record is anchored to the place its values were measured or fitted
for. Pick a study city on the map, or filter by climate zone — dot size is
the number of entries.</p></div>
<div class="mapwrap">
<svg viewBox="0 0 1000 {MAP_H}" role="img" aria-label="World map of study places">
<path class="land" d="{land_d}"></path>
{''.join(dots)}
</svg>
</div>
<p class="mapcap">Coordinates come from the place registry
(<code>db/places.yml</code>); a climate-zone chip highlights its places.</p>
<h3>By climate zone (Köppen)</h3>
<div class="pill-row">{zone_chips}</div>
<h3>Mapped places</h3>
<div class="placerows">{''.join(rows)}</div>
<p class="crumbs">{n_unmapped} more places carry entries but no coordinates
yet — mostly the per-country default sets. Find them through the Place facet
in the <a href="index.html">browser</a>, or add coordinates to
<code>db/places.yml</code>.</p>"""
    return page("Map", body, 0, MAP_JS)


# ---------------- the faceted browser ----------------

BROWSER_JS = """
<script>
const FACETS = ['kind', 'surface', 'family', 'typology', 'region', 'country',
                'city', 'rep', 'source'];
let DATA = [];
const state = { q: '', all: false };
for (const f of FACETS) state[f] = null;

function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  state.q = h.get('q') || '';
  state.all = h.get('all') === '1';
  for (const f of FACETS) state[f] = h.get(f);
  // legacy links: #place=<slug> maps onto the text search
  const legacy = h.get('place');
  if (legacy && !state.q) state.q = legacy;
}
function writeHash() {
  const h = new URLSearchParams();
  if (state.q) h.set('q', state.q);
  if (state.all) h.set('all', '1');
  for (const f of FACETS) if (state[f]) h.set(f, state[f]);
  history.replaceState(null, '', h.toString() ? '#' + h.toString() : location.pathname);
}
function matches(e) {
  for (const f of FACETS) if (state[f] && e[f] !== state[f]) return false;
  if (state.q) {
    const q = state.q.toLowerCase();
    if (!e.text.includes(q)) return false;
  }
  return true;
}
const LC_ORDER = ['paved', 'bldgs', 'evetr', 'dectr', 'grass', 'bsoil', 'water'];
const ACC = { grass: 'acc-veg', dectr: 'acc-veg', evetr: 'acc-veg',
              water: 'acc-water', paved: 'acc-built', bldgs: 'acc-built',
              bsoil: 'acc-soil', snow: 'acc-snow', common: 'acc-none' };
function itemHTML(facet, value, count, on, swatch) {
  const sw = swatch ? `<span class="sw ${ACC[value] || 'acc-none'}"></span>` : '';
  return `<button class="fitem${on ? ' on' : ''}" data-facet="${facet}" ` +
         `data-value="${value}">${sw}<span class="fv">${value}</span>` +
         `<span class="n">${count}</span></button>`;
}
function emptyState() {
  return !state.q && !state.all && FACETS.every(f => !state[f]);
}
function render() {
  const hits = DATA.filter(matches);
  // facet counts over current hits (each facet counted with itself removed,
  // so options within a facet stay visible)
  for (const f of FACETS) {
    const saved = state[f]; state[f] = null;
    const sub = DATA.filter(matches);
    state[f] = saved;
    const counts = {};
    for (const e of sub) if (e[f]) counts[e[f]] = (counts[e[f]] || 0) + 1;
    const el = document.getElementById('facet-' + f);
    if (f === 'surface') {
      // the 7 SUEWS land covers in canonical order, a divider, then
      // cross-surface entries below it
      let out = LC_ORDER.filter(v => counts[v])
        .map(v => itemHTML(f, v, counts[v], state[f] === v, true)).join('');
      const rest = Object.keys(counts)
        .filter(v => !LC_ORDER.includes(v)).sort();
      if (rest.length) {
        out += '<div class="fdiv"></div>' + rest
          .map(v => itemHTML(f, v, counts[v], state[f] === v, true)).join('');
      }
      el.innerHTML = out;
      continue;
    }
    // alphabetical, all values shown (long groups scroll)
    const entries = Object.entries(counts)
      .sort((a, b) => a[0].localeCompare(b[0]));
    el.innerHTML = entries.map(([v, n]) => itemHTML(f, v, n, state[f] === v)).join('');
  }
  const overview = document.getElementById('overview');
  const showOverview = emptyState();
  if (overview) overview.classList.toggle('hidden', !showOverview);
  document.getElementById('nres').textContent = showOverview ? '' :
    hits.length + ' of ' + DATA.length + ' entries';
  const out = showOverview ? '' : hits.slice(0, 200).map(e => {
    const acc = ACC[e.surface] || 'acc-none';
    const meta = [e.family, e.typology, e.city || e.country, e.rep, e.source]
      .filter(Boolean).join(' · ');
    const kindTag = e.kind === 'typology' ? ' <span class="chip">typology</span>' : '';
    return `<div class="card2 ${acc}"><div class="t"><a href="${e.path}.html">` +
           `${e.name}</a>${kindTag}</div><div class="meta2">${meta}</div>` +
           `<div>${e.vals}</div></div>`;
  }).join('');
  document.getElementById('results').innerHTML = out +
    (!showOverview && hits.length > 200 ? '<div class="crumbs">…narrow the filters to see the rest</div>' : '');
}
document.addEventListener('click', ev => {
  const b = ev.target.closest('button.fitem');
  if (!b) return;
  const f = b.dataset.facet, v = b.dataset.value;
  state[f] = state[f] === v ? null : v;
  writeHash(); render();
});
const input = document.getElementById('q');
input.addEventListener('input', () => { state.q = input.value; writeHash(); render(); });
window.addEventListener('hashchange', () => { readHash(); input.value = state.q; render(); });
fetch('data/index.json').then(r => r.json()).then(d => {
  DATA = d; readHash(); input.value = state.q; render();
});
</script>
"""


RELATION_SVG = """<svg viewBox="0 0 780 208" role="img" class="relfig"
 aria-label="How the database fits together">
<defs><marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7"
 markerHeight="7" orient="auto"><path d="M0 0L8 4L0 8z"
 fill="rgba(255,255,255,0.45)"/></marker></defs>
<g font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<rect x="10" y="14" width="130" height="44" rx="9"
 fill="rgba(255,255,255,0.04)" stroke="rgba(255,255,255,0.2)"/>
<text x="75" y="33" text-anchor="middle" fill="rgba(255,255,255,0.85)"
 font-size="13">sources</text>
<text x="75" y="49" text-anchor="middle" fill="rgba(255,255,255,0.5)"
 font-size="11">citation per value</text>
<rect x="10" y="98" width="130" height="44" rx="9"
 fill="rgba(255,255,255,0.04)" stroke="rgba(255,255,255,0.2)"/>
<text x="75" y="117" text-anchor="middle" fill="rgba(255,255,255,0.85)"
 font-size="13">places</text>
<text x="75" y="133" text-anchor="middle" fill="rgba(255,255,255,0.5)"
 font-size="11">region · country · city</text>
<rect x="210" y="50" width="170" height="54" rx="10"
 fill="rgba(247,181,56,0.12)" stroke="#F7B538"/>
<text x="295" y="73" text-anchor="middle" fill="#F7B538" font-size="14"
 font-weight="600">evidence records</text>
<text x="295" y="91" text-anchor="middle" fill="rgba(255,255,255,0.6)"
 font-size="11">one coherent set per source</text>
<rect x="440" y="50" width="172" height="54" rx="10"
 fill="rgba(93,173,226,0.12)" stroke="#5DADE2"/>
<text x="526" y="73" text-anchor="middle" fill="#5DADE2" font-size="14"
 font-weight="600">typologies</text>
<text x="526" y="91" text-anchor="middle" fill="rgba(255,255,255,0.6)"
 font-size="11">curated bundles of records</text>
<rect x="660" y="50" width="112" height="54" rx="10"
 fill="rgba(9,162,92,0.12)" stroke="#09a25c"/>
<text x="716" y="73" text-anchor="middle" fill="#09a25c" font-size="13"
 font-weight="600">your SUEWS</text>
<text x="716" y="90" text-anchor="middle" fill="#09a25c" font-size="13"
 font-weight="600">YAML config</text>
<line x1="140" y1="42" x2="205" y2="66" stroke="rgba(255,255,255,0.35)"
 marker-end="url(#arr)"/>
<line x1="140" y1="116" x2="205" y2="90" stroke="rgba(255,255,255,0.35)"
 marker-end="url(#arr)"/>
<line x1="380" y1="77" x2="434" y2="77" stroke="rgba(255,255,255,0.35)"
 marker-end="url(#arr)"/>
<line x1="612" y1="77" x2="654" y2="77" stroke="rgba(255,255,255,0.35)"
 marker-end="url(#arr)"/>
<path d="M295 104 L295 158 L716 158 L716 110" fill="none"
 stroke="rgba(255,255,255,0.3)" stroke-dasharray="4 4"
 marker-end="url(#arr)"/>
<rect x="380" y="148" width="252" height="20" rx="5" fill="#0F1119"/>
<text x="506" y="162" text-anchor="middle" fill="rgba(255,255,255,0.5)"
 font-size="11">or paste a single record's fragment</text>
</g></svg>"""


def build_index_page(records, sources, places, by_place):
    n_rec = sum(1 for p in records if p.startswith("records/"))
    n_arch = sum(1 for p in records if p.startswith("archetypes/"))
    stats = (
        "<div class=\"statline\">"
        f"<span><b>{n_rec}</b>evidence records</span>"
        f"<span><b>{n_arch}</b>typologies</span>"
        f"<span><b>{len(sources)}</b>sources</span>"
        f"<span><b>{len(places)}</b>places</span>"
        "</div>"
    )
    hero = (
        "<div class=\"hero\"><h2>Find a parameter value you can cite</h2>"
        "<p>Curated values for "
        "<a href=\"https://github.com/UMEP-dev/SUEWS\">SUEWS</a>: one "
        "source-coherent set per record, named by the model's own parameter "
        "paths. Every record exports as a fragment that pastes straight into "
        "a SUEWS YAML configuration — with the citation attached to every "
        "value.</p></div>"
    )

    # ---- overview panel (arrival view: structure first, data on demand) ----
    lc_counts = defaultdict(int)
    typ_counts = defaultdict(int)
    fam_records = set()
    n_mapped = 0
    for path, rec in records.items():
        s = surface_of(path, rec)
        if s:
            lc_counts[s] += 1
        if path.startswith("archetypes/"):
            typ_counts[family_of(path)] += 1
        else:
            fam_records.add(family_of(path))
    for slug in by_place:
        info = places.get(slug) or {}
        if "lat" in info and "lon" in info:
            n_mapped += 1

    lc_tiles = "".join(
        f"<a class=\"otile {SURFACE_ACC.get(s, 'acc-none')}\" "
        f"href=\"#surface={s}\"><b>{SURFACE_LABEL.get(s, s)}</b>"
        f"<span>{lc_counts[s]}</span></a>"
        for s in LC_ORDER + ["common"] if lc_counts.get(s))
    typ_tiles = "".join(
        f"<a class=\"otile acc-none\" href=\"#typology={t}\"><b>{esc(t)}</b>"
        f"<span>{n}</span></a>"
        for t, n in sorted(typ_counts.items()))
    overview = f"""<div id="overview">
<h3 style="margin-top:0.4rem">How it fits together</h3>
{RELATION_SVG}
<h3>Land cover</h3>
<div class="otiles">{lc_tiles}</div>
<h3>Typologies</h3>
<div class="otiles">{typ_tiles}</div>
<div class="orow">
<a class="obig" href="map.html"><b>Browse by place</b>
<span>{n_mapped} study places on the map, or by climate zone</span></a>
<a class="obig" href="#all=1"><b>Browse all {len(records)} entries</b>
<span>{len(fam_records)} parameter families, every value cited</span></a>
<a class="obig" href="{REPO_URL}/blob/main/docs/FORMAT.md"><b>Contribute</b>
<span>correct a record from its page, or add a new one — one small YAML
file in a pull request</span></a>
</div>
</div>"""

    def fgroup(fid, label, scroll=False, tail=""):
        cls = " class=\"fscroll\"" if scroll else ""
        return (f"<div class=\"fgroup\"><h4>{label}</h4>"
                f"<div id=\"facet-{fid}\"{cls}></div>{tail}</div>")

    rail = ("<div class=\"rail\">"
            + fgroup("kind", "Kind")
            + fgroup("surface", "Land cover")
            + fgroup("family", "Family", scroll=True)
            + fgroup("typology", "Typology")
            + fgroup("region", "Region")
            + fgroup("country", "Country", scroll=True)
            + fgroup("city", "City", scroll=True,
                     tail="<a class=\"maplink\" href=\"map.html\">pick on a map →</a>")
            + fgroup("rep", "Representativeness")
            + fgroup("source", "Source", scroll=True)
            + "</div>")
    body = (hero
            + "<input id=\"q\" class=\"search\" type=\"search\" "
              "placeholder=\"Search: parameter name, place, source, value...\">"
            + stats
            + f"<div class=\"layout\">{rail}<div>"
            + overview
            + "<div id=\"nres\"></div><div id=\"results\" class=\"results2\"></div>"
            + "</div></div>")
    return page("Browse", body, 0, BROWSER_JS)


def build_search_index(records, sources, places):
    entries = []
    for path, rec in sorted(records.items()):
        kind = "record" if path.startswith("records/") else "typology"
        fam = family_of(path)
        surface = surface_of(path, rec)
        region, country, city = geo_of(rec, places)
        pairs = [(k, v) for k, v in leaf_pairs(rec.get("parameters", {}))
                 if not k.startswith("context")]

        def pv(k, v):
            s = str(v)
            if len(s) > 26:
                s = s[:24] + "…"
            return (f"<span class=\"pv\">{html.escape(str(k))} "
                    f"<b>{html.escape(s)}</b></span>")

        vals = "".join(pv(k, v) for k, v in pairs[:5])
        src = sources.get(rec.get("source"), {})
        text = " ".join(str(x).lower() for x in [
            path, rec.get("name"), rec.get("place"), rec.get("origin"),
            region, country, city,
            rec.get("source"), src.get("author"), src.get("title"),
            rec.get("target"), fam, surface or "",
            " ".join(k for k, _ in pairs),
        ] if x)
        entries.append({
            "path": path, "name": str(rec.get("name") or path.rsplit("/", 1)[-1]),
            "kind": kind,
            "surface": surface,
            "family": fam if kind == "record" else None,
            "typology": fam if kind == "typology" else None,
            "place": rec.get("place"),
            "region": region, "country": country, "city": city,
            "rep": rec.get("representativeness"),
            "source": rec.get("source"), "vals": vals, "text": text,
        })
    # evidence records with values lead; bare pointer typologies trail, so a
    # filtered screen shows data first; alphabetical within each group so
    # entries are findable by name
    entries.sort(key=lambda e: (e["kind"] != "record", e["vals"] == "",
                                e["name"].lower(), e["path"]))
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    args = ap.parse_args()
    out = ROOT / args.out

    records, sources, places = load_all()
    used_by, cluster = build_graph(records)

    for path, rec in records.items():
        fp = out / (path + ".html")
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(record_page(path, rec, records, sources, used_by, cluster))

    by_place = defaultdict(list)
    by_source = defaultdict(list)
    for path, rec in records.items():
        if rec.get("place"):
            by_place[rec["place"]].append(path)
        if rec.get("source"):
            by_source[rec["source"]].append(path)

    (out / "place").mkdir(parents=True, exist_ok=True)
    for slug, paths in by_place.items():
        info = places.get(slug, {"name": slug})
        (out / "place" / f"{slug}.html").write_text(
            place_page(slug, info, paths, records))

    (out / "source").mkdir(parents=True, exist_ok=True)
    for key, paths in by_source.items():
        src = sources.get(key, {})
        (out / "source" / f"{key}.html").write_text(
            source_page(key, src, paths, records))

    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "data" / "index.json").write_text(
        json.dumps(build_search_index(records, sources, places),
                   ensure_ascii=False))
    (out / "index.html").write_text(
        build_index_page(records, sources, places, by_place))
    (out / "map.html").write_text(build_map_page(places, by_place))
    (out / ".nojekyll").write_text("")
    print(f"site: {len(records)} entry pages, {len(by_place)} place pages, "
          f"{len(by_source)} source pages -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

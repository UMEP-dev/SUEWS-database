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
</div></header>
<div class="wrap">
{body}
<footer>Data and site: <a href="{REPO_URL}">UMEP-dev/SUEWS-database</a> ·
parameter names follow the
<a href="{DOCS}/index.html">SUEWS YAML input specification</a> ·
per-parameter definitions in the
<a href="{DOCS_REF}/index.html">configuration reference</a></footer>
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
            for fam in ("albedo", "emissivity", "anohm", "water-storage",
                        "drainage", "water-state", "snow-lim", "soil", "lai-power",
                        "lai", "phenology", "max-conductance", "porosity",
                        "biogen-co2", "thermal-layers"):
                if f"--{fam}" in slug:
                    return fam
            return "surface"
        if parts[1] == "profiles":
            return f"profile: {parts[2]}"
        return parts[1]
    return parts[1]  # archetype group


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
    kind = "record" if path.startswith("records/") else "archetype"
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
    headline_pairs = [(k, v) for k, v in leaf_pairs(rec.get("parameters", {}))
                      if not k.startswith("context")][:3]
    headline = ""
    if headline_pairs and kind == "record":
        bits = " · ".join(
            f"<b>{esc(k)}</b> <span class=\"v\">{esc(v)}</span>"
            for k, v in headline_pairs
            if not (isinstance(v, str) and v.count(" ") > 10))
        if bits:
            headline = f"<div class=\"headline\">{bits}</div>"

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

    meta_rows = []

    def row(k, v):
        if v:
            meta_rows.append(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>")

    target = rec.get("target")
    target_doc = (f"{DOCS_REF}/hourlyprofile.html" if str(target).startswith("profile.")
                  else f"{DOCS_REF}/{TARGET_DOC[target]}.html"
                  if target in TARGET_DOC else None)
    row("Target", f"<code>{esc(target)}</code>"
        + (f" · <a href=\"{target_doc}\">documentation</a>" if target_doc else ""))
    if rec.get("attaches_to"):
        row("Attaches to", f"<code>{esc(rec['attaches_to'])}</code>")
    if rec.get("origin"):
        row("Origin (verbatim legacy string)", esc(rec["origin"]))
    if src:
        doi = src.get("doi")
        doi_html = (f" · <a href=\"https://doi.org/{esc(doi)}\">doi:{esc(doi)}</a>"
                    if doi else "")
        row("Source", f"<a href=\"{rel}source/{esc(src_key)}.html\">"
            f"{esc(src.get('author', src_key))} ({esc(src.get('year', '?'))})</a>"
            f"{doi_html}")
    if rec.get("season_label"):
        row("Season label", esc(rec["season_label"]))
    row("Schema version", esc(rec.get("schema_version")))
    row("Legacy row ID", esc(rec.get("legacy_id")))
    meta = "<table class=\"kv\">" + "".join(meta_rows) + "</table>"

    body = [crumbs,
            f"<h2>{esc(title_text)}{qualifier} "
            f"<span class=\"chip\">{kind}</span></h2>",
            headline, chip_row, meta]

    uses = rec.get("uses")
    if uses:
        body.append("<h3>Uses</h3><table class=\"kv\">")

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
                    body.append(f"<tr><th>{esc(prefix + slot)}</th><td>{link}</td></tr>")

        use_rows(uses)
        body.append("</table>")

    if rec.get("parameters"):
        params = {k: v for k, v in rec["parameters"].items() if k != "context"}
        context = rec["parameters"].get("context")
        if params:
            body.append("<h3>Parameters</h3>"
                        "<p class=\"crumbs\">Names follow the SUEWS YAML "
                        "specification; click one for its definition in the "
                        "configuration reference.</p>")
            body.append(params_table(params, rec.get("target")))
        if context:
            body.append("<h3>Context</h3>"
                        "<p class=\"crumbs\">Conditions the set was derived "
                        "under; not model inputs themselves.</p>")
            body.append(params_table({"context": context}, None, linked=False))

    if rec.get("legacy"):
        body.append("<h3>Legacy values (no home in the current model; "
                    "kept verbatim under their original column names)</h3>")
        body.append(params_table(rec["legacy"], None, muted=True, linked=False))

    try:
        frag = assemble(path, records, sources)
        if frag:
            body.append("<h3>Model-ready fragment</h3>"
                        "<p class=\"crumbs\">Paste under "
                        f"<code>{esc(rec.get('target'))}</code> in a SUEWS YAML "
                        "configuration; every value carries its citation.</p>")
            body.append("<div class=\"copywrap\"><pre>"
                        + esc(yaml.dump(frag, Dumper=PlainDumper,
                              sort_keys=False, allow_unicode=True, width=80))
                        + "</pre></div>")
    except Exception:
        pass

    # connections: used by + same-study siblings
    backlinks = sorted(set(used_by.get(path, [])))
    if backlinks:
        body.append(f"<h3>Used by {len(backlinks)} archetype"
                    f"{'s' if len(backlinks) != 1 else ''}</h3><ul class=\"linked\">")
        body.extend(entry_link(p, records, depth) for p in backlinks)
        body.append("</ul>")

    if rec.get("place") and rec.get("source"):
        sibs = [p for p in cluster.get((rec["place"], rec["source"]), [])
                if p != path]
        if sibs:
            body.append(f"<h3>Same study, same place ({len(sibs)})</h3>"
                        "<p class=\"crumbs\">Other parameter sets from "
                        f"{esc(src_key)} at {esc(rec['place'])} — values that "
                        "travelled together.</p><ul class=\"linked\">")
            body.extend(entry_link(p, records, depth) for p in sorted(sibs))
            body.append("</ul>")

    body.append(
        "<div class=\"actions\">"
        f"<a href=\"{REPO_URL}/blob/main/db/{esc(path)}.yml\">View source on GitHub</a>"
        f"<a href=\"{REPO_URL}/edit/main/db/{esc(path)}.yml\">Propose a change (fork &amp; PR)</a>"
        "</div>"
    )
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


# ---------------- the faceted browser ----------------

BROWSER_JS = """
<script>
const FACETS = ['kind', 'surface', 'family', 'place', 'rep', 'source'];
let DATA = [];
const state = { q: '', kind: null, surface: null, family: null, place: null,
                rep: null, source: null };

function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  state.q = h.get('q') || '';
  for (const f of FACETS) state[f] = h.get(f);
}
function writeHash() {
  const h = new URLSearchParams();
  if (state.q) h.set('q', state.q);
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
function chipHTML(facet, value, count, on) {
  return `<button class="chip${on ? ' on' : ''}" data-facet="${facet}" ` +
         `data-value="${value}">${value}<span class="n">${count}</span></button>`;
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
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const shown = f === 'place' || f === 'source' ? entries.slice(0, 24) : entries;
    el.innerHTML = shown.map(([v, n]) => chipHTML(f, v, n, state[f] === v)).join('');
    if (entries.length > shown.length && !state[f]) {
      el.innerHTML += `<span class="chip" style="cursor:default;opacity:0.5">` +
                      `+${entries.length - shown.length} more (type to narrow)</span>`;
    }
  }
  document.getElementById('nres').textContent =
    hits.length + ' of ' + DATA.length + ' entries';
  const out = hits.slice(0, 200).map(e => {
    const chips = [e.kind, e.surface, e.family, e.place, e.rep]
      .filter(Boolean)
      .map(v => `<span class="chip">${v}</span>`).join('');
    return `<div class="rcard"><div class="t"><a href="${e.path}.html">` +
           `${e.name}</a></div><div class="meta">${chips}</div>` +
           `<div class="vals">${e.vals}</div></div>`;
  }).join('');
  document.getElementById('results').innerHTML = out +
    (hits.length > 200 ? '<div class="crumbs">…narrow the filters to see the rest</div>' : '');
}
document.addEventListener('click', ev => {
  const b = ev.target.closest('button.chip');
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


def build_index_page(records, sources, places):
    n_rec = sum(1 for p in records if p.startswith("records/"))
    n_arch = sum(1 for p in records if p.startswith("archetypes/"))
    stats = (
        "<div class=\"stats\">"
        f"<div class=\"stat\"><b>{n_rec}</b><span>evidence records</span></div>"
        f"<div class=\"stat\"><b>{n_arch}</b><span>archetypes</span></div>"
        f"<div class=\"stat\"><b>{len(sources)}</b><span>cited sources</span></div>"
        f"<div class=\"stat\"><b>{len(places)}</b><span>places</span></div>"
        "</div>"
    )
    intro = (
        "<p>Curated parameter values for "
        "<a href=\"https://github.com/UMEP-dev/SUEWS\">SUEWS</a>: one "
        "source-coherent set per record, named by the model's own parameter "
        "paths, a citation on every value. Every record exports as a fragment "
        "that pastes straight into a SUEWS YAML configuration. Contribute via "
        f"the <a href=\"{REPO_URL}/blob/main/docs/FORMAT.md\">format guide</a> "
        "— one small YAML file in a pull request.</p>"
    )
    facets = "".join(
        f"<div class=\"facet-group\"><span class=\"fl\">{label}</span>"
        f"<span class=\"facet-body\" id=\"facet-{fid}\"></span></div>"
        for fid, label in (("kind", "Kind"), ("surface", "Surface"),
                           ("family", "Family"), ("place", "Place"),
                           ("rep", "Scope"), ("source", "Source"))
    )
    body = (intro + stats
            + "<input id=\"q\" class=\"search\" type=\"search\" "
              "placeholder=\"Search: parameter name, place, source, value...\">"
            + facets
            + "<div id=\"nres\"></div><div id=\"results\" class=\"results\"></div>")
    return page("Browse", body, 0, BROWSER_JS)


def build_search_index(records, sources):
    entries = []
    for path, rec in sorted(records.items()):
        kind = "record" if path.startswith("records/") else "archetype"
        fam = family_of(path)
        surface = surface_of(path, rec)
        pairs = [(k, v) for k, v in leaf_pairs(rec.get("parameters", {}))
                 if not k.startswith("context")]
        vals = " · ".join(f"<b>{html.escape(str(k))}</b> {html.escape(str(v))}"
                          for k, v in pairs[:5])
        src = sources.get(rec.get("source"), {})
        text = " ".join(str(x).lower() for x in [
            path, rec.get("name"), rec.get("place"), rec.get("origin"),
            rec.get("source"), src.get("author"), src.get("title"),
            rec.get("target"), fam, surface or "",
            " ".join(k for k, _ in pairs),
        ] if x)
        entries.append({
            "path": path, "name": str(rec.get("name") or path.rsplit("/", 1)[-1]),
            "kind": kind, "surface": surface, "family": fam,
            "place": rec.get("place"), "rep": rec.get("representativeness"),
            "source": rec.get("source"), "vals": vals, "text": text,
        })
    # evidence records with values lead; bare pointer archetypes trail, so
    # the unfiltered first screen shows data rather than empty country cards
    entries.sort(key=lambda e: (e["kind"] != "record", e["vals"] == "", e["path"]))
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
        json.dumps(build_search_index(records, sources), ensure_ascii=False))
    (out / "index.html").write_text(build_index_page(records, sources, places))
    (out / ".nojekyll").write_text("")
    print(f"site: {len(records)} entry pages, {len(by_place)} place pages, "
          f"{len(by_source)} source pages -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

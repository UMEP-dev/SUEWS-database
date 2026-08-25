#!/usr/bin/env python3
"""Build the static database browser site from db/.

Generates a self-contained static site under site/ (gitignored; built and
deployed by CI). The site is a linked graph with a faceted search front end:

  index.html                    faceted browser over every entry: filter by
                                kind, surface, family, place,
                                representativeness, applicable scale, urban
                                setting and source,
                                plus free
                                text; result cards show parameter values
                                inline; filter state lives in the URL hash
  data/index.json               the search index the browser runs on
  records/<path>.html           per-record page: envelope with clickable
                                place/source/facet chips, parameters,
                                model-ready fragment, "used by" backlinks
                                and "same study, same place" siblings
  archetypes/<path>.html        per-archetype page with resolved uses; a
                                typology also carries its photograph and the
                                attribution that photograph may not be shown
                                without
  archetypes/typologies/images/ verified copies of those photographs, taken
                                from the release named in db/images.yml so
                                the site serves them from its own origin
  place/<slug>.html             every record at a place, grouped by family
  source/<key>.html             the citation and every record citing it

Design follows the suews.io token palette (dark default).

Usage: python scripts/build_site.py [--out site]
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_db import (  # noqa: E402
    iter_uses,
    load_all,
    load_applicable_scales,
    load_urban_settings,
)
from export_record import PlainDumper, assemble  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/UMEP-dev/SUEWS-database"
SUEWS_SITE = "https://suews.io"
DOCS = "https://docs.suews.io/latest/inputs/yaml"
DOCS_REF = f"{DOCS}/config-reference"

URBAN_SETTING_LABEL = {
    key: value["label"] for key, value in load_urban_settings().items()
}
APPLICABLE_SCALE_LABEL = {
    key: value["label"] for key, value in load_applicable_scales().items()
}


def build_ref():
    """The commit this site was built from, for links that must not drift."""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            capture_output=True, check=True).stdout.strip() or "main"
    except (OSError, subprocess.CalledProcessError):
        return "main"


BUILD_REF = build_ref()
SITE_ISSUE_URL = f"{REPO_URL}/issues/new?template=site-issue.yml"
SIGNOFF_TEMPLATE_URL = f"{REPO_URL}/issues/new?template=provenance-signoff.yml"
VERIFIER_REQUEST_URL = f"{REPO_URL}/issues/new?template=verifier-request.yml"

PROVENANCE_STATE_LABEL = {
    "unaudited": "Unaudited",
    "agent_assessed": "Agent assessed",
    "awaiting_signoff": "Awaiting sign-off",
    "verified": "Verified",
    "unresolved": "Unresolved",
    "source_inaccessible": "Source inaccessible",
    "curation_required": "Curation required",
}
PROVENANCE_ROLE_LABEL = {
    "parameter_source": "Parameter source",
    "input_data": "Input observations",
    "input": "Input record",
    "compilation": "Later compilation",
    "validation": "Validation",
    "composition_source": "Composition rationale",
    "possible_duplicate": "Possible duplicate",
    "related": "Related record",
    "component": "Selected component",
}
METHOD_LABEL = {
    "measured": "Measured",
    "fitted": "Fitted",
    "literature": "Published value",
    "calculated": "Calculated",
    "assumed": "Assumed",
    "assembled": "Assembled",
}
COMPOSITION_FINDING_LABEL = {
    "name": "Composite identity",
    "target": "Composite target",
    "values": "Own parameter values",
    "source": "Composition rationale",
    "place": "Place applicability",
    "representativeness": "Representativeness",
    "applicable_scale": "Applicable scale",
    "urban_setting": "Urban setting applicability",
    "method": "Slot and season mapping",
    "identity": "Completeness and uniqueness",
}

# A report control that follows the reader down every page. Scaffolding for
# the phase where the site is still being shaped and structural problems are
# found faster than they can be filed -- not the furniture of a finished site.
# Set this to False to remove it: nothing else depends on it, the record rail
# button and the footer link stand on their own, and no issue template, label
# or stored state outlives it.
FLOATING_REPORT = True

# Typology photographs. The files are release assets rather than repository
# content (db/images.yml explains why); the site serves its own verified copy
# of each, so a reader's browser never contacts the host the photograph came
# from and no upstream link rot can blank a page.
IMAGES_FILE = ROOT / "db" / "images.yml"
IMAGE_CACHE = ROOT / ".image-cache"
IMAGE_SUBDIR = "archetypes/typologies/images"
IMAGE_AGENT = f"SUEWS-database-site-build/1.0 ({REPO_URL})"

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

# typology (assembly) group -> display label
TYP_LABEL = {
    "surfaces": "Surface bundles", "countries": "Country defaults",
    "regions": "Regional defaults", "typologies": "Urban types",
    "materials": "Materials", "constructions": "Constructions",
    "snow": "Snow bundles",
}

# facet key -> the group title the browser shows (rail headings and the
# active-filter chips share this table)
FACET_TITLE = {
    "kind": "Kind", "surface": "Land cover", "family": "Family",
    "typology": "Typology", "region": "Region", "country": "Country",
    "city": "City", "rep": "Representativeness", "scale": "Applicable scale",
    "setting": "Urban setting",
    "source": "Source",
    "method": "Method", "verification": "Review state",
    "role": "Provenance role", "place": "Place",
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


def load_site_provenance(base=ROOT / "db" / "provenance"):
    """Load sidecars without adding checker-only dependencies to site builds."""
    sidecars = {}
    if not base.exists():
        return sidecars
    for fp in sorted(base.rglob("*.yml")):
        key = str(fp.relative_to(base).with_suffix(""))
        sidecars[key] = yaml.safe_load(fp.read_text())
    return sidecars


def load_site_policy(path=ROOT / ".github" / "provenance-verifiers.yml"):
    """Load the CI-validated policy and reproduce its RFC 8785 revision.

    The policy schema permits only ASCII strings, integers, arrays and maps, so
    sorted compact JSON is byte-identical to RFC 8785 canonicalization here.
    """
    if not path.exists():
        return None
    document = yaml.safe_load(path.read_text())
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    verifiers = {
        item["github_handle"].casefold(): item
        for item in document.get("verifiers", [])
    }
    return {
        "revision": "sha256:" + sha256(canonical).hexdigest(),
        "required_signoffs": document.get("required_signoffs", 1),
        "required_scopes": set(document.get("required_scopes", ["record"])),
        "verifiers": verifiers,
    }


def merge_github_signoffs(sidecars):
    """Sweep authenticated sign-off issues during CI site builds."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return
    from github_attestation import fetch_issue_attestations
    from verifier_policy import load_verifier_policy

    grouped, errors = fetch_issue_attestations(
        token, load_verifier_policy(), sidecars
    )
    for error in errors:
        print(f"site sign-off ignored: {error}", file=sys.stderr)
    for record_path, attestations in grouped.items():
        sidecars[record_path].setdefault("verification", {}).setdefault(
            "attestations", []
        ).extend(attestations)


def _event_key(attestation):
    event = attestation.get("event", {})
    return event.get("kind"), event.get("id")


def attestation_states(sidecar, policy):
    """Return current, stale or superseded display state for each event."""
    assessment = sidecar.get("assessment", {})
    evidence_revision = assessment.get("evidence_revision")
    policy_revision = policy.get("revision") if policy else None
    attestations = sidecar.get("verification", {}).get("attestations", [])
    states = {}
    current = set()
    for item in attestations:
        key = _event_key(item)
        stale = (
            item.get("evidence_revision") != evidence_revision
            or item.get("verifier_policy_revision") != policy_revision
        )
        states[key] = "stale" if stale else "current"
        if not stale:
            current.add(key)
    superseded = {
        (target.get("kind"), target.get("id"))
        for item in attestations
        if _event_key(item) in current
        and (target := item.get("supersedes_event"))
    }
    for key in current & superseded:
        states[key] = "superseded"
    return states


def provenance_state(sidecar, policy):
    """Derive the review state displayed by the already-CI-validated site."""
    if not sidecar:
        return "unaudited"
    assessment = sidecar.get("assessment", {})
    status = assessment.get("status")
    if status in {"unresolved", "source_inaccessible", "curation_required"}:
        return status
    if status != "agent_assessed":
        return "agent_assessed"
    findings = assessment.get("findings", {})
    review_type = sidecar.get("review_type", "evidence")
    required_supported = (
        ("values", "method") if review_type == "evidence" else ("method",)
    )
    if "urban_setting" in findings:
        required_supported += ("urban_setting",)
    if (
        not findings
        or any(
            item.get("conclusion") not in {"supported", "not_applicable"}
            for item in findings.values()
        )
        or any(
            findings.get(scope, {}).get("conclusion") != "supported"
            for scope in required_supported
        )
    ):
        return "agent_assessed"
    if (
        review_type == "evidence"
        and assessment.get("method") in {"measured", "fitted", "literature"}
    ):
        if findings.get("source", {}).get("conclusion") != "supported":
            return "agent_assessed"
    if not policy:
        return "awaiting_signoff"

    attestations = sidecar.get("verification", {}).get("attestations", [])
    states = attestation_states(sidecar, policy)
    current = []
    for item in attestations:
        if states.get(_event_key(item)) != "current":
            continue
        verifier = policy["verifiers"].get(
            str(item.get("verifier", "")).casefold()
        )
        if (
            not verifier
            or verifier.get("github_user_id") != item.get("verifier_id")
            or item.get("scope") not in verifier.get("scopes", [])
        ):
            continue
        current.append(item)
    decisions = {item.get("decision") for item in current}
    if decisions & {"changes_requested", "unresolved", "curation_required"}:
        return "awaiting_signoff"
    signed = {
        item.get("verifier_id")
        for item in current
        if item.get("decision") == "verified"
    }
    scopes = {
        item.get("scope")
        for item in current
        if item.get("decision") == "verified"
    }
    if (
        len(signed) >= policy["required_signoffs"]
        and policy["required_scopes"] <= scopes
    ):
        return "verified"
    return "awaiting_signoff"


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

LIGHT_TOKENS = """
  color-scheme: light;
  --bg-primary: #FAFBFC; --bg-secondary: #F1F5F9;
  --bg-card: #FFFFFF; --bg-card-hover: #F1F5F9;
  --border-light: #E2E8F0; --border-medium: #CBD5E1;
  --text-primary: #1E293B; --text-secondary: #475569; --text-muted: #4B5563;
  --hairline: #E9EEF4; --hatch: rgba(45,49,66,0.07);
  --link: var(--wave-blue); --accent-label: var(--sun-gold-dark);
  --focus-ring: var(--wave-blue);
  --map-ground: #EDF2F7;
  --land-fill: rgba(45,49,66,0.10); --land-stroke: rgba(45,49,66,0.24);
  --dot-stroke: rgba(255,255,255,0.85);
  --acc-neutral: rgba(45,49,66,0.30); --acc-snow: #7FB4D8;
  --cell-ink-lo: #1E293B;
  --gold-ink: #8A5D00; --mono-ink: var(--water-blue);
  --fig-gold: #B37A0A; --fig-blue: var(--wave-blue); --fig-green: #07803F;
"""

CSS = """
:root {
  color-scheme: dark;
  /* brand palette, constant across themes */
  --sun-gold: #F7B538; --sun-gold-dark: #D4940F;
  --energy-orange: #E85D04; --water-blue: #0077B6;
  --water-blue-light: #48CAE4; --sky-blue: #5DADE2; --veg-green: #09a25c;
  --wave-blue: #0558a5; --urban-slate: #2D3142;
  /* dark theme, the default */
  --bg-primary: #0F1119; --bg-secondary: #1A1D2E;
  --bg-card: rgba(255,255,255,0.03); --bg-card-hover: rgba(255,255,255,0.06);
  --border-light: rgba(255,255,255,0.08); --border-medium: rgba(255,255,255,0.16);
  --text-primary: rgba(255,255,255,0.92); --text-secondary: rgba(255,255,255,0.7);
  --text-muted: rgba(255,255,255,0.55);
  --hairline: rgba(255,255,255,0.045); --hatch: rgba(255,255,255,0.035);
  --link: var(--sky-blue); --accent-label: var(--sun-gold);
  --focus-ring: var(--sun-gold);
  --map-ground: #0c0e16;
  --land-fill: rgba(255,255,255,0.10); --land-stroke: rgba(255,255,255,0.16);
  --dot-stroke: rgba(0,0,0,0.35);
  --acc-neutral: rgba(255,255,255,0.25); --acc-snow: #cfe8ff;
  --cell-ink-lo: rgba(255,255,255,0.86);
  --gold-ink: var(--sun-gold); --mono-ink: var(--water-blue-light);
  --cell-ink-hi: #14172a; --on-accent: #1A1D2E;
  --fig-gold: var(--sun-gold); --fig-blue: var(--sky-blue);
  --fig-green: var(--veg-green);
}
:root[data-theme="light"] {""" + LIGHT_TOKENS + """}
/* no stored choice and no explicit attribute: follow the system */
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {""" + LIGHT_TOKENS + """}
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg-primary); color: var(--text-primary);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
::selection { background: rgba(247,181,56,0.85); color: #1A1D2E; }
:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px;
  border-radius: 4px; }
input, textarea { caret-color: var(--sun-gold); }
* { scrollbar-width: thin; scrollbar-color: var(--border-medium) transparent; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: var(--border-medium); border-radius: 4px; }
::-webkit-scrollbar-track { background: transparent; }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; text-underline-offset: 0.2em;
  text-decoration-color: rgba(93,173,226,0.6); }
h2, h3 { text-wrap: balance; }
p { text-wrap: pretty; }
.wrap { max-width: 1160px; margin: 0 auto; padding: 1.6rem 1.25rem 4rem; }
header.site { border-bottom: 1px solid var(--border-light); background: var(--bg-secondary); }
header.site .wrap { display: flex; align-items: baseline; gap: 0.35rem 1rem;
  padding: 1rem 1.25rem; flex-wrap: wrap; }
header.site h1 { font-size: 1.1rem; margin: 0; white-space: nowrap; }
header.site h1 a { color: var(--text-primary); }
header.site .sub { color: var(--text-muted); font-size: 0.85rem; }
h2 { margin: 2rem 0 0.8rem; font-size: 1.2rem; }
h3 { margin: 1.5rem 0 0.5rem; font-size: 1rem; color: var(--text-secondary); }
.chip { display: inline-block; padding: 0.12rem 0.65rem; border-radius: 999px;
  font-size: 0.78rem; border: 1px solid var(--border-medium);
  color: var(--text-secondary); margin: 0 0.3rem 0.3rem 0; cursor: pointer;
  background: transparent; font-family: inherit; }
a.chip { color: var(--sky-blue); }
a.chip:hover { text-decoration: none; border-color: var(--sky-blue); }
.chip.on { background: var(--sun-gold); color: var(--on-accent);
  border-color: var(--sun-gold);
  font-weight: 600; }
.chip .n { color: var(--text-muted); font-size: 0.72rem; margin-left: 0.25rem;
  font-variant-numeric: tabular-nums; }
input.search { width: 100%; padding: 0.7rem 1.1rem; border-radius: 10px;
  border: 1px solid var(--border-medium); background: var(--bg-secondary);
  color: var(--text-primary); font-size: 1rem; margin: 0.9rem 0 0.9rem; }
input.search:focus { outline: 2px solid var(--sun-gold); border-color: transparent; }
#nres { color: var(--text-muted); font-size: 0.85rem; margin: 0.4rem 0 0.8rem;
  font-variant-numeric: tabular-nums; }
.vals { color: var(--text-secondary); font-size: 0.8rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; line-height: 1.7; }
.vals b { color: var(--mono-ink); font-weight: 500; }
table.kv { border-collapse: collapse; width: 100%; margin: 0.6rem 0 1rem; }
table.kv td, table.kv th { text-align: left; padding: 0.4rem 0.75rem;
  overflow-wrap: anywhere;
  border-bottom: 1px solid var(--border-light); font-size: 0.92rem; vertical-align: top; }
table.kv th { color: var(--text-muted); font-weight: 500; width: 220px; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
code { background: var(--bg-card); padding: 0.1rem 0.35rem; border-radius: 5px;
  font-size: 0.88em; overflow-wrap: anywhere; }
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
.actions a.report { border-color: var(--gold-ink); color: var(--gold-ink); }
.actions a.report:hover { background: rgba(247,181,56,0.12); text-decoration: none; }
footer .report { color: var(--gold-ink); }
footer { margin-top: 3.5rem; padding-top: 1.2rem; border-top: 1px solid var(--border-light);
  color: var(--text-muted); font-size: 0.82rem; }
.hidden { display: none; }
.headline { margin: 0.2rem 0 0.6rem; font-size: 1.05rem; }
.headline b { color: var(--mono-ink); font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.headline .v { color: var(--gold-ink); font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.subtitle { color: var(--text-muted); font-size: 0.95rem; font-weight: 400; }
table.params td:first-child { width: 320px; }
table.params .val { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--gold-ink); }
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
.acc-snow { --acc: var(--acc-snow); }
.acc-none { --acc: var(--acc-neutral); }
.hero { padding: 1.6rem 0 0.2rem; }
.hero h2 { margin: 0 0 0.3rem; font-size: 1.75rem; letter-spacing: -0.01em; }
.hero p { margin: 0; color: var(--text-secondary); max-width: 680px; }
.statline { display: flex; flex-wrap: wrap; gap: 1.8rem; margin: 1rem 0 1.4rem;
  color: var(--text-muted); font-size: 0.85rem; }
.statline b { color: var(--gold-ink); font-weight: 600; margin-right: 0.3rem;
  font-variant-numeric: tabular-nums; }
.layout { display: grid; grid-template-columns: 236px minmax(0, 1fr); gap: 1.8rem; }
details.fgroup { margin-bottom: 0.9rem; }
details.fgroup summary { cursor: pointer; font-size: 0.72rem;
  text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted);
  font-weight: 600; padding: 0.3rem 0; list-style-position: outside; }
details.fgroup summary:hover { color: var(--text-primary); }
.gcap { font-size: 0.75rem; color: var(--text-muted); margin: 0 0 0.35rem;
  line-height: 1.45; }
.sw { display: inline-block; width: 8px; height: 8px; border-radius: 2px;
  background: var(--acc); }
.fitem { display: flex; justify-content: space-between; align-items: baseline;
  gap: 0.4rem; width: 100%; padding: 0.42rem 0.55rem; border: none;
  border-radius: 7px; font-size: 0.88rem; color: var(--text-secondary);
  cursor: pointer; background: transparent; font-family: inherit;
  text-align: left; }
.fitem:hover { background: var(--bg-card-hover); color: var(--text-primary); }
.fitem.on { background: var(--sun-gold); color: var(--on-accent);
  font-weight: 600; }
.fitem.warnv .fv { color: var(--gold-ink); font-style: italic; }
.fitem .fv { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.fitem .n { font-size: 0.75rem; color: var(--text-muted);
  font-variant-numeric: tabular-nums; }
.fitem.on .n { color: inherit; }
.fitem .sw { flex: 0 0 8px; align-self: center; }
.fdiv { border-top: 1px solid var(--border-light); margin: 0.45rem 0.55rem; }
.fmore { padding: 0.22rem 0.55rem; font-size: 0.8rem; color: var(--text-muted); }
.maplink { display: block; padding: 0.22rem 0.55rem; font-size: 0.8rem; }
input.ffind { width: 100%; padding: 0.3rem 0.55rem; margin: 0 0 0.3rem;
  border-radius: 7px; border: 1px solid var(--border-light);
  background: var(--bg-secondary); color: var(--text-primary);
  font: inherit; font-size: 0.82rem; }
input.ffind:focus { outline: 1px solid var(--sun-gold); }
.badge-unref { display: inline-block; padding: 0.05rem 0.5rem;
  border-radius: 999px; border: 1px solid rgba(247,181,56,0.55);
  color: var(--gold-ink); font-size: 0.72rem; font-style: italic;
  vertical-align: 0.1em; }
.results2 { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 0.8rem; }
.card2 { border: 1px solid var(--border-light); border-top: 3px solid var(--acc);
  border-radius: 10px; background: var(--bg-card); padding: 0.75rem 0.95rem 0.85rem; }
.card2:hover { background: var(--bg-card-hover); border-color: var(--border-medium);
  border-top-color: var(--acc); }
.card2 .t { font-weight: 600; font-size: 0.95rem; margin-bottom: 0.15rem; }
.card2 .meta2 { color: var(--text-muted); font-size: 0.8rem; margin-bottom: 0.5rem; }
.pv { display: inline-block; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.76rem; background: var(--bg-card-hover);
  border: 1px solid var(--border-light); border-radius: 6px;
  padding: 0.08rem 0.45rem; margin: 0 0.25rem 0.25rem 0; color: var(--text-secondary); }
.pv b { color: var(--gold-ink); font-weight: 600; }
.heroval { display: flex; gap: 0.8rem; flex-wrap: wrap; margin: 0.8rem 0 1.2rem; }
.hv { border: 1px solid var(--border-light);
  border-radius: 10px; background: var(--bg-card); padding: 0.55rem 1rem 0.6rem; }
.hv .k { display: block; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.72rem; color: var(--text-muted); margin-bottom: 0.1rem; }
.hv .k .sw { margin-right: 0.45rem; vertical-align: 0.05em; }
.hv .v { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 1.35rem; font-weight: 600; color: var(--gold-ink);
  font-variant-numeric: tabular-nums; }
/* the typology photograph; the attribution is part of the figure, never
   optional furniture, because the licences it carries require it */
.typoshot { margin: 0 0 1.6rem; }
.typoshot img { display: block; width: 100%; height: auto; max-width: 100%;
  border-radius: 12px; border: 1px solid var(--border-light);
  background: var(--bg-card); }
.typoshot figcaption { margin-top: 0.5rem; font-size: 0.84rem;
  color: var(--text-secondary); line-height: 1.5; }
.typoshot figcaption .credit { display: block; font-size: 0.78rem;
  color: var(--text-muted); }
/* the invitation where a typology has no photograph: a quiet panel rather
   than an empty frame, because a placeholder that looks like a broken image
   is worse than saying plainly what is missing */
.nophoto { border: 1px dashed var(--border-light); border-radius: 12px;
  background: var(--bg-card); padding: 1.1rem 1.2rem 1rem;
  margin: 0 0 1.6rem; }
.nophoto p { margin: 0 0 0.6rem; font-size: 0.9rem;
  color: var(--text-secondary); }
.nophoto .crumbs { margin: 0.7rem 0 0; font-size: 0.8rem; }
.photobtn { display: inline-block; padding: 0.45rem 0.95rem;
  border-radius: 999px; font-size: 0.85rem; border: 1px solid var(--gold-ink);
  color: var(--gold-ink); }
.photobtn:hover { background: rgba(247,181,56,0.12); text-decoration: none; }
.cols { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 1.8rem;
  align-items: start; }
/* a grid item's automatic minimum is its min-content width, so on a phone --
   where the two columns collapse to a bare 1fr -- a full-width photograph or
   a wide table would push the whole column past the viewport and take the
   page with it. The desktop track spells this out as minmax(0, 1fr); the
   collapsed one needs it on the items. */
.cols > *, .layout > * { min-width: 0; }
.side { border: 1px solid var(--border-light); border-radius: 12px;
  background: var(--bg-card); padding: 0.95rem 1.1rem 1rem; margin-bottom: 1rem; }
.side h4 { margin: 0 0 0.6rem; font-size: 0.72rem; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600; }
.side .prow { display: flex; gap: 0.8rem; padding: 0.3rem 0;
  border-bottom: 1px solid var(--border-light); font-size: 0.88rem; }
.side .prow:last-child { border-bottom: none; }
.side .prow .pk { flex: 0 0 88px; color: var(--text-muted); font-size: 0.8rem;
  padding-top: 0.1rem; }
.side .prow > span:last-child { min-width: 0; overflow-wrap: anywhere; }
.side ul.linked li { font-size: 0.86rem; }
.side details summary { cursor: pointer; color: var(--text-muted);
  font-size: 0.83rem; margin: 0.3rem 0; }
.side .allof { display: block; margin-top: 0.5rem; font-size: 0.83rem; }
.pstate { display: inline-block; padding: 0.16rem 0.65rem;
  border: 1px solid var(--border-medium); border-radius: 999px;
  color: var(--text-secondary); font-size: 0.75rem; font-weight: 600; }
.pstate-verified { border-color: var(--veg-green); color: var(--fig-green); }
.pstate-awaiting-signoff, .pstate-curation-required {
  border-color: var(--gold-ink); color: var(--gold-ink); }
.pstate-unresolved, .pstate-source-inaccessible {
  border-color: var(--energy-orange); color: var(--energy-orange); }
.provhead { display: flex; align-items: center; justify-content: space-between;
  gap: 0.8rem; flex-wrap: wrap; margin: 2rem 0 0.75rem; }
.provhead h3 { margin: 0; }
.provmeta { color: var(--text-muted); font-size: 0.84rem; margin: 0 0 1rem; }
.evidence-list { border-top: 1px solid var(--border-light); }
.evidence-item { padding: 0.9rem 0; border-bottom: 1px solid var(--border-light); }
.evidence-item h4 { margin: 0 0 0.35rem; font-size: 0.95rem; font-weight: 600; }
.erole { display: inline-block; margin-left: 0.45rem; padding: 0.08rem 0.5rem;
  border-radius: 999px; border: 1px solid var(--border-medium);
  color: var(--text-muted); font-size: 0.7rem; font-weight: 500;
  vertical-align: 0.12em; }
.locator { margin: 0.35rem 0 0; color: var(--text-secondary); font-size: 0.86rem; }
.locator b { color: var(--text-primary); font-weight: 600; }
.finding-supported { color: var(--fig-green); }
.finding-blocked { color: var(--gold-ink); }
.signoff { display: block; margin-top: 0.8rem; padding: 0.58rem 0.8rem;
  border-radius: 8px; border: 1px solid var(--gold-ink);
  color: var(--gold-ink); text-align: center; font-weight: 600; }
.signoff:hover { background: rgba(247,181,56,0.12); text-decoration: none; }
.reviewguide { display: block; margin-top: 0.7rem; padding: 0.52rem 0.8rem;
  border-radius: 8px; border: 1px solid var(--border-medium);
  color: var(--text-primary); text-align: center; font-weight: 600; }
.reviewguide:hover { border-color: var(--gold-ink); color: var(--gold-ink);
  background: rgba(247,181,56,0.08); text-decoration: none; }
.reviewinfo { display: inline-flex; width: 1.15rem; height: 1.15rem;
  margin-left: 0.3rem; align-items: center; justify-content: center;
  color: var(--text-muted); vertical-align: -0.2rem; }
.reviewinfo:hover { color: var(--gold-ink); text-decoration: none; }
.reviewinfo svg { width: 100%; height: 100%; fill: none; stroke: currentColor;
  stroke-width: 1.4; stroke-linecap: round; }
.signoff-help { color: var(--text-muted); font-size: 0.78rem;
  line-height: 1.45; margin: 0.55rem 0 0; }
.reviewhero { max-width: 760px; margin: 2.6rem 0 2rem; }
.reviewhero h2 { max-width: 18ch; margin: 0 0 0.8rem; font-size: 2.15rem;
  line-height: 1.12; letter-spacing: -0.025em; }
.reviewhero > p { max-width: 68ch; color: var(--text-secondary);
  font-size: 1.02rem; line-height: 1.65; }
.reviewcontext { display: flex; align-items: baseline; gap: 0.65rem;
  flex-wrap: wrap; margin-top: 1.2rem; padding: 0.85rem 0;
  border-top: 1px solid var(--border-light);
  border-bottom: 1px solid var(--border-light); color: var(--text-secondary); }
.reviewcontext code { overflow-wrap: anywhere; }
.reviewlayout { display: grid; grid-template-columns: minmax(0, 1fr) 320px;
  gap: 2.4rem; align-items: start; margin-bottom: 2.6rem; }
.reviewsteps { list-style: none; counter-reset: reviewstep; padding: 0;
  margin: 0; max-width: 72ch; }
.reviewsteps li { counter-increment: reviewstep; display: grid;
  grid-template-columns: 2rem minmax(0, 1fr); gap: 0.85rem;
  padding: 1rem 0; border-top: 1px solid var(--border-light); }
.reviewsteps li::before { content: counter(reviewstep); width: 1.75rem;
  height: 1.75rem; border-radius: 999px; display: grid; place-items: center;
  border: 1px solid var(--border-medium); color: var(--gold-ink);
  font-size: 0.78rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.reviewsteps h3 { margin: 0 0 0.25rem; font-size: 1rem; }
.reviewsteps p { margin: 0; color: var(--text-secondary); line-height: 1.58; }
.reviewpolicy { border: 1px solid var(--border-light); border-radius: 14px;
  background: var(--bg-card); padding: 1.1rem 1.2rem 1.2rem; }
.reviewpolicy h3 { margin: 0 0 0.6rem; font-size: 1.05rem; }
.reviewpolicy p, .reviewpolicy li { color: var(--text-secondary);
  font-size: 0.86rem; line-height: 1.55; }
.reviewpolicy ul { margin: 0.65rem 0 1rem; padding-left: 1.2rem; }
.reviewgate { padding: 0.75rem 0.8rem; border-radius: 9px;
  border: 1px solid var(--gold-ink); color: var(--text-primary) !important;
  background: rgba(247,181,56,0.08); }
.attestation { padding: 0.5rem 0; border-bottom: 1px solid var(--border-light);
  font-size: 0.84rem; }
.attestation:last-child { border-bottom: 0; }
.attestation .decision { color: var(--text-secondary); }
.attestation .stale { color: var(--gold-ink); }
.attestation .superseded { color: var(--text-muted); }
.stag { display: inline-block; padding: 0.14rem 0.7rem; border-radius: 999px;
  font-size: 0.78rem; background: var(--acc); color: var(--on-accent);
  font-weight: 600;
  vertical-align: 0.18em; margin-left: 0.55rem; }
.mapwrap { position: relative; border: 1px solid var(--border-light);
  border-radius: 14px; background: var(--bg-secondary); padding: 0.6rem;
  margin: 1rem 0 0.4rem; }
.mapwrap svg { display: block; width: 100%; height: auto; }
.land { fill: var(--land-fill); stroke: var(--land-stroke);
  stroke-width: 0.5; }
.dot { fill: var(--sun-gold); fill-opacity: 0.75; stroke: var(--dot-stroke);
  stroke-width: 0.6; }
a:hover .dot, .dot:hover { fill-opacity: 1; }
.dot.dim { fill-opacity: 0.15; }
.mapcap { color: var(--text-muted); font-size: 0.8rem; margin: 0.2rem 0 1rem; }
.placerows { columns: 3; column-gap: 2rem; margin: 0.6rem 0 1rem; }
.placerows .fitem { break-inside: avoid; }
header.site .nav { margin-left: auto; font-size: 0.85rem; white-space: nowrap; }
.fscroll { max-height: 252px; overflow-y: auto; }
.rf-panel { fill: var(--bg-card-hover); stroke: var(--border-medium); }
.rf-title { fill: var(--text-primary); }
.rf-sub { fill: var(--text-muted); }
.rf-line { stroke: var(--border-medium); fill: none; }
.rf-dash { stroke-dasharray: 4 4; }
.rf-head { fill: var(--border-medium); }
.rf-knock { fill: var(--bg-primary); }
.rf-box { fill-opacity: 0.12; }
.rf-rec { fill: var(--sun-gold); stroke: var(--fig-gold); }
.rf-typ { fill: var(--sky-blue); stroke: var(--fig-blue); }
.rf-cfg { fill: var(--veg-green); stroke: var(--fig-green); }
.rf-rec-ink { fill: var(--fig-gold); }
.rf-typ-ink { fill: var(--fig-blue); }
.rf-cfg-ink { fill: var(--fig-green); }
.tbtn { margin-left: 0.85rem; width: 30px; height: 30px; padding: 0;
  border-radius: 8px; border: 1px solid var(--border-medium);
  background: var(--bg-card); color: var(--text-secondary); cursor: pointer;
  font-size: 0.95rem; line-height: 1; vertical-align: -0.35em; }
.tbtn:hover { background: var(--bg-card-hover); color: var(--text-primary);
  border-color: var(--focus-ring); }
.relfig { display: block; width: 100%; max-width: 780px; height: auto;
  margin: 0.4rem auto 1rem; }
.otiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 0.6rem; margin: 0.4rem 0 0.8rem; }
.otile { display: flex; align-items: baseline;
  gap: 0.55rem; border: 1px solid var(--border-light); border-radius: 9px;
  background: var(--bg-card); padding: 0.55rem 0.8rem;
  color: var(--text-primary); }
.otile:hover { background: var(--bg-card-hover); text-decoration: none;
  border-color: var(--border-medium); }
.otile .sw { flex: 0 0 8px; align-self: center; }
.otile b { flex: 1; font-weight: 600; font-size: 0.9rem; }
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
.card2 { transition: transform 0.15s ease-out, background 0.15s ease-out; }
.card2:hover { transform: translateY(-1px); }
@keyframes rise { from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; } }
#results.anim { animation: rise 0.28s cubic-bezier(0.16, 1, 0.3, 1); }
@media (prefers-reduced-motion: reduce) {
  #results.anim { animation: none; }
  .card2 { transition: none; }
  .card2:hover { transform: none; }
}

/* --- arrival: two doors ------------------------------------------------ */
.doors { display: grid; grid-template-columns: 1fr 1fr; gap: 1.4rem;
  margin: 1.3rem 0 1rem; align-items: stretch; }
.door { border: 1px solid var(--border-light); border-radius: 14px;
  background: var(--bg-card); padding: 1.15rem 1.25rem 1.3rem; }
.dlab { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--text-muted); font-weight: 600; margin: 0 0 0.65rem; }
.door input.search { margin: 0 0 0.55rem; }
.tahead { border: 1px solid var(--border-medium); border-radius: 10px;
  background: var(--bg-secondary); overflow: hidden; margin-bottom: 0.6rem; }
.tahead a { display: flex; justify-content: space-between; gap: 1rem;
  padding: 0.42rem 0.8rem; color: var(--text-primary); font-size: 0.88rem;
  border-bottom: 1px solid var(--border-light); }
.tahead a:last-child { border-bottom: 0; }
.tahead a:hover { background: var(--bg-card-hover); text-decoration: none; }
.tahead .k { color: var(--text-muted); font-size: 0.72rem;
  text-transform: uppercase; letter-spacing: 0.06em; margin-right: 0.45rem; }
.famgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 1.3rem; }
a.grow { display: flex; justify-content: space-between; gap: 0.8rem;
  padding: 0.24rem 0; font-size: 0.88rem; color: var(--text-secondary);
  border-bottom: 1px solid var(--hairline); }
a.grow:hover { color: var(--text-primary); text-decoration: none; }
a.grow .n { flex: 0 0 auto; }
.mapbox { border: 1px solid var(--border-light); border-radius: 10px;
  background: var(--map-ground); padding: 0.4rem; }
.mapbox svg { width: 100%; height: auto; display: block; }
.geocols { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
  margin-top: 1rem; }
.gscroll { max-height: 186px; overflow-y: auto; padding-right: 0.3rem;
  mask-image: linear-gradient(to bottom, #000 calc(100% - 22px), transparent);
  -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 22px),
    transparent); }
.tband { display: grid; grid-template-columns: 168px repeat(auto-fit,
  minmax(132px, 1fr)); gap: 0.6rem; align-items: stretch; margin: 0 0 1.3rem; }
.tlead { display: flex; align-items: center; font-size: 0.82rem;
  color: var(--text-muted); }
.invstrip { display: grid; grid-template-columns: repeat(4, 1fr);
  border: 1px solid var(--border-light); border-radius: 14px; overflow: hidden;
  margin-bottom: 2rem; }
.icell { display: block; padding: 1rem 1.15rem; color: var(--text-secondary);
  border-right: 1px solid var(--border-light); }
.icell:last-child { border-right: 0; }
.icell:hover { background: var(--bg-card-hover); text-decoration: none; }
.icell b { display: block; font-size: 1.65rem; color: var(--gold-ink);
  font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
.icell span { font-size: 0.82rem; }
.icell.quiet b { color: var(--text-secondary); }
/* coverage matrix: what the database holds, and where it does not */
.matrixwrap { overflow-x: auto; border: 1px solid var(--border-light);
  border-radius: 12px; }
table.matrix { border-collapse: separate; border-spacing: 0; width: 100%;
  font-size: 0.8rem; font-variant-numeric: tabular-nums; table-layout: fixed;
  min-width: 720px; }
table.matrix th { font-weight: 500; color: var(--text-muted); text-align: left;
  padding: 0.5rem 0.6rem; white-space: nowrap; line-height: 1.35;
  border-bottom: 1px solid var(--border-light); background: var(--bg-secondary);
  position: sticky; top: 0; z-index: 2; font-size: 0.74rem; }
table.matrix thead th:not(.corner) { white-space: normal; width: 68px;
  padding: 0.45rem 0.4rem; }
table.matrix thead th.corner { left: 0; z-index: 3; text-transform: uppercase;
  letter-spacing: 0.05em; }
table.matrix tbody th { position: sticky; left: 0; z-index: 1; top: auto;
  background: var(--bg-primary); color: var(--text-secondary); font-size: 0.8rem;
  border-right: 1px solid var(--border-light); }
table.matrix tbody th a { color: inherit; display: inline-block;
  max-width: calc(100% - 2.6rem); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; vertical-align: bottom; }
.rn { float: right; color: var(--text-muted); font-size: 0.74rem;
  font-variant-numeric: tabular-nums; }
table.matrix td { padding: 0; text-align: center; height: 27px;
  border-bottom: 1px solid var(--hairline);
  border-right: 1px solid var(--hairline); }
table.matrix td a.lo { color: var(--cell-ink-lo); }
table.matrix td a.hi { color: var(--cell-ink-hi); }
table.matrix td a { display: block; padding: 0.42rem 0.3rem; font-weight: 600; }
table.matrix td a:hover { text-decoration: none;
  outline: 1px solid var(--sun-gold); outline-offset: -1px; }
table.matrix td.mt { background: repeating-linear-gradient(135deg,
  transparent 0 5px, var(--hatch) 5px 6px); }
.mcap { color: var(--text-muted); font-size: 0.82rem; margin: 0.6rem 0 1.8rem; }
.lowcards { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.lcard { border: 1px solid var(--border-light); border-radius: 12px;
  padding: 0.9rem 1.1rem; background: var(--bg-card);
  color: var(--text-secondary); }
.lcard:hover { background: var(--bg-card-hover); text-decoration: none;
  border-color: var(--border-medium); }
.lcard b { display: block; color: var(--text-primary); margin-bottom: 0.2rem;
  font-size: 0.95rem; }
.lcard span b.report { display: inline; margin: 0; font-size: inherit;
  color: var(--gold-ink); font-weight: 600; }
.lcard span { font-size: 0.85rem; }
@media (max-width: 900px) {
  .doors, .lowcards { grid-template-columns: 1fr; }
  .geocols, .famgrid { grid-template-columns: 1fr; }
  .invstrip { grid-template-columns: 1fr 1fr; }
  .icell { border-bottom: 1px solid var(--border-light); }
  .tband { grid-template-columns: 1fr 1fr; }
  .layout, .cols { grid-template-columns: 1fr; }
  .reviewhero, .reviewlayout, .reviewpolicy { min-width: 0; }
  .reviewhero h2 { max-width: 16ch; font-size: 1.85rem;
    overflow-wrap: anywhere; }
  .reviewcontext { display: grid; grid-template-columns: minmax(0, 1fr); }
  .reviewcontext code { min-width: 0; max-width: 100%; word-break: break-word; }
  .reviewlayout { grid-template-columns: minmax(0, 1fr); gap: 1.4rem; }
  .layout > .rail { order: 2; }
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
    }).catch(() => {
      // clipboard denied: select the fragment so a manual copy is one keystroke
      const r = document.createRange();
      r.selectNodeContents(w.querySelector('pre'));
      const s = window.getSelection();
      s.removeAllRanges(); s.addRange(r);
      btn.textContent = 'Selected — press Ctrl/Cmd+C';
      setTimeout(() => { btn.textContent = 'Copy'; }, 3000);
    });
  });
  w.appendChild(btn);
});
</script>
"""


def esc(s):
    return html.escape(str(s), quote=True)


THEME_BOOT = """<script>
(function () {
  try {
    var t = localStorage.getItem('theme');
    if (t !== 'light' && t !== 'dark') {
      t = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    document.documentElement.setAttribute('data-theme', t);
  } catch (e) {}
})();
</script>"""

THEME_JS = """<script>
(function () {
  var btn = document.getElementById('themetoggle');
  if (!btn) return;
  var root = document.documentElement;
  function sync() {
    var light = root.getAttribute('data-theme') === 'light';
    btn.textContent = light ? '\u263E' : '\u2600';
    btn.setAttribute('aria-label',
      'Switch to the ' + (light ? 'dark' : 'light') + ' theme');
    btn.setAttribute('aria-pressed', light ? 'true' : 'false');
  }
  sync();
  btn.addEventListener('click', function () {
    var next = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (e) {}
    sync();
  });
  try {
    // follow the system only while the reader has expressed no preference
    matchMedia('(prefers-color-scheme: light)').addEventListener(
      'change', function (ev) {
        var stored = null;
        try { stored = localStorage.getItem('theme'); } catch (e) {}
        if (stored === 'light' || stored === 'dark') return;
        root.setAttribute('data-theme', ev.matches ? 'light' : 'dark');
        sync();
      });
  } catch (e) {}
})();
</script>"""


FAB_CSS = """
/* the floating report control; goes with FLOATING_REPORT */
.fab { position: fixed; right: 1.1rem; bottom: 1.1rem; z-index: 50;
  display: inline-flex; align-items: center; gap: 0.5rem;
  padding: 0.55rem 1rem; border-radius: 999px; font-size: 0.85rem;
  border: 1px solid var(--gold-ink); color: var(--gold-ink);
  background: var(--bg-secondary);
  box-shadow: 0 6px 20px rgba(0,0,0,0.28); }
.fab:hover { background: rgba(247,181,56,0.12); text-decoration: none; }
.fab .dot { width: 7px; height: 7px; border-radius: 50%; flex: none;
  background: currentColor; }
/* keep the footer clear of it once the reader reaches the bottom; the
   child combinator matters -- the header has a .wrap of its own, and
   padding it out leaves a tall empty band under the banner */
body.hasfab > .wrap { padding-bottom: 6rem; }
@media (max-width: 560px) { .fab { right: 0.7rem; bottom: 0.7rem;
  padding: 0.5rem 0.85rem; font-size: 0.8rem; } }
@media print { .fab { display: none; } }
"""

if FLOATING_REPORT:
    CSS += FAB_CSS

FAB_JS = """<script>
(function () {
  var a = document.getElementById("fabreport");
  // record pages report the record, and their address does not move
  if (!a || !a.dataset.base) return;
  function sync() {
    // the browser keeps its filter state in the hash; a report from a
    // filtered view is worth nothing without it
    var h = location.hash.slice(1);
    var where = h ? " " + decodeURIComponent(h).replace(/&/g, ", ") : "";
    a.href = a.dataset.base
      + "&title=" + encodeURIComponent(a.dataset.title + where)
      + "&page=" + encodeURIComponent(location.href);
  }
  // composing from the stored base is idempotent, so this can run as
  // often as it likes: on arrival, whenever the view changes, and once
  // more on the way out in case anything moved it without a hashchange
  sync();
  addEventListener("hashchange", sync);
  a.addEventListener("click", sync);
})();
</script>"""


def page(title, body, depth=0, script="", report_url=None):
    """One page. `report_url` scopes the floating control to a record;
    without it the control reports the page the reader is on."""
    rel = "../" * depth
    fab = fab_js = body_cls = ""
    if FLOATING_REPORT:
        body_cls = ' class="hasfab"'
        # a page-scoped report needs the page address, which only the browser
        # knows: the site has no base URL compiled into it, and on the
        # browse index the address keeps moving as facets are chosen
        if report_url:
            href, data = report_url, ""
        else:
            base_title = '[site] ' + title
            href = f"{SITE_ISSUE_URL}&title={quote(base_title, safe='')}"
            data = (f' data-base="{esc(SITE_ISSUE_URL)}"'
                    f' data-title="{esc(base_title)}"')
        fab = (f'\n<a class="fab" id="fabreport" href="{esc(href)}"{data}>'
               '<span class="dot" aria-hidden="true"></span>'
               'Report an issue</a>')
        fab_js = "\n" + FAB_JS
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · SUEWS parameter database</title>
{THEME_BOOT}
<style>{CSS}</style>
</head><body{body_cls}>
<header class="site"><div class="wrap">
  <h1><a href="{rel}index.html">SUEWS parameter database</a></h1>
  <span class="sub">curated values, linked and searchable, a citation on every one</span>
  <span class="nav"><a href="{rel}index.html">Home</a> ·
  <a href="{rel}map.html">Map</a> ·
  <a href="{SUEWS_SITE}">suews.io</a><button id="themetoggle" class="tbtn"
  type="button" title="Switch between the light and dark theme"></button></span>
</div></header>
<div class="wrap">
{body}
<footer>Data and site: <a href="{REPO_URL}">UMEP-dev/SUEWS-database</a> ·
parameter names follow the
<a href="{DOCS}/index.html">SUEWS YAML input specification</a> ·
per-parameter definitions in the
<a href="{DOCS_REF}/index.html">configuration reference</a> ·
cite via the <a href="{REPO_URL}/releases">archived releases</a>
(Zenodo DOI to follow with the database paper) ·
<a class="report" href="{SITE_ISSUE_URL}">report a problem with this site</a>
</footer>
</div>{fab}
{THEME_JS}
{script}{fab_js}
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
        # country-default typologies carry the country by name, not a place
        cname = rec.get("country")
        if cname:
            for info in places.values():
                if (info.get("kind") == "country"
                        and info.get("name", "").lower() == str(cname).lower()):
                    return info.get("region"), info["name"], None
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


def params_table(params, target, muted=False, linked=True, rec=None,
                 sources=None, rel=""):
    """Render a parameter block as a table: linked name, value, docs link."""
    rows = []
    for dotted, value in leaf_pairs(params):
        url = doc_url(target, dotted) if linked else None
        name = (f"<a href=\"{esc(url)}\" title=\"definition in the SUEWS "
                f"configuration reference\"><code>{esc(dotted)}</code></a>"
                if url else f"<code>{esc(dotted)}</code>")
        klass = "hrs" if isinstance(value, str) and value.count(" ") > 10 else "val"
        field_provenance = []
        row_path = f"parameters.{dotted}"
        matching_overrides = [
            (path, override)
            for path, override in (rec or {}).get(
                "parameter_provenance", {}
            ).items()
            if path == row_path or path.startswith(row_path + ".")
        ]
        grouped_overrides = defaultdict(list)
        for path, override in matching_overrides:
            effective = tuple(
                override.get(key, rec.get(key))
                for key in (
                    "source",
                    "method",
                    "place",
                    "representativeness",
                    "urban_setting",
                    "applicable_scale",
                )
            )
            grouped_overrides[effective].append(path)
        for effective, paths in sorted(
            grouped_overrides.items(), key=lambda item: repr(item[0])
        ):
            source_key, method, place, rep, setting, scale = effective
            source = (sources or {}).get(source_key, {})
            label = source_key or "field provenance"
            if source.get("year"):
                author = source.get("author", source_key).split(",")[0]
                label = f"{author} {source['year']}"
            details = ["Applies to " + ", ".join(sorted(paths))]
            for key, value in (
                ("method", method),
                ("place", place),
                ("representativeness", rep),
                ("urban setting", setting),
                ("applicable scale", scale),
            ):
                if value:
                    details.append(f"{key}: {value}")
            title = "; ".join(details)
            if source_key:
                field_provenance.append(
                    f' <a class="chip" href="{rel}source/{esc(source_key)}.html" '
                    f'title="{esc(title)}">{esc(label)}</a>'
                )
            else:
                field_provenance.append(
                    f' <span class="chip" title="{esc(title)}">{esc(label)}</span>'
                )
        rows.append(f"<tr><td>{name}</td>"
                    f"<td><span class=\"{klass}\">{esc(value)}</span>"
                    f"{''.join(field_provenance)}</td></tr>")
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


# ---------------- typology photographs ----------------


def load_images():
    """The image manifest: which typologies have a photograph we may show."""
    if not IMAGES_FILE.exists():
        return {}, {}, None
    doc = yaml.safe_load(IMAGES_FILE.read_text()) or {}
    return (doc.get("images") or {}, doc.get("unresolved") or {},
            doc.get("release"))


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": IMAGE_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def stage_images(out, images, release, offline=False):
    """Publish a verified copy of every manifest image under the site root.

    Copies come from the release named in the manifest, through a local cache,
    and each is checked against its recorded sha256 before it is published. An
    image that cannot be obtained, or that does not match its digest, is not
    published and not rendered: a wrong file is worse than a missing one.

    Returns the record paths whose image is on disk and verified.
    """
    if not images:
        return set()
    dest_dir = out / IMAGE_SUBDIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    IMAGE_CACHE.mkdir(exist_ok=True)
    staged, missing = set(), []
    for path, entry in images.items():
        want = entry["sha256"]
        cached = IMAGE_CACHE / entry["file"]
        blob = None
        if cached.exists():
            got = cached.read_bytes()
            if hashlib.sha256(got).hexdigest() == want:
                blob = got
        if blob is None and not offline:
            url = f"{REPO_URL}/releases/download/{release}/{entry['file']}"
            try:
                got = fetch(url)
            except (urllib.error.URLError, OSError) as exc:
                missing.append(f"{entry['file']}: {exc}")
                continue
            if hashlib.sha256(got).hexdigest() != want:
                missing.append(f"{entry['file']}: sha256 does not match the "
                               "manifest; the release asset has been replaced")
                continue
            cached.write_bytes(got)
            blob = got
        if blob is None:
            missing.append(f"{entry['file']}: not cached and --offline was given")
            continue
        (dest_dir / entry["file"]).write_bytes(blob)
        staged.add(path)
    if missing and not offline:
        raise SystemExit("image staging failed:\n  " + "\n  ".join(missing)
                         + f"\nexpected assets on release {release!r}; stage "
                         "them with scripts/fetch_images.py and upload them")
    for m in missing:
        print(f"  ! image omitted -- {m}")
    return staged


def image_figure(path, entry, depth):
    """The photograph and the attribution it may not be shown without."""
    rel = "../" * depth
    src = f"{rel}{IMAGE_SUBDIR}/{entry['file']}"
    credit = (f"<a href=\"{esc(entry['description_page'])}\">"
              f"{esc(entry['credit'])}</a>")
    licence = (f"<a href=\"{esc(entry['licence_url'])}\">"
               f"{esc(entry['licence'])}</a>")
    caption = esc(entry.get("caption") or "")
    return (f"<figure class=\"typoshot\">"
            f"<img src=\"{esc(src)}\" alt=\"{caption}\" loading=\"lazy\" "
            f"decoding=\"async\" width=\"{entry['width']}\" "
            f"height=\"{entry['height']}\">"
            f"<figcaption>{caption}"
            f"<span class=\"credit\">{credit} · {licence}</span>"
            f"</figcaption></figure>")


def photo_invite(path, rec, blocked):
    """Ask for a photograph where a typology has none.

    A typology with no picture is the weakest page on the site, and the fix
    is not one a maintainer can do alone -- it needs someone who has stood in
    the place. So say what is missing and what would let us publish it. The
    licence condition leads rather than hides in the form, because a
    photograph offered without one cannot be used, and finding that out after
    someone has gone to the trouble is the worse outcome.
    """
    name = rec.get("name") or path.rsplit("/", 1)[-1]
    url = (f"{REPO_URL}/issues/new?template=typology-photo.yml"
           f"&title={quote('[photo] ' + str(name), safe='')}"
           f"&typology={quote(path, safe='')}")
    blocked_note = ""
    if blocked:
        # a manifest-authored number; anything else points at the manifest
        # rather than fabricating an issue link or failing the whole build
        issue = blocked.get("tracked_by")
        where = "db/images.yml"
        if isinstance(issue, int):
            where = (f"<a href=\"{REPO_URL}/issues/{issue}\">"
                     f"issue #{issue}</a>")
        blocked_note = (
            f"<p class=\"crumbs\">A photograph is recorded for this typology "
            f"but cannot be published: nobody has been able to establish who "
            f"holds it or on what terms. That question is open in "
            f"{where}.</p>")
    return (
        "<div class=\"nophoto\">"
        f"<p><b>No photograph yet.</b> This kind of neighbourhood is easier "
        f"to recognise than to describe, and a picture would tell a reader "
        f"more about “{esc(name)}” than the parameters below can.</p>"
        f"{blocked_note}"
        f"<a class=\"photobtn\" href=\"{esc(url)}\">Offer a photograph</a>"
        "<p class=\"crumbs\">We can only publish one if it names its "
        "photographer and its licence — public domain, CC0, or a Creative "
        "Commons licence. A photograph with no stated terms cannot be shown, "
        "however well it fits.</p>"
        "</div>")


def _state_badge(state):
    label = PROVENANCE_STATE_LABEL.get(state, state.replace("_", " ").title())
    return (
        f"<span class=\"pstate pstate-{esc(state.replace('_', '-'))}\">"
        f"{esc(label)}</span>"
    )


def signoff_issue_url(path, sidecar, policy):
    """Prefill the authenticated GitHub issue used as a verifier decision."""
    title = f"[provenance sign-off] {path.rsplit('/', 1)[-1]}"
    fields = {
        "title": title,
        "record": path,
        "review_type": (
            "Composition"
            if sidecar.get("review_type") == "composition"
            else "Evidence"
        ),
        "evidence_revision": sidecar["assessment"]["evidence_revision"],
        "policy_revision": policy["revision"],
        "decision": "Verified",
    }
    return SIGNOFF_TEMPLATE_URL + "&" + "&".join(
        f"{key}={quote(str(value), safe='')}" for key, value in fields.items()
    )


def review_guide_url(path, review_type, state, rel):
    """Keep entry context while sending a reader to the review procedure."""
    fields = {
        "entry": path,
        "review_type": review_type,
        "state": state,
    }
    return f"{rel}review.html?" + "&".join(
        f"{key}={quote(str(value), safe='')}" for key, value in fields.items()
    )


def provenance_blocks(path, sidecar, policy, rel, sources, records):
    """Return the main evidence section and compact review rail card."""
    state = provenance_state(sidecar, policy)
    review_type = (
        sidecar.get("review_type", "evidence")
        if sidecar
        else "composition" if path.startswith("archetypes/") else "evidence"
    )
    is_composition = review_type == "composition"
    review_label = "Composition review" if is_composition else "Evidence review"
    evidence_heading = (
        "Composition provenance" if is_composition else "Provenance evidence"
    )
    guide_url = review_guide_url(path, review_type, state, rel)
    if not sidecar:
        main = (
            f"<div class=\"provhead\"><h3>{review_label}</h3>"
            + _state_badge(state)
            + "</div><p class=\"provmeta\">No structured assessment has been "
            + ("prepared for this composition yet. Component links remain "
               "visible above.</p>" if is_composition else
               "prepared for this record yet. Its model-ready source field "
               "remains visible above.</p>")
        )
        rail = (
            f"<div class=\"side\"><h4>{review_label}</h4>"
            + _state_badge(state)
            + f"<p class=\"signoff-help\">This {'composite' if is_composition else 'record'} has not entered the "
            "provenance review queue.</p>"
            f"<a class=\"reviewguide\" href=\"{esc(guide_url)}\">"
            "Review procedure</a>"
            "<p class=\"signoff-help\">Only a GitHub account in the verifier "
            "registry can create a sign-off that CI accepts.</p></div>"
        )
        return main, rail

    assessment = sidecar.get("assessment", {})
    assessor = assessment.get("assessor", {})
    method = assessment.get("method")
    meta = []
    if method:
        meta.append(METHOD_LABEL.get(method, method.replace("_", " ").title()))
    if assessor:
        meta.append(
            f"assessed by {assessor.get('name', assessor.get('kind', 'unknown'))}"
        )
    if assessment.get("assessed_at"):
        meta.append(str(assessment["assessed_at"])[:10])
    main = [
        f"<div class=\"provhead\"><h3>{evidence_heading}</h3>"
        + _state_badge(state)
        + "</div>",
        f"<p class=\"provmeta\">{esc(' · '.join(meta))}. Agent assessment is "
        f"{'composition review preparation' if is_composition else 'evidence preparation'}, "
        "not human verification.</p>",
    ]

    evidence = assessment.get("evidence", [])
    if evidence:
        main.append("<div class=\"evidence-list\">")
        for item in evidence:
            role = item.get("role", "related")
            role_label = PROVENANCE_ROLE_LABEL.get(
                role, role.replace("_", " ").title()
            )
            if item.get("source"):
                key = item["source"]
                src = sources.get(key, {})
                title = src.get("title") or src.get("author") or key
                subject = (
                    f"<a href=\"{rel}source/{esc(key)}.html\">{esc(title)}</a>"
                )
            elif item.get("record"):
                ref = item["record"]
                target = records.get(ref, {})
                title = target.get("name") or ref.rsplit("/", 1)[-1]
                subject = f"<a href=\"{rel}{esc(ref)}.html\">{esc(title)}</a>"
            else:
                subject = esc(item.get("id", "Evidence"))
            main.append(
                "<div class=\"evidence-item\"><h4>"
                + subject
                + f"<span class=\"erole\">{esc(role_label)}</span></h4>"
            )
            if item.get("parameter_paths"):
                paths = ", ".join(
                    f"<code>{esc(path)}</code>"
                    for path in item["parameter_paths"]
                )
                main.append(
                    f"<p class=\"locator\"><b>Applies to:</b> {paths}</p>"
                )
            for locator in item.get("locators", []):
                label = locator.get("label") or locator.get("kind", "locator")
                if locator.get("page"):
                    label = f"{label}, p. {locator['page']}"
                linked = esc(label)
                if locator.get("url"):
                    linked = f"<a href=\"{esc(locator['url'])}\">{linked}</a>"
                main.append(f"<p class=\"locator\"><b>Where:</b> {linked}</p>")
                if locator.get("note"):
                    main.append(
                        f"<p class=\"locator\">{esc(locator['note'])}</p>"
                    )
            if item.get("note"):
                main.append(f"<p class=\"locator\">{esc(item['note'])}</p>")
            main.append("</div>")
        main.append("</div>")

    findings = assessment.get("findings", {})
    if findings:
        rows = []
        for scope, finding in findings.items():
            conclusion = finding.get("conclusion", "unknown")
            klass = (
                "finding-supported"
                if conclusion in {"supported", "not_applicable"}
                else "finding-blocked"
            )
            detail = esc(conclusion.replace("_", " "))
            if finding.get("note"):
                detail += f"<br><span class=\"crumbs\">{esc(finding['note'])}</span>"
            links = []
            for url in finding.get("issue_urls", []):
                issue = url.rstrip("/").rsplit("/", 1)[-1]
                links.append(f"<a href=\"{esc(url)}\">issue #{esc(issue)}</a>")
            if links:
                detail += "<br>" + " · ".join(links)
            scope_label = (
                COMPOSITION_FINDING_LABEL.get(scope, scope.replace("_", " ").title())
                if is_composition
                else scope.replace("_", " ").title()
            )
            rows.append(
                f"<tr><th>{esc(scope_label)}</th>"
                f"<td class=\"{klass}\">{detail}</td></tr>"
            )
        main.append("<h3>Assessment findings</h3><table class=\"kv\">"
                    + "".join(rows) + "</table>")

    derivation = assessment.get("derivation")
    if derivation:
        bits = [derivation.get("kind")]
        bits.extend(
            derivation[key]
            for key in ("expression", "description")
            if derivation.get(key)
        )
        main.append(
            "<h3>Derivation</h3><p>" + esc(" · ".join(bits)) + "</p>"
        )
    if assessment.get("scientific_note"):
        main.append(
            f"<h3>Scientific note</h3><p>{esc(assessment['scientific_note'])}</p>"
        )

    rail = [
        f"<div class=\"side\"><h4>{review_label}</h4>",
        _state_badge(state),
    ]
    attestations = sidecar.get("verification", {}).get("attestations", [])
    if attestations:
        states = attestation_states(sidecar, policy)
        rail.append("<h4>Verifier decisions</h4>")
        guide_shown = False
        for item in attestations:
            verifier = item.get("verifier", "unknown")
            event = item.get("event", {})
            display_state = states.get(_event_key(item), "stale")
            state_note = (
                f" <span class=\"{esc(display_state)}\">"
                f"{esc(display_state)}</span>"
                if display_state != "current"
                else ""
            )
            info = ""
            if state == "verified" and display_state == "current" and not guide_shown:
                info = (
                    f" <a class=\"reviewinfo\" href=\"{esc(guide_url)}\" "
                    "aria-label=\"Review procedure\" title=\"Review procedure\">"
                    "<svg viewBox=\"0 0 16 16\" aria-hidden=\"true\" "
                    "focusable=\"false\"><circle cx=\"8\" cy=\"8\" r=\"6.5\"/>"
                    "<path d=\"M8 7v4 M8 4.5h.01\"/></svg></a>"
                )
                guide_shown = True
            rail.append(
                "<div class=\"attestation\">"
                f"<a href=\"https://github.com/{esc(verifier)}\">@{esc(verifier)}</a> "
                f"<span class=\"decision\">{esc(item.get('decision', '').replace('_', ' '))}</span>"
                f"{state_note}{info}<br><a href=\"{esc(event.get('url', '#'))}\">"
                f"{esc(str(item.get('signed_at', ''))[:10] or 'GitHub event')}</a>"
                "</div>"
            )

    if state == "awaiting_signoff" and policy:
        issue_url = signoff_issue_url(path, sidecar, policy)
        rail.append(
            f"<a class=\"signoff\" href=\"{esc(issue_url)}\" target=\"_blank\" "
            "rel=\"noopener\">"
            f"Sign off {'composition' if is_composition else 'evidence'} on GitHub</a>"
            "<p class=\"signoff-help\">Raises a prefilled sign-off issue. "
            "CI accepts it only when the issue author is in the verifier registry "
            "and the evidence revision is still current.</p>"
        )
    if state != "verified":
        rail.append(
            f"<a class=\"reviewguide\" href=\"{esc(guide_url)}\">"
            "Review procedure</a>"
        )
    rail.append("</div>")
    return "".join(main), "".join(rail)


REVIEW_GUIDE_JS = r"""<script>
(function () {
  var params = new URLSearchParams(location.search);
  var entry = params.get('entry') || '';
  if (!/^(records|archetypes)\/[a-z0-9][a-z0-9/_-]*$/.test(entry)) return;
  var type = params.get('review_type') === 'composition'
    ? 'Composition review' : 'Evidence review';
  var state = (params.get('state') || '').replaceAll('_', ' ');
  var context = document.getElementById('reviewcontext');
  document.getElementById('reviewentry').textContent = entry;
  document.getElementById('reviewtype').textContent = type;
  if (state) document.getElementById('reviewstate').textContent = ' · ' + state;
  var back = document.getElementById('reviewreturn');
  back.href = entry.split('/').map(encodeURIComponent).join('/') + '.html';
  var request = document.getElementById('verifierrequest');
  var requestUrl = new URL(request.dataset.base);
  requestUrl.searchParams.set('title', '[verifier request] ' + entry);
  requestUrl.searchParams.set('entry', entry);
  requestUrl.searchParams.set('review_type', type.replace(' review', ''));
  request.href = requestUrl.toString();
  context.hidden = false;
})();
</script>"""


def review_guide_page(policy):
    """Explain the review path and the authenticated verifier boundary."""
    verifiers = sorted(
        policy.get("verifiers", {}).values(),
        key=lambda item: item["github_handle"].casefold(),
    )
    verifier_items = "".join(
        f"<li><a href=\"https://github.com/{esc(item['github_handle'])}\">"
        f"@{esc(item['github_handle'])}</a></li>"
        for item in verifiers
    ) or "<li>No verifiers are currently registered.</li>"
    body = f"""
<p class="crumbs"><a href="index.html">Browse database</a> / Review procedure</p>
<section class="reviewhero">
  <h2>Review provenance with a registered verifier identity</h2>
  <p>Read the original evidence and assess the stored claim carefully. A review
  becomes part of the database’s verified state only when it is submitted by a
  GitHub account in the reviewed verifier registry and passes CI.</p>
  <p class="reviewcontext" id="reviewcontext" hidden>
    <strong id="reviewtype">Evidence review</strong>
    <code id="reviewentry"></code><span id="reviewstate"></span>
    <a id="reviewreturn" href="index.html">Return to this entry</a>
  </p>
</section>
<div class="reviewlayout">
  <main>
    <ol class="reviewsteps">
      <li><div><h3>Check that an assessment exists</h3>
        <p>An <strong>Unaudited</strong> entry is not ready for sign-off. It first
        needs a structured evidence or composition assessment. Contact the team
        to add it to the review queue; report any concrete data problem separately.</p>
      </div></li>
      <li><div><h3>Read the original sources</h3>
        <p>Verify the exact values, parameter source, method, place,
        representativeness, any declared urban setting, target and identity.
        For a composite, review component
        selection, completeness and slot or season mapping instead of re-reviewing
        each component’s values.</p>
      </div></li>
      <li><div><h3>Choose an evidence-based decision</h3>
        <p>Use <strong>Verified</strong> only when the current assessment is fully
        supported. Otherwise request changes, mark the question unresolved, or
        require curation. Never infer a citation or correct a value by plausibility.</p>
      </div></li>
      <li><div><h3>Submit from your own GitHub account</h3>
        <p>When the entry reaches <strong>Awaiting sign-off</strong>, use its GitHub
        sign-off button. CI authenticates your immutable GitHub user ID and checks
        the entry, evidence and policy revisions before incorporating the decision.
        An accepted issue closes automatically and remains linked as the durable
        review record. A rejected issue stays open and receives a CI comment with
        the failure reason; unregistered reviewers are also directed to the verifier
        qualification request.</p>
      </div></li>
    </ol>
  </main>
  <aside class="reviewpolicy" aria-labelledby="verifier-heading">
    <h3 id="verifier-heading">Who can sign off</h3>
    <p class="reviewgate"><strong>Registered verifier required.</strong> A sign-off
    from any other GitHub account is rejected by CI and is not incorporated into
    the database’s verified state or displayed as a verifier decision.</p>
    <p>Current registered verifiers:</p>
    <ul>{verifier_items}</ul>
    <a href="{REPO_URL}/blob/main/.github/provenance-verifiers.yml">View the verifier registry</a>
    <a class="signoff" id="verifierrequest" data-base="{esc(VERIFIER_REQUEST_URL)}"
       href="{esc(VERIFIER_REQUEST_URL)}">Contact the team to become a verifier</a>
    <p>Opening a request does not grant verifier status. The SUEWS database team
    reviews the request and adds approved GitHub identities through a reviewed
    policy change.</p>
  </aside>
</div>"""
    return page("Provenance review procedure", body, script=REVIEW_GUIDE_JS)


# ---------------- per-entry pages ----------------


def record_page(
    path, rec, records, sources, used_by, cluster, image=None,
    sidecars=None, policy=None, blocked=None, invite=False
):
    depth = path.count("/")
    rel = "../" * depth
    kind = "record" if path.startswith("records/") else "typology"
    kind_label = "evidence record" if kind == "record" else "composite"
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
            f"<div class=\"hv {acc}\"><span class=\"k\"><span class=\"sw\">"
            f"</span>{esc(k)}</span><span class=\"v\">{esc(v)}</span></div>"
            for k, v in headline_pairs)
        if blocks:
            headline = f"<div class=\"heroval\">{blocks}</div>"

    # clickable facet chips under the title
    chips = []
    if kind == "typology":
        chips.append(chip_link(f"{rel}index.html#typology={esc(fam)}",
                               TYP_LABEL.get(fam, fam)))
    else:
        chips.append(chip_link(f"{rel}index.html#family={esc(fam)}", fam))
    if surface:
        chips.append(chip_link(f"{rel}index.html#surface={esc(surface)}", surface))
    if rec.get("place"):
        chips.append(chip_link(f"{rel}place/{esc(rec['place'])}.html", rec["place"]))
    if rec.get("representativeness"):
        chips.append(chip_link(
            f"{rel}index.html#rep={esc(rec['representativeness'])}",
            rec["representativeness"]))
    if rec.get("applicable_scale"):
        scale = rec["applicable_scale"]
        chips.append(chip_link(
            f"{rel}index.html#scale={esc(scale)}",
            APPLICABLE_SCALE_LABEL.get(scale, scale)))
    if rec.get("urban_setting"):
        setting = rec["urban_setting"]
        chips.append(chip_link(
            f"{rel}index.html#setting={esc(setting)}",
            URBAN_SETTING_LABEL.get(setting, setting)))
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

    if src_key == "unreferenced":
        row("Source", "<span class=\"badge-unref\">unreferenced</span> — no "
            "citation was recorded in the legacy database; treat with care")
    elif src:
        doi = src.get("doi")
        doi_html = (f" · <a href=\"https://doi.org/{esc(doi)}\">doi:{esc(doi)}</a>"
                    if doi else "")
        row("Source", f"<a href=\"{rel}source/{esc(src_key)}.html\">"
            f"{esc(src.get('author', src_key))} ({esc(src.get('year', '?'))})</a>"
            f"{doi_html}")
    if rec.get("place"):
        row("Place", f"<a href=\"{rel}place/{esc(rec['place'])}.html\">"
            f"{esc(rec['place'])}</a>")
    row(
        "Scope",
        esc(rec["representativeness"]) if rec.get("representativeness") else "",
    )
    if rec.get("applicable_scale"):
        scale = rec["applicable_scale"]
        row("Applicable scale", esc(APPLICABLE_SCALE_LABEL.get(scale, scale)))
    if rec.get("urban_setting"):
        setting = rec["urban_setting"]
        row("Urban setting", esc(URBAN_SETTING_LABEL.get(setting, setting)))
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
    rail = []
    provenance_main = ""
    provenance_main, review_rail = provenance_blocks(
        path, (sidecars or {}).get(path), policy, rel, sources, records
    )
    rail.append(review_rail)
    metadata_label = "Record metadata" if kind == "record" else "Composite metadata"
    rail.append(f"<div class=\"side\"><h4>{metadata_label}</h4>"
                + "".join(prov_rows) + "</div>")

    unref_badge = (" <span class=\"badge-unref\">unreferenced</span>"
                   if src_key == "unreferenced" else "")
    body = [crumbs,
            f"<h2>{esc(title_text)}{qualifier}"
            + (f"<span class=\"stag {acc}\">{esc(surface)}</span>" if surface else "")
            + f" <span class=\"chip\">{kind_label}</span>{unref_badge}</h2>",
            headline, chip_row]
    main = []

    # a typology is a visual idea -- "Mixed-City Ideal" and "Residential
    # Functionalism" are not self-explanatory from their names, and a reader
    # choosing between them is choosing between kinds of place they would
    # recognise on sight. The photograph leads, because it is the fastest
    # thing on the page to read. Only the record page carries one: the browse
    # index is a facet view over eleven hundred entries, and pulling
    # photographs into it would cost every reader page weight for a handful
    # of thumbnails they did not ask for.
    if image:
        main.append(image_figure(path, image, depth))
    elif invite:
        main.append(photo_invite(path, rec, blocked))

    uses = rec.get("uses")
    if uses:
        if kind == "typology":
            main.append(
                "<h3>Composition</h3>"
                "<p class=\"provmeta\">This composite is assembled from the "
                "entries below. Each evidence record keeps its own scientific "
                "review; a separate composition review assesses why these "
                "components were selected and how they are mapped.</p>"
            )
        else:
            main.append("<h3>Uses</h3>")
        main.append("<table class=\"kv\">")

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
                        if ref.startswith("records/"):
                            extra += " " + _state_badge(
                                provenance_state((sidecars or {}).get(ref), policy)
                            )
                        else:
                            extra += " <span class=\"chip\">composite</span>"
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
            if rec.get("parameter_provenance"):
                main.append(
                    "<p class=\"provmeta\">Labelled fields override the record-level "
                    "citation or scope; unlabelled fields use the record metadata.</p>"
                )
            main.append(
                params_table(
                    params,
                    rec.get("target"),
                    rec=rec,
                    sources=sources,
                    rel=rel,
                )
            )
        if context:
            main.append("<h3>Context</h3>"
                        "<p class=\"crumbs\">Conditions the set was derived "
                        "under; not model inputs themselves.</p>")
            main.append(params_table({"context": context}, None, linked=False))

    if provenance_main:
        main.append(provenance_main)

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
    new_name = "NEW-RECORD.yml" if kind == "record" else "NEW-COMPOSITE.yml"
    dup_url = f"{REPO_URL}/new/main?filename={quote(dup_dir + '/' + new_name, safe='')}"
    try:
        raw = (ROOT / "db" / (path + ".yml")).read_text()
        prefilled = dup_url + "&value=" + quote(raw, safe="")
        if len(prefilled) <= 7500:
            dup_url = prefilled
    except OSError:
        pass
    # reporting a problem should cost one click: the record's identity travels
    # with the report, and the link is pinned to the commit the reader saw
    seen = f"{REPO_URL}/blob/{BUILD_REF}/db/{path}.yml"
    report_kind = "record" if kind == "record" else "composite"
    report_url = (
        f"{REPO_URL}/issues/new?template=record-issue.yml"
        f"&title={quote('[' + report_kind + '] ' + str(rec.get('name') or path), safe='')}"
        f"&record={quote(path, safe='')}"
        f"&seen_at={quote(seen, safe='')}")
    duplicate_label = (
        "Duplicate as a new record"
        if kind == "record"
        else "Duplicate as a new composite"
    )
    rail.append(
        "<div class=\"actions\">"
        f"<a href=\"{REPO_URL}/blob/main/db/{esc(path)}.yml\">View source</a>"
        f"<a href=\"{REPO_URL}/edit/main/db/{esc(path)}.yml\">Propose a change</a>"
        f"<a href=\"{esc(dup_url)}\">{duplicate_label}</a>"
        f"<a class=\"report\" href=\"{esc(report_url)}\">Report an issue</a>"
        "</div>"
    )
    body.append(f"<div class=\"cols\"><div>{''.join(main)}</div>"
                f"<div>{''.join(rail)}</div></div>")
    return page(title_text, "\n".join(body), depth, COPY_JS,
                report_url=report_url)


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


def provenance_source_roles(sidecars):
    """Map each citation key to the roles it plays in reviewed records."""
    roles = defaultdict(lambda: defaultdict(set))
    for record_path, sidecar in sidecars.items():
        for item in sidecar.get("assessment", {}).get("evidence", []):
            if item.get("source"):
                roles[item["source"]][item.get("role", "related")].add(record_path)
    return roles


def build_provenance_index(sidecars, policy):
    """Generated lookup from records to their audit and sign-off issues."""
    index = {}
    for record_path, sidecar in sorted(sidecars.items()):
        assessment = sidecar.get("assessment", {})
        audit_urls = set()
        run_url = assessment.get("assessor", {}).get("run_url")
        if run_url and "/issues/" in run_url:
            audit_urls.add(run_url)
        for finding in assessment.get("findings", {}).values():
            audit_urls.update(finding.get("issue_urls", []))
        audit_issues = []
        for url in sorted(audit_urls):
            issue = url.rstrip("/").rsplit("/", 1)[-1]
            if issue.isdigit():
                audit_issues.append({"issue": int(issue), "url": url})
        signoffs = []
        for item in sidecar.get("verification", {}).get("attestations", []):
            event = item.get("event", {})
            if event.get("kind") != "issue":
                continue
            signoffs.append({
                "issue": event.get("id"),
                "url": event.get("url"),
                "verifier": item.get("verifier"),
                "verifier_id": item.get("verifier_id"),
                "decision": item.get("decision"),
                "signed_at": item.get("signed_at"),
                "evidence_revision": item.get("evidence_revision"),
                "policy_revision": item.get("verifier_policy_revision"),
            })
        index[record_path] = {
            "review_type": sidecar.get("review_type", "evidence"),
            "state": provenance_state(sidecar, policy),
            "audit_issues": audit_issues,
            "signoff_issues": signoffs,
        }
    return index


def place_page(slug, info, paths, records):
    body = [f"<div class=\"crumbs\"><a href=\"../index.html\">browse</a> · place</div>",
            f"<h2>{esc(info.get('name', slug))}</h2>",
            f"<p class=\"crumbs\">{len(paths)} entries at this place · "
            f"<a href=\"../index.html#place={esc(slug)}\">filter the browser "
            "to this place</a></p>",
            grouped_list(paths, records, 1)]
    return page(info.get("name", slug), "\n".join(body), 1)


def source_page(key, src, paths, records, role_paths=None):
    title = src.get("title") or src.get("note") or key
    doi = src.get("doi")
    source_url = src.get("url")
    cite = (f"{esc(src.get('author', ''))} ({esc(src.get('year', '?'))}). "
            f"{esc(title)}. <i>{esc(src.get('journal') or '')}</i>")
    if doi:
        cite += f" · <a href=\"https://doi.org/{esc(doi)}\">doi:{esc(doi)}</a>"
    if source_url:
        cite += f" · <a href=\"{esc(source_url)}\">source page</a>"
    body = [f"<div class=\"crumbs\"><a href=\"../index.html\">browse</a> · source</div>",
            f"<h2><code>{esc(key)}</code></h2>",
            f"<p>{cite}</p>",
            f"<p class=\"crumbs\">{len(paths)} entries cite this source · "
            f"<a href=\"../index.html#source={esc(key)}\">filter the browser "
            "to this source</a></p>"]
    if role_paths:
        body.append(
            "<h3>Role in reviewed provenance</h3>"
            "<p class=\"crumbs\">Observation inputs, exact-value publications "
            "and later compilations are listed separately.</p>"
        )
        for role, reviewed_paths in sorted(
            role_paths.items(),
            key=lambda item: PROVENANCE_ROLE_LABEL.get(item[0], item[0]),
        ):
            label = PROVENANCE_ROLE_LABEL.get(
                role, role.replace("_", " ").title()
            )
            body.append(
                f"<h3>{esc(label)} <span class=\"chip\">"
                f"{len(reviewed_paths)}</span></h3><ul class=\"linked\">"
                + "".join(
                    entry_link(path, records, 1)
                    for path in sorted(reviewed_paths)
                )
                + "</ul>"
            )
    body.append("<h3>Records citing this source</h3>")
    body.append(grouped_list(paths, records, 1))
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
                'city', 'rep', 'scale', 'setting', 'source', 'method', 'verification',
                'role'];
// 'place' is a hidden exact-match key: not rendered as a facet group, but
// honoured from the hash so record-page sibling links and old bookmarks
// filter exactly rather than through the free-text search
const KEYS = FACETS.concat(['place']);
let DATA = [];
let wasOverview = true;
const state = { q: '', all: false };
const ffind = {};
for (const f of KEYS) state[f] = null;

function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  state.q = h.get('q') || '';
  state.all = h.get('all') === '1';
  for (const f of KEYS) state[f] = h.get(f);
}
function writeHash() {
  const h = new URLSearchParams();
  if (state.q) h.set('q', state.q);
  if (state.all) h.set('all', '1');
  for (const f of KEYS) if (state[f]) h.set(f, state[f]);
  history.replaceState(null, '', h.toString() ? '#' + h.toString() : location.pathname);
}
function facetValues(e, facet) {
  if (facet === 'source' && Array.isArray(e.sources)) return e.sources;
  const value = e[facet];
  if (Array.isArray(value)) return value;
  return value ? [value] : [];
}
function matches(e) {
  for (const f of KEYS) {
    if (state[f] && !facetValues(e, f).includes(state[f])) return false;
  }
  if (state.q) {
    // every word must match somewhere: "london grass" finds London grass
    const toks = state.q.toLowerCase().split(/\\s+/).filter(Boolean);
    if (!toks.every(t => e.text.includes(t))) return false;
  }
  return true;
}
function displayVal(facet, value) {
  if (facet === 'surface') return LC_LABEL[value] || value;
  if (facet === 'typology') return TYP_LABEL[value] || value;
  if (facet === 'method') return METHOD_LABEL[value] || value;
  if (facet === 'scale') return SCALE_LABEL[value] || value;
  if (facet === 'setting') return SETTING_LABEL[value] || value;
  if (facet === 'verification') return STATE_LABEL[value] || value;
  if (facet === 'role') return ROLE_LABEL[value] || value;
  return value;
}
function itemHTML(facet, value, count, on, swatch) {
  const sw = swatch ? `<span class="sw ${ACC[value] || 'acc-none'}"></span>` : '';
  const warn = facet === 'source' && value === 'unreferenced' ? ' warnv' : '';
  return `<button class="fitem${on ? ' on' : ''}${warn}" data-facet="${facet}" ` +
         `data-value="${value}" aria-pressed="${on ? 'true' : 'false'}">${sw}` +
         `<span class="fv">${displayVal(facet, value)}</span>` +
         `<span class="n">${count}</span></button>`;
}
function emptyState() {
  return !state.q && !state.all && KEYS.every(f => !state[f]);
}
function anyActive() {
  return state.q || state.all || KEYS.some(f => state[f]);
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
    for (const e of sub) for (const value of facetValues(e, f)) {
      counts[value] = (counts[value] || 0) + 1;
    }
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
    // alphabetical by display label; the active value pinned first; long
    // groups scroll and can be narrowed by their filter box
    let entries = Object.entries(counts)
      .sort((a, b) => displayVal(f, a[0]).localeCompare(displayVal(f, b[0])));
    const needle = (ffind[f] || '').toLowerCase();
    if (needle) {
      entries = entries.filter(([v]) =>
        displayVal(f, v).toLowerCase().includes(needle));
    }
    if (state[f]) {
      entries = entries.filter(([v]) => v !== state[f]);
      entries.unshift([state[f],
        counts[state[f]] !== undefined ? counts[state[f]] : 0]);
    }
    el.innerHTML = entries.map(([v, n]) =>
      itemHTML(f, v, n, state[f] === v)).join('');
  }
  // a group holding an active filter is always open
  document.querySelectorAll('details.fgroup').forEach(d => {
    if (state[d.dataset.facet]) d.open = true;
  });
  // arrival (the two doors) and the results view are the page's two states
  const showOverview = emptyState();
  document.getElementById('arrival').classList.toggle('hidden', !showOverview);
  document.getElementById('browse').classList.toggle('hidden', showOverview);
  // every active filter gets a visible, dismissible chip — including the
  // hidden place key record-page links arrive with
  const af = document.getElementById('afilters');
  if (af) {
    af.innerHTML = showOverview ? '' : KEYS.filter(f => state[f]).map(f =>
      `<button class="chip on afx" data-key="${f}" ` +
      `aria-label="remove ${FACET_TITLE[f]} filter">` +
      `${FACET_TITLE[f]}: ${displayVal(f, state[f])} ×</button>`).join('');
  }
  const nres = document.getElementById('nres');
  if (showOverview) {
    nres.textContent = '';
  } else {
    nres.innerHTML = hits.length + ' of ' + DATA.length + ' entries' +
      (anyActive() ? ' · <a href="#" class="doclear">clear filters ×</a>' : '');
  }
  let out = '';
  if (!showOverview) {
    if (hits.length === 0) {
      out = '<div class="crumbs">No matches — ' +
            '<a href="#" class="doclear">clear the filters</a> or try the ' +
            '<a href="map.html">map</a>.</div>';
    } else {
      out = hits.slice(0, 200).map(e => {
        const acc = ACC[e.surface] || 'acc-none';
        const src = e.source === 'unreferenced'
          ? '<span class="badge-unref">unreferenced</span>' : e.source;
        const meta = [e.family, TYP_LABEL[e.typology],
                      e.city || e.country, e.rep, SCALE_LABEL[e.scale],
                      SETTING_LABEL[e.setting],
                      STATE_LABEL[e.verification], METHOD_LABEL[e.method], src]
          .filter(Boolean).join(' · ');
        const kindTag = e.kind === 'typology'
          ? ' <span class="chip">composite</span>' : '';
        return `<div class="card2 ${acc}"><div class="t"><a href="${e.path}.html">` +
               `${e.name}</a>${kindTag}</div><div class="meta2">${meta}</div>` +
               `<div>${e.vals}</div></div>`;
      }).join('');
      if (hits.length > 200) {
        out += '<div class="crumbs">…narrow the filters to see the rest</div>';
      }
    }
  }
  const results = document.getElementById('results');
  results.innerHTML = out;
  // one authored moment: the results grid rises as it replaces the overview
  if (!showOverview && wasOverview) {
    results.classList.remove('anim');
    void results.offsetWidth;
    results.classList.add('anim');
  }
  wasOverview = showOverview;
}
function clearAll() {
  state.q = ''; state.all = false;
  for (const f of KEYS) state[f] = null;
  document.getElementById('q').value = '';
  pq.value = ''; ta.classList.add('hidden'); ta.innerHTML = '';
  writeHash(); render();
}
document.addEventListener('click', ev => {
  const c = ev.target.closest('a.doclear');
  if (c) { ev.preventDefault(); clearAll(); return; }
  const ds = ev.target.closest('a.dosearch');
  if (ds) { ev.preventDefault(); runSearch(); return; }
  const ax = ev.target.closest('button.afx');
  if (ax) { state[ax.dataset.key] = null; writeHash(); render(); return; }
  const b = ev.target.closest('button.fitem');
  if (!b) return;
  const f = b.dataset.facet, v = b.dataset.value;
  state[f] = state[f] === v ? null : v;
  writeHash(); render();
  // render() replaced the button; put keyboard focus back on its successor
  const nb = document.querySelector(
    `button.fitem[data-facet="${CSS.escape(f)}"][data-value="${CSS.escape(v)}"]`);
  if (nb) nb.focus();
});
document.addEventListener('input', ev => {
  const ff = ev.target.closest('input.ffind');
  if (!ff) return;
  ffind[ff.dataset.facet] = ff.value;
  render();
});
const pq = document.getElementById('pq');
const ta = document.getElementById('ta');
let FAMS = [];
function typeahead() {
  const raw = pq.value.trim();
  if (!raw) { ta.classList.add('hidden'); ta.innerHTML = ''; return; }
  ta.classList.remove('hidden');
  if (!DATA.length) {
    ta.innerHTML = '<a><span class="k">wait</span>the index is still loading…</a>';
    return;
  }
  const t = raw.toLowerCase();
  const toks = t.split(/\\s+/).filter(Boolean);
  const out = [];
  for (const f of FAMS) {
    if (out.length >= 4) break;
    if (f.toLowerCase().includes(t)) {
      const n = DATA.filter(e => e.family === f).length;
      out.push(`<a href="#family=${encodeURIComponent(f)}">` +
        `<span><span class="k">family</span>${f}</span>` +
        `<span class="n">${n} records</span></a>`);
    }
  }
  for (const e of DATA) {
    if (out.length >= 9) break;
    if (toks.every(x => e.text.includes(x))) {
      out.push(`<a href="${e.path}.html">` +
        `<span><span class="k">${e.kind}</span>${e.name}</span>` +
        `<span class="n">${e.city || e.country || e.source || ''}</span></a>`);
    }
  }
  out.push('<a href="#" class="dosearch"><span><span class="k">search</span>' +
    'every entry matching “' + raw + '”</span><span class="n">↵</span></a>');
  ta.innerHTML = out.join('');
}
function runSearch() {
  state.q = pq.value.trim();
  writeHash(); render();
  const target = document.getElementById('q');
  target.value = state.q; target.focus();
}
pq.addEventListener('input', typeahead);
pq.addEventListener('keydown', ev => {
  if (ev.key === 'Enter') { ev.preventDefault(); runSearch(); }
});
const input = document.getElementById('q');
input.addEventListener('input', () => { state.q = input.value; writeHash(); render(); });
window.addEventListener('hashchange', () => { readHash(); input.value = state.q; render(); });
fetch('data/index.json').then(r => {
  if (!r.ok) throw new Error(r.status);
  return r.json();
}).then(d => {
  DATA = d;
  FAMS = [...new Set(DATA.filter(e => e.kind === 'record' && e.family)
    .map(e => e.family))].sort();
  readHash(); input.value = state.q; render();
}).catch(() => {
  const msg = 'The search index failed to load — <a href="">reload the ' +
    'page</a>. Browsing record pages directly still works.';
  document.getElementById('nres').innerHTML = msg;
  const ae = document.getElementById('arrerr');
  ae.innerHTML = msg; ae.classList.remove('hidden');
});
</script>
"""


RELATION_SVG = """<svg viewBox="0 0 780 208" role="img" class="relfig"
 aria-label="How the database fits together">
<defs><marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7"
 markerHeight="7" orient="auto"><path d="M0 0L8 4L0 8z"
 class="rf-head"/></marker></defs>
<g font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<rect x="10" y="14" width="130" height="44" rx="9" class="rf-panel"/>
<text x="75" y="33" text-anchor="middle" class="rf-title"
 font-size="13">sources</text>
<text x="75" y="49" text-anchor="middle" class="rf-sub"
 font-size="11">citation per value</text>
<rect x="10" y="98" width="130" height="44" rx="9" class="rf-panel"/>
<text x="75" y="117" text-anchor="middle" class="rf-title"
 font-size="13">places</text>
<text x="75" y="133" text-anchor="middle" class="rf-sub"
 font-size="11">region · country · city</text>
<rect x="210" y="50" width="170" height="54" rx="10" class="rf-box rf-rec"/>
<text x="295" y="73" text-anchor="middle" class="rf-lead rf-rec-ink"
 font-size="14" font-weight="600">evidence records</text>
<text x="295" y="91" text-anchor="middle" class="rf-sub"
 font-size="11">one stable model parameter set</text>
<rect x="440" y="50" width="172" height="54" rx="10" class="rf-box rf-typ"/>
<text x="526" y="73" text-anchor="middle" class="rf-lead rf-typ-ink"
 font-size="14" font-weight="600">typologies</text>
<text x="526" y="91" text-anchor="middle" class="rf-sub"
 font-size="11">curated bundles of records</text>
<rect x="660" y="50" width="112" height="54" rx="10" class="rf-box rf-cfg"/>
<text x="716" y="73" text-anchor="middle" class="rf-lead rf-cfg-ink"
 font-size="13" font-weight="600">your SUEWS</text>
<text x="716" y="90" text-anchor="middle" class="rf-lead rf-cfg-ink"
 font-size="13" font-weight="600">YAML config</text>
<line x1="140" y1="42" x2="205" y2="66" class="rf-line"
 marker-end="url(#arr)"/>
<line x1="140" y1="116" x2="205" y2="90" class="rf-line"
 marker-end="url(#arr)"/>
<line x1="380" y1="77" x2="434" y2="77" class="rf-line"
 marker-end="url(#arr)"/>
<line x1="612" y1="77" x2="654" y2="77" class="rf-line"
 marker-end="url(#arr)"/>
<path d="M295 104 L295 158 L716 158 L716 110" class="rf-line rf-dash"
 marker-end="url(#arr)"/>
<rect x="380" y="148" width="252" height="20" rx="5" class="rf-knock"/>
<text x="506" y="162" text-anchor="middle" class="rf-sub"
 font-size="11">or paste a single record's fragment</text>
</g></svg>"""


def build_index_page(records, sources, places, by_place):
    n_rec = sum(1 for p in records if p.startswith("records/"))
    n_arch = sum(1 for p in records if p.startswith("archetypes/"))
    cited = {r.get("source") for r in records.values()
             if r.get("source") and r.get("source") != "unreferenced"}
    cited.update(
        override.get("source")
        for record in records.values()
        for override in record.get("parameter_provenance", {}).values()
        if override.get("source") and override.get("source") != "unreferenced"
    )
    n_unref = sum(1 for p, r in records.items()
                  if p.startswith("records/") and r.get("source") == "unreferenced")
    stats = (
        "<div class=\"statline\">"
        f"<span><b>{n_rec}</b>evidence records</span>"
        f"<span><b>{n_arch}</b>typologies</span>"
        f"<span><b>{len(cited)}</b>cited sources</span>"
        f"<span><b>{len(places)}</b>places</span>"
        "</div>"
    )
    hero = (
        "<div class=\"hero\"><h2>Find a parameter value you can cite</h2>"
        "<p>Curated values for "
        "<a href=\"https://github.com/UMEP-dev/SUEWS\">SUEWS</a>: one "
        "stable model parameter set per record, named by the model's own "
        "parameter paths. A mixed-source set labels its field-level citations. "
        "Every record exports as a fragment that pastes straight into "
        "a SUEWS YAML configuration, with its citation attached to every "
        f"value. {n_unref} legacy records with no recorded source are "
        "flagged <span class=\"badge-unref\">unreferenced</span>.</p></div>"
    )

    # ---- arrival: two doors ------------------------------------------------
    # A modeller arrives holding one of two things: a parameter name, or a
    # place. Each door owns half the fold; the facet rail belongs to the
    # results view, not to arrival.
    fam_counts = defaultdict(int)
    lc_counts = defaultdict(int)
    typ_counts = defaultdict(int)
    grid = defaultdict(int)
    region_c, country_c, city_c = defaultdict(int), defaultdict(int), defaultdict(int)
    for path, rec in records.items():
        s_ = surface_of(path, rec)
        if s_:
            lc_counts[s_] += 1
        if path.startswith("archetypes/"):
            typ_counts[family_of(path)] += 1
        else:
            fam = family_of(path)
            fam_counts[fam] += 1
            grid[(fam, s_ or "")] += 1
        reg, cty, city = geo_of(rec, places)
        if reg:
            region_c[reg] += 1
        if cty:
            country_c[cty] += 1
        if city:
            city_c[city] += 1

    def grow(href, label, n):
        return (f"<a class=\"grow\" href=\"{esc(href)}\"><span>{esc(label)}</span>"
                f"<span class=\"n\">{n}</span></a>")

    fam_list = "".join(grow(f"#family={f}", f, n)
                       for f, n in sorted(fam_counts.items()))

    def geo_col(title, counts, key, cap=None):
        rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if cap:
            rows = rows[:cap]
        items = "".join(grow(f"#{key}={v}", v, n) for v, n in rows)
        return (f"<div><p class=\"dlab\">{esc(title)} "
                f"<span class=\"n\">{len(counts)}</span></p>"
                f"<div class=\"gscroll\">{items}</div></div>")

    land_d = (ROOT / "scripts" / "data" / "ne110m_land.svgpath").read_text().strip()
    dots = []
    for slug in sorted(by_place):
        info = places.get(slug) or {}
        if "lat" not in info or "lon" not in info:
            continue
        n = len(by_place[slug])
        x, y = map_xy(info["lon"], info["lat"])
        r = 3.2 + min(6.0, n ** 0.5)
        dots.append(
            f"<a href=\"#place={esc(slug)}\"><circle class=\"dot\" "
            f"cx=\"{x:.1f}\" cy=\"{y:.1f}\" r=\"{r:.1f}\">"
            f"<title>{esc(info.get('name', slug))} — {n} entries</title>"
            "</circle></a>")
    n_mapped = len(dots)

    doors = f"""<div class="doors">
<section class="door">
<p class="dlab">I know the parameter</p>
<input id="pq" class="search" type="search" autocomplete="off"
 placeholder="albedo, emissivity, ohm, conductance&hellip;"
 aria-label="search by parameter, record or source">
<div id="ta" class="tahead hidden" role="listbox"></div>
<p class="dlab" style="margin-top:1rem">All {len(fam_counts)} parameter
families</p>
<div class="famgrid">{fam_list}</div>
</section>
<section class="door">
<p class="dlab">I know my site</p>
<div class="mapbox"><svg viewBox="0 0 {MAP_W:.0f} {MAP_H:.0f}" role="img"
 aria-label="{n_mapped} study places with coordinates">
<path d="{land_d}" class="land"/>{''.join(dots)}</svg></div>
<div class="geocols">
{geo_col("Region", region_c, "region")}
{geo_col("Country", country_c, "country", 40)}
{geo_col("City", city_c, "city", 40)}
</div>
</section>
</div>"""

    # typologies are neither a parameter nor a place — they are the bundle you
    # take wholesale, so they read as a shortcut rather than a third door
    typ_band = ("<div class=\"tband\"><p class=\"tlead\">"
                "&hellip;or take a ready-made typology</p>"
                + "".join(
                    f"<a class=\"otile acc-none\" href=\"#typology={t}\">"
                    f"<span class=\"sw\"></span>"
                    f"<b>{esc(TYP_LABEL.get(t, t))}</b><span>{n}</span></a>"
                    for t, n in sorted(typ_counts.items(),
                                       key=lambda kv: -kv[1]))
                + "</div>")

    strip = f"""<div class="invstrip">
<a class="icell" href="#kind=record"><b>{n_rec}</b>
<span>evidence records</span></a>
<a class="icell" href="#kind=typology"><b>{n_arch}</b>
<span>typologies</span></a>
<a class="icell" href="#all=1"><b>{len(cited)}</b>
<span>cited sources across {len(places)} places</span></a>
<a class="icell quiet" href="#source=unreferenced"><b>{n_unref}</b>
<span>records with no source on file</span></a>
</div>"""

    # the coverage matrix replaces the old land-cover and typology tiles: it
    # says what they said and also where the evidence is not
    cols = LC_ORDER + ["common", ""]
    col_label = dict(SURFACE_LABEL)
    col_label[""] = "Not surface-specific"
    mx = max(grid.values()) if grid else 1
    head = "".join(
        f"<th class=\"{SURFACE_ACC.get(c, 'acc-none')}\">"
        f"<span class=\"sw\"></span>{esc(col_label.get(c, c))}</th>"
        for c in cols)
    rows_html = []
    filled = 0
    for fam in sorted(fam_counts):
        tds = []
        for c in cols:
            n = grid.get((fam, c), 0)
            if not n:
                tds.append("<td class=\"mt\"></td>")
                continue
            filled += 1
            a = 0.10 + 0.62 * (n / mx) ** 0.45
            ink = "hi" if a >= 0.42 else "lo"
            href = f"#family={fam}" + (f"&surface={c}" if c else "")
            tds.append(f"<td style=\"background:rgba(247,181,56,{a:.3f})\">"
                       f"<a class=\"{ink}\" href=\"{esc(href)}\">{n}</a></td>")
        rows_html.append(
            f"<tr><th><a title=\"{esc(fam)}\" href=\"#family={esc(fam)}\">"
            f"{esc(fam)}</a><span class=\"rn\">{fam_counts[fam]}</span></th>"
            + "".join(tds) + "</tr>")
    cells = len(fam_counts) * len(cols)
    matrix = f"""<h3>What the database covers</h3>
<div class="matrixwrap"><table class="matrix">
<thead><tr><th class="corner">family <span class="rn">all</span></th>
{head}</tr></thead>
<tbody>{''.join(rows_html)}</tbody></table></div>
<p class="mcap">Shading is depth of evidence, from 1 record to {mx}.
{filled} of {cells} cells carry evidence; the {cells - filled} hatched cells
are real gaps, not hidden rows. Duplicate value sets and sample-run names are
reported by <code>make check</code> and await scientific adjudication — nothing
is excluded from these counts.</p>"""

    contribute = f"""<h3>How it fits together</h3>
{RELATION_SVG}
<div class="lowcards">
<a class="lcard" href="{REPO_URL}/blob/main/docs/FORMAT.md"><b>Contribute</b>
<span>Correct a record from its own page, or add a new one — one small YAML
file in a pull request. Spotted something wrong you cannot fix yourself?
Every record page has a <b class="report">Report an issue</b> button.</span></a>
<a class="lcard" href="{REPO_URL}/releases"><b>Cite this database</b>
<span>Archived releases; the DOI follows with the database paper.</span></a>
</div>"""

    arrival = ("<div id=\"arrival\">" + hero
               + "<div id=\"arrerr\" class=\"crumbs hidden\"></div>"
               + doors + typ_band + strip + matrix + contribute + "</div>")

    def fgroup(fid, label, scroll=False, is_open=False, cap="", find=False):
        cls = " class=\"fscroll\"" if scroll else ""
        cap_html = f"<div class=\"gcap\">{cap}</div>" if cap else ""
        find_html = (f"<input class=\"ffind\" data-facet=\"{fid}\" "
                     f"type=\"search\" placeholder=\"filter {label.lower()}…\" "
                     "aria-label=\"filter this list\">" if find else "")
        return (f"<details class=\"fgroup\" data-facet=\"{fid}\""
                + (" open" if is_open else "") + f"><summary>{label}</summary>"
                f"{cap_html}{find_html}<div id=\"facet-{fid}\"{cls}></div>"
                "</details>")

    rail = ("<div class=\"rail\">"
            + fgroup("kind", FACET_TITLE["kind"], is_open=True,
                     cap="records hold the evidence; typologies bundle it")
            + fgroup("surface", FACET_TITLE["surface"], is_open=True)
            + fgroup("family", FACET_TITLE["family"], scroll=True,
                     is_open=True, find=True)
            + fgroup("typology", FACET_TITLE["typology"])
            + fgroup("region", FACET_TITLE["region"])
            + fgroup("country", FACET_TITLE["country"], scroll=True, find=True)
            + fgroup("city", FACET_TITLE["city"], scroll=True, find=True)
            + "<a class=\"maplink\" href=\"map.html\">pick a place on the map →</a>"
            + fgroup("rep", FACET_TITLE["rep"],
                     cap="what a value stands for: one site, a whole city, "
                         "a region, or generic")
            + fgroup("scale", FACET_TITLE["scale"],
                     cap="the spatial unit described by the value")
            + fgroup("setting", FACET_TITLE["setting"],
                     cap="source-established intra-urban context")
            + fgroup("verification", FACET_TITLE["verification"], is_open=True)
            + fgroup("method", FACET_TITLE["method"])
            + fgroup("role", FACET_TITLE["role"])
            + fgroup("source", FACET_TITLE["source"], scroll=True, find=True)
            + "</div>")
    body = (arrival
            + "<div id=\"browse\" class=\"hidden\">"
            + "<input id=\"q\" class=\"search\" type=\"search\" "
              "placeholder=\"Search: parameter name, place, source, value...\">"
            + stats
            + f"<div class=\"layout\">{rail}<div>"
            + "<div id=\"afilters\" class=\"pill-row\"></div>"
            + "<div id=\"nres\" role=\"status\" aria-live=\"polite\">"
              "loading the index…</div>"
              "<div id=\"results\" class=\"results2\"></div>"
            + "</div></div></div>")
    # the browser shares the generator's label/order/accent tables: emitted
    # once here so Python and JS cannot drift
    js_consts = (
        "<script>"
        f"const LC_ORDER = {json.dumps(LC_ORDER)};"
        f"const ACC = {json.dumps(SURFACE_ACC)};"
        f"const LC_LABEL = {json.dumps(SURFACE_LABEL)};"
        f"const TYP_LABEL = {json.dumps(TYP_LABEL)};"
        f"const FACET_TITLE = {json.dumps(FACET_TITLE)};"
        f"const METHOD_LABEL = {json.dumps(METHOD_LABEL)};"
        f"const SCALE_LABEL = {json.dumps(APPLICABLE_SCALE_LABEL)};"
        f"const SETTING_LABEL = {json.dumps(URBAN_SETTING_LABEL)};"
        f"const STATE_LABEL = {json.dumps(PROVENANCE_STATE_LABEL)};"
        f"const ROLE_LABEL = {json.dumps(PROVENANCE_ROLE_LABEL)};"
        "</script>"
    )
    return page("Browse", body, 0, js_consts + BROWSER_JS)


def build_search_index(records, sources, places, sidecars=None, policy=None):
    entries = []
    sidecars = sidecars or {}
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
        override_source_keys = sorted({
            override.get("source")
            for override in rec.get("parameter_provenance", {}).values()
            if override.get("source")
        })
        override_sources = [sources.get(key, {}) for key in override_source_keys]
        sidecar = sidecars.get(path)
        assessment = sidecar.get("assessment", {}) if sidecar else {}
        review_type = (
            sidecar.get("review_type", "evidence")
            if sidecar
            else "composition" if kind == "typology" else "evidence"
        )
        method = assessment.get("method") or rec.get("method")
        verification = provenance_state(sidecar, policy)
        roles = sorted({
            item.get("role")
            for item in assessment.get("evidence", [])
            if item.get("role")
        })
        text = " ".join(str(x).lower() for x in [
            path, rec.get("name"), rec.get("place"), rec.get("origin"),
            rec.get("applicable_scale"),
            APPLICABLE_SCALE_LABEL.get(rec.get("applicable_scale")),
            rec.get("urban_setting"),
            URBAN_SETTING_LABEL.get(rec.get("urban_setting")),
            region, country, city,
            rec.get("source"), src.get("author"), src.get("title"),
            " ".join(override_source_keys),
            " ".join(source.get("author", "") for source in override_sources),
            " ".join(source.get("title", "") for source in override_sources),
            rec.get("target"), fam, surface or "",
            review_type, method, verification, " ".join(roles),
            " ".join(k for k, _ in pairs),
            " ".join(str(v) for _, v in pairs),
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
            "scale": rec.get("applicable_scale"),
            "setting": rec.get("urban_setting"),
            "source": rec.get("source"),
            "sources": sorted({
                key for key in [rec.get("source"), *override_source_keys] if key
            }),
            "method": method,
            "review_type": review_type,
            "verification": verification, "role": roles,
            "vals": vals, "text": text,
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
    ap.add_argument("--offline", action="store_true",
                    help="build without fetching typology photographs; pages "
                         "whose image is not already cached omit it")
    args = ap.parse_args()
    out = ROOT / args.out

    records, sources, places = load_all()
    sidecars = load_site_provenance()
    policy = load_site_policy()
    merge_github_signoffs(sidecars)
    used_by, cluster = build_graph(records)

    images, unresolved, release = load_images()
    staged = stage_images(out, images, release, offline=args.offline)

    for path, rec in records.items():
        fp = out / (path + ".html")
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(
            record_page(
                path, rec, records, sources, used_by, cluster,
                image=images.get(path) if path in staged else None,
                sidecars=sidecars, policy=policy,
                blocked=unresolved.get(path),
                # a typology the manifest publishes but this build could not
                # stage (an --offline run) shows nothing rather than claiming
                # it has no photograph
                invite=(path.startswith("archetypes/typologies/")
                        and path not in images),
            )
        )

    by_place = defaultdict(list)
    by_source = defaultdict(set)
    for path, rec in records.items():
        if rec.get("place"):
            by_place[rec["place"]].append(path)
        if rec.get("source"):
            by_source[rec["source"]].add(path)
        for override in rec.get("parameter_provenance", {}).values():
            if override.get("source"):
                by_source[override["source"]].add(path)

    (out / "place").mkdir(parents=True, exist_ok=True)
    for slug, paths in by_place.items():
        info = places.get(slug, {"name": slug})
        (out / "place" / f"{slug}.html").write_text(
            place_page(slug, info, paths, records))

    (out / "source").mkdir(parents=True, exist_ok=True)
    source_roles = provenance_source_roles(sidecars)
    for key in sorted(set(by_source) | set(source_roles)):
        paths = sorted(by_source.get(key, []))
        src = sources.get(key, {})
        (out / "source" / f"{key}.html").write_text(
            source_page(key, src, paths, records, source_roles.get(key)))

    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "data" / "index.json").write_text(
        json.dumps(build_search_index(records, sources, places, sidecars, policy),
                   ensure_ascii=False))
    (out / "data" / "provenance.json").write_text(
        json.dumps(build_provenance_index(sidecars, policy), ensure_ascii=False)
    )
    (out / "index.html").write_text(
        build_index_page(records, sources, places, by_place))
    (out / "map.html").write_text(build_map_page(places, by_place))
    (out / "review.html").write_text(review_guide_page(policy))
    (out / ".nojekyll").write_text("")
    print(f"site: {len(records)} entry pages, {len(by_place)} place pages, "
          f"{len(by_source)} source pages, {len(staged)} typology photographs "
          f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

Provenance pass (jsonschema + rfc8785, loaded only by main()):
  - sidecars validate against the Draft 2020-12 schema
  - evidence references, revision fingerprints and derivation graphs resolve
  - attestation envelopes remain offline unless authenticated facts are injected

Model pass (--supy, needs supy importable):
  - every evidence record's parameter fragment validates against the supy
    class its `target:` names (wrapped as RefValue with the record's citation,
    exactly as the exporter emits it)
  - every archetype's assembled fragment validates the same way

Exit code 0 = all checks pass.

Usage:
  python scripts/check_db.py            # structural only
  python scripts/check_db.py --supy     # structural + SUEWS configuration validation
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db"
URBAN_SETTINGS_FILE = ROOT / "schema" / "urban_settings.yml"
APPLICABLE_SCALES_FILE = ROOT / "schema" / "applicable_scales.yml"

# Repo-local record targets that have no supy class
LOCAL_TARGETS = {"material", "construction", "typology", "ohm_coefficients"}
PARAMETER_METHODS = {"measured", "fitted", "literature", "calculated", "assumed"}
REPRESENTATIVENESS = {"site", "city", "regional", "generic"}
PARAMETER_PROVENANCE_FIELDS = {
    "source",
    "method",
    "place",
    "representativeness",
    "urban_setting",
    "applicable_scale",
    "source_bounds",
}
BARE_PARAMETER_CONTAINERS = {"daywat", "daywatper", "working_day", "holiday"}


def load_urban_settings():
    """Return the controlled intra-urban setting registry."""
    doc = yaml.safe_load(URBAN_SETTINGS_FILE.read_text()) or {}
    return doc.get("urban_settings") or {}


def load_applicable_scales():
    """Return the controlled applicable-scale registry."""
    doc = yaml.safe_load(APPLICABLE_SCALES_FILE.read_text()) or {}
    return doc.get("applicable_scales") or {}


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


def export_ref_leaf_paths(rec):
    """Return exact parameter paths that the exporter wraps as RefValues."""
    paths = set()

    def visit(node, path="", bare=False):
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                visit(
                    value,
                    child,
                    bare or key in BARE_PARAMETER_CONTAINERS or key == "context",
                )
            return
        if isinstance(node, list):
            if not bare and all(
                value is None or isinstance(value, (int, float)) for value in node
            ):
                paths.add(f"parameters.{path}")
            return
        if not bare and node is not None and not isinstance(node, (str, bool)):
            paths.add(f"parameters.{path}")

    visit(rec.get("parameters", {}))
    return paths


def parameter_provenance_errors(path, rec, sources, places, urban_settings,
                                applicable_scales):
    """Validate canonical per-leaf provenance overrides."""
    overrides = rec.get("parameter_provenance")
    if overrides is None:
        return []
    if not isinstance(overrides, dict) or not overrides:
        return [f"{path}: parameter_provenance must be a non-empty mapping"]

    errors = []
    exportable_paths = export_ref_leaf_paths(rec)
    for parameter_path, override in overrides.items():
        prefix = f"{path}: parameter_provenance {parameter_path!r}"
        if parameter_path not in exportable_paths:
            errors.append(f"{prefix} is not an exportable parameter leaf")
            continue
        if not isinstance(override, dict) or not override:
            errors.append(f"{prefix} must be a non-empty mapping")
            continue
        unknown = sorted(set(override) - PARAMETER_PROVENANCE_FIELDS)
        if unknown:
            errors.append(f"{prefix} has unknown fields {unknown}")
        null_fields = sorted(key for key, value in override.items() if value is None)
        if null_fields:
            errors.append(f"{prefix} has null fields {null_fields}")
        source = override.get("source")
        if source is not None and source not in sources:
            errors.append(f"{prefix} source {source!r} not in sources.yml")
        method = override.get("method")
        if method is not None and method not in PARAMETER_METHODS:
            errors.append(f"{prefix} has invalid method {method!r}")
        place = override.get("place")
        if place is not None and place not in places:
            errors.append(f"{prefix} place {place!r} not in places.yml")
        representativeness = override.get("representativeness")
        if (
            representativeness is not None
            and representativeness not in REPRESENTATIVENESS
        ):
            errors.append(
                f"{prefix} has invalid representativeness "
                f"{representativeness!r}"
            )
        urban_setting = override.get("urban_setting")
        if urban_setting is not None and urban_setting not in urban_settings:
            errors.append(f"{prefix} has invalid urban_setting {urban_setting!r}")
        applicable_scale = override.get("applicable_scale")
        if (
            applicable_scale is not None
            and applicable_scale not in applicable_scales
        ):
            errors.append(
                f"{prefix} has invalid applicable_scale {applicable_scale!r}"
            )
        bounds = override.get("source_bounds")
        if bounds is not None:
            if not isinstance(bounds, dict):
                errors.append(f"{prefix} source_bounds must be a mapping")
                continue
            expected = {"minimum", "maximum", "active_role"}
            if set(bounds) != expected:
                errors.append(
                    f"{prefix} source_bounds must contain exactly "
                    "minimum, maximum and active_role"
                )
                continue
            minimum = bounds["minimum"]
            maximum = bounds["maximum"]
            active_role = bounds["active_role"]
            def numeric(value):
                return (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                )
            if not numeric(minimum) or not numeric(maximum):
                errors.append(
                    f"{prefix} source_bounds minimum and maximum must be finite numbers"
                )
                continue
            if minimum > maximum:
                errors.append(f"{prefix} source_bounds minimum exceeds maximum")
                continue
            if active_role not in {"minimum", "maximum", "within"}:
                errors.append(
                    f"{prefix} source_bounds active_role must be minimum, maximum or within"
                )
                continue
            node = rec.get("parameters", {})
            for part in parameter_path.removeprefix("parameters.").split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if not numeric(node):
                errors.append(f"{prefix} source_bounds require a numeric scalar leaf")
            elif not minimum <= node <= maximum:
                errors.append(f"{prefix} active value lies outside source_bounds")
            elif active_role == "minimum" and node != minimum:
                errors.append(
                    f"{prefix} active value is not the source_bounds minimum"
                )
            elif active_role == "maximum" and node != maximum:
                errors.append(
                    f"{prefix} active value is not the source_bounds maximum"
                )
            elif active_role == "within" and not minimum < node < maximum:
                errors.append(
                    f"{prefix} active value is not within source_bounds"
                )
    return errors


def structural_check(records, sources, places):
    errors = []
    urban_settings = load_urban_settings()
    applicable_scales = load_applicable_scales()
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
        urban_setting = rec.get("urban_setting")
        if urban_setting is not None and urban_setting not in urban_settings:
            errors.append(
                f"{path}: urban_setting {urban_setting!r} not in "
                "schema/urban_settings.yml"
            )
        applicable_scale = rec.get("applicable_scale")
        if (
            applicable_scale is not None
            and applicable_scale not in applicable_scales
        ):
            errors.append(
                f"{path}: applicable_scale {applicable_scale!r} not in "
                "schema/applicable_scales.yml"
            )
        if kind == "record":
            errors += parameter_provenance_errors(
                path,
                rec,
                sources,
                places,
                urban_settings,
                applicable_scales,
            )
        elif "parameter_provenance" in rec:
            errors.append(
                f"{path}: parameter_provenance is allowed only on evidence records"
            )
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
    errors += image_check(records)
    return errors


IMAGE_FIELDS = ("file", "origin_url", "description_page", "credit",
                "licence", "licence_url", "sha256", "bytes", "width", "height")


def image_check(records):
    """db/images.yml: every shown photograph carries its attribution.

    The photographs the site publishes are released under licences that
    require attribution wherever the image appears, so an entry without a
    credit and a licence is an error rather than a warning: the site renders
    an image only if it is listed here, and a listing without attribution
    would be exactly the thing the licence forbids.

    A typology that carries a `url` but whose licence could not be
    established from its source belongs under `unresolved`, saying why and
    what would settle it. Requiring every such record to appear in one
    section or the other keeps an omission deliberate rather than silent.

    Offline by design: this checks the manifest against the records, never
    the network. Whether the release actually holds the assets is settled at
    build time, where a mismatch stops the site being published.
    """
    errors = []
    manifest_file = DB / "images.yml"
    if not manifest_file.exists():
        return errors
    doc = yaml.safe_load(manifest_file.read_text()) or {}
    images = doc.get("images") or {}
    unresolved = doc.get("unresolved") or {}
    if not doc.get("release"):
        errors.append("images.yml: no release named for the image assets")

    seen_files = {}
    for path, entry in images.items():
        if path not in records:
            errors.append(f"images.yml: {path!r} is not a record")
            continue
        if not path.startswith("archetypes/typologies/"):
            errors.append(f"images.yml: {path!r} is not a typology")
        missing = [f for f in IMAGE_FIELDS if not entry.get(f)]
        if missing:
            errors.append(f"images.yml: {path}: missing {', '.join(missing)}")
        name = entry.get("file")
        if name in seen_files:
            errors.append(f"images.yml: {path}: file {name!r} already used by "
                          f"{seen_files[name]}")
        elif name:
            seen_files[name] = path

    for path, entry in unresolved.items():
        if path not in records:
            errors.append(f"images.yml: unresolved {path!r} is not a record")
        if path in images:
            errors.append(f"images.yml: {path} is both shown and unresolved")
        for field in ("reason", "what_would_settle_it"):
            if not entry.get(field):
                errors.append(f"images.yml: unresolved {path}: missing {field}")

    for path, rec in records.items():
        if not path.startswith("archetypes/typologies/"):
            continue
        if rec.get("url") and path not in images and path not in unresolved:
            errors.append(f"{path}: carries a url but images.yml neither "
                          "publishes it nor records why it cannot be")
    return errors


# ---------------- SUEWS configuration validation via SuPy ----------------


def wrap_ref(params, ref_info, ref_overrides=None):
    """Wrap each leaf value as a RefValue dict, mirroring the exporter.

    Three shapes stay bare because their supy fields are not FlexibleRefValue:
    WeeklyProfile day fields (daywat/daywatper), the hour dictionaries inside
    profile sides, and any string/bool. A list of scalars wraps as ONE
    RefValue holding the whole list (thermal_layers.dz etc.).
    """
    ref_overrides = ref_overrides or {}

    def wrap(node, path="", bare=False):
        if isinstance(node, dict):
            return {
                k: wrap(
                    v,
                    f"{path}.{k}" if path else str(k),
                    bare or k in BARE_PARAMETER_CONTAINERS,
                )
                for k, v in node.items()
            }
        if isinstance(node, list):
            if all(v is None or isinstance(v, (int, float)) for v in node):
                effective_ref = ref_overrides.get(f"parameters.{path}", ref_info)
                return node if bare else {"value": node, "ref": effective_ref}
            return [wrap(v, path, bare) for v in node]
        if bare or node is None or isinstance(node, (str, bool)):
            return node
        effective_ref = ref_overrides.get(f"parameters.{path}", ref_info)
        return {"value": node, "ref": effective_ref}

    return wrap(params)


def reference_info(rec, sources, override=None):
    """Build one export citation from the record envelope plus an override."""
    metadata = {
        key: rec.get(key)
        for key in (
            "source",
            "place",
            "representativeness",
            "urban_setting",
            "applicable_scale",
        )
    }
    metadata.update(override or {})
    src_key = metadata.get("source")
    src = sources.get(src_key, {}) if src_key else {}
    desc_bits = []
    if metadata.get("place"):
        desc_bits.append(metadata["place"])
    if metadata.get("urban_setting"):
        desc_bits.append(metadata["urban_setting"])
    if metadata.get("representativeness"):
        desc_bits.append(metadata["representativeness"])
    if metadata.get("applicable_scale"):
        desc_bits.append(metadata["applicable_scale"])
    bounds = metadata.get("source_bounds")
    if isinstance(bounds, dict):
        desc_bits.append(
            f"source bounds {bounds.get('minimum')}–{bounds.get('maximum')} "
            f"(active: {bounds.get('active_role')})"
        )
    return {
        "ID": src_key,
        "DOI": src.get("doi"),
        "desc": ", ".join(desc_bits) or None,
    }


def suews_configuration_fragment(rec, sources):
    """Build the SUEWS configuration fragment emitted for a record."""
    ref_info = reference_info(rec, sources)
    ref_overrides = {
        path: reference_info(rec, sources, override)
        for path, override in rec.get("parameter_provenance", {}).items()
    }
    params = {k: v for k, v in rec.get("parameters", {}).items() if k != "context"}
    return wrap_ref(params, ref_info, ref_overrides)


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
            frag = suews_configuration_fragment(rec, sources)
        elif target in class_for:
            cls = class_for[target]
            frag = suews_configuration_fragment(rec, sources)
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


def _leaves(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, f"{prefix}.{i}")
    else:
        yield prefix, node


SUSPECT_MARKERS = ("sample run", "sample data", "test", "placeholder",
                   "dummy", "not recommended", "unclear")


def quality_check(records):
    """Data-quality warnings for human adjudication, never auto-fixed.

    - duplicate records: same target with an identical parameter-leaf set
      (candidates for merging, or evidence of copied rather than measured
      values)
    - suspect entries: names that read as test fixtures or bookkeeping
      rather than data
    """
    warnings = []
    by_content = {}
    profile_groups = {}
    for path, rec in sorted(records.items()):
        if not path.startswith("records/"):
            continue
        params = rec.get("parameters")
        if not params:
            continue
        key = (str(rec.get("target")),
               tuple(sorted((k, str(v)) for k, v in _leaves(params))))
        if path.startswith("records/profiles/"):
            # per-country profiles share value sets by design (a handful of
            # classes applied to many countries): summarise, don't spam
            group = path.rsplit("/", 1)[0]
            profile_groups.setdefault(group, {}).setdefault(key, []).append(path)
            continue
        if key in by_content:
            warnings.append(f"duplicate values: {path} repeats "
                            f"{by_content[key]} exactly")
        else:
            by_content[key] = path
    for group, sets in sorted(profile_groups.items()):
        n = sum(len(v) for v in sets.values())
        if n > len(sets):
            warnings.append(f"shared profiles: {group}: {n} records carry "
                            f"{len(sets)} distinct value sets")
    for path, rec in sorted(records.items()):
        name = str(rec.get("name") or "").lower()
        hits = [m for m in SUSPECT_MARKERS if m in name]
        if hits:
            warnings.append(f"suspect name: {path} ({rec.get('name')!r} "
                            f"reads as {'/'.join(hits)})")
    return warnings


def linkage_check(records):
    """Cross-record coupling rules from schema/parameter_groups.yml.

    Reported as warnings, not errors: they flag scientifically inconsistent
    COMBINATIONS in curated archetypes, which need judgement to resolve.
    """
    warnings = []
    for path, rec in records.items():
        uses = rec.get("uses", {})
        lai_ref = uses.get("leaf_area_index")
        lgp_ref = uses.get("leaf_growth_power")
        if lai_ref and lgp_ref and lai_ref in records and lgp_ref in records:
            a = records[lai_ref].get("parameters", {}).get("lai", {}).get("lai_type")
            b = records[lgp_ref].get("parameters", {}).get("lai", {}).get("lai_type")
            if a is not None and b is not None and a != b:
                warnings.append(
                    f"{path}: mixes LAI records fitted for different equations "
                    f"(lai_type {a} in {lai_ref} vs {b} in {lgp_ref})"
                )
    return warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--supy", action="store_true", help="also validate against supy")
    ap.add_argument("--strict", action="store_true",
                    help="treat linkage warnings as errors")
    args = ap.parse_args()

    records, sources, places = load_all()
    errors = structural_check(records, sources, places)
    print(f"structural: {len(records)} files checked, {len(errors)} errors")
    for e in errors[:30]:
        print("  -", e)

    # Keep the provenance dependencies out of this module's import path:
    # build_site.py, export_record.py and migration checks reuse load_all()
    # in PyYAML-only environments.
    from provenance import check_provenance, load_provenance_sidecars

    sidecars, provenance_errors = load_provenance_sidecars()
    provenance_errors += check_provenance(records, sources, places, sidecars)
    print(
        f"provenance: {len(sidecars)} sidecars checked, "
        f"{len(provenance_errors)} errors"
    )
    for e in provenance_errors[:30]:
        print("  -", e)
    errors += provenance_errors

    warnings = linkage_check(records)
    print(f"linkage: {len(warnings)} warnings")
    for w in warnings:
        print("  ~", w)
    if args.strict:
        errors += warnings

    quality = quality_check(records)
    print(f"quality: {len(quality)} warnings (for review, never auto-fixed)")
    for w in quality[:40]:
        print("  ~", w)
    if len(quality) > 40:
        print(f"  ~ ... and {len(quality) - 40} more")

    if args.supy:
        supy_errors, n = supy_check(records, sources)
        print(
            "SUEWS configuration validation: "
            f"{n} configuration fragments validated, {len(supy_errors)} errors"
        )
        for e in supy_errors[:30]:
            print("  -", e)
        errors += supy_errors

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

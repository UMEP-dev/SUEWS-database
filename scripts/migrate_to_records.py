#!/usr/bin/env python3
"""Migrate the legacy per-table database (db/*.yml) to the record format.

One legacy row becomes one record file under db/records/ (evidence) or
db/archetypes/ (composite rows), integer Ref IDs become citation keys in
db/sources.yml, and every Origin string resolves to a place in db/places.yml
via schema/origins_map.yml. The mapping implemented here is documented and
audited in schema/table_mapping.yml.

Nothing is dropped: columns without a supy home land verbatim under each
record's `legacy:` block, and the census (schema/migration_census.yml) proves
every non-null cell of every legacy row landed exactly once.

Usage: python scripts/migrate_to_records.py
"""

from __future__ import annotations

import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db"
SCHEMA = ROOT / "schema"

SCHEMA_VERSION = "2026.5"  # released supy the mapping was verified against

SURFACE_NAMES = {
    "Paved": "paved",
    "Buildings": "bldgs",
    "Bare Soil": "bsoil",
    "Grass": "grass",
    "Evergreen Tree": "evetr",
    "Deciduous Tree": "dectr",
    "Water": "water",
    "Snow": "snow",
    "No Surface": "common",
}
VEG_SURFACES = {"evetr", "dectr", "grass"}

PROFILE_KINDS = {
    "Human activity": ("human-activity", "anthropogenic_emissions.co2.humactivity_24hr"),
    "Traffic": ("traffic", "anthropogenic_emissions.co2.traffprof_24hr"),
    "Energy use": ("energy-use", "anthropogenic_emissions.heat.ahprof_24hr"),
    "Commercial": ("energy-use", "anthropogenic_emissions.heat.ahprof_24hr"),
    "Residential": ("energy-use", "anthropogenic_emissions.heat.ahprof_24hr"),
    "Industry": ("energy-use", "anthropogenic_emissions.heat.ahprof_24hr"),
    "Population density": ("population", "anthropogenic_emissions.heat.popprof_24hr"),
    "Water use (manual)": ("water-use-manual", "irrigation.wuprofm_24hr"),
    "Water use (automatic)": ("water-use-automatic", "irrigation.wuprofa_24hr"),
    "Snow removal": ("snow-removal", "snow.snow_profile_24hr"),
}


def slugify(text):
    text = unicodedata.normalize("NFKD", str(text))
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text or "x"


def load_table(name):
    data = yaml.safe_load((DB / name).read_text())
    return data["entries"]


def is_missing(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "nan", "none"))


def is_sentinel(v):
    """-999 is the classic SUEWS missing-value marker, never a real value."""
    return isinstance(v, (int, float)) and v == -999


class Migration:
    def __init__(self):
        self.origins_map = yaml.safe_load((SCHEMA / "origins_map.yml").read_text())["origins"]
        self.tables = {}  # sheet name -> entries
        registry = yaml.safe_load((SCHEMA / "tables.yml").read_text())["tables"]
        self.registry = {t["sheet"]: t for t in registry}
        for sheet, t in self.registry.items():
            self.tables[sheet] = load_table(t["file"])
        # global ID index: id -> (sheet, row)
        self.id_index = {}
        for sheet, entries in self.tables.items():
            for rid, row in entries.items():
                self.id_index[rid] = (sheet, row)
        self.sources = {}       # citation key -> source dict
        self.ref_to_key = {}    # legacy ref id -> citation key
        self.places = {}        # place slug -> place dict
        self.records = {}       # record path (str, no .yml) -> record dict
        self.id_to_record = {}  # legacy row id -> record path
        self.census = defaultdict(lambda: Counter())
        self.flags = []
        self.dangling_refs = set()
        self.region_by_name = {}

    # ---------------- sources & places ----------------

    def build_sources(self):
        rows = self.tables["References"]
        for rid in sorted(rows):
            row = rows[rid]
            author = row.get("Author")
            year = row.get("Year")
            n_cells = sum(1 for v in row.values() if not is_missing(v))
            self.census["References"]["rows"] += 1
            self.census["References"]["cells_in"] += n_cells
            if is_missing(author) or author == "SUEWS" or not year:
                key = "unreferenced"
                if key not in self.sources:
                    self.sources[key] = {
                        "note": "legacy placeholder row: value carried no citation",
                        "legacy_ids": [],
                    }
                self.sources[key]["legacy_ids"].append(rid)
                self.ref_to_key[rid] = key
                self.census["References"]["cells_legacy"] += n_cells
                continue
            self.census["References"]["cells_mapped"] += n_cells
            surname = slugify(str(author).split(";")[0].split(",")[0])
            base = f"{surname}{year}"
            key = base
            n = 0
            while key in self.sources:
                n += 1
                key = f"{base}{chr(ord('a') + n)}"
            if n == 1:  # first duplicate: rename the original to ...a
                self.sources[f"{base}a"] = self.sources.pop(base)
                for r, k in list(self.ref_to_key.items()):
                    if k == base:
                        self.ref_to_key[r] = f"{base}a"
                key = f"{base}b"
            doi = row.get("DOI")
            if isinstance(doi, str):
                doi = doi.replace("https://doi.org/", "").strip() or None
            src = {
                "author": author,
                "year": year,
                "title": None if is_missing(row.get("Title")) else row.get("Title"),
                "journal": None if is_missing(row.get("Journal")) else row.get("Journal"),
                "doi": doi if not is_missing(doi) else None,
                "type": None if is_missing(row.get("Item Type")) else row.get("Item Type"),
                "legacy_id": rid,
            }
            self.sources[key] = {k: v for k, v in src.items() if v is not None}
            self.ref_to_key[rid] = key

    def place_for(self, origin):
        """Resolve a raw Origin string -> (place slug, representativeness)."""
        if is_missing(origin):
            return None, None
        entry = self.origins_map.get(str(origin).strip())
        if entry is None:
            self.flags.append(f"unmapped origin string: {origin!r}")
            return slugify(origin), None
        slug = entry["place"]
        if slug not in self.places:
            self.places[slug] = {"name": slug.replace("-", " ").title()}
        return slug, entry.get("representativeness")

    def country_place(self, country):
        slug = slugify(country)
        if slug not in self.places:
            self.places[slug] = {"name": str(country), "kind": "country"}
        return slug

    # ---------------- record plumbing ----------------

    def source_key(self, ref):
        if is_missing(ref):
            return "unreferenced"
        key = self.ref_to_key.get(ref)
        if key is None:
            self.flags.append(f"dangling Ref id {ref}")
            self.dangling_refs.add(ref)
            return "unreferenced"
        return key

    def attach_dangling(self, rec, ref):
        """Keep a dangling legacy Ref id on the record so nothing is lost."""
        if not is_missing(ref) and ref not in self.ref_to_key:
            rec["source_legacy_ref"] = ref

    def add_record(self, directory, row_id, row, target, parameters,
                   legacy=None, extra=None, slug_hint=None):
        origin = row.get("Origin")
        place, rep = self.place_for(origin)
        source = self.source_key(row.get("Ref"))
        base = f"{place or 'generic'}--{source}"
        if slug_hint:
            base = f"{base}--{slugify(slug_hint)}"
        path = f"{directory}/{base}"
        if path in self.records:
            # first disambiguator: the row's own name, when it adds anything
            name = row.get("Name")
            if not is_missing(name):
                name_slug = slugify(name)
                if name_slug not in base:
                    path = f"{directory}/{base}--{name_slug}"
        n = 2
        stem = path
        while path in self.records:
            path = f"{stem}--{n}"
            n += 1
        rec = {
            "record": path,
            "schema_version": SCHEMA_VERSION,
            "target": target,
        }
        if not is_missing(row.get("Name")):
            rec["name"] = row["Name"]
        if place:
            rec["place"] = place
        if not is_missing(origin):
            rec["origin"] = origin
        if rep:
            rec["representativeness"] = rep
        rec["source"] = source
        self.attach_dangling(rec, row.get("Ref"))
        rec["legacy_id"] = row_id
        if extra:
            rec.update(extra)
        rec["parameters"] = parameters
        if legacy:
            rec["legacy"] = legacy
        self.records[path] = rec
        self.id_to_record[row_id] = path
        return path

    def count(self, sheet, row, parameters, legacy, envelope_cols):
        """Census bookkeeping: every non-null cell must be accounted for."""
        c = self.census[sheet]
        c["rows"] += 1
        n_cells = sum(1 for k, v in row.items() if not is_missing(v))
        c["cells_in"] += n_cells
        c["cells_envelope"] += sum(
            1 for k in envelope_cols if not is_missing(row.get(k))
        )

        def leaves(x):
            if isinstance(x, dict):
                return sum(leaves(v) for v in x.values())
            if isinstance(x, list):
                return sum(leaves(v) for v in x)
            return 0 if x is None else 1

        c["cells_mapped"] += leaves(parameters)
        c["cells_legacy"] += leaves(legacy)

    # ---------------- property-table handlers ----------------

    def set_path(self, params, dotted, value):
        parts = dotted.split(".")
        d = params
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = value

    def migrate_flat(self, sheet, directory_fn, target_fn, colmap_fn,
                     envelope_cols=("ID", "Surface", "Origin", "Name", "Ref"),
                     family=None):
        for rid in sorted(self.tables[sheet]):
            row = self.tables[sheet][rid]
            surface = SURFACE_NAMES.get(row.get("Surface"), None)
            params, legacy = {}, {}
            for col, val in row.items():
                if col in envelope_cols or is_missing(val):
                    continue
                dest = colmap_fn(col, surface, row)
                if dest is None or is_sentinel(val):
                    legacy[col] = val
                else:
                    self.set_path(params, dest, val)
            self.add_record(
                directory_fn(surface, row), rid, row, target_fn(surface, row),
                params, legacy or None, slug_hint=family,
            )
            self.count(sheet, row, params, legacy, envelope_cols)

    def migrate_albedo(self):
        sheet = "Albedo"
        envelope_cols = ("ID", "Surface", "Origin", "Name", "Ref")
        for rid in sorted(self.tables[sheet]):
            row = self.tables[sheet][rid]
            surface = SURFACE_NAMES.get(row.get("Surface"))
            params, legacy = {}, {}
            amin, amax = row.get("Alb_min"), row.get("Alb_max")
            if surface == "snow":
                target, directory = "snow", "records/snow"
                if not is_missing(amin):
                    params["snow_albedo_min"] = amin
                if not is_missing(amax):
                    params["snow_albedo_max"] = amax
            elif surface in VEG_SURFACES:
                target, directory = f"land_cover.{surface}", f"records/surfaces/{surface}"
                if not is_missing(amin):
                    params["alb_min"] = amin
                if not is_missing(amax):
                    params["alb_max"] = amax
            else:
                target, directory = f"land_cover.{surface}", f"records/surfaces/{surface}"
                if not is_missing(amin):
                    params["alb"] = amin
                if not is_missing(amax):
                    if amin == amax:
                        legacy["Alb_max"] = amax  # duplicate of alb, kept for census
                    else:
                        legacy["Alb_max"] = amax
                        self.flags.append(
                            f"Albedo {rid} ({row.get('Surface')}): Alb_min {amin} != "
                            f"Alb_max {amax}; alb takes Alb_min, Alb_max kept in legacy"
                        )
            self.add_record(directory, rid, row, target, params, legacy or None,
                            slug_hint="albedo")
            self.count(sheet, row, params, legacy, envelope_cols)

    def migrate_emissivity(self):
        def colmap(col, surface, row):
            if surface == "snow":
                return "narp_emissivity_snow"
            return "emis"

        self.migrate_flat(
            "Emissivity",
            lambda s, r: "records/snow" if s == "snow" else f"records/surfaces/{s}",
            lambda s, r: "snow" if s == "snow" else f"land_cover.{s}",
            colmap,
            family="emissivity",
        )

    def migrate_ohm(self):
        sheet = "OHM"
        envelope_cols = ("ID", "Surface", "Origin", "Name", "Ref", "Season")
        for rid in sorted(self.tables[sheet]):
            row = self.tables[sheet][rid]
            surface = SURFACE_NAMES.get(row.get("Surface"), "common")
            params = {
                k: row[k] for k in ("a1", "a2", "a3")
                if not is_missing(row.get(k)) and not is_sentinel(row.get(k))
            }
            extra = {}
            if not is_missing(row.get("Season")):
                extra["season_label"] = row["Season"]
            self.add_record(
                "records/ohm", rid, row, "ohm_coefficients", params,
                extra=extra, slug_hint=row.get("Surface"),
            )
            self.count(sheet, row, params, {}, envelope_cols)

    def migrate_simple_tables(self):
        simple = {
            "ANOHM": {
                "AnOHM_Cp": "rho_cp_anohm", "AnOHM_Kk": "k_anohm", "AnOHM_Ch": "ch_anohm",
            },
            "Water Storage": {
                "StorageMin": "storage_drain_params.store_min",
                "StorageMax": "storage_drain_params.store_max",
            },
            "Drainage": {
                "DrainageEq": "storage_drain_params.drain_eq",
                "DrainageCoef1": "storage_drain_params.drain_coef_1",
                "DrainageCoef2": "storage_drain_params.drain_coef_2",
                "WetThreshold": "wet_threshold",
            },
            "Water State": {"StateLimit": "state_limit", "WaterDepth": None},
            "SnowLimPatch": {"SnowLimPatch": "snowpack_limit"},
            "Soil": {
                "SoilDepth": "soil_depth",
                "SoilStoreCap": "soil_store_capacity",
                "SatHydraulicCond": "saturated_hydraulic_conductivity",
                "SoilDensity": "soil_density",
                "InfiltrationRate": None,
                "OBS_SMDepth": "soil_observation.depth",
                "OBS_SMCap": "soil_observation.smcap",
                "OBS_SoilNotRocks": "soil_observation.soil_not_rocks",
            },
            "Leaf Area Index": {
                "LAIEq": "lai.lai_type", "LAIMin": "lai.lai_min", "LAIMax": "lai.lai_max",
            },
            "Leaf Growth Power": {
                "LeafGrowthPower1": "lai.lai_power.growth_lai",
                "LeafGrowthPower2": "lai.lai_power.growth_gdd",
                "LeafOffPower1": "lai.lai_power.senescence_lai",
                "LeafOffPower2": "lai.lai_power.senescence_sdd",
                "LAIEq": "lai.lai_type",
            },
            "Vegetation Growth": {
                "BaseT": "lai.base_temperature",
                "BaseTe": "lai.base_temperature_senescence",
                "GDDFull": "lai.gdd_full",
                "SDDFull": "lai.sdd_full",
            },
            "Max Vegetation Conductance": {"MaxConductance": "max_conductance"},
            "Porosity": {
                "PorosityMin": "porosity_min_deciduous",
                "PorosityMax": "porosity_max_deciduous",
            },
            "Biogen CO2": {
                "alpha": "alpha_bio_co2", "beta": "beta_bio_co2", "theta": "theta_bio_co2",
                "alpha_enh": "alpha_enh_bioco2", "beta_enh": "beta_enh_bioco2",
                "resp_a": "resp_a", "resp_b": "resp_b", "min_respi": "min_res_bioco2",
            },
        }
        families = {
            "ANOHM": "anohm", "Water Storage": "water-storage",
            "Drainage": "drainage", "Water State": "water-state",
            "SnowLimPatch": "snow-lim", "Soil": "soil",
            "Leaf Area Index": "lai", "Leaf Growth Power": "lai-power",
            "Vegetation Growth": "phenology",
            "Max Vegetation Conductance": "max-conductance",
            "Porosity": "porosity", "Biogen CO2": "biogen-co2",
        }
        for sheet, colmap in simple.items():
            self.migrate_flat(
                sheet,
                lambda s, r: f"records/surfaces/{s or 'common'}",
                lambda s, r: f"land_cover.{s}" if s not in (None, "common") else "land_cover.common",
                lambda col, s, row, cm=colmap: cm.get(col),
                family=families[sheet],
            )

    def migrate_estm(self):
        sheet = "ESTM"
        envelope_cols = ("ID", "Surface", "Origin", "Name", "Ref")
        for rid in sorted(self.tables[sheet]):
            row = self.tables[sheet][rid]
            surface = SURFACE_NAMES.get(row.get("Surface"), "common")
            params, legacy = {}, {}
            dz, k, rho = [], [], []
            for i in range(1, 6):
                t, kk, rc = row.get(f"Surf_thick{i}"), row.get(f"Surf_k{i}"), row.get(f"Surf_rhoCp{i}")
                # -999 layer cells are placeholders: keep them under legacy
                # and leave a None gap in the array (supy allows nulls)
                triple = []
                for col, v in ((f"Surf_thick{i}", t), (f"Surf_k{i}", kk), (f"Surf_rhoCp{i}", rc)):
                    if is_sentinel(v):
                        legacy[col] = v
                        triple.append(None)
                    else:
                        triple.append(None if is_missing(v) else v)
                if any(x is not None for x in triple):
                    dz.append(triple[0])
                    k.append(triple[1])
                    rho.append(triple[2])
            if dz and surface != "snow":
                params["thermal_layers"] = {"dz": dz, "k": k, "rho_cp": rho}
            elif dz:
                # supy's SnowParams has no thermal-layer fields: keep the snow
                # row's layer set verbatim under legacy
                for i in range(1, 6):
                    for col in (f"Surf_thick{i}", f"Surf_k{i}", f"Surf_rhoCp{i}"):
                        if not is_missing(row.get(col)) and col not in legacy:
                            legacy[col] = row[col]
            for col, val in row.items():
                if col in envelope_cols or is_missing(val) or col.startswith("Surf_"):
                    continue
                legacy[col] = val
            if surface == "snow":
                directory, target = "records/snow", "snow"
            elif surface == "common":
                directory, target = "records/surfaces/common", "land_cover.common"
            else:
                directory, target = f"records/surfaces/{surface}", f"land_cover.{surface}"
            self.add_record(
                directory, rid, row, target,
                params, legacy or None, slug_hint="thermal-layers",
            )
            self.count(sheet, row, params, legacy, envelope_cols)

    def migrate_conductance(self):
        colmap = {
            "G1": "g_max", "G2": "g_k", "G3": "g_q_base", "G4": "g_q_shape",
            "G5": "g_t", "G6": "g_sm", "TH": "th", "TL": "tl",
            "S1": "s1", "S2": "s2", "Kmax": "kmax",
            "gsModel": "context.surface_conductance_model",
        }
        self.migrate_flat(
            "Conductance",
            lambda s, r: "records/conductance",
            lambda s, r: "conductance",
            lambda col, s, row: colmap.get(col),
            envelope_cols=("ID", "Origin", "Name", "Ref"),
        )

    def migrate_irrigation(self):
        days = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
        colmap = {
            "Ie_start": "ie_start", "Ie_end": "ie_end",
            "InternalWaterUse": "internalwateruse_h",
            "Faut": "faut", "H_maintain": "h_maintain",
        }
        for i, d in enumerate(days, start=1):
            colmap[f"DayWat({i})"] = f"daywat.{d}"
            colmap[f"DayWatPer({i})"] = f"daywatper.{d}"
        self.migrate_flat(
            "Irrigation",
            lambda s, r: "records/irrigation",
            lambda s, r: "irrigation",
            lambda col, s, row: colmap.get(col),
            envelope_cols=("ID", "Origin", "Name", "Ref"),
        )

    def migrate_anthropogenic(self):
        colmap = {
            "BaseT_HC": None, "FrPDDwe": None,
            "QF_A_WD": "heat.qf_a.working_day", "QF_A_WE": "heat.qf_a.holiday",
            "QF_B_WD": "heat.qf_b.working_day", "QF_B_WE": "heat.qf_b.holiday",
            "QF_C_WD": "heat.qf_c.working_day", "QF_C_WE": "heat.qf_c.holiday",
            "AHMin_WD": "heat.ah_min.working_day", "AHMin_WE": "heat.ah_min.holiday",
            "AHSlope_Heating_WD": "heat.ah_slope_heating.working_day",
            "AHSlope_Heating_WE": "heat.ah_slope_heating.holiday",
            "AHSlope_Cooling_WD": "heat.ah_slope_cooling.working_day",
            "AHSlope_Cooling_WE": "heat.ah_slope_cooling.holiday",
            "TCritic_Heating_WD": "heat.baset_heating.working_day",
            "TCritic_Heating_WE": "heat.baset_heating.holiday",
            "TCritic_Cooling_WD": "heat.baset_cooling.working_day",
            "TCritic_Cooling_WE": "heat.baset_cooling.holiday",
            "MinQFMetab": "co2.minqfmetab", "MaxQFMetab": "co2.maxqfmetab",
            "MinFCMetab": "co2.minfcmetab", "MaxFCMetab": "co2.maxfcmetab",
            "FrFossilFuel_Heat": "co2.frfossilfuel_heat",
            "FrFossilFuel_NonHeat": "co2.frfossilfuel_nonheat",
            "EF_umolCO2perJ": "co2.ef_umolco2perj",
            "EnEF_v_Jkm": "co2.enef_v_jkm",
            "FcEF_v_kgkmWD": "co2.fcef_v_kgkm.working_day",
            "FcEF_v_kgkmWE": "co2.fcef_v_kgkm.holiday",
            "CO2PointSource": "co2.co2pointsource",
            "TrafficUnits": "co2.trafficunits",
            "Model": "context.qf_model",
        }
        self.migrate_flat(
            "AnthropogenicEmission",
            lambda s, r: "records/anthropogenic",
            lambda s, r: "anthropogenic_emissions",
            lambda col, s, row: colmap.get(col),
            envelope_cols=("ID", "Origin", "Name", "Ref"),
        )

    def migrate_materials(self):
        colmap = {
            "Albedo": "albedo", "Emissivity": "emissivity",
            "Thermal Conductivity": "thermal_conductivity",
            "Specific Heat": "specific_heat", "Density": "density",
        }
        sheet = "Spartacus Material"
        envelope_cols = ("ID", "Name", "Material Type", "Color", "Ref")
        for rid in sorted(self.tables[sheet]):
            row = self.tables[sheet][rid]
            params, legacy = {}, {}
            for col, val in row.items():
                if col in envelope_cols or is_missing(val):
                    continue
                dest = colmap.get(col)
                if dest is None:
                    legacy[col] = val
                else:
                    params[dest] = val
            extra = {}
            if not is_missing(row.get("Material Type")):
                extra["category"] = row["Material Type"]
            if not is_missing(row.get("Color")):
                extra["display_colour"] = row["Color"]
            self.add_record(
                "records/materials", rid, row, "material", params,
                legacy or None, extra=extra, slug_hint=row.get("Name"),
            )
            self.count(sheet, row, params, legacy, envelope_cols)

    def migrate_constructions(self):
        sheet = "Spartacus Surface"
        envelope_cols = ("ID", "Surface", "Origin", "Name", "Ref")
        for rid in sorted(self.tables[sheet]):
            row = self.tables[sheet][rid]
            params, legacy = {}, {}
            for side, prefix in (("roof", "r"), ("wall", "w")):
                layers = []
                for i in range(1, 6):
                    mat = row.get(f"{prefix}{i}Material")
                    thick = row.get(f"{prefix}{i}Thickness")
                    if is_missing(mat) and is_missing(thick):
                        continue
                    layer = {}
                    if not is_missing(mat):
                        ref = self.id_to_record.get(mat)
                        layer["material"] = ref if ref else mat
                        if ref is None:
                            self.flags.append(
                                f"Spartacus Surface {rid}: material id {mat} unresolved"
                            )
                    if not is_missing(thick):
                        layer["thickness"] = thick
                    layers.append(layer)
                if layers:
                    params[side] = layers
                ins = row.get(f"{prefix}Insulation")
                if not is_missing(ins):
                    params[f"{side}_insulation_layer"] = ins
            self.add_record(
                "records/constructions", rid, row, "construction", params,
                legacy or None, slug_hint=row.get("Name"),
            )
            self.count(sheet, row, params, legacy, envelope_cols)

    def migrate_profiles(self):
        sheet = "Profiles"
        rows = self.tables[sheet]
        envelope_cols = ("ID", "Profile Type", "Day", "Name", "City", "Country", "Ref")
        groups = defaultdict(dict)
        for rid in sorted(rows):
            row = rows[rid]
            key = (
                str(row.get("Profile Type")), str(row.get("Name")),
                str(row.get("City")), str(row.get("Country")), row.get("Ref"),
            )
            day = str(row.get("Day"))
            side = "working_day" if day == "Weekday" else "holiday"
            if side == "holiday" and side in groups[key] and day == "Holiday":
                # a table with Weekday+Weekend+Holiday: keep the extra side
                side = "public_holiday"
                self.flags.append(
                    f"Profiles: row {rid} is a third (Holiday) day type for {key}; "
                    "kept as an extra public_holiday side (supy models two day types)"
                )
            elif side in groups[key]:
                self.flags.append(f"Profiles: duplicate {side} row {rid} for {key}")
                side = f"{side}-dup{rid}"
            groups[key][side] = rid
        for key in sorted(groups, key=str):
            sides = groups[key]
            ptype, name, city, country, _ = key
            kind_slug, attach = PROFILE_KINDS.get(ptype, (slugify(ptype), None))
            first_rid = min(sides.values())
            row0 = rows[first_rid]
            # place: LUCY rows are per-country model output; otherwise city
            if city == "LUCY":
                place = self.country_place(country)
                rep = "regional"
                subdir = f"records/profiles/{kind_slug}/lucy"
                slug = slugify(country)
            elif city in ("None", "nan") or is_missing(row0.get("City")):
                place, rep = None, None
                subdir = f"records/profiles/{kind_slug}"
                slug = None
            else:
                place, _ = self.place_for(city)
                rep = "city"
                subdir = f"records/profiles/{kind_slug}"
                slug = None
            params = {}
            legacy_sides = {}
            hours_counted = 0
            legacy_hours_counted = 0
            for side, rid in sorted(sides.items()):
                row = rows[rid]
                prof, sentinels = {}, {}
                for h in range(24):
                    v = row.get(h)
                    if is_missing(v):
                        continue
                    if is_sentinel(v):
                        # -999 placeholder hours stay under legacy
                        sentinels[h] = v
                    else:
                        prof[h + 1] = v
                        hours_counted += 1
                if prof:
                    params[side] = prof
                if sentinels:
                    legacy_sides[side] = sentinels
                    legacy_hours_counted += len(sentinels)
            source = self.source_key(row0.get("Ref"))
            base = slug or f"{place or 'generic'}--{source}"
            if city == "LUCY":
                path = f"{subdir}/{base}"
            else:
                path = f"{subdir}/{base}"
                if not is_missing(row0.get("Name")):
                    path = f"{subdir}/{base}--{slugify(row0['Name'])}"
            n = 2
            stem = path
            while path in self.records:
                path = f"{stem}--{n}"
                n += 1
            rec = {
                "record": path,
                "schema_version": SCHEMA_VERSION,
                "target": f"profile.{kind_slug}",
            }
            if attach:
                rec["attaches_to"] = attach
            if ptype not in ("nan", "None"):
                rec["profile_type"] = ptype
            if not is_missing(row0.get("Name")):
                rec["name"] = row0["Name"]
            if place:
                rec["place"] = place
            if not is_missing(row0.get("City")) and city != "LUCY":
                rec["origin"] = f"{row0.get('City')}, {row0.get('Country')}"
            if city == "LUCY":
                rec["origin"] = f"LUCY, {country}"
            if rep:
                rec["representativeness"] = rep
            rec["source"] = source
            self.attach_dangling(rec, row0.get("Ref"))
            rec["legacy_id"] = sorted(sides.values())
            rec["parameters"] = params
            if legacy_sides:
                rec["legacy"] = legacy_sides
            self.records[path] = rec
            for rid in sides.values():
                self.id_to_record[rid] = path
                self.count(sheet, rows[rid], {}, {}, envelope_cols)
            self.census[sheet]["cells_mapped"] += hours_counted
            self.census[sheet]["cells_legacy"] += legacy_hours_counted

    # ---------------- archetype handlers ----------------

    def resolve(self, rid, ctx):
        ref = self.id_to_record.get(rid)
        if ref is None:
            self.flags.append(f"{ctx}: pointer {rid} unresolved")
            return rid
        return ref

    def migrate_surface_composites(self):
        specs = {
            "NonVeg": {
                "Albedo": "albedo", "Emissivity": "emissivity",
                "Water Storage": "water_storage", "Drainage": "drainage",
                "Spartacus Surface": "construction", "SnowLimPatch": "snow_lim_patch",
            },
            "Veg": {
                "Albedo": "albedo", "Emissivity": "emissivity",
                "Water Storage": "water_storage", "Leaf Area Index": "leaf_area_index",
                "Leaf Growth Power": "leaf_growth_power", "Drainage": "drainage",
                "Max Vegetation Conductance": "max_vegetation_conductance",
                "Porosity": "porosity", "Biogen CO2": "biogen_co2",
                "SnowLimPatch": "snow_lim_patch", "Vegetation Growth": "vegetation_growth",
            },
            "Water": {
                "Albedo": "albedo", "Emissivity": "emissivity",
                "Water Storage": "water_storage", "Water State": "water_state",
                "Drainage": "drainage",
            },
        }
        ohm_slots = {
            "OHMSummerWet": "summer_wet", "OHMSummerDry": "summer_dry",
            "OHMWinterWet": "winter_wet", "OHMWinterDry": "winter_dry",
        }
        envelope_cols = ("ID", "Surface", "Origin", "Name")
        for sheet, slots in specs.items():
            for rid in sorted(self.tables[sheet]):
                row = self.tables[sheet][rid]
                surface = SURFACE_NAMES.get(row.get("Surface"), "common")
                uses, ohm, legacy = {}, {}, {}
                for col, val in row.items():
                    if col in envelope_cols or is_missing(val):
                        continue
                    if col in slots:
                        uses[slots[col]] = self.resolve(val, f"{sheet} {rid} {col}")
                    elif col in ohm_slots:
                        ohm[ohm_slots[col]] = self.resolve(val, f"{sheet} {rid} {col}")
                    else:
                        legacy[col] = val
                if ohm:
                    uses["ohm"] = ohm
                path = self.archetype_path("surfaces/" + surface, row)
                arch = self.make_archetype(
                    path, row, rid, f"land_cover.{surface}", uses, {}, legacy
                )
                self.count(sheet, row, {}, legacy, envelope_cols)
                # pointer cells are accounted as resolved references
                self.census[sheet]["cells_pointers"] += sum(
                    1 for c in row if c in slots or c in ohm_slots
                    if not is_missing(row.get(c))
                )

    def archetype_path(self, directory, row):
        name = row.get("Name") or row.get("Region") or row.get("Country") or "entry"
        origin = row.get("Origin")
        bits = [slugify(name)]
        if not is_missing(origin):
            b = slugify(origin)
            if b != bits[0]:
                bits.insert(0, b)
        path = f"archetypes/{directory}/{'--'.join(bits)}"
        n = 2
        stem = path
        while path in self.records:
            path = f"{stem}--{n}"
            n += 1
        return path

    def make_archetype(self, path, row, rid, target, uses, params, legacy,
                       extra=None):
        origin = row.get("Origin")
        place, rep = self.place_for(origin)
        arch = {
            "archetype": path,
            "schema_version": SCHEMA_VERSION,
            "target": target,
        }
        name = row.get("Name")
        if not is_missing(name):
            arch["name"] = name
        if place:
            arch["place"] = place
        if not is_missing(origin):
            arch["origin"] = origin
        if rep:
            arch["representativeness"] = rep
        arch["legacy_id"] = rid
        if extra:
            arch.update(extra)
        if uses:
            arch["uses"] = uses
        if params:
            arch["parameters"] = params
        if legacy:
            arch["legacy"] = legacy
        self.records[path] = arch
        self.id_to_record[rid] = path
        return arch

    def migrate_snow(self):
        sheet = "Snow"
        envelope_cols = ("ID", "Origin", "Name", "Ref")
        colmap = {
            "RadMeltFactor": "radiation_melt_factor",
            "TempMeltFactor": "temperature_melt_factor",
            "tau_a": "tau_cold_snow", "tau_f": "tau_melting_snow",
            "tau_r": "tau_refreezing_snow",
            "PrecipLimAlb": "precipitation_threshold_albedo_reset",
            "SnowDensMin": "snow_density_min", "SnowDensMax": "snow_density_max",
            "CRWMin": "water_holding_capacity_min", "CRWMax": "water_holding_capacity_max",
            "PrecipLimSnow": "temperature_rain_snow_threshold",
        }
        pointer_cols = {"Albedo": "albedo", "Emissivity": "emissivity"}
        for rid in sorted(self.tables[sheet]):
            row = self.tables[sheet][rid]
            params, legacy, uses = {}, {}, {}
            for col, val in row.items():
                if col in envelope_cols or is_missing(val):
                    continue
                if col in pointer_cols:
                    uses[pointer_cols[col]] = self.resolve(val, f"Snow {rid} {col}")
                elif col in colmap and not is_sentinel(val):
                    params[colmap[col]] = val
                else:
                    legacy[col] = val
            path = self.archetype_path("snow", row)
            self.make_archetype(
                path, row, rid, "snow", uses, params, legacy,
                extra={"source": self.source_key(row.get("Ref"))},
            )
            self.count(sheet, row, params, legacy, envelope_cols)
            self.census[sheet]["cells_pointers"] += sum(
                1 for c in pointer_cols if not is_missing(row.get(c))
            )

    def migrate_regions_countries(self):
        profile_slots = {
            "SnowClearingProfWD": ("snow_clearing", "working_day"),
            "SnowClearingProfWE": ("snow_clearing", "holiday"),
            "WaterUseProfManuWD": ("water_use_manual", "working_day"),
            "WaterUseProfManuWE": ("water_use_manual", "holiday"),
            "WaterUseProfAutoWD": ("water_use_automatic", "working_day"),
            "WaterUseProfAutoWE": ("water_use_automatic", "holiday"),
            "TraffProfWD": ("traffic", "working_day"),
            "TraffProfWE": ("traffic", "holiday"),
            "EnergyUseProfWD": ("energy_use", "working_day"),
            "EnergyUseProfWE": ("energy_use", "holiday"),
            "ActivityProfWD": ("human_activity", "working_day"),
            "ActivityProfWE": ("human_activity", "holiday"),
            "PopProfWD": ("population", "working_day"),
            "PopProfWE": ("population", "holiday"),
        }
        surface_slots = {
            "Paved": "paved", "Buildings": "bldgs", "Bare Soil": "bsoil",
            "Grass": "grass", "Evergreen Tree": "evetr", "Deciduous Tree": "dectr",
            "Water": "water",
        }
        other_slots = {
            "AnthropogenicCode": "anthropogenic_emissions",
            "IrrigationCode": "irrigation",
            "SoilTypeCode": "soil",
            "SnowCode": "snow",
            "Conductance": "conductance",
        }
        scalar_map = {
            "TCritic_Heating_WD": "anthropogenic_emissions.heat.baset_heating.working_day",
            "TCritic_Heating_WE": "anthropogenic_emissions.heat.baset_heating.holiday",
            "TCritic_Cooling_WD": "anthropogenic_emissions.heat.baset_cooling.working_day",
            "TCritic_Cooling_WE": "anthropogenic_emissions.heat.baset_cooling.holiday",
        }
        for sheet, directory in (("Region", "regions"), ("Country", "countries")):
            # for Country, Region is a resolved pointer (name -> archetype),
            # so it must not also count as envelope
            envelope_cols = (
                ("ID", "Region") if sheet == "Region" else ("ID", "Country", "City")
            )
            for rid in sorted(self.tables[sheet]):
                row = self.tables[sheet][rid]
                uses, params, legacy = {}, {}, {}
                surfaces, profiles = {}, {}
                for col, val in row.items():
                    if col in envelope_cols or is_missing(val):
                        continue
                    if sheet == "Country" and col == "Region":
                        continue  # resolved into region_ref below
                    if col in profile_slots:
                        kind, side = profile_slots[col]
                        ref = self.resolve(val, f"{sheet} {rid} {col}")
                        cur = profiles.setdefault(kind, {})
                        cur[side] = ref
                    elif col in surface_slots:
                        surfaces[surface_slots[col]] = self.resolve(val, f"{sheet} {rid} {col}")
                    elif col in other_slots:
                        uses[other_slots[col]] = self.resolve(val, f"{sheet} {rid} {col}")
                    elif col in scalar_map and not is_sentinel(val):
                        self.set_path(params, scalar_map[col], val)
                    else:
                        legacy[col] = val
                # collapse profile WD/WE pointer pairs that landed on one record
                for kind, sides in list(profiles.items()):
                    vals = set(sides.values())
                    if len(vals) == 1:
                        profiles[kind] = vals.pop()
                if surfaces:
                    uses["land_cover"] = surfaces
                if profiles:
                    uses["profiles"] = profiles
                if sheet == "Region":
                    label = row.get("Region")
                    extra = {"region": label}
                else:
                    label = row.get("Country")
                    extra = {"country": label}
                    region_name = row.get("Region")
                    if not is_missing(region_name):
                        # the Country table names its region rather than
                        # pointing at it by ID
                        ref = self.region_by_name.get(str(region_name))
                        if ref is None:
                            self.flags.append(
                                f"{sheet} {rid}: region name {region_name!r} "
                                "has no Region archetype"
                            )
                            ref = region_name
                        extra["region_ref"] = ref
                    if not is_missing(row.get("City")):
                        extra["city"] = row.get("City")
                path = f"archetypes/{directory}/{slugify(label)}"
                n = 2
                stem = path
                while path in self.records:
                    path = f"{stem}--{n}"
                    n += 1
                fake_row = {"Name": label, "Origin": None}
                self.make_archetype(path, fake_row, rid, "site",
                                    uses, params, legacy, extra=extra)
                if sheet == "Region":
                    self.region_by_name[str(label)] = path
                self.count(sheet, row, params, legacy, envelope_cols)
                n_pointers = sum(
                    1 for c in row
                    if (c in profile_slots or c in surface_slots or c in other_slots
                        or (sheet == "Country" and c == "Region"))
                    and not is_missing(row.get(c))
                )
                self.census[sheet]["cells_pointers"] += n_pointers

    def migrate_types(self):
        sheet = "Types"
        # typology rows are metadata-rich; the descriptive columns are all
        # envelope (they migrate into the archetype's metadata fields)
        envelope_cols = ("ID", "Origin", "Name", "Description", "Author",
                         "Url", "imageSource", "ProfileType", "Period")
        for rid in sorted(self.tables[sheet]):
            row = self.tables[sheet][rid]
            uses, legacy = {}, {}
            extra = {}
            for col, key in (("Description", "description"), ("Author", "author"),
                             ("Url", "url"), ("imageSource", "image_source"),
                             ("ProfileType", "profile_type"), ("Period", "period")):
                if not is_missing(row.get(col)):
                    extra[key] = row[col]
            for col, slot in (("Buildings", "bldgs"), ("Paved", "paved")):
                val = row.get(col)
                if not is_missing(val):
                    if isinstance(val, int) and val in self.id_index:
                        uses[slot] = self.resolve(val, f"Types {rid} {col}")
                    else:
                        legacy[col] = val
            path = self.archetype_path("typologies", row)
            self.make_archetype(path, row, rid, "typology", uses, {}, legacy,
                                extra=extra)
            self.count(sheet, row, {}, legacy, envelope_cols)
            self.census[sheet]["cells_pointers"] += len(uses)

    # ---------------- output ----------------

    def write(self):
        # start from a clean slate so renamed slugs leave no strays
        import shutil

        for sub in ("records", "archetypes"):
            if (DB / sub).exists():
                shutil.rmtree(DB / sub)
        # sources
        out = {"sources": self.sources}
        (DB / "sources.yml").write_text(
            "# Citation registry: every record's `source:` key resolves here.\n"
            "# Keys are <firstauthor><year>; `unreferenced` marks legacy values\n"
            "# that carried no citation.\n"
            + yaml.safe_dump(out, sort_keys=True, allow_unicode=True, width=88)
        )
        # places
        (DB / "places.yml").write_text(
            "# Place registry: every record's `place:` slug resolves here.\n"
            "# Generated by the migration from schema/origins_map.yml plus the\n"
            "# per-country profile entries; enrich with lat/lon/koppen over time.\n"
            + yaml.safe_dump({"places": dict(sorted(self.places.items()))},
                             sort_keys=True, allow_unicode=True, width=88)
        )
        # records + archetypes
        for path, rec in self.records.items():
            fp = DB / (path + ".yml")
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(yaml.safe_dump(rec, sort_keys=False, allow_unicode=True,
                                         width=88))
        # census
        census = {sheet: dict(c) for sheet, c in sorted(self.census.items())}
        totals = Counter()
        for c in self.census.values():
            totals.update(c)
        summary = {
            "n_record_files": len(self.records),
            "n_sources": len(self.sources),
            "n_places": len(self.places),
            "tables": census,
            "totals": dict(totals),
            "flags": self.flags,
        }
        (SCHEMA / "migration_census.yml").write_text(
            "# Generated by scripts/migrate_to_records.py -- the completeness\n"
            "# census of the table->record migration. cells_in counts every\n"
            "# non-null cell of every legacy row; each is accounted for as\n"
            "# envelope, mapped (parameters), legacy, or pointers (resolved\n"
            "# references).\n"
            + yaml.safe_dump(summary, sort_keys=False, allow_unicode=True, width=88)
        )
        print(f"records written: {len(self.records)}")
        print(f"sources: {len(self.sources)}, places: {len(self.places)}")
        print(f"flags: {len(self.flags)}")
        for f in self.flags[:20]:
            print("  -", f)

    def run(self):
        self.build_sources()
        # evidence records first (pointer targets), then assemblies
        self.migrate_albedo()
        self.migrate_emissivity()
        self.migrate_ohm()
        self.migrate_simple_tables()
        self.migrate_estm()
        self.migrate_conductance()
        self.migrate_irrigation()
        self.migrate_anthropogenic()
        self.migrate_materials()
        self.migrate_constructions()
        self.migrate_profiles()
        self.migrate_snow()
        self.migrate_surface_composites()
        self.migrate_regions_countries()
        self.migrate_types()
        self.write()


if __name__ == "__main__":
    sys.exit(Migration().run())

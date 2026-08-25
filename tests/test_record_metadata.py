from copy import deepcopy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_db import (  # noqa: E402
    load_applicable_scales,
    load_all,
    load_urban_settings,
    structural_check,
    suews_configuration_fragment,
)
from export_record import assemble  # noqa: E402


BASE_RECORD = {
    "record": "records/test",
    "schema_version": "2026.5",
    "target": "land_cover.paved",
    "name": "Test paved surface",
    "place": "london",
    "representativeness": "site",
    "source": "paper",
    "parameters": {"alb": 0.12},
}
SOURCES = {"paper": {"doi": "10.0000/example"}}
PLACES = {"london": {"name": "London"}}


class UrbanSettingMetadataTests(unittest.TestCase):
    def errors_for_entry(self, path, entry):
        records, sources, places = load_all()
        records[path] = entry
        sources = {**sources, **SOURCES}
        places = {**places, **PLACES}
        return structural_check(records, sources, places)

    def errors_for(self, record):
        return self.errors_for_entry("records/test", record)

    def test_absent_and_registered_settings_are_valid(self):
        self.assertEqual(self.errors_for(deepcopy(BASE_RECORD)), [])
        for setting in load_urban_settings():
            with self.subTest(setting=setting):
                record = deepcopy(BASE_RECORD)
                record["urban_setting"] = setting
                self.assertEqual(self.errors_for(record), [])

                archetype = {
                    "archetype": "archetypes/test",
                    "schema_version": "2026.5",
                    "target": "land_cover.paved",
                    "name": "Test paved assembly",
                    "place": "london",
                    "representativeness": "site",
                    "urban_setting": setting,
                    "uses": {},
                }
                self.assertEqual(
                    self.errors_for_entry("archetypes/test", archetype), []
                )

    def test_unknown_and_non_string_settings_are_rejected(self):
        for setting in ("downtown", "other_urban", 3):
            with self.subTest(setting=setting):
                record = deepcopy(BASE_RECORD)
                record["urban_setting"] = setting
                errors = self.errors_for(record)
                self.assertTrue(
                    any("urban_setting" in error for error in errors), errors
                )

    def test_export_reference_description_includes_setting(self):
        record = deepcopy(BASE_RECORD)
        record["urban_setting"] = "city_centre"
        fragment = suews_configuration_fragment(record, SOURCES)
        self.assertEqual(
            fragment["alb"]["ref"]["desc"], "london, city_centre, site"
        )

    def test_absent_and_registered_applicable_scales_are_valid(self):
        self.assertEqual(self.errors_for(deepcopy(BASE_RECORD)), [])
        for scale in load_applicable_scales():
            with self.subTest(scale=scale):
                record = deepcopy(BASE_RECORD)
                record["applicable_scale"] = scale
                self.assertEqual(self.errors_for(record), [])

    def test_unknown_and_non_string_applicable_scales_are_rejected(self):
        for scale in ("plot", "city", 3):
            with self.subTest(scale=scale):
                record = deepcopy(BASE_RECORD)
                record["applicable_scale"] = scale
                errors = self.errors_for(record)
                self.assertTrue(
                    any("applicable_scale" in error for error in errors), errors
                )

    def test_export_reference_description_includes_applicable_scale(self):
        record = deepcopy(BASE_RECORD)
        record["applicable_scale"] = "material"
        fragment = suews_configuration_fragment(record, SOURCES)
        self.assertEqual(
            fragment["alb"]["ref"]["desc"], "london, site, material"
        )


class ParameterProvenanceTests(unittest.TestCase):
    def errors_for_entry(self, path, entry):
        records, sources, places = load_all()
        records[path] = entry
        sources = {**sources, **SOURCES}
        places = {**places, **PLACES}
        return structural_check(records, sources, places)

    def errors_for(self, record):
        return self.errors_for_entry("records/test", record)

    def test_valid_override_changes_only_the_named_leaf_reference(self):
        record = deepcopy(BASE_RECORD)
        record["parameters"] = {"alb": 0.12, "emis": 0.95}
        record["parameter_provenance"] = {
            "parameters.alb": {
                "source": "archive",
                "method": "assumed",
                "place": "generic",
                "representativeness": "generic",
                "applicable_scale": "land_cover",
            }
        }
        sources = {
            **SOURCES,
            "archive": {"doi": "10.0000/archive"},
        }
        places = {**PLACES, "generic": {"name": "Generic"}}
        records, repo_sources, repo_places = load_all()
        records["records/test"] = record
        self.assertEqual(
            structural_check(
                records,
                {**repo_sources, **sources},
                {**repo_places, **places},
            ),
            [],
        )

        fragment = suews_configuration_fragment(record, sources)
        self.assertEqual(fragment["alb"]["ref"]["ID"], "archive")
        self.assertEqual(
            fragment["alb"]["ref"]["desc"],
            "generic, generic, land_cover",
        )
        self.assertEqual(fragment["emis"]["ref"]["ID"], "paper")
        self.assertEqual(fragment["emis"]["ref"]["desc"], "london, site")

    def test_numeric_list_is_one_exportable_leaf(self):
        record = deepcopy(BASE_RECORD)
        record["parameters"] = {"dz": [0.1, 0.2]}
        record["parameter_provenance"] = {
            "parameters.dz": {"source": "paper"}
        }
        self.assertEqual(self.errors_for(record), [])
        fragment = suews_configuration_fragment(record, SOURCES)
        self.assertEqual(fragment["dz"]["value"], [0.1, 0.2])
        self.assertEqual(fragment["dz"]["ref"]["ID"], "paper")

    def test_source_bounds_are_validated_and_exported(self):
        record = deepcopy(BASE_RECORD)
        record["parameter_provenance"] = {
            "parameters.alb": {
                "source_bounds": {
                    "minimum": 0.12,
                    "maximum": 0.14,
                    "active_role": "minimum",
                }
            }
        }
        self.assertEqual(self.errors_for(record), [])
        fragment = suews_configuration_fragment(record, SOURCES)
        self.assertEqual(
            fragment["alb"]["ref"]["desc"],
            "london, site, source bounds 0.12–0.14 (active: minimum)",
        )

    def test_invalid_source_bounds_are_rejected(self):
        candidates = [
            [],
            {"minimum": 0.12, "maximum": 0.14},
            {"minimum": True, "maximum": 0.14, "active_role": "minimum"},
            {"minimum": 0.15, "maximum": 0.14, "active_role": "minimum"},
            {"minimum": 0.10, "maximum": 0.14, "active_role": "minimum"},
            {"minimum": 0.12, "maximum": 0.14, "active_role": "maximum"},
            {"minimum": 0.12, "maximum": 0.14, "active_role": "average"},
        ]
        for bounds in candidates:
            with self.subTest(bounds=bounds):
                record = deepcopy(BASE_RECORD)
                record["parameter_provenance"] = {
                    "parameters.alb": {"source_bounds": bounds}
                }
                errors = self.errors_for(record)
                self.assertTrue(any("source_bounds" in error for error in errors), errors)

    def test_archetype_assembly_preserves_child_field_reference(self):
        record = deepcopy(BASE_RECORD)
        record["parameters"] = {"alb": 0.12, "emis": 0.95}
        record["parameter_provenance"] = {
            "parameters.alb": {"source": "archive"}
        }
        archetype = {
            "archetype": "archetypes/test",
            "schema_version": "2026.5",
            "target": "land_cover.paved",
            "name": "Test assembly",
            "uses": {"albedo": "records/test"},
        }
        fragment = assemble(
            "archetypes/test",
            {"records/test": record, "archetypes/test": archetype},
            {**SOURCES, "archive": {"doi": "10.0000/archive"}},
        )
        self.assertEqual(fragment["alb"]["ref"]["ID"], "archive")
        self.assertEqual(fragment["emis"]["ref"]["ID"], "paper")

    def test_invalid_override_shapes_and_paths_are_rejected(self):
        candidates = [
            {},
            [],
            {"alb": {"source": "paper"}},
            {"parameters.missing": {"source": "paper"}},
            {"parameters.alb": {}},
            {"parameters.alb": "paper"},
            {"parameters.alb": {"source": None}},
        ]
        for overrides in candidates:
            with self.subTest(overrides=overrides):
                record = deepcopy(BASE_RECORD)
                record["parameter_provenance"] = overrides
                errors = self.errors_for(record)
                self.assertTrue(
                    any("parameter_provenance" in error for error in errors),
                    errors,
                )

    def test_non_exportable_leaves_are_rejected(self):
        values = [
            {"context": {"mode": 2}},
            {"label": "summer"},
            {"enabled": True},
            {"working_day": {1: 0.5}},
            {"nested": {"value": 1}},
        ]
        paths = [
            "parameters.context.mode",
            "parameters.label",
            "parameters.enabled",
            "parameters.working_day.1",
            "parameters.nested",
        ]
        for parameters, path in zip(values, paths):
            with self.subTest(path=path):
                record = deepcopy(BASE_RECORD)
                record["parameters"] = parameters
                record["parameter_provenance"] = {
                    path: {"source": "paper"}
                }
                errors = self.errors_for(record)
                self.assertTrue(
                    any("not an exportable parameter leaf" in error for error in errors),
                    errors,
                )

    def test_unknown_metadata_is_rejected(self):
        invalid = {
            "source": "missing-source",
            "method": "modelled",
            "place": "missing-place",
            "representativeness": "hemispheric",
            "urban_setting": "downtown",
            "applicable_scale": "plot",
            "role": "parameter_source",
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                record = deepcopy(BASE_RECORD)
                record["parameter_provenance"] = {
                    "parameters.alb": {field: value}
                }
                errors = self.errors_for(record)
                self.assertTrue(
                    any("parameter_provenance" in error for error in errors),
                    errors,
                )

    def test_archetype_cannot_declare_parameter_provenance(self):
        archetype = {
            "archetype": "archetypes/test",
            "schema_version": "2026.5",
            "target": "land_cover.paved",
            "name": "Test paved assembly",
            "uses": {},
            "parameters": {"alb": 0.12},
            "parameter_provenance": {
                "parameters.alb": {"source": "paper"}
            },
        }
        errors = self.errors_for_entry("archetypes/test", archetype)
        self.assertTrue(any("allowed only" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()

from copy import deepcopy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_db import (  # noqa: E402
    load_all,
    load_urban_settings,
    structural_check,
    suews_configuration_fragment,
)


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


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_db import load_all  # noqa: E402
from export_record import assemble  # noqa: E402


class ExportCompatibilityTests(unittest.TestCase):
    CASES = {
        "records/surfaces/grass/helsinki--jarvi2014--phenology": (
            "export-grass-phenology.yml"
        ),
        "archetypes/surfaces/bldgs/helsinki--kumpula": (
            "export-helsinki-kumpula.yml"
        ),
    }

    def test_representative_exports_are_byte_equivalent(self):
        for record, golden_name in self.CASES.items():
            with self.subTest(record=record):
                result = subprocess.run(
                    [sys.executable, "scripts/export_record.py", record],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                )
                # export_record.py uses print() around a newline-terminated
                # YAML dump, so stdout has one intentional extra final newline.
                expected = (
                    ROOT / "tests/golden" / golden_name
                ).read_bytes() + b"\n"
                self.assertEqual(result.stdout, expected)

    def test_check_db_import_keeps_provenance_dependencies_lazy(self):
        code = (
            "import pathlib,sys; "
            "sys.path.insert(0, str(pathlib.Path('scripts').resolve())); "
            "import check_db; "
            "assert 'provenance' not in sys.modules; "
            "assert len(check_db.load_all()) == 3"
        )
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )

    def test_region_export_includes_nested_surfaces_and_profiles(self):
        records, sources, _ = load_all()
        fragment = assemble(
            "archetypes/regions/southern-south-america", records, sources
        )

        self.assertEqual(
            set(fragment["land_cover"]),
            {"paved", "bldgs", "bsoil", "grass", "evetr", "dectr", "water"},
        )
        self.assertIn("snow_profile_24hr", fragment["snow"])
        self.assertIn("working_day", fragment["irrigation"]["wuprofm_24hr"])
        self.assertIn("holiday", fragment["irrigation"]["wuprofa_24hr"])

    def test_country_export_inherits_region_and_attaches_local_profiles(self):
        records, sources, _ = load_all()
        fragment = assemble("archetypes/countries/ecuador", records, sources)

        self.assertIn("land_cover", fragment)
        self.assertIn("snow", fragment)
        self.assertIn(
            "traffprof_24hr", fragment["anthropogenic_emissions"]["co2"]
        )
        self.assertIn(
            "humactivity_24hr", fragment["anthropogenic_emissions"]["co2"]
        )


if __name__ == "__main__":
    unittest.main()

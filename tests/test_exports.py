from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    unittest.main()

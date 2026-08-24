from pathlib import Path
from unittest import mock
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from release_bundle import (  # noqa: E402
    BundleError,
    LICENCE_PATH,
    MANIFEST_PATH,
    _zip_info,
    build_bundle,
    decode_data,
    verify_bundle,
)


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.db = self.base / "db"
        (self.db / "records/profiles").mkdir(parents=True)
        (self.db / "records/profiles/example.yml").write_text(
            """record: records/profiles/example
schema_version: 2026.5
target: profile
parameters:
  hours:
    1: integer one
    "1": string one
    "$int:1": reserved integer prefix
    "$str:key": reserved string prefix
"""
        )
        self.licence = self.base / "LICENSE"
        self.licence.write_text("fixture data licence\n")
        self.bundle = self.base / "bundle.zip"
        build_bundle(self.bundle, db_dir=self.db, licence_path=self.licence)

    def tearDown(self):
        self.temporary.cleanup()

    def _rewrite(self, output, *, remove=None, change=None, extra=None):
        with zipfile.ZipFile(self.bundle) as source:
            members = [
                (info.filename, source.read(info.filename))
                for info in source.infolist()
            ]
        with zipfile.ZipFile(output, "w") as target:
            for path, content in members:
                if path == remove:
                    continue
                if path == change:
                    content = b"[" + content[1:]
                target.writestr(_zip_info(path), content)
            if extra is not None:
                target.writestr(_zip_info(extra), b"{}\n")

    def test_build_is_byte_identical_and_verifies_from_a_copy(self):
        second = self.base / "second.zip"
        build_bundle(second, db_dir=self.db, licence_path=self.licence)
        self.assertEqual(self.bundle.read_bytes(), second.read_bytes())

        copied = self.base / "elsewhere" / "copied.zip"
        copied.parent.mkdir()
        shutil.copyfile(self.bundle, copied)
        original_import = __import__

        def no_yaml(name, *args, **kwargs):
            if name == "yaml":
                raise AssertionError("offline verification imported PyYAML")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=no_yaml):
            manifest = verify_bundle(copied)
        self.assertEqual(manifest["bundle_format_version"], 1)

        result = subprocess.run(
            [sys.executable, "scripts/release_bundle.py", "verify", str(copied)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("(1 data files)", result.stdout)

    def test_integer_and_string_keys_round_trip_without_collision(self):
        verify_bundle(self.bundle)
        with zipfile.ZipFile(self.bundle) as archive:
            encoded = json.loads(archive.read("db/records/profiles/example.json"))
        hours = decode_data(encoded)["parameters"]["hours"]
        self.assertEqual(
            {(type(key), key): value for key, value in hours.items()},
            {
                (int, 1): "integer one",
                (str, "1"): "string one",
                (str, "$int:1"): "reserved integer prefix",
                (str, "$str:key"): "reserved string prefix",
            },
        )
        self.assertEqual(
            set(encoded["parameters"]["hours"]),
            {"$int:1", "1", "$str:$int:1", "$str:$str:key"},
        )

    def test_duplicate_yaml_key_fails_instead_of_being_overwritten(self):
        duplicate = self.db / "records/profiles/example.yml"
        duplicate.write_text("values:\n  1: first\n  1: second\n")
        with self.assertRaisesRegex(BundleError, "duplicate mapping key 1"):
            build_bundle(
                self.base / "duplicate.zip",
                db_dir=self.db,
                licence_path=self.licence,
            )

    def test_corrupted_member_fails(self):
        changed = self.base / "changed.zip"
        self._rewrite(changed, change="db/records/profiles/example.json")
        with self.assertRaisesRegex(BundleError, "does not match manifest"):
            verify_bundle(changed)

    def test_missing_member_fails(self):
        missing = self.base / "missing.zip"
        self._rewrite(missing, remove="db/records/profiles/example.json")
        with self.assertRaisesRegex(BundleError, "archive member mismatch"):
            verify_bundle(missing)

    def test_extra_member_fails(self):
        extra = self.base / "extra.zip"
        self._rewrite(extra, extra="db/extra.json")
        with self.assertRaisesRegex(BundleError, "archive member mismatch"):
            verify_bundle(extra)

    def test_manifest_hash_and_size_are_checked(self):
        with zipfile.ZipFile(self.bundle) as source:
            members = {
                info.filename: source.read(info.filename) for info in source.infolist()
            }
        manifest = json.loads(members[MANIFEST_PATH])
        manifest["files"][0]["size"] += 1
        members[MANIFEST_PATH] = (
            json.dumps(
                manifest,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()
        changed = self.base / "bad-manifest.zip"
        with zipfile.ZipFile(changed, "w") as target:
            paths = [
                MANIFEST_PATH,
                LICENCE_PATH,
                "db/records/profiles/example.json",
            ]
            for path in paths:
                target.writestr(_zip_info(path), members[path])
        with self.assertRaisesRegex(BundleError, "size does not match manifest"):
            verify_bundle(changed)


class RepositoryBundleCoverageTests(unittest.TestCase):
    def test_every_canonical_yaml_has_exactly_one_json_counterpart(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "repository.zip"
            build_bundle(bundle)
            manifest = verify_bundle(bundle)
            data_entries = [
                item for item in manifest["files"] if item["type"] == "data"
            ]
            expected_sources = {
                "db/" + path.relative_to(ROOT / "db").as_posix()
                for path in (ROOT / "db").rglob("*.yml")
            }
            self.assertEqual(
                {item["source_path"] for item in data_entries}, expected_sources
            )
            self.assertEqual(len(data_entries), len(expected_sources))


if __name__ == "__main__":
    unittest.main()

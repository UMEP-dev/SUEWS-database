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
    _archive_bytes,
    _file_entry,
    _json_bytes,
    _safe_member_path,
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

    def _mutate_first_central_field(self, output, offset, width, value):
        content = bytearray(self.bundle.read_bytes())
        central = content.index(b"PK\x01\x02")
        content[central + offset : central + offset + width] = value.to_bytes(
            width, "little"
        )
        output.write_bytes(content)

    def _mutate_first_local_field(self, output, offset, width, value):
        content = bytearray(self.bundle.read_bytes())
        local = content.index(b"PK\x03\x04")
        content[local + offset : local + offset + width] = value.to_bytes(
            width, "little"
        )
        output.write_bytes(content)

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

    def test_license_line_endings_do_not_change_archive_bytes(self):
        self.licence.write_bytes(b"fixture data licence\r\nsecond line\r\n")
        crlf_bundle = self.base / "crlf.zip"
        build_bundle(crlf_bundle, db_dir=self.db, licence_path=self.licence)
        self.licence.write_bytes(b"fixture data licence\nsecond line\n")
        lf_bundle = self.base / "lf.zip"
        build_bundle(lf_bundle, db_dir=self.db, licence_path=self.licence)
        self.assertEqual(crlf_bundle.read_bytes(), lf_bundle.read_bytes())
        with zipfile.ZipFile(crlf_bundle) as archive:
            self.assertEqual(
                archive.read(LICENCE_PATH), b"fixture data licence\nsecond line\n"
            )

    def test_windows_traversal_and_control_paths_are_rejected(self):
        for path in (
            "db\\..\\evil.json",
            "db/records/evil\\name.json",
            "db/records/control\nname.json",
            "db/records/trailing-dot./value.json",
            "db/CON/value.json",
        ):
            with self.subTest(path=path), self.assertRaises(BundleError):
                _safe_member_path(path)

    def test_portably_normalized_member_collision_fails(self):
        with zipfile.ZipFile(self.bundle) as source:
            payloads = {
                info.filename: source.read(info.filename)
                for info in source.infolist()
            }
        lower_path = "db/records/profiles/example.json"
        upper_path = "db/records/profiles/EXAMPLE.json"
        payloads[upper_path] = payloads[lower_path]
        manifest = json.loads(payloads[MANIFEST_PATH])
        manifest["files"].append(
            _file_entry(
                upper_path,
                payloads[upper_path],
                kind="data",
                source_path="db/records/profiles/EXAMPLE.yml",
            )
        )
        manifest["files"] = [manifest["files"][0]] + sorted(
            manifest["files"][1:], key=lambda item: item["path"]
        )
        payloads[MANIFEST_PATH] = _json_bytes(manifest)
        names = [MANIFEST_PATH, LICENCE_PATH, upper_path, lower_path]
        collision = self.base / "normalized-collision.zip"
        collision.write_bytes(_archive_bytes(names, payloads))
        with self.assertRaisesRegex(BundleError, "portable normalization"):
            verify_bundle(collision)

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

    def test_prefixed_polyglot_bytes_fail(self):
        prefixed = self.base / "prefixed.zip"
        prefixed.write_bytes(b"#!/bin/sh\n" + self.bundle.read_bytes())
        with self.assertRaisesRegex(BundleError, "archive bytes are not canonical"):
            verify_bundle(prefixed)

    def test_trailing_bytes_fail(self):
        trailing = self.base / "trailing.zip"
        trailing.write_bytes(self.bundle.read_bytes() + b"unexpected trailer")
        with self.assertRaisesRegex(BundleError, "archive bytes are not canonical"):
            verify_bundle(trailing)

    def test_noncanonical_zip_flags_fail(self):
        flags = self.base / "flags.zip"
        self._mutate_first_central_field(flags, 8, 2, 0x0008)
        with self.assertRaisesRegex(BundleError, "unexpected ZIP flags"):
            verify_bundle(flags)

    def test_noncanonical_local_header_flags_fail(self):
        flags = self.base / "local-flags.zip"
        self._mutate_first_local_field(flags, 6, 2, 0x0008)
        with self.assertRaisesRegex(BundleError, "archive bytes are not canonical"):
            verify_bundle(flags)

    def test_noncanonical_external_attributes_fail(self):
        attributes = self.base / "attributes.zip"
        self._mutate_first_central_field(attributes, 38, 4, 0)
        with self.assertRaisesRegex(BundleError, "unexpected ZIP permissions"):
            verify_bundle(attributes)

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
    def test_data_license_is_pinned_to_lf_in_git(self):
        attributes = (ROOT / ".gitattributes").read_text().splitlines()
        self.assertIn("LICENSE text eol=lf", attributes)

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

#!/usr/bin/env python3
"""Build and verify the deterministic offline JSON release bundle.

Verification intentionally uses only the Python standard library.  PyYAML is
imported only by the build path, so a copied archive can be verified without
network access or access to the source ``db/`` tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db"
DATA_LICENCE = ROOT / "LICENSE"
DEFAULT_BUNDLE = ROOT / "dist" / "suews-database-json.zip"

BUNDLE_FORMAT_VERSION = 1
MANIFEST_PATH = "manifest.json"
LICENCE_PATH = "LICENSE"
INTEGER_KEY_PREFIX = "$int:"
STRING_ESCAPE_PREFIX = "$str:"
KEY_ENCODING = {
    "integer": "$int:<canonical base-10 integer>",
    "reserved_string": "$str:<original string key>",
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ZIP_MODE = 0o100644
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INTEGER_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")


class BundleError(ValueError):
    """The source data or release archive violates the bundle contract."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _encode_key(key: object) -> str:
    if type(key) is int:
        return INTEGER_KEY_PREFIX + str(key)
    if type(key) is not str:
        raise BundleError(
            f"mapping key {key!r} is neither a string nor an integer"
        )
    if key.startswith((INTEGER_KEY_PREFIX, STRING_ESCAPE_PREFIX)):
        return STRING_ESCAPE_PREFIX + key
    return key


def _decode_key(key: str) -> str | int:
    if key.startswith(STRING_ESCAPE_PREFIX):
        return key.removeprefix(STRING_ESCAPE_PREFIX)
    if key.startswith(INTEGER_KEY_PREFIX):
        spelling = key.removeprefix(INTEGER_KEY_PREFIX)
        if not INTEGER_RE.fullmatch(spelling):
            raise BundleError(f"invalid encoded integer mapping key {key!r}")
        return int(spelling)
    return key


def encode_data(value: object, active: set[int] | None = None) -> object:
    """Project parsed YAML into collision-free JSON-compatible data."""
    active = active if active is not None else set()
    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            raise BundleError("recursive YAML mappings are not supported")
        active.add(identity)
        encoded = {}
        try:
            for key, item in value.items():
                encoded_key = _encode_key(key)
                if encoded_key in encoded:
                    raise BundleError(
                        "mapping keys collide after JSON encoding: "
                        f"{encoded_key!r}"
                    )
                encoded[encoded_key] = encode_data(item, active)
        finally:
            active.remove(identity)
        return encoded
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise BundleError("recursive YAML sequences are not supported")
        active.add(identity)
        try:
            return [encode_data(item, active) for item in value]
        finally:
            active.remove(identity)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise BundleError(f"value {value!r} is not JSON-compatible")


def decode_data(value: object) -> object:
    """Reverse :func:`encode_data`, retaining integer mapping keys."""
    if isinstance(value, dict):
        decoded = {}
        seen = set()
        for key, item in value.items():
            decoded_key = _decode_key(key)
            identity = (type(decoded_key), decoded_key)
            if identity in seen:
                raise BundleError(
                    "mapping keys collide after JSON decoding: "
                    f"{decoded_key!r}"
                )
            seen.add(identity)
            decoded[decoded_key] = decode_data(item)
        return decoded
    if isinstance(value, list):
        return [decode_data(item) for item in value]
    return value


def _json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BundleError(f"cannot serialize canonical JSON: {exc}") from exc


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _parse_json(content: bytes, path: str) -> object:
    try:
        return json.loads(
            content.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys
        )
    except (BundleError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"{path}: invalid JSON: {exc}") from exc


def _load_yaml(content: bytes, path: str) -> object:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised by build CLI setup
        raise BundleError("building the bundle requires PyYAML") from exc

    class StrictSafeLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            if not isinstance(node, yaml.MappingNode):
                raise yaml.constructor.ConstructorError(
                    None, None, "expected a mapping node", node.start_mark
                )
            self.flatten_mapping(node)
            mapping = {}
            seen = set()
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if type(key) not in (str, int):
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"unsupported mapping key {key!r}; expected string or integer",
                        key_node.start_mark,
                    )
                identity = (type(key), key)
                if identity in seen:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"duplicate mapping key {key!r}",
                        key_node.start_mark,
                    )
                seen.add(identity)
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping

    try:
        return yaml.load(content.decode("utf-8"), Loader=StrictSafeLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BundleError(f"{path}: invalid YAML: {exc}") from exc


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = ZIP_MODE << 16
    return info


def _file_entry(path: str, content: bytes, *, kind: str, source_path=None):
    entry = {
        "path": path,
        "sha256": _sha256(content),
        "size": len(content),
        "type": kind,
    }
    if source_path is not None:
        entry["source_path"] = source_path
    return entry


def build_bundle(
    output: Path = DEFAULT_BUNDLE,
    *,
    db_dir: Path = DB,
    licence_path: Path = DATA_LICENCE,
) -> Path:
    """Build the archive atomically and return its path."""
    yaml_files = sorted(db_dir.rglob("*.yml"))
    if not yaml_files:
        raise BundleError(f"no canonical YAML files found under {db_dir}")

    payloads = {}
    entries = []
    licence = licence_path.read_bytes()
    payloads[LICENCE_PATH] = licence
    entries.append(_file_entry(LICENCE_PATH, licence, kind="license"))

    for source in yaml_files:
        relative = source.relative_to(db_dir)
        source_path = PurePosixPath("db", *relative.parts).as_posix()
        output_path = str(PurePosixPath(source_path).with_suffix(".json"))
        parsed = _load_yaml(source.read_bytes(), source_path)
        if not isinstance(parsed, dict):
            raise BundleError(f"{source_path}: top-level value must be a mapping")
        content = _json_bytes(encode_data(parsed))
        payloads[output_path] = content
        entries.append(
            _file_entry(
                output_path, content, kind="data", source_path=source_path
            )
        )

    manifest = {
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "files": entries,
        "key_encoding": KEY_ENCODING,
    }
    manifest_content = _json_bytes(manifest)
    member_order = [MANIFEST_PATH, LICENCE_PATH] + sorted(
        path for path in payloads if path != LICENCE_PATH
    )

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent, prefix=f".{output.name}.", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w") as archive:
            for path in member_order:
                content = manifest_content if path == MANIFEST_PATH else payloads[path]
                archive.writestr(_zip_info(path), content)
        temporary_path.replace(output)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return output


def _safe_member_path(path: object) -> str:
    if not isinstance(path, str) or not path:
        raise BundleError("manifest file paths must be non-empty strings")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or parsed.as_posix() != path:
        raise BundleError(f"non-canonical archive path {path!r}")
    if any(part in ("", ".", "..") for part in parsed.parts):
        raise BundleError(f"unsafe archive path {path!r}")
    return path


def _validate_manifest(manifest: object):
    if not isinstance(manifest, dict) or set(manifest) != {
        "bundle_format_version",
        "files",
        "key_encoding",
    }:
        raise BundleError("manifest.json has an unexpected top-level structure")
    if manifest["bundle_format_version"] != BUNDLE_FORMAT_VERSION:
        raise BundleError("manifest.json uses an unsupported bundle format version")
    if manifest["key_encoding"] != KEY_ENCODING:
        raise BundleError("manifest.json declares an unsupported key encoding")
    if not isinstance(manifest["files"], list):
        raise BundleError("manifest.json files must be an array")

    paths = set()
    sources = set()
    entries = []
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise BundleError("manifest file entries must be objects")
        kind = entry.get("type")
        expected_keys = {"path", "sha256", "size", "type"}
        if kind == "data":
            expected_keys.add("source_path")
        if set(entry) != expected_keys:
            raise BundleError(f"manifest file entry has unexpected fields: {entry!r}")
        path = _safe_member_path(entry["path"])
        if path in paths or path == MANIFEST_PATH:
            raise BundleError(f"duplicate or reserved manifest path {path!r}")
        paths.add(path)
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise BundleError(f"{path}: manifest size must be a non-negative integer")
        if not isinstance(entry["sha256"], str) or not SHA256_RE.fullmatch(
            entry["sha256"]
        ):
            raise BundleError(f"{path}: manifest sha256 is invalid")
        if kind == "license":
            if path != LICENCE_PATH:
                raise BundleError("the license entry must be named LICENSE")
        elif kind == "data":
            source = _safe_member_path(entry["source_path"])
            if not source.startswith("db/") or not source.endswith(".yml"):
                raise BundleError(f"{path}: invalid canonical source path {source!r}")
            expected_path = str(PurePosixPath(source).with_suffix(".json"))
            if path != expected_path:
                raise BundleError(
                    f"{path}: expected JSON counterpart path {expected_path!r}"
                )
            if source in sources:
                raise BundleError(f"duplicate canonical source path {source!r}")
            sources.add(source)
        else:
            raise BundleError(f"{path}: unknown manifest file type {kind!r}")
        entries.append(entry)
    if sum(entry["type"] == "license" for entry in entries) != 1:
        raise BundleError("manifest must contain exactly one data license")
    if not any(entry["type"] == "data" for entry in entries):
        raise BundleError("manifest contains no data files")
    expected_order = [LICENCE_PATH] + sorted(
        entry["path"] for entry in entries if entry["type"] == "data"
    )
    if [entry["path"] for entry in entries] != expected_order:
        raise BundleError("manifest file entries are not in deterministic order")
    return entries


def _validate_zip_info(info: zipfile.ZipInfo):
    if info.is_dir():
        raise BundleError(f"unexpected directory entry {info.filename!r}")
    if info.date_time != ZIP_TIMESTAMP:
        raise BundleError(f"{info.filename}: non-deterministic ZIP timestamp")
    if info.compress_type != zipfile.ZIP_STORED:
        raise BundleError(f"{info.filename}: unsupported ZIP compression")
    if info.create_system != 3 or (info.external_attr >> 16) != ZIP_MODE:
        raise BundleError(f"{info.filename}: unexpected ZIP permissions")
    if info.extra or info.comment:
        raise BundleError(f"{info.filename}: unexpected ZIP metadata")
    if info.flag_bits & 0x1:
        raise BundleError(f"{info.filename}: encrypted entries are not supported")


def verify_bundle(bundle: Path) -> dict:
    """Verify an archive using no repository files and return its manifest."""
    try:
        with zipfile.ZipFile(bundle, "r") as archive:
            if archive.comment:
                raise BundleError("the archive has an unexpected comment")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise BundleError("archive contains duplicate member names")
            if not names or names[0] != MANIFEST_PATH:
                raise BundleError("manifest.json must be the first archive member")
            for info in infos:
                _validate_zip_info(info)

            manifest_content = archive.read(MANIFEST_PATH)
            manifest = _parse_json(manifest_content, MANIFEST_PATH)
            if _json_bytes(manifest) != manifest_content:
                raise BundleError("manifest.json is not canonical JSON")
            entries = _validate_manifest(manifest)
            expected_names = [MANIFEST_PATH, LICENCE_PATH] + sorted(
                entry["path"] for entry in entries if entry["type"] == "data"
            )
            if names != expected_names:
                missing = sorted(set(expected_names) - set(names))
                extra = sorted(set(names) - set(expected_names))
                raise BundleError(
                    f"archive member mismatch (missing={missing}, extra={extra}, "
                    "or non-deterministic order)"
                )

            for entry in entries:
                path = entry["path"]
                content = archive.read(path)
                if len(content) != entry["size"]:
                    raise BundleError(f"{path}: size does not match manifest")
                if _sha256(content) != entry["sha256"]:
                    raise BundleError(f"{path}: sha256 does not match manifest")
                if entry["type"] == "data":
                    encoded = _parse_json(content, path)
                    if _json_bytes(encoded) != content:
                        raise BundleError(f"{path}: data is not canonical JSON")
                    decoded = decode_data(encoded)
                    if not isinstance(decoded, dict):
                        raise BundleError(f"{path}: top-level data must be a mapping")
                    if encode_data(decoded) != encoded:
                        raise BundleError(f"{path}: mapping-key encoding is not canonical")
            return manifest
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise BundleError(f"cannot read bundle {bundle}: {exc}") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build the deterministic archive")
    build.add_argument("--output", type=Path, default=DEFAULT_BUNDLE)
    verify = subparsers.add_parser("verify", help="verify an archive offline")
    verify.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            output = build_bundle(args.output)
            print(f"built {output}")
        else:
            manifest = verify_bundle(args.bundle)
            count = sum(item["type"] == "data" for item in manifest["files"])
            print(f"verified {args.bundle} ({count} data files)")
    except (BundleError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

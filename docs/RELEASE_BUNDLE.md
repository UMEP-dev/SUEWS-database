# JSON release bundle

The release bundle is the portable, offline form of the canonical YAML data
under `db/`. It contains one JSON counterpart for every `db/**/*.yml` file,
the repository data licence, and a machine-readable manifest.

## Build and verify

```sh
make release-bundle
make verify-release-bundle BUNDLE=dist/suews-database-json.zip
```

`release-bundle` writes `dist/suews-database-json.zip` by default. `BUNDLE`
may select another output path for either target. Verification reads only the
archive named by `BUNDLE`: it does not inspect the checkout's `db/` tree,
import PyYAML, or use the network. A copy can therefore be checked from any
local path:

```sh
cp dist/suews-database-json.zip /tmp/database-copy.zip
make verify-release-bundle BUNDLE=/tmp/database-copy.zip
```

## Archive contract (format version 1)

The archive contains, in this fixed order:

1. `manifest.json`;
2. `LICENSE`, an LF-normalized copy of the repository's CC BY 4.0 data licence;
3. the sorted JSON counterparts of all canonical YAML inputs.

An input such as `db/records/ohm/example.yml` becomes
`db/records/ohm/example.json`. There is exactly one data member for every
manifest `source_path`, and its `path` is mechanically obtained by replacing
`.yml` with `.json`. No other archive members are permitted.

Path components use only ASCII letters, digits, `.`, `_`, and `-`, separated
by `/`. Backslashes, control characters, trailing dots, and Windows device
names are forbidden. Paths must also be unique after Unicode compatibility
normalization and case-folding, preventing two members from collapsing to one
name on a case-insensitive filesystem.

`manifest.json` declares `bundle_format_version`, the mapping-key encoding,
and a `files` array. Every file entry records its relative archive `path`,
byte `size`, lowercase hexadecimal `sha256`, and `type` (`data` or `license`).
Data entries additionally record the canonical YAML `source_path`. The
manifest does not hash itself.

All JSON is UTF-8, compact, key-sorted, and terminated by one newline. JSON
objects cannot natively distinguish the YAML integer key `1` from the string
key `"1"`, so the conversion uses this reversible mapping-key encoding:

- integer keys become `$int:<canonical base-10 integer>`;
- string keys normally remain unchanged;
- string keys beginning `$int:` or `$str:` become `$str:<original key>`.

For example, integer `1`, string `"1"`, and string `"$int:1"` become the three
distinct JSON keys `"$int:1"`, `"1"`, and `"$str:$int:1"`. The builder rejects
duplicate YAML keys and mapping-key types other than strings and integers
instead of silently overwriting or stringifying them. The verifier decodes and
re-encodes every JSON file to check that this representation is canonical.

ZIP members use the stored (uncompressed) method, the timestamp
`1980-01-01 00:00:00`, regular-file mode `0644`, no comments or extra fields,
and a fixed order. These choices avoid compressor- and clock-dependent bytes:
two builds from unchanged inputs are byte-identical. The repository also pins
`LICENSE` to LF line endings, and the builder normalizes its bytes defensively,
so Windows checkout settings do not change the archive.

## Verification failures

Verification fails for duplicate, missing, extra, reordered, encrypted, or
non-canonical members; unexpected ZIP metadata; malformed or unsupported
manifests; size or SHA-256 mismatches; invalid JSON; duplicate JSON object
keys; invalid or non-canonical mapping-key encodings; non-portable paths or
normalized path collisions; and any byte-level ZIP representation other than
the canonical archive reconstructed from the verified members.

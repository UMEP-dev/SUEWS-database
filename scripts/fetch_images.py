#!/usr/bin/env python3
"""Stage the typology photographs for their release, from their origins.

The image files live as individual assets on the release named in
db/images.yml, not in this repository. This script is how that release is
built and rebuilt: it downloads each image from the origin recorded in the
manifest, checks it against the recorded sha256, and leaves the files in a
local staging directory ready to upload.

The site build never uses this script. scripts/build_site.py takes its copies
from the release, so the published site does not depend on any upstream host
staying alive; this script is the one place those hosts are contacted.

Usage:
  python scripts/fetch_images.py            # stage and verify
  python scripts/fetch_images.py --hashes   # stage, then print sha256/bytes
                                            # lines for a manifest whose
                                            # entries are still PENDING
  python scripts/fetch_images.py --upload   # print the gh command to upload
                                            # the staged files to the release
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "db" / "images.yml"
STAGE = ROOT / ".image-stage"

# Wikimedia refuses the default urllib agent; identify the project instead of
# pretending to be a browser.
AGENT = ("SUEWS-database-image-staging/1.0 "
         "(https://github.com/UMEP-dev/SUEWS-database)")

PENDING = "PENDING"


def load_manifest():
    return yaml.safe_load(MANIFEST.read_text())


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def jpeg_size(data):
    """Pixel dimensions from the JPEG frame header, stdlib only.

    The manifest records these so the page reserves the right box before the
    image arrives. Getting them from the file rather than from what a
    thumbnail URL appears to promise is the point: an upstream that serves a
    1280px rendition for a 1024px request would otherwise leave every page
    reflowing as its photograph lands.
    """
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + seglen
    return None, None


def stage(manifest):
    """Download every image into the staging directory; return per-entry facts."""
    STAGE.mkdir(exist_ok=True)
    results = []
    for path, entry in manifest["images"].items():
        dest = STAGE / entry["file"]
        try:
            blob = download(entry["origin_url"])
        except (urllib.error.URLError, OSError) as exc:
            results.append((path, entry, None, f"download failed: {exc}"))
            continue
        digest = hashlib.sha256(blob).hexdigest()
        recorded = str(entry.get("sha256", PENDING))
        if recorded == PENDING:
            note = "new (manifest sha256 is PENDING)"
        elif recorded != digest:
            note = (f"CHANGED: manifest records {recorded[:12]}..., "
                    f"origin now serves {digest[:12]}...")
        elif int(entry.get("bytes", 0)) != len(blob):
            note = (f"CHANGED: manifest records {entry.get('bytes')} bytes, "
                    f"origin serves {len(blob)}")
        else:
            note = "matches manifest"
        w, h = jpeg_size(blob)
        if w and recorded != PENDING and (w, h) != (entry.get("width"),
                                                    entry.get("height")):
            note = (f"CHANGED: manifest records {entry.get('width')}x"
                    f"{entry.get('height')}, file is {w}x{h}")
        dest.write_bytes(blob)
        results.append((path, entry, (digest, len(blob)), note))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hashes", action="store_true",
                    help="print sha256/bytes lines for the manifest")
    ap.add_argument("--upload", action="store_true",
                    help="print the gh command that uploads the staged files")
    args = ap.parse_args()

    manifest = load_manifest()
    results = stage(manifest)
    bad = [r for r in results if r[2] is None or r[3].startswith("CHANGED")]
    for path, entry, got, note in results:
        size = f"{got[1]} bytes" if got else "-"
        print(f"  {entry['file']:<52} {size:>12}  {note}")
    print(f"staged {len(results) - len(bad)}/{len(results)} images "
          f"into {STAGE.relative_to(ROOT)}/")

    if args.hashes:
        print("\nmanifest values:")
        for path, entry, got, _ in results:
            if got:
                blob = (STAGE / entry["file"]).read_bytes()
                w, h = jpeg_size(blob)
                print(f"  {path}\n    sha256: {got[0]}\n    bytes: {got[1]}\n"
                      f"    width: {w}\n    height: {h}")

    if args.upload:
        files = " ".join(f".image-stage/{e['file']}"
                         for _, e, got, _ in results if got)
        print(f"\ngh release upload {manifest['release']} {files} --clobber")

    if bad:
        print(f"\n{len(bad)} image(s) could not be staged as recorded; "
              "the release must not be rebuilt from them.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

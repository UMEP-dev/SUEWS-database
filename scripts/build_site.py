#!/usr/bin/env python3
"""Build the static database browser site from db/.

Generates a self-contained static site under site/ (gitignored; built and
deployed by CI):

  index.html                    overview: stats, browse by family/surface,
                                client-side search over every record
  records/<path>.html           one page per evidence record: envelope,
                                parameters, citation, model-ready fragment,
                                GitHub source + propose-a-change links
  archetypes/<path>.html        one page per archetype, with resolved uses
  sources.html                  the citation registry
  places.html                   the place registry

Design follows the suews.io token palette (dark default) so the site reads
as part of the SUEWS family.

Usage: python scripts/build_site.py [--out site]
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_db import load_all  # noqa: E402
from export_record import PlainDumper, assemble  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/UMEP-dev/SUEWS-database"

CSS = """
:root {
  --sun-gold: #F7B538; --energy-orange: #E85D04; --water-blue: #0077B6;
  --water-blue-light: #48CAE4; --sky-blue: #5DADE2; --veg-green: #09a25c;
  --urban-slate: #2D3142; --bg-primary: #0F1119; --bg-secondary: #1A1D2E;
  --bg-card: rgba(255,255,255,0.03); --bg-card-hover: rgba(255,255,255,0.06);
  --border-light: rgba(255,255,255,0.08); --border-medium: rgba(255,255,255,0.14);
  --text-primary: rgba(255,255,255,0.92); --text-secondary: rgba(255,255,255,0.7);
  --text-muted: rgba(255,255,255,0.55);
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg-primary); color: var(--text-primary);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
a { color: var(--sky-blue); text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
header.site { border-bottom: 1px solid var(--border-light); background: var(--bg-secondary); }
header.site .wrap { display: flex; align-items: baseline; gap: 1rem; padding: 1.1rem 1.25rem; }
header.site h1 { font-size: 1.15rem; margin: 0; }
header.site h1 a { color: var(--text-primary); }
header.site .sub { color: var(--text-muted); font-size: 0.9rem; }
.stats { display: flex; flex-wrap: wrap; gap: 2.2rem; margin: 2rem 0 2.5rem;
  padding: 1.2rem 1.5rem; border: 1px solid var(--border-light); border-radius: 12px;
  background: var(--bg-card); }
.stat b { display: block; font-size: 1.6rem; color: var(--sun-gold); }
.stat span { color: var(--text-muted); font-size: 0.85rem; }
h2 { margin: 2.4rem 0 0.9rem; font-size: 1.25rem; }
h3 { margin: 1.6rem 0 0.6rem; font-size: 1.02rem; color: var(--text-secondary); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 0.75rem; }
.card { display: block; padding: 0.85rem 1rem; border: 1px solid var(--border-light);
  border-radius: 10px; background: var(--bg-card); color: var(--text-primary); }
.card:hover { background: var(--bg-card-hover); text-decoration: none;
  border-color: var(--border-medium); }
.card .n { color: var(--text-muted); font-size: 0.8rem; }
ul.rec { list-style: none; padding: 0; margin: 0.4rem 0; columns: 2; column-gap: 2.5rem; }
ul.rec li { margin: 0.18rem 0; break-inside: avoid; font-size: 0.92rem; }
.tag { display: inline-block; padding: 0.05rem 0.5rem; border-radius: 999px;
  font-size: 0.72rem; border: 1px solid var(--border-medium); color: var(--text-secondary);
  margin-left: 0.4rem; vertical-align: middle; }
table.kv { border-collapse: collapse; width: 100%; margin: 0.6rem 0 1rem; }
table.kv td, table.kv th { text-align: left; padding: 0.4rem 0.75rem;
  border-bottom: 1px solid var(--border-light); font-size: 0.93rem; vertical-align: top; }
table.kv th { color: var(--text-muted); font-weight: 500; width: 220px; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
code { background: var(--bg-card); padding: 0.1rem 0.35rem; border-radius: 5px;
  font-size: 0.88em; }
pre { background: var(--bg-secondary); border: 1px solid var(--border-light);
  border-radius: 10px; padding: 1rem 1.2rem; overflow-x: auto; font-size: 0.85rem;
  line-height: 1.5; }
.crumbs { color: var(--text-muted); font-size: 0.85rem; margin-bottom: 1.2rem; }
.actions { display: flex; gap: 1rem; margin: 1.2rem 0; flex-wrap: wrap; }
.actions a { padding: 0.45rem 0.9rem; border: 1px solid var(--border-medium);
  border-radius: 8px; font-size: 0.88rem; }
input.search { width: 100%; padding: 0.65rem 1rem; border-radius: 10px;
  border: 1px solid var(--border-medium); background: var(--bg-secondary);
  color: var(--text-primary); font-size: 0.95rem; margin: 0.5rem 0 0.8rem; }
.hidden { display: none; }
footer { margin-top: 3.5rem; padding-top: 1.2rem; border-top: 1px solid var(--border-light);
  color: var(--text-muted); font-size: 0.82rem; }
"""

SEARCH_JS = """
const input = document.getElementById('q');
if (input) {
  const items = Array.from(document.querySelectorAll('[data-search]'));
  input.addEventListener('input', () => {
    const q = input.value.toLowerCase().trim();
    for (const el of items) {
      el.classList.toggle('hidden', q !== '' && !el.dataset.search.includes(q));
    }
    for (const sec of document.querySelectorAll('[data-section]')) {
      const any = sec.querySelector('[data-search]:not(.hidden)');
      sec.classList.toggle('hidden', !any);
    }
  });
}
"""


def esc(s):
    return html.escape(str(s), quote=True)


def page(title, body, depth=0):
    rel = "../" * depth
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · SUEWS parameter database</title>
<style>{CSS}</style>
</head><body>
<header class="site"><div class="wrap">
  <h1><a href="{rel}index.html">SUEWS parameter database</a></h1>
  <span class="sub">curated values with a citation on every one</span>
</div></header>
<div class="wrap">
{body}
<footer>Data and site: <a href="{REPO_URL}">UMEP-dev/SUEWS-database</a> ·
every value carries its citation · fragments paste into
<a href="https://suews.readthedocs.io/">SUEWS YAML configurations</a></footer>
</div>
<script>{SEARCH_JS}</script>
</body></html>"""


def kv_rows(pairs):
    out = []
    for k, v in pairs:
        if v is None:
            continue
        out.append(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>")
    return "<table class=\"kv\">" + "".join(out) + "</table>"


def yaml_block(data):
    txt = yaml.dump(data, Dumper=PlainDumper, sort_keys=False, allow_unicode=True,
                    width=80)
    return f"<pre>{esc(txt)}</pre>"


def source_line(key, src, depth):
    if not src:
        return esc(key)
    rel = "../" * depth
    label = esc(f"{src.get('author', key)} ({src.get('year', '?')})")
    doi = src.get("doi")
    doi_html = (f" · <a href=\"https://doi.org/{esc(doi)}\">doi:{esc(doi)}</a>"
                if doi else "")
    return f"<a href=\"{rel}sources.html#{esc(key)}\">{label}</a>{doi_html}"


def record_page(path, rec, records, sources, kind):
    depth = path.count("/")
    src_key = rec.get("source")
    src = sources.get(src_key) if src_key else None
    gh_file = f"{REPO_URL}/blob/main/db/{path}.yml"
    gh_edit = f"{REPO_URL}/edit/main/db/{path}.yml"
    crumbs = f"<div class=\"crumbs\">{esc(path)}.yml</div>"

    meta = kv_rows([
        ("Target", f"<code>{esc(rec.get('target'))}</code>"),
        ("Name", esc(rec["name"]) if rec.get("name") else None),
        ("Place", esc(rec["place"]) if rec.get("place") else None),
        ("Origin (verbatim)", esc(rec["origin"]) if rec.get("origin") else None),
        ("Representativeness", esc(rec["representativeness"])
         if rec.get("representativeness") else None),
        ("Source", source_line(src_key, src, depth) if src_key else None),
        ("Attaches to", f"<code>{esc(rec['attaches_to'])}</code>"
         if rec.get("attaches_to") else None),
        ("Season label", esc(rec["season_label"]) if rec.get("season_label") else None),
        ("Schema version", esc(rec.get("schema_version"))),
        ("Legacy row ID", esc(rec.get("legacy_id"))),
    ])

    body = [crumbs, f"<h2>{esc(rec.get('name') or path.rsplit('/', 1)[-1])}"
                    f"<span class=\"tag\">{kind}</span></h2>", meta]

    uses = rec.get("uses")
    if uses:
        body.append("<h3>Uses (referenced records)</h3><table class=\"kv\">")
        rel = "../" * depth

        def use_rows(u, prefix=""):
            for slot, ref in u.items():
                if isinstance(ref, dict):
                    use_rows(ref, prefix + slot + " · ")
                else:
                    link = (f"<a href=\"{rel}{esc(ref)}.html\">{esc(ref)}</a>"
                            if ref in records else esc(ref))
                    body.append(f"<tr><th>{esc(prefix + slot)}</th><td>{link}</td></tr>")

        use_rows(uses)
        body.append("</table>")

    if rec.get("parameters"):
        body.append("<h3>Parameters (as stored)</h3>")
        body.append(yaml_block(rec["parameters"]))

    if rec.get("legacy"):
        body.append("<h3>Legacy block (no supy home; kept verbatim)</h3>")
        body.append(yaml_block(rec["legacy"]))

    try:
        frag = assemble(path, records, sources)
        frag.pop("_notes", None)
        if frag:
            body.append("<h3>Model-ready fragment</h3>"
                        "<p class=\"crumbs\">Paste under "
                        f"<code>{esc(rec.get('target'))}</code> in a SUEWS YAML "
                        "configuration; every value carries its citation.</p>")
            body.append(yaml_block(frag))
    except Exception:
        pass

    body.append(
        "<div class=\"actions\">"
        f"<a href=\"{gh_file}\">View source on GitHub</a>"
        f"<a href=\"{gh_edit}\">Propose a change (fork &amp; PR)</a>"
        "</div>"
    )
    return page(rec.get("name") or path, "\n".join(body), depth)


def build_index(records, sources, places):
    n_rec = sum(1 for p in records if p.startswith("records/"))
    n_arch = sum(1 for p in records if p.startswith("archetypes/"))
    stats = (
        "<div class=\"stats\">"
        f"<div class=\"stat\"><b>{n_rec}</b><span>evidence records</span></div>"
        f"<div class=\"stat\"><b>{n_arch}</b><span>archetypes</span></div>"
        f"<div class=\"stat\"><b>{len(sources)}</b><span>cited sources</span></div>"
        f"<div class=\"stat\"><b>{len(places)}</b><span>places</span></div>"
        "</div>"
    )
    intro = (
        "<p>Curated parameter values for "
        "<a href=\"https://github.com/UMEP-dev/SUEWS\">SUEWS</a>, stored as "
        "records: one source-coherent set per file, named by the model's own "
        "parameter paths, with a citation on every value. Any record exports "
        "as a fragment that pastes straight into a SUEWS YAML configuration. "
        f"To contribute a value, see the <a href=\"{REPO_URL}/blob/main/docs/"
        "FORMAT.md\">format guide</a> — a contribution is one small YAML file "
        "in a pull request.</p>"
    )
    search = ("<input id=\"q\" class=\"search\" type=\"search\" "
              "placeholder=\"Search records: place, source, parameter family, surface...\">")

    groups = {}
    for path, rec in sorted(records.items()):
        top = path.split("/")[1]
        head = "Archetypes · " if path.startswith("archetypes/") else "Records · "
        groups.setdefault(head + top, []).append((path, rec))

    sections = []
    for label, items in sorted(groups.items()):
        lis = []
        for path, rec in items:
            name = rec.get("name") or path.rsplit("/", 1)[-1]
            bits = [path, str(name), str(rec.get("place") or ""),
                    str(rec.get("source") or ""), str(rec.get("target") or "")]
            key = esc(" ".join(bits).lower())
            extra = f" <span class=\"n\">{esc(rec.get('place'))}</span>" \
                if rec.get("place") else ""
            lis.append(f"<li data-search=\"{key}\">"
                       f"<a href=\"{esc(path)}.html\">{esc(path.split('/', 2)[-1])}</a>"
                       f"{extra}</li>")
        sections.append(
            f"<section data-section><h2>{esc(label)} "
            f"<span class=\"tag\">{len(items)}</span></h2>"
            f"<ul class=\"rec\">{''.join(lis)}</ul></section>"
        )

    registries = ("<h2>Registries</h2><div class=\"grid\">"
                  "<a class=\"card\" href=\"sources.html\">Sources"
                  f"<div class=\"n\">{len(sources)} citations</div></a>"
                  "<a class=\"card\" href=\"places.html\">Places"
                  f"<div class=\"n\">{len(places)} entries</div></a>"
                  "</div>")

    return page("Browse", intro + stats + search + registries + "".join(sections))


def build_sources_page(sources):
    rows = []
    for key, src in sorted(sources.items()):
        title = src.get("title") or src.get("note") or ""
        doi = src.get("doi")
        doi_html = (f" <a href=\"https://doi.org/{esc(doi)}\">doi:{esc(doi)}</a>"
                    if doi else "")
        rows.append(
            f"<tr id=\"{esc(key)}\"><th><code>{esc(key)}</code></th>"
            f"<td>{esc(src.get('author', ''))} ({esc(src.get('year', '?'))}). "
            f"{esc(title)}. <i>{esc(src.get('journal') or '')}</i>{doi_html}</td></tr>"
        )
    return page("Sources", "<h2>Sources</h2><table class=\"kv\">"
                + "".join(rows) + "</table>")


def build_places_page(places, records):
    counts = {}
    for rec in records.values():
        p = rec.get("place")
        if p:
            counts[p] = counts.get(p, 0) + 1
    rows = [
        f"<tr><th><code>{esc(slug)}</code></th><td>{esc(info.get('name', slug))}"
        f"<span class=\"tag\">{counts.get(slug, 0)} records</span></td></tr>"
        for slug, info in sorted(places.items())
    ]
    return page("Places", "<h2>Places</h2><table class=\"kv\">"
                + "".join(rows) + "</table>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    args = ap.parse_args()
    out = ROOT / args.out

    records, sources, places = load_all()

    n = 0
    for path, rec in records.items():
        kind = "record" if path.startswith("records/") else "archetype"
        fp = out / (path + ".html")
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(record_page(path, rec, records, sources, kind))
        n += 1

    (out / "index.html").write_text(build_index(records, sources, places))
    (out / "sources.html").write_text(build_sources_page(sources))
    (out / "places.html").write_text(build_places_page(places, records))
    (out / ".nojekyll").write_text("")
    print(f"site: {n} record pages + index/sources/places -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

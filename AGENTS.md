# Working in this repository

Instructions for any coding agent working on the SUEWS parameter database —
Claude Code, Codex, Cursor, or anything else. This file is the canonical one;
`CLAUDE.md` points here so the two cannot drift apart.

## What this repository is

A curated, citable parameter database for the SUEWS urban climate model. 840
evidence records under `db/records/`, 319 typologies under `db/archetypes/`,
66 sources in `db/sources.yml`, 260 places in `db/places.yml`. The YAML is the
canonical store. `scripts/build_site.py` generates the browse site from it, and
the site deploys to GitHub Pages on every push to `main`.

Every value is supposed to carry its provenance. Some do not, and that is the
work.

## The rule that matters most

**Never invent a citation, DOI, author, year, or source key.**

A plausible-looking citation attached to a value nobody checked is the worst
outcome anyone can produce here. It is invisible once merged, it survives
review, and it corrupts the one thing this database exists to provide. A source
key already existing in `db/sources.yml` is *not* evidence that the paper
contains a given value.

Two corollaries:

- **Never change a parameter value because someone reported it looks wrong.** A
  value changes only when the cited source has been read and shown to say
  something different. "This seems high" justifies investigation, never an edit.
- **Never attribute a derived parameter to the source of its underlying
  observations.** Where a coefficient was *fitted* to someone's measurements,
  crediting the measurer with the coefficient is a fabrication. The record
  format currently cannot express this case at all; see the escalation rule
  below.

Escalating a problem you cannot settle is a **successful** outcome, not a
failure. Say what you found, what would settle it, and who could settle it.

## Procedures

Repository procedures live in `.claude/skills/<name>/SKILL.md`. They are plain
Markdown with YAML frontmatter and are readable by any agent — the directory
name is a Claude Code convention, not a Claude Code dependency.

- **`.claude/skills/mend-record/SKILL.md`** — take a reported problem with a
  record from noticing to merged fix: record it as a structured issue,
  investigate it against the sources, then either fix it in a pull request that
  closes the issue, or escalate. Its `.claude/skills/mend-record/reference/problem-classes.md` gives one
  recipe per problem class, each stating what would settle the question and what
  would not.

**If you are handling a data problem, read that file before touching anything.**
If your agent has no mechanism for loading skills, read it as an ordinary
Markdown file — it is written to work that way.

## Record format

A record carries `record`, `schema_version`, `target` (a dotted SUEWS parameter
path), `name`, `source` (a key into `db/sources.yml`, or the literal
`unreferenced`), often `place` (a key into `db/places.yml`), sometimes
`legacy_id`, and a `parameters` mapping. `docs/FORMAT.md` is the full
specification.

Record paths are identity: they appear in issue reports and in every site URL.
Do not rename or move a record file as a side effect of another change.

## Checks

```bash
make check          # structure, references, places/sources, coupling rules
make check-strict   # as check, but coupling warnings fail
make validate       # check + validate every fragment against the supy data model
make export REC=<record path>    # the model-ready fragment for one record
python3 scripts/build_site.py --out site    # rebuild the browse site
```

`make check` must exit 0 before anything is committed. It reports two kinds of
finding beyond structural errors:

- **linkage warnings** — coupling rules across records;
- **quality warnings** — duplicate value sets, shared-profile clusters, suspect
  names (test fixtures, sample runs).

Both are **for human adjudication and are never auto-fixed**. Do not "clean them
up". Do not let their count rise; state the before and after count in any pull
request that touches data.

## Contributing changes

- `main` is protected: a pull request is the only route in, and CI (`check`)
  runs on every one.
- One problem, one pull request. Do not sweep several records into one change,
  however similar they look — each carries its own evidence.
- Never merge your own work unless the person you are working with says so in
  that session.
- Never close an issue by hand; let the pull request close it with `Fixes #N`.
- Commit messages and pull request bodies say **what changed and why it is
  correct**. They never record how the problem reached you, which conversation
  it came from, or who said what in private.

## Reporting problems

The site carries a **Report an issue** button on every record page, backed by
two issue forms in `.github/ISSUE_TEMPLATE/`:
`.github/ISSUE_TEMPLATE/record-issue.yml` (label `data problem`) and
`.github/ISSUE_TEMPLATE/site-issue.yml` (label `site problem`). A hand-written issue
should use the same headed sections the form produces, so triage looks the same
either way. The `mend-record` skill gives the exact shape.

While the structure is still being shaped, a floating **Report an issue**
control also rides on every page — record-scoped on a record page, page-scoped
everywhere else, with the page address filled in for you. It is deliberately
temporary: `FLOATING_REPORT` in `scripts/build_site.py` turns it off, and with
it off the site builds byte for byte as it did before. Remove it once the
structure has settled. Nothing depends on it — the record button and the footer
link stand on their own, and it leaves behind no template, label or stored
state.

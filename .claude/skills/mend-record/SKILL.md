---
name: mend-record
description: Take a reported problem with a SUEWS parameter database record from noticing to merged fix — record it as a structured issue, investigate it against the actual sources, and open a PR that closes it. Never invents a citation; escalates what the evidence cannot settle. Use when someone reports a wrong value, a missing or wrong citation, a derived-not-measured parameter, a duplicate, or a suspect test record in UMEP-dev/SUEWS-database.
argument-hint: '[#issue | record path | a description of the problem]'
---

# Fix a data issue

The parameter database at `UMEP-dev/SUEWS-database` holds 840 evidence records
and 319 typologies, and every value is supposed to carry its provenance. Some
do not. This skill takes one such problem from "somebody noticed" to "merged
fix", without ever inventing the evidence that would justify the fix.

**The problem this exists to solve is not volume, it is rigour.** A survey of
the 40 unreferenced records found exactly one whose name carries a recoverable
author-year. There is no pattern to batch-apply. Each case is an investigation,
and the failure mode is not slowness — it is a plausible-looking citation
attached to a value nobody checked.

## The loop

1. **Record** the problem as a structured issue (skip if given one).
2. **Investigate** it against the record, the sources registry, and the
   literature.
3. **Classify** it: fixable on the evidence, or needs a human decision.
4. **Fix** it in a branch and open a PR that closes the issue.
5. **Hand back** — report what was fixed, what was escalated, and why.

## Non-negotiables

These are the point of the skill. Breaking one causes worse damage than not
running it at all, because a wrong citation is invisible once merged.

- **Never invent a citation, DOI, author, year, or source key.** If a source is
  not already in `db/sources.yml` and cannot be verified against a real
  publication, the issue is escalated, not fixed. A plausible-looking DOI is
  the single worst outcome this skill can produce.
- **Never change a parameter value because someone said it looks wrong.** A
  value changes only when the cited source has been checked and shown to say
  something different. "This seems high" is a reason to investigate, never a
  reason to edit.
- **Never attribute a derived parameter to the source of its underlying
  observations.** See "What the schema cannot express" below; this case is
  escalated, not bodged.
- **One issue, one PR.** No sweeping several records into one change, however
  similar they look — each carries its own evidence.
- **Never close the issue by hand.** The PR closes it, via `Fixes #N`, when a
  human merges.
- **`make check` must exit 0, and the warning count must not rise.** Record the
  before and after counts in the PR body.
- **Never merge.** Open the PR and stop, unless the person running the skill
  says otherwise in this session.

## What you need to know about the repository

- Records live in `db/records/<family>/…yml`, typologies in
  `db/archetypes/…yml`. The path is the record's identity and appears in every
  issue and every site URL — treat it as stable.
- A record carries `record`, `schema_version`, `target` (a dotted SUEWS
  parameter path), `name`, `source` (a key into `db/sources.yml`, or the
  literal `unreferenced`), often `place` (a key into `db/places.yml`),
  sometimes `legacy_id`, and a `parameters` mapping.
- `db/sources.yml` holds 66 sources with DOIs. `db/places.yml` holds 260
  places, 29 with coordinates.
- Checks: `make check` — structural errors are fatal, linkage and quality
  findings are warnings for human adjudication and are never auto-fixed.
- Site rebuild: `python3 scripts/build_site.py --out site`. Not required for a
  data-only fix, but run it if the fix touches anything the site renders
  specially.
- `main` is protected: a PR is the only route in. CI (`check`) runs on every PR.
- Issue labels: `data problem` for records, `site problem` for the website.

## Phase 1 — Record the problem

If given an issue number, read it with `gh issue view <n> --repo
UMEP-dev/SUEWS-database` and go to Phase 2.

Otherwise the problem exists only in someone's head or in a passing remark, and
the first job is to make it a durable record. Write the issue body in exactly
the shape the site's report button produces, so a hand-filed issue and a
button-filed one are indistinguishable to whoever triages them:

```markdown
### Record

<record path under db/, without the .yml>

### Seen at

https://github.com/UMEP-dev/SUEWS-database/blob/<sha>/db/<path>.yml

### What kind of problem

<exactly one of: The value looks wrong | The citation is wrong or missing |
The value is derived from other data, not measured in the cited source |
This duplicates another record | The place or representativeness is wrong |
Something else>

### What is wrong

<what the record says, and what was expected instead>

### Evidence

<DOI or citation and the table or figure, or "_No response_">
```

Title is `[record] <the record's name>`. Label is `data problem`.

**Draft it in chat and wait for a go-ahead before posting.** Outward text on a
shared repository is never posted unprompted. Once approved:

```bash
gh issue create --repo UMEP-dev/SUEWS-database \
  --title "[record] …" --label "data problem" --body-file <file>
```

## Phase 2 — Investigate

Read [reference/problem-classes.md](reference/problem-classes.md) and follow
the recipe for the class this issue falls into. It states, per class, what to
read, what would settle the question, and — more important — what would *not*
settle it.

Two rules cut across every class:

- **Read the record file itself**, not the site's rendering of it. The site is
  generated and can lag.
- **Check whether the value is already elsewhere in the database.** A record
  that duplicates another exactly is often the same measurement entered twice,
  and the fix is a merge decision for a human rather than an edit.

## Phase 3 — Classify

Fix in this run only when **all** of these hold:

- the correct value or citation is established by a source you have actually
  read or verified, not inferred;
- the change is confined to the reported record, or to `db/sources.yml` where a
  verified source must be added;
- the record's schema can express the correction without a format change;
- nothing about the fix requires choosing between two defensible readings.

Escalate — write the finding into the issue as a comment and stop — when any of
these hold:

- the evidence is suggestive but not conclusive;
- the correction needs a field the record format does not have;
- the fix would merge, split or delete records;
- the value itself is disputed rather than its provenance;
- the record is a test fixture or sample run whose fate is a curation decision.

Escalation is a successful outcome. Say plainly what was found, what it would
take to settle it, and who or what could settle it.

## Phase 4 — Fix

```bash
git fetch origin
git checkout -b fix-<short-slug> origin/main
```

Edit the record YAML directly; keep the diff minimal and leave key order alone.
If a verified source must be added to `db/sources.yml`, add it in the same PR —
the citation and the record that uses it belong in one reviewable change.

Then:

```bash
make check          # must exit 0; compare the warning count with before
```

Commit with a message that says what changed and why it is correct — never how
the problem reached you, never who reported it in a private conversation.
Open the PR:

```bash
gh pr create --base main --title "…" --body "…Fixes #<n>…"
gh pr checks <pr> --watch --fail-fast
```

The PR body states: what the record said, what it says now, the source that
justifies it (with DOI), and the `make check` warning counts before and after.

Stop there. Do not merge.

## Phase 5 — Hand back

Report: the issue number and URL, the PR number and URL, what changed, what was
escalated and why, and the check counts. If nothing was fixed, that is a
complete and legitimate result — say what would unblock it.

## What the schema cannot express

The record format has **one** `source` field. It cannot distinguish the
publication a measurement came from from the publication in which a parameter
was *fitted* to that measurement.

`db/records/ohm/generic--unreferenced--all-vegetation--mixed-forest-20-mccaughey-1985.yml`
is the worked example: OHM coefficients a1, a2, a3 whose underlying flux
observations are McCaughey's, but whose fitted coefficients come from a later
paper. Setting `source: mccaughey1985` would credit McCaughey with numbers he
did not publish. Leaving it `unreferenced` hides both links.

Any issue of this class is **escalated, not fixed**. The resolution is a schema
change — a second provenance field plus an observed-versus-modelled flag —
which is a design decision for the database's maintainers, not something to
improvise inside one record. Say so in the issue and link this paragraph's
reasoning.

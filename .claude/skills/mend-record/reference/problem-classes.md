# Problem classes

One recipe per option in the report form's dropdown. Each states what to read,
what would settle the question, and what would not. The last column is the one
that matters: most wrong fixes come from treating suggestive evidence as
conclusive.

The queue as it stands (from `make check` and the record survey): 40 records
with `source: unreferenced`, 35 cross-record duplicate value sets, 2
shared-profile clusters covering 460 LUCY records, roughly 28 suspect names,
and 3 LAI linkage warnings.

## The citation is wrong or missing

**Read**: the record file; its `name` string; `db/sources.yml`; the source
pages of records with the same `target` and `place`.

**What settles it**: the name string carries an author and year, a matching key
already exists in `db/sources.yml` with a DOI, *and* the publication is
confirmed to contain that parameter for that surface. All three. A key that
exists is not evidence that the paper contains the value.

**What does not settle it**: an author-year in the name alone. A survey of the
40 unreferenced records found exactly one with a recoverable author-year, and
that one turned out to be a derived-parameter case rather than a missing
citation. Similarity to a neighbouring record does not settle it either —
adjacent records in the legacy tables often came from different studies.

**If the source is not in `db/sources.yml`**: verify the DOI through Crossref
before adding it, and add the entry in the same PR. Never construct a DOI from
an author and year.

**Escalate when** the name carries a citation but the paper cannot be obtained,
or when the paper is obtained and does not contain the value.

## The value is derived from other data, not measured in the cited source

**Always escalate.** The record format cannot express two provenances. See
"What the schema cannot express" in SKILL.md. Fixing this inside one record
means either crediting an author with numbers they did not publish, or hiding a
provenance link — both worse than the current honest `unreferenced`.

**What to do instead**: comment on the issue with the two publications you
identified, name which is the observation and which the fitting, and say that
resolution needs the schema change.

## This duplicates another record

**Read**: both records; `make check`'s duplicate-values warnings, which already
list exact cross-record repeats.

**What settles it**: nothing you can act on alone. Identical values can mean the
same measurement entered twice, or two studies independently reporting the same
rounded standard value, or one study quoting another. These are different
things and only the first is a duplicate.

**Escalate** with both paths, both sources, and which of the three readings the
evidence favours. A merge or deletion is a curation decision.

Note the shared-profile clusters separately: 230 LUCY records carrying 7
distinct value sets is a data-modelling artefact of the legacy tables, not 230
duplicate mistakes. Do not report those as duplicates.

## The place or representativeness is wrong

**Read**: the record's `place`; `db/places.yml`; the source publication's study
site.

**What settles it**: the publication names a different site, or names a site not
in the registry (in which case add it to `db/places.yml` with coordinates in
the same PR).

**What does not settle it**: the author's affiliation. Papers routinely report
measurements from somewhere other than where their authors work.

Representativeness (`site`, `city`, `regional`, `generic`) is evidence quality,
not location, and must never be changed to make a place fit.

## The value looks wrong

**Read**: the record; the cited source; the same parameter across other records
for the same surface, for a sense of range.

**What settles it**: the cited source states a different number, or states the
number in different units. Unit mismatches are the most common real defect
here and the most checkable.

**What does not settle it**: an outlying value. Real measurements are sometimes
extreme, and the database's job is to record what was published, not to make
the distribution tidy. An outlier with a checked citation stays.

**Escalate** when the source supports the value and the reporter disputes the
source. That is a scientific disagreement, not a data error.

## Something else, and suspect names

Records whose names read as test fixtures or sample runs — `OBTESTCASE`,
`[sample run]` — are flagged by `make check`. Whether they are deleted, renamed
or kept as fixtures is a curation decision about what the database is for.

**Always escalate.** Never delete a record on this basis: the values may be in
use in a typology, and `make check`'s linkage pass will not necessarily catch
every reference.

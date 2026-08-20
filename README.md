# SUEWS parameter database

Curated parameter values for [SUEWS](https://github.com/UMEP-dev/SUEWS) —
albedo, emissivity, thermal and material properties, surface conductance,
hourly profiles and more — organised by surface type, building typology and
place, with a citation on every value. It is used through the **SUEWS Database
Manager** and **SUEWS Database Prepare** plugins in
[UMEP](https://umep-docs.readthedocs.io/).

> **Using the spreadsheet?** `database.xlsx` has moved out of the repository
> and into the [releases](https://github.com/UMEP-dev/SUEWS-database/releases).
> The data is unchanged; it now lives in `db/` as YAML and the workbook is
> generated from it.

## The database is the YAML files

`db/` holds one file per table, each a dictionary keyed by an integer ID.
**Edit those.** The spreadsheet is no longer the database and is no longer in
the repository: it is built on demand with `make xlsx`, and published with each
release for anyone who needs one. The original pre-migration workbook is kept
as a release asset in its own right.

```
make xlsx     # build database.xlsx from db/
make verify   # prove db/ still reproduces the migrated workbook
make check    # referential, linkage and hygiene checks
make origins  # refresh the Origin work-list
```

`docs/LAYOUT.md` explains the layout, the ID scheme and how to add a value.

## Two things the database is starting to record

A parameter value on its own is not enough to use it well, so alongside the
values the repository carries two further layers.

**Which parameters move together.** Parameters are not independent of one
another, and until now nothing said so. Senescing-degree-day totals mean
nothing apart from the base temperature they were accumulated against; leaf
growth-power coefficients only apply to the LAI equation they were fitted for.
A user assembling a set one value at a time can produce a combination that is
individually defensible and jointly wrong, with no signal that they have.
`schema/parameter_groups.yml` records the groups and how tightly each is
coupled, and `make check` enforces the rules that are checkable.

**Where a value is from, and how far it travels.** Every value has an origin,
but a rooftop measurement at one site and a value offered as typical of a
region currently look identical. `schema/regional_axis.yml` sets out how the
database is to be sliced geographically, reusing the 22-region vocabulary and
229-country mapping already in `db/region.yml` and `db/country.yml`. This is
what makes it possible to ask what a typical Scandinavian or northern Chinese
combination looks like — not as the right answer, but as a defensible default
when nothing else is known.

Both layers are partly populated, and both mark what remains unassessed rather
than guessing it.

## Known issues

`docs/FINDINGS.md` lists data-quality problems found but deliberately not
fixed: an LAI range that appears at three sites under three citations, three
citations pointing at reference IDs that do not exist, three surface entries
mixing LAI parameters fitted for different equations, and the free-text state
of the `Origin` column.

## Useful links

- [UMEP website](https://umep-docs.readthedocs.io/en/latest/index.html)
- [Manual for the database plugin](https://umep-docs.readthedocs.io/en/latest/pre-processor/Urban%20Energy%20Balance%20SUEWS%20Database%20Manager.html#)
- [Tutorial for the database plugin](https://umep-docs.readthedocs.io/projects/tutorial/en/latest/Tutorials/SUEWSDatabase.html#suewsdatabase)
- [Installing UMEP](https://umep-docs.readthedocs.io/en/latest/Getting_Started.html)

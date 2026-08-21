# SUEWS parameter database

Curated parameter values for [SUEWS](https://github.com/UMEP-dev/SUEWS) —
albedo, emissivity, thermal and material properties, surface conductance,
hourly profiles and more — organised by surface type, building typology and
place, with a citation on every value.

## Browse it

**[database.suews.io](https://database.suews.io)**

Every record and typology has a page. Arrive knowing a parameter and search the
model's own parameter paths; arrive knowing a site and pick it off the map or
out of the region / country / city lists. Each value shows its citation, its
place and what it stands for, and exports as a fragment that pastes straight
into a SUEWS YAML configuration. Anything that looks wrong can be reported from
the record's own page.

## The database is records

`db/records/` holds the evidence: one YAML file per source-coherent
parameter set, its parameters named by the [SUEWS YAML data
model](https://suews.readthedocs.io/)'s canonical paths so a record exports
straight into a modern SUEWS configuration. `db/archetypes/` holds curated
combinations — complete surface descriptions, regional default sets,
building typologies — that reference records rather than copy their values,
so provenance survives assembly.

```yaml
# db/records/surfaces/grass/helsinki--jarvi2014--phenology.yml
target: land_cover.grass
place: helsinki
representativeness: city
source: jarvi2014
parameters:
  lai:
    base_temperature: 5
    base_temperature_senescence: 10
    gdd_full: 300
    sdd_full: -450
```

```sh
make check        # structure, references and coupling rules
make validate     # + validate every fragment against the supy data model
make audit-plan   # dry-run coverage for evidence and composition audits
make export REC=archetypes/surfaces/bldgs/helsinki--kumpula
```

`make export` renders any record or archetype as a fragment that pastes
directly into a SUEWS YAML configuration, each value carrying its citation
in supy's native `{value, ref: {ID, DOI}}` form.

`docs/FORMAT.md` specifies the format and how to contribute a value;
`docs/LAYOUT.md` maps the repository; `docs/PROVENANCE_RUNNER.md` documents the
deterministic agent-audit and resume workflow.

## What a record knows besides its values

**Which parameters move together.** Values that were measured or fitted
together live in the same record — the degree-day totals arrive with the
base temperatures they were accumulated against, a conductance set stays
whole. `schema/parameter_groups.yml` records the couplings that cross
record boundaries, and `make check` warns when an archetype combines
records inconsistently (it currently finds three, inherited from the
original data).

**Where a value is from, and how far it travels.** Every record resolves
its origin to a place in `db/places.yml` and declares its
representativeness: a rooftop measurement (`site`) and a value offered as
typical of a region (`regional`) are no longer indistinguishable. The
regional and country default sets in `db/archetypes/` make "a defensible
default when nothing else is known" an explicit, queryable thing.

## Using the spreadsheet?

The spreadsheet-based UMEP plugins (**SUEWS Database Manager**, **SUEWS
Database Prepare**) predate this format. The last `database.xlsx` built
from the table-format database is frozen as a
[release](https://github.com/UMEP-dev/SUEWS-database/releases) asset — the
data it carries is exactly what the record migration preserved
(`schema/migration_census.yml` is the proof) — and the original
pre-migration workbook remains a release asset in its own right. The
workbook is no longer generated from the database. Reconnecting the plugins to
the records is open work, and it does not happen here: this repository
publishes the data, and the code that reads a release into the dictionary the
plugins work from lives in supy
([UMEP-dev/SUEWS#1715](https://github.com/UMEP-dev/SUEWS/issues/1715)). The
release bundle it consumes is
[#48](https://github.com/UMEP-dev/SUEWS-database/issues/48).

## Known issues

`docs/FINDINGS.md` lists data-quality problems found but deliberately not
fixed: an LAI range that appears at three sites under three citations,
citations pointing at reference IDs that do not exist, three surface
archetypes mixing LAI parameters fitted for different equations, and the
legacy free-text origins now quarantined in `schema/origins_map.yml`.

## Licence

The database is meant to be archived, cited and reused, so its terms are
stated rather than assumed.

- **The data** — everything under `db/`, `schema/` and `docs/` — is
  [CC BY 4.0](LICENSE). Use it, redistribute it, build on it, including
  commercially; credit the database and say if you changed anything. Version
  4.0 rather than an earlier one because it grants sui generis database
  rights explicitly, which matters for a compilation like this one.
- **The code** — `scripts/` and `tests/` — is [MPL-2.0](LICENSE-CODE),
  matching SUEWS itself, so tooling and model do not sit under different
  terms.

Attribution-only rather than share-alike is deliberate. Values from these
records are meant to travel into SUEWS configurations and into supy, and a
copyleft data licence would follow them there, which is precisely what a
parameter database should not do to the work that uses it.

Two things this licence does not cover.

Photographs illustrating the typologies are **not** repository content. They
are release assets, each under its own licence, named with its photographer
in `db/images.yml`; see [`docs/FORMAT.md`](docs/FORMAT.md) for how they are
published and what an offered photograph needs.

The parameter values themselves come from the publications each record
cites. CC BY 4.0 covers this compilation — the curation, the structure, the
provenance — not the underlying findings, which remain the work of the
authors named in `db/sources.yml` and should be cited as such when a value
matters to a result.

## Elsewhere

- [SUEWS documentation](https://suews.readthedocs.io/) — parameter names in
  this database follow its YAML input specification
- [UMEP](https://umep-docs.readthedocs.io/en/latest/index.html) — the QGIS
  plugin suite SUEWS is used through
- [SUEWS Database Manager](https://umep-docs.readthedocs.io/en/latest/pre-processor/Urban%20Energy%20Balance%20SUEWS%20Database%20Manager.html)
  — the UMEP plugin for browsing and editing the database. It still reads the
  legacy workbook rather than these records; see *Using the spreadsheet?* above

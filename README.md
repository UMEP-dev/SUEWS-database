# SUEWS parameter database

Curated parameter values for [SUEWS](https://github.com/UMEP-dev/SUEWS) —
albedo, emissivity, thermal and material properties, surface conductance,
hourly profiles and more — organised by surface type, building typology and
place, with a citation on every value.

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
make export REC=archetypes/surfaces/bldgs/helsinki--kumpula
```

`make export` renders any record or archetype as a fragment that pastes
directly into a SUEWS YAML configuration, each value carrying its citation
in supy's native `{value, ref: {ID, DOI}}` form.

`docs/FORMAT.md` specifies the format and how to contribute a value;
`docs/LAYOUT.md` maps the repository.

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
workbook is no longer generated from the database.

## Known issues

`docs/FINDINGS.md` lists data-quality problems found but deliberately not
fixed: an LAI range that appears at three sites under three citations,
citations pointing at reference IDs that do not exist, three surface
archetypes mixing LAI parameters fitted for different equations, and the
legacy free-text origins now quarantined in `schema/origins_map.yml`.

## Useful links

- [SUEWS documentation](https://suews.readthedocs.io/)
- [UMEP website](https://umep-docs.readthedocs.io/en/latest/index.html)
- [Manual for the database plugin](https://umep-docs.readthedocs.io/en/latest/pre-processor/Urban%20Energy%20Balance%20SUEWS%20Database%20Manager.html#)
- [Installing UMEP](https://umep-docs.readthedocs.io/en/latest/Getting_Started.html)

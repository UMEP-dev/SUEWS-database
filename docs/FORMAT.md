# The record format

The database stores parameter values as records: one YAML file per
source-coherent set of values, named by supy's canonical parameter paths so
that a record exports mechanically into a modern SUEWS YAML configuration.

Two layers make up the database.

- **Evidence** (`db/records/`) — values as they were measured, fitted or
  published: one file per set that shares a source, a place and a surface.
  Values that were determined together stay together (a phenology record
  carries the degree-day totals *and* the base temperatures they were
  accumulated against), which is what makes a record safe to reuse whole.
- **Assembly** (`db/archetypes/`) — curated combinations: a complete surface
  description (`archetypes/surfaces/`), a snow parameter set
  (`archetypes/snow/`), a building typology (`archetypes/typologies/`), or a
  regional/country default set (`archetypes/regions/`,
  `archetypes/countries/`). Archetypes carry `uses:` references to evidence
  records rather than copies of their values, so provenance survives
  assembly.

Two registries resolve the short names records use.

- `db/sources.yml` — citation keys (`ward2016`, `jarvi2014`) with author,
  year, title, journal and DOI. `unreferenced` marks legacy values that
  carried no citation.
- `db/places.yml` — place slugs (`helsinki`, `se-england`) for every
  location a record claims. `schema/origins_map.yml` records how each raw
  legacy `Origin` string was resolved, with a confidence flag.

## An evidence record

```yaml
record: records/surfaces/grass/helsinki--jarvi2014--phenology
schema_version: '2026.5'          # supy schema the parameter names target
target: land_cover.grass          # where the fragment belongs in a config
name: Grass
place: helsinki                   # -> db/places.yml
origin: Helsinki                  # the raw legacy string, kept verbatim
representativeness: city          # site | city | regional | generic
source: jarvi2014                 # -> db/sources.yml
legacy_id: 35240003               # the row this came from, pre-migration
parameters:                       # supy canonical names under `target`
  lai:
    base_temperature: 5
    base_temperature_senescence: 10
    gdd_full: 300
    sdd_full: -450
```

Envelope fields:

- `record` — the path, always equal to the file's location under `db/`
  without `.yml`. `make check` enforces this.
- `schema_version` — the supy schema the parameter names were verified
  against. When supy renames fields, records migrate with supy's own
  schema-migration tooling and this pin moves.
- `target` — the supy path the `parameters:` fragment validates against:
  `land_cover.<surface>` (one of paved, bldgs, evetr, dectr, grass, bsoil,
  water, or `common` for surface-agnostic sets such as soils), `snow`,
  `conductance`, `irrigation`, `anthropogenic_emissions`,
  `profile.<kind>`, or the repo-local types `ohm_coefficients`, `material`,
  `construction`.
- `place` + `representativeness` — where the values are from and how far
  they travel. A rooftop measurement (`site`) and a value offered as typical
  of a region (`regional`) are no longer indistinguishable.
- `method` (optional) — measured | fitted | literature | assumed. Not
  recoverable for migrated records; fill it in for new ones.
- `legacy:` (optional) — columns of the migrated row that have no home in
  the current supy model (documented per column in
  `schema/table_mapping.yml`), and `-999` placeholder cells. Kept verbatim
  so nothing the legacy database asserted is lost.
- `context:` inside `parameters:` — values that condition the set without
  being model inputs themselves (e.g. which QF formulation an emission set
  was fitted for, which conductance model a g-set belongs to).

Profile records (`records/profiles/<kind>/`) hold 24-hour profiles keyed
1..24 with `working_day` and `holiday` sides, matching supy's
`HourlyProfile`; `attaches_to` names the exact config path the profile
plugs into (e.g. `anthropogenic_emissions.co2.traffprof_24hr`). The LUCY
per-country profiles live one level deeper (`traffic/lucy/<country>.yml`).

Materials and constructions describe building fabric: a material record
carries the five thermal/radiative properties of one material; a
construction record layers materials with thicknesses for a roof and wall,
ready to be compiled into supy's `vertical_layers`.

## An archetype

```yaml
archetype: archetypes/surfaces/bldgs/helsinki--kumpula
schema_version: '2026.5'
target: land_cover.bldgs
name: Kumpula
place: helsinki
uses:
  albedo: records/surfaces/bldgs/london--ward2016--albedo
  emissivity: records/surfaces/bldgs/helsinki--jarvi2014--emissivity
  drainage: records/surfaces/bldgs/helsinki--jarvi2011--drainage
  ohm:
    summer_wet: records/ohm/helsinki--jarvi2014--buildings
    summer_dry: records/ohm/helsinki--jarvi2014--buildings
    winter_wet: records/ohm/helsinki--jarvi2014--buildings
    winter_dry: records/ohm/helsinki--jarvi2014--buildings
```

`uses:` slots name the role each referenced record plays. OHM is the one
structured slot: the four season/wetness positions of supy's `ohm_coef` are
an assembly decision, so they live here rather than on the coefficient
records. An archetype may also carry its own `parameters:` (scalars asserted
by the legacy composite row itself, e.g. the snow entries) — same rules as a
record.

Because an archetype references records, its provenance is inspectable: the
Kumpula example shows at a glance that its albedo comes from a London
study — visible now, silent under the old integer-ID scheme.

## Using the database with SUEWS

```sh
python scripts/export_record.py archetypes/surfaces/bldgs/helsinki--kumpula
```

renders a model-ready fragment: every value becomes supy's RefValue form

```yaml
alb:
  value: 0.12
  ref: {ID: ward2016, DOI: 10.1016/j.uclim.2016.05.001, desc: london, city}
```

which pastes directly into a SUEWS YAML configuration under the path named
by `target` (`sites[].properties.land_cover.bldgs` here). The citation
travels with the value into the user's config. This is verified end to end:
`make validate` checks every fragment against the supy data model
(`PavedProperties`, `SnowParams`, `HourlyProfile`, ...), and fragments
merged into supy's sample configuration load through
`SUEWSConfig.from_yaml`.

## Adding a value

1. Copy the nearest record in `db/records/` as a template. Name the file
   `<place>--<sourcekey>--<what>.yml` and set `record:` to match its path.
2. Use supy's canonical parameter names (the existing records and
   `schema/table_mapping.yml` show the vocabulary; supy's own docs are the
   reference).
3. Add the citation to `db/sources.yml` (key: `<firstauthor><year>`) and,
   if the place is new, add it to `db/places.yml`.
4. Set `place`, `representativeness` and `method` honestly. `generic` is a
   valid answer; a fabricated site is not.
5. Run `make check` (structure, references, coupling rules) and, with supy
   installed, `make validate`. Open a PR; CI runs the same checks.

One value per PR is fine. A full surface description is better assembled as
an archetype referencing your new records.

## Versioning and migration

The whole database sits in git: a value's history is `git log` on its
record file, and a contribution is a reviewable diff of one or two small
YAML files. `schema_version` pins each record to the supy schema its names
were verified against; when supy's data model changes shape, records are
migrated mechanically (supy ships schema-migration tooling) and the pin
advances in the same commit — the same discipline the migration itself
followed, where `schema/migration_census.yml` proves every legacy cell
landed exactly once.

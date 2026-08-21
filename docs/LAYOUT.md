# Repository layout

The SUEWS parameter database stores values as records — one YAML file per
source-coherent parameter set — organised to feed the modern SUEWS YAML
configuration format directly. `docs/FORMAT.md` specifies the format; this
file says where things are.

## Where things are

- `db/records/` — evidence: parameter sets as measured, fitted or published.
  One file per set, parameters named by supy canonical paths.
  - `surfaces/<paved|bldgs|evetr|dectr|grass|bsoil|water|common>/` — per-
    surface properties (albedo, emissivity, drainage, soils, phenology,
    thermal layers, ...); `common/` holds surface-agnostic sets such as
    soil types
  - `ohm/` — OHM coefficient sets {a1, a2, a3}
  - `snow/` — snow-parameter fragments
  - `conductance/`, `irrigation/`, `anthropogenic/` — site-level parameter
    sets
  - `profiles/<kind>/` — 24-hour profiles (traffic, human activity, energy
    use, water use, snow removal, population); `<kind>/lucy/` holds the
    LUCY per-country defaults
  - `materials/`, `constructions/` — building-fabric materials and layered
    roof/wall assemblies
- `db/archetypes/` — assembly: curated combinations referencing records.
  - `surfaces/<surface>/` — complete surface descriptions (the old
    NonVeg/Veg/Water composites)
  - `snow/` — snow parameter sets
  - `regions/`, `countries/` — the 22-region / 229-country default sets
  - `typologies/` — building typology metadata
- `db/provenance/` — record-path-mirrored provenance assessment sidecars:
  evidence roles and locators prepared by an agent or human assessor, plus
  GitHub-backed verifier attestations. The directory is populated
  incrementally as records enter the audit workflow.
- `db/sources.yml` — citation registry (keys like `ward2016`)
- `db/places.yml` — place registry (slugs like `helsinki`, `se-england`)
- `schema/table_mapping.yml` — the audited legacy-column -> supy-path map
  the migration implemented, including what has no supy home and why
- `schema/origins_map.yml` — how each raw legacy `Origin` string resolves
  to a place, with confidence flags
- `schema/migration_census.yml` — generated: proof that every non-null cell
  of every legacy row landed exactly once in the record tree
- `schema/parameter_groups.yml` — which parameters have to move together,
  and how tightly; the cross-record rules `make check` enforces
- `schema/provenance-assessment.schema.yml` — the machine-readable format for
  provenance sidecars, assessment states, revision fingerprints and verifier
  attestations
- `schema/regional_axis.yml` — how the database slices by place and region
- `scripts/` — the checking, export and migration tools
- `scripts/legacy/` — the pre-migration spreadsheet toolchain, kept for
  reference

## Commands

- `make check` — structural integrity (paths, references, sources, places)
  plus the coupling rules from parameter_groups; warnings for
  scientifically inconsistent combinations
- `make check-strict` — as check, coupling warnings fail
- `make validate` — check plus validation of every parameter fragment
  against the supy data model, pinned to the version the records'
  schema_version was verified against (needs network access the first
  time, to fetch supy)
- `make verify` — reverse-verify the record tree against the pre-migration
  tables in git history: coverage, value multisets, profile-hour and
  thermal-layer positions, pointer resolution and citations
- `make export REC=<record-path>` — render a record or archetype as a
  model-ready fragment with per-value citations
- `scripts/e2e_sample_config.py` — end-to-end proof: exported fragments
  merged into supy's sample configuration load through
  `SUEWSConfig.from_yaml` with citations intact

## Naming

Record files are `<place>--<sourcekey>[--<family>][--<qualifier>].yml`:
`helsinki--jarvi2014--phenology.yml` reads as "measured/fitted at Helsinki,
per Järvi et al. 2014, the phenology set". The `record:` field inside always
equals the file's path under `db/` without `.yml`, and `make check` enforces
the match. Citation keys are `<firstauthor><year>`; place slugs are plain
kebab-case.

## History and the legacy formats

The database has lived in three shapes: a single Excel workbook, then a
1:1 YAML rendering of its tables, then this record format. The migrations
were scripted and census-verified, never re-keyed by hand:

- the workbook -> table migration is provable with
  `scripts/legacy/verify_roundtrip.py` against `schema/provenance.yml`
- the table -> record migration is provable with
  `scripts/migrate_to_records.py` (re-runnable from the pre-migration git
  tree) against `schema/migration_census.yml`
- every record carries `legacy_id`, the 8-digit row ID it came from, so any
  value can be traced back through both migrations to a workbook cell

The last workbook built from the table-format database is preserved as a
release asset for UMEP's spreadsheet-based tooling, and the original
pre-migration workbook remains retrievable from git history.

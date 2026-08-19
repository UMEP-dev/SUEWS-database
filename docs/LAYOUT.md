# Repository layout

The SUEWS parameter database is a small relational store. It used to live in a
single Excel workbook; it now lives in YAML, one file per table, and the
workbook is generated from it.

## Where things are

- `db/` — the database. One YAML file per table, each a dictionary keyed by
  the integer `ID`. **This is the canonical copy: edit these files.**
- `schema/tables.yml` — table registry. Column order, ID prefixes, entry
  counts, and the detected foreign keys. Generated.
- `schema/parameter_groups.yml` — which parameters have to move together, and
  how tightly. Hand-maintained.
- `schema/regional_axis.yml` — how the database is to be sliced by region.
  Hand-maintained.
- `schema/origins_inventory.yml` — every distinct `Origin` string, as a
  work-list for the regional axis. Generated, but preserves what has been
  filled in by hand.
- `scripts/` — the conversion, verification and checking tools.
- `database.xlsx` — generated from `db/`. Retained because the UMEP SUEWS
  Database Manager and Prepare QGIS plugins still ship a copy of it. Do not
  edit it; changes made there will be overwritten by `make xlsx`.

## Commands

- `make yaml` — rebuild `db/` and `schema/tables.yml` from `database.xlsx`.
  Only needed if the workbook is edited upstream.
- `make xlsx` — rebuild `database.xlsx` from `db/`. Run after changing data.
- `make verify` — prove `db/` reproduces `database.xlsx` cell for cell.
- `make check` — referential, linkage and hygiene checks.
- `make origins` — refresh the `Origin` work-list.

## How records reference each other

Every primary key is an eight-digit integer whose first two digits identify the
owning table, so a bare `90240002` in a `Ref` column resolves without further
context. The remaining digits are a serial with no meaning.

- `10` — Region (`db/region.yml`, 22 entries)
- `11` — Country (`db/country.yml`, 229 entries)
- `12`, `13` — Types (`db/types.yml`, 14 entries)
- `20` — NonVeg (`db/non_veg.yml`, 23 entries)
- `22` — Soil (`db/soil.yml`, 13 entries)
- `23` — Snow (`db/snow.yml`, 3 entries)
- `24` — Veg (`db/veg.yml`, 26 entries)
- `25` — Water (`db/water.yml`, 2 entries)
- `30` — Biogen CO2 (`db/biogen_co2.yml`, 18 entries)
- `31` — Leaf Area Index (`db/leaf_area_index.yml`, 13 entries)
- `32` — Leaf Growth Power (`db/leaf_growth_power.yml`, 15 entries)
- `33` — Max Vegetation Conductance (`db/max_vegetation_conductance.yml`, 17 entries)
- `34` — Porosity (`db/porosity.yml`, 3 entries)
- `35` — Vegetation Growth (`db/vegetation_growth.yml`, 6 entries)
- `36` — Spartacus Material (`db/spartacus_material.yml`, 62 entries)
- `37` — Spartacus Surface (`db/spartacus_surface.yml`, 22 entries)
- `38` — SnowLimPatch (`db/snow_lim_patch.yml`, 6 entries)
- `40` — Emissivity (`db/emissivity.yml`, 22 entries)
- `41` — Albedo (`db/albedo.yml`, 19 entries)
- `42` — Water State (`db/water_state.yml`, 10 entries)
- `43` — Water Storage (`db/water_storage.yml`, 9 entries)
- `44` — Conductance (`db/conductance.yml`, 3 entries)
- `45` — Drainage (`db/drainage.yml`, 18 entries)
- `50` — OHM (`db/ohm.yml`, 44 entries)
- `51` — ANOHM (`db/anohm.yml`, 13 entries)
- `52` — ESTM (`db/estm.yml`, 13 entries)
- `53` — AnthropogenicEmission (`db/anthropogenic_emission.yml`, 32 entries)
- `60` — Profiles (`db/profiles.yml`, 947 entries)
- `61` — Irrigation (`db/irrigation.yml`, 5 entries)
- `90`, `99` — References (`db/references.yml`, 67 entries)

The tables layer into three tiers:

- **Entry points.** `Region` and `Country` hold a complete default parameter
  set, pointing at surface and profile entries.
- **Surface types.** `NonVeg`, `Veg`, `Water` and `Snow` describe a surface by
  pointing at one row in each property table.
- **Property tables.** `Albedo`, `Emissivity`, `OHM` and the rest hold the
  values themselves, each citing a row in `References`.

`schema/tables.yml` lists the foreign keys for each table, detected by
resolving every eight-digit value back to the table owning its prefix.

## Adding a value

1. Add the citation to `db/references.yml` if it is not already there.
2. Add the value to the relevant property table with a new ID that continues
   that table's prefix.
3. Point a surface entry at it, or add a new surface entry.
4. Record its `Origin`, then run `make origins` and fill in the new row.
5. Run `make check`, then `make xlsx`.

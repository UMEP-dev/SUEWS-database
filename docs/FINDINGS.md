# Data-quality findings

Issues visible in the database as it stands. **Nothing here has been changed.**
Both migrations — workbook to tables, tables to records — were format-only,
so that these can be reviewed and fixed one at a time against a stable base.

The findings below were written against the table format; the values they
describe now live in `db/records/` and `db/archetypes/`, and every record's
`legacy_id` field traces back to the row IDs cited here. The record
migration itself surfaced a few more: the `-999` placeholder cells now
quarantined under records' `legacy:` blocks, two dangling reference IDs
(90241000, 90240027) kept as `source_legacy_ref`, and a third (Holiday)
day type on the Shanghai commercial energy-use profile, kept as a
`public_holiday` side.

Findings marked *(automated)* are re-checked by `make check`.

## 1. The evergreen and deciduous LAI ranges are the same at three sites

`db/leaf_area_index.yml` holds the same two ranges under three different place
labels with three different citations:

- `LAIMin=4`, `LAIMax=5.1` (evergreen tree) appears for Shanghai
  (`31240018`, citing Ao et al. 2018), Beijing (`31240023`, citing Ward et al.
  2016) and SE England (`31240044`, citing Ward et al. 2016).
- `LAIMin=1`, `LAIMax=5.5` (deciduous tree) appears for Shanghai
  (`31240019`, Ao et al. 2018), Beijing (`31240024`, Ward, Evans & Grimmond
  2013) and SE England (`31240045`, Ward et al. 2016).

Two things follow. First, the two Beijing rows cite UK papers: Ward et al.
(2016) is the Swindon and London SUEWS development and evaluation paper, and
Ward, Evans & Grimmond (2013) is also a UK site. Every other Beijing row in the
database that carries a reference cites Zheng et al. (2023), the Beijing SUEWS
study. Second, and more seriously, the Beijing evergreen row is identical to
the SE England evergreen row in both values and citation apart from `LAIEq`,
which suggests the row was copied and relabelled rather than sourced.

Correcting the citation alone would not fix this, because the values are not
site-specific either. Needs a decision on whether Beijing LAI values exist to
be substituted, or whether the rows should be withdrawn.

## 2. Three surface entries mix LAI parameters fitted for different equations *(automated)*

Each `Veg` entry points at one `Leaf Area Index` row and one
`Leaf Growth Power` row. Both tables carry a `LAIEq` column, and in three cases
they disagree, so growth-power coefficients fitted for one LAI equation are
paired with a different one:

- `24240002` — Grass, Vancouver: `LAIEq=1` against `LAIEq=0`
- `24240025` — Grass, Swindon: `LAIEq=0` against `LAIEq=1`
- `24240028` — Grass, London: `LAIEq=0` against `LAIEq=1`

`24240028` is the grass default that 14 of the 22 `Region` entries point at, so
this one propagates into most regional defaults.

## 3. Three references do not exist *(automated)*

- `db/porosity.yml` `34240010` and `34240011` cite `90241000`
- `db/estm.yml` `52240021` cites `90240027`

Neither ID is in `db/references.yml`. `90240027` falls in a gap between
`90240026` and `90240030`, which suggests a reference row was deleted without
its citations being updated. `90241000` is outside the block of allocated
reference IDs altogether and looks like a placeholder.

## 4. Degree-day thresholds were not localised with their base temperature

`db/vegetation_growth.yml` splits into a Helsinki set (`BaseT=5`, `BaseTe=10`)
and an SE England set (`BaseT=6`, `BaseTe=11`), but both keep `GDDFull=300` and
`SDDFull=-450`. Degree-day totals are accumulated relative to the base
temperature, so changing one without the other changes what the threshold
means. See `schema/parameter_groups.yml`, group `vegetation_growth_thresholds`.

## 5. `Origin` is free text and cannot be used as an axis yet *(automated)*

50 distinct strings cover roughly 20 places. The problems are:

- One place under several spellings: `Helsinki`, `Helsinki, Finland`,
  `Helsinki-Kumpula`; `London`, `London, UK`, `Barbican, London`,
  `London (River Thames), UK`; `Swindon`, `Swindon, UK`,
  `Swindon, UK (not used)`; `Gothenburg`, `Gothenburg, Sweden`.
- Mixed granularity: cities, countries, `SE England`, `Tropics`, `UK`.
- A typo: `Vanvouver` (`db/ohm.yml`, `50240014`, whose own `Name` spells it
  correctly).
- Not places at all: `Generic`, `Placeholder`, `Unknown`, `Unspecified`,
  `SUEWS`, and two `Porosity` rows (`34240010`, `34240011`) where the
  description has been entered in the `Origin` column.

The full work-list is `schema/origins_inventory.yml`.

## 6. Descriptive fields carry noise

Names and descriptions have accumulated editing artefacts that will show up in
any generated documentation or web interface:

- A misspelling repeated in two `Porosity` rows: "Porisity only used for
  Deciduous Tree".
- Trailing spaces in names, for example `City centre ` in `db/non_veg.yml`.
- Bracketed working notes left in user-facing names, for example
  `[sample run]`, `[old code: 11]`, `[800]`, and `Unclear one` as an
  `AnthropogenicEmission` name.

## Out of scope for this repository

Unit rendering faults, where a cubed unit appears as a caret expression rather
than a superscript, are in the SUEWS documentation build, not in this database.
No units are stored here at all: there is not a single caret character anywhere
in `db/`, and unit strings are attached downstream when the parameter tables
are rendered. Fixing them belongs in the documentation repository.

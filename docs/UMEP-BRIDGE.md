# Feeding UMEP from the record database: three options

The database is now record-based YAML (`db/records/`, `db/archetypes/`), and
the YAML is the canonical store. The UMEP Database Manager / Database Prepare
plugins currently read the legacy Excel workbook and load it into an internal
dictionary; nothing in the plugin UI is hardcoded to the workbook beyond that
read step. Three ways to connect the two, in increasing order of change on
the UMEP side:

## Option 1 — export the records back to the legacy workbook

A reverse exporter rebuilds the table-format workbook from the records.

- Every migrated record kept its `legacy_id`, so existing rows reconstruct
  exactly; records added since get fresh IDs in the right table ranges.
- The retired table-YAML→workbook builder
  (`scripts/legacy/yaml_to_xlsx.py`) already produces the workbook; what is
  new is a records→table-YAML step, which is the migration mapping run in
  reverse (`schema/table_mapping.yml` documents it per column).
- UMEP-side change: none. The workbook regenerates on release, so the plugin
  always reads current data.
- Cost: the reverse mapping must stay maintained as the record format
  evolves; new-format-only fields (e.g. provenance detail) cannot travel.

## Option 2 — write UMEP's dictionary directly

The plugin reads the workbook into a dictionary and works from that. If the
dictionary format (or the reading code) is shared, the database can emit that
dictionary straight from the records.

- Removes the workbook middleman and its column bookkeeping.
- UMEP-side change: one load function reads a shipped file (JSON/pickle/
  whatever the current in-memory shape serialises to) instead of parsing the
  workbook; the rest of the plugin is untouched.
- Needs: the current XLSX→dictionary code, to fix the exact shape.

## Option 3 — UMEP reads the records natively

A small reader (importable module, maintained in this repository) loads
records/archetypes and exposes them in the plugin's dictionary shape; supy is
already a UMEP dependency, so the data model and validation come free.

- Records become the single source with no export artefact at all; new
  provenance fields are available to the plugin (citations per value in the
  UI, for instance).
- Most work, and it couples plugin releases to this repository's format —
  best treated as the long-run target rather than the first step.

## Suggested path

Option 1 immediately, as the compatibility guarantee (nothing breaks, the
plugin keeps working from a regenerated workbook); Option 2 as the target
once the dictionary format is in hand, retiring the workbook to a
convenience artefact; Option 3 revisited when the record format has settled
and there is appetite on the plugin side. The three are not exclusive — 1
and 2 share the same reverse-mapping core, and 3 reuses 2's dictionary
shape.

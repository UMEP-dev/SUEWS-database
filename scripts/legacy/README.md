# Legacy toolchain

The pre-record spreadsheet/table toolchain, kept for reference and for
reproducing the two scripted migrations. These scripts operate on the
table-format `db/*.yml` files, which were removed when the record format
landed — run them from the pre-migration git tree (the commit before
`db/records/` appeared) if you need them.

- `xlsx_to_yaml.py` — workbook -> table-format YAML (first migration)
- `yaml_to_xlsx.py` — table-format YAML -> workbook (the frozen release
  asset was the last output of this)
- `verify_roundtrip.py` — proves the table YAML reproduced the migrated
  workbook, against `schema/provenance.yml`
- `check_consistency.py` — referential/linkage/hygiene checks over the
  tables; succeeded by `scripts/check_db.py`
- `build_origins_inventory.py` — the Origin work-list generator; succeeded
  by `schema/origins_map.yml`
- `record_provenance.py` — workbook fingerprint recorder
- `suewsdb.py` — shared helpers for the above

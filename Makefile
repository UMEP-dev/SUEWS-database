UV := uv run --with pyyaml --with openpyxl --no-project python

# Optional reference workbook for `make verify`, e.g. the release asset:
#   make verify XLSX=~/Downloads/database.xlsx
XLSX ?=

.PHONY: help xlsx verify check origins yaml

help:
	@echo "xlsx    - build database.xlsx from db/*.yml (untracked; the release asset)"
	@echo "verify  - prove db/*.yml still reproduces the migrated workbook"
	@echo "          add XLSX=<path> to also compare cell by cell"
	@echo "check   - referential, linkage and hygiene checks over db/*.yml"
	@echo "origins - refresh schema/origins_inventory.yml from db/*.yml"
	@echo "yaml    - one-off: rebuild db/ from a workbook (migration bootstrap)"

xlsx:
	$(UV) scripts/yaml_to_xlsx.py

verify:
	$(UV) scripts/verify_roundtrip.py $(XLSX)

check:
	$(UV) scripts/check_consistency.py

origins:
	$(UV) scripts/build_origins_inventory.py

yaml:
	$(UV) scripts/xlsx_to_yaml.py

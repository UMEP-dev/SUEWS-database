UV := uv run --with pyyaml --with openpyxl --no-project python

.PHONY: help yaml xlsx verify

help:
	@echo "yaml    - regenerate db/*.yml and schema/tables.yml from database.xlsx"
	@echo "xlsx    - regenerate database.xlsx from db/*.yml"
	@echo "verify  - prove db/*.yml reproduces database.xlsx cell for cell"

yaml:
	$(UV) scripts/xlsx_to_yaml.py

xlsx:
	$(UV) scripts/yaml_to_xlsx.py

verify:
	$(UV) scripts/verify_roundtrip.py

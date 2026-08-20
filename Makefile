PY := python3
UV := uv run --with pyyaml --no-project python
UVSUPY := uv run --with pyyaml --with supy --no-project python

.PHONY: help check check-strict validate export

help:
	@echo "check        - structure, references, places/sources and coupling rules"
	@echo "check-strict - as check, but coupling warnings fail the run"
	@echo "validate     - check + validate every fragment against the supy data model"
	@echo "export       - usage: make export REC=records/surfaces/grass/helsinki--jarvi2014--phenology"
	@echo ""
	@echo "The legacy spreadsheet toolchain (xlsx/verify/origins/yaml) is retired:"
	@echo "the last workbook built from the table-format database is a release"
	@echo "asset, and the pre-migration tooling lives in scripts/legacy/."

check:
	$(UV) scripts/check_db.py

check-strict:
	$(UV) scripts/check_db.py --strict

validate:
	$(UVSUPY) scripts/check_db.py --supy

export:
	$(UVSUPY) scripts/export_record.py $(REC)

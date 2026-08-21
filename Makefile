PY := python3
# VIRTUAL_ENV= keeps an active venv from leaking into uv's resolution; the
# supy pin is the version the records' schema_version was verified against.
SUPY_VERSION := 2026.6.5
PYYAML_VERSION := 6.0.3
JSONSCHEMA_VERSION := 4.26.0
RFC8785_VERSION := 0.1.4
UV := VIRTUAL_ENV= uv run --with "pyyaml==$(PYYAML_VERSION)" --no-project python
UVCHECK := VIRTUAL_ENV= uv run --with "pyyaml==$(PYYAML_VERSION)" --with "jsonschema==$(JSONSCHEMA_VERSION)" --with "rfc8785==$(RFC8785_VERSION)" --no-project python
UVSUPY := VIRTUAL_ENV= uv run --with "pyyaml==$(PYYAML_VERSION)" --with "jsonschema==$(JSONSCHEMA_VERSION)" --with "rfc8785==$(RFC8785_VERSION)" --with "supy==$(SUPY_VERSION)" --no-project python

.PHONY: help check check-strict check-signoffs test audit-plan validate verify export

help:
	@echo "check        - structure, references, places/sources and coupling rules"
	@echo "check-strict - as check, but coupling warnings fail the run"
	@echo "check-signoffs - authenticate stored verifier events against GitHub"
	@echo "test         - provenance and export regression tests"
	@echo "audit-plan   - dry-run evidence/composition audit coverage"
	@echo "validate     - check + validate every fragment against the supy data model"
	@echo "verify       - reverse-verify the record tree against the pre-migration tables in git history"
	@echo "export       - usage: make export REC=records/surfaces/grass/helsinki--jarvi2014--phenology"
	@echo ""
	@echo "The legacy spreadsheet toolchain (xlsx/verify/origins/yaml) is retired:"
	@echo "the last workbook built from the table-format database is a release"
	@echo "asset, and the pre-migration tooling lives in scripts/legacy/."

check:
	$(UVCHECK) scripts/check_db.py

check-strict:
	$(UVCHECK) scripts/check_db.py --strict

check-signoffs:
	$(UVCHECK) scripts/github_attestation.py

test:
	$(UVCHECK) -m unittest discover -s tests -v

audit-plan:
	$(UVCHECK) scripts/provenance_runner.py plan --mode all

validate:
	$(UVSUPY) scripts/check_db.py --supy

verify:
	$(UV) scripts/verify_migration.py

export:
	$(UV) scripts/export_record.py $(REC)

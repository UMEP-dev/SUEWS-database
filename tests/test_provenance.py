from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from provenance import (  # noqa: E402
    canonical_revision,
    check_provenance,
    derive_verification_state,
    evidence_revision,
    load_provenance_sidecars,
    signoff_eligible,
)


RECORD_KEY = "records/ohm/kyoto--paper1999--buildings"
RECORD = {
    "record": RECORD_KEY,
    "schema_version": "2026.5",
    "target": "ohm_coefficients",
    "name": "Kyoto canyon",
    "place": "kyoto",
    "representativeness": "site",
    "source": "paper1999",
    "method": "fitted",
    "parameters": {"a1": 0.71, "a2": 0.04, "a3": -39.7},
}
SOURCE = {
    "author": "A. Example",
    "title": "Example parameter publication",
    "type": "journalArticle",
    "year": 1999,
}
PLACE = {
    "name": "Kyoto",
    "lat": 35.03,
    "lon": 135.78,
    "country": "japan",
}
POLICY_REVISION = "sha256:" + "1" * 64


def fixture_sidecar():
    path = (
        ROOT
        / "tests/fixtures/provenance/valid/records/ohm"
        / "kyoto--paper1999--buildings.yml"
    )
    return yaml.safe_load(path.read_text())


def registries(record=None):
    record = record or deepcopy(RECORD)
    return {record["record"]: record}, {"paper1999": deepcopy(SOURCE)}, {
        "kyoto": deepcopy(PLACE)
    }


def attestation(
    event_id,
    *,
    verifier="SueVerifier",
    verifier_id=12345,
    decision="verified",
    signed_at="2026-08-21T13:00:00Z",
    evidence=None,
    supersedes=None,
):
    item = {
        "verifier": verifier,
        "verifier_id": verifier_id,
        "decision": decision,
        "signed_at": signed_at,
        "event": {
            "kind": "issue",
            "id": event_id,
            "url": f"https://github.com/UMEP-dev/SUEWS-database/issues/{event_id}",
        },
        "evidence_revision": evidence,
        "verifier_policy_revision": POLICY_REVISION,
        "scope": "record",
    }
    if supersedes is not None:
        item["supersedes_event"] = {
            "kind": "issue",
            "id": supersedes,
        }
    return item


def authenticated_fact(item, provenance_record=RECORD_KEY):
    return {
        "author": item["verifier"],
        "author_id": item["verifier_id"],
        "signed_at": item["signed_at"],
        "url": item["event"]["url"],
        "repository": "UMEP-dev/SUEWS-database",
        "decision": item["decision"],
        "provenance_record": provenance_record,
        "evidence_revision": item["evidence_revision"],
        "verifier_policy_revision": item["verifier_policy_revision"],
        "scope": item["scope"],
        "supersedes_event": item.get("supersedes_event"),
    }


def verifier_policy(required=1):
    return {
        "revision": POLICY_REVISION,
        "required_signoffs": required,
        "required_scopes": ["record"],
        "verifiers": {
            "SueVerifier": {
                "github_user_id": 12345,
                "scopes": ["record"],
            },
            "OtherVerifier": {
                "github_user_id": 54321,
                "scopes": ["record"],
            },
        },
    }


def calculated_record(key):
    return {
        "record": key,
        "schema_version": "2026.5",
        "target": "ohm_coefficients",
        "name": key.rsplit("/", 1)[-1],
        "source": "unreferenced",
        "method": "calculated",
        "parameters": {"a1": 0.1, "a2": 0.2, "a3": -1.0},
    }


def calculated_sidecar(record, input_keys, records, *, kind="scaled"):
    evidence = [
        {"id": f"input-{index}", "record": key, "role": "input"}
        for index, key in enumerate(input_keys, start=1)
    ]
    support = {"conclusion": "supported", "evidence_ids": ["input-1"]}
    sidecar = {
        "provenance_record": record["record"],
        "provenance_format_version": "1.0",
        "record_revision": canonical_revision(record),
        "dependency_revisions": {
            "sources": {},
            "places": {},
            "records": {
                key: canonical_revision(records[key]) for key in set(input_keys)
            },
        },
        "assessment": {
            "status": "agent_assessed",
            "assessed_at": "2026-08-21T12:00:00Z",
            "assessor": {
                "kind": "agent",
                "name": "fixture-agent",
                "version": "1.0",
            },
            "method": "calculated",
            "evidence_revision": "sha256:" + "0" * 64,
            "findings": {
                "name": deepcopy(support),
                "target": deepcopy(support),
                "values": deepcopy(support),
                "source": {"conclusion": "not_applicable"},
                "place": {"conclusion": "not_applicable"},
                "representativeness": {"conclusion": "not_applicable"},
                "method": deepcopy(support),
                "identity": deepcopy(support),
            },
            "evidence": evidence,
            "derivation": {"kind": kind, "expression": "input * 1"},
        },
        "verification": {"attestations": []},
    }
    sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
    return sidecar


def composition_fixture():
    component_key = "records/ohm/component"
    composite_key = "archetypes/surfaces/bldgs/composite"
    component = calculated_record(component_key)
    composite = {
        "archetype": composite_key,
        "schema_version": "2026.5",
        "target": "land_cover.bldgs",
        "name": "Reviewed composite",
        "uses": {"ohm": {"summer_wet": component_key}},
    }
    records = {component_key: component, composite_key: composite}
    support = {
        "conclusion": "supported",
        "evidence_ids": ["selected-component"],
    }
    sidecar = {
        "provenance_record": composite_key,
        "review_type": "composition",
        "provenance_format_version": "1.0",
        "record_revision": canonical_revision(composite),
        "dependency_revisions": {
            "sources": {},
            "places": {},
            "records": {component_key: canonical_revision(component)},
        },
        "assessment": {
            "status": "agent_assessed",
            "assessed_at": "2026-08-21T12:00:00Z",
            "assessor": {
                "kind": "agent",
                "name": "fixture-agent",
                "version": "1.0",
            },
            "method": "assembled",
            "evidence_revision": "sha256:" + "0" * 64,
            "findings": {
                "name": deepcopy(support),
                "target": deepcopy(support),
                "values": {"conclusion": "not_applicable"},
                "source": {"conclusion": "not_applicable"},
                "place": {"conclusion": "not_applicable"},
                "representativeness": {"conclusion": "not_applicable"},
                "method": deepcopy(support),
                "identity": deepcopy(support),
            },
            "evidence": [
                {
                    "id": "selected-component",
                    "record": component_key,
                    "role": "component",
                    "note": "Selected for the composite's summer-wet OHM slot.",
                }
            ],
        },
        "verification": {"attestations": []},
    }
    sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
    return records, composite_key, sidecar


class ProvenanceFixtureTests(unittest.TestCase):
    def test_valid_fixture(self):
        base = ROOT / "tests/fixtures/provenance/valid"
        sidecars, load_errors = load_provenance_sidecars(base)
        records, sources, places = registries()
        self.assertEqual(load_errors, [])
        self.assertEqual(set(sidecars), {RECORD_KEY})
        self.assertEqual(
            check_provenance(records, sources, places, sidecars), []
        )

    def test_malformed_fixture_reports_schema_error_without_crashing(self):
        base = ROOT / "tests/fixtures/provenance/invalid"
        sidecars, load_errors = load_provenance_sidecars(base)
        records, sources, places = registries()
        self.assertEqual(load_errors, [])
        errors = check_provenance(records, sources, places, sidecars)
        self.assertTrue(any("schema" in error for error in errors))

    def test_missing_sidecar_directory_is_valid_incremental_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            sidecars, errors = load_provenance_sidecars(Path(temp) / "missing")
        self.assertEqual(sidecars, {})
        self.assertEqual(errors, [])
        self.assertEqual(derive_verification_state(None, RECORD), "unaudited")


class ProvenanceSemanticTests(unittest.TestCase):
    def test_declared_urban_setting_requires_a_review_finding(self):
        record = deepcopy(RECORD)
        record["urban_setting"] = "city_centre"
        records, sources, places = registries(record)
        sidecar = fixture_sidecar()
        sidecar["record_revision"] = canonical_revision(record)
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)

        errors = check_provenance(
            records, sources, places, {RECORD_KEY: sidecar}
        )
        self.assertTrue(
            any("has no urban_setting finding" in error for error in errors),
            errors,
        )

        sidecar["assessment"]["findings"]["urban_setting"] = {
            "conclusion": "not_applicable"
        }
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
        errors = check_provenance(
            records, sources, places, {RECORD_KEY: sidecar}
        )
        self.assertTrue(
            any("cannot be not_applicable" in error for error in errors), errors
        )
        self.assertFalse(signoff_eligible(sidecar, record))

        sidecar["assessment"]["findings"]["urban_setting"] = {
            "conclusion": "supported",
            "evidence_ids": ["parameter-publication"],
        }
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
        self.assertEqual(
            check_provenance(
                records, sources, places, {RECORD_KEY: sidecar}
            ),
            [],
        )

    def test_declared_applicable_scale_requires_a_review_finding(self):
        record = deepcopy(RECORD)
        record["applicable_scale"] = "facet"
        records, sources, places = registries(record)
        sidecar = fixture_sidecar()
        sidecar["record_revision"] = canonical_revision(record)
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)

        errors = check_provenance(
            records, sources, places, {RECORD_KEY: sidecar}
        )
        self.assertTrue(
            any("has no applicable_scale finding" in error for error in errors),
            errors,
        )

        sidecar["assessment"]["findings"]["applicable_scale"] = {
            "conclusion": "not_applicable"
        }
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
        errors = check_provenance(
            records, sources, places, {RECORD_KEY: sidecar}
        )
        self.assertTrue(
            any("cannot be not_applicable" in error for error in errors), errors
        )
        self.assertFalse(signoff_eligible(sidecar, record))

        sidecar["assessment"]["findings"]["applicable_scale"] = {
            "conclusion": "supported",
            "evidence_ids": ["parameter-publication"],
        }
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
        self.assertEqual(
            check_provenance(
                records, sources, places, {RECORD_KEY: sidecar}
            ),
            [],
        )

    def test_blocking_status_must_name_the_optional_setting_finding(self):
        record = deepcopy(RECORD)
        record["urban_setting"] = "city_centre"
        records, sources, places = registries(record)
        legacy_records, legacy_sources, legacy_places = registries()

        for status in ("unresolved", "source_inaccessible", "curation_required"):
            with self.subTest(status=status):
                missing = fixture_sidecar()
                missing["assessment"]["status"] = status
                missing["assessment"]["evidence_revision"] = evidence_revision(
                    missing
                )
                errors = check_provenance(
                    legacy_records,
                    legacy_sources,
                    legacy_places,
                    {RECORD_KEY: missing},
                )
                self.assertTrue(
                    any("schema" in error for error in errors), errors
                )

                sidecar = fixture_sidecar()
                sidecar["record_revision"] = canonical_revision(record)
                sidecar["assessment"]["status"] = status
                sidecar["assessment"]["scientific_note"] = (
                    "The urban setting remains blocked in this fixture."
                )
                if status == "source_inaccessible":
                    sidecar["assessment"]["attempted_sources"] = [
                        {
                            "source": "paper1999",
                            "attempted_at": "2026-08-21T12:00:00Z",
                            "outcome": "inaccessible",
                            "note": "The source could not be accessed.",
                        }
                    ]
                sidecar["assessment"]["findings"]["urban_setting"] = {
                    "conclusion": status,
                    "evidence_ids": ["parameter-publication"],
                }
                sidecar["assessment"]["evidence_revision"] = evidence_revision(
                    sidecar
                )
                self.assertEqual(
                    check_provenance(
                        records, sources, places, {RECORD_KEY: sidecar}
                    ),
                    [],
                )

    def test_revision_changes_are_detected_but_operational_fields_are_excluded(self):
        sidecar = fixture_sidecar()
        records, sources, places = registries()

        changed_records = deepcopy(records)
        changed_records[RECORD_KEY]["name"] = "Changed name"
        self.assertTrue(
            any(
                "record_revision is stale" in error
                for error in check_provenance(
                    changed_records, sources, places, {RECORD_KEY: sidecar}
                )
            )
        )

        changed_sources = deepcopy(sources)
        changed_sources["paper1999"]["title"] = "Corrected title"
        self.assertTrue(
            any(
                "sources.paper1999" in error
                for error in check_provenance(
                    records, changed_sources, places, {RECORD_KEY: sidecar}
                )
            )
        )

        changed_places = deepcopy(places)
        changed_places["kyoto"]["name"] = "Kyoto City"
        self.assertTrue(
            any(
                "places.kyoto" in error
                for error in check_provenance(
                    records, sources, changed_places, {RECORD_KEY: sidecar}
                )
            )
        )

        changed_finding = deepcopy(sidecar)
        changed_finding["assessment"]["findings"]["values"]["note"] = "New claim"
        self.assertTrue(
            any(
                "evidence_revision is stale" in error
                for error in check_provenance(
                    records, sources, places, {RECORD_KEY: changed_finding}
                )
            )
        )

        operational = deepcopy(sidecar)
        operational["assessment"]["assessed_at"] = "2026-08-22T12:00:00Z"
        operational["assessment"]["assessor"]["name"] = "rerun-agent"
        operational["assessment"]["operational_note"] = "Retry metadata only"
        self.assertEqual(
            check_provenance(
                records, sources, places, {RECORD_KEY: operational}
            ),
            [],
        )

    def test_source_alignment_and_evidence_ids(self):
        sidecar = fixture_sidecar()
        records, sources, places = registries()
        sidecar["assessment"]["findings"]["values"]["evidence_ids"] = [
            "missing-evidence"
        ]
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
        errors = check_provenance(
            records, sources, places, {RECORD_KEY: sidecar}
        )
        self.assertTrue(any("unknown evidence id" in error for error in errors))

        mismatch_record = deepcopy(RECORD)
        mismatch_record["source"] = "otherpaper"
        mismatch_records = {RECORD_KEY: mismatch_record}
        mismatch_sources = deepcopy(sources)
        mismatch_sources["otherpaper"] = {
            "author": "B. Example",
            "title": "Different publication",
            "type": "journalArticle",
            "year": 2000,
        }
        mismatch = fixture_sidecar()
        mismatch["record_revision"] = canonical_revision(mismatch_record)
        mismatch["dependency_revisions"]["sources"]["otherpaper"] = (
            canonical_revision(mismatch_sources["otherpaper"])
        )
        mismatch["assessment"]["evidence_revision"] = evidence_revision(mismatch)
        errors = check_provenance(
            mismatch_records,
            mismatch_sources,
            places,
            {RECORD_KEY: mismatch},
        )
        self.assertTrue(any("parameter_source does not match" in e for e in errors))

    def test_evidence_parameter_paths_must_resolve_in_record(self):
        records, sources, places = registries()
        scoped = fixture_sidecar()
        scoped["assessment"]["evidence"].append(
            {
                "id": "field-validation",
                "source": "paper1999",
                "role": "validation",
                "parameter_paths": ["parameters.a1", "parameters.a2"],
                "locators": [{"kind": "table", "label": "Table 2"}],
            }
        )
        scoped["assessment"]["evidence_revision"] = evidence_revision(scoped)
        self.assertEqual(
            check_provenance(records, sources, places, {RECORD_KEY: scoped}),
            [],
        )

        missing = deepcopy(scoped)
        missing["assessment"]["evidence"][1]["parameter_paths"] = [
            "parameters.not_a_field"
        ]
        missing["assessment"]["evidence_revision"] = evidence_revision(missing)
        errors = check_provenance(
            records, sources, places, {RECORD_KEY: missing}
        )
        self.assertTrue(
            any("parameter path 'parameters.not_a_field'" in error for error in errors)
        )

    def test_field_scoped_parameter_source_cannot_validate_record_source(self):
        records, sources, places = registries()
        scoped = fixture_sidecar()
        scoped["assessment"]["evidence"][0]["parameter_paths"] = [
            "parameters.a1"
        ]
        scoped["assessment"]["evidence_revision"] = evidence_revision(scoped)
        errors = check_provenance(
            records, sources, places, {RECORD_KEY: scoped}
        )
        self.assertTrue(any("parameter_source does not match" in e for e in errors))
        self.assertFalse(signoff_eligible(scoped, RECORD))

    def test_canonical_field_sources_require_exact_scoped_evidence_and_dependencies(self):
        record = deepcopy(RECORD)
        record["parameter_provenance"] = {
            "parameters.a1": {
                "source": "otherpaper",
                "method": "literature",
                "place": "otherplace",
            }
        }
        records, sources, places = registries(record)
        sources["otherpaper"] = {
            "author": "B. Example",
            "title": "Field publication",
            "type": "journalArticle",
            "year": 2001,
        }
        places["otherplace"] = {"name": "Other place"}

        sidecar = fixture_sidecar()
        sidecar["record_revision"] = canonical_revision(record)
        sidecar["dependency_revisions"]["sources"]["otherpaper"] = (
            canonical_revision(sources["otherpaper"])
        )
        sidecar["dependency_revisions"]["places"]["otherplace"] = (
            canonical_revision(places["otherplace"])
        )
        sidecar["assessment"]["evidence"][0]["parameter_paths"] = [
            "parameters.a2",
            "parameters.a3",
        ]
        sidecar["assessment"]["evidence"].append(
            {
                "id": "field-publication",
                "source": "otherpaper",
                "role": "parameter_source",
                "parameter_paths": ["parameters.a1"],
                "locators": [{"kind": "table", "label": "Table 2"}],
            }
        )
        for scope in ("values", "source", "method"):
            sidecar["assessment"]["findings"][scope]["evidence_ids"] = [
                "parameter-publication",
                "field-publication",
            ]
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)

        self.assertNotEqual(canonical_revision(record), canonical_revision(RECORD))
        self.assertEqual(
            check_provenance(records, sources, places, {RECORD_KEY: sidecar}),
            [],
        )
        self.assertTrue(signoff_eligible(sidecar, record))

        missing_dependency = deepcopy(sidecar)
        missing_dependency["dependency_revisions"]["sources"].pop("otherpaper")
        missing_dependency["assessment"]["evidence_revision"] = evidence_revision(
            missing_dependency
        )
        errors = check_provenance(
            records, sources, places, {RECORD_KEY: missing_dependency}
        )
        self.assertTrue(
            any("missing dependency revisions ['otherpaper']" in error for error in errors),
            errors,
        )

        overclaim = deepcopy(sidecar)
        overclaim["assessment"]["evidence"][0].pop("parameter_paths")
        overclaim["assessment"]["evidence_revision"] = evidence_revision(overclaim)
        errors = check_provenance(
            records, sources, places, {RECORD_KEY: overclaim}
        )
        self.assertTrue(any("parameter_source does not match" in e for e in errors))
        self.assertFalse(signoff_eligible(overclaim, record))

    def test_derivation_self_reference_cycle_and_duplicate_mean_input(self):
        a_key = "records/ohm/calc-a"
        b_key = "records/ohm/calc-b"
        a = calculated_record(a_key)
        b = calculated_record(b_key)
        records = {a_key: a, b_key: b}

        sidecar_a = calculated_sidecar(a, [b_key], records)
        sidecar_b = calculated_sidecar(b, [a_key], records)
        errors = check_provenance(
            records, {}, {}, {a_key: sidecar_a, b_key: sidecar_b}
        )
        self.assertTrue(any("derivation cycle" in error for error in errors))

        self_ref = calculated_sidecar(a, [a_key], records)
        errors = check_provenance(records, {}, {}, {a_key: self_ref})
        self.assertTrue(any("self-reference" in error for error in errors))

        duplicate_mean = calculated_sidecar(
            a, [b_key, b_key], records, kind="arithmetic_mean"
        )
        duplicate_mean["assessment"]["derivation"].pop("expression")
        duplicate_mean["assessment"]["evidence_revision"] = evidence_revision(
            duplicate_mean
        )
        errors = check_provenance(records, {}, {}, {a_key: duplicate_mean})
        self.assertTrue(any("two distinct inputs" in e for e in errors))

    def test_source_table_inputs_can_drive_a_calculated_derivation(self):
        key = "records/ohm/external-mean"
        record = calculated_record(key)
        records = {key: record}
        sidecar = calculated_sidecar(
            record, [], records, kind="arithmetic_mean"
        )
        sidecar["dependency_revisions"]["sources"]["paper1999"] = (
            canonical_revision(SOURCE)
        )
        sidecar["assessment"]["evidence"] = [
            {
                "id": "source-table",
                "source": "paper1999",
                "role": "input_data",
                "locators": [
                    {"kind": "table", "label": "Table 1", "page": 3}
                ],
            }
        ]
        for scope in ("name", "target", "values", "method", "identity"):
            sidecar["assessment"]["findings"][scope] = {
                "conclusion": "supported",
                "evidence_ids": ["source-table"],
            }
        sidecar["assessment"]["derivation"] = {
            "kind": "arithmetic_mean",
            "expression": "(sample-a + sample-b) / 2",
            "external_inputs": [
                {
                    "id": "sample-a",
                    "evidence_id": "source-table",
                    "value": 0.1,
                },
                {
                    "id": "sample-b",
                    "evidence_id": "source-table",
                    "value": 0.2,
                },
            ],
        }
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
        self.assertEqual(
            check_provenance(
                records,
                {"paper1999": deepcopy(SOURCE)},
                {},
                {key: sidecar},
            ),
            [],
        )
        self.assertTrue(signoff_eligible(sidecar, record))

        duplicate = deepcopy(sidecar)
        duplicate["assessment"]["derivation"]["external_inputs"][1]["id"] = (
            "sample-a"
        )
        duplicate["assessment"]["evidence_revision"] = evidence_revision(
            duplicate
        )
        errors = check_provenance(
            records, {"paper1999": deepcopy(SOURCE)}, {}, {key: duplicate}
        )
        self.assertTrue(
            any("duplicate external derivation input" in error for error in errors),
            errors,
        )

        wrong_role = deepcopy(sidecar)
        wrong_role["assessment"]["evidence"][0]["role"] = "compilation"
        wrong_role["assessment"]["evidence_revision"] = evidence_revision(
            wrong_role
        )
        errors = check_provenance(
            records, {"paper1999": deepcopy(SOURCE)}, {}, {key: wrong_role}
        )
        self.assertTrue(
            any(
                "must reference source evidence with role 'input_data'" in error
                for error in errors
            ),
            errors,
        )

    def test_external_method_cannot_claim_internal_derivation(self):
        sidecar = fixture_sidecar()
        records, sources, places = registries()
        sidecar["assessment"]["derivation"] = {
            "kind": "scaled",
            "expression": "input * 1",
        }
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
        errors = check_provenance(
            records, sources, places, {RECORD_KEY: sidecar}
        )
        self.assertTrue(
            any("internal derivation" in error or "schema" in error for error in errors)
        )

    def test_composition_review_tracks_selected_components(self):
        records, composite_key, sidecar = composition_fixture()
        self.assertEqual(
            check_provenance(records, {}, {}, {composite_key: sidecar}), []
        )
        self.assertTrue(signoff_eligible(sidecar, records[composite_key]))

        skipped_method = deepcopy(sidecar)
        skipped_method["assessment"]["findings"]["method"] = {
            "conclusion": "not_applicable"
        }
        skipped_method["assessment"]["evidence_revision"] = evidence_revision(
            skipped_method
        )
        self.assertFalse(
            signoff_eligible(skipped_method, records[composite_key])
        )

        missing = deepcopy(sidecar)
        missing["assessment"]["evidence"] = []
        missing["assessment"]["evidence_revision"] = evidence_revision(missing)
        errors = check_provenance(records, {}, {}, {composite_key: missing})
        self.assertTrue(
            any("undocumented composition components" in error for error in errors)
        )


class VerificationStateTests(unittest.TestCase):
    def exact_state_inputs(self, sidecar):
        records, sources, places = registries()
        policy = verifier_policy()
        facts = {
            (item["event"]["kind"], item["event"]["id"]): authenticated_fact(item)
            for item in sidecar["verification"]["attestations"]
        }
        return records, sources, places, policy, facts

    def derive(self, sidecar, *, facts=None, policy=None, registries_=None):
        records, sources, places = registries_ or registries()
        return derive_verification_state(
            sidecar,
            records[RECORD_KEY],
            policy=policy,
            authenticated_events=facts,
            records=records,
            sources=sources,
            places=places,
            sidecars={RECORD_KEY: sidecar},
        )

    def test_exact_authenticated_intent_can_verify(self):
        sidecar = fixture_sidecar()
        item = attestation(
            101, evidence=sidecar["assessment"]["evidence_revision"]
        )
        sidecar["verification"]["attestations"] = [item]
        records, sources, places, policy, facts = self.exact_state_inputs(sidecar)
        self.assertEqual(
            self.derive(
                sidecar,
                facts=facts,
                policy=policy,
                registries_=(records, sources, places),
            ),
            "verified",
        )

    def test_unrelated_or_stale_event_cannot_verify(self):
        sidecar = fixture_sidecar()
        item = attestation(
            102, evidence=sidecar["assessment"]["evidence_revision"]
        )
        sidecar["verification"]["attestations"] = [item]
        records, sources, places, policy, facts = self.exact_state_inputs(sidecar)

        unrelated = deepcopy(facts)
        unrelated[("issue", 102)]["decision"] = "changes_requested"
        self.assertEqual(
            self.derive(sidecar, facts=unrelated, policy=policy),
            "awaiting_signoff",
        )

        stale = deepcopy(sidecar)
        stale_item = stale["verification"]["attestations"][0]
        stale_item["evidence_revision"] = "sha256:" + "9" * 64
        stale_facts = {("issue", 102): authenticated_fact(stale_item)}
        self.assertEqual(
            self.derive(stale, facts=stale_facts, policy=policy),
            "awaiting_signoff",
        )

        stale_policy = deepcopy(sidecar)
        stale_policy_item = stale_policy["verification"]["attestations"][0]
        stale_policy_item["verifier_policy_revision"] = "sha256:" + "8" * 64
        stale_policy_facts = {
            ("issue", 102): authenticated_fact(stale_policy_item)
        }
        self.assertEqual(
            self.derive(stale_policy, facts=stale_policy_facts, policy=policy),
            "awaiting_signoff",
        )

        changed_sources = deepcopy(sources)
        changed_sources["paper1999"]["title"] = "Corrected title"
        self.assertNotEqual(
            self.derive(
                sidecar,
                facts=facts,
                policy=policy,
                registries_=(records, changed_sources, places),
            ),
            "verified",
        )

    def test_core_findings_cannot_be_skipped(self):
        sidecar = fixture_sidecar()
        sidecar["assessment"]["findings"]["values"] = {
            "conclusion": "not_applicable"
        }
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
        self.assertFalse(signoff_eligible(sidecar, RECORD))

    def test_checker_invalid_sidecar_cannot_verify(self):
        sidecar = fixture_sidecar()
        sidecar["assessment"]["findings"]["identity"]["evidence_ids"] = [
            "missing-evidence"
        ]
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
        item = attestation(
            110, evidence=sidecar["assessment"]["evidence_revision"]
        )
        sidecar["verification"]["attestations"] = [item]
        facts = {("issue", 110): authenticated_fact(item)}
        self.assertEqual(
            self.derive(sidecar, facts=facts, policy=verifier_policy()),
            "unresolved",
        )

    def test_sidecar_cannot_self_declare_verified(self):
        sidecar = fixture_sidecar()
        sidecar["assessment"]["status"] = "verified"
        sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
        item = attestation(
            111, evidence=sidecar["assessment"]["evidence_revision"]
        )
        sidecar["verification"]["attestations"] = [item]
        facts = {("issue", 111): authenticated_fact(item)}
        self.assertEqual(
            self.derive(sidecar, facts=facts, policy=verifier_policy()),
            "unresolved",
        )

    def test_handle_case_does_not_count_as_two_verifiers(self):
        sidecar = fixture_sidecar()
        evidence = sidecar["assessment"]["evidence_revision"]
        first = attestation(103, verifier="SueVerifier", evidence=evidence)
        second = attestation(104, verifier="sUEvERIFIER", evidence=evidence)
        sidecar["verification"]["attestations"] = [first, second]
        facts = {
            ("issue", 103): authenticated_fact(first),
            ("issue", 104): authenticated_fact(second),
        }
        self.assertEqual(
            self.derive(sidecar, facts=facts, policy=verifier_policy(required=2)),
            "awaiting_signoff",
        )

    def test_supersession_is_order_independent_and_must_move_forward(self):
        sidecar = fixture_sidecar()
        evidence = sidecar["assessment"]["evidence_revision"]
        blocked = attestation(
            105,
            decision="changes_requested",
            signed_at="2026-08-21T13:00:00Z",
            evidence=evidence,
        )
        approved = attestation(
            106,
            decision="verified",
            signed_at="2026-08-21T14:00:00Z",
            evidence=evidence,
            supersedes=105,
        )
        facts = {
            ("issue", 105): authenticated_fact(blocked),
            ("issue", 106): authenticated_fact(approved),
        }
        for ordered in ([approved, blocked], [blocked, approved]):
            candidate = deepcopy(sidecar)
            candidate["verification"]["attestations"] = ordered
            self.assertEqual(
                self.derive(candidate, facts=facts, policy=verifier_policy()),
                "verified",
            )

        backwards = deepcopy(sidecar)
        old_approval = attestation(
            107,
            decision="verified",
            signed_at="2026-08-21T13:00:00Z",
            evidence=evidence,
            supersedes=108,
        )
        newer_block = attestation(
            108,
            decision="changes_requested",
            signed_at="2026-08-21T14:00:00Z",
            evidence=evidence,
        )
        backwards["verification"]["attestations"] = [old_approval, newer_block]
        backwards_facts = {
            ("issue", 107): authenticated_fact(old_approval),
            ("issue", 108): authenticated_fact(newer_block),
        }
        self.assertEqual(
            self.derive(
                backwards, facts=backwards_facts, policy=verifier_policy()
            ),
            "unresolved",
        )

    def test_one_event_cannot_be_reused_across_sidecars(self):
        a_key = "records/ohm/calc-a"
        b_key = "records/ohm/calc-b"
        a = calculated_record(a_key)
        b = calculated_record(b_key)
        records = {a_key: a, b_key: b}
        sidecar_a = calculated_sidecar(a, [b_key], records)
        sidecar_b = calculated_sidecar(b, [a_key], records)
        for sidecar in (sidecar_a, sidecar_b):
            sidecar["verification"]["attestations"] = [
                attestation(
                    109,
                    evidence=sidecar["assessment"]["evidence_revision"],
                )
            ]
        errors = check_provenance(
            records, {}, {}, {a_key: sidecar_a, b_key: sidecar_b}
        )
        self.assertTrue(any("already used" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

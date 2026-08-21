from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_db import load_all  # noqa: E402
from provenance import canonical_revision  # noqa: E402
from provenance_runner import (  # noqa: E402
    collect_run,
    plan_run,
    prepare_run,
)


EVIDENCE = "records/ohm/example"
COMPONENT = "records/ohm/component"
COMPOSITE = "archetypes/surfaces/bldgs/example"
SOURCE = "paper1999"


def fixtures():
    records = {
        EVIDENCE: {
            "record": EVIDENCE,
            "schema_version": "2026.5",
            "target": "ohm_coefficients",
            "name": "Example OHM",
            "source": SOURCE,
            "place": "kyoto",
            "representativeness": "site",
            "method": "literature",
            "parameters": {"a1": 0.1, "a2": 0.2, "a3": -1.0},
        },
        COMPONENT: {
            "record": COMPONENT,
            "schema_version": "2026.5",
            "target": "ohm_coefficients",
            "name": "Component OHM",
            "source": "unreferenced",
            "method": "assumed",
            "parameters": {"a1": 0.2, "a2": 0.3, "a3": -2.0},
        },
        COMPOSITE: {
            "archetype": COMPOSITE,
            "schema_version": "2026.5",
            "target": "land_cover.bldgs",
            "name": "Example composite",
            "place": "kyoto",
            "representativeness": "site",
            "uses": {"ohm": {"summer_wet": COMPONENT}},
        },
    }
    sources = {
        SOURCE: {
            "author": "A. Example",
            "title": "Example parameter publication",
            "type": "journalArticle",
            "year": 1999,
        }
    }
    places = {"kyoto": {"name": "Kyoto", "country": "japan"}}
    return records, sources, places


def supported(evidence_id):
    return {"conclusion": "supported", "evidence_ids": [evidence_id]}


def evidence_assessment():
    support = supported("parameter-publication")
    return {
        "status": "agent_assessed",
        "assessed_at": "2026-08-21T12:00:00Z",
        "assessor": {
            "kind": "agent",
            "name": "test-auditor",
            "version": "1.0",
        },
        "method": "literature",
        "findings": {
            "name": deepcopy(support),
            "target": deepcopy(support),
            "values": deepcopy(support),
            "source": deepcopy(support),
            "place": deepcopy(support),
            "representativeness": deepcopy(support),
            "method": deepcopy(support),
            "identity": deepcopy(support),
        },
        "evidence": [
            {
                "id": "parameter-publication",
                "source": SOURCE,
                "role": "parameter_source",
                "locators": [
                    {"kind": "table", "label": "Table 4", "page": 12}
                ],
            }
        ],
        "scientific_note": "The cited table states the stored triplet.",
    }


def composition_assessment():
    support = supported("selected-component")
    return {
        "status": "agent_assessed",
        "assessed_at": "2026-08-21T12:00:00Z",
        "assessor": {
            "kind": "agent",
            "name": "test-auditor",
            "version": "1.0",
        },
        "method": "assembled",
        "findings": {
            "name": deepcopy(support),
            "target": deepcopy(support),
            "values": deepcopy(support),
            "source": {"conclusion": "not_applicable"},
            "place": deepcopy(support),
            "representativeness": deepcopy(support),
            "method": deepcopy(support),
            "identity": deepcopy(support),
        },
        "evidence": [
            {
                "id": "selected-component",
                "record": COMPONENT,
                "role": "component",
                "note": "Selected for the summer-wet OHM slot.",
            }
        ],
        "scientific_note": "This review covers selection and mapping only.",
    }


def response(item, assessment):
    return {
        "response_format_version": "1.0",
        "provenance_record": item["path"],
        "review_type": item["review_type"],
        "input_revision": item["input_revision"],
        "engine": {
            "name": "test-engine",
            "version": "1.0",
            "model": "test-model",
        },
        "query": "Check the packet against the original source.",
        "source_access": [
            {
                "reference": SOURCE,
                "outcome": "accessed",
                "note": "Full text read.",
            }
        ],
        "assessment": assessment,
    }


class ProvenanceRunnerTests(unittest.TestCase):
    def test_repository_plan_covers_both_collections_exactly(self):
        records = load_all()[0]
        summary = plan_run(
            ["evidence", "composition"], records=records, existing_sidecars={}
        )
        self.assertEqual(summary["counts"], {"evidence": 840, "composition": 319})
        self.assertEqual(summary["total"], 1159)

    def test_integer_profile_keys_have_unambiguous_revisions(self):
        self.assertEqual(
            canonical_revision({1: "a", 2: "b"}),
            canonical_revision({"1": "a", "2": "b"}),
        )
        with self.assertRaisesRegex(TypeError, "collide"):
            canonical_revision({1: "a", "1": "b"})

    def test_prepare_is_deterministic_and_groups_both_review_types(self):
        records, sources, places = fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = prepare_run(
                out,
                ["evidence", "composition"],
                records=records,
                sources=sources,
                places=places,
                existing_sidecars={},
                repository_revision="a" * 40,
            )
            first_bytes = (out / "manifest.yml").read_bytes()
            second = prepare_run(
                out,
                ["evidence", "composition"],
                records=records,
                sources=sources,
                places=places,
                existing_sidecars={},
                repository_revision="a" * 40,
            )
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, (out / "manifest.yml").read_bytes())
            self.assertEqual(first["counts"], {"evidence": 2, "composition": 1})
            self.assertIn(EVIDENCE, first["source_groups"][SOURCE])
            self.assertIn(COMPOSITE, first["composition_groups"]["surfaces"])

    def test_collect_materialises_valid_candidates_and_resumes(self):
        records, sources, places = fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest = prepare_run(
                out,
                ["evidence", "composition"],
                reassess_existing=True,
                records=records,
                sources=sources,
                places=places,
                existing_sidecars={},
                repository_revision="b" * 40,
            )
            entries = {item["path"]: item for item in manifest["entries"]}
            for path, assessment in (
                (EVIDENCE, evidence_assessment()),
                (COMPOSITE, composition_assessment()),
            ):
                target = out / entries[path]["response"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(_yaml(response(entries[path], assessment)))

            refreshed, errors = collect_run(
                out,
                records=records,
                sources=sources,
                places=places,
                existing_sidecars={},
            )
            self.assertEqual(errors, [])
            by_path = {item["path"]: item for item in refreshed["entries"]}
            self.assertEqual(by_path[EVIDENCE]["status"], "candidate_review")
            self.assertEqual(by_path[COMPOSITE]["status"], "candidate_review")
            evidence_file = out / by_path[EVIDENCE]["candidate"]
            composition_file = out / by_path[COMPOSITE]["candidate"]
            evidence_candidate = yaml.safe_load(evidence_file.read_text())
            composition_candidate = yaml.safe_load(composition_file.read_text())
            self.assertEqual(evidence_candidate["verification"], {"attestations": []})
            self.assertEqual(composition_candidate["review_type"], "composition")
            self.assertEqual(
                set(composition_candidate["dependency_revisions"]["records"]),
                {COMPONENT},
            )

            candidate_bytes = evidence_file.read_bytes()
            _, errors = collect_run(
                out,
                records=records,
                sources=sources,
                places=places,
                existing_sidecars={},
            )
            self.assertEqual(errors, [])
            self.assertEqual(candidate_bytes, evidence_file.read_bytes())

    def test_invalid_or_blocked_responses_never_silently_verify(self):
        records, sources, places = fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest = prepare_run(
                out,
                ["evidence"],
                reassess_existing=True,
                records={EVIDENCE: records[EVIDENCE]},
                sources=sources,
                places=places,
                existing_sidecars={},
                repository_revision="c" * 40,
            )
            item = manifest["entries"][0]
            invalid = evidence_assessment()
            invalid["status"] = "verified"
            target = out / item["response"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_yaml(response(item, invalid)))
            _, errors = collect_run(
                out,
                records={EVIDENCE: records[EVIDENCE]},
                sources=sources,
                places=places,
                existing_sidecars={},
            )
            self.assertTrue(any("cannot assert verified" in error for error in errors))
            self.assertFalse((out / item["candidate"]).exists())

    def test_actionable_finding_creates_draft_but_never_posts_or_signs(self):
        records, sources, places = fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest = prepare_run(
                out,
                ["evidence"],
                reassess_existing=True,
                records={EVIDENCE: records[EVIDENCE]},
                sources=sources,
                places=places,
                existing_sidecars={},
                repository_revision="d" * 40,
            )
            item = manifest["entries"][0]
            blocked = evidence_assessment()
            blocked["findings"]["source"] = {
                "conclusion": "correction_required",
                "evidence_ids": ["parameter-publication"],
                "note": "The publication does not match the stored source claim.",
            }
            target = out / item["response"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_yaml(response(item, blocked)))
            refreshed, errors = collect_run(
                out,
                records={EVIDENCE: records[EVIDENCE]},
                sources=sources,
                places=places,
                existing_sidecars={},
            )
            self.assertEqual(errors, [])
            refreshed_item = refreshed["entries"][0]
            self.assertEqual(refreshed_item["status"], "candidate_problem")
            candidate = yaml.safe_load((out / refreshed_item["candidate"]).read_text())
            self.assertEqual(candidate["verification"], {"attestations": []})
            issue_index = yaml.safe_load(
                (out / "queues" / "issue-drafts.yml").read_text()
            )
            self.assertEqual(issue_index["count"], 1)
            draft = (out / issue_index["issues"][0]["body_file"]).read_text()
            self.assertIn("### What kind of problem", draft)
            self.assertIn("The citation is wrong or missing", draft)


def _yaml(value):
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=100)


if __name__ == "__main__":
    unittest.main()

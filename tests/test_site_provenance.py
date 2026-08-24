from copy import deepcopy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_site import (  # noqa: E402
    build_graph,
    build_index_page,
    build_provenance_index,
    build_search_index,
    load_site_policy,
    load_site_provenance,
    provenance_state,
    record_page,
    review_guide_page,
    signoff_issue_url,
    source_page,
)
from check_db import load_all  # noqa: E402
from verifier_policy import load_verifier_policy  # noqa: E402


MIXED = (
    "records/ohm/generic--unreferenced--all-vegetation--"
    "mixed-forest-20-mccaughey-1985"
)
KYOTO = "records/ohm/tokyo--unreferenced--buildings"
COMPOSITE = "archetypes/surfaces/evetr/tropics--broad-leaf"


class SiteProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.sources, cls.places = load_all()
        cls.sidecars = load_site_provenance()
        cls.policy = load_site_policy()
        cls.used_by, cls.cluster = build_graph(cls.records)

    def render(self, key, sidecars=None):
        return record_page(
            key,
            self.records[key],
            self.records,
            self.sources,
            self.used_by,
            self.cluster,
            sidecars=sidecars or self.sidecars,
            policy=self.policy,
        )

    def test_site_policy_revision_matches_authenticated_checker(self):
        self.assertEqual(
            self.policy["revision"], load_verifier_policy()["revision"]
        )

    def test_awaiting_and_curation_pages_render_lineage(self):
        mixed = self.render(MIXED)
        self.assertIn("Awaiting sign-off", mixed)
        self.assertIn("Sign off evidence on GitHub", mixed)
        self.assertNotIn("<span class=\"pk\">Evidence</span>", mixed)
        self.assertNotIn("<span class=\"pk\">Method</span>", mixed)
        self.assertNotIn("<code class=\"revision\"", mixed)
        self.assertIn("Parameter source", mixed)
        self.assertIn("Input observations", mixed)
        self.assertIn("template=provenance-signoff.yml", mixed)

        kyoto = self.render(KYOTO)
        self.assertIn("Curation required", kyoto)
        self.assertIn("Possible duplicate", kyoto)
        self.assertNotIn("class=\"signoff\"", kyoto)

    def test_remaining_review_states_are_explicit(self):
        self.assertEqual(provenance_state(None, self.policy), "unaudited")
        candidate = deepcopy(self.sidecars[MIXED])
        candidate["assessment"]["status"] = "unresolved"
        self.assertEqual(provenance_state(candidate, self.policy), "unresolved")

        for method in ("measured", "calculated"):
            with self.subTest(method=method):
                candidate = deepcopy(self.sidecars[MIXED])
                candidate["assessment"]["method"] = method
                if method == "calculated":
                    candidate["assessment"]["findings"]["source"] = {
                        "conclusion": "not_applicable"
                    }
                self.assertEqual(
                    provenance_state(candidate, self.policy), "awaiting_signoff"
                )

        candidate = deepcopy(self.sidecars[MIXED])
        candidate["assessment"]["findings"]["urban_setting"] = {
            "conclusion": "not_applicable"
        }
        self.assertEqual(
            provenance_state(candidate, self.policy), "agent_assessed"
        )
        candidate["assessment"]["findings"]["urban_setting"] = {
            "conclusion": "supported"
        }
        self.assertEqual(
            provenance_state(candidate, self.policy), "awaiting_signoff"
        )

    def test_clean_composition_without_own_values_awaits_signoff(self):
        sidecar = next(
            candidate
            for candidate in self.sidecars.values()
            if candidate.get("review_type") == "composition"
            and candidate.get("assessment", {}).get("status") == "agent_assessed"
            and all(
                finding.get("conclusion") in {"supported", "not_applicable"}
                for finding in candidate["assessment"]["findings"].values()
            )
        )
        self.assertEqual(
            sidecar["assessment"]["findings"]["values"]["conclusion"],
            "not_applicable",
        )
        self.assertEqual(
            sidecar["assessment"]["findings"]["method"]["conclusion"],
            "supported",
        )
        self.assertEqual(
            provenance_state(sidecar, self.policy), "awaiting_signoff"
        )

        evidence = deepcopy(self.sidecars[MIXED])
        evidence["assessment"]["findings"]["values"] = {
            "conclusion": "not_applicable"
        }
        self.assertEqual(
            provenance_state(evidence, self.policy), "agent_assessed"
        )

    def test_unaudited_entry_links_to_gated_review_procedure(self):
        key = next(path for path in self.records if path not in self.sidecars)
        page = self.render(key)
        self.assertIn("Review procedure", page)
        self.assertIn("review.html?entry=", page)
        self.assertIn("Only a GitHub account in the verifier registry", page)

        guide = review_guide_page(self.policy)
        self.assertIn("Registered verifier required", guide)
        self.assertIn("rejected by CI", guide)
        self.assertIn("not incorporated into", guide)
        self.assertIn("template=verifier-request.yml", guide)
        for verifier in self.policy["verifiers"].values():
            self.assertIn(f"@{verifier['github_handle']}", guide)

    def test_verified_and_stale_decisions_show_linked_handle(self):
        sidecar = deepcopy(self.sidecars[MIXED])
        evidence = sidecar["assessment"]["evidence_revision"]
        decision = {
            "verifier": "sunt05",
            "verifier_id": 1802656,
            "decision": "verified",
            "signed_at": "2026-08-21T17:00:00Z",
            "event": {
                "kind": "issue",
                "id": 301,
                "url": "https://github.com/UMEP-dev/SUEWS-database/issues/301",
            },
            "evidence_revision": evidence,
            "verifier_policy_revision": self.policy["revision"],
            "scope": "record",
        }
        sidecar["verification"]["attestations"] = [decision]
        candidate = dict(self.sidecars)
        candidate[MIXED] = sidecar
        self.assertEqual(provenance_state(sidecar, self.policy), "verified")
        page = self.render(MIXED, candidate)
        self.assertIn("Verified", page)
        self.assertIn("href=\"https://github.com/sunt05\">@sunt05", page)
        self.assertNotIn("class=\"signoff\"", page)
        self.assertIn("class=\"reviewinfo\"", page)
        self.assertNotIn("class=\"reviewguide\"", page)

        decision["evidence_revision"] = "sha256:" + "9" * 64
        self.assertEqual(
            provenance_state(sidecar, self.policy), "awaiting_signoff"
        )
        stale_page = self.render(MIXED, candidate)
        self.assertIn("stale", stale_page)
        self.assertIn("@sunt05", stale_page)

    def test_generated_index_keeps_audit_and_signoff_issue_numbers(self):
        sidecar = deepcopy(self.sidecars[MIXED])
        evidence = sidecar["assessment"]["evidence_revision"]
        sidecar["verification"]["attestations"] = [
            {
                "verifier": "sunt05",
                "verifier_id": 1802656,
                "decision": "verified",
                "signed_at": "2026-08-21T17:00:00Z",
                "event": {
                    "kind": "issue",
                    "id": 301,
                    "url": (
                        "https://github.com/UMEP-dev/"
                        "SUEWS-database/issues/301"
                    ),
                },
                "evidence_revision": evidence,
                "verifier_policy_revision": self.policy["revision"],
                "scope": "record",
            }
        ]
        index = build_provenance_index({MIXED: sidecar}, self.policy)[MIXED]
        self.assertEqual(
            [item["issue"] for item in index["audit_issues"]], [11, 13]
        )
        self.assertEqual(index["signoff_issues"][0]["issue"], 301)
        self.assertEqual(index["signoff_issues"][0]["verifier"], "sunt05")

    def test_facets_and_source_roles_use_sidecars(self):
        index = build_search_index(
            self.records, self.sources, self.places, self.sidecars, self.policy
        )
        mixed = next(item for item in index if item["path"] == MIXED)
        self.assertEqual(mixed["verification"], "awaiting_signoff")
        self.assertEqual(mixed["method"], "fitted")
        self.assertIn("parameter_source", mixed["role"])

        source = source_page(
            "yoshida1991",
            self.sources["yoshida1991"],
            [],
            self.records,
            {"input_data": {KYOTO}},
        )
        self.assertIn("Input observations", source)
        self.assertIn("Canyon (E-W), Japan [10]", source)

    def test_non_doi_source_url_renders_directly(self):
        source = source_page(
            "cibse2015",
            self.sources["cibse2015"],
            [],
            self.records,
        )
        expected = (
            "https://www.cibse.org/knowledge-research/knowledge-portal/"
            "guide-a-environmental-design-2015"
        )
        self.assertIn(f'href="{expected}">source page</a>', source)
        self.assertNotIn("https://doi.org/https://", source)

    def test_urban_setting_renders_and_filters(self):
        records = deepcopy(self.records)
        records[MIXED]["urban_setting"] = "city_centre"
        page = record_page(
            MIXED,
            records[MIXED],
            records,
            self.sources,
            self.used_by,
            self.cluster,
            sidecars={},
            policy=self.policy,
        )
        self.assertIn("Urban setting", page)
        self.assertIn("City centre", page)
        self.assertIn("#setting=city_centre", page)

        index = build_search_index(
            records, self.sources, self.places, self.sidecars, self.policy
        )
        mixed = next(item for item in index if item["path"] == MIXED)
        self.assertEqual(mixed["setting"], "city_centre")
        self.assertIn("city_centre", mixed["text"])

        browser = build_index_page(records, self.sources, self.places, {})
        self.assertIn('id="facet-setting"', browser)
        self.assertIn("Urban setting", browser)
        self.assertIn("Central business district", browser)

    def test_applicable_scale_renders_and_filters(self):
        records = deepcopy(self.records)
        records[MIXED]["applicable_scale"] = "facet"
        page = record_page(
            MIXED,
            records[MIXED],
            records,
            self.sources,
            self.used_by,
            self.cluster,
            sidecars={},
            policy=self.policy,
        )
        self.assertIn("Applicable scale", page)
        self.assertIn("Facet or component", page)
        self.assertIn("#scale=facet", page)

        index = build_search_index(
            records, self.sources, self.places, self.sidecars, self.policy
        )
        mixed = next(item for item in index if item["path"] == MIXED)
        self.assertEqual(mixed["scale"], "facet")
        self.assertIn("facet", mixed["text"])

        browser = build_index_page(records, self.sources, self.places, {})
        self.assertIn('id="facet-scale"', browser)
        self.assertIn("Applicable scale", browser)
        self.assertIn("Material or specimen", browser)

    def test_signoff_issue_url_binds_current_revisions(self):
        url = signoff_issue_url(MIXED, self.sidecars[MIXED], self.policy)
        self.assertIn("record=records%2Fohm%2Fgeneric--", url)
        self.assertIn("evidence_revision=sha256%3A", url)
        self.assertIn("policy_revision=sha256%3A", url)
        self.assertIn("review_type=Evidence", url)

    def test_composite_has_distinct_composition_review_layer(self):
        page = self.render(COMPOSITE)
        self.assertIn("<span class=\"chip\">composite</span>", page)
        self.assertIn("<h3>Composition</h3>", page)
        self.assertIn("a separate composition review assesses why", page)
        self.assertIn("Awaiting sign-off", page)
        self.assertIn("Composition review", page)
        self.assertIn("Composite metadata", page)

        sidecar = deepcopy(self.sidecars[MIXED])
        sidecar["provenance_record"] = COMPOSITE
        sidecar["review_type"] = "composition"
        sidecar["assessment"]["method"] = "assembled"
        candidate = dict(self.sidecars)
        candidate[COMPOSITE] = sidecar
        reviewed = self.render(COMPOSITE, candidate)
        self.assertIn("Composition provenance", reviewed)
        self.assertIn("Own parameter values", reviewed)
        self.assertIn("Slot and season mapping", reviewed)
        self.assertIn("Sign off composition on GitHub", reviewed)
        self.assertIn("review_type=Composition", reviewed)


if __name__ == "__main__":
    unittest.main()

"""Records whose legacy citation was checked and found not to hold the value.

Such a record keeps ``source: unreferenced`` and records the citation the
legacy database gave as ``source_legacy_ref``, the ``legacy_id`` of an entry in
``db/sources.yml``. The site keeps the "unreferenced" chip and adds a note
beside it that links to the record page's explanation.
"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_site import (  # noqa: E402
    build_graph,
    build_index_page,
    build_search_index,
    checked_legacy_citation,
    load_site_policy,
    load_site_provenance,
    record_page,
)
from check_db import load_all  # noqa: E402


CHECKED = "records/surfaces/water/helsinki--jarvi2014--water-state"
DANGLING = "records/materials/generic--unreferenced--gypsum"
NEVER_CITED = "records/surfaces/water/london--unreferenced--water-state"
CITED = "records/surfaces/bldgs/london--ward2016--albedo"
NOTE = "legacy citation checked: value not in the cited paper"


class LegacyCitationNoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.sources, cls.places = load_all()
        cls.sidecars = load_site_provenance()
        cls.policy = load_site_policy()
        cls.used_by, cls.cluster = build_graph(cls.records)
        cls.index = {
            entry["path"]: entry
            for entry in build_search_index(
                cls.records, cls.sources, cls.places, cls.sidecars, cls.policy
            )
        }

    def render(self, key, rec=None):
        return record_page(
            key, rec or self.records[key], self.records, self.sources,
            self.used_by, self.cluster, sidecars=self.sidecars,
            policy=self.policy,
        )

    def test_rule_resolves_only_registered_legacy_references(self):
        self.assertEqual(
            checked_legacy_citation(self.records[CHECKED], self.sources),
            "jarvi2014",
        )
        # a dangling reference never resolved to a paper
        self.assertIn("source_legacy_ref", self.records[DANGLING])
        self.assertIsNone(
            checked_legacy_citation(self.records[DANGLING], self.sources)
        )
        self.assertIsNone(
            checked_legacy_citation(self.records[NEVER_CITED], self.sources)
        )
        # a record that carries a citation is never in the set
        cited = deepcopy(self.records[CHECKED])
        cited["source"] = "jarvi2014"
        self.assertIsNone(checked_legacy_citation(cited, self.sources))

    def test_every_checked_record_has_a_supported_source_finding(self):
        for path, rec in self.records.items():
            if not checked_legacy_citation(rec, self.sources):
                continue
            with self.subTest(path=path):
                finding = (
                    self.sidecars[path]["assessment"]["findings"]["source"]
                )
                self.assertEqual(finding["conclusion"], "supported")

    def test_record_page_keeps_chip_and_explains_the_check(self):
        page = self.render(CHECKED)
        self.assertIn('<span class="badge-unref">unreferenced</span>', page)
        self.assertIn('href="#legacy-citation"', page)
        self.assertIn(NOTE, page)
        self.assertIn('id="legacy-citation"', page)
        self.assertIn("legacy reference 90240015", page)
        self.assertIn("source/jarvi2014.html\">Järvi et al. (2014)</a>", page)
        self.assertNotIn("no citation was recorded in the legacy database", page)

    def test_record_without_checked_citation_is_unchanged(self):
        for key in (DANGLING, NEVER_CITED):
            with self.subTest(key=key):
                page = self.render(key)
                self.assertIn('<span class="badge-unref">unreferenced</span>', page)
                self.assertIn(
                    "no citation was recorded in the legacy database", page
                )
                self.assertNotIn('id="legacy-citation"', page)
                self.assertNotIn(NOTE, page)
        self.assertNotIn('id="legacy-citation"', self.render(CITED))

    def test_listing_carries_the_note(self):
        entry = self.index[CHECKED]
        self.assertEqual(entry["source"], "unreferenced")
        self.assertIn("unreferenced", entry["sources"])
        self.assertEqual(entry["legacy_citation"], "jarvi2014")
        self.assertEqual(entry["legacy_citation_label"], "Järvi 2014")
        for key in (DANGLING, NEVER_CITED, CITED):
            self.assertIsNone(self.index[key]["legacy_citation"])
        browser = build_index_page(self.records, self.sources, self.places, {})
        self.assertIn("#legacy-citation", browser)
        self.assertIn(NOTE, browser)
        n_legacy = sum(
            1 for path, rec in self.records.items()
            if path.startswith("records/")
            and checked_legacy_citation(rec, self.sources)
        )
        self.assertIn(
            f"for {n_legacy} of them the legacy citation was checked", browser
        )


if __name__ == "__main__":
    unittest.main()

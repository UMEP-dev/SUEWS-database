"""Records whose legacy citation was checked and found not to hold the value.

Such a record keeps ``source: unreferenced`` and records the citation the
legacy database gave as ``source_legacy_ref``, the ``legacy_id`` of an entry in
``db/sources.yml``. The site replaces the "unreferenced" chip with a distinct
"legacy citation not supported" label and offers it as its own source filter;
records that never carried a citation keep "unreferenced".
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
    LEGACY_UNSUPPORTED,
    LEGACY_UNSUPPORTED_LABEL,
    load_site_policy,
    load_site_provenance,
    record_page,
)
from check_db import load_all  # noqa: E402


CHECKED = "records/surfaces/water/helsinki--jarvi2014--water-state"
DANGLING = "records/materials/generic--unreferenced--gypsum"
NEVER_CITED = "records/surfaces/water/london--unreferenced--water-state"
CITED = "records/surfaces/bldgs/london--ward2016--albedo"
LABEL = "legacy citation not supported"


class LegacyCitationLabelTests(unittest.TestCase):
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

    def test_label_is_not_a_source_key(self):
        self.assertEqual(LEGACY_UNSUPPORTED_LABEL, LABEL)
        self.assertNotIn(LEGACY_UNSUPPORTED, self.sources)

    def test_record_page_replaces_chip_and_explains_the_check(self):
        page = self.render(CHECKED)
        self.assertNotIn('<span class="badge-unref">unreferenced</span>', page)
        self.assertIn('class="badge-legacy" href="#legacy-citation"', page)
        self.assertIn(f">{LABEL}</a>", page)
        self.assertIn('title="The legacy database cited a publication', page)
        self.assertIn('id="legacy-citation"', page)
        self.assertIn("legacy reference 90240015", page)
        self.assertIn("source/jarvi2014.html\">Järvi et al. (2014)</a>", page)
        self.assertIn("<code>unreferenced</code>", page)
        self.assertNotIn("source/unreferenced.html", page)
        self.assertNotIn("no citation was recorded in the legacy database", page)

    def test_record_without_checked_citation_keeps_unreferenced(self):
        for key in (DANGLING, NEVER_CITED):
            with self.subTest(key=key):
                page = self.render(key)
                self.assertIn('<span class="badge-unref">unreferenced</span>', page)
                self.assertIn(
                    "no citation was recorded in the legacy database", page
                )
                self.assertNotIn('id="legacy-citation"', page)
                self.assertNotIn('class="badge-legacy"', page)
                self.assertIn("source/unreferenced.html", page)
        self.assertNotIn('id="legacy-citation"', self.render(CITED))

    def test_source_filter_separates_the_two_cases(self):
        entry = self.index[CHECKED]
        # the canonical source is unchanged; only the facet value differs
        self.assertEqual(entry["source"], "unreferenced")
        self.assertEqual(entry["sources"], [LEGACY_UNSUPPORTED])
        self.assertEqual(entry["legacy_citation"], "jarvi2014")
        self.assertEqual(entry["legacy_citation_label"], "Järvi 2014")
        for key in (DANGLING, NEVER_CITED):
            self.assertEqual(self.index[key]["sources"], ["unreferenced"])
            self.assertIsNone(self.index[key]["legacy_citation"])

    def test_arrival_counts_split_the_two_cases(self):
        browser = build_index_page(self.records, self.sources, self.places, {})
        evidence = {p: r for p, r in self.records.items()
                    if p.startswith("records/")}
        n_legacy = sum(1 for r in evidence.values()
                       if checked_legacy_citation(r, self.sources))
        n_unref = sum(1 for r in evidence.values()
                      if r.get("source") == "unreferenced") - n_legacy
        self.assertGreater(n_legacy, 0)
        self.assertIn(f'href="#source={LEGACY_UNSUPPORTED}"><b>{n_legacy}</b>',
                      browser)
        self.assertIn(f'href="#source=unreferenced"><b>{n_unref}</b>', browser)
        self.assertIn(f"const LEGACY_VALUE = '{LEGACY_UNSUPPORTED}';", browser)
        self.assertIn(f"const LEGACY_LABEL = '{LABEL}';", browser)


if __name__ == "__main__":
    unittest.main()

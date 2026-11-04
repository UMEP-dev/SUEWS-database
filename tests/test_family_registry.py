"""The family registry (schema/families.yml): its check and its rendering."""

from copy import deepcopy
from pathlib import Path
import json
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_site  # noqa: E402
from build_site import (  # noqa: E402
    build_graph,
    build_index_page,
    load_site_policy,
    load_site_provenance,
    record_page,
)
from check_db import (  # noqa: E402
    derivable_record_families,
    family_of,
    family_registry_check,
    load_all,
    load_families,
)


OHM = "records/ohm/beijing--zheng-y2023--buildings"
ALBEDO = "records/surfaces/bldgs/london--ward2016--albedo"
ANTHROPOGENIC = "records/anthropogenic/aberdeen--xie2024"


class FamilyRegistryCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.sources, cls.places = load_all()
        cls.registry = load_families()

    def check(self, registry):
        return family_registry_check(self.records, self.sources, registry)

    def test_repository_registry_passes(self):
        self.assertEqual(self.check(self.registry), [])

    def test_every_derivable_family_is_listed(self):
        families = set(self.registry["families"])
        record_fams = {family_of(p) for p in self.records
                       if p.startswith("records/")}
        self.assertLessEqual(record_fams, families)
        self.assertIn("surface", families)
        self.assertEqual(families, derivable_record_families(self.records))

    def test_branch_entries_are_tentative_without_scheme_reference(self):
        for fam, entry in self.registry["families"].items():
            with self.subTest(family=fam):
                self.assertEqual(entry["confidence"], "tentative")
                self.assertNotIn("scheme_reference", entry)

    def test_missing_family_fails(self):
        reg = deepcopy(self.registry)
        del reg["families"]["ohm"]
        self.assertTrue(any("'ohm' is derived by the site but not listed" in e
                            for e in self.check(reg)))

    def test_underivable_family_fails(self):
        reg = deepcopy(self.registry)
        reg["families"]["lai-power"] = deepcopy(reg["families"]["lai"])
        self.assertTrue(any("'lai-power' is not a family the site derives" in e
                            for e in self.check(reg)))

    def test_values_outside_vocabularies_fail(self):
        for field, bad in (("groups", ["geometry"]),
                           ("data_types", ["parameter"]),
                           ("confidence", "likely"),
                           ("groups", "materials")):
            reg = deepcopy(self.registry)
            reg["families"]["ohm"][field] = bad
            with self.subTest(field=field, value=bad):
                errors = self.check(reg)
                self.assertTrue(errors)
                self.assertTrue(all("ohm" in e for e in errors))

    def test_unknown_field_fails(self):
        reg = deepcopy(self.registry)
        reg["families"]["ohm"]["equation"] = "QS = a1 Q* + ..."
        self.assertTrue(any("unknown fields ['equation']" in e
                            for e in self.check(reg)))

    def test_scheme_reference_must_resolve(self):
        reg = deepcopy(self.registry)
        reg["families"]["ohm"]["scheme_reference"] = "no-such-key-1900"
        self.assertTrue(any("scheme_reference 'no-such-key-1900' not in" in e
                            for e in self.check(reg)))
        reg["families"]["ohm"]["scheme_reference"] = "unreferenced"
        self.assertTrue(any("cannot be 'unreferenced'" in e
                            for e in self.check(reg)))
        # a resolving key passes the check; the check proves resolution only
        some_key = next(k for k in self.sources if k != "unreferenced")
        reg["families"]["ohm"]["scheme_reference"] = some_key
        self.assertEqual(self.check(reg), [])


class FamilyRegistryRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.sources, cls.places = load_all()
        cls.sidecars = load_site_provenance()
        cls.policy = load_site_policy()
        cls.used_by, cls.cluster = build_graph(cls.records)

    def render(self, key):
        return record_page(
            key, self.records[key], self.records, self.sources,
            self.used_by, self.cluster,
            sidecars=self.sidecars, policy=self.policy,
        )

    def test_site_labels_and_notes_come_from_the_registry(self):
        registry = load_families()["families"]
        self.assertEqual(build_site.FAMILY_LABEL,
                         {f: e["label"] for f, e in registry.items()})
        self.assertEqual(set(build_site.FAMILY_NOTE),
                         {"max-conductance", "conductance"})
        self.assertTrue(build_site.FAMILY_NOTE["max-conductance"].startswith(
            "max_conductance is the maximum conductance of one vegetated "
            "surface (evergreen trees, deciduous trees or grass), set under "
            "land_cover."))

    def test_record_pages_carry_the_classification(self):
        cases = (
            (OHM, "Materials · Model coefficient",
             "Open question: Materials or thermal typologies?"),
            (ALBEDO, "Materials / Morphology · Physical parameter",
             "Open question: Bulk (neighbourhood) values belong under "
             "morphology; split by applicable_scale?"),
            (ANTHROPOGENIC, "Activities · Model coefficient",
             "Open question: Records hold the QF coefficients qf_a, qf_b, "
             "qf_c; physical or coefficient?"),
        )
        for path, label, question in cases:
            with self.subTest(path=path):
                page = self.render(path)
                self.assertIn(f">{label}</span>", page)
                self.assertRegex(page, r'class="famclass">.*?>tentative</span>')
                self.assertIn(question, page)

    def test_unflagged_family_has_no_open_question(self):
        path = next(p for p in self.records
                    if p.startswith("records/materials/"))
        page = self.render(path)
        self.assertIn(">Materials · Physical parameter</span>", page)
        self.assertNotIn("Open question:", page)

    def test_composites_carry_no_family_classification(self):
        path = next(p for p in self.records
                    if p.startswith("archetypes/surfaces/"))
        self.assertNotIn('class="famclass"', self.render(path))

    def test_browse_panel_groups_families_and_keeps_counts(self):
        page = build_index_page(self.records, self.sources, self.places, {})
        counts = {}
        for p in self.records:
            if p.startswith("records/"):
                counts[family_of(p)] = counts.get(family_of(p), 0) + 1
        # group headings, in vocabulary order, then data-type subheadings
        heads = re.findall(r'<p class="famgroup">([^<]+)</p>', page)
        self.assertEqual(heads, ["Materials", "Activities",
                                 "Thermal typologies", "Morphology"])
        self.assertIn('<p class="famtype">Physical parameter</p>', page)
        # every family keeps its filter link and its count; albedo and
        # emissivity sit under two groups, snow under two data types
        for fam, n in counts.items():
            row = (f'<a class="grow" href="#family={fam}"><span>{fam}</span>'
                   f'<span class="n">{n}</span></a>')
            expected = 2 if fam in ("albedo", "emissivity", "snow") else 1
            with self.subTest(family=fam):
                self.assertEqual(page.count(row), expected)
        self.assertIn(f"All {len(counts)} parameter\nfamilies", page)
        tree = json.loads(re.search(r"const FAMILY_TREE = (\[.*?\]);",
                                    page).group(1))
        morph = dict(tree)["morphology"]
        self.assertEqual(morph, [["physical_parameter",
                                  ["albedo", "emissivity"]]])


if __name__ == "__main__":
    unittest.main()

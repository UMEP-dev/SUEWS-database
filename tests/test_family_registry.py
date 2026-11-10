"""The family registry (schema/families.yml): its check and its use on the site."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_site  # noqa: E402
import check_db  # noqa: E402
from build_site import (  # noqa: E402
    build_graph,
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


MAX_CONDUCTANCE = "records/surfaces/evetr/beijing--zheng-y2023--max-conductance"
ENERGY_USE = "records/profiles/energy-use/helsinki--jarvi2019--energy-use"


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

    def test_missing_family_fails(self):
        reg = deepcopy(self.registry)
        del reg["families"]["ohm"]
        self.assertTrue(any("'ohm' is derived by the site but not listed" in e
                            for e in self.check(reg)))

    def test_underivable_family_fails(self):
        # "--lai" matches "--lai-power" first, so the site never derives it
        reg = deepcopy(self.registry)
        reg["families"]["lai-power"] = {"label": "LAI growth coefficients"}
        self.assertTrue(any("'lai-power' is not a family the site derives" in e
                            for e in self.check(reg)))

    def test_label_is_required(self):
        for bad in (None, "", "  "):
            reg = deepcopy(self.registry)
            if bad is None:
                del reg["families"]["ohm"]["label"]
            else:
                reg["families"]["ohm"]["label"] = bad
            with self.subTest(label=bad):
                errors = self.check(reg)
                self.assertEqual(errors,
                                 ["families.yml: ohm needs a non-empty label"])

    def test_note_must_be_text(self):
        reg = deepcopy(self.registry)
        reg["families"]["ohm"]["note"] = ""
        self.assertEqual(self.check(reg),
                         ["families.yml: ohm note must be non-empty text"])

    def test_unknown_field_fails(self):
        # the registry names families; it does not classify them
        for field, value in (("groups", ["materials"]),
                             ("data_types", ["model_coefficient"])):
            reg = deepcopy(self.registry)
            reg["families"]["ohm"][field] = value
            with self.subTest(field=field):
                self.assertTrue(any(f"unknown fields ['{field}']" in e
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


class FamilyRegistrySiteTests(unittest.TestCase):
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

    def test_site_and_checker_share_family_of(self):
        self.assertIs(build_site.family_of, check_db.family_of)

    def test_site_labels_and_notes_come_from_the_registry(self):
        registry = load_families()["families"]
        self.assertEqual(build_site.FAMILY_LABEL,
                         {f: e["label"] for f, e in registry.items()})
        self.assertEqual(list(build_site.FAMILY_NOTE),
                         ["max-conductance", "conductance"])
        self.assertTrue(build_site.FAMILY_NOTE["max-conductance"].startswith(
            "max_conductance is the maximum conductance of one vegetated "
            "surface (evergreen trees, deciduous trees or grass), set under "
            "land_cover."))

    def test_record_page_shows_the_registry_note(self):
        page = self.render(MAX_CONDUCTANCE)
        self.assertIn("It is not g_max from the conductance family", page)

    def test_profile_page_takes_its_label_from_the_registry(self):
        self.assertEqual(family_of(ENERGY_USE), "profile: energy-use")
        self.assertIn("Energy use profile", self.render(ENERGY_USE))


if __name__ == "__main__":
    unittest.main()

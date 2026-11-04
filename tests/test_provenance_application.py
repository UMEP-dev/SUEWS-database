"""The `application` evidence role: a publication that used a stored value.

The fixture sidecar is the Kyoto OHM fixture with one application item from
a fictitious publication. It is test data, not a database record.
"""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from build_site import (  # noqa: E402
    build_graph,
    load_site_policy,
    provenance_source_roles,
    record_page,
)
from provenance import (  # noqa: E402
    check_provenance,
    evidence_revision,
    load_provenance_sidecars,
    signoff_eligible,
)
from test_provenance import PLACE, RECORD, RECORD_KEY, SOURCE  # noqa: E402


FIXTURES = ROOT / "tests/fixtures/provenance/application"
APPLICATION_SOURCE = {
    "author": "C. Example",
    "title": "Example model application",
    "type": "journalArticle",
    "year": 2005,
}


def registries():
    return (
        {RECORD_KEY: deepcopy(RECORD)},
        {"paper1999": deepcopy(SOURCE), "usage2005": deepcopy(APPLICATION_SOURCE)},
        {"kyoto": deepcopy(PLACE)},
    )


def fixture():
    sidecars, errors = load_provenance_sidecars(FIXTURES)
    assert errors == [], errors
    return sidecars[RECORD_KEY]


def restamp(sidecar):
    sidecar["assessment"]["evidence_revision"] = evidence_revision(sidecar)
    return sidecar


def evidence(sidecar, evidence_id):
    return next(item for item in sidecar["assessment"]["evidence"]
                if item["id"] == evidence_id)


class ApplicationRoleCheckTests(unittest.TestCase):
    def check(self, sidecar):
        records, sources, places = registries()
        return check_provenance(records, sources, places, {RECORD_KEY: sidecar})

    def test_fixture_with_application_evidence_is_valid(self):
        sidecar = fixture()
        self.assertEqual(
            evidence(sidecar, "model-application")["role"], "application"
        )
        self.assertEqual(self.check(sidecar), [])
        # alongside a real parameter_source it does not block sign-off
        self.assertTrue(signoff_eligible(sidecar, deepcopy(RECORD)))

    def test_application_never_substitutes_for_a_parameter_source(self):
        sidecar = fixture()
        evidence(sidecar, "parameter-publication")["role"] = "application"
        sidecar["assessment"]["findings"]["source"]["conclusion"] = (
            "correction_required"
        )
        restamp(sidecar)
        errors = self.check(sidecar)
        # a fitted assessment must still name a parameter_source
        self.assertTrue(errors)
        self.assertFalse(signoff_eligible(sidecar, deepcopy(RECORD)))

    def test_application_cannot_support_a_source_finding(self):
        sidecar = fixture()
        sidecar["assessment"]["findings"]["source"]["evidence_ids"] = [
            "parameter-publication", "model-application"
        ]
        restamp(sidecar)
        errors = self.check(sidecar)
        self.assertTrue(any(
            "supported source finding cites application evidence "
            "'model-application'" in error for error in errors
        ), errors)

    def test_application_may_inform_a_non_supporting_source_finding(self):
        sidecar = fixture()
        finding = sidecar["assessment"]["findings"]["source"]
        finding["conclusion"] = "unresolved"
        finding["evidence_ids"] = ["model-application"]
        restamp(sidecar)
        self.assertFalse(any("application" in e for e in self.check(sidecar)))

    def test_application_is_a_source_role_not_a_record_role(self):
        sidecar = fixture()
        sidecar["assessment"]["evidence"].append({
            "id": "used-by-record",
            "record": RECORD_KEY,
            "role": "application",
        })
        restamp(sidecar)
        self.assertTrue(self.check(sidecar))

    def test_no_real_sidecar_carries_application_evidence(self):
        sidecars, _ = load_provenance_sidecars()
        for path, sidecar in sidecars.items():
            roles = {item.get("role") for item in
                     (sidecar.get("assessment") or {}).get("evidence", [])}
            with self.subTest(path=path):
                self.assertNotIn("application", roles)


class ApplicationRoleRenderingTests(unittest.TestCase):
    def test_record_page_lists_application_under_used_in(self):
        records, sources, _ = registries()
        sidecar = fixture()
        used_by, cluster = build_graph(records)
        page = record_page(
            RECORD_KEY, records[RECORD_KEY], records, sources, used_by,
            cluster, sidecars={RECORD_KEY: sidecar}, policy=load_site_policy(),
        )
        self.assertIn("<h3>Used in</h3>", page)
        used_in = page.index("<h3>Used in</h3>")
        parameter = page.index("Example parameter publication")
        application = page.index("Example model application")
        # the parameter source stays in the main evidence list; the
        # application sits under its own heading, after it
        self.assertLess(parameter, used_in)
        self.assertLess(used_in, application)
        self.assertIn("they are not evidence of its source", page)
        self.assertIn('<span class="erole">Used in</span>', page[used_in:])
        self.assertNotIn('<span class="erole">Used in</span>', page[:used_in])

    def test_page_without_application_evidence_has_no_used_in(self):
        records, sources, _ = registries()
        sidecar = fixture()
        sidecar["assessment"]["evidence"] = [
            item for item in sidecar["assessment"]["evidence"]
            if item["role"] != "application"
        ]
        used_by, cluster = build_graph(records)
        page = record_page(
            RECORD_KEY, records[RECORD_KEY], records, sources, used_by,
            cluster, sidecars={RECORD_KEY: sidecar}, policy=load_site_policy(),
        )
        self.assertNotIn("<h3>Used in</h3>", page)

    def test_source_page_role_index_carries_application(self):
        roles = provenance_source_roles({RECORD_KEY: fixture()})
        self.assertEqual(roles["usage2005"], {"application": {RECORD_KEY}})


if __name__ == "__main__":
    unittest.main()

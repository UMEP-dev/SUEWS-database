"""The rule the typology photographs exist under: no image without its credit.

The images are published under licences that require attribution wherever the
image appears. The site renders a photograph only if db/images.yml lists it,
so these tests hold the manifest to the same standard the licences do, and
hold the checker to catching a manifest that slips below it.
"""

from pathlib import Path
import copy
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_db import image_check, load_all  # noqa: E402


def manifest():
    return yaml.safe_load((ROOT / "db" / "images.yml").read_text())


class ImageManifestTests(unittest.TestCase):
    def setUp(self):
        self.records = load_all()[0]
        self.doc = manifest()

    def test_manifest_as_committed_passes(self):
        self.assertEqual(image_check(self.records), [])

    def test_every_published_image_carries_credit_and_licence(self):
        for path, entry in self.doc["images"].items():
            with self.subTest(path=path):
                self.assertTrue(entry["credit"].strip())
                self.assertTrue(entry["licence"].strip())
                self.assertTrue(entry["licence_url"].startswith("http"))
                self.assertTrue(entry["description_page"].startswith("http"))

    def test_every_typology_with_a_url_is_accounted_for(self):
        """No photograph is dropped silently: shown, or explained."""
        shown = set(self.doc["images"])
        unresolved = set(self.doc["unresolved"])
        for path, rec in self.records.items():
            if path.startswith("archetypes/typologies/") and rec.get("url"):
                with self.subTest(path=path):
                    self.assertIn(path, shown | unresolved)

    def test_unresolved_entries_say_what_would_settle_them(self):
        for path, entry in self.doc["unresolved"].items():
            with self.subTest(path=path):
                self.assertTrue(entry["reason"].strip())
                self.assertTrue(entry["what_would_settle_it"].strip())


class PhotoInviteTests(unittest.TestCase):
    """Where a typology has no photograph, the page asks for one -- and says
    on what terms, so nobody goes to the trouble of finding a picture we
    cannot use."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import build_site

        self.build_site = build_site
        self.doc = manifest()

    def test_invite_states_the_licence_condition(self):
        html = self.build_site.photo_invite(
            "archetypes/typologies/china--cbd", {"name": "CBD"}, None, 2)
        self.assertIn("Offer a photograph", html)
        self.assertIn("licence", html)
        self.assertIn("typology-photo.yml", html)

    def test_invite_links_the_issue_tracking_a_blocked_image(self):
        blocked = self.doc["unresolved"]["archetypes/typologies/uk--sub-urban"]
        html = self.build_site.photo_invite(
            "archetypes/typologies/uk--sub-urban", {"name": "Sub-urban"},
            blocked, 2)
        self.assertIn(f"/issues/{blocked['tracked_by']}", html)

    def test_invite_without_a_tracking_issue_still_renders(self):
        html = self.build_site.photo_invite(
            "archetypes/typologies/china--cbd", {"name": "CBD"},
            {"reason": "x", "what_would_settle_it": "y"}, 2)
        self.assertIn("db/images.yml", html)

    def test_every_unresolved_entry_names_its_tracking_issue(self):
        for path, entry in self.doc["unresolved"].items():
            with self.subTest(path=path):
                self.assertIsInstance(entry.get("tracked_by"), int)


class ImageCheckerTests(unittest.TestCase):
    """The checker has to fail on the manifests that matter, not just pass."""

    def setUp(self):
        self.records = load_all()[0]
        self.doc = manifest()
        self.path = "archetypes/typologies/sweden--modernism"

    def write(self, doc, tmp):
        (tmp / "images.yml").write_text(yaml.safe_dump(doc))

    def run_check(self, doc):
        import check_db

        original = check_db.DB
        tmp = ROOT / "tests" / "_tmp_db"
        tmp.mkdir(exist_ok=True)
        try:
            self.write(doc, tmp)
            check_db.DB = tmp
            return check_db.image_check(self.records)
        finally:
            check_db.DB = original
            (tmp / "images.yml").unlink(missing_ok=True)
            tmp.rmdir()

    def test_an_image_without_a_credit_is_an_error(self):
        doc = copy.deepcopy(self.doc)
        doc["images"][self.path]["credit"] = ""
        errors = self.run_check(doc)
        self.assertTrue(any("credit" in e for e in errors), errors)

    def test_an_image_without_a_licence_is_an_error(self):
        doc = copy.deepcopy(self.doc)
        del doc["images"][self.path]["licence"]
        errors = self.run_check(doc)
        self.assertTrue(any("licence" in e for e in errors), errors)

    def test_a_typology_url_that_is_neither_shown_nor_explained_is_an_error(self):
        doc = copy.deepcopy(self.doc)
        del doc["images"][self.path]
        errors = self.run_check(doc)
        self.assertTrue(any("neither publishes it" in e for e in errors), errors)

    def test_two_entries_may_not_claim_the_same_asset(self):
        doc = copy.deepcopy(self.doc)
        other = "archetypes/typologies/sweden--sub-urban"
        doc["images"][other]["file"] = doc["images"][self.path]["file"]
        errors = self.run_check(doc)
        self.assertTrue(any("already used by" in e for e in errors), errors)

    def test_a_record_cannot_be_both_shown_and_unresolved(self):
        doc = copy.deepcopy(self.doc)
        doc["unresolved"][self.path] = {
            "reason": "x", "what_would_settle_it": "y"}
        errors = self.run_check(doc)
        self.assertTrue(any("both shown and unresolved" in e for e in errors),
                        errors)


if __name__ == "__main__":
    unittest.main()

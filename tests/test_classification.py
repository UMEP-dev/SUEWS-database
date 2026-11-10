"""The parameter-classification registries and their site display."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_site import (  # noqa: E402
    build_graph,
    load_site_policy,
    load_site_provenance,
    params_table,
    record_page,
)
from check_db import (  # noqa: E402
    CLASSIFICATION_VOCABULARY,
    RECORD_SCALE_VOCABULARY,
    classification_check,
    generalised_paths,
    load_all,
    load_classification,
    load_record_scale,
    load_registry_text,
    record_parameter_paths,
)


PAVED = "records/surfaces/paved/test--paper--albedo"
PROFILE = "records/profiles/traffic/test--paper--traffic"
CONSTRUCTION = "records/constructions/test--paper--wall"

RECORDS = {
    PAVED: {
        "record": PAVED,
        "target": "land_cover.paved",
        "parameters": {
            "alb": 0.12,
            "thermal_layers": {"dz": [0.1, 0.2]},
            "context": {"note_field": "x"},
        },
    },
    PROFILE: {
        "record": PROFILE,
        "target": "profile.traffic",
        "parameters": {"working_day": {h: 1.0 for h in range(1, 25)}},
    },
    CONSTRUCTION: {
        "record": CONSTRUCTION,
        "target": "construction",
        "parameters": {
            "wall": [{"material": "records/materials/brick", "thickness": 0.2},
                     {"material": "records/materials/plaster",
                      "thickness": 0.01}],
            "wall_insulation_layer": 6,
        },
    },
    # archetypes are not classified and must not demand registry entries
    "archetypes/surfaces/paved/test": {
        "archetype": "archetypes/surfaces/paved/test",
        "target": "land_cover.paved",
        "parameters": {"only_in_archetype": 1.0},
    },
}


def vocabulary(spec):
    return {field: {value: "description" for value in values}
            for field, values in spec.items()}


def entry(**overrides):
    base = {"admission": "held", "type": None, "status": "unclassified",
            "reason": None, "activity": None, "note": None}
    base.update(overrides)
    return base


def registry(**overrides):
    entries = {
        "land_cover.paved.alb": entry(),
        "land_cover.paved.thermal_layers.dz": entry(),
        "land_cover.paved.context.note_field":
            entry(admission="not_a_parameter"),
        "profile.traffic.working_day.*": entry(),
        "construction.wall.*.material": entry(admission="not_a_parameter"),
        "construction.wall.*.thickness": entry(),
        "construction.wall_insulation_layer":
            entry(admission="not_a_parameter"),
    }
    entries.update(overrides)
    return {"vocabulary": vocabulary(CLASSIFICATION_VOCABULARY),
            "parameters": {k: v for k, v in entries.items() if v is not None}}


def scale_registry(records=None):
    return {"vocabulary": vocabulary(RECORD_SCALE_VOCABULARY),
            "records": records or {}}


class GeneralisationTests(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(record_parameter_paths(RECORDS[PAVED]), {
            "land_cover.paved.alb",
            "land_cover.paved.thermal_layers.dz",
            "land_cover.paved.context.note_field",
        })

    def test_hour_keys_become_a_star(self):
        self.assertEqual(record_parameter_paths(RECORDS[PROFILE]),
                         {"profile.traffic.working_day.*"})

    def test_digit_string_keys_become_a_star(self):
        self.assertEqual(set(generalised_paths({"1": 2, "2": 3}, "t.x")),
                         {"t.x.*"})

    def test_list_of_mappings_is_summarised(self):
        self.assertEqual(record_parameter_paths(RECORDS[CONSTRUCTION]), {
            "construction.wall.*.material",
            "construction.wall.*.thickness",
            "construction.wall_insulation_layer",
        })

    def test_scalar_list_is_one_path(self):
        self.assertEqual(set(generalised_paths([1, 2, 3], "t.dz")), {"t.dz"})

    def test_null_leaf_counts_and_empty_containers_do_not(self):
        self.assertEqual(
            set(generalised_paths({"a": None, "b": {}, "c": []}, "t")),
            {"t.a"})
        self.assertEqual(record_parameter_paths(
            {"target": "snow", "parameters": {}}), set())

    def test_targets_with_dots_keep_context_under_parameters(self):
        paths = record_parameter_paths(RECORDS[PAVED])
        self.assertIn("land_cover.paved.context.note_field", paths)


class CheckTests(unittest.TestCase):
    def errors(self, classification=None, scales=None, records=RECORDS):
        errors, _ = classification_check(
            records, classification or registry(), scales or scale_registry())
        return errors

    def assertError(self, fragment, errors):
        self.assertTrue(any(fragment in e for e in errors),
                        f"no error containing {fragment!r} in {errors}")

    def test_fixture_passes(self):
        self.assertEqual(self.errors(), [])

    def test_missing_entry_is_an_error(self):
        self.assertError("no entry for land_cover.paved.alb",
                         self.errors(registry(**{"land_cover.paved.alb": None})))

    def test_entry_matching_no_record_is_an_error(self):
        errors = self.errors(registry(**{"land_cover.paved.emis": entry()}))
        self.assertError("land_cover.paved.emis matches no evidence record",
                         errors)

    def test_archetype_only_path_needs_no_entry(self):
        errors = self.errors(registry(
            **{"land_cover.paved.only_in_archetype": entry()}))
        self.assertError("only_in_archetype matches no evidence record", errors)

    def test_duplicate_registry_key_is_refused(self):
        text = "parameters:\n  a.b: {}\n  a.b: {}\n"
        with self.assertRaises(yaml.YAMLError):
            load_registry_text(text)

    def test_invalid_vocabulary(self):
        for field, value in (("admission", "kept"), ("type", "physical"),
                             ("status", "done"), ("reason", "unclear")):
            with self.subTest(field=field):
                bad = entry(**{field: value})
                if field == "reason":
                    bad["status"] = "pending_review"
                self.assertError(
                    f"invalid {field} {value!r}",
                    self.errors(registry(**{"land_cover.paved.alb": bad})))

    def test_activity_must_be_boolean_or_null(self):
        self.assertError("activity must be true, false or null", self.errors(
            registry(**{"land_cover.paved.alb": entry(activity="yes")})))

    def test_vocabulary_block_must_match_checker(self):
        doc = registry()
        doc["vocabulary"]["type"].pop("model_setting")
        self.assertError("vocabulary.type must document exactly",
                         self.errors(doc))

    def test_clear_held_needs_type(self):
        self.assertError("a clear held parameter requires a type", self.errors(
            registry(**{"land_cover.paved.alb": entry(status="clear")})))

    def test_clear_held_with_type_passes(self):
        self.assertEqual(self.errors(registry(**{
            "land_cover.paved.alb":
                entry(status="clear", type="physical_attribute")})), [])

    def test_pending_review_needs_reason(self):
        self.assertError("pending_review requires a reason", self.errors(
            registry(**{"land_cover.paved.alb":
                        entry(status="pending_review")})))

    def test_reason_only_with_pending_review(self):
        self.assertError("reason is allowed only with pending_review",
                         self.errors(registry(**{"land_cover.paved.alb": entry(
                             reason="insufficient_evidence")})))

    def test_non_held_admission_needs_null_type(self):
        self.assertError("requires a null type", self.errors(registry(**{
            "construction.wall_insulation_layer": entry(
                admission="not_a_parameter", type="model_setting")})))

    def test_missing_and_unknown_fields(self):
        bad = entry()
        del bad["activity"]
        bad["group"] = "x"
        errors = self.errors(registry(**{"land_cover.paved.alb": bad}))
        self.assertError("missing fields ['activity']", errors)
        self.assertError("unknown fields ['group']", errors)

    def test_record_exception_must_name_an_evidence_record(self):
        bad = entry(record_exceptions={"records/nowhere": {
            "status": "pending_review", "reason": "conflicting_evidence"}})
        self.assertError("records/nowhere is not an evidence record",
                         self.errors(registry(**{"land_cover.paved.alb": bad})))

    def test_record_exception_must_carry_the_path(self):
        bad = entry(record_exceptions={PROFILE: {"status": "unclassified"}})
        self.assertError("does not carry this path",
                         self.errors(registry(**{"land_cover.paved.alb": bad})))

    def test_record_exception_values_are_validated(self):
        bad = entry(record_exceptions={PAVED: {"status": "pending_review"}})
        self.assertError("pending_review requires a reason",
                         self.errors(registry(**{"land_cover.paved.alb": bad})))

    def test_record_scale_key_must_be_an_evidence_record(self):
        self.assertError("records/nowhere is not an evidence record",
                         self.errors(scales=scale_registry({
                             "records/nowhere": {"status": "not_applicable"}})))

    def test_record_scale_rules(self):
        cases = (
            ({"status": "clear", "basis": "declared"},
             "a clear scale requires a scale"),
            ({"status": "clear", "scale": "material"},
             "a clear scale requires a basis"),
            ({"status": "clear", "scale": "material", "basis": "guessed"},
             "invalid basis 'guessed'"),
            ({"status": "clear", "scale": "city", "basis": "declared"},
             "invalid scale 'city'"),
            ({"status": "not_applicable", "scale": "material"},
             "not_applicable requires a null scale"),
            ({"status": "pending_review"}, "pending_review requires a reason"),
        )
        for value, fragment in cases:
            with self.subTest(fragment=fragment):
                self.assertError(fragment, self.errors(
                    scales=scale_registry({PAVED: value})))

    def test_record_scale_path_exception_must_be_carried(self):
        scales = scale_registry({PAVED: {
            "status": "clear", "scale": "material", "basis": "declared",
            "path_exceptions": {"land_cover.paved.emis": {
                "status": "not_applicable"}}}})
        self.assertError("is not a path this record carries",
                         self.errors(scales=scales))

    def test_record_exception_replaces_rather_than_inherits(self):
        pending = entry(status="pending_review", reason="conflicting_evidence",
                        record_exceptions={PAVED: {
                            "type": "physical_attribute", "status": "clear"}})
        self.assertEqual(
            self.errors(registry(**{"land_cover.paved.alb": pending})), [])

    def test_path_exception_replaces_rather_than_inherits(self):
        scales = scale_registry({PAVED: {
            "status": "clear", "scale": "material", "basis": "declared",
            "path_exceptions": {"land_cover.paved.alb": {
                "status": "not_applicable"}}}})
        self.assertEqual(self.errors(scales=scales), [])

    def test_unreadable_registry_is_an_error_not_a_traceback(self):
        from unittest import mock
        with mock.patch("check_db.load_classification",
                        side_effect=yaml.YAMLError("duplicate key")):
            errors, _ = classification_check(RECORDS)
        self.assertError("duplicate key", errors)

    def test_valid_record_scale_passes(self):
        scales = scale_registry({PAVED: {
            "status": "clear", "scale": "material", "basis": "declared",
            "path_exceptions": {"land_cover.paved.thermal_layers.dz": {
                "status": "pending_review",
                "reason": "insufficient_evidence"}}}})
        self.assertEqual(self.errors(scales=scales), [])


class ShippedRegistryTests(unittest.TestCase):
    """The committed registries are complete against db/records."""

    def test_complete_both_ways_and_valid(self):
        records, _, _ = load_all()
        errors, summary = classification_check(
            records, load_classification(), load_record_scale())
        self.assertEqual(errors, [])
        self.assertIn("errors", summary)

    def test_seed_classifies_nothing(self):
        entries = load_classification()["parameters"]
        records, _, _ = load_all()
        carried = set()
        for path, rec in records.items():
            if path.startswith("records/"):
                carried |= record_parameter_paths(rec)
        self.assertEqual(set(entries), carried)
        for gpath, value in entries.items():
            with self.subTest(path=gpath):
                self.assertEqual(value["status"], "unclassified")
                self.assertIsNone(value["type"])


class SiteDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.sources, cls.places = load_all()
        cls.sidecars = load_site_provenance()
        cls.policy = load_site_policy()
        cls.used_by, cls.cluster = build_graph(cls.records)
        cls.gpath = "material.thermal_conductivity"
        cls.path = next(p for p, r in sorted(cls.records.items())
                        if p.startswith("records/materials/")
                        and cls.gpath in record_parameter_paths(r))

    def render(self, classification, record_scale=None):
        return record_page(
            self.path, self.records[self.path], self.records, self.sources,
            self.used_by, self.cluster, sidecars=self.sidecars,
            policy=self.policy, classification=classification,
            record_scale=record_scale)

    def registry_with(self, value):
        entries = deepcopy(load_classification()["parameters"])
        entries[self.gpath] = value
        return entries

    def test_clear_entry_shows_a_type_chip(self):
        page = self.render(self.registry_with(
            entry(status="clear", type="physical_attribute")))
        self.assertIn(">physical attribute</span>", page)

    def test_pending_entry_shows_under_review(self):
        page = self.render(self.registry_with(entry(
            status="pending_review", reason="conflicting_evidence")))
        self.assertIn(">under review</span>", page)

    def test_unclassified_registry_changes_nothing(self):
        bare = self.render(None)
        seeded = self.render(load_classification()["parameters"],
                             load_record_scale()["records"])
        self.assertEqual(bare, seeded)

    def test_record_exception_overrides_the_entry(self):
        page = self.render(self.registry_with(entry(
            status="clear", type="physical_attribute",
            record_exceptions={self.path: {
                "status": "pending_review",
                "reason": "insufficient_evidence"}})))
        self.assertIn(">under review</span>", page)
        self.assertNotIn(">physical attribute</span>", page)

    def test_reviewed_scale_shows_with_its_basis(self):
        page = self.render(None, {self.path: {
            "scale": "material", "status": "clear", "basis": "declared"}})
        self.assertIn("Reviewed scale", page)
        self.assertIn("Material · basis: declared applicable scale", page)
        self.assertNotIn("Reviewed scale", self.render(None, {}))

    def test_switch_turns_the_display_off(self):
        from unittest import mock
        import build_site
        with mock.patch.object(build_site, "SHOW_CLASSIFICATION", False):
            page = self.render(
                self.registry_with(
                    entry(status="clear", type="physical_attribute")),
                {self.path: {"scale": "material", "status": "clear",
                             "basis": "declared"}})
        self.assertNotIn(">physical attribute</span>", page)
        self.assertNotIn("Reviewed scale", page)

    def test_hour_profile_row_maps_to_its_star_path(self):
        rec = RECORDS[PROFILE]
        html = params_table(
            rec["parameters"], rec["target"], rec=rec,
            classification={"profile.traffic.working_day.*": entry(
                status="clear", type="physical_attribute")})
        self.assertIn(">physical attribute</span>", html)

    def test_archetype_pages_show_no_chips(self):
        path = next((p for p, r in sorted(self.records.items())
                     if p.startswith("archetypes/") and r.get("parameters")),
                    None)
        if path is None:
            self.skipTest("no archetype carries inline parameters")
        entries = {k: entry(status="clear", type="model_coefficient")
                   for k in load_classification()["parameters"]}
        page = record_page(
            path, self.records[path], self.records, self.sources,
            self.used_by, self.cluster, sidecars=self.sidecars,
            policy=self.policy, classification=entries)
        self.assertNotIn(">model coefficient</span>", page)


if __name__ == "__main__":
    unittest.main()

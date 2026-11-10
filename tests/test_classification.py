"""The parameter-classification registries and their site display."""

from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_site import (  # noqa: E402
    build_graph,
    load_classification_display,
    load_site_policy,
    load_site_provenance,
    params_table,
    record_page,
)
from check_db import (  # noqa: E402
    CLASSIFICATION_FIELDS,
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
                             status="clear", type="physical_attribute",
                             reason="insufficient_evidence")})))

    def test_unclassified_requires_null_type_activity_and_reason(self):
        for field, value in (("type", "physical_attribute"),
                             ("activity", True),
                             ("reason", "insufficient_evidence")):
            with self.subTest(field=field):
                self.assertError(
                    f"unclassified requires null {field}",
                    self.errors(registry(**{"land_cover.paved.alb": entry(
                        **{field: value})})))

    def test_pending_review_type_is_a_candidate(self):
        self.assertEqual(self.errors(registry(**{
            "land_cover.paved.alb": entry(
                status="pending_review", type="model_coefficient",
                reason="conflicting_evidence", activity=False)})), [])

    def test_unclassified_exception_keeps_the_paths_activity(self):
        clear = entry(status="clear", type="physical_attribute",
                      activity=False,
                      record_exceptions={PAVED: {"status": "unclassified"}})
        self.assertEqual(
            self.errors(registry(**{"land_cover.paved.alb": clear})), [])
        clear["record_exceptions"][PAVED]["type"] = "model_coefficient"
        self.assertError(
            "unclassified requires null type",
            self.errors(registry(**{"land_cover.paved.alb": clear})))

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

    def test_record_exception_may_set_admission(self):
        derived = entry(status="clear", type="physical_attribute",
                        record_exceptions={PAVED: {
                            "admission": "sources_only", "status": "clear"}})
        self.assertEqual(
            self.errors(registry(**{"land_cover.paved.alb": derived})), [])

    def test_exception_admission_rule_applies_after_the_exception(self):
        typed = entry(status="clear", type="physical_attribute",
                      record_exceptions={PAVED: {
                          "admission": "sources_only", "status": "clear",
                          "type": "physical_attribute"}})
        self.assertError(
            "admission 'sources_only' requires a null type",
            self.errors(registry(**{"land_cover.paved.alb": typed})))

    def test_exception_admission_is_validated(self):
        bad = entry(record_exceptions={PAVED: {
            "admission": "dropped", "status": "unclassified"}})
        self.assertError(
            "invalid admission 'dropped'",
            self.errors(registry(**{"land_cover.paved.alb": bad})))

    def test_top_level_must_be_a_mapping(self):
        for value in ([], "text", 3):
            with self.subTest(value=value):
                errors, _ = classification_check(RECORDS, value,
                                                 scale_registry())
                self.assertError(
                    "parameter_classification.yml: top level must be a "
                    "mapping", errors)
                errors, _ = classification_check(RECORDS, registry(), value)
                self.assertError(
                    "record_scale.yml: top level must be a mapping", errors)

    def test_list_values_are_errors_not_crashes(self):
        for field in ("admission", "type", "status", "reason"):
            with self.subTest(field=field):
                bad = entry(**{field: ["clear"]})
                self.assertError(
                    f"invalid {field} ['clear']",
                    self.errors(registry(**{"land_cover.paved.alb": bad})))
        for field in ("scale", "status", "reason", "basis"):
            with self.subTest(field=field):
                value = {"status": "clear", "scale": "material",
                         "basis": "declared", field: ["material"]}
                self.assertError(
                    f"invalid {field} ['material']",
                    self.errors(scales=scale_registry({PAVED: value})))

    def test_list_valued_exception_fields_are_errors(self):
        bad = entry(record_exceptions={PAVED: {"status": ["clear"],
                                               "admission": ["held"]}})
        errors = self.errors(registry(**{"land_cover.paved.alb": bad}))
        self.assertError("invalid status ['clear']", errors)
        self.assertError("invalid admission ['held']", errors)

    def test_non_string_keys_are_errors(self):
        doc = registry()
        doc["parameters"][7] = entry()
        doc["parameters"]["land_cover.paved.alb"] = entry(
            record_exceptions={None: {"status": "unclassified"}})
        errors = self.errors(doc)
        self.assertError("key 7 must be a string", errors)
        self.assertError("record_exceptions: key None must be a string",
                         errors)
        errors = self.errors(scales=scale_registry({
            1: {"status": "not_applicable"}}))
        self.assertError("key 1 must be a string", errors)

    def test_unhashable_key_is_refused_by_the_loader(self):
        text = "parameters:\n  ? [a, b]\n  : {}\n"
        with self.assertRaises(yaml.YAMLError):
            load_registry_text(text)

    def test_non_mapping_top_level_is_returned_for_the_check(self):
        self.assertEqual(load_registry_text("- a\n- b\n"), ["a", "b"])
        self.assertEqual(load_registry_text(""), {})

    def test_flags(self):
        base = {"status": "clear", "scale": "material", "basis": "declared"}
        self.assertEqual(self.errors(scales=scale_registry({PAVED: dict(
            base, flags=["footprint_fitted", "grid_wide",
                         "declaration_conflict"])})), [])
        cases = ((["fitted"], "invalid flag 'fitted'"),
                 ("grid_wide", "flags must be a list or null"),
                 (["grid_wide", "grid_wide"], "flags repeat a value"),
                 ([["grid_wide"]], "invalid flag ['grid_wide']"))
        for flags, fragment in cases:
            with self.subTest(fragment=fragment):
                self.assertError(fragment, self.errors(scales=scale_registry(
                    {PAVED: dict(base, flags=flags)})))

    def test_path_exception_flags_are_validated(self):
        scales = scale_registry({PAVED: {
            "status": "clear", "scale": "material", "basis": "declared",
            "flags": ["footprint_fitted"],
            "path_exceptions": {"land_cover.paved.alb": {
                "status": "clear", "scale": "neighbourhood",
                "basis": "source_states", "flags": ["grid_wide"]}}}})
        self.assertEqual(self.errors(scales=scales), [])
        scales["records"][PAVED]["path_exceptions"]["land_cover.paved.alb"][
            "flags"] = ["everywhere"]
        self.assertError("invalid flag 'everywhere'",
                         self.errors(scales=scales))

    def test_not_applicable_with_null_scale_is_valid(self):
        for value in ({"status": "not_applicable"},
                      {"status": "not_applicable", "scale": None,
                       "basis": "source_states"}):
            with self.subTest(value=value):
                self.assertEqual(
                    self.errors(scales=scale_registry({PAVED: value})), [])

    def test_pending_scale_may_name_a_candidate(self):
        self.assertEqual(self.errors(scales=scale_registry({PAVED: {
            "status": "pending_review", "scale": "neighbourhood",
            "reason": "conflicting_evidence",
            "flags": ["declaration_conflict"]}})), [])

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

    def test_one_complete_entry_per_carried_path(self):
        entries = load_classification()["parameters"]
        records, _, _ = load_all()
        carried = set()
        for path, rec in records.items():
            if path.startswith("records/"):
                carried |= record_parameter_paths(rec)
        self.assertEqual(set(entries), carried)
        for gpath, value in entries.items():
            with self.subTest(path=gpath):
                self.assertIsInstance(value, dict)
                self.assertLessEqual(set(CLASSIFICATION_FIELDS), set(value))
                if ".context." in gpath:
                    self.assertEqual(value["admission"], "not_a_parameter")

    def test_record_scale_is_a_mapping_of_evidence_records(self):
        scales = load_record_scale()["records"]
        self.assertIsInstance(scales, dict)
        for rpath in scales:
            self.assertTrue(rpath.startswith("records/"), rpath)
            self.assertFalse(rpath.endswith(".yml"), rpath)


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

    @staticmethod
    def unclassified():
        """Every shipped path, all unclassified, whatever the shipped state."""
        shipped = load_classification()["parameters"]
        return {k: entry(admission=v.get("admission", "held"))
                for k, v in shipped.items()}

    def registry_with(self, value):
        entries = self.unclassified()
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
        seeded = self.render(self.unclassified(), {})
        self.assertEqual(bare, seeded)

    def test_pending_candidate_shows_only_in_the_title(self):
        page = self.render(self.registry_with(entry(
            status="pending_review", type="physical_attribute",
            reason="conflicting_evidence")))
        self.assertIn(">under review</span>", page)
        self.assertIn("candidate: physical attribute", page)
        self.assertNotIn(">physical attribute</span>", page)

    def test_note_is_escaped_in_the_title(self):
        page = self.render(self.registry_with(entry(
            status="pending_review", reason="insufficient_evidence",
            note='needs the "K1" <table> from the source')))
        self.assertIn("needs the &quot;K1&quot; &lt;table&gt; from the "
                      "source", page)
        self.assertNotIn('"K1" <table>', page)

    def test_list_valued_fields_render_nothing(self):
        page = self.render(self.registry_with(entry(
            status="clear", type=["physical_attribute"])),
            {self.path: {"status": "clear", "scale": ["material"],
                         "basis": ["declared"], "flags": "grid_wide"}})
        self.assertEqual(page, self.render(self.unclassified(), {}))

    def test_exception_is_found_by_the_page_path(self):
        rec = dict(self.records[self.path], record="records/elsewhere")
        page = record_page(
            self.path, rec, self.records, self.sources, self.used_by,
            self.cluster, sidecars=self.sidecars, policy=self.policy,
            classification=self.registry_with(entry(
                record_exceptions={self.path: {
                    "status": "clear", "type": "model_coefficient"}})))
        self.assertIn(">model coefficient</span>", page)

    def test_flags_show_beside_the_reviewed_scale(self):
        page = self.render(None, {self.path: {
            "scale": "material", "status": "clear", "basis": "declared",
            "flags": ["footprint_fitted", "grid_wide"]}})
        self.assertIn("Material · basis: declared applicable scale <small>"
                      "fitted over a flux footprint; applied grid-wide"
                      "</small>", page)
        plain = self.render(None, {self.path: {
            "scale": "material", "status": "clear", "basis": "declared"}})
        self.assertIn("Material · basis: declared applicable scale</span>",
                      plain)

    def test_pending_scale_candidate_shows_only_in_the_title(self):
        page = self.render(None, {self.path: {
            "scale": "neighbourhood", "status": "pending_review",
            "reason": "conflicting_evidence"}})
        self.assertIn('title="candidate: Neighbourhood">under review '
                      '(conflicting evidence)</span>', page)

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

    def test_display_load_never_raises(self):
        from unittest import mock
        import build_site
        with mock.patch.object(build_site, "load_classification",
                               side_effect=yaml.YAMLError("duplicate key")), \
                mock.patch.object(build_site, "load_record_scale",
                                  return_value=["not", "a", "mapping"]):
            self.assertEqual(load_classification_display(), (None, None))
        with mock.patch.object(build_site, "load_classification",
                               return_value={"parameters": []}), \
                mock.patch.object(build_site, "load_record_scale",
                                  return_value={"records": None}):
            self.assertEqual(load_classification_display(), (None, {}))

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

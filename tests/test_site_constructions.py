"""Construction pages render their layers as linked tables, not a repr."""

from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_site import (  # noqa: E402
    build_graph,
    build_search_index,
    leaf_pairs,
    load_site_policy,
    load_site_provenance,
    record_page,
)
from check_db import load_all  # noqa: E402


CONSTRUCTION = (
    "records/constructions/"
    "uk--hertwig2024a--residential-pre-1900-1944-pitched-roof-type-all"
)
BRICK = "records/materials/generic--hertwig2024a--brick-exposed"
# a construction whose wall designates layer 2 as its insulation layer
DESIGNATED = (
    "records/constructions/"
    "uk--hertwig2024a--residential-2004-2009-pitched-roof-type-"
    "terraced-detached-semi-detached"
)

# what a Python repr of a layer list looks like on a page
REPR = re.compile(r"\{(?:'|&#x27;)material(?:'|&#x27;)")


class ConstructionPageTests(unittest.TestCase):
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

    def test_no_page_prints_a_layer_list_as_a_repr(self):
        for path, rec in self.records.items():
            if not (rec.get("target") == "construction"
                    or path.startswith("archetypes/typologies/")
                    or rec.get("target") == "material"):
                continue
            with self.subTest(path=path):
                self.assertIsNone(REPR.search(self.render(path)))

    def test_layers_link_materials_from_the_exterior(self):
        page = self.render(CONSTRUCTION)
        params = self.records[CONSTRUCTION]["parameters"]
        for facet in ("roof", "wall"):
            self.assertIn(f"<h4>{facet.capitalize()}</h4>", page)
            for pos, layer in enumerate(params[facet], start=1):
                ref = layer["material"]
                name = self.records[ref]["name"]
                row = re.search(
                    rf'<td class="num">{pos}</td><td><a href="(?:\.\./)*'
                    rf'{re.escape(ref)}\.html">{re.escape(name)}</a>'
                    rf'.*?<td class="num">{re.escape(str(layer["thickness"]))}'
                    r"</td>",
                    page,
                )
                self.assertIsNotNone(row, f"{facet} layer {pos}")
                # the same provenance badge an archetype's uses row carries
                self.assertIn('class="pstate', row.group(0))
        self.assertIn("Layer (from exterior)", page)
        self.assertIn("Thickness (m)", page)
        # selector 6 designates nothing and marks no row
        self.assertIn("<code>roof_insulation_layer</code> = 6: no insulation "
                      "layer is designated.", page)
        self.assertNotIn('<tr class="insul">', page)

    def test_designated_insulation_layer_is_marked(self):
        page = self.render(DESIGNATED)
        wall = self.records[DESIGNATED]["parameters"]["wall"]
        ref = wall[1]["material"]
        self.assertRegex(
            page,
            r'<tr class="insul"><td class="num">2</td><td><a href="[^"]*'
            + re.escape(ref) + r'\.html">',
        )
        self.assertIn("layer 2 is the designated insulation layer", page)

    def test_material_page_lists_constructions_that_use_it(self):
        page = self.render(BRICK)
        self.assertIn("<h3>Used in constructions</h3>", page)
        users = [
            path for path, rec in self.records.items()
            if rec.get("target") == "construction"
            and any(layer.get("material") == BRICK
                    for facet in ("roof", "wall")
                    for layer in rec["parameters"].get(facet, []))
        ]
        self.assertIn(CONSTRUCTION, users)
        for path in users:
            self.assertIn(f'{path}.html">', page)
        self.assertIn("wall layer 1, 0.22 m", page)
        self.assertIn(f"Used by {len(users)} construction", page)

    def test_previews_summarise_layer_lists(self):
        pairs = dict(leaf_pairs(self.records[CONSTRUCTION]["parameters"]))
        self.assertEqual(pairs["roof"], "2 layers")
        self.assertEqual(pairs["wall"], "2 layers")
        self.assertEqual(dict(leaf_pairs({"x": [{"a": 1}]}))["x"], "1 entry")
        self.assertEqual(dict(leaf_pairs({"x": [1, 2]}))["x"], "[1, 2]")
        index = build_search_index(self.records, self.sources, self.places)
        for entry in index:
            self.assertIsNone(REPR.search(str(entry)), entry.get("p"))


if __name__ == "__main__":
    unittest.main()

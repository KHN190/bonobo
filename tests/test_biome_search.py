"""knowledge.search_target / biome_options: a search heads into the biome in view that finds its kind soonest (the
game's tree counts, spawn lists, village biomes), and expected_find_s prices it from the same options (K9)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import data, knowledge as K  # noqa: E402

C = data.CHUNK_BLOCKS
FEET = (C // 2, 64, C // 2)


def view(fill, **at):
    """Chunks within 2 of the origin chunk in biome `fill`, plus `at` {"cx,cz": biome}."""
    chunks = {(cx, cz): fill for cx in range(-2, 3) for cz in range(-2, 3)}
    for key, biome in at.items():
        cx, cz = (int(v) for v in key.lstrip("c").split("_"))
        chunks[(cx, cz)] = biome
    return [(cx, cz, f"minecraft:{b}") for (cx, cz), b in chunks.items()]


def column(cx, cz):
    return cx * C + C // 2, cz * C + C // 2


class SearchTarget(unittest.TestCase):

    def test_table(self):
        forest_east = view("desert", c5_0="forest")
        plains_north = view("ocean", **{"c0_-5": "plains"})
        rows = [("must fail: logs from a desert head to the forest, not into the desert", "minecraft:oak_log",
                 forest_east, column(5, 0)),
                ("must fail: sheep over the ocean head to the plains", "minecraft:sheep", plains_north, column(0, -5)),
                ("a village from a desert stays in the desert (a village biome)", "villager", forest_east,
                 column(0, 0)),
                ("no biome known: the legs as before", "minecraft:oak_log", [], None)]
        for name, kind, biomes, want in rows:
            with self.subTest(name):
                self.assertEqual(K.search_target(kind, {"feet": FEET, "biomes": biomes}), want)


class OnePrice(unittest.TestCase):

    def test_the_price_is_the_search_s(self):
        for kind, biomes in (("minecraft:oak_log", view("desert", c5_0="forest")),
                             ("minecraft:sheep", view("ocean", **{"c0_-5": "plains"}))):
            with self.subTest(kind):
                facts = {"feet": FEET, "biomes": biomes, "y": 64, "held": {}}
                best = min(K.biome_options(kind, facts), key=lambda o: o[1])
                self.assertEqual(K.expected_find_s(kind, facts), best[1])

    def test_table(self):
        tree_dens = {b: data.TREES_PER_CHUNK[b] / C ** 2 for b in ("forest", "plains")}
        rows = [("a forest under the feet: a search there", "minecraft:oak_log", view("forest"),
                 K._area_s(tree_dens["forest"])),
                ("no biome known: today's price", "minecraft:oak_log", [], K._area_s(K.FIND_DENSITY["tree"] / C ** 2))]
        for name, kind, biomes, at_least in rows:
            with self.subTest(name):
                self.assertGreaterEqual(K.expected_find_s(kind, {"feet": FEET, "biomes": biomes}), at_least)
        must_fail = [("logs in a plains view no sooner than in a forest view", "minecraft:oak_log", view("plains"),
                      view("forest")),
                     ("sheep in an ocean view no sooner than on plains", "minecraft:sheep", view("ocean"), view("plains")),
                     ("a village from an ocean no sooner than on plains", "villager", view("ocean"), view("plains"))]
        for name, kind, slow, fast in must_fail:
            with self.subTest(name):
                self.assertGreater(K.expected_find_s(kind, {"feet": FEET, "biomes": slow}),
                                   K.expected_find_s(kind, {"feet": FEET, "biomes": fast}))


if __name__ == "__main__":
    unittest.main()

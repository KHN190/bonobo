"""knowledge.expected_find_s: the seconds to find a kind never seen, from how the game places it — an ore's veins at its
band, the soil's depth, a grassland group's weight, a village's region, a biome-made patch's prior density."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import data, knowledge as K  # noqa: E402

TPS = K.TICKS_PER_S
R = data.SEARCH_LOOK_R
CHUNK2 = data.CHUNK_BLOCKS ** 2


def area_s(per_block2):
    return (1.0 / (2 * R * per_block2) + 2 * R / 3) * K.WALK_TICKS_PER_BLOCK / TPS


class OreLayer(unittest.TestCase):

    def test_table(self):
        _c_hi, (c_lo, c_lo_size, c_lo_lo, c_lo_hi, _) = data.ORE_VEINS["coal_ore"]
        peak = (c_lo_lo + c_lo_hi) // 2
        *_iron, (i_v, i_size, i_lo, i_hi, _) = data.ORE_VEINS["iron_ore"]
        rows = [("a uniform placement alone: veins × size over its span", "iron_ore", i_lo + 1,
                 i_v * i_size / (i_hi - i_lo)),
                ("a triangle's peak: twice the mean", "coal_ore", peak, c_lo * c_lo_size * 2 / (c_lo_hi - c_lo_lo)),
                ("must fail: below every placement", "coal_ore", c_lo_lo - 1, 0.0)]
        for name, ore, y, want in rows:
            with self.subTest(name):
                self.assertAlmostEqual(K.ore_layer_blocks(ore, y), want)
        _up, (v, size, lo, hi, _s), (v2, size2, lo2, hi2, _s2) = data.ORE_VEINS["iron_ore"]
        self.assertAlmostEqual(K.ore_layer_blocks("iron_ore", 16), v * size * 2 / (hi - lo) + v2 * size2 / (hi2 - lo2))


class Class(unittest.TestCase):

    def test_table(self):
        rows = [("an ore", "iron_ore", ("ore", "data.ORE_VEINS")),
                ("its deepslate form is the same ore", "minecraft:deepslate_iron_ore", ("ore", "data.ORE_VEINS")),
                ("the rock under the soil", "stone", ("soil", "data.SOIL_DEPTH")),
                ("deepslate: below DEEPSLATE_TOP", "deepslate", ("deep", "data.DEEPSLATE_TOP")),
                ("a log is a tree", "minecraft:oak_log", ("tree", "knowledge.FIND_DENSITY.tree")),
                ("a grassland animal", "minecraft:cow", ("animal", "data.CREATURE_CHUNK_P")),
                ("a village's", f"{data.COLORS[0]}_bed", ("village", "data.VILLAGE_REGION_BLOCKS")),
                ("a biome-made patch", "sand", ("sand", "knowledge.FIND_DENSITY.sand")),
                ("must fail: a kind no table knows is a prior, not free", "pointed_dripstone",
                 ("other", "knowledge.FIND_DENSITY.other"))]
        for name, kind, want in rows:
            with self.subTest(name):
                self.assertEqual(K.find_class(kind), want)
        from bonobo import dispatch
        tags = [("the ore table is the game's", "data.ORE_VEINS", "game"),
                ("a village region is the game's", "data.VILLAGE_REGION_BLOCKS", "game"),
                ("must fail: a biome density is no game data", "knowledge.FIND_DENSITY.tree", "prior")]
        for name, item, want in tags:
            with self.subTest(name):
                self.assertEqual(dispatch.price_source(item), want)


class Seconds(unittest.TestCase):

    def test_table(self):
        hand, pick = {"y": 16, "held": {}}, {"y": 16, "held": {"pickaxe": 1}}
        weight = data.PASSIVE_WEIGHT
        common, rare = max(weight, key=weight.get), min(weight, key=weight.get)
        tree = K.FIND_DENSITY["tree"] / CHUNK2
        rows = [("a tree: the sweep and the walk to it", K.expected_find_s("oak_log", hand), area_s(tree)),
                ("a village: one a region", K.expected_find_s("villager", hand),
                 area_s(1.0 / data.VILLAGE_REGION_BLOCKS ** 2)),
                ("an animal: the chunk's chance by its weight", K.expected_find_s("minecraft:" + common, hand),
                 area_s(data.CREATURE_CHUNK_P / CHUNK2 * weight[common] / sum(weight.values())))]
        for name, got, want in rows:
            with self.subTest(name):
                self.assertAlmostEqual(got, want)
        must_fail = [("a rare animal no sooner than the commonest", "minecraft:" + rare, hand, "minecraft:" + common, hand),
                     ("a village no sooner than a tree", "villager", hand, "oak_log", hand),
                     ("iron by hand no sooner than with a pickaxe", "iron_ore", hand, "iron_ore", pick),
                     ("iron from the surface no sooner than at its band", "iron_ore", dict(pick, y=70), "iron_ore", pick),
                     ("diamond no sooner than iron", "diamond_ore", dict(pick, y=-58), "iron_ore", pick)]
        for name, slow, f_slow, fast, f_fast in must_fail:
            with self.subTest(name):
                self.assertGreater(K.expected_find_s(slow, f_slow), K.expected_find_s(fast, f_fast))


if __name__ == "__main__":
    unittest.main()

"""K8, "nothing found within the radius": every findable kind (from the production tables) is looked for at one radius
the price reads too, and a miss falls to the next ring, then a seek — never a failure while the world is unexplored."""
import inspect
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo import cost, data, explore, gather, knowledge as K, world  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bench_rows.json")


def findable():
    """Every kind a search looks for, from the production tables: each ore at its band, logs, the soil's stone, the
    biome patches, every creature a biome spawns, the village's."""
    ores = [b for drop, (blocks, _t) in K.MINE.items() if drop in K.FIND_AT for b in blocks]
    mobs = sorted({m for spawns in data.BIOME_CREATURES.values() for m in spawns})
    patches = sorted({k for k, v in K.AREA_KINDS.items() if v in data.BIOME_PATCH})
    return ores + list(data.GROUPS["log"]) + ["stone"] + patches + mobs + list(data.VILLAGE_ONLY)


class OneRadius(unittest.TestCase):

    def test_every_kind(self):
        block_r = inspect.signature(cost.Cost.distance).parameters["radius"].default
        look_r = inspect.signature(world.nearest).parameters["radius"].default
        mob_r = inspect.signature(world.look_around).parameters["radius"].default
        for kind in findable():
            mob = K.find_class(kind)[0] == "animal"
            with self.subTest(kind):
                # must fail: the price reading one radius and the search another (cost's mine at 32, the skill at 48)
                if mob:
                    self.assertEqual((mob_r, explore.LOOK_MOBS), (data.SEARCH_MOB_R, data.SEARCH_MOB_R))
                else:
                    self.assertEqual((block_r, look_r, explore.LOOK_BLOCKS, data.SEARCH_RINGS[-1]),
                                     (data.SEARCH_LOOK_R,) * 4)


class AMissLooksFurther(unittest.TestCase):

    def test_rings(self):
        rings = data.SEARCH_RINGS
        rows = [(f"after the ring at {a}: {b}", a, b) for a, b in zip(rings, rings[1:])]
        rows.append(("must fail: past the last ring a seek, not a failure", rings[-1], None))
        for name, radius, want in rows:
            with self.subTest(name):
                self.assertEqual(K.next_look(radius), want)

    def test_the_last_ring_seeks(self):
        for kind in findable():
            if K.find_class(kind)[0] == "animal":
                continue
            with self.subTest(kind), mock.patch.object(gather, "seek_blocks") as seek, \
                    mock.patch.object(gather.api, "detail"):
                got = gather._look_further(object(), [kind], kind, data.SEARCH_RINGS[-1], "none in range")
                seek.assert_called_once()
                self.assertEqual(got, data.SEARCH_RINGS[0])


class AStepsMissSeeks(unittest.TestCase):
    """dispatch: a step whose skill found none in range (NotAvailable, not a nav failure) is answered by a seek for
    the step's own kinds (knowledge.step_kinds), then run again — the water fills included (E5)."""

    def test_table(self):
        from bonobo import dispatch
        from bonobo.planner import Step
        rows = [("mine", Step("mine", "minecraft:raw_iron", 1, {"blocks": ["iron_ore"]}), ["iron_ore"]),
                ("gather", Step("gather", "log", 1), list(data.GROUPS["log"])),
                ("must fail: a water bucket's miss seeks water", Step("fill", "minecraft:water_bucket", 1), ["water"]),
                ("must fail: bottles' miss seeks water", Step("fill", "minecraft:potion", 3), ["water"])]
        for name, step, kinds in rows:
            with self.subTest(name):
                self.assertIn(step.kind, dispatch.SEEK_KINDS)
                with mock.patch.object(dispatch.explore, "seek_blocks", return_value=[{}]) as seek, \
                        mock.patch.object(dispatch.nav, "arrive", return_value=True):
                    ctx = mock.Mock(mem=mock.Mock(seen=lambda *a: []), dimension="minecraft:the_nether")
                    with mock.patch.object(dispatch.world, "feet", return_value=(0, 64, 0)):
                        self.assertTrue(dispatch.go_find(ctx, step))
                seek.assert_called_once_with(ctx, kinds)


class TheRowsOwnScene(unittest.TestCase):
    """ore_buried's scene: its iron lies inside the first ring the search looks at, and the price sees it too."""

    def test_ore_buried(self):
        from bonobo.bench.words import est
        from bonobo.planner import Step
        with open(FIXTURE) as fh:
            setup = json.load(fh)["ore_buried"]["setup"]
        world_ = est.scene_world(setup)
        iron = [c for c, b in world_["blocks"].items() if b.endswith("iron_ore")]
        self.assertEqual(len(iron), 1)
        d = sum((a - b) ** 2 for a, b in zip(iron[0], world_["feet"])) ** 0.5
        self.assertLessEqual(d, data.SEARCH_RINGS[0])
        c = est.scene_cost(world_)
        step = Step("mine", "minecraft:raw_iron", 1, {"blocks": ["iron_ore", "deepslate_iron_ore"], "breaks": 1})
        # must fail: the price not seeing the ore the search sees (a search priced for a block in sight)
        self.assertIsNotNone(c._source(step))


if __name__ == "__main__":
    unittest.main()

"""With no way blocks carried, a site reached only over treads is not judged unreachable: the plan gets the blocks
first ("building", the cheapest kind by seconds, G3) and then works the site (Cost.way_blocks_short → planner's
need). The plan rows use only what the base has, so they are red there by assertion (the iron mined with no block
to cross the chasm with)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo import cost as costmod, nav, world  # noqa: E402
from bonobo.data import GROUPS  # noqa: E402
from bonobo.planner import Step, plan_needs  # noqa: E402
from tests.world import bag, inventory, memory, state  # noqa: E402

FEET, ORE = (0, 64, 0), (6, 64, 0)
LO, HI = (-12, 50, -8), (12, 72, 8)
IRON = Step("mine", "minecraft:raw_iron", 1, {"blocks": ["iron_ore", "deepslate_iron_ore"], "tier": 1, "breaks": 1})


def scene(chasm="air"):
    """Stone ground with a 3-wide chasm (air, or lava) between the feet and a remembered iron ore."""
    blocks = {(x, y, z): "stone" for x in range(LO[0], HI[0] + 1) for z in range(LO[2], HI[2] + 1) for y in range(50, 64)}
    for x in range(2, 5):
        for z in range(LO[2], HI[2] + 1):
            for y in range(50, 64):
                blocks.pop((x, y, z), None)
            if chasm != "air":
                blocks[(x, 63, z)] = blocks[(x, 64, z)] = chasm     # up to the feet's level: no tread crosses it
    blocks[ORE] = "iron_ore"
    return world.Region.of(LO, HI, blocks)


def cost_of(region, *items):
    mem = memory()
    mem.note_seen("iron_ore", ORE, "minecraft:overworld")
    snap = world.Snapshot.from_readings(state(x=.5, y=64, z=.5), world.Inventory(inventory(*items)), {}, [], region)
    return costmod.Cost(snap, mem)


def building_before_iron(steps):
    names = {s.token for s in steps}
    iron = next(i for i, s in enumerate(steps) if s.token == "minecraft:raw_iron")
    return any(s.token in GROUPS["building"] for s in steps[:iron]), names


class ThePlanGetsWayBlocksFirst(unittest.TestCase):
    """planner.plan_needs over a chasm to the iron: blocks first when none are carried, none when they are."""

    def test_rows(self):
        rows = [("must fail: no block carried — the iron planned with nothing to cross on", (), True),
                ("blocks carried: no extra blocks planned", (("cobblestone", 16),), False)]
        for name, carried, first in rows:
            with self.subTest(name):
                items = (("stone_pickaxe", 1),) + carried
                steps = plan_needs(bag(inventory(*items)), [("minecraft:raw_iron", 1)], cost_of(scene(), *items))
                got, names = building_before_iron(steps)
                self.assertEqual(got, first, names)


class WayBlocksShort(unittest.TestCase):
    """Cost.way_blocks_short: the stock that lays the way's treads less what is held; 0 when blocks fix nothing."""

    def test_rows(self):
        rows = [("no block, an air chasm: the treads' stock", scene(), (), lambda n: n > 0),
                ("enough carried: none short", scene(), (("cobblestone", 16),), lambda n: n == 0),
                ("must fail: a lava chasm is no tread's to fix", scene("lava"), (), lambda n: n == 0)]
        for name, region, carried, ok in rows:
            with self.subTest(name):
                c = cost_of(region, ("stone_pickaxe", 1), *carried)
                n = c.way_blocks_short(ORE, "mine")
                self.assertTrue(ok(n), n)

    def test_the_stock_lays_its_treads(self):
        for treads in (1, 2, 16, 17, 40):
            with self.subTest(treads=treads):
                self.assertGreaterEqual(nav.place_budget(nav.stock_for(treads)), treads)
                self.assertLess(nav.place_budget(nav.stock_for(treads) - 1), treads)


if __name__ == "__main__":
    unittest.main()

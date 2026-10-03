"""G3/D6: a placed station (crafting table or furnace) is left standing only where the walk back for the plan's own
place of the next use prices cheaper than breaking it, carrying it, and placing it again there (craft.station_kept) —
the one decision point brain.craft_act reads (craft.next_table_use / next_furnace_use), not a bare "needs one again"
bool. Rows: next use here / near (accept5) / far (another area) × hand / axe / pickaxe × table / furnace."""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import craft, nav  # noqa: E402
from bonobo.cost import walk_ticks  # noqa: E402
from bonobo.data import bare  # noqa: E402
from bonobo.game import TICKS_PER_S  # noqa: E402
from bonobo.knowledge import break_overhead, break_ticks, tool_for  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import bag, inventory, slot  # noqa: E402

TABLE = "minecraft:crafting_table"
FURNACE = "minecraft:furnace"
POS = (12984, 74, 12997)            # accept5's table
IRON = (12984 + 10, 69, 12997 + 11)  # accept5's iron: ~15.7 blocks off, a floor below

TOOLS = {
    "hand": bag(inventory()),
    "axe": bag(inventory(slot("wooden_axe"))),
    "pickaxe": bag(inventory(slot("iron_pickaxe"))),
}

DISTANCES = {
    "here": (POS[0], POS[1], POS[2] + 2),
    "near (accept5)": IRON,
    "far (another area)": (POS[0], POS[1], POS[2] + 200),
}


def rebuild_ticks(block, inv, extra_s=0.0):
    """The production cost of carrying a station on: break it with the best tool held, plus one place, plus any
    wait (a furnace still smelting)."""
    tool = tool_for(inv, bare(block))
    return break_ticks(bare(block), tool) + break_overhead() + round((nav.PLACE_S + extra_s) * TICKS_PER_S)


class StationKeptPricesTheCheaperChoice(unittest.TestCase):
    """station_kept ⇔ the walk back costs fewer ticks than breaking, carrying and placing the station again —
    over every combination of station, tool held, and where the next use sits."""

    def test_rows(self):
        for block in (TABLE, FURNACE):
            for tool_name, inv in TOOLS.items():
                for dist_name, next_use in DISTANCES.items():
                    with self.subTest(block=block, tool=tool_name, next_use=dist_name):
                        expected = walk_ticks(math.dist(POS, next_use)) < rebuild_ticks(block, inv)
                        self.assertEqual(craft.station_kept(block, POS, next_use, inv), expected,
                                         f"{block} {tool_name} {dist_name}: expected keep={expected}")

    def test_no_known_next_use_never_keeps(self):
        for block in (TABLE, FURNACE):
            self.assertFalse(craft.station_kept(block, POS, None, TOOLS["hand"]))

    def test_a_furnace_still_cooking_counts_toward_keeping_it(self):
        # the same gap in tool speed, with wait added on the carry side: cooking can only make keeping it cheaper
        near = DISTANCES["near (accept5)"]
        cold = craft.station_kept(FURNACE, POS, near, TOOLS["pickaxe"], extra_s=0.0)
        hot = craft.station_kept(FURNACE, POS, near, TOOLS["pickaxe"], extra_s=50.0)
        self.assertFalse(cold)        # a pickaxe breaks the furnace fast enough that carrying still pays cold
        self.assertTrue(hot)          # ...but not once the wait for it to finish is added to the carry side

    def test_accept5_keeps_the_table_for_the_iron_15_blocks_off(self):
        """accept5's own scene: the table at (12984,74,12997), the iron ~15.7 blocks off at y69 — a hand or a
        pickaxe walks back for it; an axe (the table's own fast tool) breaks it and carries it on instead."""
        self.assertAlmostEqual(math.dist(POS, IRON), 15.68, places=1)
        self.assertTrue(craft.station_kept(TABLE, POS, IRON, TOOLS["hand"]))
        self.assertTrue(craft.station_kept(TABLE, POS, IRON, TOOLS["pickaxe"]))
        self.assertFalse(craft.station_kept(TABLE, POS, IRON, TOOLS["axe"]))


class NextUseWalksThePlanForward(unittest.TestCase):
    """next_table_use / next_furnace_use: the planned place (D6) of the first later step that needs the station —
    price_as_run's own at-chain (cost.site, carried forward past steps with none), None past the plan's end."""

    class _FakeCost:
        """A cost stub: `site` answers from a {id(step): place} map, as price_as_run's own cost.site would;
        `feet` is the fallback start when `last` has no site of its own."""

        def __init__(self, sites, feet=(0, 0, 0)):
            self.sites = sites
            self._feet = feet

        def site(self, step):
            return self.sites.get(id(step))

        def feet(self):
            return self._feet

    def test_rows(self):
        gather = Step("gather", "minecraft:oak_log", 4)
        table_now = Step("craft", "minecraft:crafting_table", 1)
        stick = Step("craft", "minecraft:stick", 4)                       # 2×2: no table needed
        pickaxe = Step("craft", "minecraft:wooden_pickaxe", 1)            # 3×3: needs the table again
        smelt = Step("smelt", "minecraft:iron_ingot", 3)
        steps = [gather, table_now, stick, pickaxe, smelt]
        cost = self._FakeCost({id(pickaxe): (1, 2, 3), id(smelt): (4, 5, 6)})
        self.assertEqual(craft.next_table_use(cost, steps, table_now), (1, 2, 3))
        # the registry match (knowledge.step_station) is the smelt skill's own `station=`; stubbed here so the row
        # only exercises next_furnace_use's own at-chain, not the registry's key matching
        with mock.patch.object(craft._k, "step_station", lambda s: "minecraft:furnace" if s is smelt else None):
            self.assertEqual(craft.next_furnace_use(cost, steps, table_now), (4, 5, 6))

    def test_none_past_the_plans_end(self):
        table_now = Step("craft", "minecraft:crafting_table", 1)
        stick = Step("craft", "minecraft:stick", 4)
        cost = self._FakeCost({})
        self.assertIsNone(craft.next_table_use(cost, [table_now, stick], table_now))

    def test_no_move_in_between_is_zero_distance_not_unknown(self):
        """smelt → smelt with no sited step between them: the next use is right here, not None (which would read
        as "never keep" in station_kept — a free walk back must not be priced as a forced carry)."""
        table_now = Step("craft", "minecraft:crafting_table", 1)
        smelt_a = Step("smelt", "minecraft:iron_ingot", 1)
        smelt_b = Step("smelt", "minecraft:gold_ingot", 1)
        cost = self._FakeCost({id(smelt_a): (5, 70, 5)})     # smelt_a's own site: where the furnace already is
        with mock.patch.object(craft._k, "step_station", lambda s: "minecraft:furnace" if s is smelt_b else None):
            self.assertEqual(craft.next_furnace_use(cost, [table_now, smelt_a, smelt_b], smelt_a), (5, 70, 5))

    def test_carries_the_last_known_place_forward(self):
        """A craft step itself has no site (own_work's never a kinds-step): the place before it (D6) stands in."""
        table_now = Step("craft", "minecraft:crafting_table", 1)
        mine = Step("mine", "minecraft:iron_ore", 1, {"blocks": ["iron_ore"]})
        pickaxe = Step("craft", "minecraft:wooden_pickaxe", 1)
        steps = [table_now, mine, pickaxe]
        cost = self._FakeCost({id(mine): (9, 9, 9)})      # pickaxe's own site is unknown: mine's carries forward
        self.assertEqual(craft.next_table_use(cost, steps, table_now), (9, 9, 9))


if __name__ == "__main__":
    unittest.main()

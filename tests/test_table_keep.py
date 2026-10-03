"""A placed furnace is left standing only where the walk back for the plan's own place of the next use —
priced by the real dig path (_next_use_at's Cost replay), not the straight line — costs fewer ticks than breaking
it, carrying it, and placing it again there (craft.station_kept); the one decision point brain.craft_act reads
(craft.next_furnace_use), not a bare "needs one again" bool. A crafting table is never priced this way: ours (just
placed, or already standing where memory says we put it) is always taken back (craft.take_table / craft_commands),
never another's or the world's. Rows: next use here / near / far (another area) × hand / axe / pickaxe
× table / furnace."""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, craft, nav  # noqa: E402
from bonobo.cost import walk_ticks  # noqa: E402
from bonobo.data import bare  # noqa: E402
from bonobo.game import TICKS_PER_S  # noqa: E402
from bonobo.knowledge import break_overhead, break_ticks, tool_for  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import FakeRegion, bag, cost, inventory, memory, slot, snapshot, state  # noqa: E402

TABLE = "minecraft:crafting_table"
FURNACE = "minecraft:furnace"
OVER = "minecraft:overworld"
POS = (12984, 74, 12997)            # the table
IRON = (12984 + 10, 69, 12997 + 11)  # the iron: ~15.7 blocks off, a floor below

TOOLS = {
    "hand": bag(inventory()),
    "axe": bag(inventory(slot("wooden_axe"))),
    "pickaxe": bag(inventory(slot("iron_pickaxe"))),
}

DISTANCES = {
    "here": (POS[0], POS[1], POS[2] + 2),
    "near": IRON,
    "far (another area)": (POS[0], POS[1], POS[2] + 200),
}


def rebuild_ticks(block, inv, extra_s=0.0):
    """The production cost of carrying a station on: break it with the best tool held, plus one place, plus any
    wait (a furnace still smelting)."""
    tool = tool_for(inv, bare(block))
    return break_ticks(bare(block), tool) + break_overhead() + round((nav.PLACE_S + extra_s) * TICKS_PER_S)


def flat_next_use(place):
    """A plain next use, no dig in the way: `_next_use_at`'s own (place, ticks) shape with the straight line as
    the (here, exact) ticks — these rows are about the price comparison, not the real reach price
    (NextUseWalksThePlanForward.test_buried_rows is)."""
    return (place, walk_ticks(math.dist(POS, place)))


class StationKeptPricesTheCheaperChoice(unittest.TestCase):
    """station_kept ⇔ the ticks back cost fewer than breaking, carrying and placing the station again — over every
    combination of station, tool held, and where the next use sits."""

    def test_rows(self):
        for block in (TABLE, FURNACE):
            for tool_name, inv in TOOLS.items():
                for dist_name, next_use in DISTANCES.items():
                    with self.subTest(block=block, tool=tool_name, next_use=dist_name):
                        expected = walk_ticks(math.dist(POS, next_use)) < rebuild_ticks(block, inv)
                        self.assertEqual(craft.station_kept(block, POS, flat_next_use(next_use), inv), expected,
                                         f"{block} {tool_name} {dist_name}: expected keep={expected}")

    def test_no_known_next_use_never_keeps(self):
        for block in (TABLE, FURNACE):
            self.assertFalse(craft.station_kept(block, POS, None, TOOLS["hand"]))

    def test_a_furnace_still_cooking_counts_toward_keeping_it(self):
        # the same gap in tool speed, with wait added on the carry side: cooking can only make keeping it cheaper
        near = flat_next_use(DISTANCES["near"])
        cold = craft.station_kept(FURNACE, POS, near, TOOLS["pickaxe"], extra_s=0.0)
        hot = craft.station_kept(FURNACE, POS, near, TOOLS["pickaxe"], extra_s=50.0)
        self.assertFalse(cold)        # a pickaxe breaks the furnace fast enough that carrying still pays cold
        self.assertTrue(hot)          # ...but not once the wait for it to finish is added to the carry side

    def test_keeps_the_table_for_the_iron_15_blocks_off(self):
        """The scene: the table at (12984,74,12997), the iron ~15.7 blocks off at y69 — a hand or a
        pickaxe walks back for it; an axe (the table's own fast tool) breaks it and carries it on instead. (Only
        station_kept's own pure price, same whatever block is asked — a table is never run through it any more,
        see OwnTableReusedIsAlwaysTakenBack.)"""
        self.assertAlmostEqual(math.dist(POS, IRON), 15.68, places=1)
        near = flat_next_use(IRON)
        self.assertTrue(craft.station_kept(TABLE, POS, near, TOOLS["hand"]))
        self.assertTrue(craft.station_kept(TABLE, POS, near, TOOLS["pickaxe"]))
        self.assertFalse(craft.station_kept(TABLE, POS, near, TOOLS["axe"]))


class NextUseWalksThePlanForward(unittest.TestCase):
    """next_table_use / next_furnace_use: (place, ticks) of the first later step that needs the station — the
    planned place for `place` (cost.site, carried forward past steps with none), cost.reach's own seconds home
    from it for `ticks` (test_buried_rows below); None past the plan's end."""

    class _FakeCost:
        """A cost stub: `site` answers from a {id(step): place} map; `feet`/`reach` are the fallback start and a
        free, always-reachable way home (these rows exercise the at-chain only, not the real reach price)."""

        def __init__(self, sites, feet=(0, 0, 0)):
            self.sites = sites
            self._feet = feet
            self.snap = type("S", (), {"inv": bag(inventory())})()

        def site(self, step):
            return self.sites.get(id(step))

        def feet(self):
            return self._feet

        def reach(self, cell, kind, at=None, spent=0, extra=0):
            return nav.Reached(tuple(at), None, 0, walk_ticks(math.dist(at, cell)) / TICKS_PER_S)

    def test_rows(self):
        gather = Step("gather", "minecraft:oak_log", 4)
        table_now = Step("craft", "minecraft:crafting_table", 1)
        stick = Step("craft", "minecraft:stick", 4)                       # 2×2: no table needed
        pickaxe = Step("craft", "minecraft:wooden_pickaxe", 1)            # 3×3: needs the table again
        smelt = Step("smelt", "minecraft:iron_ingot", 3)
        steps = [gather, table_now, stick, pickaxe, smelt]
        cost = self._FakeCost({id(pickaxe): (1, 2, 3), id(smelt): (4, 5, 6)})
        self.assertEqual(craft.next_table_use(cost, steps, table_now), ((1, 2, 3), walk_ticks(math.dist((1, 2, 3), (0, 0, 0)))))
        # the registry match (knowledge.step_station) is the smelt skill's own `station=`; stubbed here so the row
        # only exercises next_furnace_use's own at-chain, not the registry's key matching
        with mock.patch.object(craft._k, "step_station", lambda s: "minecraft:furnace" if s is smelt else None):
            self.assertEqual(craft.next_furnace_use(cost, steps, table_now),
                             ((4, 5, 6), walk_ticks(math.dist((4, 5, 6), (0, 0, 0)))))

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
            self.assertEqual(craft.next_furnace_use(cost, [table_now, smelt_a, smelt_b], smelt_a), ((5, 70, 5), 0))

    def test_carries_the_last_known_place_forward(self):
        """A craft step itself has no site (own_work's never a kinds-step): the place before it stands in."""
        table_now = Step("craft", "minecraft:crafting_table", 1)
        mine = Step("mine", "minecraft:iron_ore", 1, {"blocks": ["iron_ore"]})
        pickaxe = Step("craft", "minecraft:wooden_pickaxe", 1)
        steps = [table_now, mine, pickaxe]
        cost = self._FakeCost({id(mine): (9, 9, 9)})      # pickaxe's own site is unknown: mine's carries forward
        self.assertEqual(craft.next_table_use(cost, steps, table_now),
                         ((9, 9, 9), walk_ticks(math.dist((9, 9, 9), (0, 0, 0)))))

    def _buried_next_use(self, depth):
        """A furnace at the surface, the next smelt's ore (between two smelts) `depth` blocks straight down in
        solid stone: next_furnace_use's own (place, ticks) for it."""
        lo, hi = (-5, 50, -5), (5, 75, 5)
        blocks = {(x, y, z): "stone" for x in range(lo[0], hi[0] + 1) for z in range(lo[2], hi[2] + 1)
                 for y in range(51, 70)}
        region = FakeRegion(lo, hi, blocks)
        furnace_pos, buried = (0, 70, 0), (0, 70 - depth, 0)
        m = memory()
        m.note_seen("iron_ore", buried, OVER)
        snap = snapshot(state(x=furnace_pos[0] + 0.5, y=float(furnace_pos[1]), z=furnace_pos[2] + 0.5), region=region)
        c = cost(snap, mem=m)
        smelt_now = Step("smelt", "minecraft:iron_ingot", 1)
        mine = Step("mine", "minecraft:raw_iron", 1, {"blocks": ["iron_ore", "deepslate_iron_ore"]})
        smelt_again = Step("smelt", "minecraft:iron_ingot", 1)
        steps = [smelt_now, mine, smelt_again]
        with mock.patch.object(craft._k, "step_station", lambda s: FURNACE if s is smelt_again else None):
            return furnace_pos, craft.next_furnace_use(c, steps, smelt_now)

    # (situation, depth, the ticks cost.reach's own seconds give — the hand-rolled "stone"-always dig guess gave a
    # different number at both: 1586 (not 1419) at 5, a finite 2060 (not inf: no way within the real run's own
    # tries) at 6)
    BURIED_ROWS = [("reachable: the real seconds, not the hand-rolled dig guess", 5, 1419),
                   ("past the run's own tries: no way, so unaffordable, not a cheap guess", 6, math.inf)]

    def test_buried_rows(self):
        for name, depth, want in self.BURIED_ROWS:
            with self.subTest(name):
                _, next_use = self._buried_next_use(depth)
                self.assertEqual(next_use[1], want, f"must fail on base: {next_use}")


class OwnTableReusedIsAlwaysTakenBack(unittest.TestCase):
    """C: a crafting table that is ours — just placed, or already standing where memory (state["own_table"]) says
    we put it before — is always taken back when the sitting is done (craft.take_table); station_kept is never
    asked (a table left standing was yesterday's bug: stuck on the ground while the body went down to mine)."""

    def test_own_standing_table_taken_back(self):
        pos = (3, 70, -2)
        inv = bag(inventory(("cobblestone", 8)))
        plan_state = {"inv": inv, "table": pos, "spot": None, "next_use": None, "own_table": True}
        tasks = craft.craft_commands(plan_state, ([("minecraft:furnace", 1)],))
        self.assertTrue(any(t.get("type") == "mine" and (t["x"], t["y"], t["z"]) == pos for t in tasks),
                        f"must fail on base: an own standing table is never taken back there: {tasks}")

    def test_a_table_nobody_here_placed_is_left_alone(self):
        pos = (3, 70, -2)
        inv = bag(inventory(("cobblestone", 8)))
        plan_state = {"inv": inv, "table": pos, "spot": None, "next_use": None}   # own_table unset: not ours
        tasks = craft.craft_commands(plan_state, ([("minecraft:furnace", 1)],))
        self.assertFalse(any(t.get("type") == "mine" for t in tasks), tasks)


class NonPricedStationsAlwaysStayStanding(unittest.TestCase):
    """Station.__exit__'s one decision for whether a placed station stays: priced only for a furnace
    (station_kept); every other station Station ever places (brewing stand, enchanting table, anvil: brewing.py,
    ui.py) stays standing regardless of next_use — built once, reused, never broken on a maybe."""

    def _exit_tears_down(self, block):
        """Did Station.__exit__ try to break `block` back (mine_cell reached) with no known next use and a
        pickaxe held (so `takes_back` alone can never be why it was left)?"""
        station = craft.Station.__new__(craft.Station)
        station.ctx = type("Ctx", (), {"policy": None, "mem": None})()
        station.block, station.pos, station.placed, station.next_use = block, POS, True, None
        torn = []

        def _tear(*a, **k):
            torn.append(True)
            raise api.Interrupted("test: stop right after the take-back is reached")

        with mock.patch.object(craft.api, "post"), mock.patch.object(craft, "Inventory", lambda: TOOLS["pickaxe"]), \
             mock.patch.object(craft, "mine_cell", _tear), mock.patch.object(craft, "_furnace_wait_s", lambda: 0.0):
            try:
                station.__exit__(None, None, None)
            except api.Interrupted:
                pass
        return bool(torn)

    def test_a_non_priced_station_with_no_next_use_is_never_torn_down(self):
        for block in ("minecraft:enchanting_table", "minecraft:brewing_stand", "minecraft:anvil"):
            with self.subTest(block=block):
                self.assertFalse(self._exit_tears_down(block),
                                 f"must fail on base: {block}, no next use, was torn down instead of left standing")


if __name__ == "__main__":
    unittest.main()

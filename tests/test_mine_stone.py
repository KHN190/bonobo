"""Stone under the soil (free run 23:32: nearest buried stone, a vertical dig priced as a walk): an open face first; a
buried one by a straight shaft only with the blocks to climb back out and nothing hot below, its overburden priced."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import cost as costmod, gather, world  # noqa: E402
from bonobo.data import SOIL_DEPTH, STAIR_CELLS  # noqa: E402
from bonobo.knowledge import FIND_AT, MINE, break_overhead  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import FakeRegion, bag, inventory, memory, state  # noqa: E402
from bonobo.skillcore import Ban  # noqa: E402

FEET = (0, 64, 0)
SOIL = 3                    # blocks of soil over the stone in every scene


def ground(extra=None):
    """Stone below SOIL blocks of dirt under the feet, over a wide patch."""
    x0, y0, z0 = FEET
    blocks = {}
    for x in range(-2, 3):
        for z in range(-2, 3):
            for y in range(y0 - SOIL - 6, y0):
                blocks[(x, y, z)] = "dirt" if y >= y0 - SOIL else "stone"
    blocks.update(extra or {})
    return FakeRegion((-2, FEET[1] - SOIL - 8, -2), (2, FEET[1] + 2, 2), blocks)


class Shaft(unittest.TestCase):
    def test_rows(self):
        stone = (FEET[0] + 2, FEET[1] - SOIL - 1, FEET[2])       # the nearest stone, a column over
        depth = FEET[1] - stone[1]
        lava_side = {(1, stone[1], 0): "lava"}
        cave = {(0, y, 0): "air" for y in range(stone[1] - 2, stone[1])}
        # (scene, blocks carried) → dug?
        from bonobo.game import JUMP_BLOCKS
        under = (FEET[0], FEET[1] - 1, FEET[2])
        deep = (FEET[0], FEET[1] - depth, FEET[2])

        def glass():
            """The same depth of glass, the dug cell too (nothing it digs places back), over stone."""
            blocks = {(0, FEET[1] - i, 0): "glass" for i in range(1, depth + 1)}
            blocks.update({(x, FEET[1] - depth - k, z): "stone" for x in range(-2, 3) for z in range(-2, 3) for k in range(1, 3)})
            return FakeRegion((-2, FEET[1] - depth - 3, -2), (2, FEET[1] + 2, 2), blocks)
        floor = FakeRegion((-2, FEET[1] - 4, -2), (2, FEET[1] + 2, 2),
                           {(x, y, z): "stone" for x in range(-2, 3) for z in range(-2, 3) for y in range(FEET[1] - 3, FEET[1])})
        # (scene, the stone dug to, blocks carried) → dug?
        rows = [("soil over stone, blocks to climb out (the last block jumped): dug", ground(), stone,
                 depth - JUMP_BLOCKS, True),
                ("the shaft's own soil and stone pillar it out: nothing carried", ground(), stone, 0, True),
                ("must fail: one block short of pillaring out (a shaft through what places nothing)", glass(), deep,
                 depth - JUMP_BLOCKS - 1, False),
                ("must fail (mine_stone__base): two down in a stone floor, nothing carried: the first stone dug pillars out",
                 floor, (FEET[0], FEET[1] - 2, FEET[2]), 0, True),
                ("must fail (mine_stone__base): the stone under the feet, nothing carried: one block down is jumped out of",
                 floor, under, 0, True),
                ("lava beside the shaft", ground(lava_side), stone, depth, False),
                ("a cave under the shaft's foot", ground(cave), stone, depth, False)]

        def sand_shaft(d):
            """d cells of sand (places nothing: not GROUPS['building']) over solid stone — isolates nav.place_budget
            (the one exit budget strip_mine_step's dig_down shares) from what the shaft itself digs."""
            blocks = {(0, FEET[1] - i, 0): "sand" for i in range(1, d + 1)}
            blocks.update({(x, FEET[1] - d - k, z): "stone" for x in range(-2, 3) for z in range(-2, 3) for k in (1, 2)})
            return FakeRegion((-2, FEET[1] - d - 3, -2), (2, FEET[1] + 2, 2), blocks)

        # the exit budget alone (dug=0 throughout): refused exactly when nav.place_budget(carried) < d - JUMP_BLOCKS
        for carried in (0, 8, 40):
            for d in (4, 12):
                want = gather.nav.place_budget(carried) >= d - JUMP_BLOCKS
                rows.append((f"sand shaft (nav.place_budget, the budget strip_mine_step's gate shares): "
                            f"{carried} carried, depth {d}", sand_shaft(d), (0, FEET[1] - d, 0), carried, want))
        for name, region, target, carried, dug in rows:
            with self.subTest(name):
                tasks, why = gather.shaft_plan(region, FEET, target, carried)
                self.assertEqual(tasks is not None, dug, why)
                if dug:
                    self.assertEqual(len([t for t in tasks if t.get("type") != "wait"]), FEET[1] - target[1])


class AnOpenFaceIsWorkedWhereItStands(unittest.TestCase):
    """mine_stone__base (a stone floor): the floor's own top faces are open and in reach, yet the nearest vein cell by
    distance was one buried under the body's supports — a shaft dug, its stone spent pillaring out. The pass goes for
    an open face in reach first (gather.approach_cell)."""

    def test_rows(self):
        x, y, z = FEET
        buried = (x + 1, y - 2, z)              # under a support cell: nearer, no open face
        top = (x + 3, y - 1, z)                 # the floor's top, its face open, in reach
        far_top = (x + 9, y - 1, z)             # open, out of reach
        rows = [("must fail: an open face in reach beats a nearer buried cell", {buried, top}, {top}, top),
                ("no open face in reach: the nearest (a shaft or a tunnel)", {buried, far_top}, {far_top}, buried),
                ("nothing open: the nearest", {buried, top}, set(), buried)]
        for name, vein, open_set, want in rows:
            with self.subTest(name):
                self.assertEqual(gather.approach_cell(vein, open_set, FEET), want)


class OnePassTakesWhatItWants(unittest.TestCase):
    """mine_stone__base: a pass of 3 took the 3 nearest cells — two buried under the supports — and sent the one open
    among them: one block a chain, re-planned between. A pass takes its count from the open faces in reach first."""

    def test_rows(self):
        x, y, z = FEET
        buried = {(x + 1, y - 2, z), (x - 1, y - 2, z)}
        tops = {(x + 2, y - 1, z), (x - 2, y - 1, z), (x, y - 1, z + 2), (x, y - 1, z - 2)}
        rows = [("must fail: three open faces in reach, not the nearer buried", buried | tops, tops, 3, 3),
                ("one open face: it, then the nearest buried", buried | {(x + 2, y - 1, z)}, {(x + 2, y - 1, z)}, 3, 1)]
        for name, vein, open_set, n, want_open in rows:
            with self.subTest(name):
                got = gather.pass_cells(vein, open_set, FEET, n)
                self.assertEqual(len(got), n)
                self.assertEqual(sum(1 for c in got if c in open_set), want_open)


class NoShaftIsNoBan(unittest.TestCase):
    """mine_stone__base: a shaft that cannot be dug from here banned every cell of the connected vein (88 stone), and the
    next pass found nothing; the way in failed, not the vein — no ban, the next pass takes another way."""

    def test_rows(self):
        from bonobo import api, nav
        from bonobo.skillcore import Context
        cells = [(x, FEET[1] - SOIL - 1, 0) for x in range(-2, 3)]      # the stone under the soil
        region = ground()
        ctx = Context(None, nav.Policy(allow_dig=True), "minecraft:overworld", {})
        hits = [{"x": c[0], "y": c[1], "z": c[2], "block": "minecraft:stone", "distance": 3.0} for c in cells]
        shaft = mock.Mock(return_value=(None, "the shaft refused"))
        with mock.patch.object(gather, "Inventory", lambda: bag(inventory(("wooden_pickaxe", 1)))), \
                mock.patch.object(gather, "require_pickaxe", lambda *a, **k: None), \
                mock.patch.object(gather, "find", lambda blocks, radius=32, limit=50, exposed=False: [] if exposed else hits), \
                mock.patch.object(gather, "feet", lambda: FEET), \
                mock.patch.object(api, "get", lambda path, *a, **k: state()), \
                mock.patch.object(gather, "region_around", lambda *a, **k: region), \
                mock.patch.object(gather.nav, "dig_down_region", lambda *a, **k: region), \
                mock.patch.object(gather, "shaft_plan", shaft), \
                mock.patch.object(api, "detail", lambda *a: None):
            run = gather.mine.__wrapped__(ctx, "minecraft:cobblestone", 3, ["stone"], 0)
            next(run)                   # the first pass's top
            next(run)                   # its shaft refused; the next pass's top
        self.assertTrue(shaft.called)                   # the scene reached the shaft
        self.assertEqual(dict(ctx.blacklist), {})       # must fail: the whole vein banned over one refused shaft


class NoWayBansTheCell(unittest.TestCase):
    """accept_fresh_iron_pickaxe: "no way to the coal_ore vein" banned all 18 cells of it; the cell gone for is
    banned, the next pass goes for another."""

    def test_rows(self):
        from bonobo import api, nav
        from bonobo.skillcore import Context
        cells = [(1, FEET[1], 0), (2, FEET[1], 0)]
        region = ground({c: "coal_ore" for c in cells})
        ctx = Context(None, nav.Policy(allow_dig=True), "minecraft:overworld", {})
        hits = [{"x": c[0], "y": c[1], "z": c[2], "block": "minecraft:coal_ore", "distance": float(c[0])} for c in cells]
        with mock.patch.object(gather, "Inventory", lambda: bag(inventory(("wooden_pickaxe", 1)))), \
                mock.patch.object(gather, "require_pickaxe", lambda *a, **k: None), \
                mock.patch.object(gather, "find", lambda blocks, radius=32, limit=50, exposed=False: [] if exposed else hits), \
                mock.patch.object(gather, "feet", lambda: FEET), \
                mock.patch.object(api, "get", lambda path, *a, **k: state()), \
                mock.patch.object(gather, "region_around", lambda *a, **k: region), \
                mock.patch.object(nav, "mod_features", lambda: {"travel"}), \
                mock.patch.object(nav, "arrived_near", lambda *a, **k: False), \
                mock.patch.object(nav, "way_to", lambda *a, **k: False), \
                mock.patch.object(api, "detail", lambda *a: None):
            run = gather.mine.__wrapped__(ctx, "minecraft:coal", 2, ["coal_ore"], 0)
            next(run)                   # the first pass's top
            next(run)                   # no way to its cell; the next pass's top
        # must fail: every cell of the vein banned over the one no way reached
        self.assertEqual(len(ctx.blacklist), 1)


class StripMineSharesShaftBudget(unittest.TestCase):
    """gather.strip_mine_step gates its own nav.dig_down with shaft_plan — the one exit budget (nav.place_budget)
    the ore shaft shares: nav.dig_down runs only when the gate passes; refused, the dry-ground fallback runs
    instead, never a silent dig (S1: nav.dig_down itself still runs the chain, with before_segment)."""

    def test_rows(self):
        from bonobo import api, nav
        from bonobo.skillcore import Context
        ctx = Context(None, nav.Policy(), "minecraft:overworld", {})
        s = {"blockX": 0, "blockY": 80, "blockZ": 0}
        dry = [{"x": 20, "y": 80, "z": 0, "block": "minecraft:stone", "distance": 20.0}]
        # (name, shaft_plan's verdict) -> dig_down called?
        rows = [("the gate passes: dig_down runs", (["a task"], None), True),
                ("must fail: the gate refuses (no exit budget): dig_down never runs, dry ground instead",
                 (None, "0 of 0 carried spendable, 0 dug on the way, 11 to pillar back out"), False)]
        for name, verdict, want_dig in rows:
            with self.subTest(name):
                dig_down = mock.Mock()
                with mock.patch.object(gather, "Inventory", lambda: bag(inventory(("wooden_pickaxe", 1)))), \
                        mock.patch.object(api, "get", lambda path, *a, **k: s), \
                        mock.patch.object(gather, "shaft_plan", lambda *a, **k: verdict), \
                        mock.patch.object(nav, "dig_down_region", lambda *a, **k: None), \
                        mock.patch.object(nav, "dig_down", dig_down), \
                        mock.patch.object(gather, "find", lambda *a, **k: dry), \
                        mock.patch.object(nav, "arrived_near", lambda *a, **k: True):
                    gather.strip_mine_step.__wrapped__(ctx, 16)
                self.assertEqual(dig_down.called, want_dig)


PICK = {"id": "minecraft:diamond_pickaxe", "count": 1, "damage": 0, "maxDamage": 1561}


class SoilDepth(unittest.TestCase):
    """knowledge.soil_depth: the soil under the feet as read (shovel blocks down to the rock); the prior SOIL_DEPTH only
    where the column is not read through — and the staircase's soil steps follow it (cost.dug_way)."""

    def test_rows(self):
        from bonobo.knowledge import soil_depth

        def column(names, bottom=None):
            lo = FEET[1] - len(names) if bottom is None else bottom
            blocks = {(0, FEET[1] - 1 - i, 0): n for i, n in enumerate(names) if n != "air"}
            return FakeRegion((-1, lo, -1), (1, FEET[1] + 2, 1), blocks)
        # (situation, region) → soil cells
        rows = [("not read: the prior", None, SOIL_DEPTH),
                ("must fail: grass over two dirt, then stone: three, not the prior",
                 column(["grass_block", "dirt", "dirt", "stone"]), 3),
                ("six dirt over stone: six", column(["dirt"] * 6 + ["stone"]), 6),
                ("two dirt, the read ends: at least the prior", column(["dirt", "dirt"]), max(2, SOIL_DEPTH)),
                ("a hole under the feet: none", column(["air", "dirt", "stone"]), 0)]
        for name, region, want in rows:
            with self.subTest(name):
                self.assertEqual(soil_depth(region, FEET), want)

    def test_the_staircase_follows_it(self):
        deep = FEET[1] - 10
        steps = FEET[1] - deep - 1
        for soil in (1, SOIL_DEPTH):
            with self.subTest(soil):
                got = costmod.dug_way(FEET, (FEET[0] + 1, deep, FEET[2]), "iron_ore", soil, True,
                                      bag(inventory(("stone_pickaxe", 1))))
                # must fail: the soil steps fixed whatever was read
                self.assertEqual(got.count("dirt"), min(soil, steps) * STAIR_CELLS)


class Overburden(unittest.TestCase):
    """Cost.dig_to: the digging to the nearest in sight — the way nav.plan_way takes there over the expected ground
    (cost.dug_way, decision R5-3) — each break by the tool held for it (knowledge.break_ticks: dirt 15 ticks by hand,
    stone 150 by hand, 6 with a diamond pickaxe)."""

    def priced(self, token, y, inv, ban=False):
        blocks = MINE[token][0]
        cell = (FEET[0] + 2, y, FEET[2])
        hit = {"x": cell[0], "y": y, "z": cell[2], "distance": 5.0, "block": blocks[0]}
        snap = world.Snapshot.from_readings(state(x=FEET[0] + 0.5, y=float(FEET[1]), z=FEET[2] + 0.5), inv,
                                            {world.bare(blocks[0]): [hit]})
        c = costmod.Cost(snap, memory(), blacklist={cell: Ban(time.time() + 60)} if ban else None)
        step = Step("mine", token, 1, {"blocks": blocks})
        return c._dug(step), c.dig_to(step)

    def test_rows(self):
        stone_tok = next(t for t, (b, _tier) in MINE.items() if "stone" in b and FIND_AT.get(t) is None)
        ore_tok = next(t for t in MINE if FIND_AT.get(t) is not None)
        floor, deep = FEET[1] - 1, FEET[1] - 20
        hand, pick = bag(inventory()), bag(inventory(PICK))
        # (situation, token, the nearest seen's y, held, banned?) → ticks: each dug cell by the tool held for it
        rows = [("stone under the soil: the soil dug by hand", stone_tok, floor - SOIL, hand, False, {"dirt": 15}),
                ("stone at the floor: nothing over it", stone_tok, floor, hand, False, None),
                ("an ore 20 below by hand: its staircase", ore_tok, deep, hand, False, {"dirt": 15, "stone": 150}),
                ("an ore 20 below, a diamond pickaxe", ore_tok, deep, pick, False, {"dirt": 15, "stone": 6}),
                ("must fail: a banned (unreachable) stone is not priced", stone_tok, floor - SOIL, hand, True, None)]
        for name, token, y, inv, ban, per in rows:
            with self.subTest(name):
                dug, ticks = self.priced(token, y, inv, ban)
                # each break a mine task: the game's break, its cooldown and the task's own (knowledge.break_overhead)
                self.assertEqual(ticks, sum(per[n] + break_overhead() for n in dug) if per else 0)
                self.assertEqual(bool(dug), per is not None)

    def test_the_staircase_brings_the_soil_first(self):
        ore_tok = next(t for t in MINE if FIND_AT.get(t) is not None)
        dug, _ticks = self.priced(ore_tok, FEET[1] - 20, bag(inventory()))
        # must fail: a straight drop (the column under the feet: no staircase)
        self.assertEqual((dug.count("dirt"), dug.count("stone") >= (20 - 1 - SOIL_DEPTH) * STAIR_CELLS),
                         (SOIL_DEPTH * STAIR_CELLS, True))


class CheapestSeed(unittest.TestCase):
    """gather._cheapest_seed: prioritize accessible exposed veins over deep buried ones."""

    def test_exposed_vein_preferred_over_deep_buried_vein(self):
        from bonobo import nav, skillcore
        ctx = skillcore.Context(memory(), nav.Policy(), "minecraft:overworld")
        start = (0, 64, 0)
        exposed_cell = (25, 64, 0)
        deep_buried_cell = (0, 44, 0)  # 20 blocks down through rock
        hits = [{"x": exposed_cell[0], "y": exposed_cell[1], "z": exposed_cell[2]},
                {"x": deep_buried_cell[0], "y": deep_buried_cell[1], "z": deep_buried_cell[2]}]
        open_set = {exposed_cell}

        calls = []

        def mock_reach(region, s, target, mode, inv, protected, walks=None):
            calls.append(target)
            if target == exposed_cell:
                return nav.Reached(stand=(24, 64, 0), why=None, seconds=6.0)
            return nav.Reached(stand=(0, 44, 0), why=None, seconds=35.0)

        with mock.patch.object(gather, "Inventory", lambda: bag(inventory())), \
                mock.patch.object(gather, "region_around", lambda *a, **k: mock.Mock()), \
                mock.patch.object(gather.nav, "reach", mock_reach):
            picked = gather._cheapest_seed(ctx, hits, start, open_set)

        self.assertEqual(picked, exposed_cell)
        self.assertEqual(calls, [exposed_cell])

    def test_shallow_buried_vein_preferred_over_far_exposed_vein_when_cheaper(self):
        from bonobo import nav, skillcore
        ctx = skillcore.Context(memory(), nav.Policy(), "minecraft:overworld")
        start = (0, 64, 0)
        far_exposed_cell = (45, 64, 0)
        shallow_buried_cell = (1, 63, 0)  # 1 block below feet
        hits = [{"x": far_exposed_cell[0], "y": far_exposed_cell[1], "z": far_exposed_cell[2]},
                {"x": shallow_buried_cell[0], "y": shallow_buried_cell[1], "z": shallow_buried_cell[2]}]
        open_set = {far_exposed_cell}

        calls = []

        def mock_reach(region, s, target, mode, inv, protected, walks=None):
            calls.append(target)
            if target == shallow_buried_cell:
                return nav.Reached(stand=(0, 64, 0), why=None, seconds=2.5)
            return nav.Reached(stand=(44, 64, 0), why=None, seconds=10.5)

        with mock.patch.object(gather, "Inventory", lambda: bag(inventory())), \
                mock.patch.object(gather, "region_around", lambda *a, **k: mock.Mock()), \
                mock.patch.object(gather.nav, "reach", mock_reach):
            picked = gather._cheapest_seed(ctx, hits, start, open_set)

        self.assertEqual(picked, shallow_buried_cell)
        self.assertEqual(calls, [shallow_buried_cell])


if __name__ == "__main__":
    unittest.main()

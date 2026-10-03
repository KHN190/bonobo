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
from bonobo.knowledge import FIND_AT, MINE  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import FakeRegion, bag, inventory, memory, state  # noqa: E402

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
        floor = FakeRegion((-2, FEET[1] - 4, -2), (2, FEET[1] + 2, 2),
                           {(x, y, z): "stone" for x in range(-2, 3) for z in range(-2, 3) for y in range(FEET[1] - 3, FEET[1])})
        # (scene, the stone dug to, blocks carried) → dug?
        rows = [("soil over stone, blocks to climb out (the last block jumped): dug", ground(), stone,
                 depth - JUMP_BLOCKS, True),
                ("must fail: one block short of pillaring out", ground(), stone, depth - JUMP_BLOCKS - 1, False),
                ("must fail (mine_stone__base): the stone under the feet, nothing carried: one block down is jumped out of",
                 floor, under, 0, True),
                ("lava beside the shaft", ground(lava_side), stone, depth, False),
                ("a cave under the shaft's foot", ground(cave), stone, depth, False)]
        for name, region, target, carried, dug in rows:
            with self.subTest(name):
                tasks, why = gather.shaft_plan(region, FEET, target, carried)
                self.assertEqual(tasks is not None, dug, why)
                if dug:
                    self.assertEqual(len([t for t in tasks if t.get("type") != "wait"]), FEET[1] - target[1])


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
        c = costmod.Cost(snap, memory(), blacklist={cell: time.time() + 60} if ban else None)
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
                self.assertEqual(ticks, sum(per[n] for n in dug) if per else 0)
                self.assertEqual(bool(dug), per is not None)

    def test_the_staircase_brings_the_soil_first(self):
        ore_tok = next(t for t in MINE if FIND_AT.get(t) is not None)
        dug, _ticks = self.priced(ore_tok, FEET[1] - 20, bag(inventory()))
        # must fail: a straight drop (the column under the feet: no staircase)
        self.assertEqual((dug.count("dirt"), dug.count("stone") >= (20 - 1 - SOIL_DEPTH) * STAIR_CELLS),
                         (SOIL_DEPTH * STAIR_CELLS, True))


if __name__ == "__main__":
    unittest.main()

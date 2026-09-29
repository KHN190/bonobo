"""Stone under the soil (free run 23:32: nearest buried stone, a vertical dig priced as a walk): an open face first; a
buried one by a straight shaft only with the blocks to climb back out and nothing hot below, its overburden priced."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import cost as costmod, gather, world  # noqa: E402
from bonobo.knowledge import DIG_HAND_S, FIND_AT, MINE  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

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
        rows = [("soil over stone, blocks to climb out: dug", ground(), depth, True),
                ("must fail: no blocks to pillar back out", ground(), depth - 1, False),
                ("lava beside the shaft", ground(lava_side), depth, False),
                ("a cave under the shaft's foot", ground(cave), depth, False)]
        for name, region, carried, dug in rows:
            with self.subTest(name):
                tasks, why = gather.shaft_plan(region, FEET, stone, carried)
                self.assertEqual(tasks is not None, dug, why)
                if dug:
                    self.assertEqual(len([t for t in tasks if t.get("type") != "wait"]), depth)


class Overburden(unittest.TestCase):
    def test_rows(self):
        stone_tok = next(t for t, (b, _tier) in MINE.items() if "stone" in b and FIND_AT.get(t) is None)
        ore_tok = next(t for t in MINE if FIND_AT.get(t) is not None)
        c = costmod.Cost(None)
        c.snap = type("Snap", (), {"feet": FEET})()
        floor = FEET[1] - 1
        # (token, the nearest seen's y) → ticks of digging priced
        rows = [("stone under the soil: the soil dug", stone_tok, floor - SOIL,
                 round(SOIL * DIG_HAND_S * costmod.TICKS_PER_S)),
                ("stone at the floor: nothing over it", stone_tok, floor, 0),
                ("an ore's depth is its staircase's, not overburden", ore_tok, floor - SOIL, 0)]
        for name, token, y, want in rows:
            with self.subTest(name):
                blocks = MINE[token][0]
                step = Step("mine", token, 1, {"blocks": blocks})
                with mock.patch.dict(world._SIGHT, {"near": {blocks[0]: 5.0}, "y": {blocks[0]: y}}):
                    got = c._overburden_ticks(step)
                self.assertEqual(got, want)
        # must fail: a buried stone priced as one lying on the ground
        with mock.patch.dict(world._SIGHT, {"near": {"stone": 5.0}, "y": {"stone": floor - SOIL}}):
            self.assertGreater(c._overburden_ticks(Step("mine", stone_tok, 1, {"blocks": MINE[stone_tok][0]})), 0)


if __name__ == "__main__":
    unittest.main()

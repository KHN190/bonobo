"""perception.ground / perceived: the field over the blocks read around us, and the kit merged whatever the ground
does. A missing field.from_region raised inside one try with the kit: sword 0 with an iron sword in hand (evade,
not fight), and no region for evade's footing (walked off the sky platform)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import field, perception  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

HERE = {"x": 0.5, "y": 64.0, "z": 0.5}


def floor(xs, zs, y=63, name="stone"):
    return {(x, y, z): name for x in xs for z in zs}


R = range(-8, 9)


def fresh():
    perception.GRID, perception.GRID_AT, perception.GRID_AT_POS, perception.REGION = None, 0.0, None, None


class Ground(unittest.TestCase):
    ROWS = [  # (why, blocks) → the field's bucket
        ("flat stone all round", floor(R, R), "open"),
        ("the edge of a sky platform", floor(range(-8, 2), R), "open"),
        ("in a pool of water", {**floor(R, R, 62), **floor(R, R, 63, "water"), **floor(R, R, 64, "water")}, "open"),
        ("must fail: no blocks read as enclosed — nothing at all: empty air", {}, "open"),
        ("under a roof", {**floor(R, R), **floor(R, R, 68)}, "underground"),
        ("walled in, a block overhead", {**floor(R, R), (1, 64, 0): "stone", (-1, 64, 0): "stone",
                                         (0, 64, 1): "stone", (0, 64, -1): "stone", (1, 65, 0): "stone",
                                         (-1, 65, 0): "stone", (0, 65, 1): "stone", (0, 65, -1): "stone",
                                         (0, 66, 0): "stone"}, "enclosed"),
    ]

    def test_a_field_over_the_blocks(self):
        for why, blocks, bucket in self.ROWS:
            with self.subTest(why):
                fresh()
                got = perception.ground(HERE, now=0.0, region_of=lambda lo, hi: FakeRegion(lo, hi, blocks))
                self.assertEqual((type(got), got.bucket, got.blocks), (field.Field, bucket, 0))
                self.assertEqual((perception.REGION.lo, perception.REGION.hi, perception.REGION.blocks),
                                 ((-8, 56, -8), (8, 72, 8), blocks), "the region read is kept for evade's footing")


class Perceived(unittest.TestCase):
    def test_the_kit_does_not_wait_on_the_ground(self):
        def boom(_state):
            raise AttributeError("module 'bonobo.field' has no attribute 'from_region'")
        kit = {"sword_tier": 2, "armor": 7}
        flat = floor(R, R)
        rows = [  # (why, ground read, kit read) → (sword tier, the field's bucket or None)
            ("both read", lambda st: perception.ground(st, now=0.0, region_of=lambda lo, hi: FakeRegion(lo, hi, flat)),
             lambda st: kit, (2, "open")),
            ("the ground raises: the kit is still merged", boom, lambda st: kit, (2, None)),
            ("the kit raises: the ground is still read",
             lambda st: perception.ground(st, now=0.0, region_of=lambda lo, hi: FakeRegion(lo, hi, flat)), boom,
             (None, "open")),
            ("must fail: both raise: the state as it came", boom, boom, (None, None)),
        ]
        for why, ground_of, kit_of, want in rows:
            with self.subTest(why):
                fresh()
                got = perception.perceived(dict(HERE), 0.0, ground_of=ground_of, kit_of=kit_of)
                ground = got.get("field")
                self.assertEqual((got.get("sword_tier"), ground.bucket if ground is not None else None), want)


if __name__ == "__main__":
    unittest.main()

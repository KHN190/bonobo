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
    perception.STATE.grid, perception.STATE.grid_at, perception.STATE.grid_at_pos, perception.STATE.region = None, 0.0, None, None
    perception.STATE.ground.clear()


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
                got = perception.field_around(HERE, now=0.0, region_of=lambda lo, hi: FakeRegion(lo, hi, blocks))
                # plugs: the blocks already in our passage, per side (a pod walls it both ways: 2 each side)
                walled = 2 if bucket == "enclosed" else 0
                self.assertEqual((type(got), got.bucket, got.blocks, max(got.plugs.values(), default=0)),
                                 (field.Field, bucket, 0, walled))
                self.assertEqual((perception.STATE.region.lo, perception.STATE.region.hi, perception.STATE.region.blocks),
                                 ((-8, 56, -8), (8, 72, 8), blocks), "the region read is kept for evade's footing")


class Seal(unittest.TestCase):
    """A 1-wide passage (walled both sides, feet and head) is sealed by two blocks: the field says so, and pricing
    reads a walker's arrival as never once they are in (threat.reshape_options)."""
    ROWS = [  # (why, blocks) → blocks that seal the way we stand in
        ("walls east and west: a passage running north", {**floor(R, R), **{(x, y, 0): "stone" for x in (1, -1)
                                                                              for y in (64, 65)}}, 2),
        ("walls north and south: a passage running east", {**floor(R, R), **{(0, y, z): "stone" for z in (1, -1)
                                                                               for y in (64, 65)}}, 2),
        ("flat stone all round: nothing we carry seals it", floor(R, R), None),
        ("must fail: walls at the feet only — a 1-high sill, not a passage", {**floor(R, R), (1, 64, 0): "stone",
                                                                               (-1, 64, 0): "stone"}, None),
    ]

    def test_the_seal_over_the_blocks(self):
        for why, blocks, seal in self.ROWS:
            with self.subTest(why):
                fresh()
                got = perception.field_around(HERE, now=0.0, region_of=lambda lo, hi: FakeRegion(lo, hi, blocks))
                self.assertEqual(got.seal, seal)


class Perceived(unittest.TestCase):
    def test_the_kit_does_not_wait_on_the_ground(self):
        def boom(_state):
            raise AttributeError("a reader failed (injected)")
        kit = {"sword": "minecraft:iron_sword", "armor": 7}
        flat = floor(R, R)
        rows = [  # (why, ground read, kit read) → (the sword item, the field's bucket or None)
            ("both read", lambda st: perception.field_around(st, now=0.0, region_of=lambda lo, hi: FakeRegion(lo, hi, flat)),
             lambda st: kit, ("minecraft:iron_sword", "open")),
            ("the ground raises: the kit is still merged", boom, lambda st: kit, ("minecraft:iron_sword", None)),
            ("the kit raises: the ground is still read",
             lambda st: perception.field_around(st, now=0.0, region_of=lambda lo, hi: FakeRegion(lo, hi, flat)), boom,
             (None, "open")),
            ("must fail: both raise: the state as it came", boom, boom, (None, None)),
        ]
        from unittest import mock
        from bonobo import api
        for why, ground_of, kit_of, want in rows:
            with self.subTest(why), mock.patch.object(api, "log"):      # the injected failure's own line, not noise
                fresh()
                got = perception.perceived(dict(HERE), 0.0, ground_of=ground_of, kit_of=kit_of)
                ground = got.get("field")
                self.assertEqual((got.get("sword"), ground.bucket if ground is not None else None), want)


if __name__ == "__main__":
    unittest.main()

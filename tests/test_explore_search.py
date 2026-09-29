"""A search walks and never digs (explore__white_bed shafts, 23:46–23:53): its leg ends on the open-sky cell over the
land it aims at, not inside it; only a search for what lies underground (an ore's band) digs."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import explore  # noqa: E402
from bonobo.knowledge import FIND_AT, MINE  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

GROUND = 100          # the surface's standing y in every scene
TOP = GROUND + explore.LOOK_BLOCKS
BOTTOM = GROUND - explore.LOOK_BLOCKS


def column(*layers):
    """{(0, y, 0): name} for (name, from_y, to_y) layers."""
    return {(0, y, 0): n for n, a, b in layers for y in range(a, b + 1)}


class SurfaceCell(unittest.TestCase):
    def test_rows(self):
        soil = [("stone", BOTTOM, GROUND - 5), ("dirt", GROUND - 4, GROUND - 2), ("grass_block", GROUND - 1, GROUND - 1)]
        # (scene, blocks) → the standing y
        rows = [("open ground", column(*soil), GROUND),
                ("a cave under the ground: its floor is no surface", column(*soil, ("air", GROUND - 20, GROUND - 17)),
                 GROUND),
                ("a canopy overhead is sky: the ground under it", column(*soil, ("oak_leaves", GROUND + 4, GROUND + 5)),
                 GROUND),
                ("a trunk stands on the ground: no stand in this column",
                 column(*soil, ("oak_log", GROUND, GROUND + 3)), None),
                ("an overhang: its top is the open-sky stand", column(*soil, ("stone", GROUND + 6, GROUND + 7)),
                 GROUND + 8)]
        for name, blocks, want in rows:
            with self.subTest(name):
                r = FakeRegion((0, BOTTOM, 0), (0, TOP + 2, 0), blocks)
                got = explore.surface_cell(r.name, r.solid, 0, 0, TOP, BOTTOM)
                self.assertEqual(got, want)
                # must fail: the old target, a buried stone hit's y+1, inside the ground
                self.assertNotEqual(got, GROUND - 5 + 1)
                if want is not None:
                    self.assertFalse(r.solid((0, got, 0)) or r.solid((0, got + 1, 0)))   # never inside the ground


class WhoDigs(unittest.TestCase):
    def test_rows(self):
        ore = next(blocks[0] for tok, (blocks, _t) in MINE.items() if FIND_AT.get(tok) is not None)
        surface = next(blocks[0] for tok, (blocks, _t) in MINE.items() if FIND_AT.get(tok) is None)
        # (kinds sought) → the search may dig
        rows = [("an ore: its band lies underground, the search digs", [ore], True),
                ("must fail: a bed search digs", ["minecraft:white_bed"], False),
                ("sheep", ["minecraft:sheep"], False),
                ("a mined block without a band is found on the surface", [surface], False)]
        for name, kinds, want in rows:
            with self.subTest(name):
                self.assertEqual(explore.underground_search(kinds), want)


if __name__ == "__main__":
    unittest.main()

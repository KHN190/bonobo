"""nav.holds: the jar's MineTask.holds mirrored — a stand works a block it can see (sight from the eye and 0.3 off it,
within the reach less 0.5), never its own column above it unless digging down. The mine's in_reach reads it."""
import unittest

from bonobo import nav
from tests.world import FakeRegion


def ground(extra=(), air=()):
    """Stone up to y 63 over x, z −6..6; `extra` cells set, `air` cells cleared."""
    blocks = {(x, y, z): "stone" for x in range(-6, 7) for y in range(58, 64) for z in range(-6, 7)}
    for c in air:
        blocks.pop(c, None)
    blocks.update(dict(extra))
    return FakeRegion((-7, 57, -7), (7, 70, 7), blocks)


class Holds(unittest.TestCase):
    def test_rows(self):
        ore = "iron_ore"
        # (situation, the world, feet, the cell, down) → holds
        rows = [("an ore standing in the open 2 off: in sight", ground([((2, 64, 0), ore)]), (0, 64, 0), (2, 64, 0),
                 False, True),
                ("digging down on purpose: its own column below", ground(), (0, 64, 0), (0, 63, 0), True, True),
                ("must fail: an ore at the rim, from a pit 2 down (distance 2.2, no sight)",
                 ground([((2, 64, 0), ore)], air=[(0, 62, 0), (0, 63, 0)]), (0, 62, 0), (2, 64, 0), False, False),
                ("must fail: its own column below, not digging down", ground(), (0, 64, 0), (0, 63, 0), False, False),
                ("must fail: every face behind a wall", ground([((3, 64, 0), ore), ((2, 64, 0), "stone"),
                                                            ((2, 65, 0), "stone"), ((2, 66, 0), "stone")]),
                 (0, 64, 0), (3, 64, 0), False, False),
                ("must fail: in sight but past the reach less 0.5", ground([((5, 64, 0), ore)]), (0, 64, 0),
                 (5, 64, 0), False, False)]
        for name, region, feet, cell, down, want in rows:
            with self.subTest(name):
                self.assertIs(nav.holds(region, feet, cell, down=down), want)

    def test_the_vein_does_not_hide_itself(self):
        # a vein cell behind another vein cell: hidden now, worked in the same batch once the first is broken
        region = ground([((2, 64, 0), "iron_ore"), ((3, 64, 0), "iron_ore"), ((2, 65, 0), "stone"),
                         ((3, 65, 0), "stone"), ((2, 64, 1), "stone"), ((2, 64, -1), "stone")])
        self.assertIs(nav.holds(region, (0, 64, 0), (3, 64, 0)), False)
        self.assertIs(nav.holds(region, (0, 64, 0), (3, 64, 0), through={(2, 64, 0), (3, 64, 0)}), True)


if __name__ == "__main__":
    unittest.main()

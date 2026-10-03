"""The mine stand check sees what the jar's raycast sees: a thin block with an outline (leaf litter, short grass)
stops the ray, so a block whose only open face it covers is out of sight."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bonobo import nav  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

FEET = (0, 64, 0)
TARGET = (0, 63, 1)


def ground(cover):
    """Grass at y 63 under the feet and around the target, `cover` on the target's only open face (its top)."""
    blocks = {(x, y, z): "grass_block" for x in range(-2, 3) for z in range(-2, 4) for y in (62, 63)}
    if cover is not None:
        blocks[(TARGET[0], TARGET[1] + 1, TARGET[2])] = cover
    return FakeRegion((-3, 60, -3), (3, 68, 4), blocks)


class ACoveredFaceIsOutOfSight(unittest.TestCase):
    def test_rows(self):
        rows = [("bare top: in sight", None, True),
                ("must fail: leaf litter over the top stops the jar's ray", "leaf_litter", False),
                ("must fail: short grass over the top stops the jar's ray", "short_grass", False)]
        for name, cover, want in rows:
            with self.subTest(name):
                self.assertIs(nav.stands_for("mine", ground(cover), FEET, TARGET), want)


if __name__ == "__main__":
    unittest.main()

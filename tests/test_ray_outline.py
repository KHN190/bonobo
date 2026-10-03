"""The mine stand check sees what the jar's raycast sees: a thin block with an outline (leaf litter, short grass)
stops the ray, so a block whose only open face it covers is out of sight — the body cannot tell it is passable the
way ray_first/holds read the world (PASSABLE), so the gate mines the cover first, unasked, and re-judges (nav.gate)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bonobo import nav  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

FEET = (0, 64, 0)
TARGET = (0, 63, 1)
COVER_CELL = (TARGET[0], TARGET[1] + 1, TARGET[2])


def ground(cover):
    """Grass at y 63 under the feet and around the target, `cover` on the target's only open face (its top)."""
    blocks = {(x, y, z): "grass_block" for x in range(-2, 3) for z in range(-2, 4) for y in (62, 63)}
    if cover is not None:
        blocks[COVER_CELL] = cover
    return FakeRegion((-3, 60, -3), (3, 68, 4), blocks)


def run_gate(region):
    """nav.gate over one mine task at TARGET, feet fixed at FEET, the region fixed (no real world read)."""
    tasks = [nav.mine_task(TARGET)]
    with mock.patch.object(nav, "feet", lambda: FEET), mock.patch.object(nav, "_read_box", lambda *a, **k: region):
        after = nav.gate(tasks, nav.Policy())
    return after, [(t["x"], t["y"], t["z"]) for t in tasks if t.get("type") == "mine"]


class ACoveredFaceIsMinedFirst(unittest.TestCase):
    def test_rows(self):
        rows = [("bare top: nothing extra mined", None, [TARGET]),
                ("leaf litter over the top: mined first, the target after", "leaf_litter", [COVER_CELL, TARGET]),
                ("short grass over the top: mined first, the target after", "short_grass", [COVER_CELL, TARGET])]
        for name, cover, want in rows:
            with self.subTest(name):
                after, mined = run_gate(ground(cover))
                self.assertTrue(callable(after))
                self.assertEqual(mined, want)


if __name__ == "__main__":
    unittest.main()

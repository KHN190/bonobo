"""K7, a moving target walked to where it was: nav.after_approach / nav.chase is the one approach to anything that
moves (a hunted animal, a piglin to barter with, a mob a seek goes to); every caller routes through it."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import explore, gather, nav, nether  # noqa: E402

COW = {"id": 7, "type": "minecraft:cow", "x": 3.5, "y": 64.0, "z": 0.5, "distance": 2.0}


class AfterApproach(unittest.TestCase):

    def test_table(self):
        for reach in (gather.HUNT_REACH, nether.PIGLIN_REACH, explore.SEEK_RANGE):
            near, far, other = dict(COW, distance=reach), dict(COW, distance=reach + 1.0), dict(COW, id=8, distance=1.0)
            rows = [("within reach", [near], ("near", near)),
                    ("must fail: walked on but in sight: walked to where it is now, not banned", [far, other],
                     ("moved", far)),
                    ("out of sight: the nearest other one", [other], ("lost", None))]
            for name, seen, want in rows:
                with self.subTest(name, reach=reach):
                    self.assertEqual(nav.after_approach(seen, COW["id"], reach), want)


class Chase(unittest.TestCase):
    """nav.chase walks to the mob's position as read after each walk (the walks' targets are its readings)."""

    def test_table(self):
        reach = gather.HUNT_REACH
        step = dict(COW, x=COW["x"] + 2 * reach, distance=2 * reach)
        rows = [("it stands within reach: no walk", [[dict(COW, distance=reach)]], ("near", 7), []),
                ("must fail: it walked on: each walk to its new place", [[step], [dict(step, x=step["x"] + 1)],
                                                                         [dict(COW, distance=1.0)]],
                 ("near", 7), [(int(step["x"]), 64, 0), (int(step["x"] + 1), 64, 0)]),
                ("it left sight: lost", [[step], []], ("lost", None), [(int(step["x"]), 64, 0)]),
                ("never closer: far after the tries", [[step]] * (nav.WAY_TRIES + 1), ("far", 7),
                 [(int(step["x"]), 64, 0)] * nav.WAY_TRIES)]
        for name, reads, want, walks in rows:
            with self.subTest(name):
                seen, walked = iter(reads), []
                with mock.patch("bonobo.world.entities", lambda *a, **k: next(seen)), \
                        mock.patch.object(nav, "arrived_near", lambda pos, *a, **k: walked.append(pos) or True):
                    how, e = nav.chase(COW["id"], [COW["type"]], None, reach)
                self.assertEqual((how, e["id"] if e else None), want)
                self.assertEqual(walked, walks)


if __name__ == "__main__":
    unittest.main()

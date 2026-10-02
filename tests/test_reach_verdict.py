"""A target with no way to it is not there for any estimate: cost.not_there (banned ∪ protected) is the one predicate the
remembered lookup, the in-sight nearest and sight_y read (C4: a vein behind the home re-picked 16 s later)."""
import os
import tempfile
import time
import unittest
from unittest import mock

from bonobo import world
from bonobo.cost import Cost
from bonobo.memory import Memory
from tests.world import snapshot, state

ORE = "iron_ore"


class NotThere(unittest.TestCase):
    def rows(self):
        snap = snapshot(state())
        x, y, z = snap.feet
        near, far = (x + 3, y - 2, z), (x + 20, y - 2, z)
        return snap, near, far

    def test_in_sight(self):
        snap, near, far = self.rows()
        hits = [{"x": c[0], "y": c[1], "z": c[2], "distance": float(abs(c[0] - snap.feet[0])), "block": ORE}
                for c in (near, far)]
        # (banned) → the distance priced
        for name, banned, want in [("nothing banned: the near one", {}, 3.0),
                                   ("must fail: the near one banned (no way): the far one", {near: time.time() + 60},
                                    20.0)]:
            with self.subTest(name), mock.patch.dict(world._SIGHT, {"key": (tuple(snap.feet), snap.dimension),
                                                                    "t": time.time(), "near": {ORE: 3.0},
                                                                    "y": {ORE: near[1]}, "hits": {ORE: hits}}):
                c = Cost(snap, blacklist=banned)
                self.assertEqual(c.distance([ORE], 48, sources=True), want)

    def test_remembered(self):
        snap, near, far = self.rows()
        for name, banned, want in [("nothing banned: the near note", {}, near),
                                   ("must fail: the near note banned: the far one", {near: time.time() + 60}, far)]:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = Memory(os.path.join(tmp, "notes.json"))
                for c in (near, far):
                    m.note_seen(ORE, c, snap.dimension)
                got = Cost(snap, mem=m, blacklist=banned)._nearest([ORE], sources=True)
                self.assertEqual(got[0] if got else None, want)


if __name__ == "__main__":
    unittest.main()

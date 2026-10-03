"""A target with no way to it is not there for any estimate: cost.not_there (banned ∪ protected) is the one predicate the
remembered lookup, the in-sight nearest and sight_y read (C4: a vein behind the home re-picked 16 s later)."""
import os
import tempfile
import time
import unittest
from unittest import mock

from bonobo import skillcore, world
from bonobo.cost import Cost
from bonobo.memory import Memory
from bonobo.planner import Step
from bonobo.world import Inventory, Versioned
from tests.world import FakeRegion, inventory, memory, snapshot, state

ORE = "iron_ore"

# merged from test_accept3.py (accept3, 21:21): the trunk column this cell belonged to
X, Z, FLOOR_Y = 12986, 12999, 74
LOGS = [(X, y, Z) for y in range(76, 80)]
LO, HI = (X - 8, FLOOR_Y - 6, Z - 8), (X + 8, FLOOR_Y + 10, Z + 8)


def _accept3_scene():
    blocks = {(x, y, z): "dirt" for x in range(LO[0], HI[0] + 1) for z in range(LO[2], HI[2] + 1)
              for y in range(LO[1], FLOOR_Y)}
    blocks.update({c: "oak_log" for c in LOGS})
    return FakeRegion(LO, HI, blocks)


def _accept3_cost(region, feet, carried, bans):
    inv = inventory(*carried)
    hits = {"oak_log": [{"x": c[0], "y": c[1], "z": c[2], "distance": float(c[1] - feet[1])} for c in LOGS]}
    snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), inv, hits, [], region)
    return Cost(snap, memory(), bans)


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
            with self.subTest(name):
                c = Cost(world.Snapshot.from_readings(snap.state, snap.inv, {ORE: hits}), memory(), blacklist=banned)
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


class ARefusedCellIsNotAskedAgain(unittest.TestCase):
    """P2/K1 with E5 (merged from test_accept3.py): a cell the door could not reach stays refused until a state that
    changes a way changes (blocks to place, a tool): a craft of planks and sticks between rounds is not one."""

    def test_rows(self):
        feet, banned = (X, FLOOR_Y, Z), LOGS[1]
        before = [("oak_log", 2)]
        crafted = [("oak_planks", 4), ("stick", 4)]
        rows = [("must fail (accept3): planks and sticks crafted lift the no-stand ban", before + crafted, True),
                ("blocks to place change the way: asked again", before + [("cobblestone", 16)], False)]
        for name, carried, still in rows:
            with self.subTest(name):
                bans = Versioned()
                kinds_then = frozenset(Inventory(inventory(*before)).slots[i]["id"] for i in range(len(before)))
                bans[banned] = skillcore.Ban(float("inf"), skillcore.ban_state(feet, kinds_then))
                c = _accept3_cost(_accept3_scene(), feet, carried, bans)
                self.assertEqual(banned in c.not_there(True), still)
                if still:
                    self.assertNotEqual(c.site(Step("gather", "log", 1, {})), banned)


class RefusedLeg(unittest.TestCase):
    """Merged from test_fix7.py (design-f1 V1): a refused travel leg is a walk that got no further, never an error
    past the walk (ban_then_other_source 041621: the task cooled, the caged cell never banned, the free ore never
    tried)."""

    def test_a_refused_leg_is_no_way_there(self):
        from unittest import mock
        from bonobo import api, nav
        ORE2 = (4, 64, 0)
        here = {"x": 0.5, "y": 64.0, "z": 0.5, "blockX": 0, "blockY": 64, "blockZ": 0, "onGround": True,
                "dimension": "minecraft:overworld"}

        def run(task, **k):
            raise api.Unreachable("travel: target unreachable; stopped at the closest reachable point", ())
        with mock.patch.object(api, "run", run), mock.patch.object(api, "get", lambda p: here), \
                mock.patch.object(nav, "feet", lambda: (0, 64, 0)), mock.patch.object(nav, "DOORS", None), \
                mock.patch.object(nav, "ROAD_MEM", None), mock.patch.object(nav, "_doorways_between", lambda a, b: {}), \
                mock.patch.object(nav, "mod_features", lambda: {"travel"}), \
                mock.patch.object(nav, "Inventory", lambda: type("I", (), {"count": lambda s, k: 0})()), \
                mock.patch.object(api, "at_boundary", lambda: None), \
                mock.patch.object(nav.arbiter.BODY, "owns", lambda *a: True):
            # must fail: the refusal raised out of the walk (the task failed and cooled, no ban)
            self.assertFalse(nav.arrived_near((ORE2[0] * 3, ORE2[1], ORE2[2]), nav.Policy(), range_=3.5, attempts=1))


if __name__ == "__main__":
    unittest.main()

"""A walk never breaks a door (a block with an `open` state): a shut one a hand opens is opened on the way, an open
one walked through untouched, one no hand opens (iron) is a wall — no digging round it, the walk fails "blocked by a
door". Python only: one bounded read with states over the walk's box."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, nav  # noqa: E402

HERE, THERE = (0, 64, 0), (10, 64, 0)
LOWER, UPPER = (5, 64, 0), (5, 65, 0)
WOOD, IRON = "oak_door", nav.LOCKED[0]


class Read:
    """A region read with states: names and props by cell."""

    def __init__(self, name, opened):
        self.cells = {LOWER: name, UPPER: name}
        self.props = {c: {"open": "true" if opened else "false", "half": "lower" if c == LOWER else "upper"}
                      for c in self.cells}

    def name(self, c):
        return self.cells.get(c, "air")


class Steps(unittest.TestCase):
    def test_rows(self):
        # (door, open) → (opened by hand, a wall)
        rows = [("a shut wooden door: opened by hand, its lower half once", WOOD, False, [LOWER], []),
                ("must fail: an open wooden door used (a use shuts it)", WOOD, True, [], []),
                ("a shut iron door: a wall", IRON, False, [], [LOWER]),
                ("an open iron door (a lever held it): walked through", IRON, True, [], [])]
        for name, block, opened, want_hand, want_locked in rows:
            with self.subTest(name):
                self.assertEqual(nav.door_steps(nav.doorways(Read(block, opened)), HERE, THERE),
                                 (want_hand, want_locked))
        # must fail: a barrel's `open` read as a door (09:35: the barrel just used "opened by hand" on the way)
        self.assertEqual(nav.door_steps(nav.doorways(Read("barrel", False)), HERE, THERE), ([], []))
        far = {(5, 64, 9): (True, False)}          # beside, not on the way
        self.assertEqual(nav.door_steps(far, HERE, THERE), ([], []))


class Walk(unittest.TestCase):
    def walk(self, block, arrive):
        posted = []
        state = {"x": HERE[0] + 0.5, "y": float(HERE[1]), "z": HERE[2] + 0.5, "blockX": HERE[0], "blockY": HERE[1],
                 "blockZ": HERE[2], "onGround": True, "dimension": "minecraft:overworld"}

        def run(task, **k):
            posted.append(task)
            return {"status": "succeeded", "message": "arrived"}
        with mock.patch.object(nav, "_doorways_between", lambda a, b: nav.doorways(Read(block, False))), \
                mock.patch.object(nav, "DOORS", None), mock.patch.object(nav, "ROAD_MEM", None), \
                mock.patch.object(api, "run", run), mock.patch.object(api, "get", lambda p: state), \
                mock.patch.object(api, "detail", lambda *a: None), mock.patch.object(nav, "feet", lambda: HERE), \
                mock.patch.object(nav, "there", lambda s, p, r: arrive), \
                mock.patch.object(nav, "Inventory", lambda: type("I", (), {"count": lambda s, k: 0})()), \
                mock.patch.object(nav, "_arrived", lambda *a, **k: arrive):
            try:
                got = nav._travel(THERE, nav.Policy(), 1.5, 1, None, "work", HERE, 0.0)
            except api.NavFailed as e:
                got = str(e)
        return posted, got

    def avoided(self, task):
        return {(c["x"], c["y"], c["z"]) for c in task.get("avoid", ())}

    def test_a_wooden_door_is_opened_and_never_dug(self):
        posted, got = self.walk(WOOD, True)
        self.assertEqual([t["type"] for t in posted][:2], ["use", "travel"])
        # must fail: the wooden door on the way broken
        self.assertTrue({LOWER, UPPER} <= self.avoided(posted[1]))
        self.assertTrue(got)

    def test_an_iron_door_is_a_wall(self):
        posted, got = self.walk(IRON, False)
        travel = [t for t in posted if t["type"] == "travel"]
        # must fail: the iron door broken, or the wall beside it dug (press_door_to_chest 022603)
        self.assertTrue(travel and all(not t["break"] and {LOWER, UPPER} <= self.avoided(t) for t in travel))
        self.assertNotIn("use", [t["type"] for t in posted])
        self.assertIn("blocked by a door", got)


if __name__ == "__main__":
    unittest.main()

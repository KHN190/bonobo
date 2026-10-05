"""Memory keeps at most 150 notes something prices (the user's rule): past it the note worth least goes — its worth
the value of what it yields (bag.note_value: the bag's own value over the planner's prices), ties the oldest — and a
note nothing prices (a portal, a fortress, a village) is kept for good and never counted. The cap rows set the
measure as the brain wires it (Memory.worth) and use only what the base has, so they are red there by assertion."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.world import memory  # noqa: E402

DIM = "minecraft:overworld"
CAP = 150
PRICES = {"tree": 20.0, "iron_ore": 60.0, "sheep": 15.0}     # seconds a note is worth (as note_value would say)


def capped():
    mem = memory()
    mem.worth = lambda row: PRICES.get(row["kind"])              # a portal, a fortress: nothing prices them
    return mem


def kinds(mem):
    return [r["kind"] for r in mem.data["seen"]]


class TheCap(unittest.TestCase):
    """[P4]"""

    def test_the_151st_evicts_the_cheapest(self):
        mem = capped()
        for i in range(CAP - 1):
            mem.note_seen("iron_ore", (i * 4, 40, 0), DIM)
        mem.note_seen("tree", (0, 64, 100), DIM)                                 # the one cheap note
        self.assertEqual(len(mem.data["seen"]), CAP)
        mem.note_seen("iron_ore", (0, 40, 500), DIM)                             # the 151st
        # must fail: 151 kept (no cap), or a dear note gone before the cheap one
        self.assertEqual(len(mem.data["seen"]), CAP)
        self.assertNotIn("tree", kinds(mem))

    def test_ties_go_oldest_first(self):
        mem = capped()
        for i in range(CAP + 1):
            mem.clock = i
            mem.note_seen("iron_ore", (i * 4, 40, 0), DIM)
        self.assertEqual(len(mem.data["seen"]), CAP)
        self.assertNotIn([0, 40, 0], [r["pos"] for r in mem.data["seen"]], "must fail: the newest of equals went")

    def test_a_portal_is_never_evicted(self):
        mem = capped()
        mem.note_seen("portal", (5, 64, 5), DIM)
        mem.note_seen("fortress", (900, 70, 900), DIM)
        for i in range(CAP + 20):
            mem.note_seen("sheep" if i % 2 else "iron_ore", (i * 20, 40, 0), DIM)
        self.assertIn("portal", kinds(mem))
        self.assertIn("fortress", kinds(mem))
        # unpriced notes are not counted: the cap is over the priced ones only
        self.assertEqual(len([k for k in kinds(mem) if k in PRICES]), CAP)


class NoteValue(unittest.TestCase):
    """bag.note_value: Σ value of what a note yields, from the tables that say it; None when nothing it yields has a
    price."""

    def test_rows(self):
        from bonobo import bag
        price = {"minecraft:raw_iron": 30.0, "minecraft:mutton": 4.0, "wool": 6.0, "log": 3.0,
                 "minecraft:coal": 10.0, "minecraft:cobblestone": 1.0}.get
        rows = [("an iron ore: its raw iron", "iron_ore", 30.0),
                ("a sheep: mutton and wool (HUNT_YIELD)", "sheep", 1.5 * 4.0 + 1 * 6.0),
                ("a tree: its logs", "tree", bag.TREE_LOGS * 3.0),
                ("a coal ore, deepslate too", "deepslate_coal_ore", 10.0),
                ("stone: cobblestone", "stone", 1.0),
                ("must fail: a portal yields nothing priced", "portal", None),
                ("a village", "village", None)]
        for name, kind, want in rows:
            with self.subTest(name):
                got = bag.note_value(kind, price)
                self.assertEqual(got, want)

    def test_one_value_with_the_bag(self):
        """The bag's reget price and a note's worth are the same value (bag.item_value)."""
        from bonobo import bag
        price = {"minecraft:raw_iron": 30.0}.get
        self.assertEqual(bag.reget_seconds({"id": "minecraft:raw_iron", "count": 3}, price),
                         bag.item_value("minecraft:raw_iron", 3, price))
        self.assertEqual(bag.note_value("iron_ore", price), bag.item_value("minecraft:raw_iron", 1, price))


if __name__ == "__main__":
    unittest.main()

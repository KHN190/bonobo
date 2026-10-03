"""A planned way builds only from what the held plans do not reserve (bag.RESERVED, read as the tidy reads it:
bag.reserved_stacks keeps the biggest stack of each reserved id): a bridge never spends the cobblestone the next
stone pickaxe is crafted from. Imports only what the base has too: the plan_way rows are red there by assertion."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import bag, nav  # noqa: E402
from bonobo.world import Inventory  # noqa: E402
from tests.world import FakeRegion, inventory  # noqa: E402

FEET, ORE = (0, 64, 0), (5, 64, 0)
COBBLE = "minecraft:cobblestone"


def chasm():
    """A 3-wide chasm on the way to an ore: crossed only on placed treads."""
    lo, hi = (-10, 50, -10), (10, 80, 10)
    blocks = {(x, 63, z): "stone" for x in range(lo[0], hi[0] + 1) for z in range(lo[2], hi[2] + 1)}
    for x in range(1, 4):
        del blocks[(x, 63, 0)]
    blocks[ORE] = "iron_ore"
    return FakeRegion(lo, hi, blocks)


def bag_of(*stacks):
    slots = [{"id": COBBLE, "count": n, "slot": i} for i, n in enumerate(stacks)]
    return Inventory({"slots": slots, "equipment": {}})


def treads(steps):
    return [t for t in steps or () if t["type"] == "place"]


class AWayKeepsTheReservedStack(unittest.TestCase):
    """nav.plan_way (the door's way, reach's and the gate's) with the plan's cobblestone reserved."""

    def way(self, stacks, reserved):
        with mock.patch.object(bag, "RESERVED", set(reserved)):
            steps, why, _s = nav.plan_way(chasm(), FEET, ORE, "mine", bag_of(*stacks), set())
        return steps, why

    def test_rows(self):
        rows = [("nothing reserved: the bridge is built", (10,), (), True),
                ("must fail: the one stack reserved (a stone pickaxe's): no tread spent from it", (10,), (COBBLE,),
                 False),
                ("two stacks, one reserved: the other bridges", (64, 10), (COBBLE,), True)]
        for name, stacks, reserved, bridged in rows:
            with self.subTest(name):
                steps, why = self.way(stacks, reserved)
                self.assertEqual(bool(treads(steps)), bridged, (steps, why))

    def test_the_tread_budget_is_the_unreserved_stock(self):
        """64 + 10 with the 64 kept: the budget is place_budget(10), never place_budget(74)."""
        steps, _why = self.way((64, 10), (COBBLE,))
        self.assertLessEqual(len(treads(steps)), nav.place_budget(10))


class WayBag(unittest.TestCase):
    """nav.way_bag: the one view of what a way may place (the reserved stacks out, as bag.reserved_stacks keeps them)."""

    def test_rows(self):
        rows = [("nothing reserved: the whole bag", (10, 5), (), 15),
                ("the biggest reserved stack kept back", (10, 5), (COBBLE,), 5),
                ("one reserved stack: none to build with", (7,), (COBBLE,), 0)]
        for name, stacks, reserved, left in rows:
            with self.subTest(name), mock.patch.object(bag, "RESERVED", set(reserved)):
                self.assertEqual(nav.way_bag(bag_of(*stacks)).count("building"), left)

    def test_no_bag_passes_through(self):
        self.assertIsNone(nav.way_bag(None))


if __name__ == "__main__":
    unittest.main()

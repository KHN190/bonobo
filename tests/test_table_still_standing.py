"""craft_chain's verify reads the table where its sitting left it (placed, kept standing for the next craft), not
only the bag -- the table must still be read as the goal, or a finished craft reads as "finished without reaching
its goal"."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import craft  # noqa: E402
from tests.world import bag, inventory  # noqa: E402


class APlacedTableIsMade(unittest.TestCase):
    """K9: a station standing from a prior sitting counts as made, same as one carried."""

    def test_table(self):
        near = [{"x": 12985, "y": 74, "z": 12998}]
        rows = [("must fail: placed and left standing: still made", craft.TABLE, inventory(), near, 1),
                ("carried in the bag", craft.TABLE, inventory(("crafting_table", 1)), [], 1),
                ("neither: not made", craft.TABLE, inventory(), [], 0),
                ("another item is the bag's alone", "minecraft:stick", inventory(("stick", 2)), near, 2)]
        for name, item, carried, tables, want in rows:
            with self.subTest(name):
                self.assertEqual(craft.made_count(item, bag(carried), tables), want)

    def test_a_table_standing_before_is_not_made(self):
        # must fail (E2): a crafting table "made" by the one that stood there before the sitting, the bag holding none
        near = [{"x": 12985, "y": 74, "z": 12998}]
        before = craft.made_count(craft.TABLE, bag(inventory()), near)
        self.assertLess(craft.made_count(craft.TABLE, bag(inventory()), near), before + 1)


if __name__ == "__main__":
    unittest.main()

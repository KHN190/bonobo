"""Detail readouts (instrumentation only): a break's line and a station use's line say what a later diagnosis needs."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, craft  # noqa: E402


class BreakLine(unittest.TestCase):
    def test_rows(self):
        mine = {"type": "mine", "x": 1, "y": 64, "z": 2, "item": "minecraft:iron_pickaxe"}
        done = {"status": "succeeded", "seconds": 0.35, "result": {"block": "minecraft:stone"}}
        rows = [("a mine: block, cell, seconds, held", mine, done,
                 "   break minecraft:stone at (1, 64, 2): succeeded 0.35s holding minecraft:iron_pickaxe"),
                ("must fail: a mine with no item named said as held", {k: v for k, v in mine.items() if k != "item"},
                 done, "   break minecraft:stone at (1, 64, 2): succeeded 0.35s holding (none named)"),
                ("a travel that may dig", {"type": "travel", "break": True, "item": "hand"},
                 {"status": "succeeded", "seconds": 4.0},
                 "   travel may dig: succeeded 4.0s holding hand (the jar's result names no dug cell)"),
                ("a travel that may not: nothing said", {"type": "travel", "break": False}, {}, None),
                ("a use: nothing said", {"type": "use"}, {}, None)]
        for name, task, r, want in rows:
            with self.subTest(name):
                self.assertEqual(api.break_line(task, r), want)


class UseLine(unittest.TestCase):
    def test_says_why_no_screen(self):
        got = craft.use_line((5, 64, 5), "crafting_table", None, {"status": "succeeded", "message": "used",
                                                                 "result": {"screen": "none"}},
                             (3, 64, 5), (-90.0, 10.0), ["oak_leaves@(4, 65, 5)"], ["zombie@2.1"])
        # must fail: a use with no screen said without what stood in the way
        for part in ("screen none", "feet (3, 64, 5)", "look (-90.0, 10.0)", "between ['oak_leaves@(4, 65, 5)']",
                     "near ['zombie@2.1']"):
            self.assertIn(part, got)


if __name__ == "__main__":
    unittest.main()

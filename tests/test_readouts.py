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
        torch = "minecraft:torch (slot 3)"
        rows = [("a mine: block, cell, seconds, asked and held", mine, done, torch,
                 "   break minecraft:stone at (1, 64, 2): succeeded 0.35s asked minecraft:iron_pickaxe, "
                 "held during minecraft:torch (slot 3)"),
                ("must fail: a mine with no item named said as held", {k: v for k, v in mine.items() if k != "item"},
                 done, None, "   break minecraft:stone at (1, 64, 2): succeeded 0.35s asked (none named), held during: "
                             "not seen (no poll while it mined)"),
                ("a travel that may dig", {"type": "travel", "break": True, "item": "hand"},
                 {"status": "succeeded", "seconds": 4.0}, torch,
                 "   travel may dig: succeeded 4.0s asked hand, held during minecraft:torch (slot 3) "
                 "(the jar's result names no dug cell)"),
                ("a travel that may not: nothing said", {"type": "travel", "break": False}, {}, torch, None),
                ("a use: nothing said", {"type": "use"}, {}, torch, None)]
        for name, task, r, held, want in rows:
            with self.subTest(name):
                self.assertEqual(api.break_line(task, r, held), want)

    def test_held_seen_while_mining(self):
        # must fail: the hand read before the post (the last task's item), not while the break ran
        from unittest import mock
        polls = iter([{"status": "running", "type": "mine", "doing": "mining minecraft:stone at 1, 64, 2"},
                      {"status": "succeeded", "type": "mine"}])
        state = {"control": {"task": {"id": 7, "type": "mine", "doing": "mining minecraft:stone at 1, 64, 2"}},
                 "mainHand": {"id": "minecraft:torch"}, "selectedSlot": 3, "x": 0, "y": 64, "z": 0}
        api.HELD_SEEN.clear()
        with mock.patch.object(api, "get", lambda path: next(polls) if path.startswith("/task") else state), \
                mock.patch.object(api, "check_interrupt", lambda *a: None):
            api.await_task(7, 30)
        self.assertEqual(api.held_seen(7), "minecraft:torch (slot 3)")
        self.assertIsNone(api.held_seen(7))               # said once


class UseLine(unittest.TestCase):
    def test_says_why_no_screen(self):
        got = craft.use_line((5, 64, 5), "crafting_table", None, {"status": "succeeded", "message": "used",
                                                                 "result": {"screen": "none"}},
                             (3, 64, 5), (-90.0, 10.0), ["oak_leaves@(4, 65, 5)"], ["zombie@2.1"])
        # must fail: a use with no screen said without what stood in the way
        for part in ("screen none", "feet (3, 64, 5)", "look (-90.0, 10.0)", "between ['oak_leaves@(4, 65, 5)']",
                     "near ['zombie@2.1']"):
            self.assertIn(part, got)


class UseOpensOrFails(unittest.TestCase):
    def test_no_screen_fails_before_the_crafts(self):
        # must fail: the table's use answered screen "none" and the crafts went on (the 2×2 failure after)
        from unittest import mock
        sent = []

        def chain(tasks, **k):
            sent.extend(tasks)
            return [{"status": "succeeded", "result": {"screen": "none" if t["type"] == "use" else None}}
                    for t in tasks]
        use = {"type": "use", "x": 1, "y": 64, "z": 2}
        craft_ = {"type": "craft", "pattern": [], "count": 1}
        region = type("R", (), {"__init__": lambda s, a, b: None, "name": lambda s, p: "crafting_table"})
        with mock.patch.object(api, "run_chain", chain), mock.patch.object(craft, "use_readout", lambda *a: None), \
                mock.patch.object(craft, "Region", region), mock.patch.object(api, "post"):
            with self.assertRaises(api.McError) as e:
                craft.run_split([use, craft_, craft.CLOSE])
        self.assertIn("crafting_table at (1, 64, 2)", str(e.exception))
        self.assertEqual(sent, [use])


if __name__ == "__main__":
    unittest.main()

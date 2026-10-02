"""check/inv/effects: E1 and E3 over the jar tasks a step would send, holding and must-fail rows."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api  # noqa: E402
from check.inv import effects  # noqa: E402
from check.oracle import Unchecked  # noqa: E402

GOTO = {"type": "goto", "x": 1, "y": 64, "z": 0}
TRAVEL_OFF = {"type": "travel", "x": 9, "y": 64, "z": 0, "break": False, "place": False, "voidBridge": False}
MINE = {"type": "mine", "x": 2, "y": 63, "z": 0, "item": "minecraft:stone_pickaxe"}


class Effects(unittest.TestCase):
    # (invariant, tasks or None: the field absent, hold wired?, fires?)
    ROWS = [
        ("E3", [GOTO, TRAVEL_OFF, MINE], False, False),
        ("E3", [dict(TRAVEL_OFF, **{"break": True})], False, True),             # must fail: a walk that may dig
        ("E3", [dict(TRAVEL_OFF, voidBridge=True)], False, True),               # must fail: a walk that bridges
        ("E3", [MINE], False, False),                                           # a named dig is no walk
        ("E1", [MINE, GOTO], False, False),
        ("E1", [dict(MINE, item=None)], False, True),                           # must fail: whatever is in the hand
        ("E1", [dict(MINE, item=None)], True, False),                           # the door's hold names it
        ("E3", None, False, None),                                              # unsaid: Unchecked
    ]

    def test_rows(self):
        for inv, tasks, hold, fires in self.ROWS:
            with self.subTest(inv=inv, tasks=tasks, hold=hold), \
                    mock.patch.object(api, "HOLD", (lambda ts: ts) if hold else None):
                got = effects.CHECKS[inv]({}, None, {}, {} if tasks is None else {"tasks": tasks})
                if fires is None:
                    self.assertIsInstance(got, Unchecked)
                else:
                    self.assertEqual(got is not None and not isinstance(got, Unchecked), fires, got)

    def test_live_ones_say_so(self):
        for inv in ("E2", "P1"):
            self.assertIsInstance(effects.CHECKS[inv]({}, None, {}, {}), Unchecked)


if __name__ == "__main__":
    unittest.main()

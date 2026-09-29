"""The event log records changes only: a repeated bid is one line, an identical line is counted, an anomaly is said
at its 1st, 10th, 100th time."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import events  # noqa: E402


class Events(unittest.TestCase):
    def setUp(self):
        events.reset_state()

    def test_decisions_only_when_they_change(self):
        out = []
        for pick in ("fight", "fight", "fight", "evade", "evade", "fight"):
            events.decision("fight", pick, 10.0, "why", t=1.0, sink=out)
        # must fail: the same bid written every round
        self.assertEqual([r["pick"] for r in out if r["kind"] == "decision"], ["fight", "evade", "fight"])

    def test_identical_lines_counted(self):
        out = []
        for _ in range(4):
            events.emit("task", "mine: ok (1.0s)", t=1.0, sink=out)
        events.emit("goal", "goal: iron", t=2.0, sink=out)
        rows = [(r["kind"], r.get("count")) for r in out]
        self.assertEqual(rows, [("task", None), ("repeat", 4), ("goal", None)])

    def test_anomalies_at_1_10_100(self):
        out = []
        for _ in range(100):
            events.anomaly("slow round", "6.0s", t=1.0, sink=out)
        self.assertEqual([r["count"] for r in out if r["kind"] == "anomaly"], [1, 10, 100])

    def test_rows(self):
        out = []
        rows = [("a slow round is an anomaly", lambda: events.round_time(events.SLOW_ROUND_S + 1, t=1.0, sink=out), 1),
                ("must fail: a quick round said", lambda: events.round_time(0.1, t=1.0, sink=out), 0),
                ("a first diamond is a milestone", lambda: events.milestones({"minecraft:diamond": 1}, t=1.0, sink=out), 1),
                ("the second time it is not", lambda: events.milestones({"minecraft:diamond": 3}, t=1.0, sink=out), 0),
                ("a goal said once", lambda: (events.goal("iron", t=1.0, sink=out), events.goal("iron", t=2.0, sink=out)),
                 1)]
        for name, act, n in rows:
            with self.subTest(name):
                before = len(out)
                act()
                self.assertEqual(len(out) - before, n)


if __name__ == "__main__":
    unittest.main()

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

    def test_resume(self):
        out = []
        events.task("mine", "interrupted", 3.0, "layer:tactic", t=1.0, sink=out)
        events.task_start("mine", t=5.0, sink=out)
        events.task_start("mine", t=9.0, sink=out)          # must fail: a second start said as a resume
        events.task_start("chop", t=9.0, sink=out)          # never interrupted: nothing said
        self.assertEqual([r["kind"] for r in out], ["task", "resume"])

    def test_goal_progress(self):
        out = []
        events.goal("gather logs", {"minecraft:oak_log": 1}, t=0.0, sink=out)
        events.goal("mine iron", {"minecraft:oak_log": 5, "minecraft:dirt": 1}, t=30.0, sink=out)
        prog = [r for r in out if r["kind"] == "progress"]
        self.assertEqual(prog[0]["gained"], {"minecraft:dirt": 1, "minecraft:oak_log": 4})
        # must fail: a goal that gained nothing still says progress
        events.goal("craft", {"minecraft:oak_log": 5, "minecraft:dirt": 1}, t=40.0, sink=out)
        self.assertEqual(len([r for r in out if r["kind"] == "progress"]), 1)

    def test_slow_round_is_deciding_not_running(self):
        # (round s, of it running a task s) → a slow-round anomaly?
        slow = events.SLOW_ROUND_S
        rows = [("slow deciding", slow + 1, 0.0, 1),
                ("must fail: a long task read as a slow round", slow * 10, slow * 10 - 0.7, 0),
                ("slow deciding around a task", slow * 10, slow * 10 - slow - 1, 1)]
        for name, total, ran, n in rows:
            with self.subTest(name):
                events.reset_state()
                out = []
                events.round_time(total, ran, t=1.0, sink=out)
                self.assertEqual(len([r for r in out if r["kind"] == "anomaly"]), n)


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

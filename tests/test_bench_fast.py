"""The bench's own waits: none fixed where the world can say it is ready."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.bench import core  # noqa: E402


class Replies(unittest.TestCase):
    def test_rows(self):
        # (situation, commands) → replies waited on
        rows = [("every command answers", ["time set day", "weather clear"], 2),
                ("must fail: a gamemode already set says nothing: not waited on", ["gamemode survival @p",
                                                                                 "time set day"], 1),
                ("execute … run X answers as X", ["execute in minecraft:overworld run gamemode survival @p",
                                                  "execute in minecraft:overworld run clear @p"], 1),
                ("none", [], 0)]
        for name, cmds, want in rows:
            with self.subTest(name):
                self.assertEqual(core.replies(cmds), want)


class AnIdleRoundWakesOnTheOutcome(unittest.TestCase):
    """brain.idle_wait ends before its first slice when `wake` says the row's outcome is there."""

    def test_rows(self):
        from types import SimpleNamespace
        from unittest import mock
        from bonobo import api, brain
        full = max(1, brain.IDLE_WAIT_TICKS // brain.IDLE_SLICE_TICKS)
        # (situation, wake) → slices waited
        rows = [("the outcome there: no slice", lambda: True, 0),
                ("must fail: no wake: the whole idle wait", None, full)]
        for name, wake, want in rows:
            with self.subTest(name), mock.patch.object(api, "run", return_value={"status": "succeeded"}):
                me = SimpleNamespace(just_finished=False, wake=wake)
                self.assertEqual(brain.Brain.idle_wait(me, lambda: False), want)


class AReportKeepsItsTrace(unittest.TestCase):
    """The failed row's report holds copies: the next row clears TRACE_NOW before the report's thread writes."""

    def test_rows(self):
        from bonobo.bench import runner
        trace, feedback, lines = [{"t": 1.0}], [{"cmd": "x"}], ["a"]
        rec = runner.failure_record("r", "c", "skill", "n", 1.0, feedback, trace, lines)
        trace.clear()          # what the next row's run() does to TRACE_NOW
        feedback.clear()
        # (situation, the field) → still what the row saw
        rows = [("must fail: the trace emptied by the next row", "trace", [{"t": 1.0}]),
                ("the feedback", "feedback", [{"cmd": "x"}]),
                ("the log", "log", ["a"])]
        for name, key, want in rows:
            with self.subTest(name):
                self.assertEqual(rec[key], want)


class APrebuildIsNeverWaitedOn(unittest.TestCase):
    def test_rows(self):
        import threading
        import time
        from bonobo.bench import runner
        # (situation, its prebuild finished) → cloned
        for name, finished, want in [("finished: cloned", True, True),
                                     ("must fail (no wait): not finished yet — built in place at once", False, False)]:
            with self.subTest(name):
                done = threading.Event()
                if finished:
                    done.set()
                runner.PREBUILT.update(name="r", done=done, ok=True)
                t0 = time.time()
                self.assertIs(runner.take_prebuilt("r"), want)
                self.assertLess(time.time() - t0, 0.5)


class ACellWaitsOnItsSummons(unittest.TestCase):
    def test_summoned(self):
        # (situation, the cell's commands) → what it summons
        rows = [("two zombies and a skeleton", ["summon zombie 1 2 3", "summon minecraft:zombie 1 2 4",
                                                 "execute in minecraft:overworld run summon skeleton 0 0 0"],
                 {"minecraft:zombie": 2, "minecraft:skeleton": 1}),
                ("must fail: a fill summons nothing: no wait", ["fill 0 0 0 1 1 1 stone"], {})]
        for name, cmds, want in rows:
            with self.subTest(name):
                self.assertEqual(core.summoned(cmds), want)

    def test_ready_at_once(self):
        """Counted on the server as soon as it is there: no fixed sleep (the old 0.5 s per cell)."""
        import time
        from unittest import mock
        with mock.patch.object(core, "_command", return_value=["Test passed, count: 1"]):
            t0 = time.time()
            core._cell_ready(["summon zombie 0 0 0"], [])
            self.assertLess(time.time() - t0, core.CELL_POLL_S)

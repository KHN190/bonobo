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

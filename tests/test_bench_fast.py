"""The bench's own waits: none fixed where the world can say it is ready."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.bench import core  # noqa: E402


class TheReportReadsWhereTheBodyStopped(unittest.TestCase):
    """accept_fresh_iron_pickaxe's report: the region read at the bench origin's box, 3000 blocks off the row: no
    blocks; a failure's region is the scene box's extent round the body."""

    def test_rows(self):
        from bonobo.bench import runner
        for name, feet in (("at the bench origin", core.at(0, 0, 0)),
                           ("must fail: 3000 blocks off the origin", (13030, 76, 12958))):
            with self.subTest(name):
                lo, hi = runner.report_box(feet)
                self.assertTrue(all(lo[i] <= feet[i] <= hi[i] for i in range(3)))
                self.assertEqual(tuple(hi[i] - lo[i] for i in range(3)), tuple(core.BOX[1][i] - core.BOX[0][i] for i in range(3)))


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


class AnInjectionIsPending(unittest.TestCase):
    """A bench interrupt reaches the running work: api.request_interrupt, never a module attribute nobody reads."""

    def test_rows(self):
        from unittest import mock
        from bonobo import api
        from bonobo.bench.words import runs

        def old_write(msg):         # what the injection did before: a write to a removed attribute
            setattr(api, "INTERRUPT", msg)
        # (situation, injection) → pending after it
        rows = [("injected", lambda: runs._inject_interrupt(), runs.INJECTED),
                ("with its own message", lambda: runs._inject_interrupt("bench: x"), "bench: x"),
                ("must fail: the old attribute write leaves nothing pending", lambda: old_write(runs.INJECTED), None)]
        for name, inject, want in rows:
            with self.subTest(name), mock.patch.object(api.STATE, "interrupt", None), \
                    mock.patch.object(api, "INTERRUPT", None, create=True):
                inject()
                self.assertEqual(api.interrupt_pending(), want)


class TheInterruptIsSaid(unittest.TestCase):
    """The bench's interrupts in detail.log: each injection, each one caught, and one left pending at the end."""

    def test_rows(self):
        from unittest import mock
        from bonobo import api
        from bonobo.bench.words import runs

        def cut_once(ctx, _n=[0]):
            _n[0] += 1
            if _n[0] == 1:
                raise api.Interrupted("bench: x")
            return True

        def ends_then_injected(ctx):
            runs._inject_interrupt()           # landed after the work's last check
            return True
        # (situation, run) → what detail.log says
        rows = [("caught and resumed", cut_once, ["caught Interrupted", "interrupts caught 1"]),
                ("must fail: landed after the end — said consumed, not caught", ends_then_injected,
                 ["interrupt injected", "interrupts caught 0, a bench interrupt still pending consumed"])]
        for name, run, want in rows:
            said = []
            with self.subTest(name), mock.patch.object(api, "detail", said.append), \
                    mock.patch.object(api.STATE, "interrupt", None), mock.patch.dict(runs.INTERRUPTS, clear=True), \
                    mock.patch.dict(runs.BASE, {"name": "r", "t": 0.0}):
                runs._resume("r", run, lambda ctx: True)(None)
                for w in want:
                    self.assertTrue(any(w in line for line in said), f"{w!r} not in {said}")
                self.assertIsNone(api.interrupt_pending(), "nothing left for the next row")


class AnInjectionLandsWhileATaskRuns(unittest.TestCase):
    def test_rows(self):
        from bonobo.bench.words import runs
        run = {"id": 7, "type": "mine", "status": "running"}
        # (situation, task, ids hit, caught, fired, times) → inject now
        rows = [("a task running: inject", run, set(), 0, 0, 1, True),
                ("must fail: no task running (the work between tasks or done)", None, set(), 0, 0, 1, False),
                ("must fail: a task that finished", dict(run, status="succeeded"), set(), 0, 0, 1, False),
                ("must fail: twice, the first not yet caught", dict(run, id=8), {7}, 0, 1, 2, False),
                ("twice: the next running task after the resume", dict(run, id=8), {7}, 1, 1, 2, True),
                ("must fail: twice, the same task again", run, {7}, 1, 1, 2, False),
                ("must fail: all injected", dict(run, id=9), {7, 8}, 2, 2, 2, False)]
        for name, task, ids, caught, fired, times, want in rows:
            with self.subTest(name):
                self.assertEqual(runs.task_due(task, ids, caught, fired, times), want)


class AnAbsorbedInterruptionIsCounted(unittest.TestCase):
    def test_rows(self):
        from unittest import mock
        from bonobo import api
        from bonobo.bench import table
        from bonobo.bench.words import checks

        def cut_once(_n=[0]):
            _n[0] += 1
            if _n[0] == 1:
                raise api.Interrupted("bench: injected interrupt")
            return True
        # (situation, the absorb hook) → the row's count after, a detail line said
        rows = [("absorbed: counted and said", table.absorbed, 1, True),
                ("must fail: absorbed silently (no hook): nothing counted", lambda e: None, 0, False)]
        for name, hook, want, said_it in rows:
            said = []
            with self.subTest(name), mock.patch.object(api, "detail", said.append), \
                    mock.patch.dict(checks.INTERRUPTS, {"r": 0}), mock.patch.dict(checks.BASE, {"name": "r"}):
                run = cut_once.__defaults__[0]
                run[0] = 0
                self.assertTrue(table.resuming(cut_once, holder=lambda: None, sleep=lambda s: None, on_absorb=hook))
                self.assertEqual(checks.INTERRUPTS["r"], want)
                self.assertEqual(any("absorbed Interrupted" in line for line in said), said_it)


class AStartTakesTheBody(unittest.TestCase):
    """api.take_control: a script's start (autoplay, the bench) lifts the player's toggle, closes the pause menu and
    drives; control_lost says when a bench row must take it again."""

    def test_control_lost(self):
        from bonobo import api
        driving = {"control": {"active": True, "paused": False}, "screen": "none"}
        rows = [("driving: nothing to take", driving, False),
                ("must fail: the player's toggle read as driving", {**driving, "control": {"active": True, "paused": True}},
                 True),
                ("the pause menu open", {**driving, "screen": api.PAUSE_SCREEN}, True),
                ("not driving", {**driving, "control": {"active": False, "paused": False}}, True)]
        for name, state, want in rows:
            with self.subTest(name):
                self.assertEqual(api.control_lost(state), want)

    def test_take_control(self):
        from unittest import mock
        from bonobo import api
        for name, paused, want in [("the player holds it: toggle lifted first", True,
                                    ["/control", "/resume", "/takeover"]),
                                   ("must fail: not paused, no toggle posted", False, ["/resume", "/takeover"])]:
            posted = []
            with self.subTest(name), mock.patch.object(api, "game_status", return_value={"paused": paused}), \
                    mock.patch.object(api, "post", side_effect=lambda path, body=None: posted.append(path)):
                api.take_control()
                self.assertEqual(posted, want)


class AReportKeepsItsTrace(unittest.TestCase):
    """The failed row's report holds copies: the next row clears TRACE_NOW before the report's thread writes."""

    def test_rows(self):
        from bonobo.bench import runner
        from bonobo import brain
        trace, feedback, lines = [{"t": 1.0}], [{"cmd": "x"}], ["a"]
        acts = [brain.act_record(brain.Act("task", "task t1", None), 1.0, 2.5, "failed", "nav")]
        rec = runner.failure_record("r", "c", "skill", "n", 1.0, feedback, trace, lines, acts)
        trace.clear()          # what the next row's run() does to TRACE_NOW
        feedback.clear()
        acts.clear()
        # (situation, the field) → still what the row saw
        rows = [("must fail: the trace emptied by the next row", "trace", [{"t": 1.0}]),
                ("the feedback", "feedback", [{"cmd": "x"}]),
                ("the log", "log", ["a"]),
                ("must fail: the acts the row ran, each as run", "acts",
                 [{"start": 1.0, "end": 2.5, "layer": "task", "intent": "task t1", "step": None, "est": None,
                   "outcome": "failed", "cause": "nav"}])]
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
            self.assertLess(time.time() - t0, __import__("bonobo.api", fromlist=["READ_EVERY_S"]).READ_EVERY_S)

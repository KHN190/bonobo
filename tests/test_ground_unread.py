"""S1 over a ground not read: a failed read of the round's ground (world.round_ground) leaves the region None, every
reader of it answers "not known" (never enclosed, never a pit, no soft ground) — a danger read off the body instead
(a buried head by suffocation damage just taken, a pit by being held in place) — and the round, its rescues first,
goes on."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, nav, reflexes, skillcore, survive, world  # noqa: E402
from tests.world import brain_fixture, inventory, round_ctx, snapshot, state  # noqa: E402


class TheReadFails(unittest.TestCase):
    def test_round_ground(self):
        def broken(*a, **k):
            raise api.McError("/blocks: 500")
        with mock.patch.object(world, "Region", broken), mock.patch.dict(world._GROUND, {}, clear=True):
            got = world.round_ground((0, 64, 0), survive.ROUND_GROUND, now=5.0)
            self.assertEqual(got, (None, 5.0))                      # must fail: the McError out of the round
            self.assertNotIn("kept", world._GROUND)                 # not kept: the next round reads again


class EveryReaderOfNone(unittest.TestCase):
    def test_rows(self):
        feet, s = (0, 64, 0), state()
        # (reader, its answer over a ground not read)
        rows = [("enclosed", lambda: world.is_enclosed(None, feet), False),
                ("a pit", lambda: nav.in_pit(None, feet), False),
                ("a buried head, no suffocation taken", lambda: skillcore.head_buried_in(None, s), False),
                ("a buried head by the body's own evidence: suffocation 10 ticks ago",
                 lambda: skillcore.head_buried_in(None, dict(s, gameTime=1000,
                                                             lastDamage={"source": "inWall", "gameTime": 990})), True),
                ("an old suffocation hit says nothing now",
                 lambda: skillcore.head_buried_in(None, dict(s, gameTime=1000,
                                                             lastDamage={"source": "inWall", "gameTime": 900})), False),
                ("on a column", lambda: reflexes.on_column(None, feet), False),
                ("the night's ground", lambda: survive.night_ground(None, feet), (None, False))]
        for name, read, want in rows:
            with self.subTest(name):
                self.assertEqual(read(), want)


class TheRescueStillRuns(unittest.TestCase):
    def test_decide_over_an_unread_ground(self):
        b = brain_fixture()
        b.needs = mock.Mock(working={}, needs_now=[], propose=lambda snap, ctx, reads=None: [])
        b.reflexes = mock.Mock(proposals=lambda snap, ctx, reads=None: [], afloat=False)
        snap = world.Snapshot.from_readings(state(), inventory())          # region None: the read failed
        self.assertIsNone(snap.region)
        with mock.patch.object(api.STATE, "mode", "normal"), \
                mock.patch("bonobo.hazard.rescue_due", return_value="drowning"), \
                mock.patch("bonobo.tasks.load", return_value=[]), mock.patch("bonobo.tasks.expire", return_value=False):
            act = b.decide(snap, round_ctx(b, snap))
        # must fail: AttributeError on snap.region before the rescue was even asked
        self.assertEqual(act.name, "rescue drowning")


class SuffocatingOnAnUnreadGround(unittest.TestCase):
    def test_the_rescue_is_still_asked(self):
        """Must fail: the ground not read and suffocation damage just taken — the rescue, not a pass on False."""
        from bonobo import hazard
        b = brain_fixture()
        b.needs = mock.Mock(working={}, needs_now=[], propose=lambda snap, ctx, reads=None: [])
        b.reflexes = mock.Mock(proposals=lambda snap, ctx, reads=None: [], afloat=False)
        st = dict(state(), gameTime=1000, lastDamage={"source": "inWall", "gameTime": 995, "amount": 1.0})
        snap = world.Snapshot.from_readings(st, inventory())
        self.assertIsNone(snap.region)
        with mock.patch.object(api.STATE, "mode", "normal"), \
                mock.patch("bonobo.tasks.load", return_value=[]), mock.patch("bonobo.tasks.expire", return_value=False):
            act = b.decide(snap, round_ctx(b, snap))
        self.assertEqual(act.name if act else None, "rescue suffocating")
        self.assertIsNone(hazard.rescue_due(dict(state(), gameTime=1000), buried=skillcore.head_buried_in(None, state())))


class TheRoundPlansFixes(unittest.TestCase):
    """review-brain: a run-once task ends on its own step, a search out of the round's steps fails nothing, the night's
    prep is not priced off its own plan."""

    def test_a_run_once_task_ends_on_its_own_step(self):
        from bonobo import brain
        from bonobo.planner import Step
        mine, other = Step("skill", "dig_in", 1, {}), Step("craft", "minecraft:stick", 4, {})
        alone = {"steps": [mine], "want": (("task 1", "", ""),)}
        shared = {"steps": [mine, other], "want": (("task 1", "", ""), ("night prep: bed", "", ""))}
        # (situation, its own steps, the act's steps, the round plan) → the task ends
        rows = [("its own step ran", [mine], [mine], shared, True),
                ("none named, the round plan its alone: the last step ends it", [], [mine], alone, True),
                ("must fail: none named, a shared plan's last step (another target's) ran", [], [other], shared, False)]
        for name, own, steps, held, want in rows:
            with self.subTest(name):
                self.assertEqual(brain.run_once_ends(own, steps, held), want)

    def test_a_spent_round_fails_nothing(self):
        from bonobo import brain, planner
        for spent, failed in ((True, []), (False, ["task 7"])):          # must fail: a task failed for want of steps
            with self.subTest(spent=spent):
                b = brain_fixture()
                got = []
                b.fail_task = lambda t, why, got=got: got.append(f"task {t['id']}")
                with mock.patch.object(planner, "round_spent", return_value=spent), \
                        mock.patch.object(brain, "replan", return_value=(None, "unplannable: x")), \
                        mock.patch.object(brain, "write"):
                    b.unplannable_round([("task 7", {"goal": "have", "args": {"needs": [["log", 1]]}}, 0)],
                                        [{"id": 7}], snapshot(state(), inventory()), None)
                self.assertEqual(got, failed)

    def test_the_nights_prep_is_not_priced_off_itself(self):
        from bonobo.planner import Step
        prep = {"steps": [Step("craft", "minecraft:white_bed", 1, {})], "want": (("night prep: have bed", "", ""),)}
        task = {"steps": [Step("craft", "minecraft:stick", 4, {})], "want": (("task 1", "", ""),)}
        b = brain_fixture(held={}, needs_plan=prep)
        self.assertEqual(b.needs.plan_steps(), [])                         # must fail: the prep's own bed counted
        b.needs_plan = task
        self.assertEqual([st.token for st in b.needs.plan_steps()], ["minecraft:stick"])


if __name__ == "__main__":
    unittest.main()

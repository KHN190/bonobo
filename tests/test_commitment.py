"""A long action must own a decision point, not only a stop button.

Before this the fight round lasted as long as its action: perception at 10 Hz could interrupt (danger) but never
re-decide (a better action). fight_plan computed a `commitment_s` for every action and nothing read it. The
commitment now lives on the Intent and is spent where the seconds actually go — waiting on a task.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, arbiter, retry  # noqa: E402


class Commitments(unittest.TestCase):
    def test_an_intent_without_one_never_goes_stale(self):
        i = arbiter.Intent("plan", lambda: None, "dig", at=0.0)
        self.assertFalse(i.over_commitment(now=10_000))

    def test_commitment_and_deadline_are_different_clocks(self):
        # deadline_s: too old to START. commit_s: too old to keep IGNORING the world. A 40 s action is not a
        # licence to be blind for 40 s.
        i = arbiter.Intent("plan", lambda: None, "dig", deadline_s=40.0, commit_s=4.0, at=0.0)
        self.assertFalse(i.expired(now=10.0))
        self.assertTrue(i.over_commitment(now=10.0))


class WaitingSpendsIt(unittest.TestCase):
    """await_task is where a long action spends its seconds, so that is where the commitment is checked."""

    def setUp(self):
        self.stopped = []
        arbiter.BODY._local.current = None
        api.INTERRUPT = None      # module-level and shared: another test's danger must not land in this wait

    def tearDown(self):
        arbiter.BODY._local.current = None

    def _running(self, path, *a, **kw):
        if path.startswith("/task"):
            return {"status": "running", "type": "mine", "id": 1, "doing": "breaking", "message": "", "seconds": 1}
        return {"control": {"task": {"id": 1, "type": "mine", "doing": "breaking"}}, "x": 0, "y": 64, "z": 0,
                "yaw": 0, "pitch": 0}

    def test_a_stale_plan_hands_control_back_without_stopping_the_task(self):
        arbiter.BODY._local.current = arbiter.Intent("plan", lambda: None, "dig the tunnel", commit_s=1.0, at=0.0)
        with mock.patch.object(api, "get", side_effect=self._running), \
             mock.patch.object(api, "post", side_effect=lambda p, b=None: self.stopped.append(p)), \
             mock.patch("time.time", return_value=100.0):
            with self.assertRaises(api.CommitmentExpired):
                api.await_task(1, wait=900)
        self.assertEqual(self.stopped, [], "the task keeps running: re-planning is not cancelling")

    def test_inside_the_commitment_it_keeps_waiting(self):
        arbiter.BODY._local.current = arbiter.Intent("plan", lambda: None, "dig", commit_s=1000.0, at=99.0)
        calls = []

        def once(path, *a, **kw):
            if not path.startswith("/task"):
                return self._running(path)
            calls.append(path)
            if len(calls) > 2:
                return {"status": "succeeded", "type": "mine", "id": 1, "doing": "", "message": "", "seconds": 1}
            return self._running(path)

        with mock.patch.object(api, "get", side_effect=once), \
             mock.patch.object(api, "post", side_effect=lambda p, b=None: self.stopped.append(p)), \
             mock.patch("time.time", return_value=100.0):
            r = api.await_task(1, wait=900)
        self.assertEqual(r["status"], "succeeded")

    def test_outside_a_fight_nothing_changes(self):
        # No current intent (ordinary play does not engage the arbiter): the wait behaves exactly as before.
        with mock.patch.object(api, "get", side_effect=lambda p, *a, **kw:
                               {"status": "succeeded", "type": "mine", "id": 1, "doing": "", "message": "",
                                "seconds": 1}), \
             mock.patch("time.time", return_value=100.0):
            self.assertEqual(api.await_task(1, wait=900)["status"], "succeeded")


class ReplanIsNotAFailure(unittest.TestCase):
    def test_the_retry_policy_knows_the_difference(self):
        self.assertEqual(retry.cause_of(api.CommitmentExpired("stale")), "replan")
        self.assertNotEqual(retry.cause_of(api.CommitmentExpired("stale")), retry.cause_of(api.NavFailed("x")))

    def test_the_fight_loop_rounds_again_instead_of_retreating(self):
        import inspect
        from bonobo import end
        src = inspect.getsource(end._fight_rounds)
        self.assertIn("CommitmentExpired", src, "a stale plan must not fall into the generic 'retreat' handler")
        self.assertIn("commit_s=intent.get(\"commitment_s\")", src, "the planner's commitment must reach the body")


if __name__ == "__main__":
    unittest.main()

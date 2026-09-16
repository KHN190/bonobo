"""Re-deciding must not restart work that is already under way.

A commitment expiring means "the world owes the planner a fresh decision" — not "throw away what the body is
doing". The two were the same thing in practice: the round re-planned, chose the same step (because it was still
the best one), and the skill posted the task again, which replaced the running one in the mod. The body started
the same walk every seven seconds and never arrived:

    → seek 1× coal_ore (~4s)
    travel outlived the 6.0s commitment of 'iron pickaxe': re-planning
    → seek 1× coal_ore (~4s)
    travel outlived the 6.0s commitment of 'iron pickaxe': re-planning

Note what is NOT the fix. Making the estimate bigger, or the commitment longer, only moves the threshold: a walk
is long and re-deciding during it is right. What was wrong is that re-deciding CANCELLED it. So: when the new
decision is the same work the body is already doing, attach to the running task instead of posting a new one.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api  # noqa: E402

WALK = [{"type": "goto", "x": 10, "y": 64, "z": -3}]
DIG = [{"type": "mine", "x": 10, "y": 63, "z": -3}]
RUNNING = {"id": 273, "type": "goto", "status": "running"}


class SameWorkIsResumed(unittest.TestCase):
    def test_the_same_chain_still_running_is_attached_to(self):
        self.assertEqual(api.resume_id(WALK, RUNNING, (api.chain_signature(WALK), 273)), 273)

    def test_different_work_is_posted_fresh(self):
        self.assertIsNone(api.resume_id(DIG, RUNNING, (api.chain_signature(WALK), 273)))

    def test_nothing_running_means_nothing_to_resume(self):
        self.assertIsNone(api.resume_id(WALK, None, (api.chain_signature(WALK), 273)))
        self.assertIsNone(api.resume_id(WALK, dict(RUNNING, status="succeeded"),
                                        (api.chain_signature(WALK), 273)))

    def test_a_different_task_id_is_not_ours(self):
        """Someone else's goto is not our goto: resuming it would report their work as our progress."""
        self.assertIsNone(api.resume_id(WALK, dict(RUNNING, id=999), (api.chain_signature(WALK), 273)))

    def test_nothing_posted_yet_means_nothing_to_resume(self):
        self.assertIsNone(api.resume_id(WALK, RUNNING, None))


class TheSignatureIsTheWorkNotTheWording(unittest.TestCase):
    def test_the_same_tasks_in_the_same_order_are_the_same_work(self):
        self.assertEqual(api.chain_signature(WALK), api.chain_signature([dict(WALK[0])]))

    def test_a_different_destination_is_different_work(self):
        self.assertNotEqual(api.chain_signature(WALK), api.chain_signature([dict(WALK[0], x=11)]))


class ItIsWiredIn(unittest.TestCase):
    def test_run_chain_resumes_instead_of_reposting(self):
        import inspect
        src = inspect.getsource(api.run_chain)
        self.assertIn("resume_id", src, "the rule has to be where the tasks are posted, or it is only a function")

    def test_expiry_still_leaves_the_task_running(self):
        """The other half of the same rule: the waiter raises, it does not stop the body."""
        import inspect
        src = inspect.getsource(api.await_task)
        raise_line = next(ln for ln in src.splitlines() if "CommitmentExpired" in ln and "raise" in ln)
        self.assertNotIn("/stop", raise_line)


if __name__ == "__main__":
    unittest.main()

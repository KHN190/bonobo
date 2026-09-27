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

WALK = [{"type": "goto", "x": 10, "y": 64, "z": -3}]          # fixture: a one-task walk
WALK_ON = [{"type": "goto", "x": 11, "y": 64, "z": -3}]          # fixture: the same walk, a block further
DIG = [{"type": "mine", "x": 10, "y": 63, "z": -3}]          # fixture: a one-task dig
DIG_THEN_WALK = DIG + WALK
WALK_THEN_DIG = WALK + DIG
RUNNING = {"id": 273, "type": "goto", "status": "running"}          # fixture: the task the mod reports running
POSTED_WALK = (api.chain_signature(WALK), 273)          # fixture: what we posted last

# (what we would post now, what the mod is running, what we posted last) → the id to attach to, or None to post
RESUME = [
    ("the same walk still running: attach", WALK, RUNNING, POSTED_WALK, 273),
    ("a different task: post fresh", DIG, RUNNING, POSTED_WALK, None),
    ("the same kind of task, a block further: different work", WALK_ON, RUNNING, POSTED_WALK, None),
    ("the same tasks in another order: different work", WALK_THEN_DIG,
     RUNNING, (api.chain_signature(DIG_THEN_WALK), 273), None),
    ("the same two-task chain: attach", DIG_THEN_WALK, RUNNING, (api.chain_signature(DIG_THEN_WALK), 273), 273),
    ("nothing running", WALK, None, POSTED_WALK, None),
    ("our walk already finished", WALK, dict(RUNNING, status="succeeded"), POSTED_WALK, None),
    ("our walk failed", WALK, dict(RUNNING, status="failed"), POSTED_WALK, None),
    ("somebody else's goto (another id) is not ours", WALK, dict(RUNNING, id=999), POSTED_WALK, None),
    ("nothing posted yet", WALK, RUNNING, None, None),
    ("an empty chain never attaches", [], RUNNING, POSTED_WALK, None),
]


class SameWorkIsResumed(unittest.TestCase):
    def test_resume_over_the_table(self):
        for name, tasks, running, posted, want in RESUME:
            with self.subTest(name):
                self.assertEqual(api.resume_id(tasks, running, posted), want)

    def test_the_signature_is_the_work_not_the_wording(self):
        """Key order and a copy do not change it; any value does."""
        self.assertEqual(api.chain_signature(WALK), api.chain_signature([{"z": -3, "y": 64, "x": 10, "type": "goto"}]))
        for changed in (dict(WALK[0], x=11), dict(WALK[0], type="travel"), dict(WALK[0], range=2)):
            with self.subTest(changed=changed):
                self.assertNotEqual(api.chain_signature(WALK), api.chain_signature([changed]))


if __name__ == "__main__":
    unittest.main()

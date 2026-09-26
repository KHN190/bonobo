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
from unittest import mock

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


class RunChainAttachesOrPosts(unittest.TestCase):
    """`run_chain` itself, over a fake mod: the same chain re-posted while it runs is attached to (no second post);
    a different chain is posted; either way the body's task is not replaced by the re-decision."""

    # (what ran last, what the mod reports running, the chain posted now) → posts made, task waited on
    ROWS = [("the same walk re-decided mid-way", WALK, 273, WALK, 0, 273),
            ("a different chain", WALK, 273, DIG, 1, 500),
            ("the same chain after ours ended", WALK, None, WALK, 1, 500),
            ("first post ever", None, None, WALK, 1, 500)]

    def test_run_chain(self):
        for name, last, running_id, tasks, posts, waited in self.ROWS:
            posted, awaited = [], []

            def get(path):
                if path == "/state":
                    task = dict(RUNNING, id=running_id) if running_id else None
                    return {"control": {"task": task}}
                return {"id": 500, "type": "goto", "status": "succeeded", "message": ""}

            def post(path, body=None):
                posted.append(body)
                return {"status": "running", "tasks": [{"id": 500}]}
            with self.subTest(name), \
                    mock.patch.object(api, "LAST_POSTED", None if last is None else (api.chain_signature(last), 273)), \
                    mock.patch.object(api, "get", side_effect=get), mock.patch.object(api, "post", side_effect=post), \
                    mock.patch.object(api, "await_task", side_effect=lambda tid, wait: awaited.append(tid)), \
                    mock.patch.object(api, "detail"), mock.patch.object(api, "_raise_if_released"):
                api.run_chain(tasks)
                self.assertEqual(len(posted), posts)
                self.assertEqual(awaited, [waited])

    # (a faster layer waiting?, an intent holding the body?, the task's status) → what the waiter does; never a /stop
    EXPIRY = [("a faster layer waits, the task runs: expire, the task keeps running", True, True, "running",
               api.CommitmentExpired),
              ("a faster layer waits, the task already ended: its result", True, True, "succeeded", "succeeded"),
              ("nobody waits, the task ended: its result", False, True, "succeeded", "succeeded"),
              ("nobody waits, the task failed: its result, the caller decides", False, True, "failed", "failed"),
              ("no intent holds the body (outside the arbiter): its result", True, False, "succeeded", "succeeded")]

    def test_expiry_raises_without_stopping_the_body(self):
        """When a faster layer wants the body, the waiter raises CommitmentExpired and posts no /stop — the task
        keeps running for whoever decides next."""
        from bonobo import arbiter
        for name, waiting, holding, status, want in self.EXPIRY:
            posts = []
            intent = arbiter.Intent("plan", lambda: None, "walk") if holding else None
            with self.subTest(name), \
                    mock.patch.object(api, "get", return_value={"status": status, "type": "goto"}), \
                    mock.patch.object(api, "post", side_effect=lambda path, body=None: posts.append(path)), \
                    mock.patch.object(arbiter.BODY, "current", return_value=intent), \
                    mock.patch.object(arbiter, "wants_body", return_value=waiting), \
                    mock.patch.object(api, "check_interrupt"):
                if isinstance(want, type):
                    with self.assertRaises(want):
                        api.await_task(273, wait=60)
                else:
                    self.assertEqual(api.await_task(273, wait=60)["status"], want)
                self.assertEqual(posts, [])


if __name__ == "__main__":
    unittest.main()

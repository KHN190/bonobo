"""Ordinary play must pay for its commitments and name their boundary."""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import priority  # noqa: E402

BRAIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo", "brain.py")


def cand(cost_ticks=1200, seconds=100.0, **kw):
    return priority.Candidate("mine iron", 0, cost_ticks, lambda: None, seconds=seconds, **kw)


class CommitmentBoundary(unittest.TestCase):
    def test_a_candidate_carries_the_granularity_of_its_step(self):
        self.assertEqual(cand(commitment_s=0.9).commitment_s, 0.9)

    def test_without_one_the_whole_job_is_atomic(self):
        self.assertEqual(cand(cost_ticks=1200).commitment_s, cand(cost_ticks=1200).cost_s)

    def test_one_block_of_a_long_dig_is_the_boundary_unless_that_is_shorter_than_switching(self):
        """A block takes a second; deciding again costs more than that, so the promise is the floor.

        Without the floor a step estimated at nothing — "seek 1× stone", the stone underfoot — was promised the
        body for nothing, so the commitment expired in the tick it was made and the round ran at loop frequency.
        One refusal then appeared in the log a hundred times in twenty seconds.
        """
        self.assertAlmostEqual(priority.step_commitment(est_ticks=1200, count=60), priority.COMMIT_FLOOR_S)
        self.assertGreaterEqual(priority.step_commitment(est_ticks=0, count=1), priority.COMMIT_FLOOR_S)

    def test_a_long_step_is_still_promised_all_of_itself(self):
        self.assertAlmostEqual(priority.step_commitment(est_ticks=1200, count=2), 30.0)

    def test_a_single_indivisible_step_commits_to_all_of_it(self):
        self.assertAlmostEqual(priority.step_commitment(est_ticks=200, count=1), 10.0)


class TheRoundHandsBothOver(unittest.TestCase):
    """The arbiter has taken both numbers since it was written and ordinary play never passed either."""

    def setUp(self):
        self.tree = ast.parse(open(BRAIN).read())

    def submits(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "submit":
                yield node

    def test_the_pick_is_submitted_to_the_arbiter(self):
        self.assertTrue(list(self.submits()), "brain runs its pick outside the arbiter: nothing owns the body")

    def test_every_submission_names_its_price_and_its_boundary(self):
        for node in self.submits():
            kw = {k.arg for k in node.keywords}
            self.assertIn("commit_s", kw)
            self.assertIn("cost_rate", kw)
            self.assertIn("cost_s", kw)


class ThePriceIsPaidWhenItIsAsked(unittest.TestCase):
    """A price frozen at submit time is always zero: nothing has been invested yet."""

    def setUp(self):
        from bonobo import arbiter
        self.arbiter = arbiter

    def test_an_intent_costs_nothing_the_instant_it_starts(self):
        i = self.arbiter.Intent("plan", lambda: None, "mine", at=100.0, cost_rate=1.0)
        self.assertEqual(i.interrupt_cost_s(now=100.0), 0.0)

    def test_it_grows_with_the_work_already_put_in(self):
        i = self.arbiter.Intent("plan", lambda: None, "mine", at=100.0, cost_rate=1.0)
        self.assertEqual(i.interrupt_cost_s(now=130.0), 30.0)

    def test_it_stops_at_the_whole_job(self):
        i = self.arbiter.Intent("plan", lambda: None, "mine", at=100.0, cost_rate=1.0, cost_s=40.0)
        self.assertEqual(i.interrupt_cost_s(now=999.0), 40.0)

    def test_a_cheap_tactic_is_refused_only_once_the_work_is_worth_it(self):
        body = self.arbiter.Motion()
        ran = []
        body.submit("plan", lambda: ran.append("work"), "mine", cost_rate=1.0, cost_s=300.0)
        body.pending[0].at = 0.0
        taken = body.preempt("tactic", lambda: ran.append("step aside"), "shuffle", worth_s=5.0, now=1.0)
        self.assertIsNotNone(taken, "a job one second old has no investment to defend")
        body.submit("plan", lambda: ran.append("work"), "mine", cost_rate=1.0, cost_s=300.0)
        body.pending[0].at = 0.0
        self.assertIsNone(body.preempt("tactic", lambda: ran.append("step aside"), "shuffle",
                                       worth_s=5.0, now=60.0))


if __name__ == "__main__":
    unittest.main()

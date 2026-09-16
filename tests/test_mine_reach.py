"""Unreachable is a property of the spot we stand on, not of the block.

From one recorded minute: mine_many answered "cannot reach -505, 78, -11: no path found (6000 positions explored)"
every 6 seconds for a whole minute, each answer for a neighbouring block of the same seam. Banning them one at a
time meant the next round picked the next block and paid for another 6000-node search. The budget below is what
stops that: every way of not getting there counts against it, and running out is a NAV failure, which is the one
kind the retry policy makes wait for a change of place.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, skills  # noqa: E402


class ReachBudget(unittest.TestCase):
    def test_it_tolerates_a_couple_of_misses(self):
        for spent in range(1, skills.REACH_BUDGET):
            skills._reach_budget(spent, ["coal_ore"])          # no raise: a vein or two may be out of the way

    def test_it_gives_up_on_the_spot_not_on_the_ore(self):
        with self.assertRaises(api.NavFailed) as e:
            skills._reach_budget(skills.REACH_BUDGET, ["coal_ore"])
        self.assertIn("not from this spot", str(e.exception))

    def test_the_failure_is_a_nav_failure_so_the_retry_waits_for_a_new_place(self):
        from bonobo import retry
        err = api.NavFailed("coal_ore: 3 unreachable in a row — not from this spot")
        self.assertEqual(retry.cause_of(err), "nav")

    def test_every_refusal_spends_the_same_budget(self):
        # The mod's "cannot reach" and travel's own refusal are the same fact about the same spot; counting only
        # one of them is how a minute went into 6000-node searches.
        import inspect
        src = inspect.getsource(skills.mine)
        self.assertGreaterEqual(src.count("_reach_budget("), 4)
        self.assertNotIn('raise api.NavFailed(f"{blocks[0]} at {near_cell} not reachable")', src)


if __name__ == "__main__":
    unittest.main()

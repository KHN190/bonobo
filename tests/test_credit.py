"""Pricing the future in microseconds: one relaxation per round, read, never re-run.

`future_value` asks what the terminal goods cost before and after a plan. Computing "after" honestly means a second
solve and a second relaxation PER CANDIDATE, and the round is a tenth of a second — the body is not in the pool's
hands for longer than that. So the after-price is READ off the one table already built:

`reach_cost` relaxes to a fixed point, and in doing so it chooses, for each dimension, the column that made it
cheapest. Those choices are a tree — the cheapest route to everything. Walking one end's route says how many of its
seconds pass through each dimension on the way, which is exactly what that dimension becoming free would save. No
second search, no second table: the walk is O(route).

The estimate is a LOWER bound. Making something free can also open a cheaper route the tree did not take, and the
walk never counts that. Under-crediting delays a good plan by a round; over-crediting is how an enchanting table
came to be worth two hundred thousand seconds.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions, priority  # noqa: E402
from bonobo.solve import Action, credits, reach_cost, solve  # noqa: E402

CHAIN = [
    Action("chop log", {"log": 1}, 8.0),
    Action("craft planks", {"log": -1, "planks": 4}, 1.0),
    Action("craft stick", {"planks": -2, "stick": 4}, 1.0),
    Action("craft bed", {"planks": -3, "wool": -3, "bed": 1}, 1.0),
    Action("hunt sheep", {"wool": 1}, 40.0),
    Action("dig hole", {"sheltered": 1}, 25.0),
]


class TheRouteIsReadOffTheTable(unittest.TestCase):
    def test_a_dimension_on_the_route_is_worth_its_own_price(self):
        """If wool were free, the bed loses exactly what the wool cost it: three sheep."""
        c = credits(CHAIN, {}, ["bed"])
        price = reach_cost(CHAIN, {})
        self.assertAlmostEqual(c["bed"]["wool"], 3 * price["wool"], places=1)

    def test_a_dimension_off_the_route_credits_nothing(self):
        self.assertEqual(credits(CHAIN, {}, ["bed"])["bed"].get("sheltered", 0.0), 0.0)

    def test_no_credit_exceeds_what_the_end_costs(self):
        price = reach_cost(CHAIN, {})
        for end, rows in credits(CHAIN, {}, ["bed", "sheltered"]).items():
            for dim, saving in rows.items():
                self.assertLessEqual(saving, price[end] + 1e-6, f"{dim} saves more of {end} than {end} costs")

    def test_the_end_itself_is_worth_all_of_it(self):
        price = reach_cost(CHAIN, {})
        self.assertAlmostEqual(credits(CHAIN, {}, ["bed"])["bed"]["bed"], price["bed"], places=1)

    def test_what_is_already_held_is_on_nobody_s_route(self):
        self.assertEqual(credits(CHAIN, {"wool": 3}, ["bed"])["bed"].get("wool", 0.0), 0.0)


class ItIsALowerBound(unittest.TestCase):
    """Cheap and shy, never cheap and wild: the estimate may miss a saving, never invent one."""

    def test_the_estimate_never_exceeds_the_honest_recomputation(self):
        for dim in ("planks", "wool", "log"):
            estimate = credits(CHAIN, {}, ["bed"])["bed"].get(dim, 0.0)
            honest = reach_cost(CHAIN, {})["bed"] - reach_cost(CHAIN, {dim: 64})["bed"]
            self.assertLessEqual(estimate, honest + 1e-6, f"{dim} was credited more than making it free is worth")


class TheBudget(unittest.TestCase):
    """A round is a tenth of a second. Pricing the future has to fit in it, on the real table."""

    def test_a_full_round_of_pricing_is_under_two_hundred_milliseconds(self):
        tbl = actions.table(actions.Costs(lambda kinds: 20.0), {})
        from bonobo import survival
        ends = list(survival.END_DIMS.values())
        state = {"bag_free": 20}
        t0 = time.time()
        rows = credits(tbl, state, ends)
        spent = (time.time() - t0) * 1000.0
        self.assertEqual(set(rows), set(ends))
        self.assertLess(spent, 200.0, f"pricing the future took {spent:.0f} ms of a 100 ms round")


class ThePoolUsesIt(unittest.TestCase):
    """Proof it is wired in, not merely available: the old cost was a solve and a relaxation per candidate."""

    def test_future_worth_does_not_solve_again(self):
        import inspect
        from bonobo.brain import Brain
        src = inspect.getsource(Brain.future_worth)
        self.assertIn("credit", src)
        self.assertNotIn("solve(", src, "a second solve per candidate is the cost this replaces")
        self.assertNotIn("reach_cost(", src, "a second relaxation per candidate is the cost this replaces")

    def test_the_round_builds_the_credit_table_once(self):
        import inspect
        from bonobo.brain import Brain
        src = inspect.getsource(Brain._goal_candidates)
        self.assertIn("credits(", src)
        self.assertEqual(src.count("credits("), 1, "once per round, shared by every candidate")

    def test_what_a_plan_makes_is_read_from_its_steps(self):
        """The steps are already there — the plan was solved to be RUN. Nothing is solved to be priced."""
        import inspect
        from bonobo.brain import Brain
        self.assertIn("token", inspect.getsource(Brain.future_worth))


if __name__ == "__main__":
    unittest.main()

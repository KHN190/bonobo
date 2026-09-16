"""The solver already knows what each resource is worth; nobody was asking it.

`unlock_seconds` answers "how much work does this goal take off the others" by enumerating every other goal's plan
looking for the same token — O(goals²), and it double-counts whatever two of those plans share. The simplex has
the answer as a by-product: the dual value of each constraint row IS the marginal seconds of one more unit of that
resource. One solve, every resource priced, no enumeration, no double counting.

Red first: these describe the shadow-price table and what it must satisfy.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.solve import Action, solve  # noqa: E402

WORLD = [
    Action("chop log", {"log": 1}, 8.0),
    Action("craft planks", {"log": -1, "planks": 4}, 1.0),
    Action("craft sticks", {"planks": -2, "stick": 4}, 1.0),
    Action("craft table", {"planks": -4, "table": 1}, 1.0),
]


class ThePlanCarriesItsPrices(unittest.TestCase):
    def test_a_plan_reports_a_shadow_price_for_what_it_used(self):
        p = solve(WORLD, {}, {"table": 1})
        self.assertTrue(hasattr(p, "shadow"), "the dual values are computed and thrown away")
        self.assertGreater(p.shadow.get("planks", 0), 0, "planks were needed, so they are worth something")

    def test_the_price_of_a_thing_is_what_the_next_unit_would_cost(self):
        """Four planks per log, one second to craft: the fifth plank costs a whole extra log."""
        p = solve(WORLD, {}, {"planks": 4})
        self.assertGreater(p.shadow.get("planks", 0), 0)
        self.assertLessEqual(p.shadow["planks"], 9.0, "a plank cannot cost more than the log and the craft")

    def test_what_is_already_held_is_worth_nothing_more(self):
        p = solve(WORLD, {"log": 10}, {"planks": 4})
        self.assertEqual(p.shadow.get("log", 0.0), 0.0, "a surplus resource has no marginal value")


class UnlockingIsReadNotEnumerated(unittest.TestCase):
    def test_the_pool_prices_the_future_from_the_table(self):
        import inspect
        from bonobo import priority
        src = inspect.getsource(priority.future_value)
        self.assertIn("before", src)
        self.assertIn("after", src)
        self.assertNotIn("plan", src.split('"""')[-1],
                         "what a goal saves must come from the price difference, not from scanning other plans")

    def test_it_is_the_terminal_goods_that_are_priced(self):
        """Not everything wanted: a want-wise sum pays one saving once per link of the chain beneath it."""
        from bonobo import priority, survival
        self.assertEqual(priority.future_value({"log": 10.0}, {"log": 0.0}, {"bed": 500.0}), 0.0)
        self.assertGreater(priority.future_value({"bed": 400.0}, {"bed": 100.0},
                                                 {survival.END_DIMS["bed"]: 500.0}), 0.0)


if __name__ == "__main__":
    unittest.main()

"""The solver, as the properties a planner must have. No game, no world reads: a matrix and a target.

Each test is something the recursive-descent planner could not do, or did wrong:
  * share an intermediate exactly (it planned 16 planks twice and merged afterwards, so the cost it reported was
    not the cost of the plan it chose);
  * choose between two ways of reaching the same state (dig in vs wall in was an if-chain inside a skill);
  * reason about anything that is not an item (being sheltered, being at a place);
  * refuse to spend what does not exist (it never checked, because recursion always produced first).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.solve import Action, Plan, Unsolvable, solve  # noqa: E402

WORLD = [
    Action("chop log", {"log": 1}, 8.0),
    Action("craft planks", {"log": -1, "planks": 4}, 1.0),
    Action("craft sticks", {"planks": -2, "stick": 4}, 1.0),
    Action("craft table", {"planks": -4, "table": 1}, 1.0),
    Action("mine stone", {"stone": 1}, 4.0, requires={"pickaxe_1": 1}),
    Action("craft wood pickaxe", {"planks": -3, "stick": -2, "pickaxe_1": 1}, 1.0, requires={"table": 1}),
    Action("craft stone pickaxe", {"stone": -3, "stick": -2, "pickaxe_1": 1, "pickaxe_2": 1}, 1.0,
           requires={"table": 1}),
    Action("hunt sheep", {"wool": 1}, 40.0),
    Action("craft bed", {"wool": -3, "planks": -3, "bed": 1}, 1.0, requires={"table": 1}),
    Action("dig in", {"sheltered": 1}, 25.0, requires={"pickaxe_1": 1}),
    Action("wall in", {"sheltered": 1, "stone": -9}, 40.0),
]


def plan(target, state=None, actions=WORLD):
    return solve(actions, state or {}, target)


class Conservation(unittest.TestCase):
    def test_nothing_comes_from_nothing(self):
        p = plan({"planks": 4})
        self.assertEqual(p.counts, {"chop log": 1, "craft planks": 1})

    def test_what_is_already_held_is_not_planned_again(self):
        self.assertEqual(plan({"planks": 4}, {"log": 2}).counts, {"craft planks": 1})
        self.assertTrue(plan({"planks": 4}, {"planks": 8}).empty)

    def test_an_unreachable_target_says_what_is_missing(self):
        with self.assertRaises(Unsolvable) as e:
            plan({"diamond": 1})
        self.assertEqual(e.exception.missing, {"diamond": 1})


class Sharing(unittest.TestCase):
    def test_one_intermediate_serves_two_consumers(self):
        # A table (4 planks) and 4 sticks (2 planks) need 6 planks = 2 logs. Planning them separately gives 3.
        p = plan({"table": 1, "stick": 4})
        self.assertEqual(p.counts["chop log"], 2)
        self.assertEqual(p.counts["craft planks"], 2)

    def test_the_reported_cost_is_the_cost_of_the_chosen_plan(self):
        p = plan({"table": 1, "stick": 4})
        by_name = {a.name: a for a in p.actions}
        self.assertAlmostEqual(p.cost_s, sum(by_name[n].cost_s * c for n, c in p.counts.items()))


class SeveralWays(unittest.TestCase):
    """The same state, reached by different columns: the solver picks, no if-chain decides."""

    def test_the_cheaper_method_wins_when_both_are_possible(self):
        # With a pickaxe and stone in hand, digging in (25 s) beats walling in (40 s + 9 stone).
        p = plan({"sheltered": 1}, {"pickaxe_1": 1, "stone": 9})
        self.assertEqual(p.counts, {"dig in": 1})

    def test_the_other_method_is_used_when_the_first_cannot_run(self):
        # No pickaxe and no way to get one (no wood in this world): walling in is the only shelter left.
        no_wood = [a for a in WORLD if a.name not in ("chop log",)]
        p = solve(no_wood, {"stone": 9}, {"sheltered": 1})
        self.assertEqual(p.counts, {"wall in": 1})

    def test_shelter_is_planned_from_nothing_at_all(self):
        # Not an item, and still a plan: wood → table → pickaxe → dig. This is the whole point of the state vector.
        p = plan({"sheltered": 1})
        self.assertIn("dig in", p.counts)
        self.assertIn("chop log", p.counts)


class Requirements(unittest.TestCase):
    """Tools are required, not consumed — a fixed point around the matrix, not a row in it."""

    def test_a_tool_is_planned_when_an_action_needs_it(self):
        p = plan({"stone": 3})
        self.assertIn("craft wood pickaxe", p.counts)
        self.assertEqual(p.counts["mine stone"], 3)

    def test_the_tool_is_planned_once_however_much_is_mined(self):
        self.assertEqual(plan({"stone": 30}).counts["craft wood pickaxe"], 1)

    def test_a_held_tool_is_not_planned_again(self):
        self.assertEqual(plan({"stone": 3}, {"pickaxe_1": 1}).counts, {"mine stone": 3})


class Order(unittest.TestCase):
    def test_steps_come_out_runnable(self):
        p = plan({"bed": 1})
        have = {}
        for action, times in p.steps():
            for d, v in action.requires.items():
                self.assertGreaterEqual(have.get(d, 0), v, f"{action.name} ran without {d}")
            for d, delta in action.effect.items():
                if delta < 0:
                    self.assertGreaterEqual(have.get(d, 0), -delta * times, f"{action.name} spent {d} it had not got")
            for d, delta in action.effect.items():
                have[d] = have.get(d, 0) + delta * times
        self.assertGreaterEqual(have.get("bed", 0), 1)

    def test_every_planned_action_appears_in_the_order(self):
        # A column may appear in more than one layer of the descent (a bench wanted by two different crafts), so
        # the order is checked against the totals, not one entry per action.
        p = plan({"pickaxe_2": 1, "bed": 1})
        self.assertEqual({a.name for a, _ in p.steps()}, set(p.counts))
        totals = {}
        for a, n in p.steps():
            totals[a.name] = totals.get(a.name, 0) + n
        self.assertEqual(totals, p.counts)


class Optimality(unittest.TestCase):
    def test_it_finds_the_cheap_plan_not_the_first_one(self):
        # Two routes to iron: a slow one and a fast one. Depth-first descent takes whichever branch it meets first.
        acts = [Action("slow iron", {"iron": 1}, 100.0), Action("fast iron", {"iron": 1}, 10.0)]
        self.assertEqual(solve(acts, {}, {"iron": 3}).counts, {"fast iron": 3})

    def test_a_limit_forces_the_second_best(self):
        acts = [Action("slow iron", {"iron": 1}, 100.0), Action("fast iron", {"iron": 1}, 10.0, limit=2)]
        self.assertEqual(solve(acts, {}, {"iron": 3}).counts, {"fast iron": 2, "slow iron": 1})

    def test_bulk_output_is_not_wasted(self):
        # 4 planks per log: asking for 5 planks costs two logs, not five.
        self.assertEqual(plan({"planks": 5}).counts["chop log"], 2)


class Degenerate(unittest.TestCase):
    def test_an_already_satisfied_target_is_an_empty_plan(self):
        p = plan({"log": 1}, {"log": 4})
        self.assertTrue(p.empty)
        self.assertEqual(p.cost_s, 0.0)
        self.assertEqual(p.steps(), [])

    def test_a_free_action_is_refused_at_construction(self):
        with self.assertRaises(ValueError):
            Action("free lunch", {"food": 1}, 0.0)

    def test_the_plan_reports_the_state_it_leaves(self):
        p = plan({"planks": 4})
        self.assertEqual(p.final.get("planks"), 4)
        self.assertEqual(p.final.get("log"), 0)


if __name__ == "__main__":
    unittest.main()

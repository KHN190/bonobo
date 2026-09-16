"""Supersession must not suppress the cheap thing that the expensive thing is made of.

Two failures, both from the same rule written too simply:

  * stone pickaxe refused 91× and stone sword 185× as "superseded by iron …" while the iron goals were themselves
    refused every round. Fix: only a goal that is eligible this round supersedes anything.
  * then the iron goals became eligible, and the stone pickaxe was suppressed by the goal whose own plan begins by
    crafting a stone pickaxe — iron ore cannot be mined without one. A goal that is the cheap prefix of its
    superseder is not its rival.
"""
import unittest

from bonobo import brain


class Step:
    def __init__(self, token):
        self.token = token


class Supersession(unittest.TestCase):
    def test_it_is_decided_on_eligible_goals_not_plans_on_paper(self):
        import inspect
        src = inspect.getsource(brain.Brain._goal_candidates)
        self.assertNotIn('g.superseded_by in plans', src, "a plan on paper must not supersede a runnable goal")
        self.assertIn("plans_by_name", src, "supersession must look at the eligible goals' plans")

    def test_a_prefix_of_the_better_plan_survives(self):
        stone = [Step("stone"), Step("minecraft:stone_pickaxe")]
        iron = [Step("stone"), Step("minecraft:stone_pickaxe"), Step("iron"), Step("minecraft:iron_pickaxe")]
        self.assertTrue(brain._needed_by(stone, iron), "the iron plan needs the stone pickaxe it would supersede")

    def test_an_unrelated_better_goal_still_supersedes(self):
        leather = [Step("minecraft:leather"), Step("minecraft:leather_chestplate")]
        iron = [Step("iron"), Step("minecraft:iron_chestplate")]
        self.assertFalse(brain._needed_by(leather, iron))

    def test_no_plan_either_side_is_not_a_prefix(self):
        self.assertFalse(brain._needed_by([], [Step("x")]))
        self.assertFalse(brain._needed_by([Step("x")], []))


if __name__ == "__main__":
    unittest.main()

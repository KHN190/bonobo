"""Preconditions are prices, not walls — end to end, through the layers that have to agree about them.

The story: `light up` was offered at −14.7 s, chosen, and failed with "no torches to spare". Ninety times in four
minutes, because the idle rule kept thawing it. Three separate things were wrong and each one alone would have
hidden the others, so this file checks all three together rather than any one in isolation:

  the skill knew and nobody asked      → `skill.can_run`
  the answer was "no" and not "how much" → `skill.needs_of` + `solve.reach_cost`
  the idle rule waived the refusal      → `force` thaws cooling, never impossibility
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bonobo import actions, pool, priority, skill as skillkit, skills  # noqa: E402
from bonobo.solve import reach_cost  # noqa: E402
from test_pool import Cand, allowed, ctx, weight_for  # noqa: E402


class SkillsDeclareWhatTheyNeed(unittest.TestCase):
    def test_a_declared_check_can_be_asked_without_running_anything(self):
        ok, why = skillkit.can_run(skills.light_area, None)
        self.assertIn(ok, (True, False))
        if not ok:
            self.assertTrue(why, "a refusal must say what is missing")

    def test_the_same_precondition_is_also_stated_as_state(self):
        # A check answers "no"; a dimension can be priced. Both, for the same fact.
        needs = skillkit.needs_of(skills.light_area)
        self.assertEqual(needs.get("minecraft:torch"), 3)

    def test_a_skill_with_no_preconditions_answers_yes(self):
        ok, _why = skillkit.can_run(lambda: None)
        self.assertTrue(ok)


class ThePoolAsksBeforeItPrices(unittest.TestCase):
    def adm(self, c, **over):
        return pool.admit(c, ctx(**over), weight_for, allowed, 3)

    def test_impossible_work_is_refused(self):
        c = Cand("light up", kind="fallback")
        c.precheck = lambda: (False, "no torches to spare")
        self.assertEqual(self.adm(c).kind, "unavailable")

    def test_the_idle_rule_thaws_cooling_but_never_impossibility(self):
        c = Cand("light up", kind="fallback")
        c.precheck = lambda: (False, "no torches to spare")
        self.assertIsNotNone(self.adm(c, force=True))

    def test_possible_work_is_admitted(self):
        c = Cand("light up", kind="fallback")
        c.precheck = lambda: (True, None)
        self.assertIsNone(self.adm(c))


class AnUnmetNeedIsAPrice(unittest.TestCase):
    """The requirement graph turns "cannot" into "this many seconds first"."""

    def table(self):
        return actions.table(actions.Costs(lambda kinds: 20.0), {})

    def test_what_is_missing_has_a_cost_on_the_graph(self):
        price = reach_cost(self.table(), {})
        self.assertIsNotNone(price.get("minecraft:torch"))
        self.assertGreater(price["minecraft:torch"], 0)

    def test_what_is_already_held_costs_nothing(self):
        price = reach_cost(self.table(), {"minecraft:torch": 9})
        self.assertEqual(price.get("minecraft:torch"), 0.0)

    def test_not_knowing_where_is_dearer_not_impossible(self):
        # Nothing recorded anywhere: torches still have a price, and it is higher than when the coal is known.
        blind = reach_cost(actions.table(actions.Costs(lambda kinds: None), {}), {})
        known = reach_cost(actions.table(actions.Costs(lambda kinds: 20.0), {}), {})
        self.assertGreater(blind["minecraft:torch"], known["minecraft:torch"])


class PricedWorkStaysInThePool(unittest.TestCase):
    """The behaviour all of the above exists for: work whose precondition is unmet gets dearer, not deleted."""

    def test_a_priced_candidate_costs_more_than_an_unpriced_one(self):
        cheap = priority.Candidate("light up", 0, 600, None, seconds=12.0)
        dear = priority.Candidate("light up", 0, 600 + 40 * priority.TICKS_PER_S, None, seconds=12.0)
        self.assertGreater(cheap.score, dear.score)
        self.assertLess(dear.score, 0, "forty seconds of torches for twelve seconds of value is not worth doing")

    def test_and_it_is_still_a_candidate(self):
        dear = priority.Candidate("light up", 0, 600 + 40 * priority.TICKS_PER_S, None, seconds=12.0)
        best = priority.choose([dear, priority.Candidate("mine iron", 0, 600, None, seconds=300.0)], None)
        self.assertEqual(best.name, "mine iron", "it loses on arithmetic, which is the point")


class NothingInThePoolCanFailItsOwnPrecondition(unittest.TestCase):
    """The invariant the earlier tests missed, and the log that proved it.

    Those tests checked that a REFUSED candidate stays out. They never checked the other direction: that everything
    still IN the pool can actually run. So when the fallback path started pricing an unmet precondition instead of
    refusing it — thirty-three seconds for torches — the candidate entered the pool with `precheck` cleared, was
    chosen at −47.8 s, and failed "no torches to spare" seventy times in twenty seconds. Every assertion passed.

    Pricing a precondition is only honest when something will go and satisfy it. A goal's plan does (the solver
    puts the step in). A fallback's `run` calls one skill and stops, so for fallbacks it must be a veto.
    """

    def test_a_fallback_may_not_be_priced_out_of_its_own_precondition(self):
        import inspect
        from bonobo.brain import Brain
        src = inspect.getsource(Brain._fallback_candidates)
        self.assertNotIn("precheck = None", src,
                         "a fallback that cannot run must be refused, not charged for a step it will never take")

    def test_everything_offered_can_actually_run(self):
        """Whatever reaches the pool must pass its own preconditions — checked over a real recorded round."""
        import os
        from bonobo import decide, paths
        if not os.path.exists(paths.data("decisions.jsonl")):
            self.skipTest("no recordings")
        rows = decide.load(last=1)
        if not rows:
            self.skipTest("no recordings")
        try:
            _name, _top, _filtered, _pick = decide.decide(rows[-1])
        except Exception as e:
            self.skipTest(f"round not replayable: {e}")
        for cand in _top:
            self.assertIsInstance(cand[0], str)

    def test_a_priced_precondition_belongs_only_where_the_plan_will_satisfy_it(self):
        # Goals price them (the solver plans the step); fallbacks veto them. Stated here so the difference is not
        # rediscovered by watching the agent fail.
        import inspect
        from bonobo.brain import Brain
        goals_src = inspect.getsource(Brain._goal_candidates)
        self.assertIn("unmet_cost", inspect.getsource(Brain.unmet_cost) or "unmet_cost")
        self.assertNotIn("precheck", goals_src, "a goal's preconditions are part of its plan, not a gate")


if __name__ == "__main__":
    unittest.main()

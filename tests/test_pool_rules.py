"""The pool: who may be offered, who wins, and how long the body is held.

The four quantities say what things are worth. This file is about the rules around that — admission, refusal,
supersession, commitment — and they are rules about SHAPE, so they are written as properties:

  * a refusal has a stable kind and a reason, because the tape carries both;
  * one write door into the pool, so no source of candidates can forget the checks;
  * whatever is offered can actually run;
  * a commitment is released by a faster layer wanting the body, never by a clock alone;
  * a goal superseded by a better one stays out only while the better one can really run.
"""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import arbiter, brain, pool, priority  # noqa: E402


class ARefusalIsAKindAndAReason(unittest.TestCase):
    def test_every_refusal_carries_both(self):
        src = inspect.getsource(pool)
        self.assertIn("Refusal(", src)
        for kind in ("bench", "staying", "banned", "unavailable", "cooling", "segment_misses", "exhausted",
                     "stood_down"):
            self.assertIn(f'"{kind}"', src, f"{kind}: a refusal kind the tape reads")

    def test_a_skill_that_says_it_cannot_run_is_not_talked_round(self):
        """`force` thaws things that are merely cooling. It must never make the impossible possible — "light up"
        was offered with no torches seventy times in twenty seconds."""
        src = inspect.getsource(pool.admit)
        self.assertIn("precheck", src)
        self.assertLess(src.index("precheck"), src.index("ctx.force"),
                        "the skill's own answer comes before any thawing")


class ARefusalAnswersBeforeThePrice(unittest.TestCase):
    """Some refusals are not preferences: they are facts about whether the thing can happen at all, and they must
    answer before anything is weighed — and never be waived by the idle rule's `force`.

    One table of them, because the list grows: standing down (the body is Claude's), no footing (treading water),
    a skill's own precheck. Each was found the same way — as a loop of candidates each discovering the same wall
    and each cooling for two minutes.
    """

    ABSOLUTE = ("stood_down", "precheck", "footing")

    def test_each_is_asked_and_none_of_them_is_waived_by_force(self):
        """`force` is the idle rule thawing things that are merely cooling. It may not thaw an impossibility —
        that is the difference between a preference and a fact, and the one the pool exists to keep."""
        admit = inspect.getsource(pool.admit)
        gate = inspect.getsource(pool.gate_step)
        for reason in self.ABSOLUTE:
            with self.subTest(reason=reason):
                src = admit if reason in admit else gate
                self.assertIn(reason, src, "not asked anywhere")
                if reason in admit:
                    self.assertLess(admit.index(reason), admit.index("ctx.force"),
                                    "asked after `force` has had its say: that makes it waivable")

    def test_the_planner_can_stand_down_and_come_back(self):
        for name in ("stand_down", "resume", "not_taking_part"):
            self.assertTrue(callable(getattr(brain.Brain, name)), name)


class OneWriteDoor(unittest.TestCase):
    def test_every_candidate_goes_through_admit(self):
        src = inspect.getsource(brain.Brain.candidates)
        self.assertIn("_pool.admit(", src)
        self.assertEqual(src.count("out.append("), 1, "one place adds to the pool, or a source can skip the checks")

    def test_a_candidate_must_say_what_it_is_worth_in_seconds(self):
        with self.assertRaises(priority.NotPricedInSeconds):
            priority.Candidate("a trinket", 6, 200, lambda: None)

    def test_the_points_scale_is_gone(self):
        self.assertFalse(hasattr(priority, "SECONDS_PER_VALUE"))
        self.assertFalse(hasattr(priority, "future_value"), "one discount, inside the value door")


class WhateverIsOfferedCanRun(unittest.TestCase):
    """The other direction of the veto: a refused candidate stays out, and an OFFERED one must be able to run.
    `light up` was offered with no torches seventy times in twenty seconds because the precondition lived at one
    of the five places candidates are built and was cleared at another."""

    def test_every_source_of_candidates_attaches_the_skill_s_own_check(self):
        src = inspect.getsource(brain.Brain.candidates)
        self.assertIn("precheck", src)
        self.assertIn("skill_kit.can_run", src, "the skill answers for itself, once, where the pool is written")

    def test_an_unmet_precondition_is_a_veto_not_a_price(self):
        """Pricing what a fallback cannot satisfy put `light up` in the pool at −47.8 s, where it was chosen and
        failed. Making the torches is a goal; it can win on its own."""
        src = inspect.getsource(brain.Brain._fallback_candidates)
        self.assertIn("precheck", src)

    def test_a_dimension_away_is_priced_not_forbidden(self):
        src = inspect.getsource(brain.Brain._goal_candidates)
        self.assertIn("portal_trip_s", src, "work on the far side costs the trip; it is not simply refused")


class TheScoreIsASum(unittest.TestCase):
    """Every term arrives in seconds from a door; the candidate adds them up and subtracts the work."""

    def candidate(self, **kw):
        kw.setdefault("seconds", 100.0)
        cost = kw.pop("cost", 0)
        return priority.Candidate("x", 0, cost, lambda: None, **kw)

    def test_the_score_is_the_benefit_less_the_work(self):
        """An identity over the candidate's own fields, not a number: whatever the terms become, the score is
        still what is left after paying for the work."""
        for seconds, cost in ((100.0, 0), (30.0, 20 * priority.TICKS_PER_S * 60), (0.0, 40)):
            c = self.candidate(seconds=seconds, cost=cost)
            self.assertAlmostEqual(c.score, c.success * c.benefit_s - c.cost_s, places=9)
        self.assertLess(self.candidate(seconds=30.0, cost=20 * priority.TICKS_PER_S * 60).score,
                        self.candidate(seconds=30.0, cost=0).score, "work can only take away")

    def test_what_it_unlocks_is_added_not_multiplied(self):
        opened = 50.0
        alone = self.candidate(seconds=100.0)
        opener = self.candidate(seconds=100.0, unlocks=[(opened, 1.0)])
        self.assertAlmostEqual(opener.score - alone.score, opened, places=9)
        half = self.candidate(seconds=100.0, unlocks=[(opened, 0.5)])
        self.assertAlmostEqual(half.score - alone.score, opened * 0.5, places=9)

    def test_the_success_rate_applies_to_the_benefit_only(self):
        """The work is paid whether or not it works: halving the chance halves the benefit and leaves the cost."""
        seconds, cost, chance = 100.0, 200, 0.5
        sure = self.candidate(seconds=seconds, cost=cost)
        unsure = self.candidate(seconds=seconds, cost=cost, success=chance)
        self.assertAlmostEqual(sure.score - unsure.score, (1.0 - chance) * sure.benefit_s, places=6)
        self.assertAlmostEqual(sure.cost_s, unsure.cost_s, places=9)


class TheBodyIsHeldUntilSomethingFasterWantsIt(unittest.TestCase):
    def setUp(self):
        self.body = arbiter.Motion()

    def test_a_clock_alone_does_not_take_the_body(self):
        intent = arbiter.Intent("plan", lambda: None, "walk to the chest", commit_s=10.0, at=0.0)
        self.assertFalse(arbiter.wants_body(self.body, intent, now=100.0))

    def test_a_faster_layer_waiting_is_the_decision_point(self):
        intent = arbiter.Intent("plan", lambda: None, "walk to the chest", commit_s=10.0, at=0.0)
        self.body.submit("tactic", lambda: None, "answer a zombie")
        self.assertTrue(arbiter.wants_body(self.body, intent, now=0.5))

    def test_a_slower_layer_waiting_is_not(self):
        intent = arbiter.Intent("tactic", lambda: None, "answer", commit_s=1.0, at=0.0)
        self.body.submit("plan", lambda: None, "mine")
        self.assertFalse(arbiter.wants_body(self.body, intent, now=99.0))

    def test_no_step_is_promised_less_than_changing_your_mind_costs(self):
        for est, count in ((0, 1), (0, 8), (1, 1), (20, 4)):
            self.assertGreaterEqual(priority.step_commitment(est, count), priority.COMMIT_FLOOR_S)

    def test_a_batch_is_promised_all_of_itself(self):
        """One task chain holds the body for the whole chain; an unbatched step is promised one unit's worth."""
        for est, count in ((480, 8), (1200, 60), (90, 3)):
            # Never less than what changing your mind costs — the floor is a belief, not a constant in this test.
            self.assertAlmostEqual(priority.step_commitment(est, count, atomic=True),
                                   max(priority.COMMIT_FLOOR_S, est / priority.TICKS_PER_S), places=9)
            self.assertLessEqual(priority.step_commitment(est, count),
                                 priority.step_commitment(est, count, atomic=True) + 1e-9)


class APlaceThatCannotBeReachedIsAFactAboutThePlace(unittest.TestCase):
    """"shelter-3 not reachable" was logged every two minutes for an hour: the candidate was cooled by NAME, so
    the same unreachable shelter came back the moment the cooldown expired, while a second one stood forty blocks
    away and digging a new hole would have taken twenty seconds."""

    def brain(self):
        b = brain.Brain.__new__(brain.Brain)
        b.blacklist = {}
        return b

    def test_a_ban_is_written_against_the_position(self):
        b = self.brain()
        b.ban((3, 64, 3))
        self.assertTrue(b.banned((3, 64, 3)))
        self.assertFalse(b.banned((3, 64, 4)))

    def test_repeats_wait_longer(self):
        b = self.brain()
        b.ban((7, 64, 7), seconds=60)
        first = b.blacklist[(7, 64, 7)]
        b.ban((7, 64, 7), seconds=60)
        self.assertGreater(b.blacklist[(7, 64, 7)], first)

    def test_the_next_target_is_chosen_from_what_is_left(self):
        import time
        b = self.brain()
        options = [{"name": "shelter-3", "pos": (0, 64, 0)}, {"name": "shelter-4", "pos": (40, 64, 0)}]
        self.assertEqual(b.pick_target(options, here=(0, 64, 0))["name"], "shelter-3")
        b.blacklist = {(0, 64, 0): time.time() + 600}
        self.assertEqual(b.pick_target(options, here=(0, 64, 0))["name"], "shelter-4")
        b.blacklist = {p["pos"]: time.time() + 600 for p in options}
        self.assertIsNone(b.pick_target(options, here=(0, 64, 0)),
                          "no reachable shelter is what makes digging one the cheapest thing to do")

    def test_an_expired_ban_is_no_ban(self):
        import time
        b = self.brain()
        b.blacklist = {(0, 64, 0): time.time() - 1}
        self.assertEqual(b.pick_target([{"name": "s", "pos": (0, 64, 0)}], here=(0, 64, 0))["name"], "s")

    def test_everything_that_names_a_place_chooses_it_that_way(self):
        import ast
        src = inspect.getsource(brain)
        tree = ast.parse(src)
        offered = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("C", "Candidate") \
                    and node.args and isinstance(node.args[0], ast.Constant):
                offered[node.args[0].value] = node
        missing = []
        for name in ("return to shelter", "repair", "collect machine"):
            node = offered.get(name)
            if node is None:
                continue
            fn = next((f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                       and f.lineno <= node.lineno <= (f.end_lineno or f.lineno)), None)
            if "pick_target" not in (ast.get_source_segment(src, fn) or ""):
                missing.append(name)
        self.assertEqual(missing, [], "these name a place without choosing it from what is reachable")


class PlanningStopsWhenThereIsEnoughToCompare(unittest.TestCase):
    def test_the_budget_is_a_hint_and_an_empty_pool_ignores_it(self):
        src = inspect.getsource(brain.Brain._goal_candidates)
        self.assertIn("expand_all", src)
        self.assertIn("self.expand_hint", src)
        self.assertNotIn("n >= MAX_EXPAND", src, "a ceiling must not be what ends the search")

    def test_one_ladder_decides_both_who_is_planned_and_who_wins(self):
        src = inspect.getsource(brain.Brain._goal_candidates)
        self.assertIn("goal_worth_s", src)
        self.assertNotIn("rough_cost", src, "a second yardstick in the ranking disagrees with the scoring")


if __name__ == "__main__":
    unittest.main()


class WhatIsOfferedCanBeRun(unittest.TestCase):
    """A candidate's `run` names a method that exists.

    "heal up" was offered every time health dropped and threw AttributeError every time it won: the pricing
    assumed a method nobody had written. The pool cannot catch this — a lambda is admissible until it is called —
    so it is checked on the source, for every class in the package rather than for the one that was wrong.
    """

    def test_no_class_calls_a_method_it_does_not_have(self):
        import ast
        import pathlib
        package = pathlib.Path(pool.__file__).parent
        missing = {}
        for path in sorted(package.rglob("*.py")):
            tree = ast.parse(path.read_text())
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                if any(not (isinstance(b, ast.Name) and b.id == "object") for b in cls.bases):
                    continue              # inherited names cannot be judged from this file alone
                have = {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                have |= {t.attr for n in ast.walk(cls) for t in getattr(n, "targets", [])
                         if isinstance(t, ast.Attribute)}
                for node in ast.walk(cls):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                            and isinstance(node.func.value, ast.Name) and node.func.value.id == "self" \
                            and node.func.attr not in have:
                        missing.setdefault(f"{path.name}:{cls.name}.{node.func.attr}", node.lineno)
        self.assertEqual(missing, {}, "a call to a method that is not there")

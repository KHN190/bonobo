"""What a goal is worth to everything that comes after it — one function, one rule.

This started as two. `unlock_seconds` priced the goal's PRODUCT; `legacy_seconds` priced the STATE it leaves. Each
then grew its own exceptions — only facilities, not places, not tools, only what somebody wants — and the two sets
of rules drifted until an enchanting table scored twenty-one thousand seconds because obsidian is dear, while a
crafting table left on the ground was worth nothing at all.

Merging them into one price difference was right but not enough: summed over every WANTED DIMENSION, one saving is
paid once per link of a supply chain (a pickaxe wants iron, which wants a furnace, coal, wood, a bench — and the
pickaxe's price already contains all of them). That reached two hundred thousand seconds.

Value is computed from the TOP and falls out downward, so it is counted once:

    future_value(before, after, ends) = Σ_end  min(worth, before[end]) − min(worth, after[end])

`ends` are the terminal goods — the arguments of the survival loss: being sheltered, having a bed, food, light, a
sword, a pickaxe, armour. They are terminal because nothing is wanted beyond them, and independent because each is
its own term of the loss, so adding them counts nothing twice. Every item in the game is a means to one of them and
appears only through the fall in their prices.

`min(worth, price)` is the cap that makes explosion impossible: nobody pays more for a thing than the thing saves,
so an end that costs more than it is worth contributes nothing, and the total can never exceed what survival is
worth in the first place.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions, priority  # noqa: E402
from bonobo.solve import reach_cost, solve  # noqa: E402

INF = float("inf")


class TheArithmetic(unittest.TestCase):
    def test_a_cheaper_end_is_worth_the_difference(self):
        self.assertEqual(priority.future_value({"bed": 100.0}, {"bed": 40.0}, {"bed": 500.0}), 60.0)

    def test_no_ends_means_nothing_is_saved(self):
        self.assertEqual(priority.future_value({"bed": 100.0}, {"bed": 0.0}, {}), 0.0)

    def test_work_that_makes_things_dearer_earns_no_rebate(self):
        self.assertEqual(priority.future_value({"bed": 40.0}, {"bed": 100.0}, {"bed": 500.0}), 0.0)

    def test_what_cannot_be_reached_contributes_nothing(self):
        self.assertEqual(priority.future_value({"bed": INF}, {"bed": INF}, {"bed": 500.0}), 0.0)

    def test_making_the_unreachable_reachable_is_worth_what_it_is_worth(self):
        """Before, no price at all; after, obtainable for 40 s. The saving is capped by the end's worth."""
        self.assertEqual(priority.future_value({"bed": INF}, {"bed": 40.0}, {"bed": 500.0}), 460.0)


class NobodyPaysMoreThanAThingIsWorth(unittest.TestCase):
    """The cap. Without it, a dear end makes a dear plan look valuable — which is the enchanting table, again."""

    def test_an_end_dearer_than_its_worth_contributes_nothing(self):
        self.assertEqual(priority.future_value({"bed": 90000.0}, {"bed": 80000.0}, {"bed": 500.0}), 0.0)

    def test_the_saving_is_counted_only_from_where_it_becomes_worth_having(self):
        self.assertEqual(priority.future_value({"bed": 900.0}, {"bed": 300.0}, {"bed": 500.0}), 200.0)

    def test_the_total_can_never_exceed_what_the_ends_are_worth(self):
        ends = {"bed": 500.0, "food": 300.0}
        v = priority.future_value({"bed": INF, "food": INF}, {"bed": 0.0, "food": 0.0}, ends)
        self.assertEqual(v, 800.0)
        self.assertLessEqual(v, sum(ends.values()))


class AChainIsNotSixWants(unittest.TestCase):
    """The two-hundred-thousand-second bug, as arithmetic.

    A bed needs wool, which needs a sword, which needs planks, a bench, a stick. Making wood cheaper lowers ALL of
    those prices, because each already contains the ones below it. Only the top — the bed — is counted.
    """

    def test_a_supply_chain_is_counted_once(self):
        before = {"wood": 100.0, "bench": 200.0, "stick": 220.0, "wool": 400.0, "bed": 460.0}
        after = {"wood": 0.0, "bench": 100.0, "stick": 120.0, "wool": 300.0, "bed": 360.0}
        top = priority.future_value(before, after, {"bed": 500.0})
        self.assertEqual(top, 100.0, "one end, one saving")
        every_link = priority.future_value(before, after, {d: 500.0 for d in before})
        self.assertGreater(every_link, top * 4, "this is what summing intermediates did, and why it exploded")

    def test_an_intermediate_is_not_an_end(self):
        """Wood is wanted only because a bed is. It has no worth of its own, so it has no line of its own."""
        self.assertEqual(priority.future_value({"wood": 100.0}, {"wood": 0.0}, {"bed": 500.0}), 0.0)


class ItCoversWhatTheTwoUsedTo(unittest.TestCase):
    """Every case the old pair handled, through the one function."""

    def table(self):
        return actions.table(actions.Costs(lambda kinds: 20.0), {})

    def test_the_product_is_worth_its_price_times_the_demand(self):
        tbl = self.table()
        start = {"bag_free": 20}
        plan = solve(tbl, start, {"minecraft:crafting_table": 1})
        before, after = reach_cost(tbl, start), reach_cost(tbl, plan.final)
        self.assertGreater(priority.future_value(before, after, {"minecraft:crafting_table": 500.0}), 0.0)

    def test_a_facility_left_behind_is_worth_what_it_saves(self):
        """A bench makes every later craft cheaper: that shows up as those crafts' prices falling."""
        tbl = self.table()
        start = {"bag_free": 20}
        plan = solve(tbl, start, {"minecraft:crafting_table": 1})
        before, after = reach_cost(tbl, start), reach_cost(tbl, plan.final)
        self.assertGreater(priority.future_value(before, after, {"minecraft:stone_pickaxe": 500.0}), 0.0)

    def test_spending_material_is_not_a_gift(self):
        """The failure that made an enchanting table worth six hours: a plan ends holding less wood, and the wood
        price has not fallen — it was spent, not left."""
        self.assertEqual(priority.future_value({"log": 10.0}, {"log": 10.0}, {"log": 80.0}), 0.0)


class TheEndsComeFromTheSurvivalModel(unittest.TestCase):
    """One owner for "what is terminal": the arguments of `survival.expected_loss`, priced by `survival.benefit`.

    Anywhere else and the list drifts — which is exactly how the two old functions grew contradictory rules about
    what counted as a facility.
    """

    def test_every_end_has_a_plannable_dimension(self):
        from bonobo import survival
        tbl = actions.table(actions.Costs(lambda kinds: 20.0), {})
        plannable = {d for a in tbl for d in list(a.effect) + list(a.requires)}
        for dim in survival.END_DIMS.values():
            self.assertIn(dim, plannable, f"{dim} is called terminal but no action can reach it")

    def test_the_worths_are_the_survival_benefit_of_having_it(self):
        from bonobo import survival
        s = survival.make_state(night=True, ticks_until_dusk=0, hp=20, food=8, bed=False, sheltered=False,
                                torches=False, sword=0, pickaxe=0, food_items=0, nights_missed=1, armor=0,
                                shield=False)
        ends = survival.end_worths(s)
        self.assertGreater(ends[survival.END_DIMS["bed"]], 0.0, "a night with no bed: a bed is worth seconds")
        self.assertGreater(ends[survival.END_DIMS["food_items"]], 0.0, "hungry with no food: food is worth seconds")

    def test_what_is_already_had_is_worth_nothing_more(self):
        from bonobo import survival
        s = survival.make_state(night=True, ticks_until_dusk=0, hp=20, food=20, bed=True, sheltered=True,
                                torches=True, sword=3, pickaxe=3, food_items=9, nights_missed=0, armor=20,
                                shield=True)
        self.assertEqual(survival.end_worths(s).get(survival.END_DIMS["bed"], 0.0), 0.0)


class WhatArrivesLaterIsWorthLess(unittest.TestCase):
    """A pickaxe in twenty seconds is not a pickaxe in four hundred.

    The discount used to apply to a candidate's OWN benefit only, while what it unlocks was added raw. So a long
    luxury plan that happens to pass through a bench and a pickaxe collected the same future value as the short
    plan that makes the pickaxe and stops — and, having a bigger body of its own, always won. That is why the live
    agent stopped making tools and benches: an enchanting table was already "making" them, four hundred seconds
    from now, and scored 409 against the pickaxe's 20.
    """

    def C(self, **kw):
        return priority.Candidate("x", 0, kw.pop("cost", 0), None, **kw)

    def test_the_same_unlock_is_worth_less_when_it_takes_longer_to_get(self):
        soon = self.C(unlocks=[(100.0, 1.0)], cost=20 * priority.TICKS_PER_S)
        late = self.C(unlocks=[(100.0, 1.0)], cost=400 * priority.TICKS_PER_S)
        self.assertGreater(soon.benefit_s, late.benefit_s)

    def test_it_is_discounted_by_when_the_plan_finishes(self):
        c = self.C(unlocks=[(100.0, 1.0)], cost=priority.DISCOUNT_HORIZON_S * priority.TICKS_PER_S)
        self.assertAlmostEqual(c.benefit_s, 50.0, places=3, msg="a day away halves it, like every other benefit")

    def test_a_benefit_that_waits_is_discounted_for_both_reasons(self):
        """The delay before it pays AND the work before it exists: a bed unlocked at dusk, four hundred seconds
        of plan away, is discounted by the sum, not by whichever of the two the code happened to look at."""
        c = self.C(unlocks=[(100.0, 1.0)], cost=100 * priority.TICKS_PER_S, delay_s=100.0)
        want = 100.0 / (1.0 + 200.0 / priority.DISCOUNT_HORIZON_S)
        self.assertAlmostEqual(c.benefit_s, want, places=3)

    def test_nothing_is_discounted_away_entirely(self):
        c = self.C(unlocks=[(100.0, 1.0)], cost=100000 * priority.TICKS_PER_S)
        self.assertGreater(c.benefit_s, 0.0)


class OnlyOneOfThem(unittest.TestCase):
    def test_there_is_a_single_function_for_future_value(self):
        self.assertFalse(hasattr(priority, "unlock_seconds"),
                         "two functions for one question is how their rules drifted apart")
        self.assertFalse(hasattr(priority, "legacy_seconds"))

    def test_the_pool_uses_it(self):
        """Through `Brain.future_worth`, which is the only caller: one function, one call site."""
        import inspect
        from bonobo.brain import Brain
        self.assertIn("future_worth", inspect.getsource(Brain._goal_candidates))
        self.assertIn("end_worths", inspect.getsource(Brain._goal_candidates),
                      "the ends must come from the survival model, not from a list kept in the brain")
        self.assertIn("future_value", inspect.getsource(Brain.future_worth))


if __name__ == "__main__":
    unittest.main()

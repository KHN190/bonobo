"""The one scoring rule: saved = price(before) − price(after) − cost.

The four quantities meet here. A state has a price (V), an action changes that state and costs time and health
(Δt and the price of health), and what it is worth is the difference less the cost. `kernel.choose`, a threat
column's `saves`, a day's `benefit` and a fight's `benefit` are four callers of this one line — and when one of
them spelled it differently it ran four times too big, the planner held `ignore`, and the agent was beaten to
death in a bench cell whose columns claimed to save a hundred and sixty seconds.

Everything here is a relation over both sweeps. Nothing asserts which answer wins.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import estimate, kernel, survival as sv, threat  # noqa: E402
from tests.world import dangers, fights  # noqa: E402

SHAPES = ("reshape", "wall_in")


class TheRuleItself(unittest.TestCase):
    def test_it_is_a_difference_less_a_cost(self):
        price = lambda s: 10.0 * s
        self.assertAlmostEqual(estimate.saved_s(price, 5.0, 2.0, 7.0), 50.0 - 20.0 - 7.0, places=6)

    def test_changing_nothing_and_paying_nothing_saves_nothing(self):
        price = lambda s: 3.0 * s
        for state in (0.0, 4.0, 19.0):
            self.assertEqual(estimate.saved_s(price, state, state), 0.0)

    def test_a_dearer_action_saves_less(self):
        price = lambda s: 2.0 * s
        self.assertGreater(estimate.saved_s(price, 8.0, 1.0, 1.0), estimate.saved_s(price, 8.0, 1.0, 5.0))


class EveryLayerSpellsItTheSameWay(unittest.TestCase):
    """Four callers, one line. Each of these was, at some point, its own arithmetic."""

    def test_a_threat_column_saves_what_the_rule_says(self):
        for cell in dangers():
            state, price = cell.threat_state(), cell.price()
            options, horizon = threat.options(state), threat.horizon_for(state)
            doing_nothing = next(o for o in options if o.kind == "ignore")
            for option in options:
                expected = estimate.saved_s(price, threat.owed(doing_nothing, horizon),
                                            threat.owed(option, horizon),
                                            threat.action_cost(option, price))
                self.assertAlmostEqual(threat.saves(option, options, price, horizon), expected, places=6,
                                       msg=f"{cell}: {option.kind}")

    def test_carrying_on_saves_nothing_by_definition(self):
        for cell in dangers():
            state, price = cell.threat_state(), cell.price()
            options, horizon = threat.options(state), threat.horizon_for(state)
            doing_nothing = next(o for o in options if o.kind == "ignore")
            self.assertAlmostEqual(threat.saves(doing_nothing, options, price, horizon), 0.0, places=6, msg=str(cell))

    def test_a_days_benefit_is_the_rule_over_the_days_price(self):
        for gift in ({"bed": True}, {"sword": 2}, {"food_items": 8}):
            state = sv.make_state(hp=12, food=10, sword=0, pickaxe=1)
            after = dict(state)
            after.update(gift)
            self.assertAlmostEqual(sv.benefit(state, gift),
                                   round(estimate.saved_s(sv.expected_loss, state, after), 1), places=1, msg=str(gift))

    def test_a_fights_benefit_is_the_rule_over_the_fights_price(self):
        for cell in fights():
            model, state = cell.model(), cell.fight_state()
            for action in model.actions:
                self.assertAlmostEqual(model.benefit(state, action),
                                       round(estimate.saved_s(model.objective, state, action.effect(state)), 2),
                                       places=2, msg=f"{cell}: {action.name}")

    def test_the_kernel_scores_by_the_same_rule(self):
        for cell in fights():
            model, state = cell.model(), cell.fight_state()
            for action in model.actions:
                self.assertAlmostEqual(kernel.value(model, state, action),
                                       estimate.saved_s(model.price, state, action.effect(state)),
                                       places=6, msg=f"{cell}: {action.name}")


class TheVetoRefusesAndNeverRanks(unittest.TestCase):
    """The fight's veto, over the whole fight sweep: what it refuses must be refused for a reason, and what it
    lets through must fit in the time the phase has left. Which action it refuses in which cell is an outcome."""

    def test_nothing_is_admitted_that_cannot_finish_inside_the_window(self):
        from bonobo import fight_plan
        for cell in fights():
            model, state = cell.model(), cell.fight_state()
            left = fight_plan.remaining(state["boss"]["phase"], state["boss"]["phase_elapsed_s"])
            for action in model.actions:
                if action.default or not model.admissible(state, action)[0]:
                    continue
                self.assertLessEqual(fight_plan.commitment(action) + action.return_s, left + 1e-6,
                                     f"{cell}: {action.name}")

    def test_interruptible_work_is_judged_by_its_segment_not_its_whole(self):
        """A six-second dig inside a two-second window is admissible one segment at a time; judged whole, nothing
        long would ever start."""
        from bonobo import fight_plan
        for action in fights().__next__().model().actions:
            if action.segment_s:
                self.assertLessEqual(fight_plan.commitment(action), action.duration_s + 1e-9, action.name)
                self.assertEqual(fight_plan.commitment(action), action.segment_s, action.name)

    def test_every_refusal_carries_a_reason(self):
        for cell in fights():
            model, state = cell.model(), cell.fight_state()
            for action in model.actions:
                allowed, why = model.admissible(state, action)
                if not allowed:
                    self.assertTrue(why, f"{cell}: {action.name} refused silently")
            for _name, why in model.plan(state)["rejected"]:
                self.assertTrue(why, cell)

    def test_a_dead_agent_admits_nothing_but_its_default(self):
        for cell in fights(fight_body="dying"):
            model, state = cell.model(), cell.fight_state()
            state["self"]["hp"] = 0.0
            for action in model.actions:
                if not action.default:
                    self.assertFalse(model.admissible(state, action)[0], f"{cell}: {action.name}")


class TheDecisionIsTheRuleAtItsBest(unittest.TestCase):
    """What gets held must be what the rule ranks highest. Two spellings disagreeing by a factor of four is what
    the live bench caught: the row said a column saved 163 s and the planner carried on."""

    def test_the_column_held_is_the_column_that_saves_most(self):
        for cell in dangers():
            state, price = cell.threat_state(), cell.price()
            options, horizon = threat.options(state), threat.horizon_for(state)
            best = max(options, key=lambda o: threat.saves(o, options, price, horizon))
            held = threat.decide(state, price).kind
            if threat.saves(best, options, price, horizon) > 0:
                self.assertEqual(held, best.kind, f"{cell}: decide and saves disagree")
            else:
                self.assertEqual(held, "ignore", f"{cell}: answered when nothing paid")

    def test_a_fight_intends_the_best_admitted_action_or_its_default(self):
        for cell in fights():
            model, state = cell.model(), cell.fight_state()
            plan = model.plan(state)
            allowed = [a for a in model.actions
                       if model.admissible(state, a)[0] and not a.default]
            best = max((model.benefit(state, a) - a.cost_s for a in allowed), default=None)
            chosen = model.action(plan["intent"])
            if best is not None and best > 0:
                self.assertGreaterEqual(model.benefit(state, chosen) - chosen.cost_s + 1e-6, best, cell)
            else:
                self.assertTrue(chosen.default or plan["benefit_s"] > 0, f"{cell}: {plan}")

    def test_there_is_always_an_answer(self):
        for cell in fights():
            self.assertTrue(cell.model().plan(cell.fight_state())["intent"], cell)
        for cell in dangers():
            self.assertTrue(threat.decide(cell.threat_state(), cell.price()).kind, cell)


class EveryColumnIsOfferedWhenItCanWork(unittest.TestCase):
    """The other half of the same rule: a column that the world makes possible must EXIST, and the fields it
    carries must mean what the executor reads them as. Swept, because which worlds make a column possible is the
    dimension table's business — these used to be three hand-built states in test_threat."""

    def test_what_we_carry_is_offered_somewhere_in_the_sweep(self):
        offered = set()
        for cell in dangers():
            offered |= {o.kind for o in threat.options(cell.threat_state())}
        for kind in ("ignore", "fight", "evade", "eat", "shield", "reshape", "wall_in"):
            self.assertIn(kind, offered, f"{kind} is a column no world in the sweep can reach")

    def test_a_column_that_heals_heals_and_one_that_protects_protects(self):
        """The executor reads these fields; a column whose `heals` is zero is a column that does nothing when it
        runs, however well it prices."""
        for cell in dangers():
            for option in threat.options(cell.threat_state()):
                if option.kind == "eat":
                    self.assertGreater(option.heals, 0.0, cell)
                if option.kind == "shield":
                    self.assertGreater(option.protects, 0.0, cell)

    def test_ground_worth_building_against_offers_the_wall(self):
        for cell in dangers():
            if not cell.ground().blocks_worth_placing() or not cell.blocks:
                continue
            rows = cell.rows()
            if not rows or any(cell.mob_of(r).get("squeezes") or cell.mob_of(r).get("ranged") for r in rows):
                continue
            wheres = {o.target[0] for o in threat.options(cell.threat_state()) if o.kind == "reshape"}
            self.assertIn("between", wheres, cell)


class WhatAnAnswerIsFor(unittest.TestCase):
    """A column is a shape with a purpose, and the rule must not be able to pay for one that cannot work.

    Stated as what a column SAVES, never as what the planner picks: which answer wins is an outcome of constants
    that move, and a test that names it is a copy of the model rather than a claim about it. `saves` carries its
    own baseline (what carrying on costs in THIS world), so it may be compared between columns of one world and
    never between worlds — the ladders for that live in test_act, over `fight_cost`, which has no baseline in it.
    """

    def test_a_column_is_not_offered_when_what_it_needs_is_not_carried(self):
        """Read off the world's kit, not written in: every column that spends something must be absent from a
        world that holds none of it, whatever the column is called."""
        needs = {"eat": "food", "shield": "shield", "wall_in": "blocks", "reshape": "blocks"}
        for cell in dangers():
            kinds = {o.kind for o in threat.options(cell.threat_state())}
            for kind, resource in needs.items():
                if not getattr(cell, resource):
                    self.assertNotIn(kind, kinds, f"{cell}: {kind} without {resource}")

    def test_what_the_beliefs_call_a_blast_is_never_offered_as_a_fight(self):
        """The property is over the belief table, not over an enemy this test happens to know the name of: if a
        mob's damage is a one-off, trading health against it is not a column."""
        for cell in dangers():
            rows = cell.rows()
            if not rows or not all(cell.mob_of(r).get("burst") for r in rows):
                continue
            self.assertNotIn("fight", [o.kind for o in threat.options(cell.threat_state())], cell)

    def test_shaping_the_ground_buys_nothing_against_what_the_beliefs_say_it_cannot_stop(self):
        """Again from the table: a mob that squeezes past walks over every shape, so no shape may come out worth
        anything. Stated as the saving, never as the pick — which column wins is an outcome."""
        for cell in dangers():
            rows = cell.rows()
            if not rows or not all(cell.mob_of(r).get("squeezes") for r in rows):
                continue
            state, price = cell.threat_state(), cell.price()
            options, horizon = threat.options(state), threat.horizon_for(state)
            for option in options:
                if option.kind in SHAPES:
                    self.assertLessEqual(threat.saves(option, options, price, horizon), 0.0,
                                         f"{cell}: {option.kind} {option.target}")

    def test_a_wall_is_only_offered_where_the_ground_says_it_is_worth_placing(self):
        """`field.blocks_worth_placing` is the one authority on whether there is anything to build against; the
        column may exist exactly where it says so."""
        for cell in dangers():
            if cell.ground().blocks_worth_placing():
                continue
            for option in threat.options(cell.threat_state()):
                if option.kind == "reshape":
                    self.assertNotEqual(option.target[0], "between", cell)


if __name__ == "__main__":
    unittest.main()

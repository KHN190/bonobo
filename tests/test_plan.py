"""The plan itself: what the solver returns, and what the columns it chooses from say.

One file for the machinery under Δt and V — the matrix, the columns, and the steps they become. Properties, not
fixtures: a column table is generated from the world's own tables, so the assertions are about SHAPE (every way
of getting something is a column, every column costs time, requirements are not consumed) rather than about a
list of names that has to be edited whenever the game changes.
"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions, knowledge, memory, priority  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from bonobo.solve import Action, Unsolvable, reach_cost, solve  # noqa: E402

EVERYWHERE = actions.Costs(lambda kinds: 20.0)
NOWHERE = actions.Costs(lambda kinds: None)


def table(state=None, cost=EVERYWHERE):
    return actions.table(cost, state or {})


def names(plan):
    return [a.name for a, _n in plan.steps()]


class TheMatrixPicksTheCheapestCombination(unittest.TestCase):
    def test_it_reaches_the_target_or_says_what_is_missing(self):
        plan = solve(table(), {"tool:pickaxe:0": 1, "bag_free": 20}, {"minecraft:cobblestone": 1})
        self.assertGreater(plan.cost_s, 0.0)
        self.assertTrue(names(plan))
        with self.assertRaises(Unsolvable):
            solve([], {}, {"minecraft:elytra": 1})

    def test_a_cheaper_way_to_the_same_state_wins(self):
        cheap = Action("cheap", {"x": 1}, 1.0)
        dear = Action("dear", {"x": 1}, 100.0)
        self.assertEqual(names(solve([cheap, dear], {}, {"x": 1})), ["cheap"])

    def test_what_is_required_is_not_consumed(self):
        tool = Action("make tool", {"tool": 1}, 5.0)
        work = Action("work", {"x": 1}, 1.0, requires={"tool": 1})
        plan = solve([tool, work], {}, {"x": 2})
        self.assertEqual(plan.counts.get("make tool"), 1, "a requirement is needed once, not once per use")

    def test_sharing_is_exact(self):
        """Two things wanting the same intermediate pay for it once — the whole reason the matrix replaced a
        recursive descent that merged afterwards."""
        planks = Action("planks", {"planks": 4}, 3.0)
        table_ = Action("table", {"table": 1, "planks": -4}, 1.0)
        door = Action("door", {"door": 1, "planks": -4}, 1.0)
        plan = solve([planks, table_, door], {}, {"table": 1, "door": 1})
        made = plan.counts.get("planks", 0) * 4
        spent = 4 * (plan.counts.get("table", 0) + plan.counts.get("door", 0))
        self.assertGreaterEqual(made, spent, "a plan cannot spend what it never made")
        self.assertLess(made - spent, 4, "and it does not make a whole batch it never spends")

    def test_steps_come_out_in_an_order_that_can_be_run(self):
        plan = solve(table(), {"tool:pickaxe:0": 1, "bag_free": 20}, {"minecraft:cobblestone": 4})
        held = {"tool:pickaxe:0": 1, "bag_free": 20}
        for action, times in plan.steps():
            for dim, need in action.requires.items():
                self.assertGreaterEqual(held.get(dim, 0), need, f"{action.name} ran before {dim} existed")
            for dim, delta in action.effect.items():
                held[dim] = held.get(dim, 0) + delta * times


class EveryWayOfGettingSomethingIsAColumn(unittest.TestCase):
    def test_the_table_covers_seek_mine_take_craft_smelt_shelter_and_room(self):
        kinds = {a.tag[0] for a in table() if a.tag}
        for kind in ("seek", "mine", "take", "craft", "smelt", "shelter", "room", "gather", "hunt"):
            self.assertIn(kind, kinds)

    def test_every_column_costs_time_and_says_what_it_leaves(self):
        for a in table():
            self.assertGreater(a.cost_s, 0.0, a.name)
            self.assertTrue(a.effect or a.requires, a.name)

    def test_mining_needs_room_and_a_tool_where_the_block_needs_one(self):
        by_name = {a.name: a for a in table()}
        stone = by_name["mine:minecraft:cobblestone"]
        self.assertIn("bag_free", stone.requires)
        self.assertTrue(any(d.startswith("at:") for d in stone.requires))
        iron = by_name["mine:minecraft:raw_iron"]
        self.assertTrue(any(d.startswith("tool:pickaxe") for d in iron.requires))

    def test_a_tool_column_leaves_uses_behind(self):
        by_name = {a.name: a for a in table()}
        pick = by_name["craft:minecraft:stone_pickaxe"]
        self.assertGreater(pick.effect.get(actions.uses_dim("pickaxe"), 0), 0)

    def test_knowing_nowhere_is_a_price_not_a_dead_end(self):
        plan = solve(actions.table(NOWHERE, {}), {}, {"bed": 1})
        self.assertTrue(any(n.startswith("seek:") for n in plan.counts), plan.counts)
        self.assertTrue(any(n in ("craft:bed", "take:bed") for n in plan.counts), plan.counts)

    def test_seeing_one_makes_it_cheaper_never_possible(self):
        blind = reach_cost(actions.table(NOWHERE, {}), {})
        seen = reach_cost(actions.table(actions.Costs(lambda kinds: 10.0), {}), {})
        for dim, price in seen.items():
            self.assertLessEqual(price, blind.get(dim, math.inf) + 1e-6, dim)


class StepsCarryWhatTheExecutorNeeds(unittest.TestCase):
    def test_every_column_becomes_a_step_with_seconds_on_it(self):
        for a in table():
            step = actions.to_step(a, 1)
            self.assertGreater(step.est, 0, a.name)
            self.assertTrue(step.kind)

    def test_a_batch_takes_what_is_wanted_and_what_the_bag_allows(self):
        def batch(count=1, shadow=3.0, demand=64, free=20, token="minecraft:cobblestone", kind="mine"):
            step = Step(kind, token, count, {})
            step.est = 60
            return actions.marginal_batch(step, {token: shadow}, {token: demand}, free)
        self.assertGreater(batch().count, 1, "a whole approach for one block is all overhead")
        for wanted in (2, 4, 9):
            self.assertLessEqual(batch(demand=wanted).count, wanted, "never more than anything actually wants")
        self.assertEqual(batch(shadow=0.0, demand=0).count, 1, "nothing wants it: take what was planned")
        # With more wanted than the bag could ever hold, the bag is what decides — and a bag with two slots
        # left must decide differently from an empty one.
        self.assertLess(batch(free=2, demand=6400).count, batch(free=30, demand=6400).count,
                        "a full bag stops the batch")
        for planned in (7, 40):
            self.assertGreaterEqual(batch(count=planned, shadow=0.0, demand=0).count, planned,
                                    "a plan is never cut down")

    def test_a_batch_is_promised_the_body_whole(self):
        step = Step("mine", "minecraft:cobblestone", 1, {})
        step.est = 60
        parcel = actions.marginal_batch(step, {"minecraft:cobblestone": 3.0}, {"minecraft:cobblestone": 8}, 20)
        self.assertTrue(parcel.detail["batched"])
        self.assertAlmostEqual(priority.step_commitment(parcel.est, parcel.count, atomic=True), parcel.est / 20.0)
        self.assertGreater(priority.step_commitment(parcel.est, parcel.count, atomic=True),
                           priority.step_commitment(60, 1))


class ToolsWearAndTheStateSaysSo(unittest.TestCase):
    def test_a_worn_tool_is_worth_less_than_a_fresh_one(self):
        m = memory.Memory(os.path.join(tempfile.mkdtemp(prefix="plan"), "notes.json"))
        worn = actions.expected_uses("pickaxe", m, 3.0, 600.0)
        fresh = actions.expected_uses("pickaxe", m, 250.0, 600.0)
        self.assertLess(worn, fresh)

    def test_mining_spends_the_tool_it_requires(self):
        by_name = {a.name: a for a in table()}
        self.assertLess(by_name["mine:minecraft:raw_iron"].effect.get(actions.uses_dim("pickaxe"), 0), 0)


if __name__ == "__main__":
    unittest.main()

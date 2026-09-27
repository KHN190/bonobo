"""The column solver (solve.py over actions.table): what it returns, and what the columns it chooses from say.

Two kinds of table. Small hand-made column sets whose optimum is known exactly (the solver's arithmetic), and the
real action table, over which each rule is stated as a list of violations that must be empty (the table's shape).
"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions, memory  # noqa: E402
from bonobo.solve import Action, Unsolvable, reach_cost, solve  # noqa: E402
from tests.world import inventory, places, slot, snapshot  # noqa: E402

EVERYWHERE = places(20.0)          # fixture: every kind known 20 s away
NOWHERE = places(None)             # fixture: nothing known anywhere


def table(state=None, cost=EVERYWHERE):
    return actions.table(cost, state or {})


def counts(plan):
    return {k: v for k, v in plan.counts.items() if v}


A = Action
# (situation, columns, start, target) → exact runs per column, or the exception
SOLVES = [
    ("the cheaper of two ways", [A("cheap", {"x": 1}, 1.0), A("dear", {"x": 1}, 100.0)], {}, {"x": 1}, {"cheap": 1}),
    ("a requirement is made once, not once per use",
     [A("make tool", {"tool": 1}, 5.0), A("work", {"x": 1}, 1.0, requires={"tool": 1})], {}, {"x": 2},
     {"make tool": 1, "work": 2}),
    ("two things sharing an intermediate pay for it once, and make no batch they never spend",
     [A("planks", {"planks": 4}, 3.0), A("table", {"table": 1, "planks": -4}, 1.0),
      A("door", {"door": 1, "planks": -4}, 1.0)], {}, {"table": 1, "door": 1}, {"planks": 2, "table": 1, "door": 1}),
    ("a chain: ore mined, then smelted, per ingot",
     [A("mine", {"ore": 1}, 2.0), A("smelt", {"ingot": 1, "ore": -1}, 1.0)], {}, {"ingot": 3}, {"mine": 3, "smelt": 3}),
    ("must fail: already held: nothing to do", [A("make", {"x": 1}, 1.0)], {"x": 2}, {"x": 2}, {}),
    ("held in part: only the rest", [A("make", {"x": 1}, 1.0)], {"x": 1}, {"x": 3}, {"make": 2}),
    ("a limit forces the dearer way for the rest",
     [A("cheap", {"x": 1}, 1.0, limit=1), A("dear", {"x": 1}, 10.0)], {}, {"x": 3}, {"cheap": 1, "dear": 2}),
    ("nothing makes it", [], {}, {"minecraft:elytra": 1}, Unsolvable),
    ("its requirement can never hold", [A("work", {"x": 1}, 1.0, requires={"key": 1})], {}, {"x": 1}, Unsolvable),
]


class TheSolver(unittest.TestCase):
    def test_exact_optimum(self):
        for name, columns, start, target, want in SOLVES:
            with self.subTest(name):
                if isinstance(want, type):
                    with self.assertRaises(want):
                        solve(columns, start, target)
                    continue
                self.assertEqual(counts(solve(columns, start, target)), want)


# Real plans the order and seek rules are checked on: (situation, state, target)
REAL = [
    ("cobblestone with a pickaxe", {"tool:pickaxe:0": 1, "uses:pickaxe": 59, "bag_free": 20}, {"minecraft:cobblestone": 4}),
    ("a stone pickaxe from nothing", {"bag_free": 20}, {"minecraft:stone_pickaxe": 1}),
    ("iron with a stone pickaxe", {"tool:pickaxe:1": 1, "uses:pickaxe": 100, "bag_free": 20},
     {"minecraft:iron_ingot": 2}),
    ("a bed from nothing", {"bag_free": 20}, {"bed": 1}),
]


class RealPlans(unittest.TestCase):
    def test_steps_come_in_an_order_that_can_run(self):
        for name, start, target in REAL:
            with self.subTest(name):
                plan = solve(table(start), dict(start), target)
                held, early = dict(start), []
                for action, times in plan.steps():
                    early += [(action.name, dim) for dim, need in action.requires.items() if held.get(dim, 0) < need]
                    for dim, delta in action.effect.items():
                        held[dim] = held.get(dim, 0) + delta * times
                self.assertEqual(early, [], "a step ran before what it requires existed")

    def test_knowing_nowhere_every_place_needed_is_sought(self):
        """With nothing known, every `at:X` a chosen column requires is made by a chosen seek column."""
        for name, start, target in REAL:
            with self.subTest(name):
                plan = solve(actions.table(NOWHERE, start), dict(start), target)
                chosen = [a for a in plan.actions if plan.counts.get(a.name)]
                needed = {d for a in chosen for d in a.requires if d.startswith("at:")}
                sought = {d for a in chosen if a.name.startswith("seek:") for d in a.effect if d.startswith("at:")}
                self.assertEqual(needed - sought, set())

    def test_seeing_one_makes_it_cheaper_never_possible(self):
        blind = reach_cost(actions.table(NOWHERE, {}), {})
        for seconds in (5.0, 10.0, 60.0, 200.0):
            with self.subTest(seconds=seconds):
                seen = reach_cost(actions.table(places(seconds), {}), {})
                dearer = sorted(d for d, p in seen.items() if p > blind.get(d, math.inf) + 1e-6)
                self.assertEqual(dearer, [])


# (column name, part of what it requires, part of what it does): the real table, exactly
COLUMNS = [
    ("mine:minecraft:cobblestone", {"bag_free": 1, actions.at("stone"): 1}, {}),
    ("mine:minecraft:raw_iron", {actions.tool_dim("pickaxe", 1): 1}, {actions.uses_dim("pickaxe"): -1}),
    ("craft:minecraft:stone_pickaxe", {}, {actions.uses_dim("pickaxe"): actions.TOOL_USES["stone"]}),
    ("craft:minecraft:iron_pickaxe", {}, {actions.uses_dim("pickaxe"): actions.TOOL_USES["iron"]}),
    ("gather:log", {actions.DAY_DIM: 1, "hands_free": 1}, {}),
    ("take:bed", {}, {"bed": 1}),
]


class TheColumns(unittest.TestCase):
    def test_named_columns(self):
        by_name = {a.name: a for a in table()}
        for name, requires, effect in COLUMNS:
            with self.subTest(name):
                a = by_name[name]
                self.assertEqual({k: a.requires.get(k) for k in requires}, requires)
                self.assertEqual({k: a.effect.get(k) for k in effect}, effect)

    def test_every_column(self):
        """Every column: costs time, does or needs something, becomes an executable step with seconds on it."""
        rules = [("costs no time", lambda a: a.cost_s <= 0),
                 ("does nothing and needs nothing", lambda a: not (a.effect or a.requires)),
                 ("becomes a step with no seconds", lambda a: actions.to_step(a, 1).est <= 0),
                 ("becomes a step of no kind", lambda a: not actions.to_step(a, 1).kind)]
        columns = table()
        for rule, broken in rules:
            with self.subTest(rule):
                self.assertEqual([a.name for a in columns if broken(a)], [])

    def test_every_way_of_getting_something_is_a_column(self):
        kinds = {a.tag[0] for a in table() if a.tag}
        for kind in ("seek", "mine", "take", "craft", "smelt", "shelter", "room", "gather", "hunt"):
            with self.subTest(kind):
                self.assertIn(kind, kinds)


# (bag) → the pickaxe uses the state vector carries (usable ones only: 3 or more left)
USES = [("must fail: none", inventory(), 0), ("a fresh iron pickaxe", inventory(("iron_pickaxe", 1)), 250),
        ("a worn one, 10 left", inventory(slot("iron_pickaxe", 1, 240)), 10),
        ("two, added up", inventory(("stone_pickaxe", 1), slot("iron_pickaxe", 1, 200)), 131 + 50),
        ("one about to break (2 left) does not count", inventory(slot("iron_pickaxe", 1, 248)), 0)]


class ToolsWear(unittest.TestCase):
    def test_uses_in_the_state(self):
        m = memory.Memory(os.path.join(tempfile.mkdtemp(prefix="plan"), "notes.json"))
        for name, inv, want in USES:
            with self.subTest(name):
                self.assertEqual(actions.state_of(snapshot(inv=inv), m).get(actions.uses_dim("pickaxe"), 0), want)


if __name__ == "__main__":
    unittest.main()

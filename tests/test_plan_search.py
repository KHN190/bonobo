"""The one planner's search (planner.py): its bound never above what a plan pays, every way of a token an option
(group members, containers), the priced alternatives it reports, and a container taken from once per what it holds.
Tables of values: the expectations come from the planner's own price of each alternative or from the game's tables."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered: the producing tables)
from bonobo import planner  # noqa: E402
from bonobo.cost import Cost  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from bonobo.planner import NullCost, Unplannable, plan_candidates, plan_needs  # noqa: E402
from tests.world import bag, inventory, snapshot  # noqa: E402

OVER = "minecraft:overworld"


def ticks(steps):
    return sum(s.est for s in steps)


class OneStationOfAKind(unittest.TestCase):
    """A station is required, never used up: one the goal asks for stands from when it is had, so a later step that
    works at it never makes another (station kit: the table asked and the pickaxe crafted at it)."""

    ROWS = [("a table and a wooden pickaxe asked", [("minecraft:crafting_table", 1), ("minecraft:wooden_pickaxe", 1)]),
            ("must fail: the station kit (table and furnace): one table, not two",
             [("minecraft:crafting_table", 1), ("minecraft:furnace", 1)])]

    def test_one_table(self):
        for name, needs in self.ROWS:
            with self.subTest(name):
                steps = plan_needs(bag(inventory()), needs, NullCost())
                self.assertEqual(sum(s.count for s in steps if s.kind == "craft"
                                     and s.token == "minecraft:crafting_table"), 1)


class TheContractsStation(unittest.TestCase):
    """A step whose contract works at a station (knowledge.step_station: sleep at a bed, brew at a stand) has it first:
    carried or standing, else made."""

    def test_sleep_has_its_bed(self):
        from bonobo.planner import Step
        # (situation, carried) → the bed made before the sleep?
        rows = [("a bed carried: sleep", [("white_bed", 1)], False),
                ("must fail: an empty bag: a bed made first (the contract's station, not a step that cannot run)", [],
                 True)]
        for name, carried, made in rows:
            with self.subTest(name):
                steps = plan_needs(bag(inventory(*carried)), [("do", Step("sleep", "bed", 1, {}))], NullCost())
                self.assertEqual((steps[-1].kind, any(s.kind == "craft" and s.token.endswith("bed") for s in steps)),
                                 ("sleep", made))


class TheBoundNeverOverprices(unittest.TestCase):
    """Bound.least (what is held credited at every level) is at most what the plan the planner finds pays: A* and
    the incumbent's pruning drop nothing cheaper."""

    ROWS = [("a stone pickaxe from nothing", [], [("tool", "pickaxe", 1)]),
            ("a stone pickaxe, cobblestone and sticks carried", [("cobblestone", 8), ("stick", 4)],
             [("minecraft:stone_pickaxe", 1)]),
            ("a bucket, ingots carried", [("iron_ingot", 3), ("crafting_table", 1)], [("minecraft:bucket", 1)]),
            ("torches, coal carried", [("coal", 2), ("oak_planks", 4)], [("minecraft:torch", 8)]),
            ("must fail: a held item credited where it is asked deep down: bread, wheat carried",
             [("wheat", 3), ("crafting_table", 1)], [("minecraft:bread", 1)])]

    def test_least_at_most_the_plan(self):
        cost = NullCost()
        for name, carried, needs in self.ROWS:
            with self.subTest(name):
                inv = planner.from_bag(bag(inventory(*carried)))
                search = planner.Search(cost)
                root = planner.Node(inv, [], [])
                root.stack = [("tool", n[1], n[2], 1, 0) if n[0] == "tool" else ("need", n[0], n[1], 0, False)
                              for n in reversed(needs)]
                self.assertLessEqual(search.h(root), ticks(plan_needs(bag(inventory(*carried)), needs, cost)))


class EveryWayIsAnOption(unittest.TestCase):
    """A group's members each by their own way, a container's stock, priced against each other."""

    def test_a_group_by_its_members(self):
        # (situation, carried, what /find saw) → the way 9 building blocks are had
        from tests.world import cost
        rows = [("dirt in sight, an empty bag: dirt by hand", [], {"dirt": 4}, "minecraft:dirt"),
                ("a pickaxe, stone close, dirt far: stone", [("stone_pickaxe", 1)], {"stone": 2, "dirt": 40},
                 "minecraft:cobblestone"),
                ("must fail: 9 cobblestone carried: nothing", [("cobblestone", 9)], {"dirt": 4}, None)]
        for name, carried, seen, want in rows:
            with self.subTest(name):
                snap = snapshot(inv=inventory(*carried))
                steps = plan_needs(snap.inv, [("building", 9)], cost(snap, **seen))
                self.assertEqual(steps[-1].token if steps else None, want)

    def test_a_container_is_taken_from_once_per_what_it_holds(self):
        """Two of four logs in a chest by the body, trees far: the chest's two never taken twice."""
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            snap = snapshot()
            m.note_container((1, int(snap.feet[1]), 0), OVER, [{"id": "minecraft:oak_log", "count": 2}])
            steps = plan_needs(snap.inv, [("log", 4)], Cost(snap, m, finds={"oak_log": 200.0}))
            taken = sum(s.count for s in steps if s.kind == "withdraw")
            self.assertLessEqual(taken, 2, [str(s) for s in steps])          # must fail: 4 taken from a chest of 2


class TheAlternativesAreReported(unittest.TestCase):
    """plan_candidates: the plan chosen first, the alternatives the search priced after — what the checker reads."""

    def test_rows(self):
        rows = [("a pickaxe from nothing: the cheapest first", [], [("tool", "pickaxe", 0)]),
                ("must fail: nothing to do: one empty plan", [("wooden_pickaxe", 1)], [("tool", "pickaxe", 0)])]
        for name, carried, needs in rows:
            with self.subTest(name):
                inv = bag(inventory(*carried))
                got = plan_candidates(inv, needs, NullCost())
                chosen = plan_needs(inv, needs, NullCost())
                self.assertEqual(got[0][2], chosen)
                self.assertEqual([c[1] for c in got], sorted(c[1] for c in got))
                self.assertAlmostEqual(got[0][1], ticks(chosen) / 20.0)

    def test_the_budget_spent_returns_the_incumbent(self):
        """MAX_NODES spent (here none at all): the incumbent the dive found stands — a plan, never a hang."""
        from unittest import mock
        needs = [("tool", "pickaxe", 1)]
        with mock.patch.object(planner, "MAX_NODES", 0):
            spent = plan_needs(bag(inventory()), needs, NullCost())
        search = planner.Search(NullCost())
        root = planner.Node(planner.from_bag(bag(inventory())), [], [])
        root.stack = [("tool", "pickaxe", 1, 1, 0)]
        self.assertEqual(spent, search.dive(root)[2])          # must fail: an empty plan or a raise when spent

    def test_unplannable_says_why(self):
        with self.assertRaises(Unplannable) as caught:
            plan_needs(bag(inventory()), [("minecraft:bedrock", 1)], NullCost())
        self.assertIn("minecraft:bedrock", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

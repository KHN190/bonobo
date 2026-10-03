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


class TheRoundsOnePlan(unittest.TestCase):
    """planner.plan_round: every target in one plan — a target that waits on another after it (hard), the queue's
    rank a tie-break only, the bar never run out along the plan's clock (hard)."""

    def test_the_order_is_the_fewest_seconds(self):
        from bonobo.planner import Target, plan_round
        targets = [Target("logs", [("log", 8)], 0), Target("torches", [("minecraft:torch", 8)], 1)]
        by_rank = ticks(plan_needs(bag(inventory()), [("log", 8), ("minecraft:torch", 8)], NullCost()))
        other = ticks(plan_needs(bag(inventory()), [("minecraft:torch", 8), ("log", 8)], NullCost()))
        _first, steps, _secs = plan_round(bag(inventory()), targets, NullCost())
        # must fail: the queue's rank (logs first) though the other order's whole plan takes fewer seconds
        self.assertEqual(ticks(steps), min(by_rank, other))
        self.assertLess(other, by_rank)

    def test_one_of_takes_the_cheapest_whole_plan(self):
        from bonobo.planner import Target, plan_round
        # (situation, the ways: (name, needs, extra seconds)) → the way taken
        rows = [("a bed's plan the cheapest", (("bed", [("bed", 1)], 0.0), ("shelter", [("minecraft:cobblestone", 8)], 120.0)),
                 "bed"),
                ("must fail: the first way listed, though its extra seconds make it the dearer",
                 (("bed", [("bed", 1)], 900.0), ("shelter", [("minecraft:cobblestone", 8)], 0.0)), "shelter"),
                ("a way that cannot be had is left out",
                 (("bedrock", [("minecraft:bedrock", 1)], 0.0), ("shelter", [("minecraft:cobblestone", 8)], 0.0)), "shelter")]
        for name, options, way in rows:
            with self.subTest(name):
                chosen: dict = {}
                plan_round(bag(inventory()), [Target("night", [], 0, options=options)], NullCost(), chosen=chosen)
                self.assertEqual(chosen, {"night": way})

    def test_the_bar_never_runs_out(self):
        from tests.world import cost, state
        from bonobo.planner import Target, plan_round
        # (situation, the bar) → the first step is food (carried beef, coal, a furnace standing)
        rows = [("a full bar: the task first", 20, False),
                ("must fail: a bar of 6 and a long task: food before it runs out", 6, True)]
        for name, food, first_food in rows:
            with self.subTest(name):
                m = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
                m.add_station("minecraft:furnace", (1, 64, 1), OVER)
                snap = snapshot(state(food=food), inventory(("beef", 2), ("coal", 4)))
                first, _steps, _secs = plan_round(snap.inv, [Target("iron", [("tool", "pickaxe", 2)], 0)], cost(snap, mem=m))
                self.assertEqual(first.token == "minecraft:cooked_beef", first_food)


class ThePlanRunsInItsOrder(unittest.TestCase):
    """forward and walk_order keep every step after what it takes (P2): a placed step is never walked to after the
    step that uses what it gets, a step is never merged forward past the step that makes its input."""

    def test_walk_order_keeps_a_withdraw_before_its_use(self):
        from types import SimpleNamespace
        from bonobo.planner import Step, walk_order
        steps = [Step("mine", "minecraft:raw_iron", 3, {}), Step("withdraw", "minecraft:oak_log", 1, {"pos": [40, 64, 0]}),
                 Step("craft", "planks", 4, {"inputs": {"log": 1}}), Step("withdraw", "minecraft:cobblestone", 8,
                                                                       {"pos": [2, 64, 0]})]
        sites = {0: (30, 64, 0), 1: (40, 64, 0), 3: (2, 64, 0)}
        cost = SimpleNamespace(snap=SimpleNamespace(feet=(0, 64, 0)), site=lambda s: sites.get(steps.index(s)))
        order = walk_order(steps, cost)
        # must fail: the log withdrawn after the planks it makes (a group read through its members: log ∋ oak_log)
        self.assertLess(order.index(1), order.index(2))

    def test_no_merge_past_the_input_maker(self):
        from bonobo.planner import Step, forward
        e = [(Step("smelt", "minecraft:iron_ingot", 3, {"inputs": {"minecraft:raw_iron": 3, "planks": 2}}), {}, 0),
             (Step("craft", "planks", 8, {"inputs": {"log": 2}}), {}, 1),
             (Step("smelt", "minecraft:iron_ingot", 1, {"inputs": {"minecraft:raw_iron": 1, "planks": 1}}), {}, 2)]
        out, _ticks = forward(e, NullCost())
        # must fail: the later smelt (fuel from the planks crafted after the first) merged into the first
        self.assertEqual([s.kind for s in out], ["smelt", "craft", "smelt"])


class TheBoundIsKeptAcrossRounds(unittest.TestCase):
    """planner.bound: one Bound while the producers and the measured durations hold; a duration measured builds it
    again (stale prices are wrong prices)."""

    def test_rows(self):
        from tests.world import cost, state
        m = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
        first = planner.bound(cost(snapshot(state(), inventory()), mem=m))
        again = planner.bound(cost(snapshot(state(), inventory()), mem=m))      # the next round, nothing measured
        for _ in range(5):
            m.record_duration("mine:minecraft:cobblestone", 1.0)
        measured = planner.bound(cost(snapshot(state(), inventory()), mem=m))
        # must fail: a duration measured, the old prices kept
        self.assertEqual((again is first, measured is first), (True, False))


class LeastPricesAreTheFixpoint(unittest.TestCase):
    """planner.least_prices (a heap, a fallen price re-pricing only its users) equals the plain fixed point: every
    way of every token relaxed until nothing falls — cycles and shares below one unit included."""

    @staticmethod
    def plain(ways):
        import math
        from bonobo.data import mid
        got: dict = {}

        def of(t):
            return got.get(t, got.get(mid(t), math.inf))
        for _ in range(len(ways) + 1):
            changed = False
            for asked, ways_ in ways.items():
                v = min((per + sum(of(t) * c for t, c in ins.items()) for per, ins, _n in ways_), default=math.inf)
                if v < got.get(asked, math.inf) - 1e-9:
                    got[asked], changed = v, True
            if not changed:
                break
        return got

    def test_the_tables(self):
        ways = planner.Bound(NullCost()).ways
        self.assertEqual({k: round(v, 6) for k, v in planner.least_prices(ways).items()},
                         {k: round(v, 6) for k, v in self.plain(ways).items()})

    def test_rows(self):
        # (situation, ways) → prices
        rows = [("a chain", {"a": [(1.0, {}, {})], "b": [(2.0, {"a": 3}, {})]}, {"a": 1.0, "b": 5.0}),
                ("a share below one unit", {"a": [(8.0, {}, {})], "b": [(1.0, {"a": 0.25}, {})]}, {"a": 8.0, "b": 3.0}),
                ("must fail: the dearer way first, the cheaper found later (a price is never final when popped)",
                 {"y": [(10.0, {}, {})], "x": [(9.0, {}, {}), (1.0, {"y": 0.5}, {})]}, {"y": 10.0, "x": 6.0}),
                ("a cycle never lowers a price", {"a": [(4.0, {}, {}), (1.0, {"b": 1}, {})], "b": [(1.0, {"a": 1}, {})]},
                 {"a": 4.0, "b": 5.0})]
        for name, ways, want in rows:
            with self.subTest(name):
                self.assertEqual(planner.least_prices(ways), want)


class TheHeldPlanIsTheBar(unittest.TestCase):
    """The plan held from the round before, priced on today's world, is the incumbent: a plan as cheap is found with
    far fewer search steps; a held plan that no longer runs from this bag is ignored."""

    def test_rows(self):
        needs = [("tool", "pickaxe", 2)]
        fresh_before = planner.SPENT["steps"]
        fresh = plan_needs(bag(inventory()), needs, NullCost())
        fresh_steps = planner.SPENT["steps"] - fresh_before
        # (situation, the held plan) → (as cheap as a fresh one, searched less than it)
        rows = [("must fail: the held plan, still good: priced, not searched again", fresh, True),
                ("a held plan missing its first steps: ignored, searched as fresh", fresh[3:], False)]
        for name, held, fewer in rows:
            with self.subTest(name):
                before = planner.SPENT["steps"]
                got = plan_needs(bag(inventory()), needs, NullCost(), held=held)
                self.assertEqual((ticks(got), planner.SPENT["steps"] - before < fresh_steps), (ticks(fresh), fewer))


class TheChainIsTheGraphs(unittest.TestCase):
    """How deep a plan may go is the recipe and contract graph's own longest chain (Bound.depth, no typed limit); a
    cycle is cut where it closes (the same thing asked while it is being made), never by depth."""

    def test_the_dragon_from_nothing(self):
        from bonobo import skill
        # must fail: a typed depth of 14 left the empty bag's dragon unplannable (G1)
        steps = plan_needs(bag(inventory()), [("fact", "state:dragon_dead", True)], NullCost())
        self.assertTrue(skill.sets_of_step(steps[-1]).get("state:dragon_dead"), [str(s) for s in steps])

    def test_the_depth_is_the_graphs(self):
        b = planner.bound(NullCost())
        self.assertEqual(b.depth, b.longest_chain() + len(planner.contract_facts()) + 2)


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
        root = planner.Node(planner.from_bag(bag(inventory()), facts=NullCost().facts()), [], [])
        root.stack = [("tool", "pickaxe", 1, 1, 0)]
        self.assertEqual(spent, search.dive(root)[2])          # must fail: an empty plan or a raise when spent

    def test_unplannable_says_why(self):
        with self.assertRaises(Unplannable) as caught:
            plan_needs(bag(inventory()), [("minecraft:bedrock", 1)], NullCost())
        self.assertIn("minecraft:bedrock", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

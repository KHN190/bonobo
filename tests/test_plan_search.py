"""The one planner's search (planner.py): its bound never above what a plan pays, every way of a token an option
(group members, containers), the priced alternatives it reports, and a container taken from once per what it holds.
Tables of values: the expectations come from the planner's own price of each alternative or from the game's tables."""
import math
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
from tests.world import bag, inventory, snapshot, state  # noqa: E402

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
        orders = {"logs first": [("log", 8), ("minecraft:torch", 8)], "torches first": [("minecraft:torch", 8), ("log", 8)]}
        priced = {k: ticks(plan_needs(bag(inventory()), v, NullCost())) for k, v in orders.items()}
        dear = max(priced, key=lambda k: priced[k])
        # the dearer order ranked first: the queue's rank is a tie-break, never the order taken
        targets = [Target("logs", [("log", 8)], 0 if dear == "logs first" else 1),
                   Target("torches", [("minecraft:torch", 8)], 1 if dear == "logs first" else 0)]
        _first, steps, _secs = plan_round(bag(inventory()), targets, NullCost())
        self.assertNotEqual(priced["logs first"], priced["torches first"])     # the orders differ: the row tells
        # must fail: the queue's rank taken though the other order's whole plan takes fewer seconds
        self.assertEqual(ticks(steps), min(priced.values()))

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
            snap = snapshot(oak_log=200.0)
            m.note_container((1, int(snap.feet[1]), 0), OVER, [{"id": "minecraft:oak_log", "count": 2}])
            steps = plan_needs(snap.inv, [("log", 4)], Cost(snap, m))
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


class ARoundThinksWithinItsCap(unittest.TestCase):
    def test_rows(self):
        saved = dict(planner.SPENT)
        # (where the round began, steps now) → spent? — None: no round open (a test, a bench estimate)
        rows = [(None, 99999, False), (0, planner.ROUND_STEPS - 1, False),
                (0, planner.ROUND_STEPS, True),            # must fail: a round past its cap searching on
                (100, planner.ROUND_STEPS, False)]        # must fail: steps before the round counted against it
        try:
            for began, now, want in rows:
                with self.subTest(began=began, now=now):
                    planner.SPENT.update(round=began, steps=now)
                    self.assertEqual(planner.round_spent(), want)
        finally:
            planner.SPENT.update(saved)

    def test_a_way_after_the_cap_is_not_weighed(self):
        saved = dict(planner.SPENT)
        try:
            planner.SPENT.update(round=0, steps=planner.ROUND_STEPS)
            search = planner.Search(NullCost())
            root = planner.Node(planner.from_bag(bag(inventory()), facts=NullCost().facts()), [], [])
            root.stack = [("need", "log", 1, 0, False)]
            # must fail: a capped way searched on past the round's cap
            self.assertIsNone(search.settle(root, 0, 10 ** 9))
        finally:
            planner.SPENT.update(saved)


class TheBoundWalksEveryTripLeft(unittest.TestCase):
    def test_each_search_a_source_needs_is_counted(self):
        from tests.world import cost
        c = cost(snapshot())
        search = planner.Search(c)
        node = planner.Node(planner.from_bag(bag(inventory()), None, None, c.reserved, c.facts()), [], [])
        node.stack = [("tool", "pickaxe", 2, 1, 0)]
        searches = search.required("minecraft:iron_pickaxe", node.inv.available, set(), frozenset())
        walks = {r[1]: r[2] for r in searches if r[0] == "search"}
        # must fail: the iron and the logs a pickaxe of iron needs, nowhere known, bounded as one walk (their max)
        self.assertEqual(set(walks), {("mine", "minecraft:raw_iron"), ("gather", "log")})
        self.assertGreaterEqual(search.h(node), sum(walks.values()))

    def test_a_way_through_what_cannot_be_made_is_no_way(self):
        from tests.world import cost
        search = planner.Search(cost(snapshot()))
        # must fail: iron ingots from an iron block (made of ingots) taken as a way that needs no walk
        self.assertTrue(search.required("minecraft:iron_ingot", lambda t: 0, set(), frozenset()))


class TheSearchPricesAtTheLeast(unittest.TestCase):
    """A*'s g never above what forward will price: forward may run a step after any other and merges repeats."""

    def test_a_repeated_craft_adds_no_work(self):
        search = planner.Search(NullCost())
        node = planner.Node(planner.from_bag(bag(inventory(("oak_log", 2))), facts=NullCost().facts()), [], [])
        added = []
        for _ in range(2):
            g0 = node.g
            search.emit(node, planner.Step("craft", "planks", 4, {"inputs": {"log": 1}}), 0, 0)
            added.append(node.g - g0)
        # must fail: the second craft of planks charged a craft's work though forward merges it into the first
        self.assertEqual(added[1], 0)
        self.assertGreater(added[0], 0)

    def test_a_walk_from_a_place_only_the_look_saw(self):
        import os
        import tempfile
        from bonobo import world
        from bonobo.cost import Cost, walk_ticks
        hits = {"iron_ore": [{"x": 30, "y": 64, "z": 0, "distance": 30.0}],
                "coal_ore": [{"x": 31, "y": 64, "z": 0, "distance": 31.0}]}
        snap = world.Snapshot.from_readings(state(), inventory(), hits, [])
        with tempfile.TemporaryDirectory() as tmp:
            c = Cost(snap, Memory(os.path.join(tmp, "notes.json")))
            step = planner.Step("mine", "minecraft:raw_iron", 1, {"blocks": ["iron_ore"], "tier": 1, "breaks": 1})
            # must fail: walked from the feet (30 blocks) though the coal the look saw a block away may come first
            self.assertLessEqual(c.walk_lb(step), walk_ticks(1.0))


class ASubPlanIsRememberedWithWhatItsPriceReads(unittest.TestCase):
    def test_the_same_need_after_another_prefix(self):
        from tests.world import cost
        c = cost(snapshot(), oak_log=30)
        search = planner.Search(c)

        def node(gathered):
            n = planner.Node(planner.from_bag(bag(inventory()), None, None, c.reserved, c.facts()), [], [])
            if gathered:
                search.emit(n, planner.Step("gather", "log", 1, {}), 0, 0)
                n.inv.consume("log", 1, awaits=False)
            n.stack = [("need", "log", 2, 0, False)]
            return n
        got = []
        for gathered in (True, False, True):        # the second and third asked after the memo holds the first
            n = node(gathered)
            g0 = n.g
            got.append(search.settled(n, math.inf).g - g0)
        # must fail: one price for both (the memo's, whichever prefix it was made under): the trees walked to or not
        self.assertLess(got[0], got[1])
        self.assertEqual(got[0], got[2])


class TheLeastWalkIsNoMoreThanFromHere(unittest.TestCase):
    def test_a_source_nearer_by_the_look_than_its_cell(self):
        import os
        import tempfile
        from bonobo import world
        from bonobo.cost import Cost
        # the look's own distance to a tree (its path) shorter than the straight line to the cell it names
        snap = world.Snapshot.from_readings(state(), inventory(), {"oak_log": [{"x": 40, "y": 64, "z": 40, "distance": 20.0}]}, [])
        with tempfile.TemporaryDirectory() as tmp:
            c = Cost(snap, Memory(os.path.join(tmp, "notes.json")))
            step = planner.Step("gather", "log", 1, {})
            # must fail: the least walk (565 ticks, the cell's line) above the walk the model prices from here (250)
            self.assertLessEqual(c.walk_lb(step), c.estimate(step) - c.work(step))


class TwoStepsAtOnePlace(unittest.TestCase):
    def test_the_second_take_from_a_chest_walks_nowhere(self):
        import os
        import tempfile
        from tests.world import cost
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            m.note_container((3, 64, 0), "minecraft:overworld", [{"id": "minecraft:oak_log", "count": 4},
                                                                  {"id": "minecraft:cobblestone", "count": 4}])
            c = cost(snapshot(), mem=m)
            search = planner.Search(c)
            node = planner.Node(planner.from_bag(bag(inventory()), None, None, c.reserved, c.facts()), [], [])
            steps = [planner.Step("withdraw", t, 1, {"pos": [3, 64, 0]}) for t in ("minecraft:oak_log", "minecraft:cobblestone")]
            for st in steps:
                search.emit(node, st, 0, 0)
            # must fail: the second take charged a walk to the chest it stands at (g above the price as run)
            self.assertLessEqual(node.g, sum(planner.price_as_run(steps, [], c)))


class AToolTakenIsHeld(unittest.TestCase):
    def test_a_mine_after_a_pickaxe_from_a_chest(self):
        from tests.world import cost
        c = cost(snapshot(), stone=10)
        mine = planner.Step("mine", "minecraft:cobblestone", 8, {"blocks": ["stone"], "tier": 0, "breaks": 8})
        took = planner.price_as_run([planner.Step("withdraw", "minecraft:wooden_pickaxe", 1, {"pos": [0, 64, 0]}), mine], [], c)
        bare = planner.price_as_run([planner.Step("withdraw", "minecraft:stick", 1, {"pos": [0, 64, 0]}), mine], [], c)
        # must fail: the mine after a pickaxe taken from a chest priced bare-handed (only a crafted tool counted)
        self.assertLess(took[1], bare[1])


class AWayNotWeighedIsSaid(unittest.TestCase):
    def test_a_capped_search_after_the_rounds_steps(self):
        saved = dict(planner.SPENT)
        try:
            planner.SPENT.update(round=0, steps=planner.ROUND_STEPS)
            search = planner.Search(NullCost())
            root = planner.Node(planner.from_bag(bag(inventory()), facts=NullCost().facts()), [], [])
            # must fail: reported Dearer (found dearer than the way had) though nothing of it was weighed
            with self.assertRaises(planner.Cut):
                search.plan(root, [("tool", "pickaxe", 1)], None, 10 ** 9)
        finally:
            planner.SPENT.update(saved)


class TheBoundFollowsTheTables(unittest.TestCase):
    def test_a_rewiring_builds_it_again(self):
        from bonobo import knowledge
        first = planner.bound(NullCost())
        saved = knowledge.TABLES_VERSION[0]
        try:
            knowledge.TABLES_VERSION[0] += 1       # a skill registered, the hooks wired again
            # must fail: the bound built from the tables before (kept by object ids an address may reuse)
            self.assertIsNot(planner.bound(NullCost()), first)
        finally:
            knowledge.TABLES_VERSION[0] = saved


class ThePlanTakenSaysItsWay(unittest.TestCase):
    def test_every_step_weighed_on_its_way_is_kept(self):
        del planner.PATHS[:]
        steps = plan_needs(bag(inventory()), [("tool", "pickaxe", 1)], NullCost())
        # must fail: no record of what the plan taken was weighed at (P3 judges each against its price as run)
        price, path = planner.PATHS[-1]
        self.assertEqual(price, sum(st.est for st in steps))
        self.assertGreaterEqual(len(path), len(steps))
        self.assertTrue(all(g + h <= price for g, h in path), path)


class TheRoundsBudgetIsShared(unittest.TestCase):
    def test_rows(self):
        saved = dict(planner.SPENT)
        # (where the round began, steps now) → the expansions one search may make
        rows = [(None, 5000, planner.MAX_NODES),
                (0, 0, (planner.ROUND_STEPS - planner.DIVE_RESERVE) // 2),
                (0, planner.ROUND_STEPS, 0)]        # must fail: a search after the round's steps are spent
        try:
            for began, now, want in rows:
                with self.subTest(began=began, now=now):
                    planner.SPENT.update(round=began, steps=now)
                    self.assertEqual(planner.search_allowance(), want)
        finally:
            planner.SPENT.update(saved)


class AHeldPlanIsReplayedHonestly(unittest.TestCase):
    def test_a_craft_whose_inputs_are_not_had_is_no_incumbent(self):
        search = planner.Search(NullCost())
        root = planner.Node(planner.from_bag(bag(inventory()), facts=NullCost().facts()), [], [])
        held = [planner.Step("craft", "minecraft:iron_pickaxe", 1, {"times": 1, "inputs": {}})]
        # must fail: a 60-tick incumbent from an empty bag (its step names no inputs: the recipe's were not read)
        self.assertIsNone(search.replay(root, [("tool", "pickaxe", 2)], held))

    def test_a_fact_asked_is_replayed_by_the_steps_that_make_it(self):
        search = planner.Search(NullCost())
        root = planner.Node(planner.from_bag(bag(inventory()), facts=NullCost().facts()), [], [])
        held = [planner.Step("build", "nether_portal", 1, {})]
        # must fail: a held plan for a fact never replayed (searched again from nothing each round)
        self.assertIsNotNone(search.replay(root, [("fact", "portal", True)], held))
        self.assertIsNone(search.replay(root, [("fact", "covered", True)], held))   # must fail: a fact it never makes

    def test_takes_from_one_container_are_one_trip(self):
        def took(a, b):
            steps = [planner.Step("withdraw", "minecraft:stick", 2, {"pos": a}),
                     planner.Step("withdraw", "minecraft:stick", 2, {"pos": b})]
            return [s.count for s in planner.forward([(s, {}, i) for i, s in enumerate(steps)], NullCost(), [])[0]]
        self.assertEqual(took([3, 64, 0], [3, 64, 0]), [4])         # must fail: two trips to one chest
        self.assertEqual(took([3, 64, 0], [9, 64, 0]), [2, 2])      # must fail: two chests' takes made one


class AlikeWaysAreOne(unittest.TestCase):
    def test_a_members_way_the_group_makes_alike_is_dropped(self):
        from bonobo.knowledge import sources
        # (group, the ways kept: (made, kind)) — must fail: twelve planks recipes searched one by one
        rows = [("planks", [("planks", "craft")]), ("bed", [("bed", "craft"), ("bed", "take")]),
                ("building", [(m, "mine") for m, _s in sources("building")])]
        for group, kept in rows:
            with self.subTest(group):
                self.assertEqual([(m, s[0]) for m, s in planner.uncovered(group, sources(group))], kept)

    def test_a_member_mined_elsewhere_is_kept(self):
        group = ("mine", ["coal_ore"], 0)
        member = ("mine", ["coal_ore", "deepslate_coal_ore"], 0)
        # must fail: a member whose blocks the group's way does not mine dropped as alike
        self.assertEqual(len(planner.uncovered("coal", [("coal", group), ("minecraft:coal", member)])), 2)


class AFasterToolPaysOrIsNotTried(unittest.TestCase):
    def test_the_tiers_offered_by_the_work(self):
        def tiers(n, later=0):
            search = planner.Search(NullCost())
            node = planner.Node(planner.from_bag(bag(inventory(("wooden_pickaxe", 1))), facts=NullCost().facts()), [], [])
            node.stack = [("need", "minecraft:cobblestone", later, 0, False)] if later else []
            step = planner.Step("mine", "minecraft:cobblestone", n, {"blocks": ["stone"], "tier": 0, "breaks": n})
            got = search.speed(node, step, 0)
            return {t[2] for c in (got or [node]) for t in c.stack if t[0] == "tool" and t[1] == "pickaxe"}
        # must fail: an iron pickaxe tried for 10 blocks (its least, with the stone pickaxe made for it alone, is
        # above what it saves there)
        self.assertNotIn(2, tiers(10))
        self.assertIn(2, tiers(500))        # must fail: never tried where 500 blocks pay for it
        self.assertIn(2, tiers(10, 500))    # must fail: cut for these 10 blocks though 500 more are still to mine


class APrepAloneStillCounts(unittest.TestCase):
    def test_the_work_behind_a_prep_is_in_the_bound(self):
        search = planner.Search(NullCost())
        node = planner.Node(planner.from_bag(bag(inventory(("wooden_pickaxe", 1))), facts=NullCost().facts()), [], [])
        later = planner.Step("mine", "minecraft:cobblestone", 500, {"blocks": ["stone"], "tier": 0, "breaks": 500})
        now = planner.Step("mine", "minecraft:cobblestone", 10, {"blocks": ["stone"], "tier": 0, "breaks": 10})
        node.stack = [("prep", later, 0)]          # its emit not queued yet
        # must fail: the 500 blocks behind a lone prep left out of what a faster pickaxe can save
        self.assertGreater(search.saves_at_most(node, "pickaxe", 0, 2, now), 0)


class ASubPlanIsTheSameUnderOtherItems(unittest.TestCase):
    def test_items_its_making_never_reads_still_find_it(self):
        search = planner.Search(NullCost())
        got = []
        for extra in ([], [("beef", 5)], [("dirt", 9)]):
            node = planner.Node(planner.from_bag(bag(inventory(*extra)), facts=NullCost().facts()), [], [])
            node.stack = [("need", "minecraft:stick", 4, 0, False)]
            size = len(search.memo)
            done = search.settled(node, math.inf)
            got.append((len(search.memo) - size, done.g, [(st.kind, st.token) for st in (s for s, _h, _x in done.steps)],
                        {k: v for k, v in done.inv.counts.items() if k in ("minecraft:beef", "minecraft:dirt")}))
        # must fail: searched again for a bag that differs only in beef or dirt (nothing sticks are made from)
        self.assertEqual([g[0] for g in got[1:]], [0, 0])
        self.assertEqual(len({(g[1], tuple(g[2])) for g in got}), 1)                  # the same plan, the same price
        self.assertEqual([g[3] for g in got], [{}, {"minecraft:beef": 5}, {"minecraft:dirt": 9}])   # its own items kept


class AFactLeftBoundsTheToolsPayback(unittest.TestCase):
    def test_a_diamond_axe_is_not_tried_for_four_logs(self):
        search = planner.Search(NullCost())
        node = planner.Node(planner.from_bag(bag(inventory()), facts=NullCost().facts()), [], [])
        node.stack = [("fact", "covered", True, 0)]      # a shelter still to make: its ways' work is in the table
        step = planner.Step("gather", "log", 4, {})
        got = search.speed(node, step, 0)
        tiers = {t[2] for c in (got or [node]) for t in c.stack if t[0] == "tool" and t[1] == "axe"}
        # must fail: a fact left made the payback unbounded (inf), so iron and diamond axes were tried for 4 logs
        self.assertLess(search.saves_at_most(node, "axe", -1, 3, step), math.inf)
        self.assertFalse(tiers & {2, 3}, tiers)


class TheNightsWaysBoundEachOther(unittest.TestCase):
    def test_a_bed_is_bounded_by_its_cheapest_way(self):
        from tests.world import cost
        c = cost(snapshot(), oak_log=30)
        search = planner.Search(c)
        node = planner.Node(planner.from_bag(bag(inventory()), None, None, c.reserved, c.facts()), [], [])
        node.stack = [("need", "bed", 1, 0, False)]
        # must fail: 20 ticks (the walks every way of a bed shares: none) for a bed whose every way walks to wool or
        # logs, so a dearer way of the night is searched uncapped before the cheap one bars it
        self.assertGreater(search.h(node), 300)

    def test_a_tools_payback_counts_its_craft(self):
        from tests.world import cost
        c = cost(snapshot(), oak_log=30)
        search = planner.Search(c)
        node = planner.Node(planner.from_bag(bag(inventory()), None, None, c.reserved, c.facts()), [], [])
        got = search.speed(node, planner.Step("gather", "log", 1, {}), 0)
        # must fail: a wooden axe tried for one log (its least counted the planks, not the craft that makes it)
        self.assertFalse({t[2] for c_ in (got or []) for t in c_.stack if t[0] == "tool" and t[1] == "axe"})


class AlikeOrdersAreOne(unittest.TestCase):
    def test_only_targets_that_share_are_permuted(self):
        from bonobo.planner import Target
        # (targets, orders planned) — must fail: 24 orders of four targets sharing the wood
        rows = [([Target("iron", [("minecraft:raw_iron", 3)], 0), Target("sand", [("minecraft:sand", 4)], 1)], 1),
                ([Target("logs", [("log", 8)], 0), Target("torches", [("minecraft:torch", 8)], 1),
                  Target("bed", [("bed", 1)], 2), Target("door", [("door", 1)], 3)], 24)]
        for targets, n in rows:
            with self.subTest(n):
                self.assertEqual(len(list(planner._orders(targets))), n)


class TheBoundKnowsTheTrip(unittest.TestCase):
    def test_a_gather_made_walks_once(self):
        from tests.world import cost
        snap = snapshot()
        c = cost(snap, oak_log=30)
        search = planner.Search(c)

        def h(facts):
            node = planner.Node(planner.from_bag(snap.inv, None, None, c.reserved, {**c.facts(), **facts}), [], [])
            node.stack = [("need", "log", 4, 0, False)]
            return search.h(node)
        # must fail: the walk to the trees counted again where the plan has gathered there (forward merges the repeat)
        self.assertLess(h({"trip log": True}), h({}))


if __name__ == "__main__":
    unittest.main()

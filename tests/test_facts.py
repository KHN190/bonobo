"""What the four quantities are GIVEN: the facts a state vector and a memory are supposed to carry.

V, Δt, p and κ are only as good as their arguments. This file is about the arguments — the half of the planner
that turns a world into a state:

  * being AT something means being able to work on it, not being near it in a straight line (a banned spot is not
    there: the walk is priced from where the work can be done);
  * what memory knows is read before anything goes exploring, and one disappointing look retires ONE note;
  * a station standing in the world is the same fact as one in the bag;
  * a thing that already exists can be taken, and is priced like anything else;
  * ground to build on is something you MAKE, at a price, not something you must find;
  * what we built is never a resource — the protection is on the near side of the one door that breaks blocks;
  * what is worth taking out of a chest is decided by price, never by a list of names.

Property-shaped and parameterised where the sweep applies, so each of these is one statement rather than a file.
"""
import math
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import world  # noqa: E402
from bonobo import brain, data, knowledge, loot, memory, nav, skill, skillcore  # noqa: E402,F401
from bonobo.cost import Cost, walk_ticks  # noqa: E402
from bonobo.planner import Step, plan_needs  # noqa: E402
from tests.world import FakeRegion, bag, flat, inventory, places_by  # noqa: E402


def mem():
    return memory.Memory(os.path.join(tempfile.mkdtemp(prefix="facts"), "notes.json"))


class Snap:
    dimension = "minecraft:overworld"
    night = False
    ticks_until_dusk = 6000

    def __init__(self, feet=(0, 64, 0)):
        self.feet = feet
        self.inv = bag(inventory())

    state = {"skyLight": 15, "health": 20, "food": 20}

    def get(self, key, default=None):
        return self.state.get(key, default)


# ------------------------------------------------------------------------------------------- being there at all

class BeingAtSomethingMeansBeingAbleToWorkOnIt(unittest.TestCase):
    """Radius cannot tell a step from a swim. The walk a step is priced with is to where its work can be done — a
    spot judged unreachable (banned: the rim of a flooded pit) is not there at all."""

    # (can the body get to it?, where memory has stone, if anywhere) → the walk the mine step is priced with
    ARRIVALS = [("nothing known: a search", True, None, None),
                ("must fail: stone under our feet, judged unreachable: a search", False, (1, 64, 1), None),
                ("stone under our feet", True, (1, 64, 1), (1, 64, 1)),
                ("stone at arm's length", True, (3, 64, 3), (3, 64, 3)),
                ("stone across the valley", True, (300, 64, 300), (300, 64, 300))]

    def test_arriving_removes_the_walk_from_the_plan(self):
        from bonobo.cost import UNKNOWN_WALK_TICKS
        for name, reachable, pos, where in self.ARRIVALS:
            with self.subTest(name):
                m = mem()
                if pos:
                    m.note_here("stone", pos, "minecraft:overworld")
                cost = Cost(Snap(), m, blacklist={} if reachable else {tuple(pos): float("inf")}, finds={})
                step = Step("mine", "minecraft:cobblestone", 1, {"blocks": ["stone"], "tier": 0, "breaks": 1})
                walk = cost.estimate(step) - cost.work(step)
                want = UNKNOWN_WALK_TICKS if where is None else walk_ticks(math.dist(Snap().feet, where))
                self.assertEqual(walk, want)

    def test_a_refused_route_is_not_there(self):
        """One reachability reading (cost.route_refused, the game's route cache): a remembered spot whose route was asked
        and not found is not there, for the walk and for Cost.reachable alike."""
        from bonobo import cost as costmod
        from bonobo.cost import UNKNOWN_WALK_TICKS
        pos = (3, 64, 3)
        # (situation, the game's answer to the route there) → (the walk priced, reachable)
        rows = [("not asked: there", None, (walk_ticks(math.dist(Snap().feet, pos)), True)),
                ("asked and found: there", (True, 2.0), (walk_ticks(math.dist(Snap().feet, pos)), True)),
                ("must fail: asked and not found: a search, not a walk to it", (False, None), (UNKNOWN_WALK_TICKS, False))]
        for name, answer, want in rows:
            with self.subTest(name):
                m = mem()
                m.note_here("stone", pos, "minecraft:overworld")
                key = costmod.route_key(pos, 2.0, costmod.NAV_NODES)
                saved = dict(costmod.ROUTES)
                try:
                    if answer is not None:
                        costmod.ROUTES[key] = answer
                    cost = Cost(Snap(), m, finds={})
                    step = Step("mine", "minecraft:cobblestone", 1, {"blocks": ["stone"], "tier": 0, "breaks": 1})
                    self.assertEqual((cost.estimate(step) - cost.work(step), cost.reachable(["stone"])), want)
                finally:
                    costmod.ROUTES.clear()
                    costmod.ROUTES.update(saved)


# ----------------------------------------------------------------------------------- what memory is for

class WhatWasWrittenDownIsReadBack(unittest.TestCase):
    # (a tree noted?, does the walk there arrive?, is a log there on arrival?) →
    #   (went somewhere new, where it walked first, explored, the note kept, the spot banned)
    GO_FIND = [("a noted tree, reached, still there", True, True, True, (True, (30, 64, 0), False, True, False)),
               ("a noted tree, reached, felled since: retired, then look around", True, True, False,
                (True, (30, 64, 0), True, False, False)),
               ("must fail: a noted tree that cannot be reached: the route banned, the note kept", True, False, False,
                (True, (30, 64, 0), True, True, True)),
               ("nothing noted: look around", False, None, None, (True, None, True, False, False))]

    def test_where_to_look_reads_memory_before_exploring(self):
        from unittest import mock
        from bonobo import dispatch
        from bonobo.planner import Step
        for name, noted, arrives, there, (want_new, want_first, want_explore, want_kept, want_ban) in self.GO_FIND:
            with self.subTest(name):
                m = mem()
                if noted:
                    m.note_seen("tree", (30, 64, 0), "minecraft:overworld")
                ctx = skillcore.Context(m, None, "minecraft:overworld", blacklist={})
                ctx.ban_counts = {}
                walks = []

                def arrived(pos, policy, range_=1.5, **kw):
                    walks.append(tuple(pos))
                    return arrives
                with mock.patch.object(world, "feet", return_value=(0, 64, 0)), \
                        mock.patch.object(dispatch.nav, "arrived_near", side_effect=arrived), \
                        mock.patch.object(dispatch, "still_there", return_value=bool(there)), \
                        mock.patch.object(dispatch.explore, "seek_blocks", return_value=[(5, 64, 5)]) as explore:
                    got = dispatch.go_find(ctx, Step("gather", "log", 4))
                self.assertEqual(got, want_new)
                self.assertEqual(walks[0] if walks else None, want_first)
                self.assertEqual(explore.called, want_explore)
                self.assertEqual(bool(m.seen("tree", "minecraft:overworld")), want_kept)
                self.assertEqual(ctx.blocked((30, 64, 0)), want_ban)

    def test_one_look_retires_one_note(self):
        """Retiring every note within a radius is how "could not find stone" survived a memory holding fourteen
        stone points."""
        m = mem()
        for pos in ((10, 64, 10), (40, 64, 10), (200, 64, 200)):
            m.note_here("stone", pos, "minecraft:overworld")
        before = {tuple(r["pos"]) for r in m.seen("stone", "minecraft:overworld")}
        m.confirm("stone", (10, 64, 10), "minecraft:overworld", found=False)
        left = {tuple(r["pos"]) for r in m.seen("stone", "minecraft:overworld")}
        self.assertEqual(before - left, {(10, 64, 10)}, "exactly the note we stood on, and no other")



class AStationStandingThereIsOneWeHave(unittest.TestCase):
    # (where the machine was built, in which world, its tags) → {station: one we use where it stands} (cost.station_near)
    ROWS = [("a smelter two blocks away", (2, 64, 0), "minecraft:overworld", ("smelting",),
             {"minecraft:furnace": True, "minecraft:crafting_table": False}),
            ("at the edge of reach (STATION_R)", "edge", "minecraft:overworld", ("smelting",),
             {"minecraft:furnace": True}),
            ("must fail: just past it", "past", "minecraft:overworld", ("smelting",), {"minecraft:furnace": False}),
            ("across the valley", (300, 64, 300), "minecraft:overworld", ("smelting",), {"minecraft:furnace": False}),
            ("two blocks away, in another world", (2, 64, 0), "minecraft:the_nether", ("smelting",),
             {"minecraft:furnace": False}),
            ("a crafting machine", (3, 64, 0), "minecraft:overworld", ("crafting",),
             {"minecraft:crafting_table": True, "minecraft:furnace": False}),
            ("a machine that provides no station", (2, 64, 0), "minecraft:overworld", ("storage",),
             {"minecraft:furnace": False, "minecraft:crafting_table": False})]

    def test_a_station_standing_there(self):
        from bonobo.data import STATION_R
        feet = Snap().feet
        for name, pos, dim, tags, want in self.ROWS:
            with self.subTest(name):
                if pos in ("edge", "past"):
                    pos = (feet[0] + STATION_R + (1 if pos == "past" else 0), feet[1], feet[2])
                m = mem()
                m.add_machine("machine-1", pos, 0, dim, tags)
                cost = Cost(Snap(), m, finds={})
                self.assertEqual({k: cost.station_near(k) for k in want}, want)


# --------------------------------------------------------------------------- what the world already has made

class WhatExistsCanBeTaken(unittest.TestCase):
    """A village is a bag of finished goods. Taking one is a column like any other, priced like any other."""

    def test_every_takeable_thing(self):
        """Over knowledge.TAKEABLE: a take way exists for what each row gives, and its call needs exactly the tool
        the row names (or none)."""
        from bonobo.planner import way
        for token, row in data.TAKEABLE.items():
            with self.subTest(token):
                takes = [(made, src) for made, src in knowledge.sources(token) if src[0] == "take"]
                self.assertEqual(len(takes), 1)
                step = way(takes[0][1], takes[0][0], 1)[0]
                self.assertEqual((step.kind, step.detail["blocks"]), ("take", list(row["blocks"])))
                tools = {d for d in knowledge.step_call(step) if d.startswith("tool:")}
                self.assertEqual(tools, {f"tool:{row['tool'][0]}:{row['tool'][1]}"} if row["tool"] else set())

    def test_memory_can_see_them(self):
        """The travel scan asks for every takeable block and notes the hits worth keeping (the fake answers them all, so this is
        about what is asked and kept, not about how many one real /find returns)."""
        from unittest import mock
        from bonobo import explore

        def find(blocks, radius=48, limit=1):
            return [{"x": i * 20, "y": 64, "z": 0, "block": f"minecraft:{b}"} for i, b in enumerate(blocks)]
        m = mem()
        with mock.patch.object(explore, "find", side_effect=find), mock.patch.object(explore, "entities",
                                                                                      return_value=[]):
            explore.note_around(m, "minecraft:overworld", (0, 64, 0))
        seen = {r["kind"] for r in m.data["seen"]}
        # Village furniture worth taking and crops are kept; stations, containers and the rest are not memory's (data.seen_class:
        # memory.stations / containers hold ours, /find answers the rest).
        self.assertEqual(sorted(t for t, row in data.TAKEABLE.items() if set(row["blocks"]) & seen),
                         ["bed", "door", "minecraft:beetroot", "minecraft:carrot", "minecraft:melon_slice",
                          "minecraft:potato", "minecraft:pumpkin", "minecraft:wheat", "wool"])

    def test_near_is_taken_and_far_is_made(self):
        """A bed standing near (remembered) is taken; one across the world is not walked to when making is cheaper."""
        def kinds(pos):
            m = mem()
            m.note_seen("white_bed", pos, "minecraft:overworld")
            cost = Cost(Snap(), m, finds={"minecraft:sheep": 8.0})
            return [s.kind for s in plan_needs(bag(inventory()), [("bed", 1)], cost)]
        self.assertEqual(kinds((4, 64, 0)), ["take"])
        self.assertNotIn("take", kinds((4000, 64, 0)))


class GroundIsSomethingYouMake(unittest.TestCase):
    """"no clear spot for shelter within 6 blocks" — in a forest, with a bag of blocks and a pickaxe. Levelling is
    work, so it belongs in the price, not in a yes/no."""

    def options(self, region, radius=6):
        from bonobo import blueprints, building
        return building.spot_options(blueprints.SHELTER, (0, 64, 0), region, nav.Policy(), radius=radius)

    def region(self, change):
        region = flat()
        if change == "logs":
            for y in (64, 65, 66):
                region.blocks[(1, y, 0)] = "oak_log"
        elif change == "hole":
            for x in range(0, 2):
                del region.blocks[(x, 63, 0)]
        elif change == "bedrock":
            for x in range(-8, 9):
                for z in range(-8, 9):
                    for y in (64, 65, 66):
                        region.blocks[(x, y, z)] = "bedrock"
        return region

    # (the ground, search radius) → the best option (price, spot, turns, work), or None when nothing is offered
    GROUND = [("ready ground: free, no work", None, 6, (0, (0, 64, 0), 0, ())),
              ("a trunk in the way: three breaks", "logs", 0,
               (3, (0, 64, 0), 0, (("break", (1, 66, 0)), ("break", (1, 64, 0)), ("break", (1, 65, 0))))),
              ("a two-block hole: filled, not avoided", "hole", 0,
               (2, (0, 64, 0), 0, (("fill", (0, 63, 0)), ("fill", (1, 63, 0))))),
              ("must fail: bedrock everywhere: nothing offered", "bedrock", 6, None)]

    def test_ground_over_the_table(self):
        for name, change, radius, want in self.GROUND:
            with self.subTest(name):
                got = self.options(self.region(change), radius=radius)
                self.assertEqual(got[0] if got else None, want)


class WhatWeBuiltIsNotAResource(unittest.TestCase):
    """The night the shelter went up, the agent mined it for the cobblestone. One door breaks a single cell, and
    the protection is on this side of it."""

    class Policy:
        def __init__(self, protected=()):
            self.protected = set(protected)

    # (what is ours, the cell asked for) → refused, or the one mine task posted to the game
    DOOR = [("must fail: our own wall", {(1, 64, 1)}, (1, 64, 1), None),
            ("the block beside our wall", {(1, 64, 1)}, (2, 64, 1), (2, 64, 1)),
            ("nothing of ours anywhere", set(), (1, 64, 1), (1, 64, 1)),
            ("one of many cells of ours", {(x, 64, 1) for x in range(5)}, (3, 64, 1), None)]

    def test_the_door_refuses_our_own_blocks(self):
        from unittest import mock
        from bonobo.api import NotAvailable
        from tests.world import bag, inventory
        for name, ours, cell, posted in self.DOOR:
            sent = []
            with self.subTest(name), mock.patch.object(skillcore.api, "run", side_effect=lambda t, wait=0, awaits=None: sent.append(
                    (t["type"], (t["x"], t["y"], t["z"]))) or {"status": "succeeded"}), \
                    mock.patch.object(skillcore, "Inventory", lambda: bag(inventory())):
                if posted is None:
                    with self.assertRaises(NotAvailable):
                        skillcore.mine_cell(self.Policy(ours), cell)
                    self.assertEqual(sent, [])
                else:
                    skillcore.mine_cell(self.Policy(ours), cell)
                    self.assertEqual(sent, [("mine", posted)])

    # fixture: (module source) → the lines that build a bare {"type": "mine"} task
    BARE = [("a literal mine task", "t = {'type': 'mine', 'x': 1}\n", [1]),
            ("must fail: another task type", "t = {'type': 'place'}\n", []),
            ("a mine task built by the funnel", "t = nav.mine_task(c)\n", []),
            ("two in one module", "a = {'type': 'mine'}\nb = 2\nc = {'type': 'mine'}\n", [1, 3]),
            ("a key named type with a variable value", "t = {'type': kind}\n", [])]

    def test_bare_mine_over_the_fixture(self):
        for name, src, want in self.BARE:
            with self.subTest(name):
                self.assertEqual(bare_mine_lines(src), want)

    def test_no_module_posts_a_bare_mine_task(self):
        pkg = pathlib.Path(__file__).resolve().parent.parent / "bonobo"
        allowed = {"building.py", "dragon.py", "wood.py", "farming.py", "nav.py", "skillcore.py"}
        offenders = [f"{p.name}:{line}" for p in sorted(pkg.glob("*.py")) if p.name not in allowed
                     for line in bare_mine_lines(p.read_text())]
        self.assertEqual(offenders, [], f"these break blocks without the protection door: {offenders}")


def bare_mine_lines(src):
    """Pure: lines of dict literals {"type": "mine", ...} in a module's source (its AST): a task that breaks a block
    without the protection door (`nav.mine_task` / `skillcore.mine_cell`)."""
    import ast
    return sorted(node.lineno for node in ast.walk(ast.parse(src)) if isinstance(node, ast.Dict)
                  for key, val in zip(node.keys, node.values)
                  if isinstance(key, ast.Constant) and key.value == "type"
                  and isinstance(val, ast.Constant) and val.value == "mine")


class WhatIsWorthTakingIsDecidedByPrice(unittest.TestCase):
    """A hand-written list of loot had no wheat in it, so the agent opened a village chest and took nothing."""

    def slots(self, *items):
        return [{"slot": i, "id": item, "count": n, "owner": owner}
                for i, (item, n, owner) in enumerate(items)]

    PRICES = {"minecraft:iron_ingot": 120.0, "minecraft:wheat": 6.0, "minecraft:stick": 0.2,
              "minecraft:diamond": 900.0}

    # (situation, chest slots (item, count, owner), free slots) → the slot indices taken, dearest first
    LOOT = [("value decides, the dearest first", [("minecraft:wheat", 20, "chest"), ("minecraft:diamond", 1, "chest")],
             20, [1, 0]),
            ("worth less than the slot it eats, when slots are tight", [("minecraft:stick", 1, "chest")], 2, []),
            ("the same stick with room to spare", [("minecraft:stick", 1, "chest")], 30, [0]),
            ("must fail: our own slots are never loot", [("minecraft:diamond", 1, "player")], 30, []),
            ("what has no price is left", [("minecraft:mystery", 4, "chest")], 30, []),
            ("an empty chest", [], 30, [])]

    def test_loot_plan(self):
        for name, items, free, want in self.LOOT:
            with self.subTest(name):
                self.assertEqual(loot.loot_plan(self.slots(*items), self.PRICES, bag_free=free), want)


class TheBodyIsAStateLikeAnyOther(unittest.TestCase):
    """What the body can do where it stands is facts, not special cases — so it is swept, not spot-checked.

    Swimming, drowning and falling are values of the body (knowledge.body_facts), which means every plan asks
    about them. What belongs HERE: that the facts exist, that the work which needs them says so (its contract's
    `when`), and that a body missing one can always plan its way back — otherwise the plan empties and the agent
    floats there deciding nothing, which is exactly what happened.
    """

    STEPS = {"craft": (Step("craft", "minecraft:furnace", 1, {"times": 1, "inputs": {}}), ("hands_free", "footing")),
             "mine": (Step("mine", "minecraft:coal", 1, {"blocks": ["coal_ore"], "tier": 0, "breaks": 1}),
                      ("hands_free", "footing")),
             "take": (Step("take", "bed", 1, {"blocks": ["white_bed"]}), ("hands_free", "footing")),
             "smelt": (Step("smelt", "minecraft:iron_ingot", 1, {"input": "minecraft:raw_iron", "fuel": "coal",
                                                                   "inputs": {}}), ("hands_free", "footing")),
             "gather": (Step("gather", "log", 1, {}), ("hands_free",)),                  # a tree needs no floor
             "hunt": (Step("hunt", "wool", 1, {"types": ["minecraft:sheep"], "kills": 1}), ("hands_free",))}

    # (the body's readings) → the facts it has (knowledge.body_facts)
    BODIES = [("ready", {"onGround": True}, {"footing": True, "hands_free": True}),
              ("swimming", {"inWater": True, "onGround": False}, {"footing": False, "hands_free": True}),
              ("wading on the bottom is footing", {"inWater": True, "onGround": True},
               {"footing": True, "hands_free": True}),
              ("drowning", {"inWater": True, "onGround": False, "air": knowledge.DROWNING_TICKS},
               {"footing": False, "hands_free": False}),
              ("boundary: one tick above drowning", {"air": knowledge.DROWNING_TICKS + 1},
               {"footing": True, "hands_free": True}),
              ("falling", {"onGround": False, "fallDistance": knowledge.FALL_TAKES_HANDS + 1},
               {"footing": False, "hands_free": False}),
              ("must fail: the player holds the controls", {"control": {"paused": True}},
               {"footing": True, "hands_free": False}),
              ("no readings: a standing body", None, {"footing": True, "hands_free": True})]

    def test_the_body_as_facts(self):
        for name, state, want in self.BODIES:
            with self.subTest(name):
                self.assertEqual(knowledge.body_facts(state), {**want, "night": False, "covered": False})

    def test_every_step_that_touches_the_world_says_what_body_it_needs(self):
        for name, (step, needs) in self.STEPS.items():
            with self.subTest(step=name):
                asked = dict(skill.when_of_step(step, {}))
                for fact in needs:
                    self.assertIs(asked.get(fact), True, f"{name} does not say it needs {fact}")

    def test_a_body_that_cannot_work_can_always_plan_its_way_back(self):
        """For every lost fact a mend can restore, the plan starts with it, and it is priced: never free."""
        from bonobo.planner import NullCost
        for name, state, _facts in self.BODIES:
            facts = knowledge.body_facts(state)
            if not (facts["footing"] and facts["hands_free"]) and not (state or {}).get("control") and not (state or {}).get("fallDistance"):
                with self.subTest(name):
                    cost = NullCost()
                    cost.facts = lambda f=facts: {"dimension": "minecraft:overworld", **f}
                    steps = plan_needs(bag(inventory(("stone_pickaxe", 1))), [("minecraft:cobblestone", 1)], cost)
                    mends = [s for s in steps if s.kind == "reach"]
                    self.assertTrue(mends, [str(s) for s in steps])
                    self.assertEqual(steps[-1].kind, "mine")
                    self.assertTrue(all(s.est > 0 for s in mends))

    def test_the_mends_are_alternatives_and_the_cheapest_is_taken(self):
        """More than one way back to footing (swim to the shore, or put a block underfoot): both are offered."""
        self.assertEqual(sorted(knowledge.fact_steps("footing", True)), [("reach", "footing"), ("reach", "land")])


class ARoundReadsEachThingOnce(unittest.TestCase):
    """Cost's cells-not-there, site and world.sight_pos are kept within a round: the same inputs give the same answer
    as reading afresh, a changed input (a ban, a new look) is read again, and a round's repeats cost one read."""

    HITS = [{"x": 3, "y": 64, "z": 0, "distance": 3.0, "block": "minecraft:stone"},
            {"x": 6, "y": 64, "z": 0, "distance": 6.0, "block": "minecraft:stone"}]

    def test_kept_answers_are_the_fresh_ones(self):
        import time
        from unittest import mock
        from bonobo import cost as costmod
        from bonobo.skillcore import banned
        step = Step("mine", "minecraft:cobblestone", 1, {"blocks": ["stone"], "tier": 0, "breaks": 1})
        with mock.patch.dict(world._SIGHT, {"hits": {"stone": list(self.HITS)}, "memo": {}, "v": 0}), \
                mock.patch.dict(costmod.ROUTES, {}, clear=True):
            c = Cost(Snap(), mem(), finds={})
            gone = c.not_there(True)
            for p in [(3, 64, 0), (6, 64, 0), (9, 9, 9)]:
                with self.subTest(cell=p):
                    self.assertEqual(p in gone, banned(c.blacklist, p) or p in (c.protected() or ()))
            first = c.site(step)
            self.assertEqual(first, (3, 64, 0))
            self.assertEqual(c.site(step), first)                       # same inputs: same answer
            # a route answer flipped in place (the table's size unchanged): read again
            key = world.route_key((3, 64, 0), 2.0, data.NAV_NODES)
            costmod.ROUTES[key] = (True, 1.0)
            self.assertEqual(c.site(step), (3, 64, 0))
            costmod.ROUTES[key] = (False, None)
            self.assertEqual(c.site(step), (6, 64, 0))      # must fail: kept by the table's size, the flip unseen
            costmod.ROUTES[key] = (True, 1.0)
            c.blacklist[(3, 64, 0)] = time.time() + 60
            self.assertEqual(c.site(step), (6, 64, 0))      # must fail: a kept answer outliving the ban that changed it
            world._SIGHT["hits"] = {"stone": [dict(self.HITS[1], x=7)]}
            self.assertEqual(world.sight_pos(["stone"]), (7, 64, 0))   # must fail: a kept answer from the last look

    def test_providers_follow_the_registry(self):
        from unittest import mock
        from bonobo import skill as skillkit
        knowledge.producers()
        before = skillkit.providers("item:log")
        fake = type("C", (), {"provides": {"item:log": None}, "prefer": 99})()
        with mock.patch.dict(skillkit.REGISTRY, {"_fake_chop": fake}):
            self.assertIs(skillkit.providers("item:log")[0], fake)    # must fail: kept from before the write
        self.assertEqual(skillkit.providers("item:log"), before)

    def test_a_rounds_repeats_cost_one_read(self):
        from unittest import mock
        from bonobo import cost as costmod
        step = Step("mine", "minecraft:cobblestone", 1, {"blocks": ["stone"], "tier": 0, "breaks": 1})
        with mock.patch.dict(world._SIGHT, {"hits": {"stone": list(self.HITS)}, "memo": {}, "v": 0}), \
                mock.patch.dict(costmod.ROUTES, {}, clear=True):
            c = Cost(Snap(), mem(), finds={})
            with mock.patch.object(Cost, "_nearest", autospec=True, return_value=None) as read, \
                    mock.patch.object(costmod, "_Gone", wraps=costmod._Gone) as built:
                for _ in range(1000):
                    c.site(step)
                    _ = (3, 64, 0) in c.not_there(True)
            self.assertEqual(read.call_count, 1)
            self.assertLessEqual(built.call_count, 2)          # one per kind of skip, not one per ask


class TheSoilIsWhatPerceptionRead(unittest.TestCase):
    """Cost's dig price reads the soil column in the blocks perception read, never the world again; unread: the prior."""

    def test_rows(self):
        from unittest import mock
        from bonobo import perception

        def column(dirt):
            blocks = {(0, y, 0): "stone" for y in range(50, 64 - dirt)}
            blocks.update({(0, y, 0): "dirt" for y in range(64 - dirt, 64)})
            return FakeRegion((-1, 50, -1), (1, 70, 1), blocks)
        # (situation, perception's region) → the soil the cost model prices
        rows = [("nothing read: the prior", None, data.SOIL_DEPTH),
                ("must fail: two dirt over rock, read", column(2), 2),
                ("seven dirt over rock, read", column(7), 7)]
        for name, region, want in rows:
            with self.subTest(name), mock.patch.object(perception.STATE, "region", region):
                ground = perception.price_inputs(dict(Snap.state, timeOfDay=0))["ground"]
                self.assertEqual(Cost(Snap(), mem(), finds={}, region=ground).soil(), want)
            with self.subTest(f"{name}: priced from its input alone"), \
                    mock.patch.object(perception.STATE, "region", column(5)):
                self.assertEqual(Cost(Snap(), mem(), finds={}, region=region).soil(), want)


class TheNightIsAFact(unittest.TestCase):
    """The night is in the plan (S4, R3): surface work waits for the day, other work goes under cover first — the
    plan prices the shelter or the sleep like any other step. Within one plan the night does not pass (no clock)."""

    # (the body's readings) → (night, covered)
    BODIES = [("midnight", {"timeOfDay": 18000}, (True, False)),
              ("must fail: noon", {"timeOfDay": 6000}, (False, False)),
              ("midnight in the Nether", {"timeOfDay": 18000, "dimension": data.NETHER}, (False, False)),
              ("under rock", {"skyLight": 0}, (False, True)),
              ("must fail: open sky", {"skyLight": 15}, (False, False))]

    def test_the_night_and_the_cover_as_facts(self):
        for name, state, want in self.BODIES:
            with self.subTest(name):
                got = knowledge.body_facts(state)
                self.assertEqual((got["night"], got["covered"]), want)

    # (situation, step, at night?) → what it asks of the night
    STEPS = [("chop", "gather", True, ("night", False)), ("hunt", "hunt", True, ("night", False)),
             ("take", "take", True, ("night", False)), ("mine", "mine", True, ("covered", True)),
             ("craft", "craft", True, ("covered", True)), ("smelt", "smelt", True, ("covered", True)),
             ("must fail: mine by day", "mine", False, None), ("chop by day", "gather", False, None)]

    def test_what_work_asks_of_the_night(self):
        steps = TheBodyIsAStateLikeAnyOther.STEPS
        for name, kind, night, want in self.STEPS:
            with self.subTest(name):
                asked = dict(skill.when_of_step(steps[kind][0], {"night": night}))
                got = next(((f, asked[f]) for f in ("night", "covered") if f in asked), None)
                self.assertEqual(got, want)

    def test_the_ways_through_the_night(self):
        self.assertTrue({("sleep", "bed"), ("wait", "day")} <= set(knowledge.fact_steps("night", False)))
        self.assertTrue({("shelter", "dig in"), ("shelter", "wall in"), ("shelter", "hut")}
                        <= set(knowledge.fact_steps("covered", True)))

    # (night, covered, need) → the step the plan starts with
    PLANS = [("must fail: night in the open, stone: under cover first", True, False, "minecraft:cobblestone", "shelter"),
             ("night under rock, stone: mined", True, True, "minecraft:cobblestone", "mine"),
             ("day in the open, stone: mined", False, False, "minecraft:cobblestone", "mine"),
             ("must fail: night, logs: the night over first", True, True, "log", None),
             ("day, logs: chopped", False, False, "log", "gather")]

    def test_the_night_in_the_plan(self):
        from bonobo.planner import NullCost
        for name, night, covered, need, first in self.PLANS:
            with self.subTest(name):
                cost = NullCost()
                facts = {**knowledge.body_facts(None), "night": night, "covered": covered}
                cost.facts = lambda f=facts: {"dimension": "minecraft:overworld", **f}
                steps = plan_needs(bag(inventory(("stone_pickaxe", 1))), [(need, 1)], cost)
                if first is None:
                    self.assertIn((steps[0].kind, steps[0].token), {("sleep", "bed"), ("wait", "day")})
                else:
                    self.assertEqual(steps[0].kind, first)


if __name__ == "__main__":
    unittest.main()

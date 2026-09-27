"""What the four quantities are GIVEN: the facts a state vector and a memory are supposed to carry.

V, Δt, p and κ are only as good as their arguments. This file is about the arguments — the half of the planner
that turns a world into a state:

  * being AT something means being able to work on it, not being near it in a straight line;
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
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions, brain, knowledge, loot, memory, nav, skillcore  # noqa: E402
from bonobo.solve import solve  # noqa: E402
from tests.world import FakeRegion, bag, flat, inventory, places, places_by  # noqa: E402


def mem():
    return memory.Memory(os.path.join(tempfile.mkdtemp(prefix="facts"), "notes.json"))


class Snap:
    dimension = "minecraft:overworld"
    night = False
    ticks_until_dusk = 6000

    def __init__(self, feet=(0, 64, 0)):
        self.feet = feet
        self.inv = bag(inventory())

    def get(self, key, default=None):
        return {"skyLight": 15, "health": 20, "food": 20}.get(key, default)


# ------------------------------------------------------------------------------------------- being there at all

class BeingAtSomethingMeansBeingAbleToWorkOnIt(unittest.TestCase):
    """Radius cannot tell a step from a swim. `at:` is what the mine step requires, so it must mean reachable —
    standing on the rim of a flooded pit with the coal five blocks away is not being at the coal."""

    def vector(self, reachable=None, kind="stone", pos=(1, 64, 1)):
        m = mem()
        m.note_here(kind, pos, "minecraft:overworld")
        return actions.state_of(Snap(), m, reachable=reachable)

    # (can the body get to it?, where the note is) → at:stone in the state vector
    AT = [("within reach, reachable", True, (1, 64, 1), 1),
          ("within reach, unreachable (the rim of a flooded pit)", False, (1, 64, 1), None),
          ("far away, however reachable", True, (300, 64, 300), None),
          ("far away and unreachable", False, (300, 64, 300), None),
          ("no one asked about the route: the radius alone", None, (1, 64, 1), 1)]

    def test_being_at_over_the_table(self):
        for name, reachable, pos, want in self.AT:
            with self.subTest(name):
                ask = None if reachable is None else (lambda kinds, _r=reachable: _r)
                self.assertEqual(self.vector(reachable=ask, pos=pos).get(actions.at("stone")), want)

    # (where memory has stone, if anywhere) → does the plan for one cobblestone still walk to stone?
    ARRIVALS = [("nothing known", None, True), ("stone under our feet", (1, 64, 1), False),
                ("stone at arm's length", (3, 64, 3), False), ("stone across the valley", (300, 64, 300), True)]

    def test_arriving_removes_the_walk_from_the_plan(self):
        cost = places(40.0)
        for name, pos, walks in self.ARRIVALS:
            with self.subTest(name):
                m = mem()
                if pos:
                    m.note_here("stone", pos, "minecraft:overworld")
                state = actions.state_of(Snap(), m) | {"tool:pickaxe:0": 1, "uses:pickaxe": 59}
                plan = solve(actions.table(cost, state), state, {"minecraft:cobblestone": 1})
                self.assertEqual(bool(plan.counts.get("seek:stone")), walks)


# ----------------------------------------------------------------------------------- what memory is for

class WhatWasWrittenDownIsReadBack(unittest.TestCase):
    # (a tree noted?, does the walk there arrive?, is a log there on arrival?) →
    #   (went somewhere new, where it walked first, explored, the note kept, the spot banned)
    GO_FIND = [("a noted tree, reached, still there", True, True, True, (True, (30, 64, 0), False, True, False)),
               ("a noted tree, reached, felled since: retired, then look around", True, True, False,
                (True, (30, 64, 0), True, False, False)),
               ("a noted tree that cannot be reached: the route banned, the note kept", True, False, False,
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
                with mock.patch.object(dispatch.nav, "feet_now", return_value=(0, 64, 0)), \
                        mock.patch.object(dispatch.nav, "arrived", side_effect=arrived), \
                        mock.patch.object(dispatch, "still_there", return_value=bool(there)), \
                        mock.patch.object(dispatch.skills, "seek_blocks", return_value=[(5, 64, 5)]) as explore:
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
    # (where the machine was built, in which world, its tags) → {station: value} it puts in the state vector
    ROWS = [("a smelter two blocks away", (2, 64, 0), "minecraft:overworld", ("smelting",),
             {"minecraft:furnace": 1, "minecraft:crafting_table": None}),
            ("at the edge of reach (8 blocks)", (8, 64, 0), "minecraft:overworld", ("smelting",),
             {"minecraft:furnace": 1}),
            ("just past it (9 blocks)", (9, 64, 0), "minecraft:overworld", ("smelting",), {"minecraft:furnace": None}),
            ("across the valley", (300, 64, 300), "minecraft:overworld", ("smelting",), {"minecraft:furnace": None}),
            ("two blocks away, in another world", (2, 64, 0), "minecraft:the_nether", ("smelting",),
             {"minecraft:furnace": None}),
            ("a crafting machine", (3, 64, 0), "minecraft:overworld", ("crafting",),
             {"minecraft:crafting_table": 1, "minecraft:furnace": None}),
            ("a machine that provides no station", (2, 64, 0), "minecraft:overworld", ("storage",),
             {"minecraft:furnace": None, "minecraft:crafting_table": None})]

    def test_a_station_standing_there(self):
        for name, pos, dim, tags, want in self.ROWS:
            with self.subTest(name):
                m = mem()
                m.add_machine("machine-1", pos, 0, dim, tags)
                x = actions.state_of(Snap(), m)
                self.assertEqual({k: x.get(k) for k in want}, want)


# --------------------------------------------------------------------------- what the world already has made

class WhatExistsCanBeTaken(unittest.TestCase):
    """A village is a bag of finished goods. Taking one is a column like any other, priced like any other."""

    def test_every_takeable_thing(self):
        """Over knowledge.TAKEABLE: a take column exists, costs time, produces exactly what the row gives, needs
        standing at it, and needs exactly the tool the row names (or none)."""
        from bonobo.data import GROUPS, RECIPES
        table = {a.name: a for a in actions.table(places(20.0), {})}
        for token, row in knowledge.TAKEABLE.items():
            with self.subTest(token):
                action = table[f"take:{token}"]
                self.assertEqual(action.cost_s > 0, True)
                self.assertEqual({k: action.effect.get(k) for k in row["gives"]}, dict(row["gives"]))
                self.assertEqual(len([d for d in action.requires if d.startswith("at:")]), 1)
                tools = {d for d in action.requires if d.startswith("tool:")}
                self.assertEqual(tools, {actions.tool_dim(*row["tool"])} if row["tool"] else set())
                self.assertEqual([g for g in row["gives"] if not (g in GROUPS or g in RECIPES
                                                                  or g.startswith("minecraft:"))], [])

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
            explore.note_around(m, "minecraft:overworld")
        seen = {r["kind"] for r in m.data["seen"]}
        # Village furniture worth taking and crops are kept; stations, containers and the rest are not memory's (data.seen_class:
        # memory.stations / containers hold ours, /find answers the rest).
        self.assertEqual(sorted(t for t, row in knowledge.TAKEABLE.items() if set(row["blocks"]) & seen),
                         ["bed", "door", "minecraft:beetroot", "minecraft:carrot", "minecraft:melon_slice",
                          "minecraft:potato", "minecraft:pumpkin", "minecraft:wheat", "wool"])

    def test_near_is_taken_and_far_is_made(self):
        village = set(knowledge.TAKEABLE["bed"]["blocks"])

        def plan(village_s):
            cost = places_by(lambda kinds: village_s if village & set(kinds) else 30.0)
            state = {"bag_free": 20, "tool:pickaxe:0": 1, "uses:pickaxe": 100}
            return [a.name for a, _n in solve(actions.table(cost, state), state, {"bed": 1}).steps()]
        self.assertIn("take:bed", plan(10.0))
        self.assertIn("craft:bed", plan(4000.0))


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
              ("bedrock everywhere: nothing offered", "bedrock", 6, None)]

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
    DOOR = [("our own wall", {(1, 64, 1)}, (1, 64, 1), None),
            ("the block beside our wall", {(1, 64, 1)}, (2, 64, 1), (2, 64, 1)),
            ("nothing of ours anywhere", set(), (1, 64, 1), (1, 64, 1)),
            ("one of many cells of ours", {(x, 64, 1) for x in range(5)}, (3, 64, 1), None)]

    def test_the_door_refuses_our_own_blocks(self):
        from unittest import mock
        from bonobo.api import NotAvailable
        from tests.world import bag, inventory
        for name, ours, cell, posted in self.DOOR:
            sent = []
            with self.subTest(name), mock.patch.object(skillcore.api, "run", side_effect=lambda t, wait=0: sent.append(
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
            ("another task type", "t = {'type': 'place'}\n", []),
            ("a mine task built by the funnel", "t = nav.mine_task(c)\n", []),
            ("two in one module", "a = {'type': 'mine'}\nb = 2\nc = {'type': 'mine'}\n", [1, 3]),
            ("a key named type with a variable value", "t = {'type': kind}\n", [])]

    def test_bare_mine_over_the_fixture(self):
        for name, src, want in self.BARE:
            with self.subTest(name):
                self.assertEqual(bare_mine_lines(src), want)

    def test_no_module_posts_a_bare_mine_task(self):
        pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")
        allowed = {"building.py", "end.py", "wood.py", "farming.py", "nav.py", "skillcore.py"}
        offenders = []
        for name in sorted(os.listdir(pkg)):
            if name.endswith(".py") and name not in allowed:
                with open(os.path.join(pkg, name)) as f:
                    offenders += [f"{name}:{line}" for line in bare_mine_lines(f.read())]
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
            ("our own slots are never loot", [("minecraft:diamond", 1, "player")], 30, []),
            ("what has no price is left", [("minecraft:mystery", 4, "chest")], 30, []),
            ("an empty chest", [], 30, [])]

    def test_loot_plan(self):
        for name, items, free, want in self.LOOT:
            with self.subTest(name):
                self.assertEqual(loot.loot_plan(self.slots(*items), self.PRICES, bag_free=free), want)


class TheBodyIsAStateLikeAnyOther(unittest.TestCase):
    """What the body can do where it stands is dimensions, not special cases — so it is swept, not spot-checked.

    Swimming, drowning and falling are values of the `self` dimension (`tests/world.py`), which means every
    relation the other files state is already asked about them. What belongs HERE is what only this idea can be
    wrong about: that the dimensions exist, that the columns which need them say so, and that a body missing one
    can always plan its way back — otherwise the pool empties and the agent floats there deciding nothing, which
    is exactly what happened.
    """

    NEEDS = {"craft:minecraft:furnace": ("hands_free", "footing"),   # 3x3: a station has to be put down
             "mine:minecraft:coal": ("hands_free", "footing"),
             "take:bed": ("hands_free", "footing"),
             "smelt:minecraft:iron_ingot": ("hands_free", "footing"),
             "gather:log": ("hands_free",),                          # cutting a tree needs no floor
             "hunt:wool": ("hands_free",)}                           # a fight can happen in the water

    # The body as the solver's state vector: the three values of the body that lose a precondition, and the one
    # that has them all. (The planner sweep in tests/world.py is readings now; this vector is the solver's own.)
    BODIES = {"ready": {"bag_free": 30, "food": 16, "lever:hp": 20, "footing": 1, "hands_free": 1},
              "swimming": {"bag_free": 30, "food": 16, "lever:hp": 20, "hands_free": 1},
              "drowning": {"bag_free": 30, "food": 16, "lever:hp": 20},
              "falling": {"bag_free": 30, "food": 16, "lever:hp": 16}}

    def columns(self, state):
        from bonobo import actions
        return {a.name: a for a in actions.table(places(6.0), state)}

    def test_every_column_that_touches_the_world_says_what_body_it_needs(self):
        whole = self.columns({"bag_free": 30, "footing": 1, "hands_free": 1})
        for name, needs in self.NEEDS.items():
            with self.subTest(column=name):
                requires = whole[name].requires
                for dim in needs:
                    self.assertEqual(requires.get(dim), 1, f"{name} does not say it needs {dim}")

    def test_a_body_that_cannot_work_can_always_plan_its_way_back(self):
        """For every way of losing a precondition, the table offers a way of getting it back — and the pool is
        never empty because of it. Swept over the `self` dimension rather than written out per case."""
        from bonobo import actions, solve
        for body, state in self.BODIES.items():
            with self.subTest(body=body):
                table = self.columns(state)
                missing = [d for d in actions.BODY_DIMS if not state.get(d)]
                mends = [a for a in table.values() if a.tag and a.tag[0] == "reach"]
                self.assertEqual(bool(missing), bool(mends),
                                 f"{missing} missing but {[a.name for a in mends]} offered")
                for dim in missing:
                    self.assertTrue(any(a.effect.get(dim) for a in mends), f"nothing restores {dim}")
                # And the mend is priced, not free or infinite: it competes with the work it unblocks.
                prices = solve.reach_cost(list(table.values()), state)
                for dim in missing:
                    self.assertGreater(prices.get(dim, 0.0), 0.0, dim)
                    self.assertLess(prices.get(dim, float("inf")), 600.0, f"{dim}: priced as good as impossible")

    def test_the_mends_are_alternatives_and_the_cheapest_is_what_it_costs(self):
        """More than one way back (swim to the shore, or put a block underfoot) — so the price of footing is the
        cheaper of them, whichever the world makes cheap here."""
        from bonobo import actions, solve
        swimming = {"bag_free": 30, "hands_free": 1, "building": 8}
        table = list(self.columns(swimming).values())
        ways = {a.name: a.cost_s for a in table if a.effect.get("footing")}
        self.assertEqual(sorted(ways), ["place:footing", "reach:land"], "two ways back: swim ashore, or a block")
        self.assertAlmostEqual(solve.reach_cost(table, swimming)["footing"], min(ways.values()))


if __name__ == "__main__":
    unittest.main()

"""Where a thing comes from, every way a contract gives it, priced by the one planner — and the facts a way needs
first (its contract's `when`: the Nether, a portal, a fortress found) put before it by the steps that set them. One
table: the bag, the world memory knows, the goal → which way the plan takes, and which place-steps it carries."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (registers every skill, so the steps find their providers)
from bonobo import decompose, goals, skill  # noqa: E402
from bonobo.cost import Cost  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from bonobo.planner import Unplannable  # noqa: E402
from tests.world import inventory, snapshot, state  # noqa: E402

OVER, NETHER = "minecraft:overworld", "minecraft:the_nether"


def world(dim=OVER, items=(), seen=(), sites=(), finds=None):
    """(bag, cost model) for a bag of `items`, memory that has `seen` [(kind, pos)] and `sites` [(kind, pos, dim)]."""
    inv = inventory(*items)
    snap = snapshot(state(dimension=dim, skyLight=15, timeOfDay=2000), inv, **(finds or {}))
    m = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
    for kind, pos in seen:
        m.note_seen(kind, pos, dim)
    for kind, pos, where in sites:
        m.add_site(kind, pos, where, name=kind)
    return snap.inv, Cost(snap, m)


def kinds(steps):
    return [(s.kind, s.token) for s in steps]


def barter_ingots(pearls):
    """The gold a barter for `pearls` takes (the loot table's expected yield, knowledge.barter_yield)."""
    import math
    from bonobo.knowledge import barter_yield
    return math.ceil(pearls / barter_yield("minecraft:ender_pearl"))


GOLD = [("gold_ingot", barter_ingots(1)), ("golden_helmet", 1)]
CAST_KIT = [("water_bucket", 1), ("bucket", 1), ("flint_and_steel", 1), ("cobblestone", 16)]

# (situation, world, goal) → (steps that must be in the plan, steps that must not)
ROWS = [
    ("a portal, no obsidian, no diamond pickaxe, buckets and blocks, lava seen: cast in place",
     dict(items=CAST_KIT, seen=[("lava", (6, 60, 0))]), goals.make("build", bp="nether_portal"),
     [("cast", "nether_portal")], [("build", "nether_portal"), ("mine", "minecraft:obsidian")]),
    ("a portal, no lava seen but a lava bucket carried: cast in place",
     dict(items=CAST_KIT + [("lava_bucket", 1)]), goals.make("build", bp="nether_portal"),
     [("cast", "nether_portal")], [("build", "nether_portal")]),
    ("must fail: casting what is already carried — a portal, 10 obsidian and flint carried: built from what is carried",
     dict(items=[("obsidian", 10), ("flint_and_steel", 1), ("cobblestone", 16)], seen=[("lava", (6, 60, 0))]),
     goals.make("build", bp="nether_portal"), [("build", "nether_portal")], [("cast", "nether_portal")]),
    ("pearls, gold carried, no enderman anywhere, a portal known: bartered, the portal first",
     dict(items=GOLD, sites=[("portal", (5, 64, 0), OVER)]), goals.have(("minecraft:ender_pearl", 1)),
     [("portal", NETHER), ("barter", "piglin")], [("hunt", "minecraft:ender_pearl")]),
    ("must fail: pearls, gold carried, no portal and no way to cast one: the search for an enderman is cheaper",
     dict(items=GOLD), goals.have(("minecraft:ender_pearl", 1)),
     [("hunt", "minecraft:ender_pearl")], [("barter", "piglin"), ("portal", NETHER)]),
    ("pearls in the Nether with gold: bartered, no portal step",
     dict(dim=NETHER, items=GOLD), goals.have(("minecraft:ender_pearl", 1)),
     [("barter", "piglin")], [("portal", NETHER)]),
    ("pearls, an enderman in sight, a sword: hunted",
     dict(items=[("iron_sword", 1)], finds={"minecraft:enderman": 8.0}), goals.have(("minecraft:ender_pearl", 1)),
     [("hunt", "minecraft:ender_pearl")], [("barter", "piglin")]),
    ("blaze rods, in the Nether with the fortress known: straight to collecting",
     dict(dim=NETHER, items=[("iron_sword", 1)], sites=[("fortress", (40, 70, 0), NETHER)]),
     goals.have(("minecraft:blaze_rod", 2)), [("hunt", "minecraft:blaze_rod")],
     [("portal", NETHER), ("seek", "fortress")]),
    ("the end portal, eyes held, the stronghold known: the room, then light it",
     dict(items=[("ender_eye", 12)], sites=[("stronghold", (800, 30, 0), OVER)]),
     goals.make("milestone", name="end portal"),
     [("seek", "portal_room"), ("activate", "end_portal")], [("seek", "stronghold")]),
    ("the end portal, eyes held, nothing known: find the stronghold first",
     dict(items=[("ender_eye", 12)]), goals.make("milestone", name="end portal"),
     [("seek", "stronghold"), ("seek", "portal_room"), ("activate", "end_portal")], []),
]


class Sources(unittest.TestCase):
    def test_which_way_the_plan_takes(self):
        for name, w, goal, has, lacks in ROWS:
            with self.subTest(name):
                inv, cost = world(**w)
                got = kinds(decompose.decompose(inv, goal, cost))
                for step in has:
                    self.assertIn(step, got)
                for step in lacks:
                    self.assertNotIn(step, got)
                if len(has) > 1:
                    self.assertEqual([s for s in got if s in has], has, "in this order")

    # (situation, facts) → why casting a frame in place cannot run from there (building.cast_portal's `when`)
    CAST_WHEN = [("must fail: no lava seen and no lava bucket", {"dimension": OVER, "lava": False},
                  "no lava known and no lava bucket"),
                 ("must fail: in the Nether, water cannot be poured", {"dimension": NETHER, "lava": True},
                  "water cannot be poured in the Nether"),
                 ("lava known in the Overworld: nothing missing", {"dimension": OVER, "lava": True}, [])]

    def test_cast_asks_of_where_it_is(self):
        from bonobo.planner import Step
        for name, facts, want in self.CAST_WHEN:
            with self.subTest(name):
                self.assertEqual(skill.when_of_step(Step("cast", "nether_portal", 1, {}), facts), want)

    def test_a_cast_that_cannot_run_is_not_planned(self):
        """No lava known: the portal is built (obsidian), never cast."""
        inv, cost = world(items=CAST_KIT)
        got = kinds(decompose.decompose(inv, goals.make("build", bp="nether_portal"), cost))
        self.assertIn(("build", "nether_portal"), got)
        self.assertNotIn(("cast", "nether_portal"), got)

    # (goal, bag) → done? — None: done when its plan has run (RUN_AFTER), never read off the bag
    DONE = [("end portal with 12 eyes: its plan decides", goals.make("milestone", name="end portal"),
             [("ender_eye", 12)], None),
            ("must fail: end portal with nothing: still the plan's", goals.make("milestone", name="end portal"), [], None),
            ("stone tools held: done from the bag", goals.make("milestone", name="stone tools"),
             [("stone_pickaxe", 1), ("stone_sword", 1), ("stone_axe", 1)], True),
            ("stone tools, the axe missing: not done", goals.make("milestone", name="stone tools"),
             [("stone_pickaxe", 1), ("stone_sword", 1)], False)]

    def test_what_is_done_by_its_plan(self):
        for name, goal, items, want in self.DONE:
            with self.subTest(name):
                inv, cost = world(items=items)
                self.assertIs(goals.done(goal, cost.snap, cost.mem), want)


class BuildingBlocks(unittest.TestCase):
    """Blocks to build with: any member of the group, each by its own way, by price (dirt by hand, stone mined)."""

    # (situation, bag, what is in sight) → the steps (kind, token, count) planned for 9 building blocks
    ROWS = [("an empty bag, dirt 4 away: dug by hand", [], {"dirt": 4}, [("mine", "minecraft:dirt", 9)]),
            ("a pickaxe, stone 2 away, dirt 40 away: the stone", [("stone_pickaxe", 1)], {"stone": 2, "dirt": 40},
             [("mine", "minecraft:cobblestone", 9)]),
            ("must fail: 16 cobblestone carried, nothing to do", [("cobblestone", 16)], {"dirt": 4}, []),
            ("boundary: exactly the 9 carried, nothing to do", [("cobblestone", 9)], {"dirt": 4}, [])]

    def test_plan_over_the_table(self):
        from tests.world import cost, snapshot, state
        for name, carried, seen, want in self.ROWS:
            with self.subTest(name):
                snap = snapshot(state(), inventory(*carried))
                steps = decompose.decompose(snap.inv, goals.have(("building", 9)), cost(snap, **seen))
                self.assertEqual([(st.kind, st.token, st.count) for st in steps], want)

    def test_nothing_in_sight_prices_the_search(self):
        """No dirt or stone in sight: the dig is still planned, its walk priced as a search (cost.find_ticks)."""
        from bonobo.data import mid
        from bonobo.knowledge import members, step_kinds
        from tests.world import cost, snapshot, state
        snap = snapshot(state(), inventory())
        steps = decompose.decompose(snap.inv, goals.have(("building", 9)), cost(snap))
        # the invariant, not the route: building blocks dug from the ground, the search for them priced (K12)
        self.assertEqual([st.kind for st in steps], ["mine"])
        self.assertIn(mid(steps[0].token), [mid(m) for m in members("building")])
        self.assertGreaterEqual(steps[0].est, cost(snap).find_ticks(step_kinds(steps[0])))


if __name__ == "__main__":
    unittest.main()

"""Where a thing comes from: the solver's own way, or another source (decompose.SOURCES), priced — and the way to
where a thing lives put first (decompose.where_it_lives). One table: the bag, the world memory knows, the goal →
which source the plan takes, and which place-steps it carries."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (registers every skill, so the steps find their providers)
from bonobo import decompose, goals  # noqa: E402
from bonobo.cost import Cost  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from bonobo.planner import Unplannable  # noqa: E402
from bonobo.world import Snapshot  # noqa: E402
from tests.world import inventory  # noqa: E402

OVER, NETHER = "minecraft:overworld", "minecraft:the_nether"


def world(dim=OVER, items=(), seen=(), sites=(), finds=None):
    """(bag, cost model) for a bag of `items`, memory that has `seen` [(kind, pos)] and `sites` [(kind, pos, dim)]."""
    inv = inventory(*items)
    snap = Snapshot.from_readings({"dimension": dim, "blockX": 0, "blockY": 64, "blockZ": 0, "skyLight": 15,
                                   "timeOfDay": 2000}, inv)
    m = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
    for kind, pos in seen:
        m.note_seen(kind, pos, dim)
    for kind, pos, where in sites:
        m.add_site(kind, pos, where, name=kind)
    return snap.inv, Cost(snap, m, finds=finds or {})


def kinds(steps):
    return [(s.kind, s.token) for s in steps]


GOLD = [("gold_ingot", 8), ("golden_helmet", 1)]
CAST_KIT = [("water_bucket", 1), ("bucket", 1), ("flint_and_steel", 1), ("cobblestone", 16)]

# (situation, world, goal) → (steps that must be in the plan, steps that must not)
ROWS = [
    ("a portal, no obsidian, no diamond pickaxe, buckets and blocks, lava seen: cast in place",
     dict(items=CAST_KIT, seen=[("lava", (6, 60, 0))]), goals.make("build", bp="nether_portal"),
     [("cast", "nether_portal")], [("build", "nether_portal"), ("mine", "minecraft:obsidian")]),
    ("a portal, no lava seen but a lava bucket carried: cast in place",
     dict(items=CAST_KIT + [("lava_bucket", 1)]), goals.make("build", bp="nether_portal"),
     [("cast", "nether_portal")], [("build", "nether_portal")]),
    ("a portal, 10 obsidian and flint carried: built from what is carried",
     dict(items=[("obsidian", 10), ("flint_and_steel", 1), ("cobblestone", 16)], seen=[("lava", (6, 60, 0))]),
     goals.make("build", bp="nether_portal"), [("build", "nether_portal")], [("cast", "nether_portal")]),
    ("pearls, gold carried, no enderman anywhere: bartered, the portal first",
     dict(items=GOLD), goals.have(("minecraft:ender_pearl", 1)),
     [("portal", NETHER), ("barter", "piglin")], [("hunt", "minecraft:ender_pearl")]),
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

    # (situation, world) → the reasons a portal cannot be had, when carrying obsidian is not plannable either
    # a reason the plan names, or None: the cast is the way when lava is known and the kit carried
    NO_WAY = [("must fail: no lava seen and no lava bucket", dict(items=CAST_KIT), "cast: no lava known and no lava bucket"),
              ("must fail: in the Nether, water cannot be poured",
               dict(dim=NETHER, items=CAST_KIT, seen=[("lava", (6, 60, 0))]), "cast: water cannot be poured in the Nether"),
              ("must fail: an empty bag, no lava known", dict(items=[]), "cast: no lava known and no lava bucket"),
              ("lava seen, the kit carried: cast", dict(items=CAST_KIT, seen=[("lava", (6, 60, 0))]), None)]

    def test_no_way_says_every_reason(self):
        def nothing():
            raise Unplannable("no obsidian to be had here")
        for name, w, reason in self.NO_WAY:
            with self.subTest(name):
                inv, cost = world(**w)
                if reason is None:
                    self.assertEqual(decompose.cheapest("build:nether_portal", 1, nothing, inv, cost)[1], "cast")
                    continue
                with self.assertRaises(Unplannable) as caught:
                    decompose.cheapest("build:nether_portal", 1, nothing, inv, cost)
                self.assertIn("default: no obsidian to be had here", str(caught.exception))
                self.assertIn(reason, str(caught.exception))

    # (goal, bag) → done? — None: done when its plan has run (RUN_AFTER), never read off the bag
    DONE = [("end portal with 12 eyes: its plan decides", goals.make("milestone", name="end portal"),
             [("ender_eye", 12)], None),
            ("end portal with nothing: still the plan's", goals.make("milestone", name="end portal"), [], None),
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
    """Blocks to build with: dug by hand where dirt is in sight (SOURCES["building"]) or mined as stone, by price."""

    # (situation, bag, what is in sight) → the steps (kind, token, count) planned for 9 building blocks
    ROWS = [("an empty bag, dirt 4 away: dug by hand", [], {"dirt": 4}, [("mine", "minecraft:dirt", 9)]),
            ("a pickaxe, stone 2 away, dirt 40 away: the stone", [("stone_pickaxe", 1)], {"stone": 2, "dirt": 40},
             [("mine", "building", 9)]),
            ("must fail: 16 cobblestone carried, nothing to do", [("cobblestone", 16)], {"dirt": 4}, []),
            ("boundary: exactly the 9 carried, nothing to do", [("cobblestone", 9)], {"dirt": 4}, [])]

    def test_plan_over_the_table(self):
        from tests.world import cost, snapshot, state
        for name, carried, seen, want in self.ROWS:
            with self.subTest(name):
                snap = snapshot(state(), inventory(*carried))
                steps = decompose.decompose(snap.inv, goals.have(("building", 9)), cost(snap, **seen))
                self.assertEqual([(st.kind, st.token, st.count) for st in steps], want)

    def test_no_dirt_says_why(self):
        """No dirt in sight and no other way: unplannable, and the reason names the missing dirt."""
        from tests.world import cost, snapshot, state
        snap = snapshot(state(), inventory())

        def no_way():
            raise Unplannable("no pickaxe to mine stone with")
        with self.assertRaises(Unplannable) as got:
            decompose.cheapest("building", 9, no_way, snap.inv, cost(snap))
        self.assertEqual(str(got.exception), "no way to building: default: no pickaxe to mine stone with; "
                                             "dig by hand: no dirt or grass in sight")

if __name__ == "__main__":
    unittest.main()

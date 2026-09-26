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

# (situation, world, goal) → (steps that must be in the plan, steps that must not)
ROWS = [
    ("obsidian, a diamond pickaxe and no lava known: mined",
     dict(items=[("diamond_pickaxe", 1)]), goals.have(("minecraft:obsidian", 4)),
     [("mine", "minecraft:obsidian")], [("cast", "obsidian")]),
    ("obsidian, a diamond pickaxe, a water bucket and a lava pool known: cast, then broken where it formed",
     dict(items=[("diamond_pickaxe", 1), ("water_bucket", 1)], seen=[("lava", (6, 60, 0))]),
     goals.have(("minecraft:obsidian", 4)), [("cast", "obsidian"), ("mine", "minecraft:obsidian")], []),
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

    def test_no_way_says_every_reason(self):
        """Neither mining (no solver can plan it here) nor casting (no lava pool known): refused, both reasons named."""
        inv, cost = world(items=[("water_bucket", 1)])
        with self.assertRaises(Unplannable) as caught:
            decompose.from_sources(inv, [("minecraft:obsidian", 4)], cost, solver="no such solver")
        self.assertIn("default:", str(caught.exception))
        self.assertIn("cast: no lava pool known", str(caught.exception))

    def test_the_end_portal_is_done_by_its_plan(self):
        inv, cost = world(items=[("ender_eye", 12)])
        self.assertIsNone(goals.done(goals.make("milestone", name="end portal"), cost.snap, cost.mem))


if __name__ == "__main__":
    unittest.main()

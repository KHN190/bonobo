"""A plan's time does not grow with memory: the planner's per-place bounds (Cost.dig_lb) read only the places the
step's own price reads (Cost.places_for: its source's cells, its stations), branch and bound — never a dug_way per
note memory holds (plan≈4.5 s a round, rising as notes filled memory). The same plan with 30 notes of
unrelated kinds as with 300. Production path (planner.plan_needs over a Cost), imports the base has: red there by
the timing assertion."""
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo import cost as costmod, lifecycle, world  # noqa: E402
from bonobo.planner import plan_needs  # noqa: E402
from tests.world import bag, inventory, memory, state  # noqa: E402

R = 48
ORES = [(30, 50, 10), (-35, 40, 20), (20, 35, -40), (45, 55, 30), (-10, 30, -45)]
ITEMS = (("wooden_pickaxe", 1), ("stone_pickaxe", 1), ("stone_axe", 1), ("cobblestone", 6), ("oak_planks", 4),
         ("stick", 4), ("crafting_table", 1))
UNRELATED = ("sand", "gravel", "clay", "pumpkin", "melon")      # notes memory gathers that no step of this plan reads


def scene(notes):
    """Stone under a dirt skin, read all round, iron remembered 30–70 off and 10–30 down, and `notes` remembered
    cells of unrelated kinds."""
    blocks = {(x, y, z): "dirt" if y >= 60 else "stone" for x in range(-R, R + 1) for z in range(-R, R + 1)
              for y in range(20, 64)}
    for o in ORES:
        blocks[o] = "iron_ore"
    region = world.Region.of((-R, 20, -R), (R, 80, R), blocks)
    mem = memory()
    for o in ORES:
        mem.note_seen("iron_ore", o, "minecraft:overworld")
    rng = random.Random(1)
    for _ in range(notes):
        mem.note_seen(rng.choice(UNRELATED), (rng.randint(-60, 60), rng.randint(30, 64), rng.randint(-60, 60)),
                      "minecraft:overworld")
    snap = world.Snapshot.from_readings(state(x=.5, y=64, z=.5), world.Inventory(inventory(*ITEMS)),
                                        {"stone": [{"x": 3, "y": 63, "z": 0, "block": "minecraft:stone",
                                                    "distance": 3.0}]}, [], region)
    return costmod.Cost(snap, mem)


def timed_plan(notes):
    lifecycle.reset_all()          # no plan kept from an earlier call: each is priced afresh
    c = scene(notes)
    t0 = time.perf_counter()
    steps = plan_needs(bag(inventory(*ITEMS)), [("minecraft:iron_pickaxe", 1)], c)
    return time.perf_counter() - t0, [(s.kind, s.token, s.count) for s in steps]


class PlanTimeIsFlatInMemory(unittest.TestCase):

    def test_300_notes_as_30(self):
        timed_plan(30)                                          # warm the imports and the process caches
        few_s, few = timed_plan(30)
        many_s, many = timed_plan(300)
        self.assertEqual(many, few, "the same plan: the notes are of no step's source")
        # must fail: one dug_way per note — 300 notes several times 30's (generous: 2× and 0.3 s)
        self.assertLess(many_s, 2 * few_s + 0.3, (few_s, many_s))


if __name__ == "__main__":
    unittest.main()

"""K13, an estimate priced with the wrong inputs: every price that reads tools or a place reads the step's own (the
plan's place before it and the tools it holds then: Cost.step_state), never the round's snapshot, for a step later in a
plan (K9: one price)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo.planner import Step  # noqa: E402
from tests.world import cost, inventory, snapshot, state  # noqa: E402

IRON = Step("mine", "minecraft:raw_iron", 1, {"blocks": ["iron_ore", "deepslate_iron_ore"], "breaks": 1})
LOG = Step("gather", "log", 2)
SEEK = Step("seek", "iron_ore", 1, {"kinds": ["iron_ore"]})
PICK = {"pickaxe": 1}


class StepState(unittest.TestCase):
    """price function × input source: (the step's own, the snapshot's) — the step's must move the price."""

    def test_table(self):
        c = cost(snapshot(state(y=70.0), inventory()))         # on the surface, an empty bag
        deep = (0, 20, 0)
        rows = [
            ("must fail: a search priced with the empty bag's hand, a pickaxe held at the step",
             lambda held, at: c.find_ticks(["iron_ore"], held, at), (PICK, None), (None, None)),
            ("must fail: a search priced from the snapshot's y, not the step's place",
             lambda held, at: c.find_ticks(["iron_ore"], held, at), (PICK, (0, 16, 0)), (PICK, None)),
            ("must fail: a surface trip priced from the snapshot's sky, the step underground",
             lambda held, at: c._surface_trip(at), (None, deep), (None, None)),
            ("must fail: a step's estimate with the bag's tools, not the plan's",
             lambda held, at: c.estimate(IRON, held, at), (PICK, None), (None, None)),
            ("must fail: walk_lb's search with the bag's tools",
             lambda held, at: c.walk_lb(IRON, held if held is not None else {}), (PICK, None), (None, None)),
            ("must fail: a seek step's work with the bag's tools",
             lambda held, at: c.work(SEEK, held), (PICK, None), (None, None)),
        ]
        for name, price, own, snap in rows:
            with self.subTest(name):
                self.assertNotEqual(price(*own), price(*snap))

    def test_a_first_step_reads_the_snapshot(self):
        c = cost(snapshot(state(y=70.0), inventory(("stone_pickaxe", 1))))
        self.assertEqual(c.step_state(), (c.snap.feet, {"pickaxe": 1}))
        self.assertEqual(c.find_ticks(["iron_ore"]), c.find_ticks(["iron_ore"], {"pickaxe": 1}, c.snap.feet))


if __name__ == "__main__":
    unittest.main()

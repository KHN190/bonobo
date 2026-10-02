"""M3: containers as sources at every level (B1); food by price (B2)."""
import os
import tempfile
import unittest

from bonobo import decompose
from bonobo.memory import Memory
from bonobo.planner import NullCost, Planner, Step
from tests.world import cost, inventory, snapshot, state


def memory_with(*chests):
    """A memory with these containers."""
    mem = Memory(os.path.join(tempfile.mkdtemp(prefix="stored"), "notes.json"))
    for pos, items in chests:
        mem.note_container(pos, "minecraft:overworld", [{"id": i, "count": n, "owner": "container"} for i, n in items.items()])
    return mem


def step(kind, token, count, est, **detail):
    s = Step(kind, token, count, detail)
    s.est = est
    return s


NEAR, FAR = (3, 64, 3), (900, 64, 900)
LOGS = {"minecraft:oak_log": 8}


class TakeStored(unittest.TestCase):
    # (situation, chests, plan, already withdrawn, kinds of the result)
    ROWS = [
        ("logs in a chest nearby: taken, not gathered", [(NEAR, LOGS)], [step("gather", "log", 4, 6000)], [],
         ["withdraw"]),
        ("must fail: the chest a long walk away — gathering is cheaper", [(FAR, LOGS)],
         [step("gather", "log", 4, 400)], [], ["gather"]),
        ("two logs in the chest, four wanted: two taken, two gathered", [(NEAR, {"minecraft:oak_log": 2})],
         [step("gather", "log", 4, 6000)], [], ["withdraw", "gather"]),
        ("must fail: the chest's logs already withdrawn by the plan", [(NEAR, LOGS)], [step("gather", "log", 4, 6000)],
         [step("withdraw", "minecraft:oak_log", 8, 100, pos=list(NEAR))], ["gather"]),
        ("raw iron in a chest: taken, not mined", [(NEAR, {"minecraft:raw_iron": 6})],
         [step("mine", "minecraft:raw_iron", 3, 6000, breaks=3, blocks=["iron_ore"], tier=1)], [], ["withdraw"]),
        ("a craft is never replaced", [(NEAR, LOGS)], [step("craft", "minecraft:oak_planks", 4, 60)], [], ["craft"]),
    ]

    def test_rows(self):
        for name, chests, plan, already, want in self.ROWS:
            with self.subTest(name):
                c = cost(snapshot(state()), mem=memory_with(*chests))
                got = decompose.take_stored(plan, c, already)
                self.assertEqual([s.kind for s in got], want, [str(s) for s in got])
                if "withdraw" in want and len(want) == 2:
                    self.assertEqual(sum(s.count for s in got), 4)       # nothing lost, nothing made twice


class CheapestFood(unittest.TestCase):
    def food_of(self, **bag):
        p = Planner(dict(bag), [], NullCost())
        p.need("food", 1)
        return [(s.kind, s.token) for s in p.merged()]

    def test_wheat_carried_makes_bread(self):
        """Must fail on the first-on-the-list rule."""
        got = self.food_of(**{"minecraft:wheat": 3})
        self.assertEqual(got[-1], ("craft", "minecraft:bread"), got)
        self.assertFalse(any(k == "hunt" for k, _t in got), got)

    def test_nothing_carried_hunts(self):
        got = self.food_of()
        self.assertTrue(any(k == "hunt" for k, _t in got), got)


if __name__ == "__main__":
    unittest.main()

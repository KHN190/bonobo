"""What is on the ground is worth what it would take to make again.

`recover items after death` was worth a flat `death_cost_s` — 240 seconds, whether the corpse held a full set of
iron or two blocks of dirt — so it lost to a stone sword at 362 and the drops despawned. The number was a stand-in
for a thing the planner can actually compute: the pile is a list of items, and the shadow prices say what each one
costs to obtain. Dying naked is worth nothing; dying in iron is worth the iron.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions, memory  # noqa: E402
from bonobo.solve import reach_cost  # noqa: E402


def a_memory(tmp):
    return memory.Memory(os.path.join(tmp, "notes.json"))


class DeathsRememberWhatWasLost(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.mem = a_memory(tempfile.mkdtemp(prefix="death-"))

    def test_a_death_records_the_bag(self):
        self.mem.log_death((10, 64, 10), "minecraft:overworld",
                           carried=[("minecraft:iron_pickaxe", 1), ("minecraft:cooked_beef", 8)])
        d = self.mem.recent_death("minecraft:overworld")
        self.assertEqual(dict(d["carried"])["minecraft:iron_pickaxe"], 1)

    def test_an_old_recording_without_a_bag_still_reads(self):
        """Tapes and notes written before this existed must not break."""
        self.mem.log_death((10, 64, 10), "minecraft:overworld")
        d = self.mem.recent_death("minecraft:overworld")
        self.assertEqual(d.get("carried", []), [])


class ThePileHasAPrice(unittest.TestCase):
    def prices(self):
        return reach_cost(actions.table(actions.Costs(lambda kinds: 20.0), {}), {})

    def test_an_empty_corpse_is_worth_nothing(self):
        self.assertEqual(memory.worth_of([], self.prices()), 0.0)

    def test_a_corpse_is_worth_what_its_contents_cost_to_make_again(self):
        price = self.prices()
        worth = memory.worth_of([("minecraft:iron_pickaxe", 1)], price)
        self.assertGreater(worth, 0)
        self.assertAlmostEqual(worth, price.get("minecraft:iron_pickaxe", 0), places=1)

    def test_more_of_a_thing_is_worth_more(self):
        price = self.prices()
        one = memory.worth_of([("minecraft:coal", 1)], price)
        ten = memory.worth_of([("minecraft:coal", 10)], price)
        self.assertGreater(ten, one)

    def test_what_this_world_cannot_make_again_is_not_infinite(self):
        """An unreachable item is still only worth walking back for; the trip is finite."""
        worth = memory.worth_of([("minecraft:elytra", 1)], {})
        self.assertEqual(worth, 0.0)


class ThePoolUsesIt(unittest.TestCase):
    def test_the_goal_is_priced_from_the_pile_not_from_a_constant(self):
        import inspect
        from bonobo.brain import Brain
        src = inspect.getsource(Brain._goal_candidates)
        self.assertIn("worth_of", src,
                      "recovery must be worth what is on the ground, not a flat death cost")


class MemoryIsAClueNotAFact(unittest.TestCase):
    """Things vanish. A note about the world is worth only what the world still confirms.

    Drops despawn after five minutes, and someone may have walked past. So the value of walking back must fall to
    nothing the moment the pile is known to be gone — otherwise the agent keeps being paid, in its own arithmetic,
    for an errand that can no longer pay.
    """

    def setUp(self):
        import tempfile
        self.mem = a_memory(tempfile.mkdtemp(prefix="clue-"))

    def test_a_recovered_death_is_worth_nothing_more(self):
        self.mem.log_death((1, 64, 1), "minecraft:overworld", carried=[("minecraft:iron_pickaxe", 1)])
        self.assertIsNotNone(self.mem.recent_death("minecraft:overworld"))
        self.mem.forget_death((1, 64, 1))
        self.assertIsNone(self.mem.recent_death("minecraft:overworld"),
                          "a pile that is not there must stop being an errand")

    def test_an_old_death_is_worth_nothing_because_the_drops_are_gone(self):
        import time
        self.mem.log_death((1, 64, 1), "minecraft:overworld", carried=[("minecraft:iron_pickaxe", 1)])
        self.assertIsNone(self.mem.recent_death("minecraft:overworld", now=time.time() + 600))

    def test_arriving_to_an_empty_spot_forgets_it(self):
        """The skill must write that down. Believing a note the world has already contradicted is how an agent
        walks the same sixty blocks every five minutes."""
        import inspect
        from bonobo import upkeep
        src = inspect.getsource(upkeep.recover_items)
        self.assertIn("forget_death", src,
                      "arriving and finding nothing must retire the note, not leave it to expire on a timer")


if __name__ == "__main__":
    unittest.main()

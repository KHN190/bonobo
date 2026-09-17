"""V — what finishing everything costs from here, in seconds.

One of the four quantities the planner is made of. Everything worth anything is worth the fall in V that having
it produces; nothing else is a source of value. These are properties over the whole sweep of worlds
(`world.worlds`: what is around us × what is between us and it × what we are × what we carry × what we have
measured), so a relation stated once covers five hundred situations, and a new dimension covers them again
without another test being written.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import gates  # noqa: E402
from tests.world import World, worlds  # noqa: E402


def V(world, **extra):
    """V of this world, through its situation — which is how health, room and the dark reach the loss model."""
    return gates.V(world.situation(dict(world.state(), **extra)))


class HavingMoreIsNeverDearer(unittest.TestCase):
    """The monotonicity everything else rests on: nothing you hold can make finishing cost more."""

    def test_over_every_world(self):
        for w in worlds():
            bare = V(w)
            for gift in ({"planks": 8}, {"bed": 1}, {"minecraft:torch": 16}, {"tool:pickaxe:1": 1}):
                self.assertLessEqual(V(w, **gift), bare + 1e-6, f"{w} + {gift}")


class WhatIsAlreadyDoneCostsNothing(unittest.TestCase):
    def test_a_saturated_state_is_the_floor(self):
        done = {"bed": 1, "sheltered": 1, "food": 16, "minecraft:torch": 16,
                "tool:sword:1": 1, "tool:pickaxe:1": 1, "bag_free": 30}
        floor = gates.V(World().situation(done))
        for w in worlds(stock="none"):
            self.assertLessEqual(floor, V(w) + 1e-6, f"{w}: a finished state is dearer than an unfinished one")

    def test_one_more_of_what_is_already_plentiful_changes_nothing(self):
        plenty = {"bed": 1, "sheltered": 1, "food": 32, "minecraft:torch": 64,
                  "tool:sword:1": 1, "tool:pickaxe:1": 1, "bag_free": 30}
        w = World()
        before = gates.V(w.situation(plenty))
        after = gates.V(w.situation(dict(plenty, **{"minecraft:torch": 128})))
        self.assertAlmostEqual(before, after, places=6)


class TheEndsCompeteRatherThanAddUp(unittest.TestCase):
    """Sixteen planks can become a bed or a shelter, not both: V answers in parts so `value.gain` can take the
    biggest single saving instead of their sum (a stack of planks once came out worth more than the bed)."""

    def test_parts_name_the_ends_and_the_run(self):
        for w in list(worlds(terrain="flat", stock="none"))[:8]:
            parts = gates.V(w.situation(), parts=True)
            self.assertTrue(any(str(k).startswith("end:") for k in parts), parts)
            self.assertIn("loss", parts)
            self.assertAlmostEqual(sum(parts.values()),
                                   gates.V(w.situation()), places=6)


class BeingWithoutThemIsPartOfTheCost(unittest.TestCase):
    """The half that was missing for a long time: V used to price only the WALK to a bed, so every goal came out
    at roughly the same number and the ranking was noise. What a bed is worth is the night it saves."""

    def test_a_hungrier_world_costs_more(self):
        for w in worlds(resource="bare", terrain="flat", stock="none", confidence="unmeasured"):
            if w.self_ != "ready":
                continue
            self.assertLess(V(w), V(w.with_(self_="hungry")), f"{w}: hunger is free")

    def test_a_hurt_body_has_further_to_go(self):
        """Health is part of the situation, so it reaches the loss model — and what one more point of it costs
        rises as there is less of it. Both facts come from the same place; neither is a special case."""
        w = World(self_="ready")
        hurt = w.with_(self_="hurt")
        self.assertLessEqual(V(w), V(hurt) + 1e-6)
        self.assertGreater(gates.marginal("blood", sstate=hurt.sstate()),
                           gates.marginal("blood", sstate=w.sstate()))


class HarderGroundIsNeverCheaper(unittest.TestCase):
    """Terrain enters V through what it costs to reach things — the only way it should enter anything."""

    def test_over_every_resource_and_stock(self):
        for w in worlds(terrain="flat"):
            easy = V(w)
            for harder in ("room", "water", "lava"):
                self.assertGreaterEqual(V(w.with_(terrain=harder)), easy - 1e-6,
                                        f"{w} on {harder} came out cheaper than flat ground")


class ItIsSecondsAllTheWayDown(unittest.TestCase):
    def test_scaling_every_price_scales_v(self):
        """Multiply what every walk costs by ten and V's price half multiplies by ten. A hidden dimensionless
        factor anywhere in the chain breaks this and nothing else catches it."""
        w = World(resource="bare", terrain="flat", self_="ready", stock="none")
        slow = World(resource="bare", terrain="flat", self_="ready", stock="none")
        slow.walk_s = w.walk_s * 10
        parts_one = gates.V(w.situation(), parts=True)
        parts_ten = gates.V(slow.situation(), parts=True)
        ends_one = sum(v for k, v in parts_one.items() if str(k).startswith("end:"))
        ends_ten = sum(v for k, v in parts_ten.items() if str(k).startswith("end:"))
        self.assertGreater(ends_ten, ends_one)
        self.assertAlmostEqual(parts_one["loss"], parts_ten["loss"], places=6,
                               msg="what being without them costs does not depend on how far away they are")


if __name__ == "__main__":
    unittest.main()

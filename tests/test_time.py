"""Δt — how long a piece of work takes, here, for this body.

The second of the four. Every "seconds of work" in the planner comes through `gates.takes_s`: walking, digging,
crafting, going to the nearest one of something, finishing what a goal still lacks. The engines behind it (the
route estimate, the work table, the measured durations) are not tested on their own — if a relation holds over
the whole sweep of worlds, the engine underneath it is doing its job.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import gates, nav  # noqa: E402
from tests.world import TERRAIN, World, flat, worlds  # noqa: E402


def region_with(block=None, at_x=3):
    """Flat ground, optionally with something in the floor between here and there."""
    region = flat()
    if block:
        for z in range(-4, 5):
            region.blocks[(at_x, 63, z)] = block
    return region


class GoingSomewhereCostsMoreTheFurtherItIs(unittest.TestCase):
    def test_distance_only_ever_adds(self):
        near = gates.takes_s(None, gates.Go((0, 64, 0), (5, 64, 0)))
        far = gates.takes_s(None, gates.Go((0, 64, 0), (50, 64, 0)))
        self.assertGreater(far, near)
        self.assertGreater(near, 0.0)

    def test_the_ground_is_the_games_answer_not_ours(self):
        """How long a walk takes over THAT ground is physics, and physics is the game's to answer (`/plan`).

        This file used to assert that stone cost more than air, water more than stone — against a table of
        seconds-per-cell kept here in Python. That table was a second physics engine beside the one the body
        actually moves with, and every place the two disagreed became a bug to patch: water in the floor priced
        as flat ground, lava the same as water, a seam two blocks inside rock called unreachable. So what is
        asserted now is the CONTRACT: whatever the game says, the door passes through, in order.
        """
        from unittest import mock
        answers = {"open": (True, 4.0), "rough": (True, 12.0), "round about": (True, 30.0)}
        priced = {}
        for name, answer in answers.items():
            with mock.patch.object(nav, "route_s", lambda *a, **k: answer):
                priced[name] = gates.takes_s(
                    gates.Situation(region=region_with(), policy=nav.Policy(), route=nav.estimate_price_s),
                    gates.Go((0, 64, 0), (10, 64, 0)))
        self.assertLess(priced["open"], priced["rough"])
        self.assertLess(priced["rough"], priced["round about"])

    def test_no_way_is_dear_and_never_a_wall(self):
        from unittest import mock
        with mock.patch.object(nav, "route_s", lambda *a, **k: (False, 30.0)):
            no_way = gates.takes_s(gates.Situation(region=region_with(), policy=nav.Policy(),
                                                   route=nav.estimate_price_s), gates.Go((0, 64, 0), (10, 64, 0)))
        with mock.patch.object(nav, "route_s", lambda *a, **k: (True, 30.0)):
            a_way = gates.takes_s(gates.Situation(region=region_with(), policy=nav.Policy(),
                                                  route=nav.estimate_price_s), gates.Go((0, 64, 0), (10, 64, 0)))
        self.assertGreater(no_way, a_way)
        self.assertLess(no_way, float("inf"), "unreachable is a price, not a wall")

    def test_with_no_answer_it_claims_nothing_about_the_ground(self):
        """Offline — an imagined state, a replayed round — the game cannot be asked. The honest answer is the
        distance at walking speed: far is dearer than near, and nothing is said about what is in the way."""
        from unittest import mock
        with mock.patch.object(nav, "route_s", lambda *a, **k: (None, None)):
            near = gates.takes_s(gates.Situation(region=region_with(), policy=nav.Policy(),
                                                 route=nav.estimate_price_s), gates.Go((0, 64, 0), (5, 64, 0)))
            far = gates.takes_s(gates.Situation(region=region_with(), policy=nav.Policy(),
                                                route=nav.estimate_price_s), gates.Go((0, 64, 0), (50, 64, 0)))
            walled = gates.takes_s(gates.Situation(region=region_with("stone"), policy=nav.Policy(),
                                                   route=nav.estimate_price_s), gates.Go((0, 64, 0), (5, 64, 0)))
        self.assertGreater(far, near)
        self.assertEqual(near, walled, "with no answer, the ground is not something this side may invent")


class WhatTheDoorIsNotToldItDoesNotInvent(unittest.TestCase):
    """A situation carries the ground AND the way this body reads it. Handed a region with no reader, the answer
    is the straight line — not a guess about terrain nobody could price."""

    def test_a_region_without_a_reader_prices_the_straight_line(self):
        region = region_with("lava")
        blind = gates.takes_s(gates.Situation(region=region, policy=nav.Policy()),
                              gates.Go((0, 64, 0), (10, 64, 0)))
        bare = gates.takes_s(None, gates.Go((0, 64, 0), (10, 64, 0)))
        self.assertAlmostEqual(blind, bare, places=9)

    def test_with_a_reader_the_games_answer_comes_through(self):
        """A `route` in the situation is not a licence to invent terrain here — it is the wire to the game. With
        the game answering, its seconds are what the door returns; the bare straight line is what it returns
        without one."""
        from unittest import mock
        region = region_with("lava")
        with mock.patch.object(nav, "route_s", lambda *a, **k: (True, 40.0)):
            seeing = gates.takes_s(gates.Situation(region=region, policy=nav.Policy(), route=nav.estimate_price_s),
                                   gates.Go((0, 64, 0), (10, 64, 0)))
        self.assertAlmostEqual(seeing, 40.0, places=6)
        self.assertGreater(seeing, gates.takes_s(None, gates.Go((0, 64, 0), (10, 64, 0))))


class GoingToTheNearestOneOfSomething(unittest.TestCase):
    def test_knowing_where_is_never_dearer_than_not_knowing(self):
        for w in worlds(self_="ready", stock="none", confidence="unmeasured"):
            costs = w.costs()
            known = gates.takes_s(None, gates.Seek(["stone"], facts=costs))
            unknown = gates.takes_s(None, gates.Seek(["stone"], facts=costs, ignore_known=True))
            self.assertLessEqual(known, unknown + 1e-6, f"{w}: knowing where made it dearer")

    def test_further_away_is_dearer(self):
        near, far = World(terrain="flat"), World(terrain="lava")
        self.assertLess(gates.takes_s(None, gates.Seek(["stone"], facts=near.costs())),
                        gates.takes_s(None, gates.Seek(["stone"], facts=far.costs())))

    def test_a_walk_to_nowhere_known_has_no_price(self):
        from bonobo import actions
        blind = actions.Costs(lambda kinds: None)
        self.assertIsNone(gates.takes_s(None, gates.Seek(["stone"], facts=blind, nearest_only=True)))


class WorkOnceWeAreThere(unittest.TestCase):
    def test_more_of_it_takes_longer(self):
        facts = World().costs()
        one = gates.takes_s(None, gates.Do("mine", "minecraft:cobblestone", count=1, facts=facts))
        eight = gates.takes_s(None, gates.Do("mine", "minecraft:cobblestone", count=8, facts=facts))
        self.assertAlmostEqual(eight, 8 * one, places=6)

    def test_every_kind_of_work_answers_in_seconds(self):
        facts = World().costs()
        for kind, token in (("mine", "minecraft:cobblestone"), ("craft", "minecraft:stick"),
                            ("smelt", "minecraft:iron_ingot"), ("gather", "log"), ("hunt", "minecraft:beef")):
            self.assertGreater(gates.takes_s(None, gates.Do(kind, token, facts=facts)), 0.0, kind)


class WhatAGoalStillNeeds(unittest.TestCase):
    """`takes_s(s, Short(...))`: the same sum the ranking used to make on its own, in the one place that answers
    "how long"."""

    def test_what_is_already_held_takes_no_time(self):
        self.assertEqual(gates.takes_s(None, gates.Short({"bed": 1}, prices={"bed": 120.0},
                                                         state={"bed": 1})), 0.0)

    def test_what_is_missing_adds_up_and_grows_with_the_shortfall(self):
        """Additive in what is missing, and monotone in how much of it — stated against the inputs, so the
        numbers may change without the relation changing."""
        prices = {"bed": 100.0, "planks": 5.0}
        ask = lambda short: gates.takes_s(None, gates.Short(short, prices=prices, state={}))  # noqa: E731
        bed, planks = ask({"bed": 1}), ask({"planks": 4})
        self.assertAlmostEqual(ask({"bed": 1, "planks": 4}), bed + planks, places=6)
        self.assertGreater(ask({"planks": 8}), planks)
        self.assertEqual(ask({}), 0.0)

    def test_what_this_world_cannot_make_is_dear_not_undefined(self):
        got = gates.takes_s(None, gates.Short({"minecraft:elytra": 1}, prices={}, state={}))
        self.assertGreater(got, 1e5, "unreachable must stay comparable with reachable, not become None")


if __name__ == "__main__":
    unittest.main()

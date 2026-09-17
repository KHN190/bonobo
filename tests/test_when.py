"""Arrival — when a threat can touch us, in seconds.

The first of the five quantities (`bonobo/estimate.py`). Properties over the whole combat sweep (`world.dangers`:
what is coming × how far off × what ground × what body), so a relation stated once covers every cell, and nothing
here asserts a number: the constants behind these are guesses and will move.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import estimate, field  # noqa: E402
from tests.world import WALKS, dangers, learner  # noqa: E402

INF = float("inf")


def soonest(cell):
    """When the first thing in this world can touch us — the world's own rows, not one of them chosen by hand."""
    seen = [estimate.arrival_s(cell.here, hazard, cell.ground()) for hazard in cell.rows()]
    return min(seen) if seen else INF


class ItIsSecondsAndNeverNegative(unittest.TestCase):
    def test_over_every_cell(self):
        for cell in dangers():
            for hazard in cell.rows():
                seconds = estimate.arrival_s(cell.here, hazard, cell.ground())
                self.assertGreaterEqual(seconds, 0.0, f"{cell}: {seconds}")

    def test_what_is_already_inside_its_own_reach_is_here_now(self):
        """Inside its reach means now, and the world decides who is inside it — a test that knows where the sweep
        puts the second zombie is a second copy of the sweep."""
        for cell in dangers(distance="touching", ground="open"):
            for hazard in cell.rows():
                if math.dist(cell.here, hazard[0]) <= hazard[1]:
                    self.assertEqual(estimate.arrival_s(cell.here, hazard, cell.ground()), 0.0, cell)


class DistanceOnlyDelays(unittest.TestCase):
    def test_further_is_never_sooner(self):
        for cell in dangers(distance="touching"):
            if not cell.rows():
                continue
            seen = [soonest(world) for world in cell.along("distance")]
            self.assertEqual(seen, sorted(seen), f"{cell}: {seen}")

    def test_far_enough_out_is_outside_the_account_whatever_the_account_is(self):
        """Infinity is not "far": it is "outside the account we are keeping". The claim is about the horizon, so
        it is stated by moving the horizon over the same world, not by inventing a distance."""
        for cell in dangers(distance="far"):
            for hazard in cell.rows():
                inside = estimate.arrival_s(cell.here, hazard, cell.ground(), horizon=1e6)
                self.assertLess(inside, INF, cell)
                self.assertEqual(estimate.arrival_s(cell.here, hazard, cell.ground(), horizon=inside / 2.0), INF, cell)

    def test_an_answer_once_inside_the_account_never_leaves_it_as_the_account_grows(self):
        """Monotone in the horizon: a longer account may reveal an arrival, never hide one, and the seconds it
        reports for the same journey may not change with the length of the account."""
        for cell in dangers():
            for hazard in cell.rows():
                answers = [estimate.arrival_s(cell.here, hazard, cell.ground(), horizon=h)
                           for h in (1.0, 10.0, 100.0, 1e6)]
                finite = [x for x in answers if x < INF]
                self.assertEqual(finite, sorted(finite, reverse=True)[::-1], f"{cell}: {answers}")
                self.assertEqual(len(set(round(x, 6) for x in finite)), min(1, len(finite)),
                                 f"{cell}: the same journey took different times: {answers}")
                once_seen = False
                for answer in answers:
                    if answer < INF:
                        once_seen = True
                    elif once_seen:
                        self.fail(f"{cell}: an arrival vanished as the account grew: {answers}")


class TheGroundOnlySlowsThingsDown(unittest.TestCase):
    """Ground never makes anything arrive sooner, and a wall is only a wall to what has to walk round it."""

    def test_no_ground_is_quicker_than_the_first_one_on_the_ladder(self):
        for cell in dangers(ground="open"):
            for hazard in cell.rows():
                seen = [estimate.arrival_s(world.here, hazard, world.ground()) for world in cell.along("ground")]
                self.assertEqual(min(seen), seen[0], f"{cell}: {seen}")

    def test_blocks_delay_what_has_to_walk_round_them_more_than_what_climbs_over(self):
        """Read as a ratio against each mob's own unblocked ground — a ratio is what "delays more" means when the
        two walk at different speeds — and who climbs comes from the beliefs, not from the enemy's name."""
        walkers, climbers = [], []
        for cell in dangers(distance="across", ground="corridor"):
            blocked = cell.with_(ground="walled")
            for hazard in cell.rows():
                plain = estimate.arrival_s(cell.here, hazard, cell.ground())
                if plain in (0.0, INF):
                    continue
                delayed = estimate.arrival_s(cell.here, hazard, blocked.ground())
                if delayed == INF:
                    continue
                (climbers if cell.mob_of(hazard).get("squeezes") else walkers).append(delayed / plain)
        if walkers and climbers:
            self.assertGreater(min(walkers), max(climbers))

    def test_nothing_the_ground_does_can_make_it_arrive_sooner(self):
        for cell in dangers(distance="across"):
            for hazard in cell.rows():
                over_nothing = estimate.arrival_s(cell.here, hazard, None)
                for world in cell.along("ground"):
                    self.assertGreaterEqual(estimate.arrival_s(world.here, hazard, world.ground()),
                                            over_nothing - 1e-9, world)


class OneJourney(unittest.TestCase):
    """`field` says how long the ground takes; `estimate` says when the mob is on us. They are the same journey,
    and they were four times apart before this was written down."""

    def test_the_mob_walks_at_the_speed_the_ground_charges(self):
        """Over the sweep's own worlds and its own mobs: the journey `field` prices and the one `estimate` prices
        are the same journey, whatever is walking it and whatever ground it is on."""
        for cell in dangers():
            ground = cell.ground()
            for hazard in cell.rows():
                mob = estimate.MOBS[hazard[3]]
                gap = max(0.0, math.dist(cell.here, hazard[0]) - hazard[1])
                walk = ground.arrival_s((gap, cell.here[1], 0.0), cell.here, speed=mob["speed"],
                                        squeezes=bool(mob.get("squeezes")))
                mine = estimate.arrival_s(cell.here, hazard, ground)
                if mine == INF:
                    continue
                self.assertAlmostEqual(mine, walk, places=2, msg=f"{cell}: {hazard[3]}")


class TheGroundLearnsWhatWalkingCosts(unittest.TestCase):
    """The terrain factor is the one part of arrival that is measured rather than believed: every completed walk
    is a sample (`nav._arrived`). What it must do is follow the samples, stay above a straight line, never leave
    the bound, and keep each kind of ground apart — stated over the ladder of walks, not over three of them."""

    STRAIGHT = 10.0

    def learned(self, ratio, times=1, bucket="open"):
        ground = learner()
        for _ in range(times):
            ground.observed(bucket, straight_s=self.STRAIGHT, actual_s=self.STRAIGHT * ratio)
        return ground.of(bucket)

    def test_it_follows_the_walks_it_has_seen(self):
        seen = [self.learned(ratio) for ratio in WALKS.values()]
        self.assertEqual(seen, sorted(seen), f"{list(WALKS)}: {seen}")

    def test_it_settles_on_what_walking_costs(self):
        for name, ratio in WALKS.items():
            if ratio < 1.0 or ratio > field.MAX_FACTOR:
                continue
            self.assertAlmostEqual(self.learned(ratio, times=20), ratio, places=1, msg=name)

    def test_no_route_is_ever_shorter_than_the_straight_line(self):
        for name, ratio in WALKS.items():
            self.assertGreaterEqual(self.learned(ratio, times=20), 1.0, name)

    def test_no_walk_however_absurd_leaves_the_bound(self):
        for name, ratio in WALKS.items():
            self.assertLessEqual(self.learned(ratio, times=20), field.MAX_FACTOR, name)

    def test_each_kind_of_ground_learns_on_its_own(self):
        ground = learner()
        for _ in range(10):
            ground.observed("underground", self.STRAIGHT, self.STRAIGHT * WALKS["much_slower"])
        self.assertGreater(ground.of("underground"), ground.of("open"))

    def test_a_state_picks_the_ground_it_is_standing_on(self):
        for state, bucket in (({"skyLight": 15, "y": 70}, "open"), ({"skyLight": 0, "y": 30}, "underground"),
                              ({"enclosed": True}, "enclosed")):
            self.assertEqual(field.for_state(state).bucket, bucket, state)

    def test_only_a_walk_that_arrived_teaches_anything(self):
        from unittest import mock
        from bonobo import nav
        for ok in (True, False):
            ground = learner()
            with mock.patch.object(field, "TERRAIN", ground), \
                 mock.patch.object(nav.api, "get", return_value={"skyLight": 15, "y": 64}):
                nav._arrived((0, 64, 0), (43, 64, 0), began=nav.time.time() - 30.0, ok=ok)
            moved = ground.of("open") > 1.0
            self.assertEqual(moved, ok, f"arrived={ok}")


class WhereToPutABlock(unittest.TestCase):
    """The choke is where a block would go: between us and it, and never on top of us. Over the sweep's own
    worlds, so what "between" means is the world's geometry rather than a pair of coordinates written here."""

    def test_the_choke_lies_between_us_and_what_is_coming(self):
        for cell in dangers():
            for hazard in cell.rows():
                spot = cell.ground().choke(cell.here, hazard[0])
                if spot is None:
                    continue
                self.assertLess(math.dist(cell.here, spot), math.dist(cell.here, hazard[0]) + 1e-9, cell)
                self.assertGreater(math.dist(cell.here, spot), 0.0, cell)

    def test_there_is_nothing_to_block_when_it_is_already_on_us(self):
        for cell in dangers(distance="touching"):
            for hazard in cell.rows():
                if math.dist(cell.here, hazard[0]) < 2.0:
                    self.assertIsNone(cell.ground().choke(cell.here, hazard[0]), cell)

    def test_placing_a_block_leaves_the_ground_it_came_from_alone(self):
        for cell in dangers():
            ground = cell.ground()
            for hazard in cell.rows():
                before = estimate.arrival_s(cell.here, hazard, ground)
                ground.with_block()
                self.assertEqual(estimate.arrival_s(cell.here, hazard, ground), before, cell)


if __name__ == "__main__":
    unittest.main()

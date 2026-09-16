"""Arrival time, estimated. No search anywhere: the numbers have to be cheap enough for a 5 Hz loop."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import field  # noqa: E402


class Estimating(unittest.TestCase):
    def open_field(self):
        return field.Field(speed=4.0, bucket="open", terrain=field.Terrain(prior=1.0))

    def test_flat_ground_is_the_straight_line(self):
        self.assertAlmostEqual(self.open_field().arrival_s((0, 64, 0), (8, 64, 0)), 2.0)

    def test_rough_ground_takes_longer_than_the_line(self):
        rough = field.Field(speed=4.0, bucket="underground", terrain=field.Terrain(prior=2.5))
        self.assertAlmostEqual(rough.arrival_s((0, 64, 0), (8, 64, 0)), 5.0)

    def test_a_state_picks_its_own_bucket(self):
        self.assertEqual(field.for_state({"skyLight": 15, "y": 70}).bucket, "open")
        self.assertEqual(field.for_state({"skyLight": 0, "y": 30}).bucket, "underground")
        self.assertEqual(field.for_state({"enclosed": True}).bucket, "enclosed")


class LearningTheGround(unittest.TestCase):
    def test_a_walk_that_took_longer_raises_the_factor(self):
        t = field.Terrain(prior=1.0, memory=0.5)
        t.observed("open", straight_s=10.0, actual_s=20.0)
        self.assertGreater(t.of("open"), 1.0)

    def test_it_settles_towards_what_walking_costs(self):
        t = field.Terrain(prior=1.0, memory=0.5)
        for _ in range(20):
            t.observed("underground", straight_s=10.0, actual_s=30.0)
        self.assertAlmostEqual(t.of("underground"), 3.0, places=1)

    def test_each_kind_of_ground_learns_on_its_own(self):
        t = field.Terrain(prior=1.0, memory=0.5)
        for _ in range(10):
            t.observed("underground", 10.0, 40.0)
        self.assertGreater(t.of("underground"), t.of("open"))

    def test_a_route_shorter_than_the_line_does_not_pull_it_below_one(self):
        t = field.Terrain(prior=1.0, memory=0.5)
        t.observed("open", straight_s=10.0, actual_s=1.0)
        self.assertGreaterEqual(t.of("open"), 1.0)

    def test_one_absurd_walk_does_not_take_over(self):
        t = field.Terrain(prior=1.0, memory=0.5)
        t.observed("open", straight_s=1.0, actual_s=10_000.0)
        self.assertLessEqual(t.of("open"), field.MAX_FACTOR)


class Blocking(unittest.TestCase):
    def setUp(self):
        self.f = field.Field(speed=4.0, bucket="open", terrain=field.Terrain(prior=1.0))

    def test_a_block_sends_it_round(self):
        self.assertGreater(self.f.with_block().arrival_s((0, 64, 0), (8, 64, 0)),
                           self.f.arrival_s((0, 64, 0), (8, 64, 0)))

    def test_each_further_block_costs_it_more(self):
        one = self.f.with_block().arrival_s((0, 64, 0), (8, 64, 0))
        two = self.f.with_block().with_block().arrival_s((0, 64, 0), (8, 64, 0))
        self.assertGreater(two, one)

    def test_what_climbs_or_teleports_is_barely_delayed(self):
        plain = self.f.arrival_s((0, 64, 0), (8, 64, 0))
        squeezed = self.f.with_block().arrival_s((0, 64, 0), (8, 64, 0), squeezes=True)
        walked = self.f.with_block().arrival_s((0, 64, 0), (8, 64, 0))
        self.assertLess(squeezed, walked)
        self.assertAlmostEqual(squeezed, plain * field.SQUEEZE_FACTOR)

    def test_blocking_does_not_change_the_field_it_came_from(self):
        self.f.with_block()
        self.assertAlmostEqual(self.f.arrival_s((0, 64, 0), (8, 64, 0)), 2.0)

    def test_the_choke_is_between_us_and_it(self):
        cell = self.f.choke((0, 64, 0), (8, 64, 0), within=3.0)
        self.assertIsNotNone(cell)
        self.assertLess(cell[0], 8)
        self.assertGreater(cell[0], 0)

    def test_nothing_to_block_when_it_is_on_top_of_us(self):
        self.assertIsNone(self.f.choke((0, 64, 0), (0, 64, 1), within=3.0))


if __name__ == "__main__":
    unittest.main()


class FedByWalking(unittest.TestCase):
    """The estimate is only worth having if something feeds it. Every completed walk is one sample."""

    def test_a_walk_reports_what_it_cost(self):
        from unittest import mock
        from bonobo import nav
        t = field.Terrain(prior=1.0, memory=0.5)
        with mock.patch.object(field, "TERRAIN", t), \
             mock.patch.object(nav.api, "get", return_value={"skyLight": 15, "y": 64}):
            nav._arrived((0, 64, 0), (43, 64, 0), began=nav.time.time() - 30.0, ok=True)
        self.assertGreater(t.of("open"), 1.0)

    def test_a_failed_walk_teaches_nothing(self):
        from unittest import mock
        from bonobo import nav
        t = field.Terrain(prior=1.0, memory=0.5)
        with mock.patch.object(field, "TERRAIN", t):
            nav._arrived((0, 64, 0), (43, 64, 0), began=0.0, ok=False)
        self.assertEqual(t.of("open"), 1.0)

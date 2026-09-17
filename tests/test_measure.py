"""Reading a quantity back out of the world, as properties.

Every quantity the agent estimates has exactly one way to be measured, and the pair lives in one table
(`bonobo.measure.QUANTITIES`: predict, measure, tolerance). This file asserts the readings behave — over synthetic
traces, because a property about a reading has to hold for worlds nobody could have played — and that the table
covers what `estimate` predicts. No golden numbers: the constants move, the relations do not.

A measurement that quietly disagrees with its estimate is how a bench produces fourteen rows and no knowledge: the
walk came back as six thousand blocks because a respawn counted as walking, and every residual read from it was
noise wearing a number.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import measure  # noqa: E402
from tests.world import APPROACH, MOTION, SAMPLING, TELEPORTS, trace  # noqa: E402


class TheTableIsTheContract(unittest.TestCase):
    """One entry per quantity, and every entry complete. A quantity with an estimate and no reading is a number
    nobody can check; a reading with no tolerance is a comparison nobody can fail."""

    def test_every_quantity_has_a_prediction_a_reading_and_a_tolerance(self):
        self.assertTrue(measure.QUANTITIES)
        for name, spec in measure.QUANTITIES.items():
            self.assertTrue(callable(spec.predict), f"{name}: no prediction")
            self.assertTrue(callable(spec.measure), f"{name}: no reading")
            self.assertGreater(spec.tolerance, 0.0, f"{name}: no tolerance")

    def test_the_five_quantities_are_all_covered(self):
        for name in ("arrival_s", "pressure_hp_s", "fight_s", "evade_s", "hp_lost"):
            self.assertIn(name, measure.QUANTITIES, f"{name} is estimated and never measured")

    def test_a_reading_of_an_empty_world_is_not_a_number(self):
        for name, spec in measure.QUANTITIES.items():
            self.assertIsNone(spec.measure([], {}), f"{name}: read something out of an empty trace")


class WhatTheBodyDid(unittest.TestCase):
    """Walking, and what is not walking."""

    def test_standing_still_walks_nowhere(self):
        for sampling in SAMPLING:
            self.assertEqual(measure.walked(trace(motion="still", sampling=sampling)), 0.0, sampling)

    def test_walking_is_read_back_at_the_speed_it_walked(self):
        for motion, speed in MOTION.items():
            if speed == 0.0:
                continue
            seen = measure.walked(trace(motion=motion, sampling="even", seconds=4.0))
            self.assertAlmostEqual(seen / 4.0, speed, delta=speed * 0.35, msg=motion)

    def test_a_teleport_is_not_a_walk(self):
        for motion in MOTION:
            walked = measure.walked(trace(motion=motion, teleports="none", seconds=4.0))
            jumped = measure.walked(trace(motion=motion, teleports="once", seconds=4.0))
            self.assertAlmostEqual(jumped, walked, delta=max(1.0, walked * 0.2), msg=motion)

    def test_how_often_we_looked_does_not_change_how_far_we_walked(self):
        seen = [measure.walked(trace(motion="walking", sampling=s, seconds=6.0)) for s in SAMPLING]
        self.assertLess(max(seen) - min(seen), max(seen) * 0.35, f"{list(SAMPLING)}: {seen}")


class WhatTheWorldDid(unittest.TestCase):
    def test_a_rate_is_read_back_as_the_rate_it_was_given(self):
        for bleed in (0.5, 2.0, 6.0):
            seen = measure.hp_rate(trace(motion="still", sampling="even", seconds=6.0, bleed=bleed))
            self.assertAlmostEqual(seen, bleed, delta=bleed * 0.35, msg=f"{bleed} hp/s")

    def test_nothing_bleeding_is_no_rate(self):
        self.assertEqual(measure.hp_rate(trace(bleed=0.0)), 0.0)

    def test_arrival_is_when_it_first_came_within_reach(self):
        for approach, closing in APPROACH.items():
            seen = measure.first_arrival(trace(approach=approach, start=8.0, seconds=6.0), reach=3.0)
            if closing < 0:
                self.assertIsNotNone(seen, approach)
                self.assertGreater(seen, 0.0, approach)
            else:
                self.assertIsNone(seen, approach)

    def test_the_closer_it_starts_the_sooner_it_arrives(self):
        seen = [measure.first_arrival(trace(approach="closing", start=start, seconds=10.0), reach=3.0)
                for start in (4.0, 8.0, 16.0)]
        self.assertEqual(seen, sorted(seen), seen)

    def test_time_in_reach_is_never_longer_than_the_trace(self):
        for approach in APPROACH:
            for sampling in SAMPLING:
                world = trace(approach=approach, sampling=sampling, seconds=6.0)
                self.assertLessEqual(measure.in_reach_s(world, reach=3.0), world[-1]["t"] + 1e-6,
                                     f"{approach}/{sampling}")

    def test_clearing_is_never_before_the_last_time_it_was_seen(self):
        world = trace(approach="closing", seconds=6.0)
        cleared = measure.cleared_s(world + [{"t": 7.0, "hp": 20.0, "pos": [0, 64, 0], "near": []}])
        self.assertGreaterEqual(cleared, world[-1]["t"])


class HowMuchToTrustTheReading(unittest.TestCase):
    """The trace's own metadata: a measurement taken from samples that arrived seconds apart is not the same
    evidence as one taken at 5 Hz, and the residual has to say which it was."""

    def test_the_gap_between_samples_is_reported(self):
        for sampling, step in SAMPLING.items():
            self.assertAlmostEqual(measure.sample_gap_s(trace(sampling=sampling)), step, delta=step * 0.25,
                                   msg=sampling)

    def test_a_trace_that_stopped_arriving_is_visible_as_a_gap(self):
        world = trace(sampling="even", seconds=2.0)
        world += [{"t": 30.0, "hp": 20.0, "pos": [0, 64, 0], "near": []}]
        self.assertGreater(measure.worst_gap_s(world), 10.0)


class TheResidualIsTheOnlyComparison(unittest.TestCase):
    def test_it_is_measured_against_what_was_measured(self):
        """The measurement is the truth the estimate is judged against, so it is the denominator: being 2 s out
        on a 12 s fight is not the same mistake as being 2 s out on a 2 s one."""
        self.assertAlmostEqual(measure.residual(10.0, 12.0), 2.0 / 12.0, places=6)
        self.assertEqual(measure.residual(0.0, 0.0), 0.0)

    def test_agreement_is_zero_and_everything_else_is_positive(self):
        for value in (0.5, 4.0, 90.0):
            self.assertEqual(measure.residual(value, value), 0.0)
        for said, seen in ((1.0, 9.0), (9.0, 1.0), (0.1, 100.0)):
            self.assertGreater(measure.residual(said, seen), 0.0)

    def test_nothing_measured_is_not_a_residual(self):
        self.assertIsNone(measure.residual(10.0, None))


if __name__ == "__main__":
    unittest.main()

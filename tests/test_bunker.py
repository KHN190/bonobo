"""Offline tests for the bunker geometry.

The bunker's value is that its failure modes are few and nameable. Each of those names is a test here, so a change to
the layout that quietly reintroduces one (a corridor dug 3 high, a mouth out of reach of the bed, a trench with no
roof) fails offline instead of during a fight that costs five minutes to set up.
"""
import math
import unittest

from bonobo import bunker
from bonobo.end import PIT_DEPTH, PIT_R, bed_cell

SIDE = (1, 0)
# The production height relation, not a convenient one: build_bed_pit sets `fy = top`, so the floor plane and the
# fountain's bedrock top are the SAME height, and bed_cell puts the bed one block above it. An earlier version of
# this file used FLOOR=64/TOP=63, which quietly made the bed one block lower than it really is — every reach
# assertion passed while the real geometry was a block further away. Tests that invent their own geometry only
# prove things about the geometry they invented.
FLOOR = 67
TOP = FLOOR


class Layout(unittest.TestCase):
    def test_mouth_is_the_pit_floor(self):
        m = bunker.mouth(SIDE, FLOOR)
        self.assertEqual(m, (PIT_R, FLOOR - PIT_DEPTH, 0))

    def test_tunnel_runs_outward_not_inward(self):
        cells = bunker.tunnel(SIDE, FLOOR)
        xs = [c[0] for c in cells]
        self.assertEqual(xs, sorted(xs))                 # away from the portal centre
        self.assertTrue(all(c[1] == cells[0][1] for c in cells))   # level: it is a corridor, not a staircase

    def test_corridor_is_one_wide_and_two_high(self):
        cells = bunker.tunnel(SIDE, FLOOR)
        heads = bunker.head_cells(cells)
        self.assertEqual(len(set((c[0], c[2]) for c in cells)), len(cells))   # single file
        for feet, head in zip(cells, heads):
            self.assertEqual(head[1] - feet[1], 1)

    def test_ceiling_sits_above_the_head_cells(self):
        cells = bunker.tunnel(SIDE, FLOOR)
        for feet, roof in zip(cells, bunker.ceiling(cells)):
            self.assertEqual(roof[1] - feet[1], 2)

    def test_dig_plan_starts_at_the_mouth(self):
        plan = bunker.dig_plan(SIDE, FLOOR)
        self.assertEqual(plan[0], bunker.mouth(SIDE, FLOOR))
        self.assertEqual(len(plan), 2 * (bunker.TUNNEL_LEN + 1))   # feet and head for every cell


class Properties(unittest.TestCase):
    def test_bed_reachable_from_the_mouth(self):
        bed = bed_cell(SIDE, TOP)
        reachable, _, _ = bunker.bunker_checks(SIDE, FLOOR, bed)
        self.assertTrue(reachable, "the mouth must be able to click the bed without stepping out")

    def test_retreat_is_clear_of_the_mouth(self):
        bed = bed_cell(SIDE, TOP)
        _, clear, _ = bunker.bunker_checks(SIDE, FLOOR, bed)
        self.assertTrue(clear)

    def test_bed_reachable_from_inside_cover(self):
        # The point of the whole design: an agent reads entity data directly, so it never needs to see the bed, only
        # to reach it. Measured at 4.11 blocks from the firing cell against a 4.5 reach — if this ever fails, the
        # fight goes back to stepping out into the open for every bomb.
        bed = bed_cell(SIDE, TOP)
        self.assertTrue(bunker.bed_in_reach(bunker.fire(SIDE, FLOOR), bed))

    def test_retreat_is_deeper_than_the_firing_cell(self):
        f = bunker.fire(SIDE, FLOOR)
        r = bunker.retreat(SIDE, FLOOR)
        self.assertGreater(math.dist(r, bunker.mouth(SIDE, FLOOR)), math.dist(f, bunker.mouth(SIDE, FLOOR)))

    def test_enderman_cannot_stand_in_the_corridor(self):
        self.assertFalse(bunker.enderman_can_enter(bunker.tunnel(SIDE, FLOOR)))

    def test_exposure_is_the_mouth_column_only(self):
        cells = bunker.exposure_cells(SIDE, FLOOR)
        m = bunker.mouth(SIDE, FLOOR)
        self.assertTrue(all((c[0], c[2]) == (m[0], m[2]) for c in cells))

    def test_reinforcement_covers_roof_and_walls(self):
        cells = bunker.reinforce_cells(SIDE, FLOOR)
        m = bunker.mouth(SIDE, FLOOR)
        self.assertIn((m[0], m[1] + 2, m[2]), cells)                  # roof
        self.assertTrue(any(c[2] != m[2] for c in cells))             # walls across the corridor axis


class Timing(unittest.TestCase):
    def test_walk_back_fits_inside_the_take_off_warning(self):
        # The tapes measured take-off at 0.85 s. A peek from the mouth must be recoverable inside that, or the
        # design is only pretending to be safe.
        t = bunker.time_to_cover(bunker.mouth(SIDE, FLOOR), SIDE, FLOOR)
        self.assertLess(t, 0.85, f"getting back into cover takes {t}s, longer than the take-off warning")

    def test_time_to_cover_is_zero_in_the_retreat_cell(self):
        r = bunker.retreat(SIDE, FLOOR)
        self.assertEqual(bunker.time_to_cover(r, SIDE, FLOOR), 0.0)

    def test_further_out_takes_longer(self):
        m = bunker.mouth(SIDE, FLOOR)
        far = (m[0], m[1] + 2, m[2])
        self.assertGreater(bunker.time_to_cover(far, SIDE, FLOOR), bunker.time_to_cover(m, SIDE, FLOOR))


class AgainstTheTapes(unittest.TestCase):
    """The numbers the design is justified by, asserted so a later re-measurement that contradicts them is noticed."""

    WINDOW_S = 4.95        # phase 6, median and maximum across 23 observed spans
    TAKEOFF_S = 0.85       # phase 4, median across 23 spans (sd 0.15)

    def test_a_window_affords_several_peeks(self):
        out_and_back = 2 * bunker.time_to_cover(bunker.mouth(SIDE, FLOOR), SIDE, FLOOR)
        self.assertGreater(self.WINDOW_S / max(out_and_back, 1e-6), 3)

    def test_warning_is_shorter_than_a_round_trip_from_outside(self):
        # Standing in the open 8 blocks out, the take-off warning is not enough to reach cover — which is the whole
        # argument for waiting in the tunnel rather than at a "safe distance".
        outside = (PIT_R + 8, FLOOR, 0)
        self.assertGreater(bunker.time_to_cover(outside, SIDE, FLOOR), self.TAKEOFF_S)


if __name__ == "__main__":
    unittest.main()

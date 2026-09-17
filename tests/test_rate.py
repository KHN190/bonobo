"""Pressure — how fast health comes off us while something can reach us, in hp per second.

The second of the five quantities, and the one both planners read: ordinary play charges it on every second of
every plan (`brain.hp_tax_rate`), the fight prices every column against it. What explodes is not a rate and is
here too, as the thing pressure is NOT (`burst_hp`).

Properties over the whole combat sweep; no numbers asserted.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import estimate, threat  # noqa: E402
from tests.world import AWARENESS, dangers, unaware  # noqa: E402


def rate(cell, **kw):
    return estimate.pressure_hp_s(cell.here, cell.rows(), cell.armour, ground=cell.ground(), **kw)


class ItIsARateAndNeverNegative(unittest.TestCase):
    def test_over_every_cell(self):
        for cell in dangers():
            self.assertGreaterEqual(rate(cell), 0.0, cell)

    def test_nothing_there_presses_nothing(self):
        for cell in dangers(enemy="none"):
            self.assertEqual(rate(cell), 0.0, cell)


class MoreOfIt(unittest.TestCase):
    def test_a_crowd_presses_harder_than_one(self):
        for cell in dangers(enemy="walker"):
            self.assertGreaterEqual(rate(cell.with_(enemy="pack")) + 1e-9, rate(cell), cell)

    def test_closer_presses_harder(self):
        for cell in dangers(enemy="walker", distance="touching"):
            seen = [rate(world) for world in cell.along("distance")]
            self.assertEqual(seen, sorted(seen, reverse=True), f"{cell}: {seen}")

    def test_armour_takes_a_share_off_whatever_it_is(self):
        """One world, one baseline, two protections: what the cell's own armour is does not enter into it — that
        is the world's business, and a test that reads it is keeping its own copy of the world."""
        for cell in dangers(weapon="stone", ground="open"):
            rows, ground = cell.rows(), cell.ground()
            bare = estimate.pressure_hp_s(cell.here, rows, 0.0, ground=ground)
            if bare == 0.0:
                continue
            self.assertAlmostEqual(estimate.pressure_hp_s(cell.here, rows, 0.5, ground=ground), bare * 0.5,
                                   places=6, msg=str(cell))

    def test_the_less_it_has_noticed_us_the_less_it_presses(self):
        """Awareness is a dimension of the row, so it is swept like one: along the ladder, worst first."""
        for cell in dangers(distance="near", ground="open"):
            rows = cell.rows()
            if not rows:
                continue
            seen = [estimate.pressure_hp_s(cell.here, unaware(rows, aware), 0.0) for aware in AWARENESS.values()]
            self.assertEqual(seen, sorted(seen, reverse=True), f"{cell}: {seen}")


class TheGroundAndTheShape(unittest.TestCase):
    """Two different things a block does, and reading them as one is why an agent with a stack of cobblestone
    stood still at four hearts: the ground DELAYS (that is `arrival`), the shape decides whether it REACHES us."""

    def test_ground_never_raises_the_pressure(self):
        for cell in dangers(ground="open"):
            seen = [rate(world) for world in cell.along("ground")]
            self.assertEqual(max(seen), seen[0], f"{cell}: {seen}")

    def test_standing_on_blocks_takes_out_of_reach_whatever_has_to_walk_to_us(self):
        for cell in dangers(distance="touching", ground="corridor"):
            rows = [r for r in cell.rows() if not cell.mob_of(r).get("squeezes")
                    and not cell.mob_of(r).get("ranged") and not cell.mob_of(r).get("burst")]
            if not rows:
                continue
            ground = cell.ground()
            plain = estimate.pressure_hp_s(cell.here, rows, 0.0, ground=ground)
            if plain == 0.0:
                continue
            self.assertLess(estimate.pressure_hp_s(cell.here, rows, 0.0, ground=ground, shape=("under", 2)),
                            plain, cell)

    def test_no_shape_helps_against_what_the_beliefs_say_squeezes_past(self):
        for cell in dangers(distance="touching", ground="corridor"):
            rows = [r for r in cell.rows() if cell.mob_of(r).get("squeezes")]
            if not rows:
                continue
            ground = cell.ground()
            plain = estimate.pressure_hp_s(cell.here, rows, 0.0, ground=ground)
            for shape in (("under", 2), ("down", 2), ("between", 2)):
                self.assertAlmostEqual(
                    estimate.pressure_hp_s(cell.here, rows, 0.0, ground=ground, shape=shape),
                    plain, places=6, msg=f"{cell} {shape}")

    def test_a_hole_breaks_the_line_of_what_shoots_and_a_pillar_does_not(self):
        for cell in dangers(distance="across", ground="corridor"):
            rows = [r for r in cell.rows() if cell.mob_of(r).get("ranged")]
            if not rows:
                continue
            ground = cell.ground()
            plain = estimate.pressure_hp_s(cell.here, rows, 0.0, ground=ground)
            under = estimate.pressure_hp_s(cell.here, rows, 0.0, ground=ground, shape=("under", 2))
            down = estimate.pressure_hp_s(cell.here, rows, 0.0, ground=ground, shape=("down", 2))
            self.assertAlmostEqual(under, plain, places=6, msg=str(cell))
            self.assertLess(down, plain, cell)


class ABlastIsNotARate(unittest.TestCase):
    """Which mobs explode is the belief table's business; that what explodes is owed at once and not per second is
    the architecture. Every claim here reads the flag rather than naming a creeper."""

    def bursting(self, **fixed):
        for cell in dangers(**fixed):
            rows = cell.rows()
            if rows and all(cell.mob_of(r).get("burst") for r in rows):
                yield cell

    def test_what_explodes_presses_nothing(self):
        for cell in self.bursting():
            self.assertEqual(rate(cell), 0.0, cell)

    def test_but_it_is_owed_at_once_while_it_can_reach_us(self):
        for cell in self.bursting(distance="touching", ground="open"):
            self.assertGreater(estimate.burst_hp(cell.here, cell.rows()), 0.0, cell)

    def test_and_owed_nothing_once_it_cannot(self):
        for cell in self.bursting(distance="far", ground="open"):
            self.assertEqual(estimate.burst_hp(cell.here, cell.rows()), 0.0, cell)

    def test_armour_takes_the_same_share_off_it_as_off_a_rate(self):
        for cell in self.bursting(distance="touching", ground="open"):
            bare = estimate.burst_hp(cell.here, cell.rows(), prot=0.0)
            if bare == 0.0:
                continue
            self.assertAlmostEqual(estimate.burst_hp(cell.here, cell.rows(), prot=0.5), bare * 0.5,
                                   places=6, msg=str(cell))


class TheTaxTheOtherPlannerReads(unittest.TestCase):
    """The whole coupling between the two planners is this rate under another name, plus a set of circles. Never
    a decision, and never a second arithmetic."""

    def test_the_rate_the_planner_reads_is_the_rate_the_columns_are_priced_against(self):
        """There is no second name for it any more: what ordinary play charges per second is this function, and
        the only thing the live reader adds is the measured twin (`perception.pressure_now`)."""
        from bonobo import perception
        for cell in dangers():
            rows, ground = cell.rows(), cell.ground()
            modelled = estimate.pressure_hp_s(cell.here, rows, cell.armour, ground=ground)
            self.assertGreaterEqual(perception.pressure_now(cell.here, rows, cell.armour, ground=ground),
                                    modelled - 1e-9, cell)

    def test_every_threat_is_a_zone_and_the_zone_is_wider_than_its_reach(self):
        for cell in dangers():
            zones = threat.no_go(cell.threat_state())
            self.assertEqual(len(zones), len(cell.rows()), cell)
            for (centre, radius), hazard in zip(zones, cell.rows()):
                self.assertGreater(radius, hazard[1], cell)
                self.assertTrue(threat.inside_no_go(centre, zones), cell)


class TimeToDie(unittest.TestCase):
    def test_it_is_health_over_the_rate(self):
        for cell in dangers():
            press = rate(cell)
            if press <= 0:
                self.assertEqual(estimate.time_to_die_s(cell.hp, press), float("inf"), cell)
            else:
                self.assertAlmostEqual(estimate.time_to_die_s(cell.hp, press),
                                       cell.hp / press, places=6, msg=str(cell))


if __name__ == "__main__":
    unittest.main()

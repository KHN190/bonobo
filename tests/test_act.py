"""What doing something costs — an action's health and its seconds, as one number.

The third of the five quantities. `act_cost_s(seconds, hp, price)` is the whole of it; `fight_cost` and
`leaving_hp` are the two ways the health half is worked out when the action is a fight or a walk away.

Properties over the combat sweep, and over an arbitrary price of health: nothing here may depend on what the
price function IS, only on it being monotone — that is what keeps time and health comparable.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import estimate, threat  # noqa: E402
from tests.world import BLOOD_LOST, HP_PRICES, RATES, SECONDS, dangers  # noqa: E402

class OneCurrency(unittest.TestCase):
    def test_it_is_the_time_plus_the_priced_health(self):
        for name, price in HP_PRICES.items():
            for seconds in SECONDS:
                for hp in BLOOD_LOST:
                    self.assertAlmostEqual(estimate.act_cost_s(seconds, hp, price), seconds + price(hp),
                                           places=6, msg=f"{name} {seconds}s {hp}hp")

    def test_doing_nothing_costs_nothing(self):
        for price in HP_PRICES.values():
            self.assertEqual(estimate.act_cost_s(0.0, 0.0, price), 0.0)

    def test_it_grows_along_both_ladders(self):
        for price in HP_PRICES.values():
            for hp in BLOOD_LOST:
                seen = [estimate.act_cost_s(seconds, hp, price) for seconds in SECONDS]
                self.assertEqual(seen, sorted(seen), f"{hp}hp: {seen}")
            for seconds in SECONDS:
                seen = [estimate.act_cost_s(seconds, hp, price) for hp in BLOOD_LOST]
                self.assertEqual(seen, sorted(seen), f"{seconds}s: {seen}")

    def test_the_same_action_costs_more_the_less_blood_there_is(self):
        """The price is the caller's, and this is why: the same four hearts are a scratch or most of a death. Read
        along the ladder, so the claim is about the direction the dimension declares rather than two cells."""
        for cell in dangers(enemy="walker", distance="near", ground="open", blood="whole"):
            seen = [estimate.act_cost_s(SECONDS[-1], BLOOD_LOST[-2], world.price())
                    for world in cell.along("blood")]
            self.assertEqual(seen, sorted(seen), f"{cell}: {seen}")


class AFight(unittest.TestCase):
    def test_it_takes_time_and_health_whenever_there_is_anything_to_kill(self):
        for cell in dangers():
            seconds, hp = estimate.fight_cost(cell.here, cell.rows(), cell.sword, cell.armour)
            if not cell.rows():
                self.assertEqual((seconds, hp), (0.0, 0.0), cell)
            else:
                self.assertGreater(seconds, 0.0, cell)
                self.assertGreaterEqual(hp, 0.0, cell)

    def test_a_better_weapon_is_faster_and_cheaper(self):
        for cell in dangers(enemy="walker", weapon="fist"):
            costs = [estimate.fight_cost(world.here, world.rows(), world.sword, 0.0)
                     for world in cell.along("weapon")]
            self.assertEqual([c[0] for c in costs], sorted((c[0] for c in costs), reverse=True), cell)
            self.assertEqual([c[1] for c in costs], sorted((c[1] for c in costs), reverse=True), cell)

    def test_more_of_them_is_longer_and_dearer(self):
        """One dimension moved, everything else the cell's own: `enemy` is the only thing that differs between
        these two worlds, and the tier comes off the world rather than being written in."""
        for cell in dangers(enemy="walker"):
            pack = cell.with_(enemy="pack")
            one = estimate.fight_cost(cell.here, cell.rows(), cell.sword, cell.armour)
            many = estimate.fight_cost(pack.here, pack.rows(), pack.sword, pack.armour)
            self.assertGreater(many[0], one[0], cell)
            self.assertGreater(many[1], one[1], cell)

    def test_armour_buys_health_and_not_speed(self):
        for cell in dangers(enemy="archer", distance="across", armour="skin"):
            seconds, blood = zip(*(estimate.fight_cost(world.here, world.rows(), world.sword,
                                                       world.armour) for world in cell.along("armour")))
            self.assertEqual(len(set(round(t, 6) for t in seconds)), 1, f"{cell}: armour changed the time")
            self.assertEqual(list(blood), sorted(blood, reverse=True), f"{cell}: {blood}")

    def test_walking_to_it_is_part_of_it(self):
        for cell in dangers(enemy="walker", distance="touching"):
            seen = [estimate.fight_cost(world.here, world.rows(), world.sword, world.armour)[0]
                    for world in cell.along("distance")]
            self.assertEqual(seen, sorted(seen), f"{cell}: {seen}")


class WalkingAway(unittest.TestCase):
    def test_it_costs_less_than_standing_in_it(self):
        """Pressure falls as the distance opens — that is what leaving IS. The property is the inequality, not the
        factor: charge the full rate for the whole walk and running looks as lethal as fighting."""
        for press in RATES:
            for seconds in SECONDS:
                if press == 0.0 or seconds == 0.0:
                    continue
                self.assertLess(estimate.leaving_hp(press, seconds), press * seconds)
                self.assertGreater(estimate.leaving_hp(press, seconds), 0.0)

    def test_it_grows_along_both_ladders(self):
        for seconds in SECONDS[1:]:
            seen = [estimate.leaving_hp(press, seconds) for press in RATES]
            self.assertEqual(seen, sorted(seen), f"{seconds}s: {seen}")
        for press in RATES[1:]:
            seen = [estimate.leaving_hp(press, seconds) for seconds in SECONDS]
            self.assertEqual(seen, sorted(seen), f"{press}hp/s: {seen}")

    def test_nothing_to_walk_out_of_costs_nothing(self):
        for seconds in SECONDS:
            self.assertEqual(estimate.leaving_hp(0.0, seconds), 0.0)
        for press in RATES:
            self.assertEqual(estimate.leaving_hp(press, 0.0), 0.0)

    def test_the_spot_it_walks_to_is_away_from_them(self):
        for cell in dangers(distance="near", ground="open"):
            rows = cell.rows()
            if not rows:
                continue
            spot = threat.escape_spot(cell.here, rows)
            before = estimate.pressure_hp_s(cell.here, rows, 0.0)
            after = estimate.pressure_hp_s(spot, rows, 0.0)
            self.assertLessEqual(after, before + 1e-9, f"{cell}: walked into it")


class EveryColumnIsPricedThroughIt(unittest.TestCase):
    """`threat.options` builds the columns; each one's `cost_s` is this quantity and nothing else. A column that
    priced itself some other way is how `saves` came to be four times the planner's own score."""

    def test_every_option_costs_its_seconds_plus_its_priced_health(self):
        for cell in dangers():
            price = cell.price()
            for option in threat.options(cell.threat_state()):
                self.assertAlmostEqual(threat.action_cost(option, price),
                                       estimate.act_cost_s(option.seconds, option.hp, price),
                                       places=6, msg=f"{cell}: {option.kind}")

    def test_carrying_on_spends_nothing(self):
        """`ignore` is a state, not an act: while its damage was also a cost, every other column looked four times
        better than it was and the planner still held `ignore`."""
        for cell in dangers():
            doing_nothing = next(o for o in threat.options(cell.threat_state()) if o.kind == "ignore")
            self.assertEqual((doing_nothing.hp, doing_nothing.seconds), (0.0, 0.0), cell)


if __name__ == "__main__":
    unittest.main()

"""The price of a state — seconds the future costs from here.

The fifth quantity, and the only source of value in the agent: a thing is worth exactly the fall in this that
having it produces. Two models own one each — `survival.expected_loss` for a day of ordinary play, `Fight.objective`
for a dragon — and `kernel` reaches both through `estimate.state_price_s` without knowing which it has.

Properties over both sweeps. Nothing asserts a number; everything asserts a relation that has to survive the
constants moving.
"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import estimate, survival as sv  # noqa: E402
from tests.world import DUSK, LADDERS, WORSE, along_day, day_state, fights  # noqa: E402


def price(state):
    return sv.expected_loss(state)


class ItIsSecondsAndNeverNegative(unittest.TestCase):
    def test_over_every_day_state(self):
        for dim in list(LADDERS) + list(WORSE) + ["ticks_until_dusk"]:
            for state in along_day(dim):
                seconds = price(state)
                self.assertGreaterEqual(seconds, 0.0, f"{dim}: {state}")
                self.assertLess(seconds, 1e7, f"{dim}: {state}")

    def test_over_every_fight_state(self):
        for cell in fights():
            self.assertGreaterEqual(cell.model().price(cell.fight_state()), 0.0, cell)

    def test_the_door_is_the_model(self):
        for cell in fights():
            model, state = cell.model(), cell.fight_state()
            self.assertEqual(estimate.state_price_s(model, state), model.price(state), cell)


class HavingMoreIsNeverDearer(unittest.TestCase):
    """The monotonicity everything else rests on: nothing you hold can make the future cost more."""

    def test_every_ladder_falls(self):
        for dim in LADDERS:
            for dusk in DUSK:
                seen = [price(state) for state in along_day(dim, ticks_until_dusk=dusk)]
                self.assertEqual(seen, sorted(seen, reverse=True), f"{dim} at {dusk}: {seen}")

    def test_what_is_worse_is_dearer(self):
        for dim in WORSE:
            seen = [price(state) for state in along_day(dim)]
            self.assertEqual(seen, sorted(seen), f"{dim}: {seen}")

    def test_a_dead_boss_is_the_floor_and_nothing_improves_on_it(self):
        for cell in fights(boss="dead"):
            model, state = cell.model(), cell.fight_state()
            self.assertEqual(model.price(state), 0.0, cell)
            for action in model.actions:
                self.assertLessEqual(model.benefit(state, action), 0.0, f"{cell}: {action.name}")

    def test_a_hurt_boss_is_closer_to_done(self):
        for cell in fights(boss="whole"):
            hurt = cell.with_(boss="hurt")
            self.assertLessEqual(hurt.model().price(hurt.fight_state()),
                                 cell.model().price(cell.fight_state()) + 1e-6, cell)

    def test_cover_is_never_dearer_than_the_open(self):
        for cell in fights(fight_body="exposed"):
            covered = cell.with_(fight_body="fresh")
            self.assertLessEqual(covered.model().price(covered.fight_state()),
                                 cell.model().price(cell.fight_state()) + 1e-6, cell)


class TheClockIsPartOfIt(unittest.TestCase):
    def test_the_closer_dusk_is_the_more_urgent_everything_becomes(self):
        seen = [sv.urgency(state) for state in along_day("ticks_until_dusk")]
        self.assertEqual(seen, sorted(seen), seen)

    def test_a_night_answered_costs_nothing_extra(self):
        self.assertEqual(sv.night_loss(day_state(bed=True, sheltered=True)), 0.0)

    def test_a_night_unanswered_costs_something(self):
        self.assertGreater(sv.night_loss(day_state(bed=False, sheltered=False)), 0.0)


class TheOnlySourceOfValue(unittest.TestCase):
    """`estimate.saved_s` over this price is the whole scoring rule. A thing is worth the fall in the price it
    produces, and nothing may be added on top — no urgency multiplier, no weight, no prior."""

    GIFTS = ({"bed": True}, {"sheltered": True}, {"food_items": 8}, {"sword": 3}, {"pickaxe": 2},
             {"armor": 4}, {"shield": True}, {"torches": True}, {"hp": 20})

    def test_a_benefit_is_the_difference_of_two_prices(self):
        for dusk in DUSK:
            state = day_state(ticks_until_dusk=dusk)
            for gift in self.GIFTS:
                after = dict(state)
                after.update(gift)
                self.assertAlmostEqual(sv.benefit(state, gift),
                                       round(sv.expected_loss(state) - sv.expected_loss(after), 1),
                                       places=1, msg=f"{gift} at {dusk}")

    def test_nothing_is_worth_more_than_the_state_it_changes(self):
        for dusk in DUSK:
            state = day_state(ticks_until_dusk=dusk)
            whole = sv.expected_loss(state)
            for gift in self.GIFTS:
                self.assertLessEqual(sv.benefit(state, gift), whole + 1e-6, f"{gift} at {dusk}")

    def test_what_we_already_have_is_worth_nothing(self):
        for gift in self.GIFTS:
            already = day_state(**dict(gift))
            self.assertAlmostEqual(sv.benefit(already, gift), 0.0, places=1, msg=str(gift))

    def test_an_effect_never_changes_the_state_it_was_priced_from(self):
        state = day_state()
        before = dict(state)
        for gift in self.GIFTS:
            sv.benefit(state, gift)
        self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()

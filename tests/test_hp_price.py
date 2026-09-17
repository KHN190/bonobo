"""The price of health — what losing health costs, in seconds, from where we stand.

The fourth quantity. It is what makes "run away" and "keep digging" comparable at all, and it is why the same four
hearts mean different things to a fresh body and a dying one. Implemented in `survival` (health is only worth what
being hurt costs HERE) and handed to `estimate` as an argument, never re-implemented there.

Properties over the survival state, swept; no number asserted anywhere.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import estimate, survival as sv  # noqa: E402
from tests.world import LADDERS, along_day, dangers, day_state  # noqa: E402

# How much health is lost, as its own ladder — everything else about the body comes off the shared dimensions.
LOSSES = (0.0, 1.0, 4.0, 10.0, 25.0)


def bodies():
    """Every body the price is asked from: one step along each ladder that changes what being hurt costs."""
    for dim in ("hp", "food", "food_items", "sword", "armor", "shield", "bed"):
        for state in along_day(dim):
            yield state


class ItIsSecondsAndNeverNegative(unittest.TestCase):
    def test_over_every_body_and_every_loss(self):
        for body in bodies():
            for dhp in LOSSES:
                seconds = sv.hp_seconds(body, dhp)
                self.assertGreaterEqual(seconds, 0.0, f"{body['hp']}hp −{dhp}")
                self.assertLess(seconds, 1e6, f"{body['hp']}hp −{dhp}")

    def test_losing_nothing_costs_nothing(self):
        for body in bodies():
            self.assertEqual(sv.hp_seconds(body, 0.0), 0.0)
            self.assertEqual(sv.hp_seconds(body, -3.0), 0.0)


class MoreDamageCostsMore(unittest.TestCase):
    def test_over_every_body(self):
        for body in bodies():
            seen = [sv.hp_seconds(body, dhp) for dhp in LOSSES]
            self.assertEqual(seen, sorted(seen), f"{body['hp']}hp: {seen}")


class TheCurveHasNoCliff(unittest.TestCase):
    """A branch at "this kills us" priced every answer in a bad spot as the same certain death, so the cheapest
    certain death won and the agent stood still and was beaten. The curve must stay continuous across the bar."""

    def test_nothing_jumps_as_the_damage_crosses_the_health(self):
        for body in bodies():
            hp = float(body["hp"])
            just_under = sv.hp_seconds(body, hp - 0.05)
            just_over = sv.hp_seconds(body, hp + 0.05)
            step = just_over - just_under
            self.assertLess(step, max(1.0, 0.25 * max(just_under, 1.0)), f"{hp}hp: {just_under} → {just_over}")

    def test_the_chance_of_dying_is_a_probability(self):
        for body in bodies():
            for dhp in LOSSES:
                p = estimate.fatal_chance(body["hp"], dhp)
                self.assertTrue(0.0 <= p <= 1.0, f"{body['hp']}hp −{dhp}: {p}")

    def test_more_damage_and_less_health_are_both_more_likely_fatal(self):
        health = LADDERS["hp"]
        for hp in health:
            seen = [estimate.fatal_chance(hp, dhp) for dhp in LOSSES]
            self.assertEqual(seen, sorted(seen), f"{hp}hp: {seen}")
        for dhp in LOSSES[1:]:
            seen = [estimate.fatal_chance(hp, dhp) for hp in reversed(health)]
            self.assertEqual(seen, sorted(seen), f"−{dhp}: {seen}")

    def test_a_cap_bounds_the_answer_without_bending_the_curve(self):
        for hp in LADDERS["hp"]:
            for dhp in LOSSES:
                capped = estimate.fatal_chance(hp, dhp, cap=0.9)
                self.assertLessEqual(capped, 0.9)
                self.assertLessEqual(capped, estimate.fatal_chance(hp, dhp) + 1e-12)


class WhatTheBodyChangesAboutThePrice(unittest.TestCase):
    def test_the_same_loss_is_dearer_the_less_health_there_is(self):
        """Along the ladder the dimension declares, not between two bodies chosen by hand."""
        seen = [sv.hp_seconds(state, 6.0) for state in along_day("hp")]
        self.assertEqual(seen, sorted(seen, reverse=True), seen)

    def test_armour_makes_the_same_encounter_cheaper(self):
        seen = [sv.encounter_damage(state)[1] for state in along_day("armor")]
        self.assertEqual(seen, sorted(seen, reverse=True), seen)

    def test_the_price_a_cell_carries_is_a_price_of_health(self):
        """The cells carry their own price for exactly this reason: a column priced with somebody else's body is
        the bug that made a dying agent value a fight like a healthy one. What the price IS belongs to whoever
        owns the state — all that may be assumed here is that it is seconds and rises with the loss."""
        for cell in dangers(enemy="walker", distance="near", ground="open"):
            price = cell.price()
            self.assertEqual(price(0.0), 0.0, cell)
            seen = [price(dhp) for dhp in LOSSES]
            self.assertEqual(seen, sorted(seen), f"{cell}: {seen}")

    def test_the_less_blood_there_is_the_dearer_it_is(self):
        """Along the ladder, in the order the dimension declares it — not between two cells picked by hand. Two
        cells only say "these two differ"; a ladder says which way the model must lean."""
        for cell in dangers(enemy="walker", distance="near", ground="open", blood="whole"):
            seen = [world.price()(6.0) for world in cell.along("blood")]
            self.assertEqual(seen, sorted(seen), f"{cell}: {seen}")


if __name__ == "__main__":
    unittest.main()

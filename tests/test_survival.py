"""The survival model gives ordinary goals their value in seconds. Each test names a behaviour the old point
values could not produce: a bed at dusk, food before it runs out, a pickaxe before anything else."""
import unittest

from bonobo import priority as pr
from bonobo import survival as sv


class Values(unittest.TestCase):
    def test_bed_matters_more_at_dusk_than_at_dawn(self):
        dawn = sv.make_state(ticks_until_dusk=11000)
        dusk = sv.make_state(ticks_until_dusk=500)
        self.assertGreater(sv.urgency(dusk), sv.urgency(dawn))
        self.assertGreater(sv.benefit(dusk, {"bed": True}), 0)

    def test_a_bed_is_worth_a_whole_night(self):
        s = sv.make_state()
        self.assertGreaterEqual(sv.benefit(s, {"bed": True}), sv.CONFIG["time"]["night_s"])

    def test_shelter_removes_most_of_the_night_risk_but_not_the_idle_time(self):
        s = sv.make_state()
        self.assertGreater(sv.benefit(s, {"bed": True}), sv.benefit(s, {"sheltered": True}))
        self.assertGreater(sv.benefit(s, {"sheltered": True}), 0)

    def test_food_is_worth_more_when_none_is_carried(self):
        none = sv.make_state(food_items=0, food=8)
        some = sv.make_state(food_items=3, food=14)
        self.assertGreater(sv.benefit(none, {"food_items": 8}), sv.benefit(some, {"food_items": 8}))

    def test_first_pickaxe_beats_the_upgrade(self):
        s0, s1 = sv.make_state(pickaxe=0), sv.make_state(pickaxe=1)
        self.assertGreater(sv.benefit(s0, {"pickaxe": 1}), sv.benefit(s1, {"pickaxe": 2}))

    def test_phantoms_make_the_bed_more_valuable(self):
        s = sv.make_state(nights_missed=3)
        self.assertGreater(sv.benefit(s, {"bed": True}), sv.benefit(sv.make_state(), {"bed": True}))

    def test_effects_are_pure(self):
        s = sv.make_state()
        sv.benefit(s, {"bed": True})
        self.assertFalse(s["bed"])

    def test_unknown_state_keys_are_refused(self):
        with self.assertRaises(KeyError):
            sv.make_state(hat=True)

    def test_guesses_are_declared(self):
        self.assertIn("night_open", sv.UNMEASURED)


class Candidates(unittest.TestCase):
    def test_seconds_and_points_rank_on_one_scale(self):
        from bonobo import priority as pr
        by_points = pr.Candidate("legacy", 9, 2000, lambda: None)
        by_seconds = pr.Candidate("bed", 0, 2000, lambda: None, seconds=9 * pr.SECONDS_PER_VALUE)
        self.assertAlmostEqual(by_points.score, by_seconds.score)

    def test_explain_reports_seconds(self):
        # Every term of the explanation is seconds and they add up to the score: that is what makes the number
        # mean something. The old line multiplied four unitless factors and divided by a time.
        line = pr.Candidate("x", 0, 600, lambda: None, seconds=100.0).explain()
        self.assertIn("100s", line)
        self.assertIn("30s work", line)
        self.assertIn("+70s", line)
        self.assertIn("100s", line)
        self.assertIn("30s work", line)
        self.assertIn("+70s", line)


if __name__ == "__main__":
    unittest.main()


class Encounters(unittest.TestCase):
    """The encounter model: what a weapon, armour and health are worth in seconds (sv.fight_loss)."""

    def base(self, **kw):
        return sv.make_state(**{"pickaxe": 1, "sword": 0, "food_items": 8, "bed": True, "hp": 20, **kw})

    def test_a_sword_is_worth_real_time(self):
        # Without one the threat layer refuses the fight, so every encounter is a flight — and the mob is still
        # there afterwards. A flat per-day risk priced a sword at 10 s and it was never made.
        self.assertGreater(sv.benefit(self.base(), {"sword": 1}), 30)

    def test_iron_beats_stone(self):
        s = self.base()
        self.assertGreater(sv.benefit(s, {"sword": 2}), sv.benefit(s, {"sword": 1}))

    def test_being_hurt_costs_and_healing_saves_more_the_worse_it_is(self):
        at18 = sv.benefit(self.base(hp=18, sword=1), {"hp": 20})
        at10 = sv.benefit(self.base(hp=10, sword=1), {"hp": 20})
        self.assertGreater(at18, 0)
        self.assertGreater(at10, at18)

    def test_armour_and_a_shield_pay_for_themselves(self):
        s = self.base(sword=1)
        self.assertGreater(sv.benefit(s, {"armor": 2}), 0)
        self.assertGreater(sv.benefit(s, {"shield": True}), 0)

    def test_a_bed_in_the_open_is_not_a_night_skipped(self):
        # Mobs interrupt sleep: a shelter is worth building even carrying a bed, which is why nothing built one
        # while night_loss returned 0 for any bed at all.
        self.assertGreater(sv.benefit(self.base(), {"sheltered": True}), 0)

    def test_sheltered_with_a_bed_is_free(self):
        self.assertEqual(sv.night_loss(self.base(sheltered=True)), 0.0)

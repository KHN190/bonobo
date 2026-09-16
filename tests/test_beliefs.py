"""One belief table. Written before the table exists: the point is what must be true afterwards.

The same fact was written down twice. A skeleton's reach was 15 blocks in `play.toml` (where ordinary play prices
a threat) and 3 in `combat_model.HAZARD_R` (where movement decides what to walk around), so the planner would
price an archer as dangerous at fifteen blocks while navigation walked to within four. A creeper was 5 and 3, a
witch 3 and 8. Nobody wrote a bug; two tables of one fact simply drift.

Same for what a death costs (`play.toml` says 240 s, `fight.toml` said 120) and for how much armour saves
(`threat.protection` and `survival.protection`, two functions of the same idea).

Beliefs also carry how much they are worth trusting: `(value, n)`, where `n` is how many observations are behind
it. Every number starts at n=0 — a declared guess — and `fit` raises it from the tape. Nothing reads the count
yet; the shape is here so that when it is read, it does not mean changing every call site again.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import beliefs, combat_model, fight_plan, survival, threat  # noqa: E402


class OneTable(unittest.TestCase):
    def test_reach_and_keep_out_are_different_questions_in_one_row(self):
        archer = beliefs.mob("minecraft:skeleton")
        self.assertGreater(archer["reach"], archer["keep_out"])

    def test_the_hazard_radii_are_a_view_of_the_table(self):
        """`HAZARD_R` is a view, not a second copy."""
        self.assertEqual(combat_model.HAZARD_R,
                         {k: m["keep_out"] for k, m in beliefs.MOBS.items() if m.get("keep_out")})

    def test_the_end_entities_are_in_the_same_table(self):
        for kind in ("minecraft:ender_dragon", "minecraft:enderman", "minecraft:area_effect_cloud"):
            self.assertIn(kind, beliefs.MOBS, f"{kind} was only in the fight's own table")

    def test_a_death_costs_the_same_in_a_fight_as_out_of_one(self):
        self.assertEqual(fight_plan.CONFIG["combat"]["death_cost_s"], beliefs.value("time.death_cost_s"))

    def test_armour_is_one_function(self):
        """There were two protection functions over two different units, so the same chestplate was worth two
        different things. Both layers now reach the table's one function."""
        self.assertAlmostEqual(threat.protection(8, True), beliefs.protection(8, True))
        self.assertAlmostEqual(survival._protection(survival.make_state(armor=8, shield=True)),
                               beliefs.protection(8, True))


class Trust(unittest.TestCase):
    def test_every_belief_carries_a_count(self):
        value, n = beliefs.belief("time.death_cost_s")
        self.assertIsInstance(n, int)
        self.assertEqual(value, beliefs.value("time.death_cost_s"))

    def test_guesses_start_at_no_observations(self):
        for name in beliefs.UNMEASURED:
            self.assertEqual(beliefs.count(name), 0, f"{name} is listed unmeasured but claims observations")

    def test_an_unknown_belief_is_an_error_not_a_default(self):
        with self.assertRaises(KeyError):
            beliefs.value("time.no_such_number")


if __name__ == "__main__":
    unittest.main()


class FromTheWiki(unittest.TestCase):
    def test_published_numbers_count_as_observed(self):
        self.assertGreater(beliefs.count("mobs.minecraft:zombie.hp"), 0)
        self.assertGreater(beliefs.count("mobs.minecraft:zombie.attack"), 0)
        self.assertGreater(beliefs.count("mobs.minecraft:zombie.notice_r"), 0)

    def test_what_the_wiki_does_not_say_stays_unmeasured(self):
        self.assertEqual(beliefs.count("mobs.minecraft:zombie.attack_s"), 0)
        self.assertEqual(beliefs.count("risk.encounters_per_day"), 0)

    def test_the_published_values_are_the_published_values(self):
        z, s, e = beliefs.mob("minecraft:zombie"), beliefs.mob("minecraft:skeleton"), beliefs.mob("minecraft:enderman")
        self.assertEqual((z["hp"], z["attack"], z["notice_r"]), (20, 3.0, 35.0))
        self.assertEqual((s["hp"], s["attack"], s["notice_r"]), (20, 4.0, 16.0))
        self.assertEqual((e["hp"], e["attack"], e["notice_r"]), (40, 7.0, 64.0))

    def test_damage_per_second_is_derived_not_stored(self):
        z = beliefs.mob("minecraft:zombie")
        self.assertAlmostEqual(z["dps"], z["attack"] / z["attack_s"])

    def test_a_mob_notices_from_its_own_distance(self):
        self.assertGreater(beliefs.mob("minecraft:enderman")["notice_r"],
                           beliefs.mob("minecraft:skeleton")["notice_r"])

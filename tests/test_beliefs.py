"""One belief table: one fact, one place (a skeleton's reach was 15 in play.toml and 3 in combat_model; a death 240 s
and 120 s). Every layer reads the same number through beliefs."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import beliefs, combat_model, fight_plan, threat  # noqa: E402


class OneTable(unittest.TestCase):
    # (fact, where one layer reads it, where the other reads it): the same number, from the one table
    def rows(self):
        archer = beliefs.mob("minecraft:skeleton")
        return [
            ("the hazard radii are a view of the table", combat_model.HAZARD_R,
             {k: m["keep_out"] for k, m in beliefs.MOBS.items() if m.get("keep_out")}),
            ("a death costs the same in a fight", fight_plan.CONFIG["combat"]["death_cost_s"],
             beliefs.value("time.death_cost_s")),
            ("reach and keep-out are two questions in one row", (archer["reach"], archer["keep_out"]), (15.0, 3.0)),
            ("the End's entities are in the one table",
             {"minecraft:ender_dragon", "minecraft:enderman", "minecraft:area_effect_cloud"} - set(beliefs.MOBS), set()),
            ("armour: none", threat.protection(0, False), beliefs.protection(0, False)),
            ("armour: 8 and a shield", threat.protection(8, True), beliefs.protection(8, True)),
            ("armour: 20, no shield", threat.protection(20, False), beliefs.protection(20, False)),
            ("armour through the price state", threat._protection(threat.price_state(armor=8, shield=True)),
             beliefs.protection(8, True)),
        ]

    def test_one_fact_one_place(self):
        for name, a, b in self.rows():
            with self.subTest(name):
                self.assertEqual(a, b)


class Paths(unittest.TestCase):
    def test_unknown_beliefs_are_errors(self):
        for path in ("time.no_such_number", "mobs.minecraft:zombie.no_such_field", "mobs.minecraft:unicorn.hp",
                     "no_section.x"):
            with self.subTest(path), self.assertRaises(KeyError):
                beliefs.value(path)


class FromTheWiki(unittest.TestCase):
    # (mob) → (hp, attack, notice_r, dps = attack / attack_s): published numbers, and the one derived from them
    MOBS = [("minecraft:zombie", 20, 3.0, 35.0), ("minecraft:skeleton", 20, 4.0, 16.0),
            ("minecraft:enderman", 40, 7.0, 64.0), ("minecraft:wither_skeleton", 20, 8.0, 16.0)]

    def test_published_values(self):
        for kind, hp, attack, notice in self.MOBS:
            m = beliefs.mob(kind)
            with self.subTest(kind):
                self.assertEqual((m["hp"], m["attack"], m["notice_r"]), (hp, attack, notice))
                self.assertAlmostEqual(m["dps"], attack / m["attack_s"])
        with self.subTest("must fail: an animal has no row (it does not fight back)"), self.assertRaises(KeyError):
            beliefs.mob("minecraft:cow")


if __name__ == "__main__":
    unittest.main()

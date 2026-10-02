"""The game's armour and explosion formulas (formulas.py), each row from the Minecraft Wiki's worked numbers."""
import unittest

from bonobo import beliefs, formulas as game


class Formulas(unittest.TestCase):
    ARMOR = [  # (points, toughness, hit, fraction removed)
        ("no armour", 0, 0, 3.0, 0.0),
        ("a small hit: nearly a/25", 15, 0, 1.0, 0.58),
        ("a big hit wears iron down to a/5 (must fail on a flat 4 % a point: 0.6)", 15, 0, 43.0, 0.12),
        ("toughness keeps more of it", 20, 8, 20.0, 0.6),
        ("the cap: 20 of 25", 30, 0, 1.0, 0.8),
    ]
    BLAST = [  # (power, distance, damage)
        ("a creeper at point blank: 43", 3.0, 0.0, 43.0),
        ("halfway to its reach", 3.0, 3.0, 16.75),
        ("must fail on a flat 43: at its 6-block edge nothing", 3.0, 6.0, 0.0),
        ("a ghast's fireball at point blank: 15", 1.0, 0.0, 15.0),
    ]

    def test_armor(self):
        for name, points, tough, hit, want in self.ARMOR:
            with self.subTest(name):
                self.assertAlmostEqual(game.armor_reduction(points, tough, hit), want, places=2)

    def test_blast(self):
        for name, power, d, want in self.BLAST:
            with self.subTest(name):
                self.assertAlmostEqual(game.explosion_damage(power, d), want, places=2)

    def test_protection_asks_the_hit(self):
        """Must fail if a caller can leave the hit out (a second, flat arithmetic)."""
        with self.assertRaises(TypeError):
            beliefs.protection(10)   # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()

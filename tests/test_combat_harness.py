"""The combat rows' harness: a scene proven before the run (exact line-up, the kit in hand), and checks a fighter that
does nothing — or whose kills are the sun's or the plan's — fails."""
import unittest

from bonobo.bench import runner
from bonobo.bench.words import fight
from tests.world import bag, inventory


class EntityMismatch(unittest.TestCase):
    def test_rows(self):
        # (situation, count on the server, at least, at most) → a mismatch?
        rows = [("one wanted, one there", 1, 1, 1, False), ("at least one, three there (no cap)", 3, 1, None, False),
                ("must fail: exactly one wanted, a second spawned", 2, 1, 1, True),
                ("must fail: none there", 0, 1, 1, True)]
        for name, n, want, most, bad in rows:
            with self.subTest(name):
                self.assertEqual(runner.entity_mismatch("minecraft:zombie", n, want, most) is not None, bad)


class GearMismatches(unittest.TestCase):
    def test_rows(self):
        gear = {"items": [["minecraft:iron_sword", 1]], "offhand": "minecraft:shield"}
        sword = ("iron_sword", 1)
        rows = [("sword carried, shield in the offhand", inventory(sword, offhand="shield"), 0),
                ("must fail: no sword", inventory(offhand="shield"), 1),
                ("must fail: the shield not in the offhand", inventory(sword), 1),
                ("must fail: neither", inventory(), 2)]
        for name, answer, bad in rows:
            with self.subTest(name):
                self.assertEqual(len(runner.gear_mismatches(bag(answer), gear)), bad)


class EngagedGaps(unittest.TestCase):
    def test_rows(self):
        # (situation, bids [(when, engaged, kind)]) → the gaps judged
        rows = [("engaged throughout", [(0.0, True, "fight"), (0.1, True, "fight"), (0.25, True, "fight")],
                 [0.1, 0.15]),
                ("the walk-in before engaging is not judged", [(0.0, False, None), (2.0, False, None),
                                                               (2.1, True, "fight")], []),
                ("a gap across the end of an engagement is not judged", [(0.0, True, "fight"), (3.0, False, None)], []),
                ("must fail: a long gap while engaged is judged", [(0.0, True, "fight"), (1.0, True, "fight")], [1.0])]
        for name, bids, want in rows:
            with self.subTest(name):
                self.assertEqual([round(g, 2) for g in fight.engaged_gaps(bids)], want)


class KillsWhileEngaged(unittest.TestCase):
    def test_rows(self):
        # (situation, samples [(alive, engaged)]) → the fight's kills
        rows = [("three killed while engaged", [(3, True), (2, True), (0, True)], 3),
                ("a kill as the engagement ends still counts", [(1, True), (0, False)], 1),
                ("must fail: the sun burned them while nothing was engaged", [(3, False), (0, False)], 0),
                ("a mob that came back is no negative kill", [(1, True), (2, True), (1, True)], 1)]
        for name, samples, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.kills_while_engaged(samples), want)


if __name__ == "__main__":
    unittest.main()

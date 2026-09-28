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
        # (situation, samples [(when, alive)], bids [(when, engaged, kind)]) → the fight's kills
        fought = [(0.5, True, "fight"), (1.5, True, "fight")]
        rows = [("three killed while engaged", [(0, 3), (1, 2), (2, 0)], fought, 3),
                ("a fight begun and ended inside one round still counts", [(0, 1), (5, 0)], [(2.0, True, "fight")], 1),
                ("must fail: the sun burned them, nothing engaged", [(0, 3), (5, 0)], [(2.0, False, None)], 0),
                ("a mob that came back is no negative kill", [(0, 1), (1, 2), (2, 1)], fought, 1)]
        for name, samples, bids, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.kills_while_engaged(samples, bids), want)


class KeptOff(unittest.TestCase):
    """low_hp_eat's end: every hostile 3 or more off, or the body walled in."""

    def test_rows(self):
        rows = [("backed off: 4 blocks", [4.0], False, True), ("walled in beside it", [1.0], True, True),
                ("nothing left in reach", [], False, True),
                ("must fail: stood still, the zombie at 1.5", [1.5], False, False)]
        for name, gaps, enclosed, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.kept_off(gaps, enclosed), want)



class GapOpen(unittest.TestCase):
    """block_gap's scene proof: the corridor's gap open before the run."""

    def test_rows(self):
        g = fight.GAP
        rows = [("nothing solid: open", [], True), ("solid elsewhere only: open", [(0, 0, 0)], True),
                ("must fail: one gap cell solid", [g[0]], False), ("must fail: the gap filled", list(g), False)]
        for name, solid, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.gap_open(solid), want)



class NeedsRespawn(unittest.TestCase):
    """runner.needs_respawn: a row begins alive — the dead flag or no health means respawn first."""

    def test_rows(self):
        rows = [("alive", {"dead": False, "health": 20.0}, False), ("hurt but alive", {"dead": False, "health": 3.0}, False),
                ("dead", {"dead": True, "health": 0.0}, True),
                ("must fail to start: the death screen reads not dead with no health", {"dead": False, "health": 0.0},
                 True)]
        for name, state, want in rows:
            with self.subTest(name):
                self.assertEqual(runner.needs_respawn(state), want)



class OneRunPerFight(unittest.TestCase):
    """runner.decided_by_chance: a combat-tier row runs once and that run is its verdict; other chance rows wait."""

    def test_rows(self):
        rows = [("a combat row, however random", {"tier": "combat", "combat": True}, False),
                ("a deterministic row", {"tier": "core", "setup": ["fill 0 0 0 1 1 1 stone"], "doc": "a wall"}, False),
                ("a chance row outside combat waits for more runs", {"tier": "common", "setup": ["summon cow 1 2 3"],
                                                                   "doc": ""}, True),
                ("must fail: a combat row re-run until it passes", {"tier": "combat", "stochastic": True}, False)]
        for name, row, want in rows:
            with self.subTest(name):
                self.assertEqual(runner.decided_by_chance(row), want)
        with self.subTest("its one run decides"):
            self.assertEqual(runner.verdict_of([False], chance=False), "fail")
            self.assertEqual(runner.verdict_of([True], chance=False), "pass")


if __name__ == "__main__":
    unittest.main()

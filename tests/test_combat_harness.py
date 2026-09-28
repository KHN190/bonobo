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
        # (situation, samples [(when, [(id, hp, distance)])], bids [(when, engaged, kind)]) → proven kills
        fought = [(0.5, True, "fight"), (1.5, True, "fight")]
        rows = [("hurt, in reach, gone while fighting: a kill", [(0, [(7, 20.0, 2.0)]), (1, [(7, 8.0, 2.5)]), (2, [])],
                 fought, 1),
                ("three hurt and gone in reach", [(0, [(1, 20.0, 2), (2, 20.0, 3), (3, 20.0, 3)]),
                                                  (1, [(1, 5.0, 2), (2, 4.0, 3), (3, 3.0, 3)]), (2, [])], fought, 3),
                ("must fail: vanished at full health 10 off — no kill", [(0, [(7, 20.0, 10.0)]), (1, [])], fought, 0),
                ("must fail: hurt and gone but nothing engaged", [(0, [(7, 20.0, 2.0)]), (1, [(7, 6.0, 2.0)]), (2, [])],
                 [(0.5, False, None), (1.5, False, None)], 0),
                ("must fail: hurt but gone far off (fled or despawned)", [(0, [(7, 20.0, 2.0)]), (1, [(7, 6.0, 12.0)]),
                                                                          (2, [])], fought, 0),
                ("a short first reading (0) is no baseline: 20, hurt to 6, gone — a kill",
                 [(0, [(7, 0.0, 2.0)]), (0.4, [(7, 20.0, 2.0)]), (1, [(7, 6.0, 2.0)]), (2, [])], fought, 1)]
        for name, samples, bids, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.kills_while_engaged(samples, bids), want)


class ColumnsPossible(unittest.TestCase):
    """The columns a cell paid for: its kit's, less eating at full health; a siege cell names no kit."""

    def test_rows(self):
        rows = [("blocks, whole: the shaping columns", {"kit": "blocks", "blood": "whole"}, {"reshape", "wall_in"}),
                ("food, hurt: eat", {"kit": "food", "blood": "hurt"}, {"eat"}),
                ("must fail: food at full health is no eat column", {"kit": "food", "blood": "whole"}, set()),
                ("a siege cell {wave, line_up}: nothing, no KeyError", {"wave": 1, "line_up": "one walker"}, set())]
        for name, cell, want in rows:
            with self.subTest(name):
                self.assertEqual(fight._columns_possible(cell), want)


class LastSeen(unittest.TestCase):
    """The readout of each mob that went: its last reading and the most health seen — why a kill counted or not."""

    def test_rows(self):
        rows = [("one killed in reach", [(0, [(7, 20.0, 2.0)]), (1, [(7, 3.0, 2.5)]), (2, [])], {7: [3.0, 2.5, 20.0]}),
                ("two, one went", [(0, [(1, 20.0, 2), (2, 20.0, 6)]), (1, [(2, 20.0, 6)])], {1: [20.0, 2, 20.0]}),
                ("must fail: none went — nothing listed", [(0, [(7, 20.0, 2.0)]), (1, [(7, 9.0, 2.0)])], {}),
                ("no samples", [], {})]
        for name, samples, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.last_seen(samples), want)


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



class CheckParts(unittest.TestCase):
    """runner.check_parts: a failed check made of parts says which part said no."""

    def test_rows(self):
        from bonobo.bench.words.checks import _all
        yes, no = (lambda a, i: True), (lambda a, i: False)
        yes.__table__, no.__table__ = ("gone",), ("kills_by_the_fight", 1)

        def boom(a, i):
            raise ValueError("bad")
        boom.__table__ = ("decision_gaps_ok",)
        rows = [("all parts said", _all(yes, no), [("('gone',)", True), ("('kills_by_the_fight', 1)", False)]),
                ("a part that raised names its error", _all(boom), [("('decision_gaps_ok',)", "ValueError: bad")]),
                ("must fail: a check with no parts names nothing", no, [])]
        for name, check, want in rows:
            with self.subTest(name):
                got = runner.check_parts(check, None, None)
                # a raised part also says where (its frames after ' @ ')
                self.assertEqual([(w, v.split(" @ ")[0] if isinstance(v, str) else v) for w, v in got], want)
        with self.subTest("the raised part's frames name the function that raised"):
            self.assertIn(" boom", runner.check_parts(_all(boom), None, None)[0][1].split(" @ ")[1])



class DiedDuring(unittest.TestCase):
    """runner.died_during: any death in the run fails it, whatever hp reads after the respawn."""

    def test_rows(self):
        s = lambda x, hp=20.0, dead=False: {"x": x, "z": 0.0, "health": hp, "dead": dead}      # noqa: E731
        rows = [("fought and lived", [s(0.5), s(1.5, 14.0), s(2.0, 12.0)], False),
                ("must fail: read dead once", [s(0.5), s(0.5, 0.0, True), s(-527.0)], True),
                ("must fail: never read dead, but back at spawn (a jump no walk makes)", [s(0.5, 4.0), s(-527.0)],
                 True),
                ("a sprint is no jump", [s(0.5), s(4.0)], False)]
        for name, trace, want in rows:
            with self.subTest(name):
                self.assertEqual(runner.died_during(trace), want)



class FightReset(unittest.TestCase):
    """fight_loop.reset: a row begins with no held decision from the last one."""

    def test_rows(self):
        from unittest import mock
        from bonobo import fight_loop
        rows = [("a held decision dropped", object(), None), ("nothing held: still nothing", None, None)]
        for name, held, want in rows:
            with self.subTest(name), mock.patch.object(fight_loop.STATE, "held", held):
                fight_loop.reset()
                self.assertIs(fight_loop.STATE.held, want)
        with self.subTest("must fail: without the reset the last row's decision is still held"):
            marker = object()
            with mock.patch.object(fight_loop.STATE, "held", marker):
                self.assertIs(fight_loop.STATE.held, marker)



class LongestStall(unittest.TestCase):
    """words.fight.longest_stall: the body never stands with no task while engaged beside a mob."""

    def test_rows(self):
        bids, near = [(0.0, True, "fight")], [(0.0, [(7, 20.0, 2.0)])]
        run = lambda *tasks: [{"t": round(0.2 * i, 1), "task": t} for i, t in enumerate(tasks)]    # noqa: E731
        rows = [("a task all the way", run(1, 1, 2, 2), bids, near, 0.0),
                ("one sample between tasks: within a poll", run(1, None, 2), bids, near, 0.0),
                ("idle but not engaged: not the fight's stall", run(1, None, None, 2), [(0.0, False, None)], near, 0.0),
                ("idle but the mob far off: nothing to act on", run(1, None, None, 2), bids, [(0.0, [(7, 20.0, 9.0)])],
                 0.0),
                ("must fail: a task ended and 0.4 s idle beside the mob", run(1, None, None, None, 2), bids, near, 0.4)]
        for name, trace, b, alive, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.longest_stall(trace, b, alive), want)



class ShieldOnlyWhereTheJarHoldsUse(unittest.TestCase):
    """threat.options offers the shield-alone answer only where the jar can hold the use key (state hold_use)."""

    def test_rows(self):
        from bonobo import threat
        zombie = ((2.0, 64.0, 0.0), 3.0, (0.0, 0.0, 0.0), "minecraft:zombie", 1.0, 6.25)
        base = {"here": (0.0, 64.0, 0.0), "hp": 20.0, "sword": 2, "protection": 0.0, "hazards": [zombie],
                "shield": True, "ids": [7], "blocks": 0, "food_items": 0, "hunger": 20.0}
        kinds = lambda st: {o.kind for o in threat.options(st)}      # noqa: E731
        rows = [("a jar that holds use: the shield alone on offer", dict(base, hold_use=True), True),
                ("no word from the jar (pure callers): offered as before", dict(base), True),
                ("must fail: 0.1.62 cannot hold use — never offered", dict(base, hold_use=False), False),
                ("no shield: never offered", dict(base, shield=False, hold_use=True), False)]
        for name, st, offered in rows:
            with self.subTest(name):
                self.assertEqual("shield" in kinds(st), offered)
        with self.subTest("the fight behind the shield stays either way (the attack's own)"):
            self.assertIn("fight_shielded", kinds(dict(base, hold_use=False)))


if __name__ == "__main__":
    unittest.main()

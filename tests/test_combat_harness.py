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


class SweepCheckParts(unittest.TestCase):
    """A sweep's check names its parts: the row count, then each rule with what it said."""

    def test_rows(self):
        from bonobo.bench import core

        def broken(rows):
            return ["wave 1 not cleared"]

        def kept(rows):
            return []
        rows = [("every rule kept", [kept], 1, (True, [("('rows',)", True), ("('kept',)", True)])),
                ("must fail: a broken rule names its words", [broken, kept], 1,
                 (False, [("('rows',)", True), ("('broken',)", "wave 1 not cleared"), ("('kept',)", True)])),
                ("too few rows: the count, the rules not judged", [kept], 3,
                 (False, [("('rows',)", "1 rows, need ≥ 3"), ("('kept',)", "not judged")])),
                ("no rules: rows only", [], 1, (True, [("('rows',)", True)]))]
        for name, rules, least, want in rows:
            with self.subTest(name):
                core.SWEEP["t"] = [{"cell": 1}]
                check = core._sweep_check("t", "/nonexistent", rules, least)
                self.assertEqual((check(None, None), runner.check_parts(check, None, None)), want)
        core.SWEEP.pop("t", None)


class Resolved(unittest.TestCase):
    """A threat is over when we killed it (the server's count) or keep it off; a creeper that blew up is not."""

    def test_rows(self):
        # (start hp, hp now, gaps first, gaps after the hold, server kills) → resolved
        rows = [("killed, 18 hp left", 20, 18, [], [], 1, True),
                ("must fail: gone with no kill — the idle bot's creeper blew up", 20, 17, [], [], 0, False),
                ("must fail: killed, but the blast took 9", 20, 11, [], [], 1, False),
                ("kept 7 off and not closing", 20, 20, [7.0], [7.5], 0, True),
                ("must fail: 7 off, then closing to 3", 20, 20, [7.0], [3.0], 0, False)]
        for name, start, now, first, last, kills, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.resolved(start, now, first, last, kills), want)


class GhastAnswered(unittest.TestCase):
    """ghast_fireball passes on the server's word: the ghast hurt or dead, or it fired and nothing hit us."""

    def test_health_from_the_reply(self):
        from bonobo.bench import vocab
        rows = [("full", ["Ghast has the following entity data: 10.0f"], 10.0),
                ("hurt", ["Ghast has the following entity data: 3.5f"], 3.5),
                ("must fail: no entity answered", ["No entity was found"], None), ("nothing said", [], None)]
        for name, lines, want in rows:
            with self.subTest(name):
                self.assertEqual(vocab.ghast_health(lines), want)

    def test_rows(self):
        from bonobo.bench import vocab
        # (ghast seen, its health now, fireballs seen, start hp, worst hp) → answered
        rows = [("struck back: the ghast hurt", True, 4.0, 1, 20.0, 20.0, True),
                ("the ghast dead", True, None, 2, 20.0, 14.0, True),
                ("it fired, nothing hit us", True, 10.0, 1, 20.0, 20.0, True),
                ("must fail: idle — it fired and hit us, unhurt itself", True, 10.0, 1, 20.0, 13.0, False),
                ("must fail: idle — it never fired", True, 10.0, 0, 20.0, 20.0, False),
                ("must fail: no ghast ever read", False, None, 0, 20.0, 20.0, False)]
        for name, seen, ghp, fb, start, worst, want in rows:
            with self.subTest(name):
                self.assertEqual(vocab.ghast_was_answered(seen, ghp, fb, start, worst), want)


class RunWordsResume(unittest.TestCase):
    """A run word calls its work again after a faster layer took the body (the brain's Brain.attempt rule)."""

    def test_rows(self):
        from bonobo import api
        from bonobo.bench import table
        # (what each call does in turn, raise or return) → the result, or the error that ends the run
        rows = [("the fight took the body, then the work ran", [api.CommitmentExpired("fight"), "rods"], "rods"),
                ("an interrupt, a preemption, then done", [api.Interrupted("x"), api.CommitmentExpired("y"), 7], 7),
                ("must fail: the work's own failure is no preemption", [api.NotAvailable("no blaze")], api.NotAvailable),
                ("the game lost is not resumed by a run word", [api.GameUnreachable("gone")], api.GameUnreachable)]
        for name, script, want in rows:
            with self.subTest(name):
                calls, waited = list(script), []
                busy = iter([object(), None] * 5)

                def work():
                    step = calls.pop(0)
                    if isinstance(step, Exception):
                        raise step
                    return step
                run = lambda: table.resuming(work, holder=lambda: next(busy), sleep=waited.append)  # noqa: E731
                if isinstance(want, type):
                    with self.assertRaises(want):
                        run()
                else:
                    self.assertEqual(run(), want)
                    self.assertTrue(waited, "it waited for the body to be handed back")


class GhastReadout(unittest.TestCase):
    def test_rows(self):
        from bonobo.bench import vocab
        rows = [("a fireball seen, unhit", {"seen": True, "hp": 10.0, "fireballs": {1}, "start": 20.0, "worst": 20.0},
                 {"seen": True, "ghast_hp": 10.0, "fireballs": 1, "start_hp": 20.0, "worst_hp": 20.0}),
                ("must fail: no ghast read", {"seen": False, "hp": None, "fireballs": set(), "start": 20.0,
                                              "worst": 20.0},
                 {"seen": False, "ghast_hp": None, "fireballs": 0, "start_hp": 20.0, "worst_hp": 20.0}),
                ("nothing watched", {}, {"seen": None, "ghast_hp": None, "fireballs": 0, "start_hp": None,
                                         "worst_hp": None})]
        for name, watch, want in rows:
            with self.subTest(name):
                self.assertEqual(vocab.ghast_readout(watch), want)


class BehaviourParts(unittest.TestCase):
    """A behaviour check names its parts: dig_in's went_out, depth, hp_lost."""

    def test_rows(self):
        rule = fight.BEHAVIOURS["dig_in"][1]
        y0 = fight.START_Y
        dug = {"answered": [{"kind": "reshape", "outcome": "answered"}], "trace": [{"pos": [0, y0 - 2, 0]}],
               "outcome": {"hp_lost": 1.0}}
        fought = dict(dug, answered=[{"kind": "fight", "outcome": "answered"}])
        shallow = dict(dug, trace=[{"pos": [0, y0 - 1, 0]}])
        # (the recorded row) → (passed, [(part, said)])
        rows = [("dug two down, health kept", dug, (True, [True, True, True])),
                ("must fail: fought instead", fought, (False, [False, True, True])),
                ("one down only", shallow, (False, [True, False, True])),
                ("no row recorded", None, (False, [False, False, False]))]
        for name, recorded, want in rows:
            with self.subTest(name):
                fight.SWEEP["combat__dig_in"] = [recorded] if recorded else []
                check = fight._behaviour_check("combat__dig_in", rule)
                self.assertEqual((check(None, None), [v for _w, v in runner.check_parts(check, None, None)]), want)
        fight.SWEEP.pop("combat__dig_in", None)


class Endermen(unittest.TestCase):
    def test_angers(self):
        rows = [("two calm", ["Enderman has the following entity data: 0"] * 2, [0, 0]),
                ("must fail: one provoked", ["Enderman has the following entity data: 0",
                                             "Enderman has the following entity data: 412"], [0, 412]),
                ("none found", ["No entity was found"], []), ("nothing said", [], [])]
        for name, lines, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.angers(lines), want)

    def test_positions(self):
        rows = [("two", ["Enderman has the following entity data: [1.5d, 64.0d, -2.5d]",
                         "Enderman has the following entity data: [-3.2d, 64.0d, 2.5d]"], [[1.5, 64.0, -2.5], [-3.2, 64.0, 2.5]]),
                ("must fail: none found", ["No entity was found"], []), ("nothing said", [], [])]
        for name, lines, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.positions(lines), want)

    def test_enderman_walk_has_no_sword(self):
        from bonobo.bench import bench_bases
        self.assertNotIn("sword", bench_bases.KIT.get("fight_enderman_1", []))

    def test_covered_in_time(self):
        cells = [(4, 64, 0)]

        def at(t, x, hp):
            return {"t": t, "x": x + 0.5, "y": 64.0, "z": 0.5, "health": hp}
        rows = [("under it at 2 s, no hit after", [at(0, 0, 20), at(2, 4, 18), at(5, 4, 18)], True),
                ("must fail: never there (idle)", [at(0, 0, 20), at(5, 0, 12)], False),
                ("too late: 7 s", [at(0, 0, 20), at(7, 4, 18)], False),
                ("hit after it got there", [at(0, 0, 20), at(2, 4, 18), at(4, 4, 11)], False)]
        for name, trace, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.covered_in_time(trace, cells, 5.0), want)


class GhastSpawn(unittest.TestCase):
    """The ghast is summoned where vanilla lets it target us: its height off ours within GHAST_TARGET_DY."""

    def test_within_targeting(self):
        from unittest import mock
        from bonobo.bench import vocab
        said = []
        with mock.patch.object(vocab, "_chat", said.append):
            vocab._summon_ghast(None)
        x, y, z = (float(v) for v in said[0].split("summon ghast ")[1].split()[:3])
        dy = y - vocab.at(0, 0, 0)[1]
        self.assertLessEqual(abs(dy), vocab.GHAST_TARGET_DY)
        self.assertLess(abs(x - vocab.at(0, 0, 0)[0]), vocab.GHAST_FIRE_R)


class DerivedScenes(unittest.TestCase):
    """Scene spots come from rules, never hand-placed to fit one path."""

    def test_endermen_off_path(self):
        import re
        from bonobo.field import PLAYER_HALF, TALL_WIDTH
        start, end = (-7.0, 64.0, 0.0), (7.0, 64.0, 0.0)
        for n in (1, 3, 4, 6):
            with self.subTest(n=n):
                cmds = fight.endermen_off_path(start, end, n)
                zs = [float(re.search(r"summon \S+ \S+ \S+ (\S+)", c).group(1)) for c in cmds]
                self.assertEqual(len(cmds), n)
                # clear of a body walking the line (z 0): half its width + ours, never touching
                self.assertTrue(all(abs(z) - TALL_WIDTH / 2 >= PLAYER_HALF for z in zs))

    def test_alcove_cover(self):
        from bonobo.field import reached_from
        rows = [("2 deep: the inner cell", 2), ("3 deep", 3), ("must fail: 1 deep has none", 1), ("4 deep", 4)]
        for name, depth in rows:
            with self.subTest(name):
                cells = fight.alcove_cover(4, depth)
                self.assertEqual(len(cells), sum(1 for k in range(depth) if not reached_from((k + 1, 0))))
                self.assertEqual(bool(cells), depth > 1)


class UnarmedCellsGetNoSword(unittest.TestCase):
    """A behaviour cell with a fist is not handed a sword by the kit rule (combat__dig_in fought with one)."""

    def test_rows(self):
        from bonobo.bench import bench_bases
        fist = [f"combat__{b}" for b, (cell, _r, _w) in fight.BEHAVIOURS.items() if cell.get("weapon") == "fist"]
        self.assertTrue(fist, "at least one unarmed behaviour (dig_in)")
        for name in fist:
            with self.subTest(name):
                self.assertNotIn("sword", bench_bases.KIT.get(name, []))


class TheWindowOpensBeforeTheHooks(unittest.TestCase):
    """The row's answer mark is taken where the window opens, before its hooks: an answer a hook provokes counts."""

    def test_rows(self):
        from unittest import mock
        from bonobo import perception
        looks = []
        with mock.patch.object(perception, "looks_taken", lambda: len(looks)), \
                mock.patch.object(perception, "pause", lambda on: None):
            runner.open_window()
            looks.append("reshape (the hook woke the walker)")        # answered before the recording starts
            first = runner.take_row_mark()
            second = runner.take_row_mark()
        rows = [("the first recording takes the window's mark", first, 0),
                ("must fail: marked after the hook's answer", first == len(looks), False),
                ("later cells mark their own", second, None)]
        for name, got, want in rows:
            with self.subTest(name):
                self.assertEqual(got, want)


class MissingColumnsOnceEngaged(unittest.TestCase):
    def test_rows(self):
        cell = {"kit": "blocks", "blood": "whole"}
        start = {"rows": 1, "options": {"ignore": {}}}
        engaged = {"rows": 1, "options": {"ignore": {}, "reshape": {}, "wall_in": {}}}
        rows = [("engaged: all offered", start, engaged, []),
                ("must fail: read at the window start", start, None, ["reshape", "wall_in"]),
                ("engaged without the pod", start, {"rows": 1, "options": {"reshape": {}}}, ["wall_in"]),
                ("nothing in reach ever", {"rows": 0}, None, [])]
        for name, s0, eng, want in rows:
            with self.subTest(name):
                self.assertEqual(fight.missing_columns(cell, s0, eng), want)


class Escaped(unittest.TestCase):
    """An escape cell is judged on what the bot achieved — alive, health kept — whatever it chose."""

    def test_rows(self):
        def cell(hp, before=20.0, missing=("wall_in",)):
            return {"enemy": "walker", "outcome": {"hp": hp, "hp_before": before},
                    "intent": {"missing_column": list(missing)}}
        rows = [("alive, 1 hp lost, a column never offered: passes", [cell(19.0)], 0),
                ("must fail: died", [cell(0.0)], 1),
                ("alive but past the loss allowed", [cell(20.0 - fight.RESOLVE_HP_LOSS - 1)], 1),
                ("two cells, one lost", [cell(20.0), cell(0.0)], 1)]
        for name, cells, n_bad in rows:
            with self.subTest(name):
                self.assertEqual(len(fight.escaped(cells)), n_bad)


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



class TheReflexPolicy(unittest.TestCase):
    """fight_loop sets the jar's reflex policy (POST /reflex) and never posts a shield or a shielded attack: always
    shield and deflect once wired, counter-hits only while an engagement runs."""

    def test_rows(self):
        from unittest import mock
        from bonobo import api, fight_loop
        posted = []
        with mock.patch.object(api, "post", side_effect=lambda path, body=None: posted.append((path, body)) or {}):
            fight_loop.wire(None, lambda snap: None, {})
            fight_loop.reflex(counter=True)
        self.assertEqual(posted[0], ("/reflex", {"counter": False, "shield": True, "deflect": True,
                                                 "priority": "creeper"}))
        self.assertEqual(posted[1], ("/reflex", {"counter": True}))
        with self.subTest("must fail: a jar without /reflex: said (swallowed), never raised"), \
                mock.patch.object(api, "post", side_effect=api.McError("/reflex: 404")), \
                mock.patch.object(api, "swallowed") as said:
            fight_loop.reflex(counter=True)
            self.assertTrue(said.called)
        with self.subTest("no batch builds a shield (the jar's reflex is the one)"):
            self.assertNotIn("shield", fight_loop.BATCH)
            self.assertNotIn("fight_shielded", fight_loop.BATCH)


if __name__ == "__main__":
    unittest.main()

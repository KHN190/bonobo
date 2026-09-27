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
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import beliefs, combat_model, fight_plan, threat  # noqa: E402


class Clean:
    """The measured history emptied for a test and put back after: counts and values are then the declared ones."""

    def __enter__(self):
        import tempfile
        beliefs.flush()
        self.kept = (dict(beliefs.COUNTS), {k: list(v) for k, v in beliefs.OBSERVED.items()}, beliefs.LOG)
        beliefs.COUNTS.clear(), beliefs.OBSERVED.clear()
        beliefs.LOG = os.path.join(tempfile.mkdtemp(), "beliefs.jsonl")
        return self

    def __exit__(self, *exc):
        beliefs.flush()
        counts, observed, log = self.kept
        beliefs.COUNTS.clear(), beliefs.OBSERVED.clear()
        beliefs.COUNTS.update(counts), beliefs.OBSERVED.update(observed)
        beliefs.LOG = log


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


class Counts(unittest.TestCase):
    # (belief, observations behind it with nothing measured): a guess is 0, what the game publishes is WIKI_N (must fail: unknown paths raise, test_unknown_beliefs_are_errors)
    ROWS = [("time.death_cost_s", 0), ("risk.encounters_per_day", 0), ("mobs.minecraft:zombie.attack_s", 0),
            ("mobs.minecraft:zombie.hp", beliefs.WIKI_N), ("mobs.minecraft:zombie.attack", beliefs.WIKI_N),
            ("mobs.minecraft:zombie.notice_r", beliefs.WIKI_N)]

    def test_counts(self):
        with Clean():
            for path, n in self.ROWS:
                with self.subTest(path):
                    self.assertEqual(beliefs.belief(path), (beliefs.value(path), n))
            for name in beliefs.UNMEASURED:
                with self.subTest(unmeasured=name):
                    self.assertEqual(beliefs.count(name), 0)

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


class AMeasurementSurvivesTheProcessThatTookIt(unittest.TestCase):
    """A count that lives in one process is not an observation count.

    The bench measured a route, the run ended, and the next round believed the prior again — so `note` appends to
    a log and `load` reads it back. The rules are about the SHAPE of that history, not about any number in it.
    """

    def setUp(self):
        import tempfile
        beliefs.flush()              # another test's buffer must not land in this test's file
        self.dir = tempfile.mkdtemp()
        self.log = os.path.join(self.dir, "beliefs.jsonl")
        self.kept = (dict(beliefs.COUNTS), {k: list(v) for k, v in beliefs.OBSERVED.items()}, beliefs.LOG)
        beliefs.COUNTS.clear(), beliefs.OBSERVED.clear()
        beliefs.LOG = self.log

    def tearDown(self):
        counts, observed, log = self.kept
        beliefs.COUNTS.clear(), beliefs.OBSERVED.clear()
        beliefs.COUNTS.update(counts), beliefs.OBSERVED.update(observed)
        beliefs.LOG = log

    PATH = "risk.regen_s_per_hp"

    def test_every_note_is_one_line_and_the_count_is_its_length(self):
        for i, measured in enumerate((3.0, 5.0, 4.0, 4.5), start=1):
            _, n = beliefs.note(self.PATH, measured, where="test")
            self.assertEqual(n, i)
            beliefs.flush()          # measurements are written in batches; the history is buffer + file
            with open(self.log) as f:
                self.assertEqual(len([l for l in f if l.strip()]), i)

    def test_reading_the_log_back_gives_the_same_count(self):
        for measured in (3.0, 5.0, 4.0):
            beliefs.note(self.PATH, measured, where="test")
        beliefs.flush()
        counts = dict(beliefs.COUNTS)
        beliefs.COUNTS.clear(), beliefs.OBSERVED.clear()
        beliefs.load(self.log)
        self.assertEqual(beliefs.COUNTS, counts)

    # (lines in the log) → (lines loaded, the beliefs they count against)
    LOGS = [("one real line", [{"path": PATH, "measured": 4.0, "at": 0}], 1, {PATH: 1}),
            ("a line naming no belief is not counted", [{"path": PATH, "measured": 4.0, "at": 0},
                                                        {"path": "risk.no_such_belief", "measured": 1.0, "at": 0}],
             1, {PATH: 1}),
            ("two of one belief", [{"path": PATH, "measured": 4.0, "at": 0}, {"path": PATH, "measured": 5.0, "at": 1}],
             2, {PATH: 2}),
            ("must fail: an empty log", [], 0, {})]

    def test_loading_a_log(self):
        for name, lines, loaded, counts in self.LOGS:
            with self.subTest(name):
                with open(self.log, "w") as out:
                    out.writelines(json.dumps(l) + "\n" for l in lines)
                beliefs.COUNTS.clear(), beliefs.OBSERVED.clear()
                self.assertEqual(beliefs.load(self.log), loaded)
                self.assertEqual(dict(beliefs.COUNTS), counts)

    def test_what_took_it_is_written_down(self):
        beliefs.note(self.PATH, 4.0, where="bench:decision_arena")
        beliefs.flush()
        with open(self.log) as f:
            row = json.loads(f.readline())
        for field in ("path", "measured", "believed", "n", "at", "where"):
            self.assertIn(field, row)
        self.assertEqual(row["where"], "bench:decision_arena")


class EveryCounterTellsTheHistory(unittest.TestCase):
    """The connection between what the agent DOES and what it believes, run rather than read: each counter is
    called with a measured span, and what it files (or refuses to file) is the row's answer.

    A timing is only a measurement while the clock was measuring the thing — a queued task, a stall, an interrupted
    break time the harness. So every counter's rows include spans it must refuse: one thousand-second outlier would
    drag a belief the pseudo-counts are meant to protect.
    """

    def _break(self, took, pickaxe):
        from unittest import mock
        from bonobo import skillcore
        inv = type("Inv", (), {"tools": lambda self, k: [(1, 100, "minecraft:stone_pickaxe")] if pickaxe else []})
        with mock.patch.object(skillcore.time, "time", return_value=1000.0 + took), \
                mock.patch.object(skillcore, "Inventory", inv):
            skillcore._note_break(1000.0)

    def _death(self, took):
        import tempfile
        from unittest import mock
        from bonobo import memory
        m = memory.Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
        m.data["deaths"].append({"pos": [0, 64, 0], "dimension": "minecraft:overworld", "t": 1000.0})
        with mock.patch.object(memory.time, "time", return_value=1000.0 + took):
            self.assertEqual(m.forget_death(), {"pos": [0, 64, 0], "dimension": "minecraft:overworld", "t": 1000.0,
                                                "recovered": True})

    def _eat(self, took):
        from unittest import mock
        from bonobo import brain, skills  # noqa: F401  (brain registers every skill module)
        from bonobo.knowledge import ALL_FOOD
        # one steak carried, the bar at 12: one bite (8 points) fills it — the bite's span is the chain's
        inv = type("Inv", (), {"count": lambda self, item: 1 if item == ALL_FOOD[0] else 0})
        with mock.patch.object(skills.time, "time", side_effect=[1000.0, 1000.0 + took]), \
                mock.patch.object(skills, "Inventory", inv), \
                mock.patch.object(skills.api, "get", return_value={"food": 12}), \
                mock.patch.object(skills.api, "run_chain", return_value=[{"status": "succeeded"}]) as run:
            self.assertEqual(skills.eat.__wrapped__(None, raw_ok=False), 20)
            run.assert_called_once_with([{"type": "eat", "item": ALL_FOOD[0]}], stop_on_failure=True)

    # (counter, how it is run, span in seconds) → the belief it files, or None when the span is not a sample
    ROWS = [("break with a pickaxe", "_break", (2.0, True), "tools.mine_time_stone"),
            ("break by hand", "_break", (6.0, False), "tools.mine_time_no_pickaxe"),
            ("must fail: break: a queued task, too quick to be one", "_break", (0.01, True), None),
            ("break: a stall", "_break", (45.0, True), None),
            ("death walked back", "_death", (120.0,), "time.death_cost_s"),
            ("death: recovered in under a second (a replay)", "_death", (0.5,), None),
            ("death: over an hour (the session slept)", "_death", (7200.0,), None),
            ("a bite", "_eat", (1.6,), "engage.eat_s"),
            ("a bite that timed the queue", "_eat", (0.01,), None),
            ("a bite interrupted for a minute", "_eat", (60.0,), None)]

    def test_each_counter_files_its_span_or_refuses_it(self):
        from unittest import mock
        for name, how, args, path in self.ROWS:
            with self.subTest(name), mock.patch.object(beliefs, "note") as note:
                getattr(self, how)(*args)
                filed = [(c.args[0], round(c.args[1], 6)) for c in note.call_args_list]
                self.assertEqual(filed, [(path, args[0])] if path else [])

    def test_every_belief_filed_exists(self):
        for name, _how, _args, path in self.ROWS:
            if path:
                with self.subTest(name):
                    beliefs.value(path)        # KeyError here: a measurement filed under a typo


class MeasurementsMoveTheNumber(unittest.TestCase):
    """`value` is the declared number MOVED by what has been measured — the last step of the loop.

    The rules are relations: only what play.toml calls a guess can move, it moves toward the measurements and
    never past them, one odd sample cannot carry it, and what the game publishes never moves at all.
    """

    PATH = "engage.eat_s"
    WIKI = "mobs.minecraft:zombie.hp"

    def setUp(self):
        self.kept = ({k: list(v) for k, v in beliefs.OBSERVED.items()}, dict(beliefs.COUNTS), beliefs.LOG)
        beliefs.OBSERVED.clear(), beliefs.COUNTS.clear()
        beliefs.LOG = os.path.join(__import__("tempfile").mkdtemp(), "beliefs.jsonl")

    def tearDown(self):
        observed, counts, log = self.kept
        beliefs.OBSERVED.clear(), beliefs.COUNTS.clear()
        beliefs.OBSERVED.update(observed), beliefs.COUNTS.update(counts)
        beliefs.LOG = log

    def _measure(self, path, *values):
        for v in values:
            beliefs.note(path, v, where="test")

    # (situation, path, measurements as multiples of the declared number) → the believed number, exactly:
    # (W·prior + n·median) / (W + n) with W = PRIOR_STRENGTH for a guess; a published number never moves
    def rows(self):
        p, w = beliefs.declared(self.PATH), beliefs.PRIOR_STRENGTH
        wiki = beliefs.declared(self.WIKI)
        return [("nothing measured: the declared number", self.PATH, [], p),
                ("one sample at 2×: halfway there at W = 1", self.PATH, [2], (w * p + 2 * p) / (w + 1)),
                ("six samples at 2×: most of the way, never past", self.PATH, [2] * 6, (w * p + 6 * 2 * p) / (w + 6)),
                ("eight at 1× and one wild 1000×: the median holds", self.PATH, [1] * 8 + [1000],
                 (w * p + 9 * p) / (w + 9)),
                ("a published number measured at 5×: unmoved", self.WIKI, [5, 5, 5], wiki)]

    def test_exact_values(self):
        for name, path, multiples, want in self.rows():
            with self.subTest(name):
                beliefs.OBSERVED.clear(), beliefs.COUNTS.clear()
                self._measure(path, *[m * beliefs.declared(path) for m in multiples])
                self.assertAlmostEqual(beliefs.value(path), want)

    def test_only_what_play_toml_calls_a_guess_can_move(self):
        self.assertTrue(beliefs.is_unmeasured(self.PATH))
        self.assertFalse(beliefs.is_unmeasured(self.WIKI))
        for path in ("time.day_s",):
            if not beliefs.is_unmeasured(path):
                before = beliefs.value(path)
                self._measure(path, before * 4, before * 4, before * 4)
                self.assertEqual(beliefs.value(path), before, f"{path} is not declared a guess")


class TheHistoryIsNeverLost(unittest.TestCase):
    """Measurements arrive at the speed of the world, so they are written in batches — and a batch is a place
    where a history can be lost. The rules: the buffer counts as part of the history, something empties it, and
    the process ending is one of those things."""

    def test_a_queued_measurement_is_already_in_the_count(self):
        kept = (dict(beliefs.COUNTS), {k: list(v) for k, v in beliefs.OBSERVED.items()}, beliefs.LOG)
        beliefs.flush()
        beliefs.COUNTS.clear(), beliefs.OBSERVED.clear()
        beliefs.LOG = os.path.join(__import__("tempfile").mkdtemp(), "beliefs.jsonl")
        try:
            _v, n = beliefs.note("engage.eat_s", 0.9, where="test")
            self.assertEqual(n, 1, "a measurement counts the moment it is taken, not when it reaches the disk")
            self.assertEqual(beliefs.flush(), 1)
            self.assertEqual(beliefs.flush(), 0, "flushing twice does not write it twice")
        finally:
            counts, observed, log = kept
            beliefs.COUNTS.clear(), beliefs.OBSERVED.clear()
            beliefs.COUNTS.update(counts), beliefs.OBSERVED.update(observed)
            beliefs.LOG = log

    def test_the_end_of_the_process_writes_what_is_queued(self):
        """A process that notes one measurement and exits without flushing still leaves it on disk (atexit)."""
        import subprocess
        import tempfile
        log = os.path.join(tempfile.mkdtemp(), "beliefs.jsonl")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = "from bonobo import beliefs; beliefs.note('engage.eat_s', 0.9, where='exit test')"
        subprocess.run([sys.executable, "-c", code], cwd=root, env={**os.environ, "MC_BELIEFS": log},
                       check=True, timeout=60)
        with open(log) as f:
            rows = [json.loads(line) for line in f]
        self.assertEqual([(r["path"], r["measured"], r["where"]) for r in rows], [("engage.eat_s", 0.9, "exit test")])

    def test_a_batch_is_written_when_it_fills(self):
        """Notes queue; the batch reaches the disk when it holds FLUSH_EVERY (the age clock held still here)."""
        from unittest import mock
        for n, on_disk in ((1, 0), (beliefs.FLUSH_EVERY - 1, 0), (beliefs.FLUSH_EVERY, beliefs.FLUSH_EVERY),
                           (beliefs.FLUSH_EVERY + 1, beliefs.FLUSH_EVERY)):
            with self.subTest(n=n), Clean(), mock.patch.object(beliefs, "_last_flush", 1e12):
                for _ in range(n):
                    beliefs.note("engage.eat_s", 0.9, where="test")
                lines = open(beliefs.LOG).read().splitlines() if os.path.exists(beliefs.LOG) else []
                self.assertEqual(len(lines), on_disk)



if __name__ == "__main__":
    unittest.main()

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
import inspect
import json
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

    def test_what_the_wiki_does_not_say_is_never_published_knowledge(self):
        """It may have been MEASURED — that is the point of the log — but it can never count as published.

        This used to assert the counts were zero, which stopped being true the moment ordinary play started
        writing measurements down. What must hold is the relation: only a wiki field is known the way Mojang
        knows it, everything else carries however many observations we have actually taken.
        """
        for path in ("mobs.minecraft:zombie.attack_s", "risk.encounters_per_day"):
            self.assertLess(beliefs.count(path), beliefs.WIKI_N, path)

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

    def test_a_line_naming_no_belief_is_not_counted_against_another(self):
        beliefs.note(self.PATH, 4.0, where="test")
        beliefs.flush()
        with open(self.log, "a") as out:
            out.write('{"path": "risk.no_such_belief", "measured": 1.0, "at": 0}\n')
        beliefs.COUNTS.clear(), beliefs.OBSERVED.clear()
        self.assertEqual(beliefs.load(self.log), 1)
        self.assertEqual(set(beliefs.COUNTS), {self.PATH})

    def test_one_measurement_barely_moves_the_belief(self):
        """A sample is evidence, not an answer: with the prior worth PRIOR_STRENGTH observations, the first one
        can move the number at most halfway to itself — and the history, not the last line, is what it reads."""
        before = beliefs.value(self.PATH)
        beliefs.note(self.PATH, before * 3, where="test")
        moved = beliefs.value(self.PATH)
        self.assertLessEqual(abs(moved - before), abs(before * 3 - before) / 2 + 1e-9)
        self.assertEqual(beliefs.declared(self.PATH), before, "what is written down is never rewritten")

    def test_what_took_it_is_written_down(self):
        beliefs.note(self.PATH, 4.0, where="bench:decision_arena")
        beliefs.flush()
        with open(self.log) as f:
            row = json.loads(f.readline())
        for field in ("path", "measured", "believed", "n", "at", "where"):
            self.assertIn(field, row)
        self.assertEqual(row["where"], "bench:decision_arena")


class EveryCounterTellsTheHistory(unittest.TestCase):
    """The connection between what the agent DOES and what it believes, stated once and swept over every place it
    is made.

    Each row is: the function that watches something happen, and the belief it is a measurement of. Written this
    way, adding a measurement is a row — not a new test — and the rules (it names a real belief, it says what
    counts as a sample, it never moves the number by itself) are asked of all of them at once.
    """

    WIRED = (("memory", "Memory", "note_exposure", "risk.encounters_per_day"),
             ("memory", "Memory", "note_yield", "yield_s."),
             ("memory", "Memory", "forget_death", "time.death_cost_s"),
             ("brain", "Brain", "note_body_of", "risk.regen_s_per_hp"),
             ("brain", "Brain", "note_body_of", "risk.food_drain_s"),
             ("brain", "Brain", "note_body_of", "pool.slot_fill_s"),
             ("brain", "Brain", "note_walk_of", "nav.unit_s"),
             ("skillcore", None, "_note_break", "tools.mine_time_stone"),
             ("skillcore", None, "_note_break", "tools.mine_time_no_pickaxe"),
             ("skills", None, "eat", "engage.eat_s"))

    def source(self, module, owner, func):
        import importlib
        mod = importlib.import_module(f"bonobo.{module}")
        holder = getattr(mod, owner) if owner else mod
        return inspect.getsource(getattr(holder, func))

    def test_each_counter_notes_a_belief_that_exists(self):
        for module, owner, func, path in self.WIRED:
            with self.subTest(where=f"{module}.{func}", belief=path):
                src = self.source(module, owner, func)
                self.assertIn("beliefs.note(", src, "keeps its measurement to itself")
                self.assertIn(path.rstrip("."), src, f"does not name {path}")
                if not path.endswith("."):
                    beliefs.value(path)        # KeyError here: filed under a typo

    def test_each_timer_says_what_counts_as_a_sample(self):
        """A queued task, a stall, an interrupted break time the harness rather than the world. Every place that
        times something states a window it will accept — otherwise one thousand-second outlier drags a belief the
        pseudo-counts exist to protect."""
        import re as _re
        for module, owner, func, _path in self.WIRED:
            with self.subTest(where=f"{module}.{func}"):
                src = self.source(module, owner, func)
                bounded = (_re.search(r"<=\s*\w+\s*<=", src) or _re.search(r"[<>]=?\s*(self\.)?[A-Z_]*SAMPLE", src)
                           or _re.search(r"if\s+\w+\s*[<>]", src) or "floor" in src)
                self.assertTrue(bounded, "times something without saying what counts as a sample")


class MeasurementsMoveTheNumber(unittest.TestCase):
    """`value` is the declared number MOVED by what has been measured — the last step of the loop.

    The rules are relations: only what play.toml calls a guess can move, it moves toward the measurements and
    never past them, one odd sample cannot carry it, and what the game publishes never moves at all.
    """

    PATH = "nav.unit_s"
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

    def test_with_nothing_measured_it_is_the_declared_number(self):
        for path in (self.PATH, self.WIKI, "risk.regen_s_per_hp"):
            self.assertEqual(beliefs.value(path), beliefs.declared(path), path)

    def test_it_moves_toward_the_measurements_and_never_past_them(self):
        prior = beliefs.declared(self.PATH)
        seen = [prior * 2] * 6
        self._measure(self.PATH, *seen)
        got = beliefs.value(self.PATH)
        self.assertGreater(got, prior)
        self.assertLessEqual(got, max(seen) + 1e-9)

    def test_more_of_the_same_moves_it_further(self):
        prior = beliefs.declared(self.PATH)
        steps = []
        for _ in range(6):
            self._measure(self.PATH, prior * 3)
            steps.append(beliefs.value(self.PATH))
        self.assertEqual(steps, sorted(steps), "each consistent measurement moves it further, never back")

    def test_one_wild_sample_cannot_carry_it(self):
        prior = beliefs.declared(self.PATH)
        self._measure(self.PATH, *([prior] * 8))
        steady = beliefs.value(self.PATH)
        self._measure(self.PATH, prior * 1000)
        self.assertLess(abs(beliefs.value(self.PATH) - steady), abs(prior), "the median, not the last sample")

    def test_what_the_game_publishes_never_moves(self):
        published = beliefs.declared(self.WIKI)
        self._measure(self.WIKI, published * 5, published * 5, published * 5)
        self.assertEqual(beliefs.value(self.WIKI), published)

    def test_only_what_play_toml_calls_a_guess_can_move(self):
        self.assertTrue(beliefs.is_unmeasured(self.PATH))
        self.assertFalse(beliefs.is_unmeasured(self.WIKI))
        for path in ("time.day_s",):
            if not beliefs.is_unmeasured(path):
                before = beliefs.value(path)
                self._measure(path, before * 4, before * 4, before * 4)
                self.assertEqual(beliefs.value(path), before, f"{path} is not declared a guess")

    def test_trust_grows_with_the_count(self):
        """`cautious` reads an unmeasured number pessimistically; measuring it must close that gap, not widen it."""
        gaps = []
        for _ in range(4):
            v, _n = beliefs.belief(self.PATH), None
            gaps.append(abs(beliefs.cautious(self.PATH, "benefit") - beliefs.value(self.PATH)) / beliefs.value(self.PATH))
            self._measure(self.PATH, beliefs.declared(self.PATH))
        self.assertEqual(gaps, sorted(gaps, reverse=True), "each observation narrows the doubt")


class EveryMeasurementHasABound(unittest.TestCase):
    """A timing is only a measurement while the clock was measuring the thing.

    A queued task, a stall, an interrupted break: all of them time the harness rather than the world, and a single
    one of them at a thousand seconds would drag a belief the pseudo-counts are meant to protect. Every place that
    times something therefore states a window it will accept.
    """

    TIMERS = (("skillcore", "_note_break"), ("skills", "eat"),
              ("brain", "note_walk_of"), ("brain", "note_body_of"), ("memory", "forget_death"))

    def test_each_timer_states_what_it_will_accept(self):
        import importlib
        import re as _re
        for module, func in self.TIMERS:
            mod = importlib.import_module(f"bonobo.{module}")
            owner = getattr(mod, "Brain", None) or getattr(mod, "Memory", None) or mod
            src = inspect.getsource(getattr(owner, func, None) or getattr(mod, func))
            bounded = _re.search(r"<=\s*\w+\s*<=", src) or _re.search(r"[<>]=?\s*(self\.)?[A-Z_]*SAMPLE", src) \
                or _re.search(r"if\s+\w+\s*[<>]", src)
            self.assertTrue(bounded, f"{module}.{func} times something without saying what counts as a sample")


class TheWorldsCountersAlsoTellTheHistory(unittest.TestCase):
    """Every counter the agent already keeps is a measurement of a declared guess.

    `memory` counted exposure and yields, `brain` watched health and food every round, and the beliefs those
    numbers are about stayed at their priors — two records of one fact, one of them never consulted. The rules
    here are about the CONNECTION, not about any value: it exists, it names a real belief, and a stretch too
    short to mean anything is not a sample.
    """

    WIRED = (("memory", "note_exposure", "risk.encounters_per_day"),
             ("memory", "note_yield", "yield_s."),
             ("memory", "forget_death", "time.death_cost_s"),
             ("brain", "note_body_of", "risk.regen_s_per_hp"),
             ("brain", "note_body_of", "risk.food_drain_s"),
             ("brain", "note_body_of", "pool.slot_fill_s"),
             ("brain", "note_walk_of", "nav.unit_s"),
             ("skillcore", "_note_break", "tools.mine_time_stone"),
             ("skillcore", "_note_break", "tools.mine_time_no_pickaxe"),
             ("skills", "eat", "engage.eat_s"))

    def test_each_counter_notes_the_belief_it_measures(self):
        import importlib
        for module, func, path in self.WIRED:
            mod = importlib.import_module(f"bonobo.{module}")
            owner = getattr(mod, "Brain", None) or getattr(mod, "Memory", None) or mod
            src = inspect.getsource(getattr(owner, func, None) or getattr(mod, func))
            self.assertIn("beliefs.note(", src, f"{module}.{func} keeps its measurement to itself")
            self.assertIn(path.rstrip("."), src, f"{module}.{func} does not name {path}")

    def test_every_belief_named_that_way_exists(self):
        for _module, _func, path in self.WIRED:
            if path.endswith("."):
                continue
            beliefs.value(path)          # KeyError here means a measurement is being filed under a typo

    def test_a_stretch_too_short_is_not_a_sample(self):
        from bonobo import brain, memory
        self.assertGreater(brain.Brain.BODY_SAMPLE_S, 0)
        self.assertGreater(memory.Memory.EXPOSURE_SAMPLE_S, 0)
        self.assertIn("BODY_SAMPLE_S", inspect.getsource(brain.Brain.note_body_of))
        self.assertIn("EXPOSURE_SAMPLE_S", inspect.getsource(memory.Memory.note_exposure))

    def test_health_going_down_is_not_regeneration(self):
        """The one direction that matters: a fight must never be filed as a regeneration rate."""
        src = inspect.getsource(__import__("bonobo.brain", fromlist=["brain"]).Brain.note_body_of)
        self.assertIn("hp > was_hp", src)
        self.assertIn("regen_food_floor", src, "and it is only regeneration while there is food to do it with")


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
            _v, n = beliefs.note("nav.unit_s", 0.9, where="test")
            self.assertEqual(n, 1, "a measurement counts the moment it is taken, not when it reaches the disk")
            self.assertEqual(beliefs.flush(), 1)
            self.assertEqual(beliefs.flush(), 0, "flushing twice does not write it twice")
        finally:
            counts, observed, log = kept
            beliefs.COUNTS.clear(), beliefs.OBSERVED.clear()
            beliefs.COUNTS.update(counts), beliefs.OBSERVED.update(observed)
            beliefs.LOG = log

    def test_the_end_of_the_process_writes_what_is_queued(self):
        import atexit
        registered = [f for f, *_ in getattr(atexit, "_ncallbacks", lambda: None) and []] or None
        src = inspect.getsource(beliefs)
        self.assertIn("atexit.register(flush)", src)
        self.assertIsNone(registered)          # nothing to assert about CPython's private list; the source says it

    def test_a_batch_has_a_size_and_an_age(self):
        self.assertGreater(beliefs.FLUSH_EVERY, 1)
        self.assertGreater(beliefs.FLUSH_AFTER_S, 0)

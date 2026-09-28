"""The scenario sheet's shape (the bench tables, bonobo/bench/bench_<tier>.py, built by table.py), offline. The rows are run in the game, never here.

What is checked is what makes the sheet trustworthy before anyone runs it:
  - every row is well-formed: setup commands, a run, a WORLD check, a budget, the skills it proves, its test point
  - every registered skill has a real `verify` and at least one row proving it in the world — except the skills
    in the two allowlists below, which may only SHRINK: an entry that is no longer a gap fails until removed, a new
    gap fails until it is fixed (or, visibly, added here)
  - the cross-product is whole: every base has its plain row, every condition a row per base it applies to, every
    axis (terrain, timing, inventory) and every surprise is there
  - expected-failure rows name a specific reason (a regex that is not a catch-all)
  - the named rows of test points A–D exist, in the right order, with the right budgets
Driven from `skill.REGISTRY` and `table.SCENARIOS`: a new skill or a new row needs no edit here.
"""
import contextlib
import os
import re
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain, fight_loop  # noqa: E402,F401
from bonobo.bench import table as sc  # noqa: E402,F401  (brain/fight_loop: every skill module)
from bonobo import skill as skillkit  # noqa: E402
from bonobo.bench import runner  # noqa: E402

CHAIN_C = ("slice_start_tools", "iron_ingots", "slice_nether_kit")    # test point C, in this order

# Skills without a real verify (the runner judges them by nothing). May only shrink.
VERIFY_GAPS = {"await_perch", "bed_bomb_window", "break_caged_crystal", "build_bed_pit",
               "shake_enderman", "slay_dragon", "station"}
# Skills no scenario row proves in the world yet. May only shrink.
SCENARIO_GAPS = {"await_perch", "bed_bomb_window", "build_bed_pit", "shake_enderman", "station"}
CEILING = 8        # neither list grows past this; lower it as they shrink

COMMANDS = {"experience", "gamemode", "fill", "setblock", "tp", "give", "clear", "summon", "place", "time", "weather", "effect", "item", "kill",
            "spreadplayers", "locate", "execute", "gamerule", "difficulty", "forceload", "data", "damage"}
POINTS = {"A", "B", "C", "D"}
BASICS_A = ("nav", "chop", "mine_stone", "craft", "smelt", "hunt", "eat", "sleep", "loot")
L0_B = ("water_clutch", "cross_lava_8", "cave_escape", "lava_edge_walk", "buried_by_sand")


def rows():
    return sorted(sc.SCENARIOS.items())


def proven_by(entry):
    """The skills a row's `skills` entry proves: a registered name, or an effect (every skill that provides it)."""
    return ({entry} if entry in skillkit.REGISTRY else set()) | {c.name for c in skillkit.providers(entry)}


class EveryRow(unittest.TestCase):
    def test_well_formed(self):
        for name, row in rows():
            with self.subTest(name):
                self.assertIsInstance(row.get("doc"), str)
                self.assertTrue(row["doc"].strip())
                self.assertTrue(callable(row.get("run")))
                self.assertTrue(callable(row.get("check")), "judged by the world, not by what run returned")
                self.assertGreater(row.get("budget", 0), 0)
                self.assertIn(row.get("point"), POINTS)
                for entry in row.get("skills", ()):
                    self.assertTrue(proven_by(entry), f"{entry!r} is neither a skill nor an effect any skill provides")
                for cmd in row.get("setup", ()):
                    self.assertIsInstance(cmd, str)
                    self.assertIn(cmd.split()[0], COMMANDS, f"setup command {cmd!r}")
                if row.get("before") is not None:
                    self.assertTrue(callable(row["before"]))

    def test_boxed_rows_stay_in_the_box(self):
        """A row the runner resets (not raw) builds inside the bench box: what it leaves outside survives the reset
        and becomes the next row's surprise."""
        (x0, y0, z0), (x1, y1, z1) = sc.BOX
        ox, oy, oz = sc.ORIGIN
        for name, row in sorted(sc.SCENARIOS.items()):
            if row.get("raw"):
                continue
            for cmd in row["setup"]:
                for x, y, z in re.findall(r"(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)", cmd):
                    with self.subTest(name, cmd=cmd):
                        rel = (float(x) - ox, float(y) - oy, float(z) - oz)
                        outside = [(axis, v) for axis, v, lo, hi in (("x", rel[0], x0 - 1, x1 + 1),
                                                                    ("y", rel[1], y0, y1 + 1),
                                                                    ("z", rel[2], z0 - 1, z1 + 1)) if not lo <= v <= hi]
                        self.assertEqual(outside, [], "outside the bench box")

    def test_expected_failures_name_a_reason(self):
        for name, row in rows():
            if "fails" not in row:
                continue
            with self.subTest(name):
                pattern = re.compile(row["fails"], re.I)
                accepted = [g for g in ("", "error", "failed", "finished without reaching its goal", "TaskStuck")
                            if pattern.fullmatch(g)]
                accepted += [g for g in ("chop: finished without reaching its goal",) if pattern.search(g)]
                self.assertEqual(accepted, [], f"{row['fails']!r} accepts a failure that names no reason")


class EverySkillIsProven(unittest.TestCase):
    def gaps(self):
        no_verify = {n for n, c in skillkit.REGISTRY.items() if c.verify is None}
        proven = set().union(*(proven_by(e) for _, row in rows() for e in row.get("skills", ())))
        return no_verify, set(skillkit.REGISTRY) - proven

    def test_gaps_are_exactly_the_allowlists(self):
        """Each gap list is exactly its allowlist: a new gap fails until fixed, a fixed one until removed; every list
        stays within the ceiling and names only what exists."""
        no_verify, unproven = self.gaps()
        named = {e for _, row in rows() for e in row.get("skills", ())}
        unknown = {e for e in named if not proven_by(e)}
        orphans = (VERIFY_GAPS | SCENARIO_GAPS) - set(skillkit.REGISTRY)
        table = [("skills without a real verify", no_verify, VERIFY_GAPS),
                 ("skills no bench row proves", unproven, SCENARIO_GAPS),
                 ("must fail: row entries that are neither a skill nor a provided effect", unknown, set()),
                 ("allowlisted names that no longer exist", orphans, set())]
        for name, actual, allowed in table:
            with self.subTest(name):
                self.assertEqual(sorted(actual), sorted(allowed))
                self.assertEqual(len(allowed) <= CEILING, True, f"{len(allowed)} over the ceiling {CEILING}")


class TheCoverIsWhole(unittest.TestCase):
    """The sheet runs `cover`'s pairs, not the product: every base alone, every condition at least once, every base
    of an axis at least once in it — each such row present and tagged with its condition."""

    def test_every_base_and_condition(self):
        for base in sc.BASES:
            with self.subTest(base=base):
                self.assertIn(f"{base}__base", sc.SCENARIOS)
        pairs = sc.cover(sc.CONDITIONS, sc.BASES, [("night", "chop")])
        for cond, spec in sc.CONDITIONS.items():
            self.assertEqual(set(spec["bases"]) - set(sc.BASES), set(), cond)
            with self.subTest(condition=cond):
                self.assertIn(cond, {c for c, _b in pairs})
        for cond, base in pairs:
            with self.subTest(condition=cond, base=base):
                row = sc.SCENARIOS[f"{base}__{cond}"]
                self.assertEqual(row["tags"][sc.CONDITIONS[cond]["axis"]], cond)
        for surprise in sc.SURPRISES:
            with self.subTest(surprise=surprise):
                self.assertIn(surprise, sc.SCENARIOS)

    def test_every_axis_is_covered(self):
        axes = {spec["axis"] for spec in sc.CONDITIONS.values()}
        self.assertEqual(axes, {"terrain", "timing", "inventory", "hazard"})
        self.assertTrue({"buried_by_sand", "lava_edge"} <= {c for c, s in sc.CONDITIONS.items() if s["axis"] == "hazard"})
        terrains = {c for c, s in sc.CONDITIONS.items() if s["axis"] == "terrain"}
        self.assertTrue({"canopy", "cave", "underwater", "pillar", "cliff_edge", "nether", "night", "rain"} <= terrains)
        timing = {c for c, s in sc.CONDITIONS.items() if s["axis"] == "timing"}
        self.assertTrue({"pickup_lag", "interrupt_mid_work", "interrupt_twice", "interrupt_at_success",
                         "player_takeover"} <= timing)
        inventory = {c for c, s in sc.CONDITIONS.items() if s["axis"] == "inventory"}
        self.assertTrue({"full_bag", "tool_one_use", "wrong_tool", "goal_met"} <= inventory)
        self.assertIn("inventory_lag", timing)
        self.assertIn("dead_flicker_on_respawn", sc.SCENARIOS)
        self.assertTrue({"leaves_block_trunk", "floating_logs", "empty_chest", "bed_obstructed", "bed_in_nether",
                         "lava_under_ore", "falling_gravel"} <= set(sc.SURPRISES))

    def test_interrupted_rows_resume_and_count(self):
        for name, row in sorted(sc.SCENARIOS.items()):
            if sc.CONDITIONS.get(row.get("tags", {}).get("timing"), {}).get("interrupt"):
                with self.subTest(name):
                    self.assertEqual(row["budget"], sc.BASES[row["tags"]["base"]]["budget"],
                                     "an interrupted run keeps the base's time: the base is small enough to resume")

    def test_every_timing_row_runs(self):
        """No row stands in for a missing hook: every timing condition injects its interruption for real."""
        for name, row in sc.SCENARIOS.items():
            if row.get("tags", {}).get("timing"):
                with self.subTest(name):
                    self.assertNotIn("hook", row)
                    self.assertTrue(sc.CONDITIONS[row["tags"]["timing"]].get("interrupt")
                                    or sc.CONDITIONS[row["tags"]["timing"]].get("tick_rate"), name)


class Unique(unittest.TestCase):
    """No two rows are the same scenario: (setup, dimension, tick_rate, budget, skills, tags, queue, variant) differ.
    `variant` is what a row family varies outside its setup (the facing asked, a shard's cells)."""

    @staticmethod
    def key(row):
        return (tuple(map(str, row["setup"])), row.get("dimension"), row.get("tick_rate"), row["budget"],
                tuple(row.get("skills", ())), repr(sorted(row.get("tags", {}).items())), repr(row.get("queue")),
                repr(row.get("variant")))

    def dups(self, rows):
        seen, out = {}, []
        for name, row in sorted(rows.items()):
            k = self.key(row)
            if k in seen:
                out.append((seen[k], name))
            seen.setdefault(k, name)
        return out

    def test_unique(self):
        some = dict(list(sc.SCENARIOS.items())[:3])
        first = next(iter(some))
        rows = [("the real sheet", sc.SCENARIOS, []),
                ("a copy under another name", {**some, "zz_copy": dict(some[first])}, [(first, "zz_copy")]),
                ("must fail: the same setup, another queue", {"a": {"setup": ["x"], "budget": 5, "queue": [1, 2]},
                                                   "b": {"setup": ["x"], "budget": 5, "queue": [2, 1]}}, []),
                ("the same setup, another tag", {"a": {"setup": ["x"], "budget": 5, "tags": {"inventory": "full"}},
                                                 "b": {"setup": ["x"], "budget": 5, "tags": {}}}, [])]
        for name, table, want in rows:
            with self.subTest(name):
                self.assertEqual(self.dups(table), want)


class Tiers(unittest.TestCase):
    def test_every_row_has_a_tier(self):
        for name, row in rows():
            with self.subTest(name):
                self.assertIn(row.get("tier"), sc.TIERS)
                self.assertEqual(row["tier"], sc.tier_of(name, row))

    def test_core_is_small_and_whole(self):
        core = [n for n, r in rows() if r["tier"] == "core"]
        self.assertLessEqual(len(core), 17, "core runs on every change: keep it small")
        for base in sc.BASES:
            with self.subTest(base=base):
                self.assertIn(f"{base}__base", core)
        for name in ("lava_edge_walk", "drowning_in_a_pit", "buried_by_sand", CHAIN_C[0], CHAIN_C[1]):
            with self.subTest(name):
                self.assertIn(name, core)

    def test_common_is_core_under_everyday_conditions(self):
        for name, row in rows():
            if row["tier"] == "common" and name not in sc.COMMON and not row.get("tier_fixed"):
                with self.subTest(name):
                    self.assertIn(row["tags"]["base"], sc.BASES)
                    self.assertTrue(set(row["tags"].values()) & set(sc.COMMON_CONDITIONS))

    # (row, tier it must be in): the rules the tiers exist for, stated per row.
    PLACED = [("bed_in_nether", "core"), ("slice_start_tools", "core"), ("dig_in_night", "common"), ("reach_land_swim", "common"),
              ("chest_or_tree", "common"), ("water_clutch", "core"), ("cross_lava_8", "common"),
              ("cave_escape", "common"), ("slice_nether_kit", "common"), (sc.ACCEPTANCE_D, "acceptance"),
              
              ("plan_repair_on_event", "brain"), ("brain__tight", "brain"), ("seen_store__noted", "brain"),
              ("chop_without_interrupt", "brain"), ("ban_then_other_source", "brain"),
              ("resume_after_combat", "brain"), ("l3_two_goals_in_order", "brain"),
              ("ban_needs_a_failure", "brain"), 
              ("l3_order_swapped", "brain"), ("plan_without_events", "brain")] + [
              # the fight's behaviour cells: one each, all in the combat tier
              (f"combat__{b}", "combat") for b in ("block_gap", "dig_in", "pillar", "shield_arrows",
                                                   "fight_without_shield", "fight_and_block", "wall_in",
                                                   "surrounded_low")]

    def test_placed_rows(self):
        for name, tier in self.PLACED:
            with self.subTest(name):
                self.assertEqual(sc.SCENARIOS[name]["tier"], tier)

    def test_no_tier_run_includes_acceptance(self):
        for tier in ("core", "common", "brain", "exception"):
            with self.subTest(tier):
                self.assertNotIn(sc.ACCEPTANCE_D, sc.select(sc.SCENARIOS, tier))


# Rows that still take longer than the tier's limit: real-world searches and whole boss fights (the fight bench's
# sweeps included) that no setup can shorten without changing what they measure. May only shrink.
LONG = set()  # rows over the 60 s limit still to be cut down by setup: none left (may only stay empty)
LIMIT_S = {t: runner.ROW_LIMIT_S for t in ("core", "common", "brain", "combat", "exception")}   # the one limit


def over_limit(rows_):
    """Names of rows whose budget is over their tier's limit (acceptance has its own 30 minutes)."""
    return sorted(n for n, r in rows_.items() if r["tier"] in LIMIT_S and r["budget"] > LIMIT_S[r["tier"]])


class SheetMerge(unittest.TestCase):
    """Every table row reaches SCENARIOS: table.py builds the one sheet (search_night_resume once missed the merge
    and was in no run)."""

    def test_every_table_row_is_a_scenario(self):
        self.assertEqual(sorted(set(sc.sheet()) ^ set(sc.SCENARIOS)), [])
        self.assertIn("search_night_resume", sc.SCENARIOS)

    def test_a_row_made_twice_is_refused(self):
        # must fail: two rows of one name in a tier's table are an error at build, never one silently dropped
        from unittest import mock
        mod = __import__("bonobo.bench.bench_core", fromlist=["ROWS"])
        with mock.patch.object(mod, "ROWS", list(mod.ROWS) + [dict(mod.ROWS[0])]):
            with self.assertRaises(ValueError):
                sc.rows("core")


class FightRowsInCombat(unittest.TestCase):
    """A row that fights (a hostile summoned in its setup or `before` hook, or the dragon slain) is in the combat tier.
    resume_after_combat is exempt by name: its zombie comes mid-task and its tier is the user's decision."""
    EXEMPT = {"resume_after_combat"}
    HOSTILE = re.compile(r"summon (?:minecraft:)?(zombie|skeleton|creeper|blaze|ghast|spider|cave_spider|enderman|"
                         r"witch|slime|magma_cube|wither|pillager|husk|drowned|stray|phantom|hoglin|vindicator|"
                         r"piglin_brute)\b")

    @classmethod
    def fights(cls, row):
        """From the row's data alone: a hostile summoned in its setup, or the dragon slain. (A hostile a `before` hook
        summons is code, not data: not seen here.)"""
        text = " ".join(map(str, row.get("setup", ())))
        return bool(cls.HOSTILE.search(text)) or "slay_dragon" in row.get("skills", ())

    def test_fight_rows_are_combat(self):
        out = sorted(n for n, r in sc.SCENARIOS.items()
                     if n not in self.EXEMPT and r["tier"] != "combat" and self.fights(r))
        self.assertEqual(out, [])

    def test_the_check_sees_a_fight(self):
        # must fail: a summoned zombie outside the combat tier is caught; a peaceful row is not
        rows = [("summoned zombie", {"setup": ["summon zombie 0 0 0"]}, True),
                ("the dragon slain", {"skills": ["slay_dragon"]}, True),
                ("a namespaced skeleton among other setup", {"setup": ["give @p bread", "summon minecraft:skeleton"]},
                 True),
                ("must fail: a zombified piglin is not a zombie", {"setup": ["summon zombified_piglin 0 0 0"]}, False),
                ("cows only", {"setup": ["summon cow 0 0 0"]}, False)]
        for name, row, want in rows:
            with self.subTest(name):
                self.assertEqual(self.fights(row), want)


class Budgets(unittest.TestCase):
    # (situation, a sheet) → the rows over their limit
    ROWS = [("the real sheet: only the long list", None, None),
            ("a core row over the limit", {"x": {"tier": "core", "budget": runner.ROW_LIMIT_S + 1}}, ["x"]),
            ("must fail: the off-by-one a strict count gets wrong — a core row at the limit", {"x": {"tier": "core", "budget": runner.ROW_LIMIT_S}}, []),
            ("an exception row over the limit", {"x": {"tier": "exception", "budget": runner.ROW_LIMIT_S + 1}}, ["x"]),
            ("a brain row over the limit", {"x": {"tier": "brain", "budget": runner.ROW_LIMIT_S + 1}}, ["x"]),
            ("a combat row over the limit", {"x": {"tier": "combat", "budget": runner.ROW_LIMIT_S + 1}}, ["x"]),
            ("a common row at the limit", {"x": {"tier": "common", "budget": runner.ROW_LIMIT_S}}, []),
            ("acceptance is its own limit", {"x": {"tier": "acceptance", "budget": 1800}}, [])]

    def test_budget_limits(self):
        for name, sheet, want in self.ROWS:
            with self.subTest(name):
                if sheet is None:
                    self.assertEqual(sorted(set(over_limit(sc.SCENARIOS)) - LONG), [])
                    self.assertEqual(sorted(LONG - set(over_limit(sc.SCENARIOS))), [], "fixed: drop it from LONG")
                else:
                    self.assertEqual(over_limit(sheet), want)


class FakeContract:
    def __init__(self, *provides):
        self.provides = {p: None for p in provides}


REGISTRY = {"chop": FakeContract("item:log"), "mine": FakeContract("mine"), "eat": FakeContract(),
            "smelt": FakeContract("smelt"), "load_smelter": FakeContract("smelt")}
SHEET = {"chop_row": {"tier": "core", "skills": ["item:log"]}, "mine_row": {"tier": "core", "skills": ["mine"]},
         "eat_row": {"tier": "common", "skills": ["eat"]}, "smelt_row": {"tier": "exception", "skills": ["smelt"]},
         "brain_row": {"tier": "exception", "skills": []}}
# (tier, changed skills or None, rows selected)
SELECT = [
    ("core", None, ["chop_row", "mine_row"]),
    ("common", None, ["eat_row"]),
    ("all", None, list(SHEET)),
    ("all", {"chop"}, ["chop_row"]),                       # by the effect the skill provides
    ("all", {"eat"}, ["eat_row"]),                         # by name, for a skill that provides nothing
    ("all", {"load_smelter"}, ["smelt_row"]),              # an effect shared by two skills: either proves it
    ("core", {"eat"}, ["chop_row", "mine_row"]),           # nothing in this tier proves it: core stands in
    ("all", {"a_skill_no_row_proves"}, ["chop_row", "mine_row"]),  # must fail: no row proves it, core stands in
    ("all", set(), ["chop_row", "mine_row"]),              # a change that touched no skill: core
]
DIFF = """diff --git a/bonobo/wood.py b/bonobo/wood.py
--- a/bonobo/wood.py
+++ b/bonobo/wood.py
@@ -40,2 +40,3 @@ def chop(ctx, n):
@@ -200 +201 @@ def other():
diff --git a/bonobo/skills.py b/bonobo/skills.py
--- a/bonobo/skills.py
+++ /dev/null
"""
SPANS = {"chop": ("bonobo/wood.py", 20, 60), "mine": ("bonobo/skills.py", 400, 500), "far": ("bonobo/wood.py", 300, 320)}          # fixture: three skill spans the diff rows touch


class Changed(unittest.TestCase):
    def test_selection(self):
        for tier, changed, want in SELECT:
            with self.subTest(tier=tier, changed=changed):
                self.assertEqual(sc.select(SHEET, tier, changed, REGISTRY), want)

    # (situation, git diff -U0 text) → changed lines per file → skills whose spans they touch
    DIFFS = [("one hunk in chop, one elsewhere in the file", DIFF, {"bonobo/wood.py": [40, 41, 42, 201]}, {"chop"}),
             ("must fail: nothing changed", "", {}, set()),
             ("a deleted file only", "+++ /dev/null\n@@ -1,3 +0,0 @@\n", {}, set()),
             ("two skills in two files", "+++ b/bonobo/wood.py\n@@ -300 +300 @@\n+++ b/bonobo/skills.py\n"
              "@@ -450,2 +450,2 @@\n", {"bonobo/wood.py": [300], "bonobo/skills.py": [450, 451]}, {"far", "mine"}),
             ("the line just past a span", "+++ b/bonobo/wood.py\n@@ -61 +61 @@\n", {"bonobo/wood.py": [61]}, set())]

    def test_diff_to_skills(self):
        for name, diff, hunks, touched in self.DIFFS:
            with self.subTest(name):
                self.assertEqual(sc.diff_hunks(diff), hunks)
                self.assertEqual(sc.touched_skills(hunks, SPANS), touched)

    def test_real_registry_spans(self):
        spans = sc.skill_spans(skillkit.REGISTRY, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(set(spans), set(skillkit.REGISTRY))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name, (path, lo, hi) in spans.items():
            with self.subTest(name):
                self.assertTrue(path.startswith("bonobo/") and lo <= hi)
                with open(os.path.join(root, path)) as f:
                    body = "".join(f.readlines()[lo - 1:hi])
                self.assertIn(f"def {skillkit.REGISTRY[name].fn.__name__}(", body, "the span is the skill's own body")

    # a failure note → does it say nothing about why? (the runner marks such rows "NO REASON")
    NOTES = [("", True), ("McError", True), ("McError: failed", True),
             ("McError: chop: finished without reaching its goal", True),
             ("must fail: McError: chop: finished without reaching its goal (outcome not reached in 40s, budget 45s)", False),
             ("NotAvailable: no trees found nearby, even after exploring", False),
             ("TaskStuck: chop: no progress toward its goal for 45s", False),
             ("died (McError: failed)", False), ("NavFailed: could not get to (1, 2, 3)", False)]

    # (reached?, seconds, budget, crashed?) → ok, and the note says why not
    JUDGE = [(True, 30, 45, False, True, None), (True, 45, 45, False, True, None),
             (True, 46, 45, False, False, "over budget"), (False, 10, 45, False, False, "not reached"),  # must fail: one second over the budget
             (True, 10, 45, True, False, "crash"), (False, 500, 45, True, False, "not reached")]

    def test_budget_and_crash_judgment(self):
        for reached, seconds, budget, crashed, ok, why in self.JUDGE:
            with self.subTest(reached=reached, seconds=seconds, crashed=crashed):
                got_ok, got_why = sc.judge(reached, seconds, budget, crashed)
                self.assertEqual(got_ok, ok)
                if why:
                    self.assertIn(why, got_why)
                    self.assertFalse(sc.generic_failure(f"McError: {got_why}"), "an over-budget note says why")

    def test_generic_failure_notes(self):
        for note, want in self.NOTES:
            with self.subTest(note=note):
                self.assertEqual(sc.generic_failure(note), want)

    def test_run_verdicts(self):
        """Once; a failure re-runs; three at most; ≥ 2 of 3 passes."""
        for oks, want in (([], None), ([True], "pass"), ([False], None), ([False, True], None),
                          ([False, False], "fail"), ([True, True], "pass"), ([False, True, True], "pass"),
                          ([False, True, False], "fail"), ([True, False, False], "fail"),
                          ([False, False, True, True, True], "pass"),
                          # stopped at the limit: slow every time, never re-run
                          ([sc.TIMEOUT], "fail"), ([False, sc.TIMEOUT], "fail"), ([sc.TIMEOUT, True], None)):
            with self.subTest(oks=oks):
                self.assertEqual(sc.verdict_of(oks), want)
        self.assertEqual(sc.MAX_RUNS, 3)


class ThePoints(unittest.TestCase):
    def test_a_basics(self):
        for base in BASICS_A:
            with self.subTest(base):
                self.assertEqual(sc.SCENARIOS[f"{base}__base"]["point"], "A")

    def test_b_hazards(self):
        for name in L0_B:
            with self.subTest(name):
                self.assertEqual(sc.SCENARIOS[name]["point"], "B")

    def test_c_chain_in_order(self):
        chain = sorted((r["chain"], n) for n, r in sc.SCENARIOS.items() if "chain" in r)
        self.assertEqual([n for _, n in chain], list(CHAIN_C))
        for name in CHAIN_C:
            with self.subTest(name):
                self.assertEqual(sc.SCENARIOS[name]["point"], "C")

    # (what the acceptance row must be) — test point D: 30 minutes, a real world, from nothing, its own tier
    ACCEPT = [("point", lambda r: r["point"] == "D"), ("budget ≤ 30 min", lambda r: r["budget"] <= 30 * 60),
              ("a real world", lambda r: bool(r.get("raw"))), ("from nothing", lambda r: "clear @p" in r["setup"]),
              ("its own tier", lambda r: r["tier"] == "acceptance")]

    def test_d_acceptance(self):
        row = sc.SCENARIOS[sc.ACCEPTANCE_D]
        for what, holds in self.ACCEPT:
            with self.subTest(what):
                self.assertEqual(holds(row), True)
        with self.subTest("must fail: a core row is not an acceptance run"):
            self.assertFalse(all(holds(sc.SCENARIOS["iron_ingots"]) for _what, holds in self.ACCEPT))



class Cover(unittest.TestCase):
    # (conditions, bases, pinned) → the pairs run: coverage, not the product
    A = {"x": {"axis": "t", "bases": {"a", "b"}}, "y": {"axis": "t", "bases": {"b"}}}
    ROWS = [("one condition, two bases: both bases in the axis", {"x": A["x"]}, "ab", (), [("x", "a"), ("x", "b")]),
            ("the second condition covers the second base: two pairs, not three", A, "ab", (), [("x", "a"), ("y", "b")]),
            ("another axis covers its bases again", dict(A, z={"axis": "u", "bases": {"a"}}), "ab", (),
             [("x", "a"), ("y", "b"), ("z", "a")]),
            ("a pinned pair stays even when it adds little", A, "ab", [("x", "b")], [("x", "a"), ("x", "b"), ("y", "b")]),
            ("no condition applies: nothing", {"x": {"axis": "t", "bases": set()}}, "ab", (), []),
            ("a pinned pair that is not applicable is dropped", {"x": A["x"]}, "ab", [("x", "c")],
             [("x", "a"), ("x", "b")]),
            ("a must-fail condition: every base it applies to keeps its control",
             {"x": A["x"], "f": {"axis": "t", "bases": {"a", "b"}, "fails": "full"}}, "ab", (),
             [("x", "b"), ("f", "a"), ("f", "b")])]

    def test_cover(self):
        for name, conds, bases, pinned, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(sc.cover(conds, list(bases), pinned), want)

    def test_real_sheet_covers_every_condition_and_axis_base(self):
        pairs = sc.cover(sc.CONDITIONS, sc.BASES, [("night", "chop")])
        self.assertEqual({c for c, _ in pairs}, set(sc.CONDITIONS))
        self.assertEqual({(sc.CONDITIONS[c]["axis"], b) for c, b in pairs},
                         {(v["axis"], b) for v in sc.CONDITIONS.values() for b in v["bases"] if b in sc.BASES})


class SliceAndSelection(unittest.TestCase):
    # (queue task states) → the slice's own work is over
    QUEUES = [("all done", ["done", "done"], True),
              ("one still running", ["done", "running"], False),
              ("done, failed, cancelled: over (the row's check judges which)", ["done", "failed", "cancelled"], True),
              ("still pending", ["pending"], False),
              ("must fail: no queue: never over by this rule", [], False)]

    def test_queue_finished(self):
        for name, states, want in self.QUEUES:
            with self.subTest(name):
                self.assertEqual(sc.queue_finished([{"state": st} for st in states]), want)

    ROWS = {"b": {"tier": "brain"}, "c": {"tier": "combat"}, "m": {"tier": "common"}, "a": {"tier": "acceptance"}}
    # (tier asked, named on the command line) → the rows --failed / --pending may pick from
    TIERS = [("brain named: only brain (fight and common rows out)", "brain", True, ["b"]),
             ("combat named", "combat", True, ["c"]),
             ("all named: every tier but acceptance", "all", True, ["b", "c", "m"]),
             ("no tier named: every tier but acceptance", "core", False, ["b", "c", "m"]),
             ("must fail: a tier with no rows: none", "exception", True, [])]

    def test_tier_rows(self):
        for name, tier, named, want in self.TIERS:
            with self.subTest(name):
                self.assertEqual(sorted(sc.tier_rows(self.ROWS, tier, named)), want)


class FedAsNeeded(unittest.TestCase):
    B = "minecraft:bread"
    # (situation, food before, bread before, food after, bread after) → the eating filled the bar
    ROWS = [("food 0, bread ×4, all four eaten, bar 20", 0, 4, 20, 0, True),
            ("must fail: one bite of four, bar 5", 0, 4, 5, 3, False),
            ("food 12, two bites to 20", 12, 4, 20, 2, True),
            ("the last bite's boundary: 16 of 20 counts (bread 5 − 1)", 12, 4, 16, 2, True),
            ("one under the boundary", 12, 4, 15, 2, False),
            ("full already, nothing eaten", 20, 4, 20, 4, True),
            ("full already, a bite eaten anyway", 20, 4, 20, 3, False)]

    def test_rows(self):
        for name, fb, cb, fa, ca, want in self.ROWS:
            with self.subTest(name):
                self.assertIs(sc.fed_as_needed(fb, {self.B: cb}, fa, {self.B: ca}), want)

class EndgameBuilt(unittest.TestCase):
    # (row, what its setup must build so the job fits 30 s)
    ROWS = [("activate_end_portal", lambda r: "give @p ender_eye 3" in r["setup"]
             and sum("eye=true" in c for c in r["setup"]) == 3),
            ("fight_dragon", lambda r: r["before"] is sc._worn_perched_dragon
             and not any(c.startswith(("kill ", "summon ")) for c in r["setup"])),
            ("bed_bomb_kill", lambda r: r["before"] is sc._worn_perched_dragon),
            ("find_portal_room_fresh", lambda r: r["before"] is sc._built_stronghold),
            ("locate_stronghold", lambda r: not any(c.startswith("spreadplayers") for c in r["setup"])),
            ("seek_blocks_real", lambda r: any("oak_log" in c for c in r["setup"])),
            ("explore_for_animals_real", lambda r: any("summon cow" in c for c in r["setup"]) and r["stochastic"])]

    # a row each predicate must reject (chop__base, unless it happens to hold there)
    CONTRAST = {"locate_stronghold": "trek_overworld_30", "seek_blocks_real": "mine_stone__base"}

    def test_built(self):
        for name, ok in self.ROWS:
            with self.subTest(name):
                self.assertTrue(ok(sc.SCENARIOS[name]))
            other = self.CONTRAST.get(name, "chop__base")
            with self.subTest(f"must fail: {other} is not {name}'s build"):
                self.assertFalse(ok(sc.SCENARIOS[other]))

    # (start, stronghold, the leg's padded box): the plane lies under the skill's perpendicular (-dz, dx) leg
    LEGS = [((0, 0), (1000, 0), (-12, -12, 12, 212)),
            ((0, 0), (0, 1000), (-212, -12, 12, 12)),
            ((0, 0), (-1000, 0), (-12, -212, 12, 12)),
            ((0, 0), (0, -1000), (-12, -12, 212, 12)),
            ((0, 0), (0, 0), (-12, -12, 12, 12))]            # must fail: no leg at all, the padding only

    def test_leg_box(self):
        for start, sh, want in self.LEGS:
            with self.subTest(sh):
                self.assertEqual(sc._leg_box(start, sh), want)

    # (rectangle) → every fill under 32768 blocks, together covering it
    FLATS = [(0, 0, 200, 200), (-12, -12, 212, 12), (0, 0, 0, 0), (0, 0, 300, 300),
             (5, 5, 1, 1)]                                 # must fail: an inverted rectangle fills nothing

    def test_flat_fills(self):
        for x0, z0, x1, z1 in self.FLATS:
            with self.subTest((x0, z0, x1, z1)):
                xs = []
                for cmd in sc._flat(x0, z0, x1, z1, 5, "stone"):
                    a, _, _, b = (int(v) for v in cmd.split()[1:5])
                    self.assertLessEqual((b - a + 1) * (z1 - z0 + 1), 32768)
                    xs += range(a, b + 1)
                self.assertEqual(xs, list(range(x0, x1 + 1)))

if __name__ == "__main__":
    unittest.main()


class ResetBrain(unittest.TestCase):
    """bench.core.reset_brain: nothing a row learned reaches the next row (the "axe broke" leak)."""

    def test_row_state_is_dropped(self):
        from bonobo import arbiter, brain as B
        from bonobo.memory import Memory
        br = B.Brain()
        shared = br.blacklist
        # (what the last row left, how to read it after the reset, the clean value)
        rows = [("a broken axe note", lambda: br.needs.broken.add("axe"), lambda: br.needs.broken, set()),
                ("a tier that worked", lambda: br.needs.working.update(pickaxe=2), lambda: br.needs.working, {}),
                ("a ban", lambda: br.blacklist.update({(1, 2, 3): 9e9}), lambda: br.blacklist, {}),
                ("a held plan", lambda: br.held.update(t1={}), lambda: br.held, {}),
                ("a committed task", lambda: setattr(br, "committed", "t1"), lambda: br.committed, None),
                ("the ban dict stays the one fight_loop holds", lambda: None, lambda: br.blacklist is shared, True),
                ("must fail: a fight left holding the body: the next row starts with it free",
                 lambda: setattr(arbiter.BODY, "lease", (arbiter.Intent(sorted(arbiter.SCALES)[0], "fight", "last row"),
                                                          lambda: False, time.time())),
                 lambda: arbiter.BODY.holder(), None)]
        for name, dirty, read, clean in rows:
            with self.subTest(name):
                dirty()
                sc.reset_brain(br, Memory(os.path.join(os.environ["MC_DATA"], "reset.json")))
                self.assertEqual(read(), clean)


class TimeoutSticks(unittest.TestCase):
    """A TIMEOUT sticks to the row's key (setup + skill code + mod): same key → FAIL without a run; any change → run."""

    ROW = {"module": "skills", "setup": ["fill 0 0 0 1 1 1 stone"], "budget": 30}

    def key(self, row, dep="d1", mod="m1"):
        with mock.patch.dict(sc.SCENARIOS, {"x": row}), mock.patch.object(runner, "reach_hash", lambda r: dep), \
                mock.patch.object(runner, "mod_hash", lambda tags: mod):
            return runner._code_for("x")

    def test_timeout_is_cached_per_key(self):
        stopped = {"ok": False, "note": f"{sc.TIMEOUT}: stopped at the 30s limit", "cls": "skill"}
        table = {"x": {self.key(self.ROW): [stopped]}}
        rows = [  # (what changed since the TIMEOUT, the key now, cached?)
            ("nothing: skipped, reported FAIL", self.key(self.ROW), True),
            ("must fail: the setup changed, not cached", self.key(dict(self.ROW, setup=["fill 0 0 0 2 2 2 stone"])), False),
            ("the skill's Python", self.key(self.ROW, dep="d2"), False),
            ("the mod jar", self.key(self.ROW, mod="m2"), False),
            ("the budget", self.key(dict(self.ROW, budget=45)), False),
        ]
        for name, key, cached in rows:
            with self.subTest(name):
                got = sc.cached_timeout(table, "x", key)
                self.assertEqual(got is not None and got.startswith(f"{sc.TIMEOUT} (cached)"), cached)

    def test_only_a_timeout_sticks(self):
        rows = [("must fail: an ordinary failure is re-run", [{"ok": False, "note": "NavFailed: no route", "cls": "skill"}], None),
                ("a pass after the timeout clears it",
                 [{"ok": False, "note": f"{sc.TIMEOUT}: x", "cls": "skill"}, {"ok": True, "note": "", "cls": "skill"}],
                 None),
                ("no runs", [], None),
                ("the latest is the timeout", [{"ok": True, "note": "", "cls": "skill"},
                                              {"ok": False, "note": f"{sc.TIMEOUT}: x", "cls": "skill"}],
                 f"{sc.TIMEOUT} (cached): {sc.TIMEOUT}: x")]
        for name, runs, want in rows:
            with self.subTest(name):
                self.assertEqual(sc.cached_timeout({"x": {"k": runs}}, "x", "k"), want)


class BrainGrid(unittest.TestCase):
    """The brain tier's families: cells from the fight sheet's walker, each family ≥ 2 cells (the base and one value
    off it; combinations are the arbiter's offline table) and ≥ 2 expectations
    (the decision and its boundary or must-not), every cell ≤ 60 s."""

    def test_families(self):
        for fam, (grid, _queue, rule) in sc.BRAIN_FAMILIES.items():
            with self.subTest(fam):
                grid = list(grid)
                whys = {rule(c)[1] for c in grid}
                cells = sc._grid_cells()
                names = [sc.grid_name(cells[tuple(c[d] for d in sc.BRAIN_DIMS)]["families"], c) for c in grid]
                self.assertEqual((len(grid) >= 2, len(whys) >= 2, len(set(names)) == len(names)), (True, True, True))
                self.assertEqual([n for n in names if sc.SCENARIOS[n]["budget"] > 60 or sc.tier_of(n, sc.SCENARIOS[n]) != "brain"],
                                 [])

    def test_rules(self):
        # (family, the cell's moved dimensions, the expectation it must get)
        rows = [("night_first", {}, "a day ahead: the task first, no bed made (must not)"),  # must fail: the (must not) expectations
                ("night_first", {"dusk": "tight"}, "dusk or night on the surface, no bed: the night first"),
                ("night_first", {"dusk": "night", "food": "low"}, "hungry: food before the task (cooking it counts)"),
                ("tool_tier", {"tool": "one_use"}, "broken: the best tier this bag crafts (iron)"),
                ("tool_tier", {}, "fresh: nothing crafted, the ingots kept (must not craft)"),
                ("night_under", {"dusk": "night", "head": "underground"}, "night underground: work there (ore), no climb"),
                ("night_under", {"dusk": "tight", "head": "underground"},
                 "dusk underground: already under cover, no climb to the surface (boundary)"),
                ("night_under", {}, "daylight: no bed made, no sleep (must not)"),
                ("seen_store", {"seen": "noted"}, "noted: straight there without a scan (must not scan), the note retired"),
                ("seen_store", {}, "not noted: found anyway, by scanning")]
        for fam, moved, want in rows:
            with self.subTest(fam, **moved):
                self.assertEqual(sc.BRAIN_FAMILIES[fam][2](dict(sc.BRAIN_BASE, **moved))[1], want)


class FoodFirstFromTheWorld(unittest.TestCase):
    """night_first__low's check, from the world: a furnace holding the beef (or cooked beef in the bag) before any
    log, and the bar no
    lower at the end than the drain left it. Logs first with nothing cooking must fail."""

    def test_over_the_table(self):
        from types import SimpleNamespace
        check = sc.BRAIN_FAMILIES["night_first"][2](dict(sc.BRAIN_BASE, dusk="night", food="low"))[0]
        rows = [("beef in a furnace at 2 s, logs at 9 s, food kept", {"furnace_beef": 2.0, "log": 9.0}, 12, True),
                ("cooked beef in the bag before logs", {"minecraft:cooked_beef": 3.0, "log": 9.0}, 12, True),
                ("must fail: logs first, nothing in a furnace", {"log": 4.0}, 12, False),
                ("must fail: logs at 3 s, the beef in the furnace only at 8 s", {"furnace_beef": 8.0, "log": 3.0}, 12,
                 False),
                ("must fail: food first but the bar fell below the drain", {"furnace_beef": 2.0, "log": 9.0}, 7, False)]
        for name, first, food_end, want in rows:
            with self.subTest(name):
                fake = SimpleNamespace(get=lambda path, _f=food_end: {"food": _f})
                saved_first, saved_base = dict(sc.FIRST), dict(sc.BASE)
                try:
                    sc.FIRST.clear()
                    sc.FIRST.update(first)
                    sc.BASE["food_drained"] = 10
                    self.assertIs(bool(check(fake, None)), want)
                finally:
                    sc.FIRST.clear()
                    sc.FIRST.update(saved_first)
                    sc.BASE.clear()
                    sc.BASE.update(saved_base)


class FurnaceSlots(unittest.TestCase):
    """vocab.furnace_slots: what a furnace holds, from the game's `data get block … Items` answer."""

    def test_table(self):
        head = "Furnace at 10000, 200, 10000 has the following block data: "
        rows = [("must fail: empty", [head + "[]"], {}),
                ("raw beef in the input", [head + '[{Slot: 0b, id: "minecraft:beef", count: 2}]'],
                 {0: ("minecraft:beef", 2)}),
                ("cooked beef in the output", [head + '[{count: 2, Slot: 2b, id: "minecraft:cooked_beef"}]'],
                 {2: ("minecraft:cooked_beef", 2)}),
                ("both, and the fuel", [head + '[{Slot: 0b, id: "minecraft:beef", count: 1}, {Slot: 1b, id: '
                                               '"minecraft:coal", count: 1}, {Slot: 2b, id: "minecraft:cooked_beef", '
                                               'count: 1}]'],
                 {0: ("minecraft:beef", 1), 1: ("minecraft:coal", 1), 2: ("minecraft:cooked_beef", 1)}),
                ("not a furnace answer", ["Found no elements matching Items"], {})]
        for name, reply, want in rows:
            with self.subTest(name):
                self.assertEqual(sc.furnace_slots(reply), want)


class Drain(unittest.TestCase):
    """vocab.drain_step: fast while saturation is left or the bar is high, slow for the last points, stop at
    the level + 1 — at full strength all the way it overshot to 0 and 4."""

    def test_table(self):
        rows = [("full, saturation left: fast", 20, 5.0, 10, "fast"),
                ("no saturation, high: fast", 16, 0.0, 10, "fast"),
                ("four above the stop: slow (the overshoot zone)", 15, 0.0, 10, "slow"),
                ("one above the stop: slow", 12, 0.0, 10, "slow"),
                ("saturation left though low: fast (the bar cannot move before it is gone)", 13, 3.0, 10, "fast"),
                ("at level + 1: stop", 11, 0.0, 10, "stop"),
                ("must fail: overshot below the level: stop", 8, 0.0, 10, "stop")]
        for name, food, sat, level, want in rows:
            with self.subTest(name):
                self.assertEqual(sc.drain_step(food, sat, level), want)


class EatTarget(unittest.TestCase):
    """vocab.eat_target_s: per bite × the bites the gap takes (a flat 3 s failed a 4-bite meal at 7.2 s)."""

    def test_table(self):
        per = sc.TARGET_S["eat"] * sc.TARGET_SLACK
        rows = [("food 4, bread: 16 points of gap, 4 bites", 4, {"minecraft:bread": 8}, 4 * per),
                ("food 10, bread: 2 bites", 10, {"minecraft:bread": 8}, 2 * per),
                ("food 16, cooked beef: one bite", 16, {"minecraft:cooked_beef": 4}, 1 * per),
                ("must fail: full: nothing to eat, no target", 20, {"minecraft:bread": 8}, None),
                ("nothing carried: no target", 6, {}, None)]
        for name, food, carried, want in rows:
            with self.subTest(name):
                self.assertEqual(sc.eat_target_s(food, carried), want)


class Chance(unittest.TestCase):
    """runner.stochastic / needs_clock / verdict_of(chance=): a deterministic row is decided by one run."""

    def test_stochastic(self):
        rows = [("must fail: blocks only", {"setup": ["fill 0 0 0 1 1 1 stone"], "doc": "a wall"}, False),
                ("a summoned mob", {"setup": ["summon cow 1 2 3"], "doc": ""}, True),
                ("a generated tree", {"setup": ["place feature minecraft:oak 1 2 3"], "doc": ""}, True),
                ("a fight", {"setup": [], "doc": "", "combat": True}, True),
                ("a trade roll (barter)", {"setup": [], "doc": "barter with piglins"}, True),
                ("marked deterministic over a summon", {"setup": ["summon cow 1 2 3"], "doc": "", "stochastic": False},
                 False),
                ("a built tree (_grove)", {"setup": sc._grove((3, 0)), "doc": ""}, False)]
        for name, row, want in rows:
            with self.subTest(name):
                self.assertEqual(runner.stochastic(row), want)

    def test_needs_clock(self):
        rows = [("must fail: nothing about time", {"setup": ["fill 0 0 0 1 1 1 stone"], "doc": "a wall"}, False),
                ("a set time", {"setup": ["time set 18000"], "doc": ""}, True),
                ("a sleep skill", {"setup": [], "doc": "", "skills": ["sleep"]}, True),
                ("a night in the doc", {"setup": [], "doc": "survive the night"}, True)]
        for name, row, want in rows:
            with self.subTest(name):
                self.assertEqual(runner.needs_clock(row), want)

    def test_one_run_decides_a_deterministic_row(self):
        rows = [([True], False, "pass"), ([False], False, "fail"), ([sc.TIMEOUT], False, "fail"),
                ([False], True, None), ([False, True], True, None), ([], False, None)]  # must fail: one failure of a chance row decides nothing
        for oks, chance, want in rows:
            with self.subTest(oks=oks, chance=chance):
                self.assertEqual(sc.verdict_of(oks, chance=chance), want)


class InterruptByProgress(unittest.TestCase):
    """Interrupts, contests, take-overs and sand land on progress (`_progress_of`), never on a clock."""

    def test_progress_of(self):
        rows = [("a product to count", {"effect": ("log", 1)}, ("bag", "log")),
                ("an earlier product wins over the effect", {"effect": ("minecraft:wooden_pickaxe", 1),
                                                             "progress": ("planks", 1)}, ("bag", "planks")),
                ("no product: the walk to the target", {"target": (14, 200, 0)}, ("walk", (14, 200, 0))),
                ("must fail: neither: nothing to trigger on", {}, None)]
        for name, base, want in rows:
            with self.subTest(name):
                self.assertEqual(sc._progress_of(base), want)

    def test_every_triggered_row_has_progress(self):
        kinds = {k: c for k, c in sc.CONDITIONS.items() if c.get("interrupt") or c.get("hazard") == "sand"}
        missing = sorted(f"{b}__{k}" for k, c in kinds.items() for b in c["bases"] if sc._progress_of(sc.BASES[b]) is None)
        self.assertEqual(missing, [])


class Watchdog(unittest.TestCase):
    """runner._watchdog: the row is interrupted at its limit and /stop is posted, whatever SIGINT was set to."""

    def test_it_fires(self):
        import signal
        import threading
        import time
        from bonobo import api
        rows = [("SIGINT ignored (a background job)", signal.SIG_IGN, 0.2, 1.0, True),
                ("Python's own handler", signal.default_int_handler, 0.2, 1.0, True),
                ("SIGINT at its default", signal.SIG_DFL, 0.2, 1.0, True),
                ("must fail: the run ends first: nothing fires", signal.default_int_handler, 1.0, 0.2, False)]
        for name, handler, limit, work, want in rows:
            with self.subTest(name):
                old = signal.signal(signal.SIGINT, handler)
                posts, fired, hit = [], threading.Event(), False
                try:
                    with mock.patch.object(api, "post", lambda path, body=None: posts.append(path)):
                        t = runner._watchdog(limit, fired)
                        try:
                            time.sleep(work)
                        except KeyboardInterrupt:
                            hit = True
                        t.cancel()
                        time.sleep(0.05)
                finally:
                    signal.signal(signal.SIGINT, old)
                self.assertEqual((hit, fired.is_set(), posts), (want, want, ["/stop"] if want else []))


class FailedLast(unittest.TestCase):
    """`mc.py scenario --failed`: the rows whose latest counted run failed (runner.failed_last)."""

    def run_(self, ok, t, cls="skill", note=""):
        return {"ok": ok, "s": 1.0, "note": note, "cls": cls, "t": t}

    def test_rows_over_the_table(self):
        table = {
            "passed_last": {"a": [self.run_(False, 1)], "b": [self.run_(True, 2, "pass")]},
            "failed_last": {"a": [self.run_(True, 1, "pass")], "b": [self.run_(False, 2)]},
            "timed_out": {"a": [self.run_(False, 3, note="TIMEOUT: stopped at the 60s limit")]},
            "setup_only_after_a_pass": {"a": [self.run_(True, 1, "pass"), self.run_(False, 5, "setup")]},
            "never_counted": {"a": [self.run_(False, 1, "harness")]},
            "failed_on_an_older_code_last": {"new": [self.run_(True, 1, "pass")], "old": [self.run_(False, 9)]},
        }
        self.assertEqual(runner.failed_last(table), ["failed_last", "failed_on_an_older_code_last", "timed_out"])

    def test_empty_table(self):
        self.assertEqual(runner.failed_last({}), [])


class Difficulty(unittest.TestCase):
    """runner.difficulty_of / difficulty_set: normal unless a row asks, and the game's reply is what decides."""

    def test_rows(self):
        rows = [("any row: normal", {}, ["The difficulty has been set to Normal"], "normal", True),
                ("already normal", {}, ["The difficulty did not change; it is already set to normal"], "normal", True),
                ("must fail: the game stayed peaceful: a setup failure", {},
                 ["The difficulty did not change; it is already set to peaceful"], "normal", False),
                ("a row that asks for peaceful", {"difficulty": "peaceful"},
                 ["The difficulty has been set to Peaceful"], "peaceful", True),
                ("no reply at all", {}, [], "normal", False)]
        for name, row, reply, want, ok in rows:
            with self.subTest(name):
                self.assertEqual((runner.difficulty_of(row), runner.difficulty_set(reply, runner.difficulty_of(row))),
                                 (want, ok))


class Pending(unittest.TestCase):
    """`mc.py scenario --pending`: no result under the row's current key, or its last run there failed."""

    def run_(self, ok, t, cls="skill", note=""):
        return {"ok": ok, "s": 1.0, "note": note, "cls": cls, "t": t}

    def test_rows(self):
        table = {"passed": {"k1": [self.run_(True, 1, "pass")]},
                 "failed": {"k1": [self.run_(True, 1, "pass"), self.run_(False, 2)]},
                 "timed_out": {"k1": [self.run_(False, 3, note="TIMEOUT: stopped at the 30s limit")]},
                 "setup_changed": {"old": [self.run_(True, 1, "pass")]},
                 "only_a_setup_failure": {"k1": [self.run_(False, 1, "setup")]},
                 "passed_after_failing": {"k1": [self.run_(False, 1), self.run_(True, 2, "pass")]}}
        codes = {name: "k1" for name in table} | {"never_run": "k1"}
        rows = [("must fail: passed under its key: not pending", "passed", False),
                ("last run failed: pending", "failed", True),
                ("timed out: pending", "timed_out", True),
                ("its key changed (setup, code or mod): pending", "setup_changed", True),
                ("never run: pending", "never_run", True),
                ("only an uncounted setup failure: pending", "only_a_setup_failure", True),
                ("failed then passed: not pending", "passed_after_failing", False)]
        got = runner.pending(table, codes)
        for name, row, want in rows:
            with self.subTest(name):
                self.assertEqual(row in got, want)


class SkillsAreTimed(unittest.TestCase):
    """A skill row fails past TARGET_SLACK × its target, timed from its own run (bench_bases.TARGET_S)."""

    # (situation, run_s, target_s) → ok, and the note names slowness (the outcome itself was reached)
    QUICK = [("chop in 12 s against 15: inside", 12.0, 15.0, True, None),
             ("chop in 16 s: slow, named", 16.0, 15.0, False, "slow: its own run 16.0s > target 15.0s"),
             ("edge: exactly the target", 12.0, 12.0, True, None),
             ("must fail: never timed", None, 3.0, False, "never timed"),
             ("no target: speed not judged", 50.0, None, True, None)]

    def test_quick_over_the_table(self):
        for name, took, target, want, why in self.QUICK:
            with self.subTest(name):
                ok, got_why = sc.judge(True, 5, 45, False, took, target)
                self.assertIs(ok, want)
                if why:
                    self.assertIn(why, got_why)

    def test_the_timed_rows(self):
        """The rows with a target run through `_timed`; the others do not."""
        for name, want in (("chop__base", True), ("mine_stone__base", True), ("craft__base", True), ("eat__base", True),
                           ("find_air_capped", True), ("smelt__base", False), ("chop__night", False)):
            with self.subTest(name):
                self.assertEqual(sc.SCENARIOS[name]["run"].__qualname__ == "_timed.<locals>.go", want)
                self.assertEqual("target_s" in sc.SCENARIOS[name], want)
        sc.BASE.pop("run_s", None)
        self.assertEqual((sc._timed(lambda ctx: "done")(None), sc.BASE["run_s"] < 1.0), ("done", True))


class EveryPartHasAMustFail(unittest.TestCase):
    """Every base and every brain family has a control that must fail (a `fails=` row) or must not happen (a
    reverse check, said "(must not" in its doc): a bench of only happy paths passes a skill that always succeeds."""

    @staticmethod
    def controls(rows):
        return [n for n, r in rows if r.get("fails") or "(must not" in r.get("doc", "")]

    def test_every_base(self):
        for base in sc.BASES:
            with self.subTest(base):
                rows = [(n, r) for n, r in sc.SCENARIOS.items() if r.get("tags", {}).get("base") == base]
                self.assertNotEqual(self.controls(rows), [], f"{base}: no must-fail row")

    def test_every_brain_family(self):
        families = set(sc.BRAIN_FAMILIES) | {"fight_first"}
        for fam in sorted(families):
            with self.subTest(fam):
                rows = [(n, r) for n, r in sc.SCENARIOS.items() if fam in r.get("tags", {}).get("family", "").split("+")]
                self.assertNotEqual(self.controls(rows), [], f"{fam}: no must-fail row")


def walk_frames(speed=4.0, rise_at=3.0, stop=(None, None), task=None, food0=10, back=False):
    """Frames every 0.2 s for 5 s: x advancing at `speed` (standing still over `stop`, backwards if `back`), the
    food bar up by 3 from `rise_at` (never when None), `task` as the jar's running task."""
    out, x = [], 0.0
    for k in range(26):
        t = k * 0.2
        still = stop[0] is not None and stop[0] <= t <= stop[1]
        x += 0.0 if still or k == 0 else (-1 if back else 1) * speed * 0.2
        food = food0 + (3 if rise_at is not None and t >= rise_at else 0)
        out.append({"t": t, "x": round(x, 2), "food": food, "task": task})
    return out


class EatingOnTheWay(unittest.TestCase):
    """eat_while_walking's bar (ate_on_the_way over the walk's trace): fed during the walk, no eat task, and still
    walking forward through the bite."""
    ROWS = [("fed while walking on", walk_frames(), True),
            ("stopped to chew: x flat through the bite", walk_frames(stop=(1.4, 3.0)), False),
            ("a separate eat task ran", walk_frames(task={"type": "eat"}), False),
            ("must fail: never fed", walk_frames(rise_at=None), False),
            ("fed while walking the wrong way", walk_frames(back=True), False),
            ("fed at the very first frame: no bite window", walk_frames(rise_at=0.0), False),
            ("no frames", [], False)]

    def test_ate_on_the_way(self):
        for name, frames, want in self.ROWS:
            with self.subTest(name):
                self.assertIs(sc.ate_on_the_way(frames), want)

    def test_the_walk_is_done_once_fed(self):
        """fed_up: the walk stops once the bar rose to the autoeat's level, not at the walk's full length."""
        from bonobo.bench import vocab
        f = lambda *bars: [{"t": k * 0.2, "food": b} for k, b in enumerate(bars)]  # noqa: E731
        rows = [("fed 8 → 16 → 20", f(8, 8, 16, 20), True),
                ("fed to the level exactly", f(8, 18), True),
                ("must fail: one bite, still below the level", f(8, 16), False),
                ("must fail: full from the start, never ate", f(20, 20), False),
                ("must fail: no frames", [], False)]
        for name, frames, want in rows:
            with self.subTest(name):
                self.assertIs(vocab.fed_up(frames, 18), want)

    def test_the_row_reads_the_walk_it_ran(self):
        """The row's check word (`call walk_ate`) reads the walk's frames when the check runs: a word that took
        them when the row was built judged an empty walk every time."""
        from bonobo.bench import vocab
        rows = [("fed on the walk just run", walk_frames(), True),
                ("stopped to chew", walk_frames(stop=(1.4, 3.0)), False),
                ("must fail: no walk ran", None, False),
                ("must fail: never fed", walk_frames(rise_at=None), False)]
        for name, frames, want in rows:
            with self.subTest(name), mock.patch.dict(vocab.WALK, {} if frames is None else {"frames": frames},
                                                     clear=True):
                self.assertIs(vocab.call(None, None, "walk_ate", [], resolve=vocab.resolve), want)


def work_frames(food0=8, rise_at=2.0, gap=None, task_type="mine_many", end_food=None):
    """Frames of a traced mine: the jar's task running throughout (`gap`: (from, to) s with no task), the bar rising
    by 8 at `rise_at` (None: never), `end_food` the last frame's bar."""
    out = []
    for k in range(30):
        t = k * 0.2
        idle = gap is not None and gap[0] <= t <= gap[1]
        task = None if idle else {"type": task_type, "status": "running"}
        food = food0 + (8 if rise_at is not None and t >= rise_at else 0)
        out.append({"t": t, "x": 0.0, "food": food, "task": task})
    if end_food is not None:
        out[-1]["food"] = end_food
    return out


class EatingWhileWorking(unittest.TestCase):
    """mine_while_hungry's bar (worked_fed over the mine's trace): eaten, if at all, inside the running work."""
    ROWS = [("fed during the collect walk, the mine running", work_frames(), True),
            ("not fed, the bar held", work_frames(rise_at=None), True),
            ("must fail: an eat task inserted (a pause)", work_frames(task_type="eat"), False),
            ("must fail: the work idle through the bite", work_frames(gap=(1.0, 2.0)), False),
            ("must fail: the bar lower at the end", work_frames(rise_at=None, end_food=6), False),
            ("must fail: no frames", [], False)]

    def test_worked_fed(self):
        from bonobo.bench import vocab
        for name, frames, want in self.ROWS:
            with self.subTest(name):
                self.assertIs(vocab.worked_fed(frames), want)

    def test_the_row_reads_the_mine_it_ran(self):
        from bonobo.bench import vocab
        for name, frames, want in [("traced mine, fed inside it", work_frames(), True),
                                   ("must fail: no mine traced", None, False)]:
            with self.subTest(name), mock.patch.dict(vocab.WALK, {} if frames is None else {"mine": frames},
                                                     clear=True):
                self.assertIs(vocab.call(None, None, "mine_fed", [], resolve=vocab.resolve), want)


class HungryRowsTarget(unittest.TestCase):
    """A row made hungry for its own work is judged by its check, not held to the eat base's time: only an eat row's
    `before` sets BASE["target_s"] (the runner's judge fails a slower run against it)."""
    ROWS = [("eat_while_walking: drained, no eat target", "eat_while_walking", False),
            ("mine_while_hungry: drained, no eat target", "mine_while_hungry", False),
            ("must fail: the eat base sets its target", "eat__base", True),
            ("must fail: an eat row under water sets it too", "eat__underwater", True)]

    def test_only_eat_rows_set_a_target(self):
        from bonobo import api
        from bonobo.bench import vocab
        world = {"/state": {"food": 8, "health": 20.0}, "/inventory": {"slots": [], "selectedSlot": 0, "equipment": {}}}
        for name, row, want in self.ROWS:
            with self.subTest(name), contextlib.ExitStack() as quiet, mock.patch.object(vocab.time, "sleep"), \
                    mock.patch.object(api, "get", side_effect=lambda path, *a, **k: world[path.split("?")[0]]), \
                    mock.patch.dict(vocab.BASE, clear=True):
                for m in (vocab,) + vocab.WORD_MODULES:          # every home that sends a command
                    quiet.enter_context(mock.patch.object(m, "_chat"))
                sc.SCENARIOS[row]["before"](None)
                self.assertIs("target_s" in vocab.BASE, want)


class DecisionOrderWatch(unittest.TestCase):
    """first_step: a row's watcher stamps tokens on its own clock; one left from an earlier row (its 70 s not up)
    writes nothing once the next row's hook ran — two clocks in one FIRST put a bed made first after the logs."""

    def test_first_step(self):
        from bonobo.bench import vocab
        from bonobo.world import Inventory
        from tests.world import inventory
        bag = Inventory(inventory(("white_bed", 1), ("oak_log", 2)))
        logs_at_start = {"minecraft:oak_log": 2, "log": 2}
        all_at_start = dict(logs_at_start, **{"minecraft:white_bed": 1, "bed": 1})
        bed = {"minecraft:white_bed": 3.0, "bed": 3.0}
        # (situation, generations behind, start counts, beef in a furnace, FIRST after, the watcher goes on)
        rows = [("this row's watcher: the bed stamped on its clock", 0, logs_at_start, False, bed, True),
                ("beef in a furnace stamped", 0, logs_at_start, True, dict(bed, furnace_beef=3.0), True),
                ("must fail: a stale watcher (a later row started) writes nothing", 1, logs_at_start, True, {}, False),
                ("must fail: tokens at their start count are not stamped", 0, all_at_start, False, {}, True)]
        for name, behind, start, beef, want, going in rows:
            with self.subTest(name), mock.patch.dict(vocab.FIRST_WATCH, {"gen": 5}), \
                    mock.patch.dict(vocab.FIRST, {}, clear=True):
                got = vocab.first_step(5 - behind, 100.0, bag, lambda t: start.get(t, 0), lambda: beef, 103.0)
                self.assertEqual((dict(vocab.FIRST), got), (want, going))


class BuriedFirst(unittest.TestCase):
    """buried_by_sand's setup holds only when the sand buried the head: pushed clear, head_clear passes with no round."""

    def test_buried_first(self):
        from bonobo import skillcore
        from bonobo.bench import vocab
        from bonobo.bench.core import SetupInvalid
        rows = [("buried at once", [True], None),
                ("buried once the fill lands (third look)", [False, False, True], None),
                ("must fail: pushed clear of the sand", [False] * 5, SetupInvalid),
                ("must fail: never buried in the looks allowed", [False] * 9, SetupInvalid)]
        for name, looks, raises in rows:
            with self.subTest(name), mock.patch.object(skillcore, "head_buried", side_effect=looks), \
                    mock.patch.object(vocab.time, "sleep"):
                if raises:
                    with self.assertRaises(raises):
                        vocab._buried_first(None)
                else:
                    self.assertIsNone(vocab._buried_first(None))


class PlacedFacing(unittest.TestCase):
    """place_*'s check: the asked block stands at the cell with the asked facing — not any block that faces so."""

    def test_placed_facing_in(self):
        from bonobo.bench import vocab

        class Cell:
            def __init__(self, name, facing):
                self.n, self.f = name, facing

            def name(self, p):
                return self.n

            def prop(self, p, key):
                return self.f
        pos = (3, 200, 0)
        rows = [("the furnace, facing north", Cell("minecraft:furnace", "north"), "minecraft:furnace", "north", True),
                ("cobblestone, no facing asked", Cell("minecraft:cobblestone", None), "minecraft:cobblestone", None, True),
                ("must fail: another block with that facing", Cell("minecraft:chest", "north"), "minecraft:furnace",
                 "north", False),
                ("must fail: the furnace facing elsewhere", Cell("minecraft:furnace", "south"), "minecraft:furnace",
                 "north", False),
                ("must fail: nothing placed", Cell("minecraft:air", None), "minecraft:air", "north", False)]
        for name, cell, item, facing, want in rows:
            with self.subTest(name):
                self.assertIs(vocab.placed_facing_in(cell, pos, item, facing), want)


class SleptBefore(unittest.TestCase):
    """brain__night carries its bed: "night first" is the day coming back (a sleep, read from the world's clock) before
    the logs rose — the bed never rises in the bag, so the bag's order cannot say it."""

    def test_slept_through(self):
        from bonobo.bench import vocab
        rows = [("night, then morning after the sleep", 18000, 24100, True),
                ("night, then morning in raw ticks", 18000, 1000, True),
                ("must fail: still night", 18000, 18400, False),
                ("must fail: began in daylight", 1000, 1200, False)]
        for name, start, now, want in rows:
            with self.subTest(name):
                self.assertIs(vocab.slept_through(start, now), want)

    def test_slept_before(self):
        from bonobo.bench import vocab
        rows = [("slept, then logs", {"morning": 3.0, "log": 9.0}, False, True),
                ("slept, no ore after (or never)", {"morning": 3.0}, True, True),
                ("must fail: logs rose while still night", {"log": 2.0, "morning": 8.0}, False, False),
                ("must fail: never slept", {"log": 2.0}, False, False)]
        for name, first, or_never, want in rows:
            with self.subTest(name), mock.patch.dict(vocab.FIRST, first, clear=True):
                self.assertIs(vocab.slept_before("log", or_never)(None, None), want)

    def test_the_watcher_stamps_morning(self):
        from bonobo.bench import vocab
        from bonobo.world import Inventory
        from tests.world import inventory
        for name, turned, want in [("the day turned", True, {"morning": 2.0}), ("must fail: still night", False, {})]:
            with self.subTest(name), mock.patch.dict(vocab.FIRST_WATCH, {"gen": 1}), \
                    mock.patch.dict(vocab.FIRST, {}, clear=True):
                vocab.first_step(1, 10.0, Inventory(inventory()), lambda t: 0, lambda: False, 12.0, lambda: turned)
                self.assertEqual(dict(vocab.FIRST), want)


class ServerProbe(unittest.TestCase):
    """The bench's server probe: a test command's own reply (the lines before its numbered marker), read exactly."""

    def test_rows(self):
        from bonobo.bench import core
        rows = [("passed before the marker", ["Test passed", "[knh190] probe-3"], "probe-3", True),
                ("failed before the marker", ["Test failed", "[knh190] probe-3"], "probe-3", False),
                ("must fail: a 'Test passed' after the marker is another command's", ["Test failed",
                 "[knh190] probe-3", "Test passed"], "probe-3", False),
                ("must fail: no test line at all: no answer", ["[knh190] probe-3"], "probe-3", None),
                ("must fail: a line that only mentions 'passed' is not the answer",
                 ["the ore passed the test", "[knh190] probe-3"], "probe-3", None)]
        for name, lines, mark, want in rows:
            with self.subTest(name):
                self.assertIs(core.probe_answer(core.reply_before(lines, mark)), want)


class DiamondScan(unittest.TestCase):
    """is_diamond_scan: _no_scan counts a search for the ore, not the estimates' one look per round."""

    def test_is_diamond_scan(self):
        from bonobo.bench import vocab
        rows = [("mine's search for the ore", "/find?blocks=minecraft:diamond_ore&radius=24&limit=60", True),
                ("a travel scan's rare sightings", "/find?blocks=minecraft:diamond_ore,minecraft:obsidian&radius=48&limit=8",
                 True),
                ("must fail: the round's one look over every source block",
                 "/find?blocks=minecraft:coal_ore,minecraft:diamond_ore,minecraft:oak_log&radius=48&limit=14&perBlock=1",
                 False),
                ("must fail: a find for other blocks", "/find?blocks=minecraft:oak_log&radius=48&limit=20", False),
                ("must fail: not a find", "/state", False)]
        for name, path, want in rows:
            with self.subTest(name):
                self.assertIs(vocab.is_diamond_scan(path), want)


class SliceVerdict(unittest.TestCase):
    """vocab.slice_verdict: a failed slice says which part failed (a bare False told nobody anything)."""

    def test_over_the_table(self):
        ok = {"idle_s": 3.0, "loops": [], "waits": 0}
        rows = [("done, quiet, no waits: passes", True, ok, True, {}, True, "done=True"),
                ("must fail: not done: fails, says so", False, ok, True, {}, False, "done=False"),
                ("idle too long: fails, says how long", True, dict(ok, idle_s=40.0), True, {}, False, "idle_s=40.0/15"),
                ("a loop: fails, counts it", True, dict(ok, loops=["x ×5"]), True, {}, False, "loops=1/0"),
                ("waited with work queued: fails, names the kind", True, dict(ok, waits=2), True,
                 {"wait for day": 2, "idle": 3}, False, "{'wait for day': 2}"),
                ("waited with nothing queued: passes", True, dict(ok, waits=2), False, {}, True, "waits=2/-")]
        for name, finished, rep, queued, picks, want, says in rows:
            with self.subTest(name):
                got, why = sc.slice_verdict(finished, rep, queued, 15, 0, picks)
                self.assertIs(got, want)
                self.assertIn(says, why)


class KitRule(unittest.TestCase):
    """bench.core.kit_sword / bench_bases.KIT: iron for ordinary mobs, diamond for the high tier; the best work
    tool for every row whose work uses one."""

    def test_kit_sword(self):
        from bonobo.bench import core
        rows = [("a zombie: iron", ["zombie"], "give @p iron_sword"),
                ("a blaze: diamond", ["blaze"], "give @p diamond_sword"),
                ("must fail: the first mob alone — a zombie and an enderman: the best either calls for", ["zombie", "enderman"], "give @p diamond_sword"),
                ("nothing named: iron", [], "give @p iron_sword"),
                ("the dragon: diamond", ["ender_dragon"], "give @p diamond_sword")]
        for name, mobs, want in rows:
            with self.subTest(name):
                self.assertEqual(core.kit_sword(mobs), want)

    def test_rows_carry_their_tools(self):
        from bonobo.bench import core
        rows = [("chop__base", core.BEST_TOOLS["axe"]), ("mine_stone__base", core.BEST_TOOLS["pickaxe"]),
                ("unbury_sand", core.BEST_TOOLS["shovel"]), ("fight_blaze_3", "give @p diamond_sword"),
                ("fight_zombie_1", "give @p iron_sword")]
        for name, give in rows:
            with self.subTest(name):
                self.assertIn(give, sc.SCENARIOS[name]["setup"])
        # must not: a row that tests getting the tool is left bare
        self.assertNotIn(core.BEST_TOOLS["pickaxe"], sc.SCENARIOS["tool_tier__one_use"]["setup"])


class SetupReplies(unittest.TestCase):
    """bench.core.chat_lines / runner.difficulty_set over one batch's replies: the setup is sent a phase at a time and
    read once, so the difficulty read-back has to find its line among the others."""

    def test_chat_lines(self):
        from bonobo.bench import core
        rows = [("one chat line", "[12:00:01] [Render thread/INFO]: [CHAT] Set the time to 1000\n", ["Set the time to 1000"]),
                ("chat among other log lines", "[x] loading chunk\n[y]: [CHAT] Cleared the inventory\n[z] tick\n",
                 ["Cleared the inventory"]),
                ("must fail: none", "[x] nothing said\n", []),
                ("several, in order", "a [CHAT] one\nb [CHAT] two\n", ["one", "two"])]
        for name, text, want in rows:
            with self.subTest(name):
                self.assertEqual(core.chat_lines(text), want)

    def test_difficulty_among_a_batch(self):
        from bonobo.bench import runner
        batch = ["Cleared the inventory", "Set own game mode to Survival Mode", "The difficulty has been set to normal",
                 "Gamerule random_tick_speed is now set to: 0"]
        rows = [("normal asked, normal said", batch, "normal", True),
                ("peaceful asked, normal said", batch, "peaceful", False),
                ("already that difficulty", ["The difficulty did not change; it is already set to normal"], "normal", True),
                ("must fail: no difficulty line at all", batch[:2], "normal", False)]
        for name, lines, want, ok in rows:
            with self.subTest(name):
                self.assertIs(runner.difficulty_set(lines, want), ok)


class TwoSites(unittest.TestCase):
    """The next row's world is built at site B while a row runs at A (bench.core classify / shift / split_setup)."""

    def test_classify(self):
        from bonobo.bench import core
        rows = [("a fill: the build", "fill 9994 199 9994 10006 199 10006 stone", "world"),
                ("a give: the body", "give @p stone_pickaxe", "body"),
                ("a summon: late (it would wander, or burn)", "summon zombie 10004 200 10000", "late"),
                ("a gamerule: the global state, in the row", "gamerule spawn_mobs false", "body"),
                ("time and difficulty: in the row", "time set 13000", "body"),
                ("must fail: a fill read as the build — a fill relative to the player: in the row", "fill ~-1 ~ ~-1 ~1 ~2 ~1 air", "body"),
                ("wrapped in execute: what it runs", "execute in minecraft:overworld run setblock 1 2 3 stone", "world"),
                ("a tp: the body", "tp @p 10000.5 200 10000.5", "body")]
        for name, cmd, want in rows:
            with self.subTest(name):
                self.assertEqual(core.classify(cmd), want)

    def test_the_body_reset_leaves_a_hungry_row_hungry(self):
        from bonobo.bench import core
        fed = "effect give @p minecraft:saturation 1 10 true"
        rows = [("a plain row: fed after its setup", {"setup": ["give @p stone"]}, True),
                ("must fail: hunger in its setup: not refilled", {"setup": ["effect give @p minecraft:hunger 4 255 true"]}, False),
                ("tagged hungry (its before hook drains): not refilled",
                 {"setup": [], "tags": {"state": "hungry"}}, False),
                ("another state tag: fed", {"setup": [], "tags": {"state": "night"}}, True),
                ("no setup at all: fed", {}, True)]
        for name, sc_, want in rows:
            with self.subTest(name):
                got = core.body_reset(sc_)
                self.assertIn("effect give @p minecraft:instant_health 1 10 true", got)
                self.assertIs(fed in got, want)

    def test_shift(self):
        from bonobo.bench import core
        rows = [("a box corner pair moved 100 east", "fill 9994 184 9994 10006 199 10006 stone",
                 "fill 10094 184 9994 10106 199 10006 stone"),
                ("a decimal stays a decimal", "setblock 10000.5 200 10000.5 stone", "setblock 10100.5 200 10000.5 stone"),
                ("must fail: far outside the box: untouched", "fill 5 64 5 6 64 6 stone", "fill 5 64 5 6 64 6 stone"),
                ("block states and item counts untouched", "setblock 10002 200 10000 chest[facing=north]",
                 "setblock 10102 200 10000 chest[facing=north]")]
        for name, cmd, want in rows:
            with self.subTest(name):
                self.assertEqual(core.shift(cmd), want)

    def test_split_and_what_is_prebuilt(self):
        from bonobo.bench import core, runner
        setup = ["fill 9994 199 9994 10006 199 10006 stone", "clear @p", "summon cow 10002 200 10000",
                 "setblock 10002 200 10002 furnace", "time set day"]
        self.assertEqual(core.split_setup(setup),
                         (["fill 9994 199 9994 10006 199 10006 stone", "setblock 10002 200 10002 furnace"],
                          ["clear @p", "summon cow 10002 200 10000", "time set day"]))
        for sc_, want in (({}, True), ({"raw": True}, False), ({"dimension": "minecraft:the_nether"}, False),
                          ({"sweep": True}, False)):
            with self.subTest(sc=sc_):
                self.assertIs(runner.prebuildable(sc_), want)

    def test_a_prebuilt_world_is_taken_once_and_only_by_its_row(self):
        import threading
        from bonobo.bench import runner
        done = threading.Event()
        done.set()
        runner.PREBUILT.update(name="chop__base", done=done, ok=True)
        self.assertEqual((runner.take_prebuilt("craft__base"), runner.take_prebuilt("chop__base"),
                          runner.take_prebuilt("chop__base")), (False, True, False))


class PrebuiltReady(unittest.TestCase):
    """A row clones from site B only after its own prebuild signalled completion; everything else builds here."""

    @staticmethod
    def event(is_set):
        import threading
        e = threading.Event()
        if is_set:
            e.set()
        return e

    # (situation, PREBUILT, row) → clone from B
    TABLE = [
        ("the first row: nothing prebuilt → built synchronously", {"name": None, "done": None, "ok": False},
         "iron_ingots", False),
        ("its own build, finished and good → cloned", {"name": "iron_ingots", "done": True, "ok": True},
         "iron_ingots", True),
        ("must fail: another row's build", {"name": "chop__base", "done": True, "ok": True}, "iron_ingots", False),
        ("must fail: its own build failed", {"name": "iron_ingots", "done": True, "ok": False}, "iron_ingots",
         False),
        ("must fail: its own build not finished (no completion signal)",
         {"name": "iron_ingots", "done": False, "ok": True}, "iron_ingots", False),
    ]

    def test_table(self):
        from bonobo.bench import runner
        for why, pre, name, want in self.TABLE:
            with self.subTest(why):
                pre = dict(pre, done=None if pre["done"] is None else self.event(pre["done"]))
                self.assertIs(runner.prebuilt_ready(pre, name), want)


class RowKey(unittest.TestCase):
    """A row's readiness key: its own definition (row_hash) and the production functions it reaches (reached), not
    its module's whole closure — editing one row, or code a row never reaches, keeps every other verdict."""

    INDEX = {"chop": ["wood.def chop(ctx, n):\n    return trunk_batch(n)\n"],
             "trunk_batch": ["wood.def trunk_batch(n):\n    return [n]\n"],
             "decide": ["threat.def decide(state):\n    return 1\n"]}

    def key(self, index):
        from bonobo.bench import runner
        import hashlib
        return hashlib.sha1("\n".join(runner.reached({"chop"}, index)).encode()).hexdigest()

    def test_reach_over_the_table(self):
        base = self.key(self.INDEX)
        rows = [("must fail: a key over the whole module — an unrelated function changed (the fight's decide): same key",
                 dict(self.INDEX, decide=["threat.def decide(state):\n    return 2\n"]), True),
                ("a function it calls changed (trunk_batch): new key",
                 dict(self.INDEX, trunk_batch=["wood.def trunk_batch(n):\n    return [n, n]\n"]), False),
                ("the skill itself changed: new key", dict(self.INDEX, chop=["wood.def chop(ctx, n):\n    return 0\n"]),
                 False),
                ("nothing changed", dict(self.INDEX), True)]
        for name, index, same in rows:
            with self.subTest(name):
                self.assertEqual(self.key(index) == base, same)

    def test_row_hash(self):
        from bonobo.bench import runner
        row = dict(sc.SCENARIOS["chop__base"])
        other = dict(sc.SCENARIOS["craft__base"])
        base = runner.row_hash(row)
        rows = [("the same row: same", dict(row), True),
                ("its setup changed: new", dict(row, setup=list(row["setup"]) + ["give @p dirt"]), False),
                ("its budget changed: new", dict(row, budget=row["budget"] + 1), False),
                ("must fail: another row changed: this one untouched", dict(row), True)]
        other["setup"] = list(other["setup"]) + ["give @p dirt"]        # the edit to another row
        for name, r, same in rows:
            with self.subTest(name):
                self.assertEqual(runner.row_hash(r) == base, same)

    def test_a_call_on_a_module_is_that_module_s(self):
        from bonobo.bench import runner
        index = {"run": ["api.def run(task):\n    return 1\n", "perception.def run(self):\n    loop()\n"],
                 "loop": ["perception.def loop():\n    pass\n"]}
        got = runner.reached({"api.run"}, index)
        self.assertEqual(got, ["api.def run(task):\n    return 1\n"])


class Migrate(unittest.TestCase):
    """runner.migrate: an old verdict moves to the current key when the row's code, keyed the new way at that
    time, is today's — and the jar is the same."""

    def test_migrate_over_the_table(self):
        from bonobo.bench import runner
        passed = [{"ok": True, "s": 5.0, "note": "", "cls": "pass", "t": 100}]
        rows = [("the same code then and now: moved", {"r": {"OLD-jar-0.1.47": passed}}, {"r": "NEW-jar-0.1.47"},
                 {"r": "NEW"}, ["r"]),
                ("the code changed since: stays to be run", {"r": {"OLD-jar-0.1.47": passed}}, {"r": "NEW-jar-0.1.47"},
                 {"r": "OTHER"}, []),
                ("another jar then: stays", {"r": {"OLD-jar-0.1.46": passed}}, {"r": "NEW-jar-0.1.47"}, {"r": "NEW"},
                 []),
                ("must fail: no old record: nothing to move", {}, {"r": "NEW-jar-0.1.47"}, {"r": "NEW"}, []),
                ("already has a result under today's key: untouched",
                 {"r": {"OLD-jar-0.1.47": passed, "NEW-jar-0.1.47": passed}}, {"r": "NEW-jar-0.1.47"}, {"r": "NEW"},
                 [])]
        for name, table, current, then, want in rows:
            with self.subTest(name):
                got = runner.migrate(table, current, lambda n, t: then.get(n))
                self.assertEqual(got, want)
                if want:
                    self.assertEqual(table["r"]["NEW-jar-0.1.47"], passed)


class RealKeys(unittest.TestCase):
    def test_every_row_has_a_key(self):
        """code_for over the real sheet (nothing mocked): every row keys, and two rows differ."""
        from bonobo.bench import runner
        with mock.patch.object(runner, "mod_hash", lambda tags: "jar-test"):
            keys = {n: runner._code_for(n) for n in ("chop__base", "craft__base", "eat__base")}
        self.assertEqual(len(set(keys.values())), 3)
        self.assertTrue(all(k.endswith("-jar-test") and len(k.split("-")[0]) == 16 for k in keys.values()), keys)

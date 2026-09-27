"""The scenario sheet's shape (bonobo/scenarios.py), offline. The rows are run in the game, never here.

What is checked is what makes the sheet trustworthy before anyone runs it:
  - every row is well-formed: setup commands, a run, a WORLD check, a budget, the skills it proves, its test point
  - every registered skill has a real `verify` and at least one row proving it in the world — except the skills
    in the two allowlists below, which may only SHRINK: an entry that is no longer a gap fails until removed, a new
    gap fails until it is fixed (or, visibly, added here)
  - the cross-product is whole: every base has its plain row, every condition a row per base it applies to, every
    axis (terrain, timing, inventory) and every surprise is there
  - expected-failure rows name a specific reason (a regex that is not a catch-all)
  - the named rows of test points A–D exist, in the right order, with the right budgets
Driven from `skill.REGISTRY` and `scenarios.SCENARIOS`: a new skill or a new row needs no edit here.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain, fight_loop, scenarios as sc  # noqa: E402,F401  (brain/fight_loop: every skill module)
from bonobo import skill as skillkit  # noqa: E402

# Skills without a real verify (the runner judges them by nothing). May only shrink.
VERIFY_GAPS = {"await_perch", "bed_bomb_window", "break_caged_crystal", "build_bed_pit", "fight_dragon",
               "shake_enderman", "slay_dragon", "station"}
# Skills no scenario row proves in the world yet. May only shrink.
SCENARIO_GAPS = {"await_perch", "bed_bomb_window", "break_caged_crystal", "build_bed_pit", "shake_enderman",
                 "station"}
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
        for name, row in sorted(sc.SHEET.items()):
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
                 ("row entries that are neither a skill nor a provided effect", unknown, set()),
                 ("allowlisted names that no longer exist", orphans, set())]
        for name, actual, allowed in table:
            with self.subTest(name):
                self.assertEqual(sorted(actual), sorted(allowed))
                self.assertEqual(len(allowed) <= CEILING, True, f"{len(allowed)} over the ceiling {CEILING}")


class TheCrossProductIsWhole(unittest.TestCase):
    def test_every_base_and_condition(self):
        for base in sc.BASES:
            with self.subTest(base=base):
                self.assertIn(f"{base}__base", sc.SHEET)
        for cond, spec in sc.CONDITIONS.items():
            self.assertTrue(spec["bases"] <= set(sc.BASES), cond)
            for base in spec["bases"]:
                with self.subTest(condition=cond, base=base):
                    row = sc.SHEET[f"{base}__{cond}"]
                    self.assertEqual(row["tags"][spec["axis"]], cond)
        for surprise in sc.SURPRISES:
            with self.subTest(surprise=surprise):
                self.assertIn(surprise, sc.SHEET)

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
        self.assertIn("dead_flicker_on_respawn", sc.SHEET)
        self.assertTrue({"leaves_block_trunk", "floating_logs", "empty_chest", "bed_obstructed", "bed_in_nether",
                         "lava_under_ore", "falling_gravel"} <= set(sc.SURPRISES))

    def test_interrupted_rows_resume_and_count(self):
        for name, row in sorted(sc.SHEET.items()):
            if sc.CONDITIONS.get(row["tags"].get("timing"), {}).get("interrupt"):
                with self.subTest(name):
                    self.assertEqual(row["budget"], 2 * sc.BASES[row["tags"]["base"]]["budget"],
                                     "an interrupted run gets twice the base's time, to resume")

    def test_every_timing_row_runs(self):
        """No row stands in for a missing hook: every timing condition injects its interruption for real."""
        for name, row in sc.SHEET.items():
            if row["tags"].get("timing"):
                with self.subTest(name):
                    self.assertNotIn("hook", row)
                    self.assertTrue(sc.CONDITIONS[row["tags"]["timing"]].get("interrupt")
                                    or sc.CONDITIONS[row["tags"]["timing"]].get("tick_rate"), name)


class Tiers(unittest.TestCase):
    def test_every_row_has_a_tier(self):
        for name, row in rows():
            with self.subTest(name):
                self.assertIn(row.get("tier"), sc.TIERS)
                self.assertEqual(row["tier"], sc.tier_of(name, row))

    def test_core_is_small_and_whole(self):
        core = [n for n, r in rows() if r["tier"] == "core"]
        self.assertLessEqual(len(core), 16, "core runs on every change: keep it small")
        for base in sc.BASES:
            with self.subTest(base=base):
                self.assertIn(f"{base}__base", core)
        for name in ("lava_edge_walk", "drowning_in_a_pit", "buried_by_sand", sc.CHAIN_C[0], sc.CHAIN_C[1]):
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
              ("chest_or_tree", "common"), ("water_clutch", "common"), ("cross_lava_8", "common"),
              ("cave_escape", "common"), ("slice_nether_kit", "common"), (sc.ACCEPTANCE_D, "acceptance"),
              ("upkeep_preempts_task", "brain"), ("upkeep_waits_in_daylight", "brain"), ("food_lead", "brain"),
              ("broken_tool_best_tier", "brain"), ("plan_repair_on_event", "brain"), ("ban_then_other_source", "brain"),
              ("resume_after_combat", "brain"), ("seen_store_goes_back", "brain"), ("l3_two_goals_in_order", "brain"),
              ("ban_needs_a_failure", "brain"), ("chop_without_interrupt", "brain"), ("seen_store_forgotten", "brain"),
              ("l3_order_swapped", "brain"), ("upkeep_waits_in_daylight", "brain"), ("plan_without_events", "brain")]

    def test_placed_rows(self):
        for name, tier in self.PLACED:
            with self.subTest(name):
                self.assertEqual(sc.SCENARIOS[name]["tier"], tier)

    def test_no_tier_run_includes_acceptance(self):
        for tier in ("core", "common", "exception"):
            with self.subTest(tier):
                self.assertNotIn(sc.ACCEPTANCE_D, sc.select(sc.SCENARIOS, tier))


# Rows that still take longer than the tier's limit: real-world searches and whole boss fights (the fight bench's
# sweeps included) that no setup can shorten without changing what they measure. May only shrink.
LONG = set()  # rows over the 60 s limit still to be cut down by setup: none left (may only stay empty)
LIMIT_S = {"core": 30, "common": 60, "brain": 60, "exception": 60}


def over_limit(rows_):
    """Names of rows whose budget is over their tier's limit (acceptance has its own 30 minutes)."""
    return sorted(n for n, r in rows_.items() if r["tier"] in LIMIT_S and r["budget"] > LIMIT_S[r["tier"]])


class Budgets(unittest.TestCase):
    # (situation, a sheet) → the rows over their limit
    ROWS = [("the real sheet: only the long list", None, None),
            ("a core row at 31 s", {"x": {"tier": "core", "budget": 31}}, ["x"]),
            ("a core row at 30 s", {"x": {"tier": "core", "budget": 30}}, []),
            ("an exception row at 61 s", {"x": {"tier": "exception", "budget": 61}}, ["x"]),
            ("a brain row at 61 s", {"x": {"tier": "brain", "budget": 61}}, ["x"]),
            ("a common row at 60 s", {"x": {"tier": "common", "budget": 60}}, []),
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
    ("all", {"a_skill_no_row_proves"}, ["chop_row", "mine_row"]),
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
             ("nothing changed", "", {}, set()),
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
             ("McError: chop: finished without reaching its goal (outcome not reached in 40s, budget 45s)", False),
             ("NotAvailable: no trees found nearby, even after exploring", False),
             ("TaskStuck: chop: no progress toward its goal for 45s", False),
             ("died (McError: failed)", False), ("NavFailed: could not get to (1, 2, 3)", False)]

    # (reached?, seconds, budget, crashed?) → ok, and the note says why not
    JUDGE = [(True, 30, 45, False, True, None), (True, 45, 45, False, True, None),
             (True, 46, 45, False, False, "over budget"), (False, 10, 45, False, False, "not reached"),
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
        self.assertEqual([n for _, n in chain], list(sc.CHAIN_C))
        for name in sc.CHAIN_C:
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


if __name__ == "__main__":
    unittest.main()


class ResetBrain(unittest.TestCase):
    """bench.core.reset_brain: nothing a row learned reaches the next row (the "axe broke" leak)."""

    def test_row_state_is_dropped(self):
        from bonobo import brain as B
        from bonobo.memory import Memory
        br = B.Brain()
        shared = br.blacklist
        # (what the last row left, how to read it after the reset, the clean value)
        rows = [("a broken axe note", lambda: br.table.broken.add("axe"), lambda: br.table.broken, set()),
                ("a tier that worked", lambda: br.table.working.update(pickaxe=2), lambda: br.table.working, {}),
                ("a ban", lambda: br.blacklist.update({(1, 2, 3): 9e9}), lambda: br.blacklist, {}),
                ("a held plan", lambda: br.held.update(t1={}), lambda: br.held, {}),
                ("a committed task", lambda: setattr(br, "committed", "t1"), lambda: br.committed, None),
                ("the ban dict stays the one fight_loop holds", lambda: None, lambda: br.blacklist is shared, True)]
        for name, dirty, read, clean in rows:
            with self.subTest(name):
                dirty()
                sc.reset_brain(br, Memory(os.path.join(os.environ["MC_DATA"], "reset.json")))
                self.assertEqual(read(), clean)

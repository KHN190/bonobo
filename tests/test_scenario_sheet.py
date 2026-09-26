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
SCENARIO_GAPS = {"anvil_repair", "await_perch", "bed_bomb_window", "break_caged_crystal", "brew_fire_resistance",
                 "build_bed_pit", "collect_machine", "enchant_item", "shake_enderman", "station", "trade"}
CEILING = 11        # neither list grows past this; lower it as they shrink

COMMANDS = {"fill", "setblock", "tp", "give", "clear", "summon", "place", "time", "weather", "effect", "item", "kill",
            "spreadplayers", "locate", "execute", "gamerule", "difficulty", "forceload", "data", "damage"}
POINTS = {"A", "B", "C", "D"}
BASICS_A = ("nav", "chop", "mine_stone", "craft", "smelt", "hunt", "eat", "sleep", "loot")
L0_B = ("water_clutch", "cross_lava_8", "cave_escape", "lava_edge_walk", "buried_by_sand")


def rows():
    return sorted(sc.SCENARIOS.items())


def proven_by(entry):
    """The skills a row's `skills` entry proves: a registered name, or an effect (every skill that provides it)."""
    if entry in skillkit.REGISTRY:
        return {entry}
    return {c.name for c in skillkit.providers(entry)}


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

    def test_the_sheet_is_in_scenarios(self):
        for name in sc.SHEET:
            with self.subTest(name):
                self.assertIs(sc.SCENARIOS[name], sc.SHEET[name])

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
                        self.assertTrue(x0 - 1 <= float(x) - ox <= x1 + 1 and y0 <= float(y) - oy <= y1 + 1
                                        and z0 - 1 <= float(z) - oz <= z1 + 1, (x, y, z))

    def test_expected_failures_name_a_reason(self):
        for name, row in rows():
            if "fails" not in row:
                continue
            with self.subTest(name):
                pattern = re.compile(row["fails"], re.I)
                for generic in ("", "error", "failed", "finished without reaching its goal", "TaskStuck"):
                    self.assertIsNone(pattern.fullmatch(generic), f"{row['fails']!r} accepts {generic!r}")
                self.assertIsNone(pattern.search("chop: finished without reaching its goal"),
                                  "a verify failure is not a reason")


class EverySkillIsProven(unittest.TestCase):
    def gaps(self):
        no_verify = {n for n, c in skillkit.REGISTRY.items() if c.verify is None}
        proven = set().union(*(proven_by(e) for _, row in rows() for e in row.get("skills", ())))
        return no_verify, set(skillkit.REGISTRY) - proven

    def test_real_verify(self):
        no_verify, _ = self.gaps()
        self.assertEqual(no_verify - VERIFY_GAPS, set(), "a skill without verify: give it one (the world's effect)")
        self.assertEqual(VERIFY_GAPS - no_verify, set(), "fixed: remove it from VERIFY_GAPS (the list only shrinks)")

    def test_a_row_per_skill(self):
        _, unproven = self.gaps()
        self.assertEqual(unproven - SCENARIO_GAPS, set(), "a skill no scenario row proves: add a row")
        self.assertEqual(SCENARIO_GAPS - unproven, set(), "proven now: remove it from SCENARIO_GAPS")

    def test_the_allowlists_only_shrink(self):
        self.assertLessEqual(len(VERIFY_GAPS), CEILING)
        self.assertLessEqual(len(SCENARIO_GAPS), CEILING)
        self.assertTrue(VERIFY_GAPS <= set(skillkit.REGISTRY) and SCENARIO_GAPS <= set(skillkit.REGISTRY),
                        "an allowlisted skill that no longer exists")


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
        self.assertTrue({"leaves_block_trunk", "floating_logs", "empty_chest", "bed_obstructed", "bed_in_nether",
                         "lava_under_ore", "falling_gravel"} <= set(sc.SURPRISES))

    def test_interrupted_rows_resume_and_count(self):
        for name, row in sorted(sc.SHEET.items()):
            if row["tags"].get("timing", "").startswith("interrupt"):
                with self.subTest(name):
                    self.assertGreaterEqual(row["budget"], 2 * sc.BASES[row["tags"]["base"]]["budget"],
                                            "an interrupted run gets time to resume")

    def test_missing_hooks_are_rows_not_silence(self):
        hooked = {n: r["hook"] for n, r in sc.SHEET.items() if "hook" in r}
        self.assertTrue(hooked, "the player-takeover rows exist and say what they need")
        for name, what in hooked.items():
            with self.subTest(name):
                self.assertIn("mod:", what)


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

    def test_d_acceptance(self):
        row = sc.SCENARIOS[sc.ACCEPTANCE_D]
        self.assertEqual(row["point"], "D")
        self.assertLessEqual(row["budget"], 30 * 60, "30 minutes from a fresh start to an iron pickaxe")
        self.assertTrue(row.get("raw"), "a real world, not the bench box")
        self.assertIn("clear @p", row["setup"], "from nothing")


if __name__ == "__main__":
    unittest.main()

"""Bench rows start clean and expect what production and the scene say: a row's records reset by the one registry
(lifecycle), no copied constant, a mid-row scene change built from the scene words."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import lifecycle, reflexes, survive  # noqa: E402
from bonobo.bench import table  # noqa: E402,F401  (the sheet built: every word module imported)
from bonobo.bench import bench_brain as bb  # noqa: E402
from bonobo.bench.core import ORIGIN  # noqa: E402
from bonobo.bench.words import brain as wb, checks as wc, scene as ws  # noqa: E402
from bonobo.data import DAY_END  # noqa: E402


class RowRecords(unittest.TestCase):
    def test_reset_by_lifecycle(self):
        # must fail: a row's records outlive it (SEARCH_FLAGS was cleared only by one row's own hook)
        for store, key in ((wb.SEARCH_FLAGS, "sheltered"), (wc.BASE, "name"), (wc.INTERRUPTS, "row"),
                           (wc.RESUMED_LEFT, "row"), (wc.FIRST, "log")):
            store[key] = 1
        wb.FINDS["diamond"] = 3
        lifecycle.reset_all()
        self.assertEqual((wb.SEARCH_FLAGS, wc.BASE, wc.INTERRUPTS, wc.RESUMED_LEFT, wc.FIRST, wb.FINDS["diamond"]),
                         ({}, {}, {}, {}, {}, 0))


class FromProduction(unittest.TestCase):
    def test_day(self):
        # must fail: a copied dusk (12500) that drifts from data.DAY_END
        day = wc._is_day()
        self.assertTrue(day(type("A", (), {"get": lambda s, p: {"timeOfDay": DAY_END - 1}})(), None))
        self.assertFalse(day(type("A", (), {"get": lambda s, p: {"timeOfDay": DAY_END}})(), None))

    def test_row_values(self):
        rows = {r[0]: r for fam, params in bb.FAMILIES if fam == "upkeep" for r in params}
        bag = rows["empty_the_bag"][-1]
        self.assertEqual(bag[-1], reflexes.BAG_FULL)                     # must fail: 34 typed in
        wall = rows["shelter_wall_in"][-1][2]
        self.assertEqual(wall[-1], len(survive._pod_cells((0, 0, 0))))   # must fail: 9 typed in
        dirt = {r[0]: r for fam, params in bb.FAMILIES if fam == "dirt" for r in params}
        unreachable = dirt["night_dig_in_dirt_unreachable"]
        gap_x = unreachable[2][0][1][1]
        self.assertEqual(unreachable[-1][-1], ORIGIN[0] + gap_x)       # must fail: 10004, not the scene's gap


class Floors(unittest.TestCase):
    def test_hazard_rows_share_one_floor(self):
        import json
        from bonobo.bench.core import KEPT_HP
        from bonobo.data import CRITICAL_HP
        self.assertEqual(KEPT_HP, CRITICAL_HP + 1)
        sheet = table.SCENARIOS
        # must fail: a floor typed per row (16, 14, 10, 8, 18)
        for name in ("water_clutch", "buried_by_sand", "drowning_in_a_pit", "bed_in_nether"):
            with self.subTest(name):
                words = json.dumps(getattr(sheet[name]["check"], "__table__", None) or
                                   [getattr(p, "__table__", None) for p in getattr(sheet[name]["check"], "parts", ())])
                self.assertIn(f'"alive", {KEPT_HP}', words.replace("!", ""))

    def test_dig_in_depth(self):
        # must fail: "one down" (y < 200) judged as a dug-in body
        from bonobo.bench import bench_brain as bb2, bench_common as bc
        from bonobo.survive import DIG_IN_DEPTH
        one = {r[0]: r for fam, params in bc.FAMILIES if fam == "one" for r in params}["dig_in_night"]
        self.assertIn(("!state", "blockY", "<=", ORIGIN[1] - DIG_IN_DEPTH), one[5])
        up = {r[0]: r for fam, params in bb2.FAMILIES if fam == "upkeep" for r in params}["shelter_dig_in"]
        self.assertIn(("!state", "blockY", "<=", ORIGIN[1] - DIG_IN_DEPTH), up[-1])

    def test_slept_through(self):
        from bonobo.survive import SLEEP_FROM_TICKS
        self.assertTrue(wb.slept_through(SLEEP_FROM_TICKS, DAY_END - 1))
        self.assertFalse(wb.slept_through(SLEEP_FROM_TICKS - 1, 0))           # must fail: begun before beds work
        self.assertFalse(wb.slept_through(SLEEP_FROM_TICKS, DAY_END))         # the night not over


class OneWordPerJudgement(unittest.TestCase):
    def test_duplicates_gone(self):
        from bonobo.bench import vocab
        # must fail: a second word for a judgement another word makes (holds n, day, nothing lost, hp)
        for name in ("_inv_has", "_is_day_now", "_kept", "_hp_kept", "_count", "_regen_fed"):
            self.assertNotIn(name, vocab.REGISTRY)

    def test_progress_is_the_gain_check_read_now(self):
        from bonobo.bench.words import runs
        rows = [(5, 3, 2, True), (4, 3, 2, False)]
        for held, start, n, want in rows:
            inv = type("I", (), {"count": lambda s, t, h=held: h})()
            with mock.patch.object(wc, "BASE", {"inv": type("I", (), {"count": lambda s, t, h=start: h})()}), \
                    mock.patch.object(wc, "bag_now", lambda i=inv: i):
                self.assertEqual(runs.gained_at_least("log", n)(), want)
                self.assertEqual(wc._now(wc._gain("log", n))(), want)


class NightUnderCover(unittest.TestCase):
    def test_the_night_condition_is_covered(self):
        from bonobo.bench.bench_bases import CONDITIONS
        from bonobo.bench.core import TREE_HEIGHT
        from bonobo.bench.words.scene import scene
        cmds = scene(CONDITIONS["night"]["scene"])
        roof = [c for c in cmds if " outline" in c]
        # must fail: night on open sky (nightfall's policy cuts the job: chop__night 034104)
        self.assertTrue(roof)
        top_y = int(roof[0].split()[5])
        self.assertGreater(top_y, ORIGIN[1] + TREE_HEIGHT)

    def test_reaching_land_is_the_nights_way(self):
        from bonobo import skill
        call = type("C", (), {"contract": skill.REGISTRY["reach_land"]})()
        with mock.patch.object(skill, "CALLS", [call]):
            self.assertTrue(skill._night_way_running())      # must fail: reach_land cut at nightfall


class SceneExpect(unittest.TestCase):
    def test_counts_what_the_scene_left(self):
        got = ws.scene_expect(["fill 0 0 0 2 0 2 stone", "setblock 1 0 1 chest{Items:[]}",
                               "fill 0 1 0 2 3 2 glass hollow", "fill 0 0 0 0 0 0 dirt replace stone",
                               "fill 5 0 0 5 0 0 water"])
        self.assertEqual(got, [((1, 0, 1), (1, 0, 1), "chest", 1, 1), ((0, 0, 0), (0, 0, 0), "dirt", 1, 1),
                               ((0, 1, 0), (2, 3, 2), "glass", 26, 26), ((0, 0, 0), (2, 0, 2), "stone", 7, 7)])

    def test_every_built_row_proves_its_scene(self):
        # must fail: a row whose expect is "one block anywhere in the box"
        for name, row in table.SCENARIOS.items():
            if row.get("raw"):
                continue
            with self.subTest(name):
                self.assertTrue(row["expect"])
                self.assertFalse([e for e in row["expect"] if e[2] == "*" and e[3] >= 1 and e[4] >= 10 ** 6])


class Windows(unittest.TestCase):
    def test_drowning_first(self):
        from bonobo.bench.core import SetupInvalid
        from bonobo.bench.words import runs
        with mock.patch("bonobo.skillcore.head_underwater", lambda s=None: False):
            with self.assertRaises(SetupInvalid):           # must fail: a dry start judged a surfacing
                runs._drowning_first(None)

    def test_must_not_row_ends_when_the_brain_is_idle(self):
        from bonobo.bench import core
        from bonobo.bench.words import runs
        brain = type("B", (), {"idle_since": None})()
        with mock.patch.object(core, "BRAIN", brain):
            self.assertFalse(runs._brain_idle())
            brain.idle_since = 1.0
            self.assertTrue(runs._brain_idle())              # must fail: a constant False (the whole budget)


class CheckParts(unittest.TestCase):
    def test_a_rule_is_read_down_to_its_words(self):
        from bonobo.bench import runner
        yes = lambda api, inv: True          # noqa: E731
        no = lambda api, inv: False          # noqa: E731
        no.why = lambda: "said why"
        rule = wc._all(yes, no)
        row = wc._named_all([(rule, "the brain rule"), (yes, "the slice")])
        parts = runner.check_parts(row, None, None)
        # must fail: the rule reported as one part (seen_store__noted 044949: parts empty)
        self.assertEqual(len(parts), 4)
        self.assertEqual([v for _w, v in parts], [False, True, "said why", True])

    def test_no_scan_names_its_scans(self):
        with mock.patch.dict(wb.FINDS, {"diamond": 1, "paths": [(1.0, "/find?blocks=minecraft:diamond_ore")]}):
            check = wb._no_scan()
            self.assertIn("diamond_ore", check.why())


class SceneNow(unittest.TestCase):
    def test_sent_as_the_scene_words_build_it(self):
        words = [("fill", ("@", -1, 0, -1), ("@", 1, 1, 1), "stone"), ("stand", 0, 30)]
        sent = []
        with mock.patch("bonobo.bench.core._chat", sent.append):
            ws.scene_now(words)(None)
        self.assertEqual(sent, ws.scene(words))                          # must fail: an absolute copy


class TightDusk(unittest.TestCase):
    """brain__tight's clock from DAY_END and the night's lead (words.brain.tight_dusk_time), never a literal."""

    def test_the_night_is_due(self):
        from types import SimpleNamespace
        from bonobo import needs
        for plan_s in (3.0, 12.0, 40.0):          # a bed carried in wool, one to make, a long way through
            with self.subTest(plan_s=plan_s):
                t = wb.tight_dusk_time(plan_s)
                self.assertTrue(needs.due_now(needs.dusk_s(SimpleNamespace(time=t)), plan_s, True, False))
                self.assertLess(t, DAY_END)
        # must fail: the old literal against the bed the row carries wool for (3.0 s, 041700): not due, log first
        self.assertFalse(needs.due_now(needs.dusk_s(SimpleNamespace(time=11930)), 3.0, True, False))
        self.assertNotIn("time set 11930", [c for cs in wb.BRAIN_DIMS["dusk"].values() for c in cs])

    def test_the_order_ends_the_run(self):
        # 043641: bed first, then the whole night slept before the log — past the budget; the claim is the order
        from bonobo.bench import table
        from bonobo.bench.words import checks as wc
        row = next(r for t in table.TIERS for n, r in table.rows(t).items() if n == "brain__tight")
        done = table.dec(row["run"][1])
        cell = wb._grid_cell(tuple(row["tags"][d] for d in wb.BRAIN_DIMS))
        rule = wb.BRAIN_FAMILIES["night_first"][2](cell)[0]
        # (FIRST, the run over, the claim holds); a log first ends the run at the queue's end (the log task done)
        rows = [("nothing yet: runs on", {}, False, False),
                ("the bed first: over at once, passed", {"bed": 1.0}, True, True),
                ("must fail: the log first: failed", {"log": 1.0}, False, False),
                ("the log, then the bed: failed", {"log": 1.0, "bed": 2.0}, True, False)]
        for name, first, over, ok in rows:
            with self.subTest(name), mock.patch.dict(wc.FIRST, first, clear=True):
                self.assertEqual((done(), rule(None, None)), (over, ok))

class FullBagStart(unittest.TestCase):
    def test_a_full_bag_throws_away_from_the_tree(self):
        # must fail: valuables_full started by the grove and the chop took the thrown junk back (041819)
        for bag, fill in wb.BAG_FILL.items():
            if fill:
                with self.subTest(bag):
                    self.assertEqual(wb.BRAIN_DIMS["bag"][bag], [ws._tp(*wb.THROW_START)])

if __name__ == "__main__":
    unittest.main()

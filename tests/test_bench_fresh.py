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
        for name in ("water_clutch", "buried_by_sand", "drowning_in_a_pit", "bed_in_nether", "lava_under_ore",
                     "falling_gravel"):
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


class SceneNow(unittest.TestCase):
    def test_sent_as_the_scene_words_build_it(self):
        words = [("fill", ("@", -1, 0, -1), ("@", 1, 1, 1), "stone"), ("stand", 0, 30)]
        sent = []
        with mock.patch("bonobo.bench.core._chat", sent.append):
            ws.scene_now(words)(None)
        self.assertEqual(sent, ws.scene(words))                          # must fail: an absolute copy


if __name__ == "__main__":
    unittest.main()

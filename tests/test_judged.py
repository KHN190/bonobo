"""Checks judge the world: every row's pass condition reads the server (blocks, entities, the bag, /state), never the
bot's own record — a proxy is a readout, never the pass, save the few PASS_ALLOW names with a reason each. And the
idle mode's pure parts: its verdict, its own table."""
import unittest

from bonobo.bench import judged, runner, table
from bonobo.bench.core import TABLE
from bonobo.bench.words import fight


def all_rows():
    return {n: r for t in table.TIERS for n, r in table.rows(t).items()}


class PassReadsTheWorld(unittest.TestCase):
    def test_classes_are_apart(self):
        self.assertEqual(sorted(judged.WORLD & set(judged.PROXY)), [])

    def test_every_pass_word_is_classed(self):
        bad = {n: u for n, r in all_rows().items() for u in [judged.verdict_of_words(judged.row_pass_words(r))[1]] if u}
        self.assertEqual(bad, {})

    def test_no_pass_reads_a_proxy(self):
        bad = {n: p for n, r in all_rows().items() for p in [judged.verdict_of_words(judged.row_pass_words(r))[0]] if p}
        self.assertEqual(bad, {})

    def test_the_allowlist_is_proxies_with_reasons_all_in_use(self):
        used = set().union(*(judged.row_pass_words(r) for r in all_rows().values()))
        for word, why in judged.PASS_ALLOW.items():
            with self.subTest(word):
                self.assertIn(word, judged.PROXY)
                self.assertTrue(why.strip())
                self.assertIn(word, used)          # an entry no row needs is dropped, not kept "just in case"

    def test_rows(self):
        from bonobo.bench.words.fight import FIGHT_LOG  # noqa: F401  (the name a hand-written check reads)
        rows = [
            ("the world: hp, gone, the server's kills", [("hp_kept", 12), ("gone", ["minecraft:zombie"]),
                                                         ("killed", ["minecraft:zombie"], 1)], [], []),
            ("a call to a world helper", [("call", "hostiles", [24, ("$set", ["minecraft:zombie"])])], [], []),
            ("an allowed proxy beside the world", [("all", ("!failed_as_expected",), ("!alive",))], [], []),
            ("must fail: the fight's own kill count", [("hp_kept", 12), ("kills_by_the_fight", 1)],
             ["kills_by_the_fight"], []),
            ("must fail: the decision rhythm nested in all", [("all", ("!gain", "log", 1), ("!no_stall",))],
             ["no_stall"], []),
            ("must fail: a hand-written check reading FIGHT_LOG",
             lambda api, inv: len(FIGHT_LOG["bids"]) > 0, ["kills_by_the_fight"], []),
            ("must fail: a word in neither class", [("vibes_ok",)], [], ["vibes_ok"]),
        ]
        for why, check, proxy, unknown in rows:
            with self.subTest(why):
                got = judged.verdict_of_words(judged.row_pass_words({"check": check}))
                self.assertEqual(got, (proxy, unknown))

    def test_a_row_passing_on_a_proxy_is_caught(self):
        # must fail: fight_zombie_1 as it was — kills and stalls from the fight's own log in the pass
        row = dict(all_rows()["fight_zombie_1"], check=[("hp_kept", 12), ("gone", ["minecraft:zombie"]),
                                                       ("kills_by_the_fight", 1), ("no_stall",)])
        self.assertEqual(judged.verdict_of_words(judged.row_pass_words(row))[0], ["kills_by_the_fight", "no_stall"])


class KillStat(unittest.TestCase):
    def test_rows(self):
        rows = [("three credited", ["Steve has 3 [bk_zombie]"], 3), ("zeroed", ["Steve has 0 [bk_zombie]"], 0),
                ("must fail: no score set reads 0", ["Can't get value of bk_zombie for Steve; none is set"], 0)]
        for why, lines, want in rows:
            with self.subTest(why):
                self.assertEqual(fight.stat_count(lines), want)

    def test_scene_zeroes_the_stat(self):
        self.assertEqual(fight.kill_stat_scene("minecraft:zombie"),
                         [("cmd", "scoreboard objectives add bk_zombie minecraft.killed:minecraft.zombie"),
                          ("cmd", "scoreboard players set @p bk_zombie 0")])

    def test_every_killed_row_zeroes_its_stat(self):
        # must fail: a kill check with no objective in the scene reads 0 (or an older row's count)
        for name, r in all_rows().items():
            kinds = [k for c in (r["check"] if isinstance(r["check"], list) else []) if c[0] == "killed" for k in c[1]]
            with self.subTest(name):
                for k in kinds:
                    self.assertTrue(all(step in r["scene"] for step in fight.kill_stat_scene(k)))


class Idle(unittest.TestCase):
    def test_verdict(self):
        rows = [("idle fails the check", False, False, runner.VALID),
                ("must fail: an idle body passes", True, False, runner.INVALID),
                ("passed but died: a death fails any run", True, True, runner.VALID)]
        for why, reached, died, want in rows:
            with self.subTest(why):
                self.assertEqual(runner.idle_verdict(reached, died), want)

    def test_recorded_apart(self):
        self.assertNotEqual(runner.IDLE_TABLE, TABLE)
        t = {}
        for i in range(7):
            runner.record_idle(t, "r", "c", runner.VALID, f"n{i}")
        self.assertEqual([r["note"] for r in t["r"]["c"]], [f"n{i}" for i in range(2, 7)])
        self.assertNotIn("ok", t["r"]["c"][0])      # no readiness fields: never read as a verdict


if __name__ == "__main__":
    unittest.main()

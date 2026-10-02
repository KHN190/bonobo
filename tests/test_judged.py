"""Checks judge the world: every row's pass condition reads the server (blocks, entities, the bag, /state), never the
bot's own record — a proxy is a readout, never the pass, save the few PASS_ALLOW names with a reason each. And the
idle mode's pure parts: its verdict, its own table."""
import unittest

from bonobo.bench import judged, runner, table
from bonobo.bench.core import TABLE
from bonobo.bench.words import fight

_EXPECTED = "an expect_failure row: the expected failure IS the outcome under test; the world parts (alive, the bag " \
            "unchanged) ride beside it"
_DECISION = "a decision-table row: its claim is which answer the bot chose; the recorded outcome (hp, gap, bag) " \
            "rides beside it"
_NOTE = "a find/scout skill: its product is the note it leaves (the site), there is no other world trace of 'found'"
_SPEED = "a speed row: its claim is the time; the world effect is judged beside it"

# proxies a pass condition may still read, each with why — keep it short: every entry is a row that trusts the bot
PASS_ALLOW = {
    "failed_as_expected": _EXPECTED,
    "interrupted": "proves the bench's own injected interrupt landed (the scene), never the success",
    "slice_check": "loops and idle time have no world reading; the slice's done() is judged by the bag",
    "replans_at_most": _DECISION, "brain_rule": _DECISION, "not_banned": _DECISION, "behaviour": _DECISION,
    "no_scan": _DECISION,
    "answers_are_closed": _DECISION, "shapes_fit_the_enemy": _DECISION, "more_of_them_costs_more": _DECISION,
    "remembered_any": _NOTE, "memory": _NOTE, "stronghold_error": _NOTE,
    "found_fortress_now": _NOTE, "portal_room_found": _NOTE,
    "skill_within": _SPEED, "road_times": _SPEED,
}


def row_pass_words(row):
    """The words a table row's pass condition (its `check`) reads."""
    check = row["check"]
    if callable(check):
        return judged.callable_words(check)
    return set().union(*(judged.data_words(i) for i in check)) if check else set()


def verdict_of_words(words, allow=None):
    """Pure: (proxies the pass reads that are not allowed, words in neither class)."""
    allow = PASS_ALLOW if allow is None else allow
    known = judged.WORLD | set(judged.PROXY) | {"lambda:world"}
    return sorted(w for w in words if w in judged.PROXY and w not in allow), sorted(w for w in words if w not in known)


def all_rows():
    return {n: r for t in table.TIERS for n, r in table.rows(t).items()}


class PassReadsTheWorld(unittest.TestCase):
    def test_classes_are_apart(self):
        self.assertEqual(sorted(judged.WORLD & set(judged.PROXY)), [])

    def test_every_pass_word_is_classed(self):
        bad = {n: u for n, r in all_rows().items() for u in [verdict_of_words(row_pass_words(r))[1]] if u}
        self.assertEqual(bad, {})

    def test_no_pass_reads_a_proxy(self):
        bad = {n: p for n, r in all_rows().items() for p in [verdict_of_words(row_pass_words(r))[0]] if p}
        self.assertEqual(bad, {})

    def test_the_allowlist_is_proxies_with_reasons_all_in_use(self):
        used = set().union(*(row_pass_words(r) for r in all_rows().values()))
        for word, why in PASS_ALLOW.items():
            with self.subTest(word):
                self.assertIn(word, judged.PROXY)
                self.assertTrue(why.strip())
                self.assertIn(word, used)          # an entry no row needs is dropped, not kept "just in case"

    def test_rows(self):
        from bonobo.bench.words.fight import FIGHT_LOG  # noqa: F401  (the name a hand-written check reads)
        rows = [
            ("the world: hp, gone, the server's kills", [("alive", 12), ("gone", ["minecraft:zombie"]),
                                                         ("killed", ["minecraft:zombie"], 1)], [], []),
            ("a call to a world helper", [("call", "hostiles", [24, ("$set", ["minecraft:zombie"])])], [], []),
            ("an allowed proxy beside the world", [("all", ("!failed_as_expected",), ("!alive",))], [], []),
            ("must fail: the fight's own kill count", [("alive", 12), ("kills_by_the_fight", 1)],
             ["kills_by_the_fight"], []),
            ("must fail: the decision rhythm nested in all", [("all", ("!gain", "log", 1), ("!no_stall",))],
             ["no_stall"], []),
            ("must fail: a hand-written check reading FIGHT_LOG",
             lambda api, inv: len(FIGHT_LOG["bids"]) > 0, ["kills_by_the_fight"], []),
            ("must fail: a word in neither class", [("vibes_ok",)], [], ["vibes_ok"]),
        ]
        for why, check, proxy, unknown in rows:
            with self.subTest(why):
                got = verdict_of_words(row_pass_words({"check": check}))
                self.assertEqual(got, (proxy, unknown))

    def test_a_row_passing_on_a_proxy_is_caught(self):
        # must fail: fight_zombie_1 as it was — kills and stalls from the fight's own log in the pass
        row = dict(all_rows()["fight_zombie_1"], check=[("alive", 12), ("gone", ["minecraft:zombie"]),
                                                       ("kills_by_the_fight", 1), ("no_stall",)])
        self.assertEqual(verdict_of_words(row_pass_words(row))[0], ["kills_by_the_fight", "no_stall"])


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
            kinds = [k for c in (r["check"] if isinstance(r["check"], list) else [])
                     if isinstance(c, tuple) and c and c[0] == "killed" for k in c[1]]
            with self.subTest(name):
                for k in kinds:
                    self.assertTrue(all(step in r.get("scene", ()) for step in fight.kill_stat_scene(k)))


class Idle(unittest.TestCase):
    def test_verdict(self):
        rows = [("idle fails the check", False, False, runner.VALID),
                ("must fail: an idle body passes", True, False, runner.INVALID),
                ("must fail: passed but died — valid only by the death, flagged apart", True, True, runner.DIED_ONLY),
                ("failed and died: the check failed", False, True, runner.VALID)]
        for why, reached, died, want in rows:
            with self.subTest(why):
                self.assertEqual(runner.idle_verdict(reached, died), want)

    def test_a_hold_is_judged_at_the_end(self):
        from bonobo.bench import bench_combat, table as sc
        trapped = runner.holds(sc.SCENARIOS[bench_combat.TRAPPED_ROW]["check"])
        self.assertTrue(trapped, "kept_health holds over the window")
        # (situation, reached, died, over, hold) -> the window ends now
        rows = [("must fail: trapped_unarmed passing at t 0 ends the window", True, False, False, trapped, False),
                ("a hold that fails ends it (idle fails: valid)", False, False, False, True, True),
                ("a hold still true at the budget: judged there", True, False, True, True, True),
                ("a death ends it", True, True, False, True, True),
                ("any other check ends on a pass", True, False, False, False, True),
                ("must fail: any other check, failing, waits", False, False, False, False, False)]
        for why, reached, died, over, hold, want in rows:
            with self.subTest(why):
                self.assertEqual(runner.idle_done(reached, died, over, hold), want)

    def test_one_hp(self):
        rows = [("20 hp: 19 to take", 20.0, 19.0), ("already 1: nothing", 1.0, 0.0), ("7.5: 6.5", 7.5, 6.5),
                ("must fail: below 1 never heals", 0.5, 0.0)]
        for why, hp, want in rows:
            with self.subTest(why):
                self.assertEqual(runner.one_hp_damage(hp), want)
        self.assertEqual(runner.health_of(["knh190 has the following entity data: 20.0f"]), 20.0)

    def test_recorded_apart(self):
        self.assertNotEqual(runner.IDLE_TABLE, TABLE)
        t = {}
        for i in range(7):
            runner.record_idle(t, "r", "c", runner.VALID, f"n{i}")
        self.assertEqual([r["note"] for r in t["r"]["c"]], [f"n{i}" for i in range(2, 7)])
        self.assertNotIn("ok", t["r"]["c"][0])      # no readiness fields: never read as a verdict


if __name__ == "__main__":
    unittest.main()

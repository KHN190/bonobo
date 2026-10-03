"""E4: a step's actual seconds against its estimate (bench.e4), the bench's E4 column, and the bounded fit of the
priors (tools.fit_prices)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.bench import e4, runner  # noqa: E402
from bonobo.tools import fit_prices  # noqa: E402


def line(kind="gather", token="log", est=200, actual=10.0, ok=True, item="PRIOR_TICKS.gather_each", tag="prior",
         row="chop__base", t=100.0, rate=None):
    return {"t": t, "row": row, "kind": kind, "token": token, "est": est, "actual_s": actual, "ok": ok,
            "why": None if ok else "interrupted: X", "price": {"work": f"{item}:{tag}"},
            "cond": {} if rate is None else {"tick_rate": rate}}


class Ratio(unittest.TestCase):
    def test_rows(self):
        # (situation, line) → actual / estimate
        rows = [("as priced", line(), 1.0),
                ("twice as long", line(actual=20.0), 2.0),
                ("a row under a 2× clock: game seconds", line(actual=5.0, rate=40), 1.0),
                ("must fail: an interrupted step is no sample", line(ok=False), None),
                ("must fail: no estimate", line(est=0), None)]
        for name, ln, want in rows:
            with self.subTest(name):
                got = e4.ratio(ln)
                self.assertEqual(None if got is None else round(got, 6), want)


class RowVerdict(unittest.TestCase):
    def test_rows(self):
        # (situation, lines) → (holds, misses)
        rows = [("every step in", [line(), line(actual=19.0)], (True, [])),
                ("the band's ends are in", [line(actual=5.0), line(actual=20.0)], (True, [])),
                ("must fail: a step 2.5× its estimate", [line(), line(actual=25.0)], (False, ["gather log: 2.50"])),
                ("must fail: a step at a third", [line(actual=10 / 3)], (False, ["gather log: 0.33"])),
                ("no ok step: no verdict", [line(ok=False)], (None, []))]
        for name, lines, want in rows:
            with self.subTest(name):
                self.assertEqual(e4.row_verdict(lines), want)


class Items(unittest.TestCase):
    def test_rows(self):
        # (situation, ratios of one item's steps) → (n kept, holds, dropped)
        rows = [("three in", [1.0, 1.2, 0.9], (3, True, [])),
                ("must fail: the mean out", [2.5, 3.0, 2.2], (3, False, [])),
                ("must fail: the mean in, one sample past the spread", [0.5, 1.0, 4.5], (3, False, [])),
                ("a stuck step dropped, never fitted", [1.0, 1.1, 6.0], (2, True, [6.0]))]
        for name, ratios, want in rows:
            with self.subTest(name):
                got = e4.item_verdicts([line(actual=10.0 * r) for r in ratios])["PRIOR_TICKS.gather_each"]
                self.assertEqual((got["n"], got["holds"], got["dropped"]), want)


class Fit(unittest.TestCase):
    def test_fitted(self):
        # (situation, current, origin, gmean, n, tag, a speed) → the new value or None
        rows = [("half again as long: raised", 60, 60, 1.5, 3, "prior", False, 90),
                ("one fit moves at most ×2", 60, 60, 3.0, 5, "prior", False, 120),
                ("never past ×4 of the first prior", 200, 60, 2.0, 5, "measured", False, 240),
                ("a speed: walks took longer, the speed falls", 0.12, 0.12, 1.5, 4, "prior", True, 0.08),
                ("must fail: a game price is never fitted", 200, 200, 1.5, 9, "game", False, None),
                ("must fail: a policy is never fitted", 0, 0, 2.0, 9, "policy", False, None),
                ("must fail: two samples are too few", 60, 60, 1.5, 2, "prior", False, None),
                ("must fail: under a tenth off: left", 60, 60, 1.05, 9, "prior", False, None)]
        for name, cur, origin, g, n, tag, inverse, want in rows:
            with self.subTest(name):
                self.assertEqual(fit_prices.fitted(cur, origin, g, n, tag, inverse), want)

    def test_written_into_the_sources(self):
        src = ('PRIOR_TICKS = {"craft": 60, "smelt_each": 200,\n               "gather_each": 60}\n'
               'GROW_S = {"crop": 900, "animal": 1200}\n'
               'PRICE_SOURCE = {\n    "knowledge.PRIOR_TICKS": {\n        "craft": "prior", "smelt_each": "game", '
               '"gather_each": "prior"},\n    "knowledge.GROW_S": {"crop": "prior", "animal": "game"},\n'
               '    "data.WALK_BLOCKS_PER_TICK": "prior",\n}\nPRIOR_ORIGIN = {}     # first priors\n')
        values = {"PRIOR_TICKS.gather_each": (60, False), "PRIOR_TICKS.smelt_each": (200, False),
                  "knowledge.GROW_S.crop": (900, False), "data.WALK_BLOCKS_PER_TICK": (0.12, True)}
        report = {"PRIOR_TICKS.gather_each": {"tag": "prior", "n": 4, "gmean": 1.5},
                  "PRIOR_TICKS.smelt_each": {"tag": "game", "n": 9, "gmean": 1.6},
                  "knowledge.GROW_S.crop": {"tag": "prior", "n": 3, "gmean": 0.8},
                  "data.WALK_BLOCKS_PER_TICK": {"tag": "prior", "n": 5, "gmean": 1.2}}
        changes = fit_prices.fit_plan(report, values, {})
        self.assertEqual(changes, {"PRIOR_TICKS.gather_each": 90, "knowledge.GROW_S.crop": 720,
                                   "data.WALK_BLOCKS_PER_TICK": 0.1})          # must fail: the game price moved
        first = {k: values[k][0] for k in changes}
        ns = {}
        exec(fit_prices.fitted_knowledge(src, changes, first), ns)
        self.assertEqual((ns["PRIOR_TICKS"]["gather_each"], ns["GROW_S"]["crop"],
                          ns["PRICE_SOURCE"]["knowledge.PRIOR_TICKS"]["gather_each"],
                          ns["PRICE_SOURCE"]["knowledge.GROW_S"]["crop"], ns["PRICE_SOURCE"]["data.WALK_BLOCKS_PER_TICK"],
                          ns["PRIOR_ORIGIN"]),
                         (90, 720, "measured", "measured", "measured", first))
        again = fit_prices.fitted_knowledge(fit_prices.fitted_knowledge(src, changes, first),
                                            {"PRIOR_TICKS.gather_each": 120}, {"PRIOR_TICKS.gather_each": 90})
        ns2 = {}
        exec(again, ns2)
        self.assertEqual(ns2["PRIOR_ORIGIN"]["PRIOR_TICKS.gather_each"], 60)    # the first prior kept, not the fitted one
        self.assertIn("WALK_BLOCKS_PER_TICK = 0.1   #",
                      fit_prices.fitted_data("WALK_BLOCKS_PER_TICK = 0.12   # walk\n", changes))
        toml = 'seek_prior_s = 300.0       # [prior] seconds to find one\n'
        self.assertEqual(fit_prices.fitted_play(toml, {"plan.seek_prior_s": 450.0}),
                         'seek_prior_s = 450.0       # [measured] seconds to find one\n')
        self.assertEqual(fit_prices.fitted_play(toml, {}), toml)                 # must fail: rewritten unasked
        drain = 'food_drain_s = 80.0                  # seconds of ordinary activity per point\n'
        self.assertEqual(fit_prices.fitted_play(drain, {"risk.food_drain_s": 40.0}),
                         'food_drain_s = 40.0                  # [measured] seconds of ordinary activity per point\n')
        self.assertIn("ROUTE_FACTOR = 1.8 ",
                      fit_prices.fitted_data("ROUTE_FACTOR = 1.5            # route\n", {"data.ROUTE_FACTOR": 1.8}))
        self.assertEqual(fit_prices.fitted(80.0, 80.0, 2.0, 5, "prior", True), 40.0)   # draining faster: fewer s a point


class ByPart(unittest.TestCase):
    """A step measured by phase: each part against its own price item."""

    def measured(self, est_parts, actual_parts, actual_s):
        ln = line(est=sum(est_parts.values()), actual=actual_s)
        ln.update(est_parts=est_parts, actual_parts=actual_parts,
                  price={"work": "PRIOR_TICKS.gather_each:prior", "walk": "data.WALK_BLOCKS_PER_TICK:prior",
                         "seek": "PRIOR_TICKS.unknown_walk:prior"})
        return ln

    def test_rows(self):
        # (situation, line) → {item: ratio}
        rows = [("work and a known walk apart",
                 self.measured({"work": 200, "walk": 100}, {"work": 20.0, "walk": 2.5, "seek": 0.0}, 22.5),
                 {"PRIOR_TICKS.gather_each": 2.0, "data.WALK_BLOCKS_PER_TICK": 0.5}),
                ("a thing nowhere known: the walk is the seek's",
                 self.measured({"work": 200, "seek": 400}, {"work": 10.0, "walk": 15.0, "seek": 5.0}, 30.0),
                 {"PRIOR_TICKS.gather_each": 1.0, "PRIOR_TICKS.unknown_walk": 1.0}),
                ("must fail: no phases: the whole step is the work's", line(actual=20.0),
                 {"PRIOR_TICKS.gather_each": 2.0})]
        for name, ln, want in rows:
            with self.subTest(name):
                self.assertEqual({k: round(v, 6) for k, v in e4.part_ratios(ln).items()}, want)

    def test_the_games_own_clock(self):
        ln = line(est=18000, actual=12.0, kind="await", token="minecraft:wheat", item="knowledge.GROW_S.crop")
        ln["game_s"] = 900.0                   # a sprinted clock: 12 s of wall, 900 s of the game's
        self.assertEqual(round(e4.ratio(ln), 6), 1.0)                                # must fail: 12 / 900
        self.assertEqual(e4.part_ratios(ln), {"knowledge.GROW_S.crop": 1.0})


class ThePriceLineByPart(unittest.TestCase):
    def test_rows(self):
        from bonobo import dispatch
        from bonobo.planner import Step
        st = Step("gather", "log", 2, {})
        st.est, st.parts = 1300, {"work": 900, "walk": 200, "seek": 0, "chance": 0}
        ln = dispatch.price_line(st, False, "minecraft:overworld", 70.0, None, "chop__base",
                                 {"walk": 12.0, "seek": 0.0, "arrived_s": 12.5})
        self.assertEqual(ln["est_parts"], {"work": 900, "walk": 200, "hunger": 200})      # the rest: hunger's share
        self.assertEqual(ln["actual_parts"], {"walk": 12.0, "seek": 0.0, "work": 58.0})
        self.assertEqual((ln["price"]["work"], ln["price"]["walk"], ln["price"]["hunger"], ln["arrived_s"]),
                         ("PRIOR_TICKS.gather_each:prior", "data.WALK_BLOCKS_PER_TICK:prior", "risk.food_drain_s:prior",
                          12.5))
        self.assertNotIn("seek", ln["price"])                     # must fail: a zero part priced


class TheWalkSplit(unittest.TestCase):
    """The path the /state reads traced: the route factor and the walking speed apart, and the bar's drain."""

    def test_route_and_speed(self):
        from bonobo.data import ROUTE_FACTOR, WALK_BLOCKS_PER_TICK
        from bonobo.game import TICKS_PER_S
        speed = WALK_BLOCKS_PER_TICK * TICKS_PER_S
        # (situation, path, straight, walk seconds) → {item: ratio}
        rows = [("the route as priced, the speed as priced", 15.0 * ROUTE_FACTOR, 15.0, 15.0 * ROUTE_FACTOR / speed,
                 {"data.ROUTE_FACTOR": 1.0, "data.WALK_BLOCKS_PER_TICK": 1.0}),
                ("a straight road walked twice as fast", 15.0, 15.0, 15.0 / speed / 2,
                 {"data.ROUTE_FACTOR": round(1 / ROUTE_FACTOR, 6), "data.WALK_BLOCKS_PER_TICK": 0.5}),
                ("must fail: a step of 2 blocks tells nothing of its route", 2.0, 2.0, 1.0, {}),
                ("must fail: no path traced", None, 15.0, 5.0, {})]
        for name, path, straight, walk_s, want in rows:
            with self.subTest(name):
                ln = {"path_m": path, "straight_m": straight}
                self.assertEqual({k: round(v, 6) for k, v in e4.walk_split(ln, walk_s).items()}, want)

    def test_the_drain(self):
        from bonobo import beliefs
        drain = beliefs.value("risk.food_drain_s")
        # (situation, lines) → the priced drain against the measured one
        rows = [("as priced: one point every drain seconds", [dict(line(actual=drain), bar_drop=1.0)], 1.0),
                ("twice as fast over two steps, one of them dropping nothing",
                 [dict(line(actual=drain / 2), bar_drop=0.0), dict(line(actual=drain / 2), bar_drop=2.0)], 2.0),
                ("must fail: no bar read: no verdict", [line()], None)]
        for name, lines, want in rows:
            with self.subTest(name):
                g, _n = e4.drain_ratio(lines)
                self.assertEqual(None if g is None else round(g, 6), want)

    def test_the_odometer(self):
        from unittest import mock
        from bonobo import api, dispatch
        with mock.patch.object(api, "STATE", api.ApiState()), mock.patch.object(api, "api") as wire:
            for x, food, dim in ((0.0, 20, "o"), (3.0, 19, "o"), (7.0, 19, "o"), (500.0, 19, "o"), (501.0, 18, "n")):
                wire.return_value = {"x": x, "y": 64.0, "z": 0.0, "food": food, "saturation": 0.0, "dimension": dim}
                api.get("/state")
                if x == 0.0:
                    start = {"moved": api.STATE.moved_m, "feet": api.STATE.feet_seen, "bar": api.STATE.bar_seen}
            got = dispatch.step_moved(start, api.STATE)
        # 3 + 4 walked; the 493-block jump (a teleport) and the dimension change are no walk (must fail: 501)
        self.assertEqual((got["path_m"], got["straight_m"], got["bar_drop"]), (7.0, 501.0, 2.0))


class BenchColumn(unittest.TestCase):
    def test_e4_status(self):
        t = runner.record({}, "chop__base", "k", True, 9.0)
        self.assertEqual(runner.e4_status(t, "chop__base", "k"), "-")         # nothing priced
        t = runner.record(t, "chop__base", "k", True, 9.0, e4=(False, ["gather log: 2.50"]))
        self.assertEqual(runner.e4_status(t, "chop__base", "k"), "out")       # must fail: read as in
        self.assertEqual(t["chop__base"]["k"][-1]["e4_miss"], ["gather log: 2.50"])
        t["chop__base"]["k"][-1]["t"] -= 1
        t = runner.record(t, "chop__base", "k", True, 9.0, e4=(True, []))
        self.assertEqual(runner.e4_status(t, "chop__base", "k"), "in")

    def test_row_reads_its_own_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "prices.jsonl")
            with open(path, "w") as fh:
                for ln in (line(t=50.0, actual=99.0), line(row="other", actual=99.0), line(t=150.0), "not json"):
                    fh.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")
            # an earlier run's and another row's lines are not this run's (must fail: either would read out)
            self.assertEqual(runner.row_e4("chop__base", 100.0, path), (True, []))


if __name__ == "__main__":
    unittest.main()

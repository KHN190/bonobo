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
        # (situation, current, origin, gmean, n, tag) → the new prior or None
        rows = [("half again as long: raised", 60, 60, 1.5, 3, "prior", 90),
                ("one fit moves at most ×2", 60, 60, 3.0, 5, "prior", 120),
                ("never past ×4 of the first prior", 200, 60, 2.0, 5, "measured", 240),
                ("must fail: a game price is never fitted", 200, 200, 1.5, 9, "game", None),
                ("must fail: a policy is never fitted", 0, 0, 2.0, 9, "policy", None),
                ("must fail: two samples are too few", 60, 60, 1.5, 2, "prior", None),
                ("must fail: under a tenth off: left", 60, 60, 1.05, 9, "prior", None)]
        for name, cur, origin, g, n, tag, want in rows:
            with self.subTest(name):
                self.assertEqual(fit_prices.fitted(cur, origin, g, n, tag), want)

    def test_written_into_the_source(self):
        src = ('PRIOR_TICKS = {"craft": 60, "smelt_each": 200,\n               "gather_each": 60}\n'
               'PRICE_SOURCE = {\n    "knowledge.PRIOR_TICKS": {\n        "craft": "prior", "smelt_each": "game", '
               '"gather_each": "prior"},\n}\nPRIOR_ORIGIN = {}     # first priors\n')
        priors = {"craft": 60, "smelt_each": 200, "gather_each": 60}
        report = {"PRIOR_TICKS.gather_each": {"tag": "prior", "n": 4, "gmean": 1.5},
                  "PRIOR_TICKS.smelt_each": {"tag": "game", "n": 9, "gmean": 1.6}}
        changes = fit_prices.fit_plan(report, priors, {})
        self.assertEqual(changes, {"gather_each": 90})                    # must fail: the game price moved
        out = fit_prices.fitted_source(src, changes, priors)
        ns = {}
        exec(out, ns)
        self.assertEqual((ns["PRIOR_TICKS"], ns["PRICE_SOURCE"]["knowledge.PRIOR_TICKS"]["gather_each"],
                          ns["PRIOR_ORIGIN"]),
                         ({"craft": 60, "smelt_each": 200, "gather_each": 90}, "measured", {"gather_each": 60}))
        again = fit_prices.fitted_source(out, {"gather_each": 120}, ns["PRIOR_TICKS"])
        ns2 = {}
        exec(again, ns2)
        self.assertEqual(ns2["PRIOR_ORIGIN"], {"gather_each": 60})       # the first prior kept, not the fitted one


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

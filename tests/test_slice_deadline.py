"""K9: one deadline per slice row — its loop's end and the runner's stop are the slice's limit in seconds, the
verdict the row's budget (accept_fresh_iron_pickaxe: a 600 s slice stopped at its 320 s budget; its message said "not
done after 10.0 min" at 23 s)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.bench import table  # noqa: E402
from bonobo.bench.words import runs  # noqa: E402


def slice_rows():
    return [(tier, name, row) for tier in table.TIERS for name, row in table.rows(tier).items()
            if isinstance(row.get("run"), (tuple, list)) and row["run"] and row["run"][0] == "slice"]


class EverySliceHasOneDeadline(unittest.TestCase):
    def test_rows(self):
        rows = slice_rows()
        self.assertTrue(rows)
        for tier, name, row in rows:
            with self.subTest(name):
                run, limit = table.slice_deadline(row, tier)
                built = table.build(row, tier)
                # must fail: accept_fresh_iron_pickaxe stopped at its 320 s budget under its 600 s slice
                self.assertEqual(built["limit"], limit)
                self.assertAlmostEqual(runs.slice_limit_s(run[2]), limit)
                self.assertEqual(built["budget"], row.get("budget") or (
                    table.est_budget(row, built["setup"]) if "est" in row else table.row_budget(row)))

    def test_the_run_stops_at_the_limit_and_is_judged_by_the_budget(self):
        from unittest import mock
        from bonobo.bench import runner
        sc = {"budget": 320, "limit": 600, "run": lambda ctx: None}
        from bonobo import skillcore
        with mock.patch.object(runner, "_watchdog") as dog, mock.patch.object(runner, "open_window"), \
                mock.patch.object(skillcore, "really_dead", return_value=False):
            runner._run_row(sc, lambda: None, None)
        self.assertEqual(dog.call_args.args[0], 600)          # must fail: stopped at the budget
        self.assertEqual(runner.judge(True, 400, sc["budget"]),
                         (False, "outcome reached but over budget: 400s > 320s"))


class WhyASliceEnded(unittest.TestCase):
    def test_rows(self):
        rows = [("must fail: the queue ended at 23 s of 600: said so, not the limit", 23.0, 600, True,
                 "slice its queue ended after 23 s of 600 s, last decision: d"),
                ("the limit reached", 600.0, 600, True, "slice not done after 600 s, last decision: d"),
                ("the limit reached, the queue live", 600.0, 600, False, "slice not done after 600 s, last decision: d")]
        for name, elapsed, limit, over, want in rows:
            with self.subTest(name):
                self.assertEqual(runs.slice_unfinished(elapsed, limit, over, "d"), want)


if __name__ == "__main__":
    unittest.main()

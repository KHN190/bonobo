"""K9: one deadline per slice row — its loop's end and the runner's budget are the slice's limit in seconds
(accept_fresh_iron_pickaxe: a 600 s slice under a 320 s budget; its message said "not done after 10.0 min" at 23 s)."""
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
                # must fail: accept_fresh_iron_pickaxe's budget 320 under its 600 s slice
                self.assertEqual(built["budget"], limit)
                self.assertAlmostEqual(runs.slice_limit_s(run[2]), limit)


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

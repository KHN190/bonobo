"""Helpers that were written twice and now live in one place: a pure table each (normal, edge, must-fail)."""
import unittest

from bonobo import data


class CannotReach(unittest.TestCase):
    """data.cannot_reach: the cells a mod answer names as "cannot reach x, y, z" (end and skills read it)."""

    ROWS = [("one cell", "cannot reach 1, 64, -3", {(1, 64, -3)}),
            ("two cells in one answer", "2 of 3 steps failed: cannot reach 1, 2, 3; cannot reach -4, 5, -6",
             {(1, 2, 3), (-4, 5, -6)}),
            ("must fail: another refusal names no cell", "no path found (108 positions explored)", set()),
            ("must fail: no message", None, set()),
            ("must fail: a cell without the words", "stuck at 1, 2, 3", set())]

    def test_rows(self):
        for name, message, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(data.cannot_reach(message), want)

    def test_every_reader_uses_it(self):
        from bonobo import end
        failed = [{"status": "failed", "message": "cannot reach 1, 2, 3"},
                  {"status": "succeeded", "message": "cannot reach 9, 9, 9"}]
        self.assertEqual(end.unreachable(failed), {(1, 2, 3)})


if __name__ == "__main__":
    unittest.main()

"""static_check.py inside the suite: every rule's rows (holding and must-fail), then the scan of bonobo/: no rule over
its known count (check/static_known.txt, which only falls)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import static_check  # noqa: E402


class Rules(unittest.TestCase):
    def test_rows(self):
        for rule, srcs, fires in static_check.ROWS:
            with self.subTest(rule=rule, srcs=srcs):
                got = static_check.run_row(rule, srcs)
                self.assertEqual(bool(got), fires, got)

    def test_every_rule_has_a_must_fail_row(self):
        self.assertEqual({r for r, _s, fires in static_check.ROWS if fires}, set(static_check.RULES))


class Bonobo(unittest.TestCase):
    def test_no_rule_over_its_known_count(self):
        self.assertEqual(static_check.grew(static_check.hits(), static_check.known()), [])

    def test_the_ratchet(self):
        found = {"R1": [1, 2], "R2": []}
        self.assertEqual(static_check.grew(found, {"R1": 2}), [])
        self.assertEqual(static_check.grew(found, {"R1": 1}), [("R1", 2, 1)])      # must fail: a new hit


if __name__ == "__main__":
    unittest.main()

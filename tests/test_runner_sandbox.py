"""The test runner's data directories: one per test process, under the run's one root."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import runtests  # noqa: E402


class OwnDataDir(unittest.TestCase):
    def test_each_process_its_own(self):
        try:
            a, b = runtests.sandbox()["MC_DATA"], runtests.sandbox()["MC_DATA"]
            # (situation, got, want)
            rows = [("must fail: two processes share one dir (a round's queue read by another's)", a != b, True),
                    ("both under the run's root (one cleanup)", {os.path.dirname(a), os.path.dirname(b)},
                     {runtests._SANDBOX}),
                    ("each empty", (os.listdir(a), os.listdir(b)), ([], []))]
            for name, got, want in rows:
                with self.subTest(name):
                    self.assertEqual(got, want)
        finally:
            runtests.cleanup()
            runtests._SANDBOX = None


if __name__ == "__main__":
    unittest.main()

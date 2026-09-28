"""The type layer holds: pyright's error count never grows past the committed baseline (a ratchet: lower it when it
shrinks, never raise it), and the Literal vocabularies in bonobo/shapes.py name exactly what the tables hold.
The pyright row runs when a `pyright` binary is on PATH (or MC_PYRIGHT names one), skipped otherwise."""
import json
import os
import shutil
import subprocess
import sys
import typing
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bonobo import arbiter, retry, shapes  # noqa: E402
from bonobo.data import EXCEPTIONS  # noqa: E402

BASELINE = os.path.join(ROOT, "tests", "data", "pyright_baseline.txt")
PYRIGHT = os.environ.get("MC_PYRIGHT") or shutil.which("pyright")


def error_count(output):
    """Pure: pyright's error count from its --outputjson answer."""
    return json.loads(output)["summary"]["errorCount"]


class Vocabulary(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(set(typing.get_args(shapes.Source)), set(arbiter.RESUME_OF))
        self.assertLessEqual({s for _c, s in EXCEPTIONS.values()}, set(typing.get_args(shapes.Source)))

    def test_rules_and_causes(self):
        self.assertEqual(set(typing.get_args(shapes.Rule)), set(arbiter.RESUME_RULES))
        self.assertEqual(set(typing.get_args(shapes.Cause)),
                         {c for c, _s in EXCEPTIONS.values()} | set(retry.BACKSTOP))

    def test_count_reader(self):
        self.assertEqual(error_count('{"summary": {"errorCount": 7, "warningCount": 1}}'), 7)


@unittest.skipUnless(PYRIGHT, "pyright is not installed")
class Ratchet(unittest.TestCase):
    def test_errors_never_grow(self):
        out = subprocess.run([PYRIGHT, "--outputjson"], cwd=ROOT, capture_output=True, text=True, timeout=600)
        n, baseline = error_count(out.stdout), int(open(BASELINE).read().split()[0])
        self.assertLessEqual(n, baseline, f"pyright: {n} errors, baseline {baseline} (pyrightconfig.json): fix the new "
                                          f"ones; the baseline only goes down")
        if n < baseline:
            print(f"pyright: {n} errors < baseline {baseline}: lower tests/data/pyright_baseline.txt to {n}")


if __name__ == "__main__":
    unittest.main()

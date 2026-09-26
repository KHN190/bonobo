"""The decision tape is capped and single: it reached 50 MB, and a second copy doubled that for no gain."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import tape  # noqa: E402

def line(i):
    """One round, padded so every round is the same size whatever its number."""
    return json.dumps({"round": i, "pad": "x" * (300 - len(str(i)))}) + "\n"


LINE = len(line(0).encode())          # fixture: the size of every round here


def rounds(n):
    return "".join(line(i) for i in range(n))


# (situation, rounds on the tape, cap in bytes) → rounds kept (the newest, in order)
TRIMS = [
    ("over the cap: the newest that fit", 300, 10_000, list(range(300 - 10_000 // LINE, 300))),
    ("under the cap: all of it", 5, 10_000, list(range(5))),
    ("exactly one round fits", 300, LINE, [299]),
    ("not even one round fits", 300, LINE - 1, []),
    ("an empty tape stays empty", 0, 10_000, []),
]


class Trimming(unittest.TestCase):
    def test_rounds_kept(self):
        for name, n, cap, kept in TRIMS:
            with self.subTest(name):
                path = os.path.join(tempfile.mkdtemp(), "decisions.jsonl")
                with open(path, "w") as f:
                    f.write(rounds(n))
                self.assertEqual(tape.trim(path, cap), len(kept))
                self.assertEqual([json.loads(l)["round"] for l in open(path)], kept)
                self.assertEqual(os.listdir(os.path.dirname(path)), ["decisions.jsonl"], "no second copy, no leftover")

    def test_a_missing_tape(self):
        for name, path in (("no file", os.path.join(tempfile.mkdtemp(), "none.jsonl")),
                           ("no directory", "/nonexistent-dir/none.jsonl")):
            with self.subTest(name):
                self.assertEqual(tape.trim(path, 10_000), 0)


if __name__ == "__main__":
    unittest.main()

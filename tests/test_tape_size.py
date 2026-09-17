"""The decision tape is capped and single: it reached 50 MB, and a second copy doubled that for no gain."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import tape  # noqa: E402


class Trimming(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "decisions.jsonl")
        with open(self.path, "w") as f:
            for i in range(300):
                f.write(json.dumps({"round": i, "pad": "x" * 300}) + "\n")

    def test_it_keeps_the_newest_rounds_within_the_cap(self):
        tape.trim(self.path, 10_000)
        self.assertLessEqual(os.path.getsize(self.path), 10_000)
        rows = [json.loads(l) for l in open(self.path)]
        self.assertEqual(rows[-1]["round"], 299)
        self.assertEqual(rows, sorted(rows, key=lambda r: r["round"]))

    def test_it_leaves_no_second_copy(self):
        tape.trim(self.path, 10_000)
        self.assertFalse(os.path.exists(self.path + ".1"))

    def test_the_cap_is_megabytes_not_hundreds(self):
        self.assertLessEqual(tape.MAX_BYTES, 4 << 20)


if __name__ == "__main__":
    unittest.main()

"""Every invariant ID is bound: a test docstring names it in brackets, or a bench row's tags["proves"]; the unbound set only shrinks."""
import ast
import glob
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "fixtures", "invariants_baseline.json")
TEST, BENCH, STATIC, RUNTIME = "test", "bench", "static", "runtime"
INVARIANTS = {
    "G1": BENCH, "G2": STATIC, "G3": TEST, "G4": BENCH, "G5": TEST, "G6": TEST, "G7": TEST, "G8": TEST, "G9": TEST,
    "K1": STATIC, "K2": STATIC, "K3": TEST, "K4": STATIC, "K5": STATIC, "K6": STATIC, "K7": STATIC,
    "D1": TEST, "D2": BENCH, "D3": STATIC, "D4": TEST, "D5": TEST, "D6": TEST, "D7": BENCH, "D8": TEST, "D9": TEST,
    "D10": TEST, "D11": RUNTIME, "D12": TEST,
    "P1": TEST, "P2": STATIC, "P3": TEST, "P4": TEST,
    "S1": BENCH, "S2": BENCH, "S3": BENCH, "S4a": BENCH, "S4b": BENCH, "S5": BENCH, "S6": BENCH, "S7": BENCH,
    "S8": TEST, "S9": TEST,
    "E1": STATIC, "E2": BENCH, "E3": BENCH, "E4": RUNTIME, "E5a": TEST, "E5b": TEST, "E5c": TEST, "E5d": TEST,
    "E6": TEST, "E7": TEST, "E8": TEST,
    "R1": TEST, "R2": TEST, "R3": TEST, "R4": TEST, "R5": TEST,
}
ID = r"((?:[GKDPSER])\d+[a-d]?)"


def bound_in_tests():
    out = set()
    for path in glob.glob(os.path.join(HERE, "test_*.py")):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            doc = ast.get_docstring(node) if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) else None
            if doc:
                out |= set(re.findall(r"\[" + ID + r"\]", doc))
    return out


def proved_in_bench():
    from bonobo.bench import table
    out = set()
    for tier in table.TIERS:
        for row in table.rows(tier).values():
            proves = (row.get("tags") or {}).get("proves")
            out |= {proves} if isinstance(proves, str) else set(proves or ())
    return out


def unbound(invariants, tested, proved):
    return sorted(name for name, layer in invariants.items()
                  if name not in (proved if layer == BENCH else tested))


class Bound(unittest.TestCase):
    def test_the_unbound_set_only_shrinks(self):
        now = unbound(INVARIANTS, bound_in_tests(), proved_in_bench())
        if not os.path.exists(BASELINE):
            with open(BASELINE, "w", encoding="utf-8") as f:
                json.dump(now, f, indent=1)
            self.skipTest(f"baseline written: {len(now)} unbound")
        with open(BASELINE, encoding="utf-8") as f:
            before = set(json.load(f))
        self.assertEqual(sorted(set(now) - before), [])
        if before - set(now):
            with open(BASELINE, "w", encoding="utf-8") as f:
                json.dump(now, f, indent=1)

    def test_a_test_names_only_declared_ids(self):
        self.assertEqual(sorted(bound_in_tests() - set(INVARIANTS)), [])

    def test_rows(self):
        table = {"G1": BENCH, "S3": BENCH, "D6": TEST, "E5b": TEST}
        self.assertEqual(unbound(table, {"D6"}, {"G1"}), ["E5b", "S3"])
        self.assertEqual(unbound(table, {"D6", "E5b"}, {"G1", "S3"}), [])


if __name__ == "__main__":
    unittest.main()

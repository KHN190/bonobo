"""A feature that switches itself off must say so.

Route pricing was off for a whole session and every test passed: `reach_s` read the blocks between us and the
target, the call was not on the tape, the handler returned None, and "we could not price it" was indistinguishable
from "it costs nothing special". The bench then measured a bed behind lava at exactly the price of a bed on flat
ground and nobody could see why.

A world read inside a decision is allowed to fail — the game restarts, a recorded round has no such call — but
the failure has to leave a mark (`api.swallowed`). This scans for the shape rather than the case: any handler in
the deciding modules that catches a world read and quietly returns nothing.
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api  # noqa: E402

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")
# Where a silent failure changes a DECISION rather than an action: the brain's pricing and the value module.
DECIDING = ("brain.py", "value.py", "actions.py", "priority.py", "pool.py", "solve.py", "memory.py")
WORLD = {"find", "entities", "region_around", "Region", "Snapshot", "Inventory", "status", "container",
         "dark_spots", "mod_features"}
# Read-only bookkeeping: failing to read these changes nothing about what is chosen.
ALLOWED = {"note_yield_of"}


def quiet_handlers(path):
    """[(line, function, what it swallows)] for handlers that catch a world read and return nothing."""
    src = open(path).read()
    tree = ast.parse(src)
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            reads = {getattr(c.func, "attr", getattr(c.func, "id", None))
                     for c in ast.walk(ast.Module(body=node.body, type_ignores=[]))
                     if isinstance(c, ast.Call)} & WORLD
            if not reads:
                continue
            for h in node.handlers:
                marked = any(isinstance(c, ast.Call) and getattr(c.func, "attr", None) == "swallowed"
                             for c in ast.walk(ast.Module(body=h.body, type_ignores=[])))
                silent = all(isinstance(st, ast.Pass) or
                             (isinstance(st, ast.Return) and
                              (st.value is None or (isinstance(st.value, ast.Constant)
                                                    and st.value.value in (None, 0, 0.0, False))))
                             for st in h.body)
                if silent and not marked:
                    out.append((node.lineno, fn.name, ",".join(sorted(reads))))
    return out


class NothingTurnsItselfOffInSilence(unittest.TestCase):
    def test_every_swallowed_world_read_in_a_decision_leaves_a_mark(self):
        found = []
        for name in DECIDING:
            path = os.path.join(PKG, name)
            if not os.path.exists(path):
                continue
            for line, fn, reads in quiet_handlers(path):
                if fn not in ALLOWED:
                    found.append(f"{name}:{line} {fn}() swallows [{reads}]")
        self.assertEqual(found, [], "these decide something and hide it when the world does not answer")

    def test_the_mark_is_countable(self):
        """However many times it is ignored, that is how many the tally shows."""
        for times in (1, 3, 7):
            api.SWALLOWED.clear()
            for _ in range(times):
                got = api.swallowed("a test", ValueError("no"))
            self.assertEqual(api.SWALLOWED["a test: ValueError"], times)
            self.assertIsNone(got, "a handler can return it directly")


if __name__ == "__main__":
    unittest.main()

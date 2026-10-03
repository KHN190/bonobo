"""Layering. A test, because the damage is silent.

`runner.module_deps` is the bench's re-run key: a scenario is re-run when one of the modules its skill actually
depends on changes. When a low module reaches upward — perception importing the brain to ask whether a mob is
hostile, the transport layer importing a fight skill to check an aim, the tape importing the four modules whose
state it records — every closure becomes the whole package. Nothing fails. The bench simply re-runs everything,
for every change, forever, and looks slow rather than broken.

So: facts live at the bottom (a mob's reach, whether it is hostile, the geometry of a line of sight), decisions at
the top, and the top is wired INTO the bottom rather than imported from it (`brain._wire_tape`).
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.bench import runner  # noqa: E402

PKG = pathlib.Path(__file__).resolve().parent.parent / "bonobo"

# The two modules that decide things. Nothing may import them: they are where the wiring is done, not a library.
TOP = {"brain"}
# Replaying and reviewing a decision means building the decider — that is the whole job, not a layering slip.
MAY_IMPORT_TOP = {"brain", "review"}

# How many modules each one drags in, frozen. A ratchet, not a target: these may fall, never rise. When one rises
# the bench's re-run key has just got coarser, and this is the only place that will say so. Rebased when the belief
# table was introduced: one leaf module that genuinely belongs in every closure raises them all by one.
# 重构后基线（refactor 1–5 之后的当前值），只降不升。
CLOSURE = {
    "api": 9,
    "arbiter": 2,            # arbiter no longer imports api: api wires its message and /stop in (arbiter.WIRE)
    "bag": 12,
    "beliefs": 2,
    "blueprints": 1,
    "brain": 57,            # + reflexes (the maintenance table split off needs); + mechanisms (taught doors); + fight_plan (dragon)
    "brewing": 22,
    "building": 20,
    "combat": 18,
    "combat_model": 4,
    "combat_tape": 10,
    "cost": 22,
    "craft": 21,
    "data": 1,
    "decompose": 25,
    "dispatch": 23,
    "dragon": 25,            # the fight rewritten (G1): fight_plan, nav, combat.shoot, fluids (water at the feet)
    "end": 18,
    "estimate": 6,           # + data (READ_EVERY_S: fight_cost charges the loop's poll per target)
    "events": 2,             # the concise event log: paths only
    "explore": 18,
    "farming": 23,
    "field": 1,
    "fight_loop": 20,
    "fight_plan": 8,         # + data via estimate (READ_EVERY_S)
    "fluids": 19,
    "formulas": 1,           # the game's formulas (armour, explosion): +1 in every closure reading beliefs or estimate
    "fresh": 2,
    "game": 0,               # a constant leaf (constant_leaf): counted by no closure, its own included
    "gather": 21,
    "goals": 3,
    "hazard": 14,
    "intent": 10,
    "jobs": 22,
    "kernel": 7,             # + data via estimate (READ_EVERY_S)
    "knowledge": 2,
    "lifecycle": 1,
    "loot": 18,
    "mechanisms": 15,        # taught doors: the store, the press skill
    "memory": 4,
    "nav": 15,               # + knowledge (data-only facts): plan_way prices its breaks (break_ticks, tool_for)
    "needs": 36,
    "nether": 19,
    "paths": 1,
    "perception": 24,
    "planner": 11,
    "reflexes": 35,
    "retry": 2,                     # → data.UNREACHABLE (03cfe4f): one fact edge, the one list api shares
    "review": 7,
    "roads": 1,
    "shapes": 1,                    # types only (TypedDicts, Literals): imported under TYPE_CHECKING
    "skill": 14,
    "skillcore": 13,
    "skills": 28,
    "store": 23,
    "survive": 24,
    "tape": 2,
    "tasks": 5,
    "terrain": 16,
    "threat": 8,             # + data via estimate (READ_EVERY_S)
    "ui": 22,
    "wood": 25,
    "world": 10,
}

# And by one again for `lifecycle`: a leaf with no imports where per-life state registers its reset beside itself,
# so a bench row, a death and a dimension change forget it in one call (lifecycle.reset_all).

# And by one again when the arbiter stopped holding the body with a lock and started holding a DECISION: keeping
# or replacing one is `kernel`'s rule, and a second copy of it here is what made every answer after the first an
# intruder.

# Every module above grew by one when `arbiter` started asking `estimate` what work already put in is worth:
# one edge to a fact module, which is the shape this is meant to allow — a quantity computed in one place and
# read from wherever it is needed, rather than re-derived by each caller.

# The bottom: pure facts and pure functions over them. Anything here that grows an import has stopped being a fact.
FACTS = {"data", "shapes", "kernel", "combat_model", "retry", "roads", "blueprints",
         "paths", "beliefs", "field", "estimate", "lifecycle", "formulas"}


def modules():
    return sorted(p.stem for p in PKG.glob("*.py") if p.stem != "__init__")


def constant_leaf(source):
    """Pure: a module of constants only — no import, no function, no class (a docstring and assignments of literals):
    it cannot close a cycle nor couple behaviour, so a closure does not count it."""
    import ast
    tree = ast.parse(source)
    body = tree.body[1:] if tree.body and isinstance(tree.body[0], ast.Expr) else tree.body
    return all(isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None
               and all(isinstance(x, (ast.Constant, ast.Dict, ast.List, ast.Tuple, ast.Set, ast.Name, ast.Store,
                                      ast.Load, ast.UnaryOp, ast.USub, ast.BinOp, ast.operator))
                       for x in ast.walk(n.value)) for n in body)


def closure(m, pkg=PKG):
    """The modules m's re-run key reaches that couple behaviour (runner.module_deps, constant leaves apart)."""
    return [x for x in runner.module_deps(m, str(pkg)) if not constant_leaf((pathlib.Path(pkg) / f"{x}.py").read_text())]


class Direction(unittest.TestCase):
    """Asked of the bench's own key (`runner.module_deps`), which is what an upward edge damages."""

    # fixture: (situation, {module: source} of a package, the module asked) → its key's closure
    KEYS = [("a fact alone", {"data": "X = 1\n"}, "data", ["data"]),
            ("transitive: a → b → c", {"a": "from . import b\n", "b": "from .c import X\n", "c": "X = 1\n"}, "a",
             ["a", "b", "c"]),
            ("a deferred import is still an edge", {"a": "def f():\n    from . import brain\n", "brain": ""}, "a",
             ["a", "brain"]),
            ("a cycle ends", {"a": "from . import b\n", "b": "from . import a\n"}, "a", ["a", "b"]),
            ("must fail: a type-only import is no edge",
             {"a": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .shapes import Cell\n", "shapes": ""},
             "a", ["a"]),
            ("a fact reaching a decider: the key shows it (what the tests below fail on)",
             {"data": "from .brain import decide\n", "brain": ""}, "data", ["brain", "data"]),
            ("must fail: not edges: the standard library, a module that is not there", {"a": "import os\nfrom .gone import X\n"},
             "a", ["a"])]

    def test_the_key_follows_every_package_import(self):
        import tempfile
        for name, files, asked, want in self.KEYS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                for m, src in files.items():
                    (pathlib.Path(tmp) / f"{m}.py").write_text(src)
                self.assertEqual(runner.module_deps(asked, tmp), want)

    def test_nothing_imports_the_deciders(self):
        for m in modules():
            if m in MAY_IMPORT_TOP:
                continue
            with self.subTest(m):
                self.assertEqual(sorted(set(runner.module_deps(m)) & TOP), [],
                                 f"{m}'s key reaches deciders; move the fact down, or wire it from the top")

    def test_facts_import_only_facts(self):
        for m in sorted(FACTS):
            with self.subTest(m):
                self.assertEqual(sorted(set(closure(m)) - FACTS), [], f"{m} is a fact module")


class ReRunKey(unittest.TestCase):
    """The key only discriminates while the closures differ. These are the numbers it discriminates by."""

    def test_no_closure_has_grown(self):
        for m in modules():
            self.assertIn(m, CLOSURE, f"new module {m}: add it to CLOSURE with its size")
            self.assertLessEqual(len(closure(m)), CLOSURE[m],
                                 f"{m} now drags in more of the package; the bench will re-run more than it must")

    def test_a_constant_leaf_is_not_counted(self):
        rows = [("constants only", '"""doc"""\nX = 1\nT = {"a": 1.0, "b": -2}\nN = X * 2\n', True),
                ("must fail: an import couples", "import os\nX = 1\n", False),
                ("must fail: a relative import", "from .data import Y\nX = Y\n", False),
                ("must fail: a function is behaviour", "X = 1\ndef f():\n    return X\n", False),
                ("must fail: a call is behaviour", "X = dict(a=1)\n", False)]
        for why, src, want in rows:
            with self.subTest(why):
                self.assertIs(constant_leaf(src), want)

    def test_a_skill_does_not_depend_on_the_whole_package(self):
        """The failure this file exists for: every closure equal to the package means no scenario is ever skipped."""
        whole = len(modules())
        for m in ("fluids", "nav", "perception", "api", "tape"):
            self.assertLess(len(runner.module_deps(m)), whole // 2, m)


if __name__ == "__main__":
    unittest.main()

"""Layering. A test, because the damage is silent.

`scenarios.module_deps` is the bench's re-run key: a scenario is re-run when one of the modules its skill actually
depends on changes. When a low module reaches upward — perception importing the brain to ask whether a mob is
hostile, the transport layer importing a fight skill to check an aim, the tape importing the four modules whose
state it records — every closure becomes the whole package. Nothing fails. The bench simply re-runs everything,
for every change, forever, and looks slow rather than broken.

So: facts live at the bottom (a mob's reach, whether it is hostile, the geometry of a line of sight), decisions at
the top, and the top is wired INTO the bottom rather than imported from it (`brain._wire_tape`).
"""
import ast
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import scenarios  # noqa: E402

PKG = pathlib.Path(__file__).resolve().parent.parent / "bonobo"

# The two modules that decide things. Nothing may import them: they are where the wiring is done, not a library.
TOP = {"brain", "scenarios"}
# Replaying and reviewing a decision means building the decider — that is the whole job, not a layering slip.
MAY_IMPORT_TOP = {"brain", "scenarios", "decide", "review"}

# How many modules each one drags in, frozen. A ratchet, not a target: these may fall, never rise. When one rises
# the bench's re-run key has just got coarser, and this is the only place that will say so. Rebased when the belief
# table was introduced: one leaf module that genuinely belongs in every closure raises them all by one.
CLOSURE = {"actions": 16, "api": 9, "arbiter": 9, "bag": 11, "beliefs": 1, "value": 7, "gates": 6, "blueprints": 1, "brain": 57,   # +want: the cerebrum's door is a thing the planner must know about
    "brewing": 30, "building": 23, "bunker": 35, "combat": 35, "combat_model": 2, "combat_tape": 10, "data": 1,
    "decide": 58, "directives": 2, "end": 35, "explore": 21, "farming": 29, "field": 1, "fight_plan": 6, "estimate": 4, "fit": 1,
    "fluids": 19, "intent": 10, "jobs": 29, "kernel": 5, "knowledge": 2, "lookahead": 2, "loot": 18, "memory": 3,
    "nav": 17, "nether": 21, "paths": 1, "perception": 15, "planner": 14, "pool": 1, "priority": 6, "recovery": 1,
    "retry": 1, "review": 41, "roads": 1, "route": 4, "scenarios": 41, "skill": 10, "skillcore": 12, "skills": 29,
    "solve": 1, "survival": 4, "tape": 2, "terrain": 20, "threat": 6, "ui": 30, "upkeep": 18, "want": 15, "wood": 29,
    "world": 9,
}

# Every module above grew by one when `arbiter` started asking `estimate` what work already put in is worth:
# one edge to a fact module, which is the shape this is meant to allow — a quantity computed in one place and
# read from wherever it is needed, rather than re-derived by each caller.

# The bottom: pure facts and pure functions over them. Anything here that grows an import has stopped being a fact.
FACTS = {"data", "kernel", "pool", "solve", "combat_model", "recovery", "retry", "roads", "blueprints",
         "paths", "fit", "beliefs", "field", "estimate"}


def imports_of(module):
    """Every module of this package `module` imports, at any nesting (deferred imports count — they are still
    edges in the graph the bench keys on)."""
    out = set()
    for node in ast.walk(ast.parse((PKG / f"{module}.py").read_text())):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            out |= {node.module.split(".")[0]} if node.module else {a.name for a in node.names}
    return {m for m in out if (PKG / f"{m}.py").exists()}


def modules():
    return sorted(p.stem for p in PKG.glob("*.py") if p.stem != "__init__")


class Direction(unittest.TestCase):
    def test_nothing_imports_the_deciders(self):
        for m in modules():
            if m in MAY_IMPORT_TOP:
                continue
            self.assertFalse(imports_of(m) & TOP,
                             f"{m} imports {sorted(imports_of(m) & TOP)}; move the fact down, or wire it from the top")

    def test_facts_import_only_facts(self):
        for m in sorted(FACTS):
            self.assertFalse(imports_of(m) - FACTS, f"{m} is a fact module but imports {sorted(imports_of(m) - FACTS)}")


class ReRunKey(unittest.TestCase):
    """The key only discriminates while the closures differ. These are the numbers it discriminates by."""

    def test_no_closure_has_grown(self):
        for m in modules():
            self.assertIn(m, CLOSURE, f"new module {m}: add it to CLOSURE with its size")
            self.assertLessEqual(len(scenarios.module_deps(m)), CLOSURE[m],
                                 f"{m} now drags in more of the package; the bench will re-run more than it must")

    def test_a_skill_does_not_depend_on_the_whole_package(self):
        """The failure this file exists for: every closure equal to the package means no scenario is ever skipped."""
        whole = len(modules())
        for m in ("fluids", "nav", "perception", "api", "tape"):
            self.assertLess(len(scenarios.module_deps(m)), whole // 2, m)


if __name__ == "__main__":
    unittest.main()

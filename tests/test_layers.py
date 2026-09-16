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
CLOSURE = {"actions": 15, "api": 8, "arbiter": 8, "bag": 10, "beliefs": 1, "blueprints": 1, "brain": 54,
    "brewing": 29, "building": 22, "bunker": 34, "combat": 34, "combat_model": 2, "combat_tape": 9, "data": 1,
    "decide": 54, "directives": 2, "end": 34, "explore": 20, "farming": 28, "field": 1, "fight_plan": 4, "fit": 1,
    "fluids": 18, "intent": 9, "jobs": 28, "kernel": 1, "knowledge": 2, "lookahead": 2, "loot": 17, "memory": 3,
    "nav": 16, "nether": 20, "paths": 1, "perception": 14, "planner": 12, "pool": 1, "priority": 4, "recovery": 1,
    "retry": 1, "review": 54, "roads": 1, "route": 4, "scenarios": 54, "skill": 9, "skillcore": 11, "skills": 28,
    "solve": 1, "survival": 2, "tape": 2, "terrain": 19, "threat": 4, "ui": 29, "upkeep": 17, "wood": 28,
    "world": 8,
}

# The bottom: pure facts and pure functions over them. Anything here that grows an import has stopped being a fact.
FACTS = {"data", "kernel", "pool", "solve", "combat_model", "recovery", "retry", "roads", "blueprints",
         "paths", "fit", "beliefs", "field"}


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

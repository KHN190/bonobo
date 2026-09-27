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
    "actions": 15,
    "api": 9,
    "arbiter": 9,
    "bag": 11,
    "beliefs": 2,
    "blueprints": 1,
    "brain": 54,            # + reflexes (the maintenance table split off needs)
    "brewing": 26,
    "building": 19,
    "bunker": 36,           # end → fight_loop: the dragon fight is carried by the answer loop
    "combat": 36,
    "combat_model": 3,
    "combat_tape": 10,
    "cost": 17,
    "data": 1,
    "decompose": 22,
    "dispatch": 27,
    "end": 36,
    "estimate": 4,
    "explore": 17,
    "farming": 25,
    "field": 1,
    "fight_loop": 21,
    "fight_plan": 6,
    "fluids": 18,
    "fresh": 2,
    "goals": 14,
    "hazard": 13,
    "intent": 10,
    "jobs": 25,
    "kernel": 5,
    "knowledge": 2,
    "loot": 17,
    "memory": 5,
    "nav": 12,
    "nether": 18,
    "paths": 1,
    "perception": 21,
    "planner": 13,
    "recovery": 1,
    "retry": 2,                     # → data.UNREACHABLE (03cfe4f): one fact edge, the one list api shares
    "review": 16,
    "roads": 1,
    "skill": 13,
    "skillcore": 12,
    "skills": 25,
    "solve": 1,
    "tape": 2,
    "tasks": 15,
    "terrain": 15,
    "threat": 6,
    "ui": 26,
    "needs": 36,
    "reflexes": 36,
    "wood": 25,
    "world": 9,
}

# And by one again when the arbiter stopped holding the body with a lock and started holding a DECISION: keeping
# or replacing one is `kernel`'s rule, and a second copy of it here is what made every answer after the first an
# intruder.

# Every module above grew by one when `arbiter` started asking `estimate` what work already put in is worth:
# one edge to a fact module, which is the shape this is meant to allow — a quantity computed in one place and
# read from wherever it is needed, rather than re-derived by each caller.

# The bottom: pure facts and pure functions over them. Anything here that grows an import has stopped being a fact.
FACTS = {"data", "kernel", "solve", "combat_model", "recovery", "retry", "roads", "blueprints",
         "paths", "beliefs", "field", "estimate"}


def modules():
    return sorted(p.stem for p in PKG.glob("*.py") if p.stem != "__init__")


class Direction(unittest.TestCase):
    """Asked of the bench's own key (`runner.module_deps`), which is what an upward edge damages."""

    # fixture: (situation, {module: source} of a package, the module asked) → its key's closure
    KEYS = [("a fact alone", {"data": "X = 1\n"}, "data", ["data"]),
            ("transitive: a → b → c", {"a": "from . import b\n", "b": "from .c import X\n", "c": "X = 1\n"}, "a",
             ["a", "b", "c"]),
            ("a deferred import is still an edge", {"a": "def f():\n    from . import brain\n", "brain": ""}, "a",
             ["a", "brain"]),
            ("a cycle ends", {"a": "from . import b\n", "b": "from . import a\n"}, "a", ["a", "b"]),
            ("a fact reaching a decider: the key shows it (what the tests below fail on)",
             {"data": "from .brain import decide\n", "brain": ""}, "data", ["brain", "data"]),
            ("not edges: the standard library, a module that is not there", {"a": "import os\nfrom .gone import X\n"},
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
                self.assertEqual(sorted(set(runner.module_deps(m)) - FACTS), [], f"{m} is a fact module")


class ReRunKey(unittest.TestCase):
    """The key only discriminates while the closures differ. These are the numbers it discriminates by."""

    def test_no_closure_has_grown(self):
        for m in modules():
            self.assertIn(m, CLOSURE, f"new module {m}: add it to CLOSURE with its size")
            self.assertLessEqual(len(runner.module_deps(m)), CLOSURE[m],
                                 f"{m} now drags in more of the package; the bench will re-run more than it must")

    def test_a_skill_does_not_depend_on_the_whole_package(self):
        """The failure this file exists for: every closure equal to the package means no scenario is ever skipped."""
        whole = len(modules())
        for m in ("fluids", "nav", "perception", "api", "tape"):
            self.assertLess(len(runner.module_deps(m)), whole // 2, m)


if __name__ == "__main__":
    unittest.main()

"""lifecycle: per-life state is forgotten in one place.

  * every module-level name a function rebinds via `global` (runtime state) is covered by a registered reset, or is
    in ALLOW with the reason it outlives a life (wiring, a per-process cache, output bookkeeping);
  * each registered reset puts every name it covers back to the value the module source gives it.
Must-fail rows: a fake module with an unregistered global is flagged; a reset that forgets a name it covers is caught.
"""
import ast
import importlib
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import lifecycle  # noqa: E402

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")

# (module, name) → why it outlives a life
ALLOW = {
    ("bonobo.fight_loop", "ANSWER"): "wiring: the brain wires the fight once (fight_loop.wire)",
    ("bonobo.bench.core", "BRAIN"): "wiring: `mc.py scenario` hands the bench the brain",
    ("bonobo.nav", "_features"): "the running jar's features: per process, not per life",
    ("bonobo.actions", "_CRAFT_SPEC"): "a cache derived from the recipe tables",
    ("bonobo.actions", "_SMELT_SPEC"): "a cache derived from the recipe tables",
    ("bonobo.api", "LAST_SEGMENT_S"): "the jar's measured pace, not world state",
    ("bonobo.beliefs", "_last_flush"): "the belief log's flush throttle (output bookkeeping)",
    ("bonobo.intent", "_last_sent"): "publish dedupe: a resend is harmless (output bookkeeping)",
    ("bonobo.tape", "_calls"): "the tape recorder's session: spans a recording, not a life",
}


def _modname(path):
    rel = os.path.relpath(path, os.path.dirname(PKG))[:-3]
    return rel.replace(os.sep, ".").removesuffix(".__init__")


def package_sources():
    out = {}
    for root, _dirs, files in os.walk(PKG):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                with open(p) as fh:
                    out[_modname(p)] = fh.read()
    return out


def runtime_globals(sources):
    """Pure over {module: source}: {(module, name)} for every name a function declares `global` (rebinds at runtime)."""
    found = set()
    for mod, src in sources.items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Global):
                found.update((mod, n) for n in node.names)
    return found


def uncovered(sources, covered, allow):
    """Pure: the runtime globals neither reset by a registered reset nor allowed with a reason."""
    return sorted(g for g in runtime_globals(sources) if g not in covered and g not in allow)


def initial_values(src):
    """Pure: {name: value} for module-level `NAME = <literal>` (and tuple) assignments in `src`."""
    out = {}
    for node in ast.parse(src).body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            pairs = []
            if isinstance(t, ast.Name):
                pairs = [(t, node.value)]
            elif isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple) and len(t.elts) == len(node.value.elts):
                pairs = list(zip(t.elts, node.value.elts))
            for name, value in pairs:
                if isinstance(name, ast.Name):
                    try:
                        out[name.id] = ast.literal_eval(value)
                    except ValueError:
                        pass
    return out


_DIRTY = object()


def dirty(module, name):
    """Make `name` not what it was: in place for a container (a reset that rebinds or clears both pass), else rebound."""
    v = getattr(module, name)
    if isinstance(v, dict):
        v["__dirty__"] = _DIRTY
    elif isinstance(v, list):
        v.append(_DIRTY)
    elif isinstance(v, set):
        v.add(_DIRTY)
    else:
        setattr(module, name, _DIRTY)


def unrestored(module, names, reset, initial):
    """Names `reset` did not put back to `initial` after each was dirtied."""
    for n in names:
        dirty(module, n)
    reset()
    return [n for n in names if getattr(module, n) != initial[n]]


class Coverage(unittest.TestCase):
    def test_every_runtime_global_is_reset_or_allowed(self):
        sources = package_sources()
        for mod in sorted({m for m, _ in runtime_globals(sources)}):
            importlib.import_module(mod)
        self.assertEqual(uncovered(sources, lifecycle.covered(), ALLOW), [])

    def test_allow_names_real_globals(self):
        """A stale allowlist entry (the global is gone, or now registered) is removed, not kept."""
        live = runtime_globals(package_sources())
        self.assertEqual(sorted(k for k in ALLOW if k not in live or k in lifecycle.covered()), [])

    def test_must_fail_unregistered_global(self):
        fake = {"bonobo.fake": "X = None\ndef f():\n    global X\n    X = 1\n"}
        self.assertEqual(uncovered(fake, set(), {}), [("bonobo.fake", "X")])
        self.assertEqual(uncovered(fake, {("bonobo.fake", "X")}, {}), [])
        self.assertEqual(uncovered(fake, set(), {("bonobo.fake", "X"): "why"}), [])


class Restores(unittest.TestCase):
    def test_each_registered_reset_restores_its_names(self):
        sources = package_sources()
        for mod in sorted({m for m, _ in runtime_globals(sources)}):
            importlib.import_module(mod)
        regs = lifecycle.registered()
        self.assertTrue(regs)
        for mod, names in regs:
            module = sys.modules[mod]
            initial = initial_values(sources[mod])
            with self.subTest(mod):
                self.assertEqual([n for n in names if n not in initial], [],
                                 "a covered name needs a literal initial value in its module")
                self.assertEqual(unrestored(module, names, lifecycle.reset_all, initial), [])

    def test_must_fail_reset_that_forgets_a_name(self):
        fake = types.ModuleType("fake")
        fake.A, fake.B = None, {}

        def forgets_b():
            fake.A = None
        self.assertEqual(unrestored(fake, ("A", "B"), forgets_b, {"A": None, "B": {}}), ["B"])

        def whole():
            fake.A, fake.B = None, {}
        self.assertEqual(unrestored(fake, ("A", "B"), whole, {"A": None, "B": {}}), [])


if __name__ == "__main__":
    unittest.main()

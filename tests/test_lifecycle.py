"""lifecycle: per-life state is forgotten in one place.

  * every module-level name a function rebinds via `global`, and every module-level container (dict/list/set/...)
    a function mutates in place, is covered by a registered reset, or is in ALLOW with the reason it outlives a life
    (wiring, an import-time registry, a cache keyed by its whole input, output bookkeeping);
  * each registered reset puts every name it covers back to the value the module source gives it.
Must-fail rows: a fake module with an unregistered global, or mutating an unregistered dict, is flagged; a reset that
forgets a name it covers is caught.
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
    ("bonobo.intent", "_last_sent"): "publish dedupe: a resend is harmless (output bookkeeping)",
    ("bonobo.events", "STATE"): "the session's event log bookkeeping (dedupe, milestones seen): outlives a life",
    ("bonobo.tape", "_calls"): "the tape recorder's session: spans a recording, not a life",
    ("bonobo.api", "LAST_DETAIL"): "the process's liveness (the watchdog's freeze clock): per process, not per life",
    # containers mutated in place
    ("bonobo.lifecycle", "_RESETS"): "the registry itself",
    ("bonobo.decompose", "SOLVERS"): "import-time registry",
    ("bonobo.decompose", "ORDER"): "import-time registry",
    ("bonobo.fight_loop", "BATCH"): "import-time registry (skills lend their batches)",
    ("bonobo.fight_loop", "REGION"): "import-time registry (skills lend their regions)",
    ("bonobo.goals", "DESIRED"): "import-time registry",
    ("bonobo.skill", "REGISTRY"): "import-time registry",
    ("bonobo.skill", "CALLS"): "the live call stack: popped by each call's own finally; clearing it mid-call breaks it",
    ("bonobo.skill", "LAST_S"): "measured skill durations (like LAST_SEGMENT_S), not world state",
    ("bonobo.actions", "_GROUPS_OF"): "cache keyed by immutable data (item groups)",
    ("bonobo.solve", "_MEMO"): "memo keyed by (columns, state, target): the key is the whole input",
    ("bonobo.solve", "_PRICES"): "memo keyed by (columns, state): the key is the whole input",
    ("bonobo.world", "_PER_BLOCK"): "the running jar's capability: per process",
    ("bonobo.intent", "_state"): "the published intent line, rewritten every round (output bookkeeping)",
    ("bonobo.tape", "SOURCES"): "tape recorder (recording session)",
    ("bonobo.tape", "FILES"): "tape recorder (recording session)",
    ("bonobo.tape", "_events"): "tape recorder: flushed each round",
    ("bonobo.tape", "_readings"): "tape recorder: flushed each round",
    ("bonobo.tape", "_last"): "tape recorder (recording session)",
    ("bonobo.bench.core", "PROBE_SEQ"): "monotonic probe number: a reset would match an old reply",
    ("bonobo.bench.words.brain", "FIRST_WATCH"): "monotonic watcher generation: a reset would revive a retired watcher",
    ("bonobo.bench.runner", "PREBUILT"): "the NEXT row's world, built while this one runs: cross-row by design",
    ("bonobo.bench.runner", "_IMPORTS"): "cache keyed by source files",
    ("bonobo.bench.runner", "_CODE"): "cache keyed by source files",
    ("bonobo.bench.rowkey", "_INDEX"): "cache keyed by source files",
    ("bonobo.bench.runner", "REPORTING"): "the last failure report's thread: joined by the next row, never dropped",
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


CTORS = {"dict", "list", "set", "defaultdict", "deque", "Counter", "OrderedDict"}
MUTATORS = {"append", "add", "update", "pop", "clear", "setdefault", "extend", "remove", "discard", "insert",
            "popleft", "appendleft", "popitem"}


def _is_container(v):
    if isinstance(v, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)):
        return True
    if isinstance(v, ast.Call):
        f = v.func
        return (f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None) in CTORS
    return False


def _module_containers(tree):
    out = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and _is_container(node.value):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None and _is_container(node.value) \
                and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _shadowed(fn):
    """Names that are the function's own (parameters, locals not declared global), not the module's."""
    a = fn.args
    own = {x.arg for x in a.args + a.kwonlyargs + a.posonlyargs} | {x.arg for x in (a.vararg, a.kwarg) if x}
    if isinstance(fn, ast.Lambda):
        return own
    declared = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Global):
            declared.update(n.names)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            own.add(n.id)
    return own - declared


def _name_of_subscript(t):
    return t.value.id if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) else None


def runtime_containers(sources):
    """Pure over {module: source}: {(module, name)} for every module-level container (dict/list/set/... literal or
    constructor) that some function mutates in place: item assign/del, +=, or a mutating method."""
    found = set()
    for mod, src in sources.items():
        tree = ast.parse(src)
        cands = _module_containers(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            live = cands - _shadowed(fn)
            for n in ast.walk(fn):
                hit = set()
                if isinstance(n, (ast.Assign, ast.Delete)):
                    hit = {_name_of_subscript(t) for t in n.targets}
                elif isinstance(n, ast.AugAssign):
                    t = n.target
                    hit = {t.id if isinstance(t, ast.Name) else _name_of_subscript(t)}
                elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in MUTATORS \
                        and isinstance(n.func.value, ast.Name):
                    hit = {n.func.value.id}
                found.update((mod, h) for h in hit & live)
    return found


def runtime_state(sources):
    return runtime_globals(sources) | runtime_containers(sources)


def uncovered(sources, covered, allow):
    """Pure: runtime state (rebound via `global`, or a container mutated in place) neither reset by a registered
    reset nor allowed with a reason."""
    return sorted(g for g in runtime_state(sources) if g not in covered and g not in allow)


def initial_values(src):
    """Pure: {name: value} for module-level `NAME = <literal>` (and tuple, and annotated `NAME: T = <literal>`)
    assignments in `src`."""
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            node = ast.Assign(targets=[node.target], value=node.value)
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


def state_unrestored(st, reset):
    """A lifecycle.State's LIFE fields `reset` did not put back to their defaults after each was dirtied."""
    fresh = type(st)()
    for f in st.LIFE:
        setattr(st, f, _DIRTY)
    reset()
    return [f for f in st.LIFE if getattr(st, f) != getattr(fresh, f)]


def _import_stateful(sources):
    for mod in sorted({m for m, _ in runtime_state(sources)}):
        importlib.import_module(mod)


class Coverage(unittest.TestCase):
    def test_every_runtime_state_is_reset_or_allowed(self):
        sources = package_sources()
        _import_stateful(sources)
        self.assertEqual(uncovered(sources, lifecycle.covered(), ALLOW), [])

    def test_allow_names_real_state(self):
        """A stale allowlist entry (the state is gone, or now registered) is removed, not kept."""
        sources = package_sources()
        _import_stateful(sources)
        live = runtime_state(sources)
        self.assertEqual(sorted(k for k in ALLOW if k not in live or k in lifecycle.covered()), [])

    def test_must_fail_unregistered_global(self):
        fake = {"bonobo.fake": "X = None\ndef f():\n    global X\n    X = 1\n"}
        self.assertEqual(uncovered(fake, set(), {}), [("bonobo.fake", "X")])
        self.assertEqual(uncovered(fake, {("bonobo.fake", "X")}, {}), [])
        self.assertEqual(uncovered(fake, set(), {("bonobo.fake", "X"): "why"}), [])

    def test_containers_mutated_in_place(self):
        # (situation, source) → flagged names
        rows = [("must fail: item assigned", "D = {}\ndef f(k):\n    D[k] = 1\n", ["D"]),
                ("must fail: a mutating method", "L = []\ndef f(x):\n    L.append(x)\n", ["L"]),
                ("must fail: item deleted", "D = dict()\ndef f(k):\n    del D[k]\n", ["D"]),
                ("must fail: +=", "L = [0]\ndef f():\n    L[0] += 1\n", ["L"]),
                ("must fail: a constructor call, a lambda", "import collections\nQ = collections.deque()\n"
                 "g = lambda x: Q.append(x)\n", ["Q"]),
                ("only read: not state", "D = {}\ndef f(k):\n    return D.get(k)\n", []),
                ("a local of the same name: not the module's", "D = {}\ndef f():\n    D = {}\n    D['a'] = 1\n", []),
                ("a parameter of the same name", "D = {}\ndef f(D):\n    D.clear()\n", []),
                ("a constant, not a container", "N = 1\ndef f(x):\n    x.N = 2\n", [])]
        for name, src, want in rows:
            with self.subTest(name):
                self.assertEqual(uncovered({"bonobo.fake": src}, set(), {}), [("bonobo.fake", n) for n in want])
        with self.subTest("registered or allowed: not flagged"):
            src = rows[0][1]
            self.assertEqual(uncovered({"bonobo.fake": src}, {("bonobo.fake", "D")}, {}), [])
            self.assertEqual(uncovered({"bonobo.fake": src}, set(), {("bonobo.fake", "D"): "why"}), [])


class Restores(unittest.TestCase):
    def test_each_registered_reset_restores_its_names(self):
        sources = package_sources()
        _import_stateful(sources)
        regs = lifecycle.registered()
        self.assertTrue(regs)
        for mod, names in regs:
            module = sys.modules[mod]
            initial = initial_values(sources[mod])
            if names == ("STATE",):
                with self.subTest(f"{mod}.STATE"):
                    self.assertIsInstance(module.STATE, lifecycle.State)
                    self.assertTrue(module.STATE.LIFE)
                    self.assertEqual(state_unrestored(module.STATE, lifecycle.reset_all), [])
                continue
            with self.subTest(mod):
                self.assertEqual([n for n in names if n not in initial], [],
                                 "a covered name needs a literal initial value in its module")
                self.assertEqual(unrestored(module, names, lifecycle.reset_all, initial), [])

    def test_state_object(self):
        import dataclasses
        import threading

        @dataclasses.dataclass
        class S(lifecycle.State):
            a: object = None
            b: dict = dataclasses.field(default_factory=dict)
            kept: int = 0
            lock: object = dataclasses.field(default_factory=threading.Lock)
            LIFE = ("a", "b")

        st = S(kept=5)
        self.assertEqual(state_unrestored(st, st.reset), [])
        self.assertEqual(st.kept, 5, "a field outside LIFE outlives the reset")

        class Forgets(S):
            def reset(self):
                self.a = None
        st = Forgets()
        self.assertEqual(state_unrestored(st, st.reset), ["b"], "must fail: a reset that forgets a LIFE field")

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

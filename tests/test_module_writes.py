"""A write to another module's attribute (`api.X = ...`, `setattr(api, "X", ...)`, `__import__("bonobo.api").X = ...`)
must name something that module declares. pyright checks `api.X = ` against api's names, but not a write through
`__import__(...)` (Any) or `setattr` with a string: bench injections wrote `api.INTERRUPT` for a day after it moved
into api.STATE, and nothing was pending."""
import ast
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PKG = ROOT / "bonobo"


def module_file(dotted):
    """The source file of a bonobo module (a.b.c), or None when it is no module."""
    p = ROOT.joinpath(*dotted.split("."))
    for f in (p.with_suffix(".py"), p / "__init__.py"):
        if f.exists():
            return f
    return None


def declared(src):
    """Pure: the names a module's source declares at top level (assigned, annotated, def, class, imported) or
    through `global` in a function."""
    out = set()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        out.add(n.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out |= {(a.asname or a.name).split(".")[0] for a in node.names}
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            out |= set(node.names)
    return out


def aliases(src, dotted):
    """Pure-ish: {local name: bonobo module} for the modules this file imports (relative or absolute)."""
    pkg = dotted.split(".")[:-1]
    out = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            base = pkg[:len(pkg) - node.level + 1] if node.level else []
            mod = base + (node.module.split(".") if node.module else [])
            for a in node.names:
                full = ".".join(mod + [a.name])
                if full.startswith("bonobo") and module_file(full):
                    out[a.asname or a.name] = full
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("bonobo.") and a.asname and module_file(a.name):
                    out[a.asname] = a.name
    return out


def _module_of(node, names):
    """The bonobo module an expression names: an imported alias, or `__import__("bonobo.x", ...)`."""
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__" \
            and node.args and isinstance(node.args[0], ast.Constant) and str(node.args[0].value).startswith("bonobo"):
        return node.args[0].value
    return None


def stray_writes(src, dotted, declared_of):
    """Pure: [(line, module, name)] of writes to a module attribute its module does not declare."""
    names = aliases(src, dotted)
    out = []
    for node in ast.walk(ast.parse(src)):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and (m := _module_of(t.value, names)):
                if t.attr not in declared_of(m):
                    out.append((node.lineno, m, t.attr))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr" \
                and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                and (m := _module_of(node.args[0], names)) and node.args[1].value not in declared_of(m):
            out.append((node.lineno, m, node.args[1].value))
    return out


_DECLARED = {}


def declared_of(dotted):
    if dotted not in _DECLARED:
        f = module_file(dotted)
        _DECLARED[dotted] = declared(f.read_text()) if f else set()
    return _DECLARED[dotted]


class ModuleWrites(unittest.TestCase):
    # (situation, source written in bonobo/bench/words/runs.py) → strays
    ROWS = [("must fail: the removed attribute through __import__",
             '__import__("bonobo.api", fromlist=["INTERRUPT"]).INTERRUPT = "x"\n', [(1, "bonobo.api", "INTERRUPT")]),
            ("must fail: through setattr", 'from ... import api\nsetattr(api, "INTERRUPT", "x")\n',
             [(2, "bonobo.api", "INTERRUPT")]),
            ("must fail: a plain write to a name api never declared", "from ... import api\napi.NOPE = 1\n",
             [(2, "bonobo.api", "NOPE")]),
            ("a declared hook is fine", "from ... import api\napi.DRESS = None\n", []),
            ("a write to a local object is not a module's", "x = object()\nx.INTERRUPT = 1\n", [])]

    def test_rows(self):
        for name, src, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(stray_writes(src, "bonobo.bench.words.runs", declared_of), want)

    def test_the_package(self):
        bad = []
        for f in sorted(PKG.rglob("*.py")):
            dotted = ".".join(f.relative_to(ROOT).with_suffix("").parts)
            bad += [f"{f.relative_to(ROOT)}:{ln} writes {m}.{n}" for ln, m, n in
                    stray_writes(f.read_text(), dotted, declared_of)]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()

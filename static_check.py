"""Static checks over bonobo/ (AST only, no allowlist): `python3 static_check.py` prints every rule's hits;
tests/test_static_check.py runs ROWS (each rule's holding and must-fail rows) and the scan inside the suite.
R1 one module-level function name, one module (a `main` beside an `if __name__ == "__main__"` is an entry point);
R2 no two function bodies alike (local names apart); R3 no re-export (an alias, a used noqa re-import, a star
import); R4 one number, one definition (one name in two modules, or equal numbers under one suffix);
R5 game data only in data.py and game.py (ticks<->seconds by the tick rate, a light level, a numeric table keyed by registry ids);
R6 no dead code (a module-level def or constant production never names); R7 no swallowed exception (a handler that
only passes, continues or returns a value); R8 no module-level container changed in a function unless its module
registers its reset (lifecycle.in_place / on_reset covers); R9 a "Pure" function reaches no api call, HTTP or
module-state write; R10 no bench budget or estimate written as a number; R11 (E5) a skill declares its budget, abandon
only one of skill.ABANDON_WAYS, cover only for a soft or fight skill;
R12 (K9, over check/) a checker function that prices (its name says a price, a cost, an estimate, seconds or ticks)
calls production for it, never a model of its own."""
import ast
import os
import re
import sys
from collections import defaultdict

from bonobo.data import TICKS_PER_S, TIER_OF_MATERIAL

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "bonobo")
DATA = ("data.py", "game.py")     # the homes of game data: the tables, and the import-free constants leaf
MIN_STMTS = 3                       # R2: a body this long is code, shorter is a one-liner idiom
LIGHT_READINGS = ("skyLight", "blockLight")


def sources(root=ROOT):
    """{relative path: source} of every module under bonobo/."""
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(d, f)
                with open(p) as fh:
                    out[os.path.relpath(p, root)] = fh.read()
    return out


def _main_guarded(tree):
    return any(isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name)
               and n.test.left.id == "__name__" for n in tree.body)


def r1(trees):
    """[(name, [path:line…])]: a module-level function name defined in two modules."""
    seen = defaultdict(list)
    for path, (tree, _src) in trees.items():
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if n.name == "main" and _main_guarded(tree):
                    continue
                seen[n.name].append(f"{path}:{n.lineno}")
    return sorted((k, v) for k, v in seen.items() if len({p.split(":")[0] for p in v}) > 1)


class _Anon(ast.NodeTransformer):
    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node):
        return ast.copy_location(ast.arg(arg="_", annotation=None), node)


def _body(fn):
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


def r2(trees):
    """[(dump, [path:line qualname…])]: functions (any depth) whose bodies are equal up to local names."""
    seen = defaultdict(list)
    for path, (tree, _src) in trees.items():
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and len(_body(n)) >= MIN_STMTS:
                mod = ast.Module(body=[_Anon().visit(ast.parse(ast.unparse(s)).body[0]) for s in _body(n)],
                                 type_ignores=[])
                seen[ast.dump(mod)].append(f"{path}:{n.lineno} {n.name}")
    return [(k[:60], v) for k, v in seen.items() if len(v) > 1]


def _module_of(path):
    return path[:-3].replace(os.sep, ".").removesuffix(".__init__")


def _resolve(path, node):
    """The bonobo-relative module a `from … import` names (None outside bonobo)."""
    if node.level == 0:
        mod = node.module or ""
        return mod.removeprefix("bonobo.") if mod.startswith("bonobo") else None
    base = _module_of(path).split(".")
    base = base[:len(base) - node.level] if not path.endswith("__init__.py") else base[:len(base) - node.level + 1]
    return ".".join(base + ([node.module] if node.module else []))


def r3(trees):
    """[(kind, path:line, name)]: a module attribute rebound to its own name; a noqa: F401 import another module
    imports from here; a star import."""
    out, imported_from = [], defaultdict(set)
    for path, (tree, _src) in trees.items():
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                mod = _resolve(path, n)
                if mod is not None:
                    for a in n.names:
                        imported_from[mod].add(a.name)
    for path, (tree, src) in trees.items():
        lines = src.splitlines()
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                    and isinstance(n.value, ast.Attribute) and isinstance(n.value.value, ast.Name) \
                    and n.value.attr == n.targets[0].id:
                out.append(("alias", f"{path}:{n.lineno}", n.targets[0].id))
        for n in ast.walk(tree):
            if not isinstance(n, ast.ImportFrom):
                continue
            if any(a.name == "*" for a in n.names):
                out.append(("star", f"{path}:{n.lineno}", n.module or "."))
                continue
            text = "\n".join(lines[n.lineno - 1:(n.end_lineno or n.lineno)])
            if "noqa: F401" in text:
                for a in n.names:
                    if (a.asname or a.name) in imported_from[_module_of(path)]:
                        out.append(("re-export", f"{path}:{n.lineno}", a.asname or a.name))
    return sorted(out)


def _number(node):
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _number(node.operand)
        return None if v is None else -v
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    return None


def r4(trees):
    """[(why, [path:line NAME=value…])]: an UPPER name bound to a number at module level in two modules; or two
    UPPER names with the same suffix (all but the first word) bound to the same number."""
    by_name, by_word = defaultdict(list), defaultdict(list)
    for path, (tree, _src) in trees.items():
        for n in tree.body:
            targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
            value = n.value if isinstance(n, (ast.Assign, ast.AnnAssign)) else None
            for t in targets:
                v = _number(value) if value is not None else None
                if isinstance(t, ast.Name) and t.id.isupper() and v is not None:
                    by_name[t.id].append((path, n.lineno, v))
                    if len(t.id.partition("_")[2]) > 1:    # a one-letter unit (_S, _R) names no quantity
                        by_word[(t.id.split("_", 1)[1], v)].append((path, n.lineno, t.id))
    out = [(f"name {k}", [f"{p}:{ln} ={v}" for p, ln, v in rows]) for k, rows in by_name.items()
           if len({p for p, _l, _v in rows}) > 1]
    out += [(f"*_{w} = {v}", [f"{p}:{ln} {name}" for p, ln, name in rows]) for (w, v), rows in by_word.items()
            if len({name for _p, _l, name in rows}) > 1]
    return sorted(out)


def _reads_light(node):
    return any(isinstance(c, ast.Constant) and c.value in LIGHT_READINGS for c in ast.walk(node))


def _registry_key(k):
    return isinstance(k, ast.Constant) and isinstance(k.value, str) and (
        k.value.startswith("minecraft:") or k.value in TIER_OF_MATERIAL)


def r5(trees):
    """[(kind, path:line)] outside data.py: a 20 multiplying or dividing (ticks↔seconds); a number compared with a
    light reading; a constant table of numbers keyed by registry ids (a game table)."""
    out = []
    for path, (tree, _src) in trees.items():
        if path in DATA:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mult, ast.Div, ast.FloorDiv)) \
                    and TICKS_PER_S in (_number(n.left), _number(n.right)):
                out.append(("ticks", f"{path}:{n.lineno}"))
            elif isinstance(n, ast.Compare) and _reads_light(n) \
                    and any(_number(x) is not None for x in [n.left, *n.comparators]):
                out.append(("light", f"{path}:{n.lineno}"))
        out += [("table", f"{path}:{d.lineno}") for d in _tables(tree)
                if d.keys and all(k is not None and _registry_key(k) for k in d.keys)
                and all(_number(v) is not None for v in d.values)]
    return sorted(out)


def _tables(tree):
    """Dict literals a module holds as constant tables: bound at module level to an UPPER name no function changes
    (a skill's `needs` argument is a declaration, a counter a function bumps is state)."""
    changed = {name for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
               for _l, name in _mutations(fn, _containers(tree))}
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            names = [t.id for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)]
            if names and all(x.isupper() and x not in changed for x in names):
                yield from (d for d in ast.walk(n.value) if isinstance(d, ast.Dict))


# -- R6 dead code ---------------------------------------------------------------------------------------------------
def _strings_out_of_all(tree):
    """String constants of a module, docstrings and __all__ lists apart (a name listed there is exported, not
    used; one a docstring mentions is talked about)."""
    skip = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AugAssign)):
            tg = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if isinstance(tg, ast.Name) and tg.id == "__all__":
                skip |= {id(c) for c in ast.walk(n)}
    return [c.value for c in ast.walk(tree) if isinstance(c, ast.Constant) and isinstance(c.value, str)
            and id(c) not in skip]


LOOKUPS = ("getattr", "hasattr", "get", "import_module", "resolve")     # calls that find a thing by its name


def _lookups(tree):
    """The string expressions a module looks a name up by: a subscript's key, a LOOKUPS call's argument."""
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript):
            yield n.slice
        elif isinstance(n, ast.Call) and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) in LOOKUPS:
            yield from n.args


def _patterns(tree):
    """Name patterns a module looks up composed at run time: globals()[f"{x}_row"], getattr(m, "_" + x)."""
    out = []
    for n in _lookups(tree):
        if isinstance(n, ast.JoinedStr) and any(not isinstance(v, ast.Constant) for v in n.values):
            out.append("".join(re.escape(str(v.value)) if isinstance(v, ast.Constant) else r"\w*" for v in n.values))
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            parts = [n.left, n.right]
            if any(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in parts) \
                    and not all(isinstance(x, ast.Constant) for x in parts):
                out.append("".join(re.escape(str(x.value)) if isinstance(x, ast.Constant) else r"\w*" for x in parts))
    return out


def _uses(tree):
    """{name: {the top-level def each use sits in (None: module level)}} of every name, attribute and import."""
    out = defaultdict(set)
    for top in tree.body:
        owner = top.name if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None
        for n in ast.walk(top):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                out[n.id].add(owner)
            elif isinstance(n, ast.Attribute):
                out[n.attr].add(owner)
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    out[a.name].add(owner)
    return out


def _defined(tree):
    """[(name, line)] module-level defs and constants (a decorated def is registered by its decorator: used)."""
    out = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.decorator_list:
            out.append((n.name, n.lineno))
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for tg in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(tg, ast.Name):
                    out.append((tg.id, n.lineno))
    return [(k, ln) for k, ln in out if not (k.startswith("__") and k.endswith("__"))]


def r6(trees, production=None):
    """[(path:line, name)]: a module-level def or constant production never names — as a name, an attribute, an
    import, a word in a string or a composed name — its own body apart. Tests and the checker keep nothing alive."""
    production = trees if production is None else production
    names, words, pats = defaultdict(set), set(), []
    for path, (tree, _src) in production.items():
        for k, owners in _uses(tree).items():
            names[k] |= {(path, o) for o in owners}
        for s in _strings_out_of_all(tree):
            for w in re.findall(r"[A-Za-z_]\w*", s):
                words |= {w, "_" + w}           # a row's word names `w` or `_w` (scene.resolve)
        pats += _patterns(tree)
    rx = re.compile("|".join(f"(?:{p})" for p in pats)) if pats else None
    out = []
    for path, (tree, _src) in trees.items():
        guarded = _main_guarded(tree)
        for name, line in _defined(tree):
            if name == "main" and guarded:
                continue
            if any(not (p == path and o == name) for p, o in names.get(name, ())) or name in words \
                    or (rx is not None and rx.fullmatch(name)):
                continue
            out.append((f"{path}:{line}", name))
    return sorted(out)


# -- R7 swallowed exceptions ------------------------------------------------------------------------------------------
EMPTY_MAKERS = ("set", "dict", "list", "tuple", "frozenset")    # `return set()`: an empty container too


def _quiet(stmt):
    if isinstance(stmt, (ast.Pass, ast.Continue, ast.Break)):
        return True
    if isinstance(stmt, ast.Return):
        v = stmt.value
        return v is None or isinstance(v, (ast.Constant, ast.Name)) or (
            isinstance(v, (ast.List, ast.Tuple, ast.Set)) and not v.elts) or (isinstance(v, ast.Dict) and not v.keys) or (
            isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in EMPTY_MAKERS
            and not v.args and not v.keywords)
    return False


def r7(trees):
    """[(path:line, the handler's statement)]: an except whose body only passes, continues, breaks or returns a value
    (a constant, a name, an empty container): no raise, no record."""
    out = []
    for path, (tree, _src) in trees.items():
        for n in ast.walk(tree):
            if isinstance(n, ast.ExceptHandler):
                body = _body(n)
                if len(body) == 1 and _quiet(body[0]):
                    out.append((f"{path}:{n.lineno}", ast.unparse(body[0])))
    return sorted(out)


# -- R8 module-level mutable state ------------------------------------------------------------------------------------
CONTAINERS = ("dict", "list", "set", "defaultdict", "Counter", "deque", "OrderedDict")
MUTATORS = ("append", "extend", "insert", "remove", "pop", "popitem", "clear", "update", "setdefault", "add",
            "discard", "appendleft", "extendleft", "sort", "reverse")
RESETS = ("in_place", "on_reset")       # lifecycle's registration of a reset: the one place state is forgotten


def _containers(tree):
    """{name: line} module-level names bound to a container."""
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            v = n.value
            box = isinstance(v, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)) or (
                isinstance(v, ast.Call) and (getattr(v.func, "id", None) or getattr(v.func, "attr", None)) in CONTAINERS)
            for tg in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if box and isinstance(tg, ast.Name):
                    out[tg.id] = n.lineno
    return out


def _registered(tree):
    """Names a module hands lifecycle for reset (the strings in its in_place / on_reset calls)."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) in RESETS:
            out |= {c.value for a in [*n.args, *[k.value for k in n.keywords]] for c in ast.walk(a)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    return out


def _locals(fn):
    declared = {g for n in ast.walk(fn) if isinstance(n, ast.Global) for g in n.names}
    bound = {a.arg for a in ast.walk(fn.args) if isinstance(a, ast.arg)} | {
        n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    return bound - declared


def _mutations(fn, boxes):
    """[(line, name)] in-place changes of the module containers `boxes` inside function `fn`."""
    mine = _locals(fn)
    out = []
    for n in ast.walk(fn):
        target = None
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.Delete)):
            for tg in (n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target]):
                if isinstance(tg, ast.Subscript) and isinstance(tg.value, ast.Name):
                    target = tg.value.id
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in MUTATORS \
                and isinstance(n.func.value, ast.Name):
            target = n.func.value.id
        if target in boxes and target not in mine and isinstance(n, ast.stmt | ast.expr):
            out.append((n.lineno, target))
    return out


def _call_key(f):
    """A call's target as (the module-ish name it is read from, or "", the name)."""
    if isinstance(f, ast.Name):
        return "", f.id
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f.value.id, f.attr
    return None


def _import_time(trees):
    """{(module's last name, function)} only ever run while modules load: used as a decorator, or called at module
    level and never inside a function (a registration point: the registry it fills is the program's, not a life's)."""
    at_top, in_def = set(), set()

    def visit(n, inside):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for d in n.decorator_list:          # a decorator runs where its def stands
                (in_def if inside else at_top).add(_call_key(d.func if isinstance(d, ast.Call) else d))
                visit(d, inside)
            for b in n.body:                    # a class body runs where it stands, a function's when called
                visit(b, inside or not isinstance(n, ast.ClassDef))
            return
        if isinstance(n, ast.Call):
            (in_def if inside else at_top).add(_call_key(n.func))
        for c in ast.iter_child_nodes(n):
            visit(c, inside)
    for tree, _src in trees.values():
        visit(tree, False)
    return at_top - in_def


def _loading(name, mod, keys):
    """Is function `name` of module `mod` import-time only (every way it is called is)?"""
    ways = {("", name), (mod.rsplit(".", 1)[-1], name)}
    return bool(ways & keys) and not any(w not in keys and w in _ALL_CALLS for w in ways)


_ALL_CALLS = set()


def r8(trees):
    """[(path:line, name)]: a module-level container changed in place inside a function that runs after import, its
    module registering no reset of it: state one test or one life leaks into the next."""
    _ALL_CALLS.clear()
    for tree, _src in trees.values():
        _ALL_CALLS.update(_call_key(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call))
    keys = _import_time(trees)
    out = set()

    def visit(node, path, mod, boxes, ok, loading):
        for n in ast.iter_child_nodes(node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                here = loading or _loading(n.name, mod, keys)
                if not here:
                    nested = [(x.lineno, x.end_lineno or x.lineno) for x in ast.walk(n) if x is not n
                              and isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    out.update((f"{path}:{line}", name) for line, name in _mutations(n, boxes)
                               if name not in ok and not any(lo <= line <= hi for lo, hi in nested))
                visit(n, path, mod, boxes, ok, here)
            else:
                visit(n, path, mod, boxes, ok, loading)

    for path, (tree, _src) in trees.items():
        visit(tree, path, _module_of(path), _containers(tree), _registered(tree), False)
    return sorted(out)


# -- R9 pure functions -----------------------------------------------------------------------------------------------
NET = ("urllib", "http", "requests", "socket")
API = "api"


def _imports(path, tree, modules):
    """{local name: (module, name)} of a module's imports; name None when the local name is a module."""
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mod = _resolve(path, n) if n.level or (n.module or "").startswith("bonobo") else n.module
            for a in n.names:
                if mod is None or a.name == "*":
                    continue
                sub = f"{mod}.{a.name}" if mod else a.name
                out[a.asname or a.name] = (sub, None) if sub in modules else (mod, a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                out[a.asname or a.name.split(".")[0]] = (a.name.removeprefix("bonobo."), None)
    return out


def _effects(fn, imports, boxes, api_fns):
    """Why `fn` itself is not pure: a call of an api function or of the network, a `global`, a module container
    changed (raising an api exception is not an effect)."""
    why = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Global):
            why.append(f"global {', '.join(n.names)}")
        elif isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in imports:
                mod, name = imports[f.id]
                if (mod == API and name in api_fns) or (mod or "").split(".")[0] in NET:
                    why.append(f"calls {f.id}")
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in imports:
                mod, name = imports[f.value.id]
                if name is None and ((mod == API and f.attr in api_fns) or mod.split(".")[0] in NET):
                    why.append(f"calls {f.value.id}.{f.attr}")
    return why + [f"changes {name}" for _l, name in _mutations(fn, boxes)]


def r9(trees):
    """[(path:line name, why)]: a function whose docstring says "Pure" reaching, through the calls it names, an api
    or network name, a `global`, or a module container changed in place."""
    modules = {_module_of(p) for p in trees}
    api_tree = next((t for p, (t, _s) in trees.items() if _module_of(p) == API), None)
    api_fns = {n.name for n in api_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))} \
        if api_tree is not None else set()
    defs, calls, effects = {}, defaultdict(set), {}
    for path, (tree, _src) in trees.items():
        mod, imports, boxes = _module_of(path), _imports(path, tree, modules), _containers(tree)
        top = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            key = (mod, fn.name, fn.lineno)
            defs[key] = (path, fn)
            effects[key] = _effects(fn, imports, boxes, api_fns)
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                if isinstance(f, ast.Name) and f.id in top:
                    calls[key].add((mod, f.id))
                elif isinstance(f, ast.Name) and f.id in imports and imports[f.id][1]:
                    calls[key].add(imports[f.id])
                elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in imports \
                        and imports[f.value.id][1] is None:
                    calls[key].add((imports[f.value.id][0], f.attr))
    top_level = {}
    for key, (path, fn) in defs.items():
        top_level.setdefault(key[:2], key)       # the first def of a name in a module (module level comes first)
    out = []
    for key, (path, fn) in defs.items():
        if not (ast.get_docstring(fn) or "").startswith("Pure"):
            continue
        seen, todo, why = {key}, [key], None
        while todo and why is None:
            k = todo.pop()
            if effects[k]:
                why = f"{k[1]}: {effects[k][0]}"
            for c in calls[k]:
                nxt = top_level.get(c)
                if nxt is not None and nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        if why is not None:
            out.append((f"{path}:{fn.lineno} {fn.name}", why))
    return sorted(out)


# -- R10 bench budgets -----------------------------------------------------------------------------------------------
BUDGET_WORDS = ("budget", "est", "budget_s", "est_s")
BENCH = "bench" + os.sep


def r10(trees):
    """[(path:line, what)] in bench/: a row's budget or estimate written as a number (a keyword, a dict key, a
    parameter default); it comes from production's estimate × TARGET_SLACK."""
    out = []
    for path, (tree, _src) in trees.items():
        if not path.startswith(BENCH):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.keyword) and n.arg in BUDGET_WORDS and _number(n.value) is not None:
                out.append((f"{path}:{n.value.lineno}", f"{n.arg}={_number(n.value)}"))
            elif isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value in BUDGET_WORDS and _number(v) is not None:
                        out.append((f"{path}:{v.lineno}", f"{k.value}: {_number(v)}"))
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                pos = n.args.args[len(n.args.args) - len(n.args.defaults):]
                for a, d in [*zip(pos, n.args.defaults), *zip(n.args.kwonlyargs, n.args.kw_defaults)]:
                    if d is not None and a.arg in BUDGET_WORDS and _number(d) is not None:
                        out.append((f"{path}:{d.lineno}", f"{a.arg}={_number(d)} (default)"))
    return sorted(out)


# -- R11 (E5) ---------------------------------------------------------------------------------------------------------
ABANDON_WAYS = ("cover", "replan")      # skill.ABANDON_WAYS


def r11(trees):
    """[(path:line, what)] of skills with no budget, an unknown abandon, or cover without danger."""
    out = []
    for path, (tree, _src) in trees.items():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in fn.decorator_list:
                if not (isinstance(d, ast.Call) and getattr(d.func, "id", None) == "skill"):
                    continue
                kws = {k.arg: k.value for k in d.keywords}
                where = f"{path}:{d.lineno} {fn.name}"
                if "budget" not in kws:
                    out.append((where, "no budget: no time limit"))
                way = kws.get("abandon")
                given = way.value if isinstance(way, ast.Constant) else None if way is None else "?"
                if given is not None and given not in ABANDON_WAYS:
                    out.append((where, f"abandon {given!r}: not one of {ABANDON_WAYS}"))
                flag = kws.get("soft")
                soft = isinstance(flag, ast.Constant) and flag.value is True
                if given == "cover" and not (soft or "fights" in kws):
                    out.append((where, "cover after giving up, with no danger: replan"))
    return sorted(out)


PRICE_NAME = re.compile(r"(^|_)(price|prices|cost|costs|estimate|estimates)(_|$)|_(s|ticks)$")
CHECK = os.path.join(HERE, "check")


def _from_bonobo(tree):
    """Names a module binds from bonobo (`from bonobo… import x`, `import bonobo…`), anywhere in it."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "bonobo":
            out.update(a.asname or a.name for a in n.names)
        elif isinstance(n, ast.Import):
            out.update((a.asname or a.name).split(".")[0] for a in n.names if a.name.split(".")[0] == "bonobo")
    return out


def _root(f):
    while isinstance(f, ast.Attribute):
        f = f.value
    return f.id if isinstance(f, ast.Name) else None


def r12(trees):
    """[(path:line, name)] in check/: a function named for a price that calls nothing production's."""
    out = []
    for path, (tree, _src) in trees.items():
        ours = _from_bonobo(tree)
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and PRICE_NAME.search(n.name):
                local = ours | _from_bonobo(n)
                if not any(isinstance(c, ast.Call) and _root(c.func) in local for c in ast.walk(n)):
                    out.append((f"{path}:{n.lineno}", n.name))
    return sorted(out)


RULES = {"R1": r1, "R2": r2, "R3": r3, "R4": r4, "R5": r5, "R6": r6, "R7": r7, "R8": r8, "R9": r9, "R10": r10,
         "R11": r11, "R12": r12}
ENTRY = "mc.py"         # production beside bonobo/: its uses keep code alive (R6)


def parse(srcs):
    return {p: (ast.parse(s), s) for p, s in srcs.items()}


def hits():
    """{rule: its hits on bonobo/}."""
    trees = parse(sources())
    with open(os.path.join(HERE, ENTRY)) as fh:
        production = dict(trees, **parse({os.path.join(os.pardir, ENTRY): fh.read()}))
    checker = parse(sources(CHECK))
    return {k: (fn(trees, production) if fn is r6 else fn(checker) if fn is r12 else fn(trees))
            for k, fn in RULES.items()}


def run_row(rule, srcs):
    """One ROWS row: the rule's hits on the given sources."""
    return RULES[rule](parse({k: v + "\n" for k, v in srcs.items()}))


# (rule, {path: source}, hits?): each rule's holding rows and its must-fail rows
ROWS = [
    ("R12", {"c.py": "def exact_s(x):\n from bonobo.planner import plan_candidates\n return plan_candidates(x)[0][1]"},
     False),                                                                        # production's price, called
    ("R12", {"c.py": "def walk_s(d):\n return d / 4.3"}, True),                    # must fail: a model of its own
    ("R12", {"c.py": "from bonobo import nav\ndef way_cost(a, b):\n return nav.least_way_s(a, b)"}, False),
    ("R12", {"c.py": "def price(step):\n return {'mine': 60}[step]"}, True),       # must fail: a price table
    ("R12", {"c.py": "def judge(x):\n return x + 1"}, False),                      # not a price
    ("R1", {"a.py": "def f(): pass", "b.py": "def g(): pass"}, False),
    ("R1", {"a.py": "def f(): pass", "b.py": "def f(): pass"}, True),               # must fail
    ("R1", {"a.py": "class A:\n def f(self): pass", "b.py": "def f(): pass"}, False),
    ("R1", {"a.py": "def main(): pass\nif __name__ == '__main__': main()",
                "b.py": "def main(): pass\nif __name__ == '__main__': main()"}, False),
    ("R1", {"a.py": "def main(): pass", "b.py": "def main(): pass"}, True),         # must fail: no entry guard
    ("R2", {"a.py": "def f(x):\n y = x.a\n z = y + 1\n return z",
                "b.py": "def g(p):\n q = p.a\n r = q + 1\n return r"}, True),           # must fail: renamed copy
    ("R2", {"a.py": "def f(x):\n y = x.a\n z = y + 1\n return z",
                "b.py": "def g(p):\n q = p.b\n r = q + 1\n return r"}, False),          # another field
    ("R2", {"a.py": "def f(x):\n return x", "b.py": "def g(y):\n return y"}, False),   # a one-liner
    ("R3", {"a.py": "import m\nX = m.X"}, True),                                    # must fail
    ("R3", {"a.py": "import m\nX = m.Y"}, False),
    ("R3", {"a.py": "from .b import *"}, True),                                     # must fail
    ("R3", {"a.py": "from .b import n  # noqa: F401", "c.py": "from .a import n"}, True),   # must fail
    ("R3", {"a.py": "from .b import n  # noqa: F401", "c.py": "from .b import n"}, False),  # side effect only
    ("R4", {"a.py": "STEP_S = 3", "b.py": "STEP_S = 3"}, True),                     # must fail: one name twice
    ("R4", {"a.py": "STEP_S = 3", "b.py": "STEP_S = 4"}, True),                     # must fail: a contradiction
    ("R4", {"a.py": "ROOM_REACH = 12", "b.py": "BRIDGE_REACH = 12"}, True),         # must fail: same word, value
    ("R4", {"a.py": "ROOM_REACH = 12", "b.py": "BRIDGE_REACH = 11"}, False),
    ("R4", {"a.py": "ROOM_REACH = 12", "b.py": "ROOM_SIZE = 12"}, False),
    ("R4", {"a.py": "FILE = p('a')", "b.py": "FILE = p('b')"}, False),               # a name, not a number
    ("R4", {"a.py": "FIGHT_POLL_S = 0.1", "b.py": "FORCED_WORTH_S = 0.1"}, False),   # another suffix
    ("R4", {"a.py": "SAME_R = 4", "b.py": "LIGHT_R = 4"}, False),                   # a unit alone
    ("R4", {"a.py": "FIGHT_POLL_S = 0.1", "b.py": "CELL_POLL_S = 0.1"}, True),      # must fail
    ("R5", {"a.py": "s = t / 20"}, True),                                           # must fail
    ("R5", {"data.py": "s = t / 20"}, False),
    ("R5", {"a.py": "s = t / TICKS_PER_S"}, False),
    ("R5", {"a.py": "dark = s.get('skyLight', 15) <= 7"}, True),                    # must fail
    ("R5", {"a.py": "dark = s.get('skyLight', 15) <= DARK"}, False),
    ("R5", {"a.py": "T = {'minecraft:cow': 3, 'minecraft:pig': 2}"}, True),         # must fail: a game table
    ("R5", {"a.py": "T = {'keep': 3, 'spare': 2}"}, False),                         # a policy table, by token
    ("R5", {"a.py": "@skill(needs={'minecraft:bucket': 1})\ndef f(): pass"}, False),  # a declaration, not a table
    ("R5", {"a.py": "FINDS = {'diamond': 0}\ndef f():\n FINDS['diamond'] += 1"}, False),   # state a function bumps
    ("R5", {"a.py": "T = {'iron': 250, 'stone': 131}"}, True),                      # must fail: by material,
    ("R6", {"a.py": "def f(): pass", "b.py": "from .a import f\nf()"}, False),
    ("R6", {"a.py": "def f(): pass"}, True),                                        # must fail: never named
    ("R6", {"a.py": "def f():\n return f()"}, True),                                # must fail: only itself
    ("R6", {"a.py": "def f_row(): pass", "b.py": "print(globals()[f'{t}_row'])"}, False),   # a composed name
    ("R6", {"a.py": "def _gain(): pass", "b.py": "print(('gain', 2))"}, False),      # a word of a row
    ("R6", {"a.py": "def f(): pass\n__all__ = ['f']"}, True),                       # must fail: exported only
    ("R6", {"a.py": "@register\ndef f(): pass"}, False),
    ("R7", {"a.py": "try:\n x()\nexcept KeyError:\n pass"}, True),                 # must fail
    ("R7", {"a.py": "def f():\n try:\n  x()\n except OSError:\n  return []"}, True),   # must fail: a default
    ("R7", {"a.py": "def f():\n try:\n  x()\n except OSError:\n  return set()"}, True),  # must fail: an empty set
    ("R7", {"a.py": "def f():\n try:\n  x()\n except OSError:\n  return set(y)"}, False),
    ("R7", {"a.py": "try:\n x()\nexcept KeyError as e:\n raise Bad() from e"}, False),
    ("R7", {"a.py": "try:\n x()\nexcept KeyError:\n events.anomaly('x')\n raise"}, False),
    ("R7", {"a.py": "def f():\n try:\n  x()\n except KeyError:\n  events.anomaly('x')\n  return None"}, False),
    ("R8", {"a.py": "SEEN = {}\ndef f(k):\n SEEN[k] = 1"}, True),                   # must fail
    ("R8", {"a.py": "SEEN = set()\ndef f(k):\n SEEN.add(k)"}, True),               # must fail
    ("R8", {"a.py": "SEEN = {}\ndef f(k):\n SEEN[k] = 1\nlifecycle.in_place(__name__, 'SEEN')"}, False),
    ("R8", {"a.py": "SEEN = {}\ndef f(k):\n SEEN = {}\n SEEN[k] = 1"}, False),     # a local of its own
    ("R8", {"a.py": "SEEN = {}\ndef f(k):\n return SEEN.get(k)"}, False),
    ("R8", {"a.py": "SEEN = {}\ndef reg(fn):\n SEEN[fn.__name__] = fn\n return fn\n@reg\ndef g(): pass"}, False),
    ("R8", {"a.py": "SEEN = {}\ndef reg(fn):\n SEEN[fn.__name__] = fn\ndef g():\n reg(g)"}, True),  # must fail
    ("R8", {"a.py": "SEEN = {}\ndef reg(k):\n def wrap(fn):\n  SEEN[k] = fn\n  return fn\n return wrap\n"
                    "@reg('x')\ndef g(): pass"}, False),                              # a decorator factory
    ("R9", {"a.py": "def f(x):\n 'Pure: x.'\n return x + 1"}, False),
    ("R9", {"api.py": "def get(x): pass", "a.py": "from . import api\ndef f(x):\n 'Pure: x.'\n return api.get(x)"}, True),   # must fail
    ("R9", {"api.py": "def get(x): pass", "a.py": "from . import api\ndef g(x):\n return api.get(x)\ndef f(x):\n 'Pure.'\n return g(x)"},
     True),                                                                            # must fail: through a call
    ("R9", {"api.py": "class NotAvailable(Exception): pass",
            "a.py": "from .api import NotAvailable\ndef f(x):\n 'Pure.'\n raise NotAvailable(x)"}, False),
    ("R9", {"a.py": "N = []\ndef f(x):\n 'Pure.'\n N.append(x)"}, True),            # must fail: module state
    ("R9", {"a.py": "def f(x):\n 'Pure.'\n global N\n N = x"}, True),               # must fail
    ("R10", {"bench/a.py": "ROW = dict(name='x', budget=25)"}, True),               # must fail
    ("R10", {"bench/a.py": "ROW = dict(name='x', budget=est('x') * TARGET_SLACK)"}, False),
    ("R10", {"bench/a.py": "def row(name, budget=30): pass"}, True),                # must fail: a default
    ("R10", {"a.py": "ROW = dict(name='x', budget=25)"}, False),                    # not the bench
    ("R11", {"a.py": "@skill(budget=60)\ndef f(): pass"}, False),                  # bounded, replanned after
    ("R11", {"a.py": "@skill(needs={})\ndef f(): pass"}, True),                    # must fail: no time limit
    ("R11", {"a.py": "@skill(budget=60, abandon='wander')\ndef f(): pass"}, True),  # must fail: no next step
    ("R11", {"a.py": "@skill(budget=60, abandon='cover')\ndef f(): pass"}, True),  # must fail: cover for nothing
    ("R11", {"a.py": "@skill(budget=60, soft=True, abandon='cover')\ndef f(): pass"}, False),   # a fight's
    ("R11", {"a.py": "@skill(budget=60, fights=g, abandon='cover')\ndef f(): pass"}, False),
]


KNOWN = os.path.join(HERE, "check", "static_known.txt")     # "RULE COUNT" per line: the hits a rule may still have


def known(path=KNOWN):
    """{rule: count} of the baseline (counts only fall)."""
    with open(path) as fh:
        return {k: int(v) for k, v in (ln.split() for ln in fh if ln.strip() and not ln.startswith("#"))}


def grew(found, baseline):
    """[(rule, hits, known)] of every rule over its known count."""
    return [(r, len(rows), baseline.get(r, 0)) for r, rows in found.items() if len(rows) > baseline.get(r, 0)]


def main():
    found = hits()
    for rule, rows in found.items():
        print(f"{rule}: {len(rows)}")
        for row in rows:
            print("   ", row)
    over = grew(found, known())
    for rule, n, k in over:
        print(f"{rule}: {n} > known {k}")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())

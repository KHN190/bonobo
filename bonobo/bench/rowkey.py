"""A bench row's readiness key, parts that do not depend on this checkout: the row's own definition (`row_hash`)
and the production functions it reaches (`reach_hash`, over any package directory — another checkout's, when old
verdicts are migrated to a new key format). Standard library only, so an old checkout can load it by path."""
import ast
import hashlib
import os
import re

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_INDEX = {}
NOT_PRODUCTION = ("scenarios",)          # and every bench module: the row's own definition covers what it uses


def code_index(pkg_dir=PKG):
    """{name: [source]} of every function, method and class in the production modules (not scenarios, not the
    bench): the graph a row's key walks, by name (a name defined twice counts both: over-reaching is safe)."""
    if pkg_dir in _INDEX:
        return _INDEX[pkg_dir]
    index = {}
    for fname in sorted(os.listdir(pkg_dir)):
        mod = fname[:-3]
        if not fname.endswith(".py") or mod in NOT_PRODUCTION:
            continue
        with open(os.path.join(pkg_dir, fname), encoding="utf-8") as f:
            text = f.read()
        tree = ast.parse(text)
        # Top-level functions and constants, and the methods of top-level classes (a nested def is its
        # function's own business — a local named like it pulled the dragon fight into a log chop's key).
        nodes = list(tree.body) + [m for c in tree.body if isinstance(c, ast.ClassDef) for m in c.body]
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index.setdefault(node.name, []).append(f"{mod}.{ast.get_source_segment(text, node)}")
            elif isinstance(node, ast.Assign) and node in tree.body:
                for t in node.targets:          # module constants a function reads (tables, thresholds)
                    if isinstance(t, ast.Name):
                        index.setdefault(t.id, []).append(f"{mod}.{ast.get_source_segment(text, node)}")
    _INDEX[pkg_dir] = index
    return index


_BUILTINS = set(dir(__import__("builtins")))
_MODULES = {f[:-3] for f in os.listdir(PKG) if f.endswith(".py")}      # module names are stable across checkouts


@__import__("functools").lru_cache(maxsize=None)
def _names_in(source):
    """Pure: every name and attribute a source mentions, and its string constants (skill names passed as strings)."""
    import textwrap
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        # A lambda cut out of a dict literal is not a statement: every identifier and quoted word it holds.
        return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", source))
    # What is called (f(), x.f()) or read as a constant (ALL_CAPS): a local
    # variable named like a function elsewhere is not a reference to it.
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id not in _BUILTINS:
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                # `api.run(...)`: that module's run, not every run in the package
                owner = f.value.id if isinstance(f.value, ast.Name) else None
                out.add(f"{owner}.{f.attr}" if owner in _MODULES else f.attr)
        elif isinstance(n, ast.Name) and n.id.isupper():
            out.add(n.id)
        elif isinstance(n, ast.Attribute) and n.attr.isupper():
            out.add(n.attr)
    return out


def _strings_in(source):
    """Pure: the identifier-like strings a source holds (a skill named as `_skill("chop")`)."""
    return set(re.findall(r"""["']([A-Za-z_][A-Za-z0-9_:]*)["']""", source))


COMMON = 3        # a name defined in this many places is a word (get, run, count), not a call: not followed


def reached(roots, index):
    """Pure: the sources reachable from `roots` (names) through the names each source mentions. A name defined
    COMMON or more times is not followed: every "get" in the package is not every row's code."""
    seen, todo, out = set(), list(roots), []
    while todo:
        ref = todo.pop()
        mod, _, name = ref.rpartition(".")
        if ref in seen or name not in index:
            continue
        seen.add(ref)
        srcs = [src for src in index[name] if src.startswith(mod + ".")] if mod else index[name]
        if not mod and len(srcs) >= COMMON:
            continue
        for src in srcs:
            if src not in out:
                out.append(src)
                todo.extend(_names_in(src.split(".", 1)[1]))
    return sorted(out)


def _callable_sources(obj, depth=0, seen=None):
    """The source of a row's callable and of what its closure holds (functions, tuples of them, values)."""
    import inspect
    seen = set() if seen is None else seen
    if id(obj) in seen or depth > 5:
        return []
    seen.add(id(obj))
    if callable(obj) and hasattr(obj, "__code__"):
        try:
            out = [inspect.getsource(obj)]
        except (OSError, TypeError):
            out = [obj.__qualname__]
        for cell in obj.__closure__ or ():
            try:
                out += _callable_sources(cell.cell_contents, depth + 1, seen)
            except ValueError:
                pass
        return out
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [x for o in obj for x in _callable_sources(o, depth + 1, seen)]
    if isinstance(obj, dict):
        return [x for o in obj.items() for x in _callable_sources(o, depth + 1, seen)]
    return [repr(obj)]


def row_hash(sc):
    """The row's own definition: its commands, what it expects and its limits, and the source of its run, check
    and hooks with everything their closures hold."""
    parts = [repr((sc.get("setup"), sc.get("expect"), sc.get("budget"), sc.get("fails"), sc.get("tier"),
                   sc.get("dimension"), sc.get("tick_rate"), sc.get("skills")))]
    for key in ("run", "check", "before"):
        parts += _callable_sources(sc.get(key))
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:6]


def reach_hash(sc, index=None, registry=None, pkg_dir=PKG):
    """The production code the row reaches: from its skills (their contracts' functions) and every name its run,
    check and hooks mention, through the call graph (`reached`). `registry`: the skill registry of the code the row
    belongs to (another checkout's, when migrating)."""
    if registry is None:
        from .. import skill as skillkit
        registry = registry
    index = code_index(pkg_dir) if index is None else index
    roots = set()
    named = set()
    for key in ("run", "check", "before"):
        for src in _callable_sources(sc.get(key)):
            roots |= _names_in(src)
            named |= _strings_in(src)
    for entry in list(sc.get("skills", ())) + sorted(n for n in named if n in registry):
        c = registry.get(entry)
        if c is not None:
            roots.add(c.fn.__name__)
        roots |= {c2.fn.__name__ for c2 in registry.values() if entry in (c2.provides or {})}
    return hashlib.sha1("\n".join(reached(roots, index)).encode()).hexdigest()[:10]

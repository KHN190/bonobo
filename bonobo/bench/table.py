"""The one interpreter: a bench table row (plain data in `vocab`'s words) → the row dict the runner and SCENARIOS
use (setup, run, check, before, budget, tier, …). Nothing here decides; it only reads the words back into the
sheet's own factories and `vocab`'s predicates."""
import importlib
import sys

from . import vocab

TIERS = ("core", "common", "brain", "combat", "exception", "acceptance")
TABLES = {t: f"bonobo.bench.bench_{t}" for t in TIERS}
RUNS = ("skill", "skill_bare", "do")          # the run words a lambda was written in (the rest: sheet factories)
WORDS = ("hooks", "named_all", "interrupt_when", "iter", "constant", "now_api")    # the interpreter's own words
MARKERS = ("$api", "$inv", "$ctx")


def _scen():
    return sys.modules.get("bonobo.scenarios") or importlib.import_module("bonobo.scenarios")


def resolve(name):
    """A word → the sheet's function: "mod.path:attr", a dotted module function, or a sheet name (its own, or
    with the leading underscore the table drops)."""
    if ":" in name:
        mod, attr = name.split(":")
        return getattr(importlib.import_module(mod), attr)
    scen = _scen()
    if hasattr(scen, name):
        return getattr(scen, name)
    if hasattr(scen, "_" + name):
        return getattr(scen, "_" + name)
    if "." in name:
        mod, attr = name.rsplit(".", 1)
        return getattr(importlib.import_module(mod), attr)
    raise KeyError(f"no word {name!r}")


# -- values -------------------------------------------------------------------------------------------------------
def dec(v):
    """Data → value: positions, nested callables ("!kind", ...), containers."""
    if isinstance(v, tuple) and v and isinstance(v[0], str):
        if v[0] == "@" and len(v) == 4:
            return vocab.pos(v)
        if v[0].startswith("!"):
            return make((v[0][1:],) + tuple(v[1:]))
        if v[0].startswith("&") and len(v) == 1:
            return resolve(v[0][1:])
    if isinstance(v, list):
        return [dec(x) for x in v]
    if isinstance(v, tuple):
        return tuple(dec(x) for x in v)
    if isinstance(v, dict):
        return {k: dec(x) for k, x in v.items()}
    return v


def _arg(a, ctx):
    """A run argument at run time: the context markers read, helpers called."""
    if a == "$ctx":
        return ctx
    if isinstance(a, tuple) and a and a[0] == "$ctx":
        return getattr(ctx, a[1])
    if isinstance(a, tuple) and a and a[0] == "$call":
        return resolve(a[1])(*[_arg(x, ctx) for x in a[2:]])
    return dec(a)


def _pred(kind, args):
    """A predicate word → check(api, inv)."""
    if kind == "all":
        parts = [dec(a) for a in args]
        return lambda api, inv: all(p(api, inv) for p in parts)
    if kind == "any":
        parts = [dec(a) for a in args]
        return lambda api, inv: any(p(api, inv) for p in parts)
    if kind == "not":
        p = dec(args[0])
        return lambda api, inv: not p(api, inv)
    if kind == "now_api":
        p = dec(args[0])

        def now_api():
            from .. import api
            return p(api, None)
        return now_api
    if kind == "now":
        p = dec(args[0])

        def now():
            from .. import api
            from ..world import Inventory
            return p(api, Inventory())
        return now
    f = vocab.PREDICATES[kind]
    vals = [dec(a) for a in args]
    if kind == "call":
        return lambda api, inv: f(api, inv, *vals, resolve=resolve)
    return lambda api, inv: f(api, inv, *vals)


def _run(kind, args):
    if kind in ("skill", "skill_bare"):
        name, rest = args[0], args[1:]

        def skill_run(ctx):
            fn = resolve("_skill")(name)
            vals = [_arg(a, ctx) for a in rest]
            return fn(ctx, *vals) if kind == "skill" else fn(*vals)
        return skill_run
    target, pargs, kwargs = args

    def do(ctx):
        return resolve(target)(*[_arg(a, ctx) for a in pargs], **{k: _arg(v, ctx) for k, v in kwargs.items()})
    return do


def make(item):
    """(kind, *args) → the callable it names, carrying its own data (`__table__`: what `tabulate` reads back)."""
    if item[0].startswith("&"):
        return resolve(item[0][1:])            # the function itself, not a call
    f = _make(item)
    try:
        f.__table__ = tuple(item)
    except AttributeError:
        pass
    return f


def _make(item):
    kind, args = item[0], item[1:]
    if kind in vocab.PREDICATES or kind in vocab.LOGIC or kind == "now_api":
        return _pred(kind, args)
    if kind in RUNS:
        return _run(kind, args)
    if kind == "hooks":
        return resolve("_hooks")(*[dec(a) for a in args])
    if kind == "named_all":
        return resolve("_named_all")([(dec(c), why) for why, c in args])
    if kind == "iter":
        vals = dec(args[0])
        return lambda *_a, **_k: iter(vals)
    if kind == "constant":
        val = dec(args[0])
        return lambda *_a, **_k: val
    if kind == "interrupt_when":
        return resolve("_interrupt_when")(*[dec(a) for a in args])
    return resolve(kind)(*[dec(a) for a in args])


# -- rows ---------------------------------------------------------------------------------------------------------
def _slot(items, wrap):
    """A slot's list (check, before) → one callable: the item alone, or `wrap` over them."""
    made = [make(i) for i in items]
    return made[0] if len(made) == 1 else wrap(made)


def build(row, tier):
    """A table row → the runner's row dict."""
    from .runner import ROW_LIMIT_S
    out = {"doc": row["doc"], "module": row["module"], "setup": vocab.scene(row["scene"]),
           "run": make(row["run"]), "budget": row["budget"], "tier": tier}
    if "why" in row:
        out["check"] = resolve("_named_all")([(make(c), w) for c, w in zip(row["check"], row["why"])])
    else:
        out["check"] = _slot(row["check"], lambda ps: resolve("_all")(*ps))
    if "before" in row:
        out["before"] = _slot(row["before"], lambda hs: resolve("_hooks")(*hs))
    if "queue" in row:
        out["queue"] = dec(row["queue"])
    if "detail" in row:
        out["detail"] = make(row["detail"])
    for k, v in row.items():
        if k not in out and k not in ("name", "scene", "why", "no_detail"):
            out[k] = dec(v)
    if tier != "acceptance":
        out["budget"] = min(out["budget"], ROW_LIMIT_S)
    return out


def rows(tier):
    """{name: row data} of one tier's table."""
    mod = importlib.import_module(TABLES[tier])
    return {r["name"]: r for r in mod.ROWS}


def sheet():
    """{name: runner row} of every table (not wired to the runner yet)."""
    return {name: build(r, t) for t in TIERS for name, r in rows(t).items()}

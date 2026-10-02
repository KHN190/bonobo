"""The one interpreter and the bench's one sheet: every tier's table (bench_<tier>.py) read into the runner's row
dicts (setup, run, check, before, budget, tier, …) — SCENARIOS, built here at import and nowhere else. Nothing here
decides; it reads the words back into vocab's helpers and predicates. The bench's other names (the runner's, the
helpers') are reached through here too: `from bonobo.bench import table as sheet`."""
import importlib
from typing import Any
from . import core, vocab
from .core import bag_now, kit_jobs
from .words.scene import resolve
from .words import checks as words_checks, runs as words_runs, scene as words_scene
TIERS = ("core", "common", "brain", "combat", "exception", "acceptance")
TABLES = {t: f"bonobo.bench.bench_{t}" for t in TIERS}
RUNS = ("skill", "skill_bare", "do", "seq", "remember", "pause")          # the run words a lambda was written in (the rest: sheet factories)
WORDS = ("hooks", "named_all", "interrupt_when", "iter", "constant", "now_api", "thunk", "api_only")    # the interpreter's own words

# -- values -------------------------------------------------------------------------------------------------------
def dec(v) -> Any:
    """Data → value: positions, nested callables ("!kind", ...), containers."""
    if isinstance(v, tuple) and v and isinstance(v[0], str):
        if v[0] == "@" and len(v) == 4:
            return core.pos(v)
        if v[0] == "$set" and len(v) == 2:
            return set(dec(v[1]))
        if v[0] == "$data" and len(v) == 2:
            from .. import paths
            return paths.data(v[1])
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
    if kind == "api_only":
        p = dec(args[0])
        return lambda api, inv: p(api, None)
    if kind == "thunk":
        p = dec(args[0])
        return lambda api, inv: p()
    if kind == "now_api":
        p = dec(args[0])
        def now_api():
            from .. import api
            return p(api, None)
        return now_api
    if kind == "now":
        return words_checks._now(dec(args[0]))
    f = words_checks.PREDICATES[kind]
    vals = [dec(a) for a in args]
    if kind == "call":
        return lambda api, inv: f(api, inv, *vals, resolve=resolve)
    return lambda api, inv: f(api, inv, *vals)

RESUMES_AFTER = (None, "fight")     # the arbiter's "what first" for which a run word calls its work again


def absorbed(err):
    """An interruption a run word absorbed (resumed without raising): counted for the row (INTERRUPTS, as `_resume`
    counts the ones that reach it) and said — resumed silently, the interrupt rows read 0 caught."""
    from .. import api
    from .words.checks import BASE, INTERRUPTS
    row = BASE.get("name")
    if row is not None:
        INTERRUPTS[row] = INTERRUPTS.get(row, 0) + 1
    api.detail(f"bench: {row} run word absorbed {type(err).__name__} ({err}), resumed ({INTERRUPTS.get(row, 0)})")


def resuming(call, holder=None, sleep=None, poll=0.1, on_absorb=absorbed):
    """Run `call` to its end as the brain would (Brain.attempt): a McError the arbiter resumes — a faster layer took
    the body (a fight: CommitmentExpired, an interrupt) — waits for the body to be handed back and calls again, until
    the row's own limit stops it; the row judges the world at the end. Anything else is raised as before.
    collect_blaze_rods ended at 0 s on the fight's first bid ('a faster layer took the body'), no rod collected."""
    from .. import arbiter, retry
    from ..api import McError
    import time
    holder = holder or arbiter.BODY.holder
    sleep = sleep or time.sleep
    while True:
        try:
            return call()
        except McError as e:
            source = retry.source_of(e)
            resumes, first = arbiter.resume_of(source) if source in arbiter.RESUME_OF else (False, None)
            if not resumes or first not in RESUMES_AFTER:
                raise
            on_absorb(e)
            while holder() is not None:
                sleep(poll)


def _run(kind, args):
    if kind == "seq":
        at_, steps = args[0], [dec(a) for a in args[1:]]
        return lambda ctx: (lambda done: tuple(done) if at_ is None else done[at_])([st(ctx) for st in steps])
    if kind == "remember":
        method, pargs, kwargs = args
        return lambda ctx: getattr(ctx.mem, method)(*[_arg(a, ctx) for a in pargs],
                                                   **{k: _arg(v, ctx) for k, v in kwargs.items()})
    if kind == "pause":
        import time
        return lambda ctx=None: time.sleep(args[0])
    if kind in ("skill", "skill_bare"):
        name, rest = args[0], args[1:]
        def skill_run(ctx):
            fn = resolve("_skill")(name)
            vals = [_arg(a, ctx) for a in rest]
            return resuming(lambda: fn(ctx, *vals) if kind == "skill" else fn(*vals))
        return skill_run
    target, pargs, kwargs = args
    def do(ctx):
        return resuming(lambda: resolve(target)(*[_arg(a, ctx) for a in pargs],
                                                **{k: _arg(v, ctx) for k, v in kwargs.items()}))
    return do

def make(item):
    """(kind, *args) → the callable it names, carrying its own data (`__table__`: what the tests read back). A
    one-off row's code is taken as it is."""
    if callable(item):
        return item
    if item[0].startswith("&"):
        return resolve(item[0][1:])            # the function itself, not a call
    if item[0] in words_scene.WORDS:
        return words_scene.WORDS[item[0]](*[dec(a) for a in item[1:]])     # the old sheet's own callable, as it made it
    f = _make(item)
    try:
        f.__table__ = tuple(item)
    except AttributeError:
        pass
    return f

def _make(item):
    kind, args = item[0], item[1:]
    if kind in words_runs.HOOKS:
        return words_runs.HOOKS[kind]
    if kind in words_scene.WORDS:
        return words_scene.WORDS[kind](*[dec(a) for a in args])
    if kind in words_checks.PREDICATES or kind in words_checks.LOGIC or kind in ("now_api", "thunk", "api_only"):
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
    if callable(items):
        return items
    made = [make(i) for i in items]
    return made[0] if len(made) == 1 else wrap(made)

def build(row, tier):
    """A table row → the runner's row dict."""
    from .runner import ROW_LIMIT_S
    setup = list(row["setup"]) if "scene" not in row else words_scene.scene(row["scene"])    # a one-off row: its commands
    out = {"doc": row["doc"], "module": row["module"], "setup": setup,
           "run": make(row["run"]), "budget": row["budget"], "tier": tier}
    if "why" in row and not callable(row["check"]):
        out["check"] = resolve("_named_all")([(make(c), w) for c, w in zip(row["check"], row["why"])])
    else:
        out["check"] = _slot(row["check"], lambda ps: resolve("_all")(*ps))
    if "before" in row:
        out["before"] = _slot(row["before"], lambda hs: resolve("_hooks")(*hs))
    if "queue" in row:
        out["queue"] = dec(row["queue"])
    if "detail" in row:
        out["detail"] = make(row["detail"])
    jobs = kit_jobs(row)
    if jobs:                                 # the kit rule: the best work tool per job, the sword a fight calls for
        out["setup"] = out["setup"] + resolve("_kit_gives")(out, jobs)
    for k, v in row.items():
        if k not in out and k not in ("name", "scene", "why", "no_detail", "kit"):
            out[k] = dec(v)
    if "expect" not in out and not out.get("raw"):
        out["expect"] = words_scene.scene_expect(out["setup"])     # what its own scene built, the one signature
    out.setdefault("skills", [])            # a row that proves no one skill carries an empty list
    out.setdefault("point", "A")
    if tier != "acceptance":
        out["budget"] = min(out["budget"], ROW_LIMIT_S)
    return out

def expand(families):
    """[(template, [params, ...])] → {name: row data}: one entry, many rows."""
    out = {}
    for template, params in families:
        for p in params:
            row = vocab.TEMPLATES[template](vocab.NAMES[template](*p), *(p[1:] if template in vocab.NAMED else p))
            out[row["name"]] = row
    return out

def rows(tier):
    """{name: row data} of one tier's table: its families expanded, its rows in words, its rows in code."""
    mod = importlib.import_module(TABLES[tier])
    out = expand(getattr(mod, "FAMILIES", ()))
    code = getattr(mod, "CODE_ROWS", ())      # a list, or a fn building it
    made = list(code) if isinstance(code, (list, tuple)) else code()
    for r in list(getattr(mod, "ROWS", ())) + made:
        if r["name"] in out:
            raise ValueError(f"{r['name']} made twice in the {tier} table")
        out[r["name"]] = r
    return out

def sheet():
    """{name: runner row} of every table."""
    return {name: build(r, t) for t in TIERS for name, r in rows(t).items()}

def load():
    """The one SCENARIOS (bench.core's dict, which the runner reads): every table's rows, built."""
    built = sheet()
    core.SCENARIOS.clear()
    core.SCENARIOS.update(built)
    return core.SCENARIOS

SCENARIOS = load()

"""The bench tables' one vocabulary: scene templates (→ console commands), predicates (→ checks over the world and
the bag), and the names a row's run, hooks and interrupts are written in. Rows (bench_<tier>.py) hold only data in
these words; `table.py` turns them into the runner's row dicts.

Data conventions, one each:
- a position relative to the bench ORIGIN is ("@", dx, dy, dz) — `at(dx, dy, dz)` in the old sheet;
- a callable written inside another's arguments is a tuple whose head is "!" + its kind: ("!gain", "log", 2);
  at the top of a row's `check`, `before` and `run` the head is the bare kind (the slot says what it is);
  ("&name",) is the function `name` itself, not a call of it;
- ("$data", "bench/siege.jsonl") is a file in this player's data directory (paths.data);
- "$api", "$inv", "$ctx" stand for the objects a predicate or a run is called with; ("$ctx", "policy") is an
  attribute of the context, ("$call", name, *args) a helper called at run time (its args may hold these markers).
"""
import operator

from .core import ORIGIN, at

# -- positions ----------------------------------------------------------------------------------------------------
def pos(p):
    """("@", dx, dy, dz) → the absolute position; anything else unchanged."""
    return at(*p[1:]) if isinstance(p, tuple) and len(p) == 4 and p[0] == "@" else p


def _c(p):
    p = pos(p)
    return f"{p[0]} {p[1]} {p[2]}"


# -- scene templates: (template, *params) → console commands -----------------------------------------------------
def _tree(x, z, wood="oak", height=5):
    """One tree block by block (the old `_tree`): leaves persistent, the same crown every run."""
    return [f"fill {_c(('@', x - 2, height - 2, z - 2))} {_c(('@', x + 2, height - 1, z + 2))} "
            f"{wood}_leaves[persistent=true]",
            f"fill {_c(('@', x - 1, height, z - 1))} {_c(('@', x + 1, height, z + 1))} {wood}_leaves[persistent=true]",
            f"fill {_c(('@', x, 0, z))} {_c(('@', x, height - 1, z))} {wood}_log"]


SCENE = {
    "cmd": lambda text: [text],                                                   # a command with no position
    "fill": lambda lo, hi, block: [f"fill {_c(lo)} {_c(hi)} {block}"],
    "setblock": lambda p, block: [f"setblock {_c(p)} {block}"],
    "tp": lambda p: [f"tp @p {_c(p)}"],
    "give": lambda item, n=None: [f"give @p {item}" + ("" if n is None else f" {n}")],
    "time": lambda t: [f"time set {t}"],
    "summon": lambda mob, p, nbt=None: [f"summon {mob} {_c(p)}" + ("" if nbt is None else f" {nbt}")],
    "at": lambda template, *ps: [template.format(*[_c(p) for p in ps])],        # any other command with positions
    "tree": _tree,
}


def scene(items):
    """A row's scene → the setup command list, in order."""
    out = []
    for kind, *params in items:
        out += SCENE[kind](*params)
    return out


# -- predicates: (kind, *args) over (api, inv) ---------------------------------------------------------------------
OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt, "==": operator.eq,
       "!=": operator.ne, "is": operator.is_, "is not": operator.is_not,
       "in": lambda a, b: a in b, "not in": lambda a, b: a not in b}


def cmp(value, op=None, want=None):
    """Pure: `value op want`, or the value's truth when no op is given."""
    return bool(value) if op is None else OPS[op](value, want)


def count(api, inv, token, op, want):
    """The bag's count of `token` (a group name counts its members) compared: ("count", "log", ">=", 4)."""
    return cmp(inv.count(token), op, want)


def state(api, inv, key, op=None, want=None):
    """A /state field compared (or its truth): ("state", "dimension", "==", "minecraft:the_nether")."""
    return cmp(api.get("/state")[key], op, want)


def bag(api, inv, method, args=(), op=None, want=None):
    """An Inventory reading compared: ("bag", "used_slots", [], "<", 34)."""
    return cmp(getattr(inv, method)(*args), op, want)


def call(api, inv, name, args=(), op=None, want=None, resolve=None):
    """A named world helper's answer compared (or its truth); "$api"/"$inv" in `args` are the check's own."""
    fn = resolve(name)
    return cmp(fn(*[api if a == "$api" else inv if a == "$inv" else a for a in args]), op, want)


PREDICATES = {"count": count, "state": state, "bag": bag, "call": call}
LOGIC = ("all", "any", "not", "now")      # composition: all/any of predicates, not one, one read on the bag now

# -- interrupts: the progress kinds a row's interruption waits for (the old sheet's `_interrupt_when`) -------------
INTERRUPT_PROGRESS = ("gained_at_least", "spent_at_least", "placed_at_least", "walked_at_least")

__all__ = ["ORIGIN", "OPS", "PREDICATES", "LOGIC", "SCENE", "INTERRUPT_PROGRESS", "cmp", "pos", "scene"]

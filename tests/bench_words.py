"""Old sheet → table data: reads a row of the old SCENARIOS (callables and all) and writes it in `vocab`'s words.
Tooling for the move to tables, and the proof of it: the tests encode every old row, build it back with `table`
and encode the result again — the same data means the same factories called with the same arguments.

A callable is written as one of:
- a module-level function: ("!name",);
- a closure made by a factory (`_gain("log", 2)`): ("!gain", "log", 2) — its arguments read from the closure and
  checked by building it again (a factory that reworks its arguments before closing over them is refused);
- a lambda in one of the shapes `vocab` names (inv.count(...) >= n, a /state field, `_skill("x")(ctx, ...)`, ...).
Anything else raises NotExpressible, with the reason.
"""
import ast
import inspect
import os
import sys

from bonobo.bench import table, vocab
from bonobo.bench.core import ORIGIN

NEAR = 256           # a triple within this of ORIGIN on every axis is a bench position ("@", dx, dy, dz)
# Factory parameters the closure keeps under another name (the closure's value passes as the parameter as is).
ALIAS = {"_blocks": {"name": "names"}}
API_MOD = "bonobo.api"


class NotExpressible(Exception):
    pass


# -- values -------------------------------------------------------------------------------------------------------
def _num_rel(v, o):
    return v - o


def enc(v, depth=0):
    """A value of an old row → data."""
    if depth > 40:
        raise NotExpressible("nested too deep")
    if isinstance(v, str):
        from bonobo import paths
        root = paths.data_dir().rstrip(os.sep) + os.sep
        return ("$data", v[len(root):]) if v.startswith(root) else v   # a data file: where this player keeps them
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, tuple) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                     for x in v) \
            and all(abs(v[i] - ORIGIN[i]) <= NEAR for i in range(3)):
        return ("@",) + tuple(_num_rel(v[i], ORIGIN[i]) for i in range(3))
    if isinstance(v, (set, frozenset)):
        return ("$set", sorted(enc(x, depth + 1) for x in v))
    if isinstance(v, (list, tuple)):
        out = [enc(x, depth + 1) for x in v]
        return out if isinstance(v, list) else tuple(out)
    if isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise NotExpressible("a dict keyed by something other than text")
        return {k: enc(x, depth + 1) for k, x in v.items()}
    if callable(v):
        return fn(v, depth + 1)
    raise NotExpressible(f"a {type(v).__name__} value")


def _scen():
    return sys.modules.get("bonobo.bench.vocab") or __import__("bonobo.bench.vocab", fromlist=["x"])


def name_of(f):
    """The shortest name `table.resolve` finds `f` under."""
    q = f.__name__
    scen = _scen()
    short = q.lstrip("_")
    if short and short not in vocab.PREDICATES and short not in vocab.LOGIC and short not in table.RUNS \
            and short not in table.WORDS \
            and not hasattr(scen, short) and getattr(scen, q, None) is f:
        return short
    if getattr(scen, q, None) is f:
        return q
    return f"{f.__module__}:{q}"


def _closure(f):
    return dict(zip(f.__code__.co_freevars, [c.cell_contents for c in (f.__closure__ or ())]))


def fn(f, depth=0):
    """A callable → ("!kind", *args)."""
    if not hasattr(f, "__code__"):
        raise NotExpressible(f"{f!r} is not a Python function")
    made = getattr(f, "__table__", None)
    if made is not None:
        return ("!" + made[0],) + tuple(made[1:])
    q = f.__qualname__
    if "<locals>" not in q and "<lambda>" not in q and not f.__closure__:
        return ("&" + name_of(f),)
    special = _special(f, depth)
    if special is not None:
        return special
    why = "a lambda"
    if "<locals>" in q:
        try:
            return _factory(f, depth)
        except NotExpressible as e:
            why = str(e)
    if q.endswith("<lambda>"):
        try:
            return _lambda(f, depth)
        except NotExpressible as e:
            why = f"{why}; {e}"
    raise NotExpressible(f"{q}: {why}")


def _special(f, depth):
    """The shapes whose factory is not the maker of the closure: `_interrupt_when` (a `_when` hook) and the
    wrappers `_all`, `_named_all`, `_hooks` (lists)."""
    q = f.__qualname__
    if q.startswith("_when.<locals>."):
        cv = _closure(f)
        act = cv.get("act")
        if act is not None and act.__qualname__.startswith("_interrupt_when."):
            msg = _closure(act).get("message")
            data = ("!interrupt_when", enc(cv["progress"], depth + 1), None, msg)
            if cv.get("limit_s", 120) != 120:
                raise NotExpressible("an interrupt with its own time limit")
            return data
    if q.startswith("_all.<locals>."):
        return ("!all",) + tuple(enc(c, depth + 1) for c in _closure(f)["checks"])
    if q.startswith("_named_all.<locals>."):
        named = _closure(f)["named"]
        return ("!named_all",) + tuple((why, enc(c, depth + 1)) for c, why in named)
    if q.startswith("_hooks.<locals>."):
        return ("!hooks",) + tuple(enc(h, depth + 1) for h in _closure(f)["hooks"])
    return None


def _factory(f, depth):
    q = f.__qualname__
    fname = q.split(".<locals>")[0]
    if "." in fname:
        raise NotExpressible(f"made inside {fname}")
    mod = sys.modules[f.__module__]
    maker = getattr(mod, fname, None)
    if maker is None or not callable(maker):
        raise NotExpressible(f"no factory {fname} in {f.__module__}")
    cv = _closure(f)
    alias = ALIAS.get(fname, {})
    args = []
    sig = inspect.signature(maker)
    params = list(sig.parameters.values())
    for p in params:
        key = alias.get(p.name, p.name)
        if p.kind == p.VAR_POSITIONAL:
            if key not in cv:
                raise NotExpressible(f"{fname}: *{p.name} not in its closure")
            args += [enc(x, depth + 1) for x in cv[key]]
            continue
        if p.kind in (p.VAR_KEYWORD, p.KEYWORD_ONLY):
            raise NotExpressible(f"{fname}: keyword parameters")
        if key in cv:
            args.append(enc(cv[key], depth + 1))
        elif p.default is not p.empty:
            args.append(enc(p.default, depth + 1))
        else:
            raise NotExpressible(f"{fname}: {p.name} not kept in its closure")
    # trailing defaults dropped (no *args: every position is its parameter's)
    if not any(p.kind == p.VAR_POSITIONAL for p in params):
        while args and params[len(args) - 1].default is not inspect.Parameter.empty \
                and enc(params[len(args) - 1].default) == args[-1]:
            args.pop()
    data = ("!" + name_of(maker),) + tuple(args)
    # proof: built again from the data, the closure is the same
    try:
        again = table.dec(data)
    except Exception as e:           # noqa: BLE001  (any failure to rebuild is a refusal, with its reason)
        raise NotExpressible(f"{fname}: rebuilt with an error ({type(e).__name__}: {e})")
    if not callable(again) or getattr(again, "__qualname__", None) != q or _enc_closure(again, depth) != _enc_closure(f, depth):
        raise NotExpressible(f"{fname}: reworks its arguments (built again, the closure differs)")
    return data


def _enc_closure(f, depth):
    try:
        return {k: enc(v, depth + 1) for k, v in _closure(f).items()}
    except NotExpressible:
        return None


# -- lambdas ------------------------------------------------------------------------------------------------------
def _lambda_node(f):
    """The lambda's own AST node, cut from its file by the positions its code carries."""
    try:
        lines = inspect.getsourcelines(sys.modules[f.__module__])[0]
    except (OSError, TypeError):
        raise NotExpressible("no source")
    pos = [p for p in f.__code__.co_positions() if p[0] is not None and p[2] is not None
           and not (p[2] == 0 and p[3] == 0)]          # RESUME carries (line, 0, 0): not the body
    if not pos:
        raise NotExpressible("no positions")
    first = min(pos, key=lambda p: (p[0], p[2]))
    last = max(pos, key=lambda p: (p[1], p[3]))
    raw = [ln.encode() for ln in lines]
    # the nearest "lambda" before the body's first instruction
    li, col = first[0] - 1, first[2]
    head = raw[li][:col]
    while b"lambda" not in head:
        li -= 1
        if li < 0 or li < first[0] - 6:
            raise NotExpressible("lambda keyword not found")
        head = raw[li]
        col = len(head)
    start_line, start_col = li, head.rindex(b"lambda")
    end_line, end_col = last[1] - 1, last[3]
    if start_line == end_line:
        text = raw[start_line][start_col:end_col]
    else:
        text = raw[start_line][start_col:] + b"".join(raw[start_line + 1:end_line]) + raw[end_line][:end_col]
    src = text.decode()
    for cut in (src, src.rstrip(" ,)\n")):
        try:
            node = ast.parse("(" + cut + ")", mode="eval").body
            if isinstance(node, ast.Lambda):
                return node
        except SyntaxError:
            continue
    raise NotExpressible(f"lambda source not parsed: {src[:60]!r}")


class _L:
    """Translate one lambda's body; `env` evaluates the parts that do not touch the call's own arguments."""

    def __init__(self, f, node):
        self.f, self.node = f, node
        self.params = [a.arg for a in node.args.args]
        self.defaults = {}
        if node.args.defaults:
            names = self.params[-len(node.args.defaults):]
            for n_, d in zip(names, f.__defaults__ or ()):
                self.defaults[n_] = d
        self.env = dict(f.__globals__)
        self.env.update(_closure(f))
        self.env.update(self.defaults)

    def free(self, node):
        """Does `node` read none of the lambda's own (non-default) parameters?"""
        own = {p for p in self.params if p not in self.defaults}
        return not any(isinstance(n, ast.Name) and n.id in own for n in ast.walk(node))

    def sub(self, node):
        """A lambda written inside this one → its words (same globals and closure, its own parameters)."""
        t = _L.__new__(_L)
        t.f, t.node, t.params, t.defaults, t.env = self.f, node, [a.arg for a in node.args.args], {}, self.env
        return _lambda_of(t, node)

    def value(self, node):
        if isinstance(node, ast.Lambda):
            return self.sub(node)
        if not self.free(node):
            raise NotExpressible(f"reads the call's arguments: {ast.unparse(node)[:50]}")
        if any(isinstance(n, (ast.Lambda, ast.GeneratorExp, ast.ListComp)) for n in ast.walk(node)):
            raise NotExpressible(f"a function inside an argument: {ast.unparse(node)[:50]}")
        try:
            v = eval(compile(ast.Expression(node), "<row>", "eval"), self.env)     # noqa: S307  (the sheet's own code)
        except Exception as e:     # noqa: BLE001
            raise NotExpressible(f"not evaluable offline: {ast.unparse(node)[:50]} ({type(e).__name__})")
        return enc(v)

    # api / inventory recognisers
    def is_api(self, node):
        if isinstance(node, ast.Name) and node.id == "api" and "api" in self.params:
            return True
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__"
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == API_MOD)

    def is_inv(self, node):
        if isinstance(node, ast.Name) and node.id == "inv" and "inv" in self.params:
            return True
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_inv_now"
                and not node.args)

    def reading(self, node):
        """A reading of the world compared later: ("count", tok) | ("state", key) | ("bag", m, args) | ("call", n, args)."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and self.is_inv(node.func.value):
            args = [self.value(a) for a in node.args]
            if node.func.attr == "count" and len(args) == 1:
                return ("count", args[0])
            return ("bag", node.func.attr, args)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "_st" \
                and len(node.value.args) == 1 and self.is_api(node.value.args[0]):
            return ("state", self.value(node.slice))
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "get" \
                and self.is_api(node.value.func.value) and len(node.value.args) == 1 \
                and isinstance(node.value.args[0], ast.Constant) and node.value.args[0].value == "/state":
            key = self.value(node.slice)
            return ("state", key)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.env \
                and node.func.id not in self.params and not node.keywords:
            args = []
            for a in node.args:
                if self.is_api(a):
                    args.append("$api")
                elif self.is_inv(a):
                    args.append("$inv")
                else:
                    args.append(self.value(a))
            target = self.env[node.func.id]
            if not callable(target) or not hasattr(target, "__name__"):
                raise NotExpressible(f"{node.func.id} is not a function")
            return ("call", name_of(target), args)
        return None

    def pred(self, node):
        """A predicate over (api, inv) → data."""
        if isinstance(node, ast.BoolOp):
            kind = "!all" if isinstance(node.op, ast.And) else "!any"
            return (kind,) + tuple(self.pred(v) for v in node.values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return ("!not", self.pred(node.operand))
        if isinstance(node, ast.Compare):
            parts, left = [], node.left
            for op, right in zip(node.ops, node.comparators):
                parts.append(self.compare(left, op, right))
                left = right
            return parts[0] if len(parts) == 1 else ("!all",) + tuple(parts)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "bool" \
                and len(node.args) == 1 and self.env.get("bool", bool) is bool:
            return self.pred(node.args[0])
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return ("!constant", node.value)
        if isinstance(node, ast.Call) and not node.args and not node.keywords and self.free(node.func) \
                and not isinstance(node.func, ast.Name):         # _count("log", 2)() — a done read in place
            return ("!thunk", fn(eval(compile(ast.Expression(node.func), "<row>", "eval"), self.env)))  # noqa: S307
        if isinstance(node, ast.Call) and self.free(node.func) and not isinstance(node.func, ast.Name) \
                and len(node.args) == 2 and self.is_api(node.args[0]) and isinstance(node.args[1], ast.Constant) \
                and node.args[1].value is None:                     # a check asked with no bag: (api, None)
            return ("!api_only", fn(eval(compile(ast.Expression(node.func), "<row>", "eval"), self.env)))  # noqa: S307
        # a factory's check called in place: _alive(10)(api, inv)
        if isinstance(node, ast.Call) and self.free(node.func) and not isinstance(node.func, ast.Name) \
                and len(node.args) == 2 and self.is_api(node.args[0]) and self.is_inv(node.args[1]):
            inner = eval(compile(ast.Expression(node.func), "<row>", "eval"), self.env)     # noqa: S307
            return fn(inner)
        r = self.reading(node)
        if r is not None:
            return self._pred_of(r, None, None)
        raise NotExpressible(f"no predicate for {ast.unparse(node)[:60]}")

    OPNAMES = {ast.GtE: ">=", ast.Gt: ">", ast.LtE: "<=", ast.Lt: "<", ast.Eq: "==", ast.NotEq: "!=",
               ast.Is: "is", ast.IsNot: "is not", ast.In: "in", ast.NotIn: "not in"}
    FLIP = {">=": "<=", ">": "<", "<=": ">=", "<": ">", "==": "==", "!=": "!="}

    def compare(self, left, op, right):
        o = self.OPNAMES[type(op)]
        r = self.reading(left)
        if r is not None and self.free(right):
            return self._pred_of(r, o, self.value(right))
        r = self.reading(right)
        if r is not None and self.free(left) and o in self.FLIP:
            return self._pred_of(r, self.FLIP[o], self.value(left))
        raise NotExpressible(f"no comparison for {ast.unparse(left)[:40]} … {ast.unparse(right)[:40]}")

    @staticmethod
    def _pred_of(r, op, want):
        kind = r[0]
        if kind == "count":
            return ("!count", r[1], op, want)
        if kind == "state":
            return ("!state", r[1]) if op is None else ("!state", r[1], op, want)
        if kind == "bag":
            return ("!bag", r[1], r[2]) if op is None else ("!bag", r[1], r[2], op, want)
        return ("!call", r[1], r[2]) if op is None else ("!call", r[1], r[2], op, want)

    # runs and hooks (ctx) → ("!skill", name, *args) | ("!do", target, args, kwargs)
    def arg(self, node):
        if isinstance(node, ast.Name) and node.id == "ctx" and "ctx" in self.params:
            return "$ctx"
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ctx":
            return ("$ctx", node.attr)
        if isinstance(node, ast.Call) and not self.free(node) and isinstance(node.func, ast.Name) \
                and node.func.id in self.env and not node.keywords:
            return ("$call", name_of(self.env[node.func.id])) + tuple(self.arg(a) for a in node.args)
        return self.value(node)

    def target(self, func):
        """What a run calls: ("skill", name) | a dotted module function | a sheet function."""
        if isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and func.func.id == "_skill" \
                and len(func.args) == 1 and isinstance(func.args[0], ast.Constant):
            return ("skill", func.args[0].value)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call) \
                and isinstance(func.value.func, ast.Name) and func.value.func.id == "__import__" \
                and isinstance(func.value.args[0], ast.Constant):
            return ("do", f"{func.value.args[0].value}.{func.attr}")
        if isinstance(func, ast.Name) and func.id in self.env and func.id not in self.params:
            return ("do", name_of(self.env[func.id]))
        raise NotExpressible(f"no run target in {ast.unparse(func)[:50]}")

    def run(self, node):
        if isinstance(node, ast.Tuple):                         # steps in order, the tuple of what they gave
            return ("!seq", None) + tuple(self.run(e) for e in node.elts)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Tuple) \
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            return ("!seq", node.slice.value) + tuple(self.run(e) for e in node.value.elts)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "mem" \
                and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == "ctx":
            return ("!remember", node.func.attr, [self.arg(a) for a in node.args],
                    {k.arg: self.arg(k.value) for k in node.keywords})
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "sleep" \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "time" and len(node.args) == 1:
            return ("!pause", self.value(node.args[0]))
        if not isinstance(node, ast.Call):
            raise NotExpressible(f"a run that is not one call: {ast.unparse(node)[:60]}")
        kind, what = self.target(node.func)
        args = [self.arg(a) for a in node.args]
        kwargs = {k.arg: self.arg(k.value) for k in node.keywords}
        if kind == "skill":
            if kwargs:
                raise NotExpressible("a skill called with keywords")
            if args[:1] == ["$ctx"]:
                return ("!skill", what) + tuple(args[1:])
            return ("!skill_bare", what) + tuple(args)
        return ("!do", what, args, kwargs)


def _lambda(f, depth):
    node = _lambda_node(f)
    return _lambda_of(_L(f, node), node)


def _lambda_of(t, node):
    params = [p for p in t.params if p not in t.defaults]
    if params == ["api", "inv"]:
        return t.pred(node.body)
    body = node.body
    if isinstance(body, ast.Call) and isinstance(body.func, ast.Name) and body.func.id == "iter" \
            and len(body.args) == 1 and t.free(body):
        return ("!iter", t.value(body.args[0]))                 # a sweep's cells: the one list, iterated
    if params and t.free(body) and not any(isinstance(n, ast.Call) for n in ast.walk(body)):
        return ("!constant", t.value(body))                     # a build that builds nothing: lambda c: []
    if params == []:
        if isinstance(body, ast.Call) and t.free(body.func) and not isinstance(body.func, ast.Name):
            if not body.args:                                   # lambda: _count("log", 2)() — the same thunk
                return fn(eval(compile(ast.Expression(body.func), "<row>", "eval"), t.env))     # noqa: S307
            if len(body.args) == 2 and t.is_api(body.args[0]) and isinstance(body.args[1], ast.Constant) \
                    and body.args[1].value is None:             # a check asked with no bag: (api, None)
                return ("!now_api", fn(eval(compile(ast.Expression(body.func), "<row>", "eval"), t.env)))  # noqa: S307
        # a zero-argument progress or done: a predicate read on the bag and the /state now
        return ("!now", t.pred(body))
    if params == ["ctx"]:
        return t.run(node.body)
    raise NotExpressible(f"a lambda of ({', '.join(t.params)})")


# -- rows ---------------------------------------------------------------------------------------------------------
TOP_LIST = {"!all", "!hooks"}
CORE_KEYS = ("doc", "module", "setup", "run", "check", "budget", "tier", "before", "queue", "detail")


def _top(data):
    """A callable at the top of its slot: the bare kind."""
    return ((data[0][1:],) if data[0].startswith("!") else (data[0],)) + tuple(data[1:])


def _as_list(data):
    """`_all(...)` / `_hooks(...)` → the list of items (each at the top of the slot); one callable → [it]."""
    if data[0] in TOP_LIST:
        return [_top(d) for d in data[1:]]
    return [_top(data)]


def _one(cmd):
    """One setup command → a scene word (checked to render back)."""
    import re
    from bonobo.bench.core import _TRIPLE
    rel = []

    def sub(m):
        vals = [m.group(i + 1) for i in range(3)]
        nums = [float(v) if "." in v else int(v) for v in vals]
        if not all(abs(nums[i] - ORIGIN[i]) <= NEAR for i in range(3)):
            return m.group(0)
        rel.append(("@",) + tuple(nums[i] - ORIGIN[i] for i in range(3)))
        return "{%d}" % (len(rel) - 1)
    tmpl = _TRIPLE.sub(sub, cmd.replace("{", "{{").replace("}", "}}"))
    item = None
    if not rel:
        m = re.fullmatch(r"give @p (\S+)(?: (\d+))?", cmd)
        if m:
            item = ("give", m.group(1)) + ((int(m.group(2)),) if m.group(2) else ())
        m = re.fullmatch(r"time set (\d+)", cmd)
        if m:
            item = ("time", int(m.group(1)))
        item = item or ("cmd", cmd)
    else:
        m = re.fullmatch(r"fill \{0\} \{1\} (\S+)", tmpl)
        if m and len(rel) == 2:
            item = ("fill", rel[0], rel[1], m.group(1))
            lo, hi = rel
            if lo[1] == lo[3] == -hi[1] == -hi[3] and hi[2] == -1 and lo[2] < 0:
                item = _trim(("floor", m.group(1), hi[1], -lo[2]), ("stone", 8, 3))
        m = re.fullmatch(r"setblock \{0\} (\S+)", tmpl)
        if m and len(rel) == 1:
            item = ("setblock", rel[0], m.group(1))
        if tmpl == "tp @p {0}":
            p = rel[0]
            item = ("tp", p)
            if isinstance(p[1], float) and isinstance(p[3], float) and p[1] % 1 == 0.5 and p[3] % 1 == 0.5:
                item = _trim(("stand", p[1] - 0.5, p[2], p[3] - 0.5), (0, 0, 0))
                item = tuple(int(x) if isinstance(x, float) and x == int(x) and i != 2 else x
                             for i, x in enumerate(item))
        m = re.fullmatch(r"summon (\S+) \{0\}(?: (.+))?", tmpl)
        if m and len(rel) == 1:
            nbt = m.group(2).replace("{{", "{").replace("}}", "}") if m.group(2) else None
            item = ("summon", m.group(1), rel[0]) + ((nbt,) if nbt else ())
        item = item or ("at", tmpl) + tuple(rel)
    for cand in (item, ("cmd", cmd)):
        try:
            if vocab.scene([cand]) == [cmd]:
                return cand
        except Exception:     # noqa: BLE001
            pass
    return ("cmd", cmd)


def _trim(word, defaults):
    """Drop trailing parameters equal to their defaults (`defaults` align with the last parameters)."""
    head, params = word[:1], list(word[1:])
    k = len(params) - len(defaults)
    while params and len(params) > k and params[-1] == defaults[len(params) - 1 - k]:
        params.pop()
    return head + tuple(params)


def _sheet_lists():
    """The old sheet's shared command lists (NAME → commands), longest first."""
    import bonobo.bench.core as core_mod
    import bonobo.bench.fight as fight_mod
    scen = _scen()
    out = {}
    for mod, prefix in ((scen, ""), (fight_mod, "bonobo.bench.fight:"), (core_mod, "bonobo.bench.core:")):
        for k, v in vars(mod).items():
            if isinstance(v, list) and len(v) >= 2 and all(isinstance(c, str) for c in v) and k.isupper() or \
                    isinstance(v, list) and len(v) >= 2 and k.startswith("_") and k[1:].isupper() \
                    and all(isinstance(c, str) for c in v):
                if all(isinstance(c, str) for c in v) and any(c.split(" ")[0] in ("fill", "give", "setblock", "tp",
                                                                                "clear", "summon", "kill", "item")
                                                             for c in v):
                    name = k if prefix == "" else prefix + k
                    if getattr(scen, k, None) is v:
                        name = k
                    out.setdefault(name, v)
    return sorted(out.items(), key=lambda kv: -len(kv[1]))


def _groups(cmds, i):
    """A word covering several commands from `i`: a shared list, a grove, a pen, a chest, a tank."""
    for name, lst in _sheet_lists():
        if cmds[i:i + len(lst)] == lst:
            return ("sheet", name), len(lst)
    first = _one(cmds[i])
    if vocab.scene([first]) == vocab.scene([("grove",)]):
        spots, j, wood = [], i + 1, "oak"
        while j + 2 < len(cmds) + 0 and j + 2 <= len(cmds) - 1:
            log = _one(cmds[j + 2])
            if log[0] == "fill" and log[3].endswith("_log") and log[1][2] == 0:
                cand = ("tree", log[1][1], log[1][3], log[3][:-4])
                if vocab.scene([cand]) == cmds[j:j + 3]:
                    spots.append((log[1][1], log[1][3]))
                    wood = log[3][:-4]
                    j += 3
                    continue
            break
        word = ("grove",) + tuple(spots)
        if wood != "oak":
            raise NotExpressible("a grove of another wood")
        if vocab.scene([word]) == cmds[i:j]:
            return word, j - i
    if first[0] == "fill" and first[3] == "oak_fence":
        half = first[2][1]
        for n in range(6, -1, -1):
            for mob in ("cow", "sheep", "pig", "chicken"):
                cand = ("pen", mob, n, half)
                out = vocab.scene([cand])
                if cmds[i:i + len(out)] == out:
                    return _trim(cand, (7,)), len(out)
    if first[0] == "setblock" and first[2] == "chest":
        p, items, j = first[1], [], i + 1
        while j < len(cmds):
            w = _one(cmds[j])
            k = len(items)
            if w[0] == "at" and w[1].startswith(f"item replace block {{0}} container.{k} with ") and w[2] == p:
                items.append(w[1].split(" with ", 1)[1])
                j += 1
                continue
            break
        cand = ("chest", p) + tuple(items)
        if vocab.scene([cand]) == cmds[i:j]:
            return cand, j - i
    if first[0] in ("fill", "floor") and i + 4 < len(cmds):
        f = _one(cmds[i])
        if f[0] == "fill" and f[3] == "stone" and f[1][2] == f[2][2]:
            x0, z0, x1, z1, fy = f[1][1] + 1, f[1][3] + 1, f[2][1] - 1, f[2][3] - 1, f[1][2]
            for wall in ("glass", "stone", "obsidian"):
                for top in range(fy + 1, fy + 20):
                    for wt in [None] + list(range(fy + 1, top + 1)):
                        for side in (None, "north", "south", "west", "east"):
                            cand = ("tank", x0, x1, z0, z1, top, wt, fy, wall, side)
                            out = vocab.scene([cand])
                            if cmds[i:i + len(out)] == out:
                                return _trim(cand, (None, -4, "glass", None)), len(out)
    return None, 0


def scene_of(setup):
    """Setup commands → scene words (vocab.SCENE), the shared lists and groups first; renders back the same."""
    out, i = [], 0
    while i < len(setup):
        word, n = _groups(setup, i)
        if word is None:
            word, n = _one(setup[i]), 1
            if word[0] == "fill" and word[3].endswith("_leaves[persistent=true]") and i + 2 < len(setup):
                log = _one(setup[i + 2])
                if log[0] == "fill" and log[3].endswith("_log") and log[1][2] == 0:
                    cand = _trim(("tree", log[1][1], log[1][3], log[3][:-4], log[2][2] + 1), ("oak", 5))
                    if vocab.scene([cand]) == setup[i:i + 3]:
                        word, n = cand, 3
        out.append(word)
        i += n
    if vocab.scene(out) != list(setup):
        raise NotExpressible("the scene does not render back")
    return out


def row_of(name, old):
    """One old row → the table's data (dict). Raises NotExpressible."""
    data = {"name": name, "module": old["module"], "doc": old["doc"], "scene": scene_of(old["setup"])}
    if old.get("queue"):
        data["queue"] = enc(old["queue"])
    data["run"] = _top(enc(old["run"]))
    if old.get("before") is not None:
        data["before"] = _as_list(enc(old["before"]))
    chk = enc(old["check"])
    if chk[0] == "!named_all":
        data["check"] = [_top(c) for _why, c in chk[1:]]
        data["why"] = [why for why, _c in chk[1:]]
    else:
        data["check"] = _as_list(chk)
    if old.get("detail") is not None:
        try:
            data["detail"] = _top(enc(old["detail"]))
        except NotExpressible as e:
            data["no_detail"] = f"the report line is code: {e}"[:160]      # report text only, not the verdict
    data["budget"] = old["budget"]
    for k, v in old.items():
        if k not in CORE_KEYS and k not in data:
            data[k] = enc(v)
    return data

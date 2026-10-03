"""The branch-coverage gate: sys.monitoring BRANCH_LEFT/RIGHT over the decision code while the explorer runs.

The denominator is the brain's decision code, found by structure, not by a list of names: every function of the
decision modules the round's entry points (ROOTS: what check/round.py calls) reach through the static call graph,
never through execution. Execution is excluded, each with its reason (K8: checked live, E/P):
  - a skill's body (registered by @skill: skill.REGISTRY) and what is defined inside it: it runs the world;
  - a function that sends to the jar (calls an api function that POSTs): it acts, it does not decide;
  - a maintain row's act (reflexes.TABLE's third column) and the Maintain methods only acts name: the arbiter runs
    the act when the row wins; the round only proposes it (the row's trigger, its second column, is decision code);
  - a function no entry point reaches without passing through one of those: run-time code (runners, threads).
Every arm of what is left must be hit; the unhit arms are listed module:line with the line's text."""
import dis
import importlib
import inspect
import linecache
import sys
import types

MODULES = ("brain", "needs", "decompose", "cost", "estimate", "planner", "threat", "kernel", "fight_loop", "arbiter",
           "reflexes", "retry", "gather", "survive", "perception")
# the round's entry points: what check/round.py calls (the brain's round, the watcher's look and threat answer)
ROOTS = ("brain.Brain.decide", "brain.Brain.policy", "brain.Brain.context", "brain.Brain.failed", "brain.Brain.ready",
         "brain.fight_line_holds", "needs.Needs.overnight", "perception.Watcher._look",
         "perception.Watcher._answer_threats")
TOOL = sys.monitoring.COVERAGE_ID
# execution entries: a call into one runs the world (the body handed over, a rescue run), never the round's decision;
# what is reached only through them is execution (their own arms and the callbacks handed to them). check/round.py's
# STUBBED (the threat answer's handover) and the hazard rescues (hazard.recover: the shelter it runs, needs.cover)
EXECUTION = ("fight_loop.offer", "hazard.recover", "needs.cover")
VIA = {}        # a decision code → the code it was first reached from (why it is in the denominator)


def _codes(module):
    """{code: its top-level owner's qualname} for every code object defined in `module` (functions, methods,
    nested), and {class name: its methods' codes}."""
    out, classes, todo, seen = {}, {}, [module], set()
    here = inspect.getsourcefile(module)
    while todo:
        obj = todo.pop()
        for v in vars(obj).values() if isinstance(obj, (types.ModuleType, type)) else ():
            if isinstance(v, (staticmethod, classmethod)):
                v = v.__func__
            if isinstance(v, property):
                v = v.fget
            if isinstance(v, types.FunctionType):
                v = inspect.unwrap(v)           # a skill's runner: its body
            if isinstance(v, types.FunctionType) and v.__code__.co_filename == here and id(v) not in seen:
                seen.add(id(v))
                if isinstance(obj, type):
                    classes.setdefault(obj.__name__, []).append(v.__code__)
                stack = [v.__code__]
                while stack:
                    c = stack.pop()
                    out[c] = v.__code__
                    stack += [k for k in c.co_consts if isinstance(k, types.CodeType)]
            elif isinstance(v, type) and v.__module__ == module.__name__ and id(v) not in seen:
                seen.add(id(v))
                todo.append(v)
    return out, classes


def ident(code):
    return code.co_filename, code.co_firstlineno, code.co_qualname


def _senders():
    """api's functions that send to the jar: those calling its transport with "POST", and those calling one of them
    (closed)."""
    from bonobo import api
    fns = {n: f for n, f in vars(api).items() if isinstance(f, types.FunctionType) and f.__module__ == api.__name__}
    # api("POST", …)
    out = {n for n, f in fns.items() if "POST" in f.__code__.co_consts and "api" in f.__code__.co_names}
    while True:
        more = {n for n, f in fns.items() if n not in out and set(f.__code__.co_names) & out}
        if not more:
            return out
        out |= more


def _skill_bodies():
    """The codes registered as skills' bodies (@skill: skill.REGISTRY)."""
    from bonobo import skill
    out = set()
    for contract in skill.REGISTRY.values():
        fn = inspect.unwrap(contract.fn)
        if hasattr(fn, "__code__"):
            out.add(fn.__code__)
    return out


def _thread_bodies(module_of, owner):
    """Codes a thread runs: a threading.Thread subclass's run, and every function a code that makes a Thread loads
    (its target)."""
    import threading
    out = set()
    for m in module_of.values():
        for v in vars(m).values():
            if isinstance(v, type) and issubclass(v, threading.Thread) and "run" in vars(v):
                out.add(vars(v)["run"].__code__)
    for c in owner:
        ins = list(dis.get_instructions(c))
        if not any(i.opname in ("LOAD_ATTR", "LOAD_GLOBAL") and i.argval == "Thread" for i in ins):
            continue
        g = vars(module_of[c.co_filename])
        for i in ins:
            obj = g.get(i.argval) if i.opname == "LOAD_GLOBAL" else None
            if isinstance(obj, types.FunctionType) and obj.__code__ in owner:
                out.add(obj.__code__)
        out |= {k for k in c.co_consts if isinstance(k, types.CodeType)}
    return out


def _stubbed():
    """The codes check/round.py stands in for while the round runs (its STUBBED)."""
    from .round import STUBBED
    out = set()
    for name in STUBBED:
        mod, _, attr = name.partition(".")
        out.add(inspect.unwrap(getattr(importlib.import_module(f"bonobo.{mod}"), attr)).__code__)
    return out


def _row_acts(owner):
    """(the codes of reflexes.TABLE's acts, the Maintain methods named by acts and by no other code): what a maintain
    row runs once the arbiter hands it the body — never called by the round deciding."""
    from bonobo import reflexes
    acts = {act.__code__ for _name, _trigger, act in reflexes.TABLE}
    named = {n for c in acts for n in c.co_names if callable(getattr(reflexes.Maintain, n, None))}
    elsewhere = {n for c in owner if c not in acts for n in c.co_names}
    return acts, {f"Maintain.{n}" for n in named - elsewhere}


def decision_code():
    """(decision codes, {excluded code: why}) — the structure above."""
    mods = [importlib.import_module(f"bonobo.{m}") for m in MODULES]
    owner, classes = {}, {}
    for m in mods:
        codes, cls = _codes(m)
        owner.update(codes)
        for k, v in cls.items():
            classes.setdefault(k, []).extend(v)
    from bonobo import api
    senders, bodies = _senders(), _skill_bodies()
    module_of = {inspect.getsourcefile(m): m for m in mods}
    threads, stubbed = _thread_bodies(module_of, owner), _stubbed()
    acts, act_only = _row_acts(owner)
    why = {}
    for c, top in owner.items():
        mod = module_of[c.co_filename]
        # api.<sender>(…), or a sender imported by name
        sends = sorted(n for n in c.co_names if n in senders
                       and ("api" in c.co_names or getattr(mod, n, None) is getattr(api, n)))
        if top in bodies:
            why[c] = "a skill's body (@skill): it runs the world"
        elif top in threads:
            why[c] = "a thread's body (threading.Thread): it runs beside the round"
        elif top in acts or (mod.__name__ == "bonobo.reflexes" and top.co_qualname in act_only):
            why[c] = "a maintain row's act (reflexes.TABLE): the arbiter runs it, the round only proposes"
        elif top in stubbed:
            why[c] = "the round stands in for it (check/round.py STUBBED): the body's handover"
        elif f"{mod.__name__.rpartition('.')[2]}.{top.co_qualname}" in EXECUTION:
            why[c] = "an execution entry (coverage.EXECUTION): a rescue it runs"
        elif sends:
            why[c] = f"sends to the jar ({', '.join(sends)})"

    methods = {}
    for k, v in classes.items():
        for m in v:
            methods.setdefault(m.co_name, []).append(m)

    def resolve(obj):
        """The decision codes a module-level object stands for: a function, a class's constructor, a table's
        functions."""
        if isinstance(obj, types.FunctionType):
            obj = inspect.unwrap(obj)
            return [obj.__code__] if obj.__code__ in owner else []
        if isinstance(obj, type):
            return [m for m in classes.get(obj.__name__, ()) if m.co_name in ("__init__", "__post_init__", "__call__")]
        if isinstance(obj, (dict, list, tuple, set, frozenset)):
            vals = obj.values() if isinstance(obj, dict) else obj
            return [k for v in vals if isinstance(v, types.FunctionType) for k in resolve(v)]
        return []

    def wired(code, i):
        """Is the function loaded at `i` handed over rather than called: stored in an attribute (api.GUARD = …) or
        passed by keyword to a constructor (Policy(before_segment=…)) — a hook, run where it is called (the door, a
        runner), not by the round. A keyword `key` (sorted, min, max) is called right there."""
        if code[i].arg is not None and code[i].arg & 1:
            return False                                   # loaded to be called (the NULL/self slot)
        if i + 1 < len(code) and code[i + 1].opname == "PUSH_NULL":
            return False                                   # the callee itself (module.fn(...): its NULL follows)
        for ins in code[i + 1:]:
            if ins.opname in ("STORE_ATTR", "STORE_GLOBAL"):
                return True
            if ins.opname == "LOAD_CONST" and isinstance(ins.argval, tuple) and ins.argval \
                    and all(isinstance(k, str) for k in ins.argval):
                return "key" not in ins.argval
            if ins.opname.startswith(("CALL", "RETURN", "STORE_FAST", "POP_JUMP", "BUILD")):
                return False
        return False

    def handed_to_execution(code, g):
        """The functions defined inline (a lambda) and handed as an argument to an execution entry (EXECUTION): they
        run inside it, so only through it — the code between the entry's load and its call (a keyword call:
        `offer(..., release=lambda: ...)`)."""
        out = set()
        for i, ins in enumerate(code):
            entry = ins.opname == "LOAD_ATTR" and i and code[i - 1].opname == "LOAD_GLOBAL" \
                and isinstance(g.get(code[i - 1].argval), types.ModuleType) \
                and f"{g[code[i - 1].argval].__name__.rpartition('.')[2]}.{ins.argval}" in EXECUTION
            if not entry:
                continue
            for later in code[i + 1:]:
                if later.opname == "CALL_KW":
                    break
                if later.opname == "LOAD_CONST" and isinstance(later.argval, types.CodeType):
                    out.add(later.argval)
        return out

    def edges(c):
        """What `c` calls: a global resolved in its module (a module's attribute through it), a method by its name
        on an object, a name asked for by its text (getattr), and the functions defined inside it."""
        g = vars(module_of[c.co_filename])
        code = list(dis.get_instructions(c))
        handed = handed_to_execution(code, g)
        out = [k for k in c.co_consts if isinstance(k, types.CodeType) and k not in handed]
        out += [m for k in c.co_consts if isinstance(k, str) and k.isidentifier() and not k.startswith("__")
                for m in methods.get(k, ())]
        base = None
        for i, ins in enumerate(code):
            if ins.opname == "LOAD_GLOBAL":
                obj = g.get(ins.argval)
                base = obj if isinstance(obj, types.ModuleType) else None
                out += [] if wired(code, i) else resolve(obj)
            elif ins.opname in ("LOAD_ATTR", "LOAD_METHOD", "LOAD_SUPER_ATTR"):
                # a dunder by name is any class's (super().__init__): only a resolved class's constructor counts
                got = resolve(getattr(base, ins.argval, None)) if base is not None \
                    else [] if ins.argval.startswith("__") else methods.get(ins.argval, [])
                out += [] if wired(code, i) else got
                base = None
            else:
                base = None
        return out
    roots = []
    for r in ROOTS:
        mod, _, qual = r.partition(".")
        roots += [c for c in owner if c.co_filename == inspect.getsourcefile(importlib.import_module(f"bonobo.{mod}"))
                  and c.co_qualname == qual]
    seen, todo = set(), [c for c in roots if c not in why]
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        for k in edges(c):
            if k in owner and k not in why and k not in seen:
                VIA.setdefault(k, c)
                todo.append(k)
    for c in owner:
        if c not in seen and c not in why:
            why[c] = "no entry point reaches it but through execution (run-time code)"
    return seen, why


def inlined_guards(code):
    """Offsets of the compiler's builtin guards: 3.14 inlines any()/all()/tuple()… over a generator behind
    `LOAD_COMMON_CONSTANT <builtin>; IS_OP is; POP_JUMP_IF_FALSE`, whose jump runs only with the builtin rebound."""
    ins = [i for i in dis.get_instructions(code) if i.opname != "CACHE"]
    return {c.offset for a, b, c in zip(ins, ins[1:], ins[2:])
            if a.opname == "LOAD_COMMON_CONSTANT" and b.opname == "IS_OP" and c.opname == "POP_JUMP_IF_FALSE"}


class Gate:
    def __init__(self):
        decision, self.excluded = decision_code()
        self.arms = {}          # (code, offset, dest) → hit
        self.codes = {}         # a code's process-independent id → the code
        self.inlined = 0        # the builtin guards' rebound arms, out of the denominator
        for c in decision:
            self.codes[ident(c)] = c
            guards = inlined_guards(c)
            for src, left, right in c.co_branches():
                self.arms[(c, src, left)] = False
                if src in guards:
                    self.inlined += 1
                else:
                    self.arms[(c, src, right)] = False

    def __enter__(self):
        sys.monitoring.use_tool_id(TOOL, "check")
        sys.monitoring.restart_events()
        hit = self.arms

        def on(code, src, dst):
            k = (code, src, dst)
            if k in hit:
                hit[k] = True
            return sys.monitoring.DISABLE if k in hit else None
        sys.monitoring.register_callback(TOOL, sys.monitoring.events.BRANCH_LEFT, on)
        sys.monitoring.register_callback(TOOL, sys.monitoring.events.BRANCH_RIGHT, on)
        for c in {k[0] for k in self.arms}:
            sys.monitoring.set_local_events(TOOL, c, sys.monitoring.events.BRANCH_LEFT
                                            | sys.monitoring.events.BRANCH_RIGHT)
        return self

    def __exit__(self, *exc):
        sys.monitoring.free_tool_id(TOOL)

    def hits(self):
        """The arms hit, as process-independent keys (to merge the shards' runs)."""
        return {(ident(c), src, dst) for (c, src, dst), ok in self.arms.items() if ok}

    def merge(self, keys):
        for cid, src, dst in keys:
            c = self.codes.get(cid)
            if c is not None and (c, src, dst) in self.arms:
                self.arms[(c, src, dst)] = True

    def exclusions(self):
        """{reason: [module qualname]} of the excluded functions (top-level codes only)."""
        out = {}
        for c, why in self.excluded.items():
            out.setdefault(why.split(" (")[0], []).append(f"{c.co_filename.rsplit('/', 1)[-1][:-3]}.{c.co_qualname}")
        return {k: sorted(set(v)) for k, v in out.items()}

    def unhit(self):
        """[(module, line, qualname)] of the unhit arms."""
        out = set()
        for (c, src, dst), ok in self.arms.items():
            if not ok:
                line = next((ln for st, end, ln in c.co_lines() if st <= src < end and ln), c.co_firstlineno)
                out.add((c.co_filename.rsplit("/", 1)[-1], line, c.co_qualname))
        return sorted(out)

    def clusters(self):
        """{module: (functions entered, of all; arms hit, of the entered functions' arms; unhit arms of entered
        functions as 'line qualname')}."""
        out = {}
        by = {}
        for (c, src, dst), ok in self.arms.items():
            by.setdefault(c, []).append((src, ok))
        for c, arms in by.items():
            mod = c.co_filename.rsplit("/", 1)[-1]
            entered = any(ok for _s, ok in arms)
            row = out.setdefault(mod, [0, 0, 0, 0, []])
            row[1] += 1
            if entered:
                row[0] += 1
                row[3] += len(arms)
                row[2] += sum(ok for _s, ok in arms)
                for src, ok in arms:
                    if not ok:
                        line = next((ln for st, end, ln in c.co_lines() if st <= src < end and ln), c.co_firstlineno)
                        row[4].append(f"{line} {c.co_qualname}")
        return {m: (f, n, h, t, sorted(set(u), key=lambda x: int(x.split()[0]))) for m, (f, n, h, t, u) in out.items()}

    def report(self):
        """(hit, total, [unhit 'module:line  text'])."""
        unhit = [f"{m}:{ln}  {linecache.getline(c, ln).strip()}" for m, ln, _q in self.unhit()
                 for c in [next(k.co_filename for k in self.codes.values() if k.co_filename.endswith('/' + m))]]
        hit = sum(self.arms.values())
        return hit, len(self.arms), sorted(set(unhit))

"""The branch-coverage gate: sys.monitoring BRANCH_LEFT/RIGHT over the decision modules while the explorer runs.
Every branch arm (code.co_branches) of every function in those modules must be hit; the unhit arms are listed
module:line with the line's text."""
import inspect
import linecache
import sys
import types

MODULES = ("needs", "decompose", "cost", "planner", "threat", "kernel", "fight_loop", "arbiter", "reflexes", "retry",
           "actions", "solve")
TOOL = sys.monitoring.COVERAGE_ID


def _codes(module):
    """Every code object defined in `module` (functions, methods, nested)."""
    out, todo = [], [module]
    seen = set()
    while todo:
        obj = todo.pop()
        for v in vars(obj).values() if isinstance(obj, (types.ModuleType, type)) else ():
            if isinstance(v, types.FunctionType) and v.__module__ == module.__name__ and id(v) not in seen:
                seen.add(id(v))
                todo_code = [v.__code__]
                while todo_code:
                    c = todo_code.pop()
                    out.append(c)
                    todo_code += [k for k in c.co_consts if isinstance(k, types.CodeType)]
            elif isinstance(v, type) and v.__module__ == module.__name__ and id(v) not in seen:
                seen.add(id(v))
                todo.append(v)
    return out


def ident(code):
    return code.co_filename, code.co_firstlineno, code.co_qualname


class Gate:
    def __init__(self):
        import importlib
        self.mods = [importlib.import_module(f"bonobo.{m}") for m in MODULES]
        self.arms = {}          # (code, offset, dest) → hit
        self.codes = {}         # a code's process-independent id → the code
        for m in self.mods:
            for c in _codes(m):
                self.codes[ident(c)] = c
                for src, left, right in c.co_branches():
                    self.arms[(c, src, left)] = False
                    self.arms[(c, src, right)] = False
        self.files = {inspect.getsourcefile(m) for m in self.mods}

    def __enter__(self):
        sys.monitoring.use_tool_id(TOOL, "check")
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

    def clusters(self):
        """{module: (functions entered, of all; arms hit, of the entered functions' arms; unhit arms of entered
        functions as 'line qualname')}. An unentered function is code the decision never calls (execution, a skill);
        an unhit arm of an entered one is a value the facts never take."""
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
        unhit = []
        for (c, src, dst), ok in self.arms.items():
            if not ok:
                line = next((ln for start, _end, ln in c.co_lines() if start <= src < _end and ln), c.co_firstlineno)
                unhit.append(f"{c.co_filename.rsplit('/', 1)[-1]}:{line}  {linecache.getline(c.co_filename, line).strip()}")
        hit = sum(self.arms.values())
        return hit, len(self.arms), sorted(set(unhit))

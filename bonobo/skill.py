"""Skill contracts: every skill declares what it is for and how to judge it, and one runner enforces that.

A skill is a function (usually a generator) plus a contract:
  doc       what it does (first docstring line)
  pre       preconditions, each raises ToolMissing / NotAvailable before anything is touched
  start     baseline measured before running (e.g. how many logs we hold)
  done      goal reached — checked before starting (skip) and after every step (stop early)
  verify    postcondition after the body returns (defaults to `done`); failing it raises McError
  budget    max seconds for the whole skill
  stall     max seconds without progress; progress = the value the body yields changed, or, when it yields
            None, the inventory/position signature changed

Two levels of stuck detection: api.await_task watches one mod task (10 s without visible movement), the runner
watches the skill's own goal metric across tasks (a hunt that walks around forever without closing in)."""
import functools
import inspect
import os
import time

from . import api, paths
from .api import McError, TaskStuck

REGISTRY = {}


class Call:
    """What contract callables see: the call's args, the baseline and (for verify) the result."""

    def __init__(self, args, kwargs):
        self.args, self.kwargs, self.base, self.result = args, kwargs, None, None


def world_signature():
    from .world import Inventory
    s = api.get("/state")
    inv = Inventory()
    return ((s["blockX"], s["blockY"], s["blockZ"]),
            tuple(sorted((x["id"], x.get("count", 1), x.get("damage", 0)) for x in inv.slots)))


class Contract:
    def __init__(self, name, fn, pre, start, done, verify, budget, stall, per_unit, units, key, soft=False):
        # soft: perception's interrupt is left standing for the body to read (`api.INTERRUPT`) instead of ending the
        # skill. A fight answers danger by taking cover and trying again — four "dragon breath close" interrupts in a
        # row otherwise killed whole bench runs before anything was built.
        self.soft = soft
        self.name, self.fn, self.pre, self.start, self.done = name, fn, pre, start, done
        self.verify = verify if verify is not None else done
        self.budget, self.stall = budget, stall
        # Time estimation: `per_unit` prior seconds, `units(c)` how many units a call does (blocks, logs, kills,
        # items), `key(c)` the statistics key (e.g. "mine:minecraft:raw_iron"). Measured durations replace the prior.
        self.per_unit = per_unit if per_unit is not None else budget / 3
        self.units = units or (lambda c: 1)
        self.key = key or (lambda c: name)
        self.doc = (inspect.getdoc(fn) or "").split("\n")[0]

    def describe(self, stats=None):
        learned = stats.duration(self.name) if stats is not None else None
        est = f"~{self.per_unit:g}s/unit" + (f" (measured {learned:.1f}s)" if learned is not None else "")
        return f"{self.name:<18} {est:<30} budget {self.budget:>4}s  stall {self.stall:>3}s  {self.doc}"


# Set by the brain to the memory object: record_duration(key, seconds, units) / duration(key).
STATS = None
MIN_SAMPLES = 3


def expected(fn, *args, **kwargs):
    """Expected seconds for a skill call: measured seconds per unit once there are MIN_SAMPLES runs of that key,
    otherwise the contract's prior. Used to decide when a skill is worth doing."""
    contract = fn.contract
    c = Call(args, kwargs)
    try:
        units = max(1, contract.units(c))
        key = contract.key(c)
    except (IndexError, KeyError, TypeError):
        units, key = 1, contract.name
    per = STATS.duration(key) if STATS is not None else None
    return (per if per is not None else contract.per_unit) * units


def needs_of(fn):
    """{dimension: minimum} this skill requires, for the planner to price. Empty when it declares none."""
    contract = getattr(fn, "contract", None)
    return dict(getattr(contract, "needs", {}) or {})


def can_run(fn, *args, **kwargs):
    """Would this skill's preconditions pass right now? (ok, why not).

    The same `pre` the runner checks, asked BEFORE the planner offers the work rather than after it fails. A skill
    that says "no torches to spare" already knew it could not run; nobody asked, so the pool priced it, chose it,
    and learned by failing — ninety times in four minutes, because the idle rule kept thawing it.
    """
    contract = getattr(fn, "contract", None)
    if contract is None or not contract.pre:
        return True, None
    c = Call(args, kwargs)
    for check in contract.pre:
        try:
            check(c)
        except Exception as e:
            return False, str(e) or type(e).__name__
    return True, None


def skill(name=None, *, pre=(), needs=None, start=None, done=None, verify=None, budget=300, stall=45,
          per_unit=None, units=None, key=None, soft=False):
    """`needs` is the same preconditions stated as STATE — {dimension: minimum} — instead of as a check.

    A check can only answer "no". A dimension can be priced: `solve.reach_cost` walks the requirement graph and
    says what it costs to get there, so "no torches" stops being a refusal and becomes "three torches first, about
    forty seconds". The checks in `pre` stay as the runtime guard; `needs` is what the planner reads.
    """
    def wrap(fn):
        contract = Contract(name or fn.__name__, fn, tuple(pre), start, done, verify, budget, stall, per_unit, units,
                            key, soft)
        contract.needs = dict(needs or {})
        REGISTRY[contract.name] = contract

        @functools.wraps(fn)
        def runner(*args, **kwargs):
            c = Call(args, kwargs)
            for check in contract.pre:
                check(c)
            if contract.start:
                c.base = contract.start(c)
            if contract.done and contract.done(c):
                return None
            t0 = time.time()
            prev_soft = api.SOFT
            api.SOFT = contract.soft or prev_soft     # nested skills (eat inside a fight) inherit the protection
            try:
                out = fn(*args, **kwargs)
                if inspect.isgenerator(out):
                    out = _drive(contract, c, out)
            finally:
                api.SOFT = prev_soft
            c.result = out
            if contract.verify and not contract.verify(c):
                raise McError(f"{contract.name}: finished without reaching its goal")
            if STATS is not None:
                try:
                    STATS.record_duration(contract.key(c), time.time() - t0, max(1, contract.units(c)))
                except (IndexError, KeyError, TypeError):
                    pass
            return out

        runner.contract = contract
        contract.runner = runner     # directives call any registered skill by name, whatever module it lives in
        runner.contract = contract      # so the planner can ask `can_run` before it offers the work
        return runner

    return wrap


HEARTBEAT = paths.data("skill-heartbeat")


def _heartbeat(name):
    """Skills that work on the Python side (watching a furnace) run no mod task; the supervisor reads this file so it
    doesn't mistake them for an idle agent."""
    try:
        with open(HEARTBEAT, "w") as f:
            f.write(f"{time.time():.0f} {name}\n")
    except OSError:
        pass


def _drive(contract, c, gen):
    t0 = time.time()
    last, since = world_signature(), t0
    try:
        while True:
            try:
                marker = next(gen)
            except StopIteration as stop:
                return stop.value
            now = time.time()
            _heartbeat(contract.name)
            if api.INTERRUPT and api.MODE != "survival" and not contract.soft:
                api.take_interrupt()   # Python-side loops stop too, not only mod tasks
            if api.get("/state").get("dead"):
                # Dead ends every skill now: a dragon fight kept issuing 20+ "travel: no route" after dying.
                raise McError(f"{contract.name}: died")
            if contract.done and contract.done(c):
                return None
            metric = marker if marker is not None else world_signature()
            if metric != last:
                last, since = metric, now
            elif now - since >= contract.stall:
                raise TaskStuck(f"{contract.name}: no progress toward its goal for {int(now - since)}s")
            if now - t0 > contract.budget:
                raise TaskStuck(f"{contract.name}: over its {contract.budget}s budget")
    finally:
        gen.close()

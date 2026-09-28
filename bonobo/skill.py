"""Skill contracts: every skill declares what it is for and how it is judged, and one runner enforces that.

Contract fields: needs/speed/gives (what the planner prices), remaining (what is left after an interruption), pre,
start, done, verify, budget, stall, provides (effect → the call a plan step makes), prefer, and commands — the pure
batch an open-loop skill sends, so a fight can post the same batch itself. The runner watches the goal metric across
tasks; api.await_task watches one mod task."""
import dataclasses
import functools
import inspect
import time
from typing import Any, Callable, cast

from . import api, lifecycle, paths, skillcore, tape, knowledge
from .api import McError, TaskStuck
from .knowledge import have_remainder, needs_rows
from .bag import has_room

REGISTRY: "dict[str, Contract]" = {}
Needs = dict[str, int]         # {dimension: minimum}: "tool:pickaxe:2", "item:minecraft:bucket", ... (knowledge.needs_rows)
Speed = dict[str, float]       # {tool kind: how much faster}: what the planner prices
Bag = dict[str, int]           # {item or group token: count}: a wanted bag, what is left of it
VERIFY_SETTLE_S = 3.0      # how long a finished skill's effect may take to show up in the world

class Call:
    """What contract callables see: the call's args, the baseline and (for verify) the result."""

    base: Any
    result: Any
    times: dict[str, float]         # the runner's ms per phase (pre, body, checks, verify) and body steps (n)

    def __init__(self, args, kwargs):
        self.args, self.kwargs, self.base, self.result = args, kwargs, None, None
        self.want: Bag | None = None            # an item skill's desired bag, fixed at its start (skill.wanted)
        self.keep: dict[str, Any] = {}          # what the skill fixed at its first start (an anchor: a column, a direction)
        self.contract: "Contract | None" = None   # the skill this call runs (set by its runner)

# an interrupted call's base, wanted bag and anchors, for its resume (read off the world, never a step index); stale after RESUME_TTL_S
RESUME = {}
lifecycle.on_reset(lambda: RESUME.clear(), covers=("RESUME",))     # an anchor from the last life (another site) resumes nothing
RESUME_TTL_S = 300
CALLS = []
# skill → fn (state, args) → the keys a call fixes at its first start, kept so its resume rebuilds against the same anchor
ANCHORS = {}

def _night_way_running():
    """Is the work running now the night's way itself (a shelter, sleep, waiting for day)? Then nightfall's
    boundary request waits — it must never cut the shelter it asks for."""
    return any(c.contract is not None and (c.contract.name == "sleep"
                                           or {"state:sheltered", "state:day"} & set(c.contract.gives))
               for c in CALLS)


api.BOUNDARY_EXEMPT = lambda: not CALLS or _night_way_running()     # only a skill's work stops; never the night's way


def current():
    """The call running now (innermost); its `keep` survives an interruption for its resume."""
    return CALLS[-1] if CALLS else None

def _resume_key(contract, args):
    return contract.name, repr(args[1:])

def _resumed(contract, args):
    """(base, want, keep) the interrupted call left, if fresh; taken once."""
    kept = RESUME.pop(_resume_key(contract, args), None)
    if kept is None or time.time() - kept[0] > RESUME_TTL_S:
        return None
    return kept[1:]

def body_now():
    """One /state: the runner's seam for death and a dimension change."""
    return api.get("/state")

def world_signature():
    from .world import Inventory
    s = api.get("/state")
    inv = Inventory()
    return ((s["blockX"], s["blockY"], s["blockZ"]),
            tuple(sorted((x["id"], x.get("count", 1), x.get("damage", 0)) for x in inv.slots)))

@dataclasses.dataclass(frozen=True)
class Spec:
    """What the `skill` decorator declares: the planner's prices and the runner's checks."""
    pre: tuple = ()
    needs: Needs | Callable[[tuple], Needs] | None = None    # a fn of the call's args when the need depends on them
    speed: Speed | None = None
    gives: Any = None               # producing tables (knowledge.Produces) and states it leaves ("state:sheltered")
    start: Callable[[Call], Any] | None = None
    done: Callable[[Call], bool] | None = None
    verify: Callable[[Call], bool] | None = None
    budget: float = 300
    stall: float = 45
    units: Callable[[Call], int] | None = None
    key: Callable[[Call], str] | None = None
    soft: bool = False
    commands: Callable[..., list] | None = None
    provides: dict[str, Callable[[Any, Any], Any]] | None = None     # effect → (ctx, step) → the call's args, None
    prefer: float = 0
    fills_bag: bool | Callable[[Call], list[str]] = False
    remaining: Callable[[Any, Call], "Bag | None"] | None = None      # (state, call) → what is left, {} when met

class Contract:
    # set by the `skill` decorator once built
    needs_fn: Callable[[tuple], Needs] | None
    needs: Needs
    speed: Speed
    gives: list
    remaining: Callable[[Any, Call], "Bag | None"] | None
    fills_bag: bool | Callable[[Call], list[str]]
    runner: Callable[..., Any]

    def __init__(self, name: str, fn: Callable[..., Any], spec: Spec):
        self.commands = spec.commands
        self.provides = dict(spec.provides or {})
        self.prefer = spec.prefer
        # soft: perception's interrupt is left for the body to read instead of ending the skill (a fight takes cover and retries)
        self.soft = spec.soft
        self.name, self.fn, self.pre, self.start, self.done = name, fn, tuple(spec.pre), spec.start, spec.done
        self.verify = spec.verify if spec.verify is not None else spec.done
        self.budget, self.stall = spec.budget, spec.stall
        # units(c): how many units a call does; key(c): the statistics key
        self.units = spec.units or (lambda c: 1)
        self.key = spec.key or (lambda c: name)
        self.doc = (inspect.getdoc(fn) or "").split("\n")[0]

    def describe(self):
        return f"{self.name:<18} budget {self.budget:>4}s  stall {self.stall:>3}s  {self.doc}"

# the memory object, set by the brain
STATS = None
LAST_S = {}     # skill name → seconds its last verified run took, preconditions and planning excluded
MIN_SAMPLES = 3

def can_run(fn, *args, **kwargs):
    """(ok, why not) of the runner's own checks, asked before the work is offered, not learned by failing."""
    contract = getattr(fn, "contract", None)
    if contract is None:
        return True, None
    c = Call(args, kwargs)
    for check in contract.pre:
        try:
            check(c)
        except McError as e:
            return False, str(e) or type(e).__name__
    missing = unmet(contract, args, skillcore.Inventory)
    if missing:
        return False, f"{contract.name}: {skillcore.NeedMissing(missing)}"
    return True, None

def unmet(contract, args, bag):
    """Pure given the bag: this call's needs not held ({} = can start); `bag()` is read only when there are needs."""
    needs = needs_of(contract, args)
    return have_remainder(bag(), needs_rows(needs)) if needs else {}

def step_keys(step):
    """The effects a plan step asks for, most specific first."""
    return [f"{step.kind}:{step.token}", f"item:{step.token}", step.kind]

def providers(effect):
    """Contracts that provide `effect`, preferred first."""
    return sorted((c for c in REGISTRY.values() if effect in c.provides), key=lambda c: -c.prefer)

def provider(ctx, step):
    """(runner, args) of the skill that carries out `step` here, or None when no registered skill can."""
    for effect in step_keys(step):
        for contract in providers(effect):
            args = contract.provides[effect](ctx, step)
            if args is not None:
                return contract.runner, tuple(args)
    return None

def handles(step):
    """Does any registered skill provide what this step asks for? (No world read: decompose asks it.)"""
    return step.kind == "skill" and step.token in REGISTRY or any(providers(e) for e in step_keys(step))

def needs_of(contract: Contract, args: tuple) -> Needs:
    """The hard prerequisites of this call: the static `needs`, or the function of the call's args."""
    fn = getattr(contract, "needs_fn", None)
    return dict(fn(args)) if fn else dict(contract.needs)

def step_call(step) -> tuple[Needs, Speed]:
    """Pure: (needs, speed) of what carries out `step`, merged over its providers; a call-dependent need reads the args `provides` builds, without ctx."""
    if step.kind == "skill" and step.token in REGISTRY:
        # a skill asked for by name: that skill with the goal's args
        c = REGISTRY[step.token]
        try:
            needs = needs_of(c, (None,) + tuple(step.detail.get("args") or ()))
        except (IndexError, KeyError, TypeError):
            needs = dict(c.needs)            # args it cannot read: its no-call default (the runner refuses them)
        return needs, dict(c.speed)
    for effect in step_keys(step):
        found = providers(effect)
        if not found:
            continue
        needs: Needs = {}
        speed: Speed = {}
        for c in found:
            args = ()
            if getattr(c, "needs_fn", None):
                got = c.provides[effect](None, _lenient(step))
                if got is None:
                    continue
                args = (None,) + tuple(got)
            call_needs = needs_of(c, args)
            for k, v in call_needs.items():
                needs[k] = max(needs.get(k, 0), v)
            # a shovel helps soft ground only, not a call that needs a pickaxe
            hard = any(k.startswith("tool:pickaxe:") for k in call_needs)
            for k, v in c.speed.items():
                if not (k == "shovel" and hard):
                    speed[k] = max(speed.get(k, 0), v)
        return needs, speed
    return {}, {}

class _Blank(dict):
    def __missing__(self, key):
        return None

def _lenient(step):
    """The step with any missing detail read as None: a missing argument is decompose.missing_detail's refusal, not this one's."""
    import types
    return types.SimpleNamespace(kind=step.kind, token=step.token, count=step.count, detail=_Blank(step.detail))

def _wire_planner():
    knowledge.STEP_CALL = step_call

def declared(name, needs, speed, gives=(), remaining=None):
    """Refuse at import a skill that does not state `needs`, `speed` and `gives` ({} when none), or leaves a world state without `remaining`."""
    missing = [k for k, v in (("needs", needs), ("speed", speed), ("gives", gives)) if v is None]
    if missing:
        raise TypeError(f"skill {name!r} declares no {' and no '.join(missing)} (write {{}} when there are none)")
    if world_effect(gives) and not callable(remaining):
        raise TypeError(f"skill {name!r} leaves {', '.join(g for g in gives_of(gives) if isinstance(g, str))} in the "
                        f"world and declares no remaining= (a pure fn (state, call) → what is still missing, {{}} "
                        f"when met)")
    # nothing given is an item and no remaining=: nothing could say what is left after an interruption
    if not any(not isinstance(g, str) for g in gives_of(gives)) and not callable(remaining):
        raise TypeError(f"skill {name!r} gives no item and declares no remaining= (a state it leaves in gives, and a "
                        f"pure fn (state, call) → what is still missing, {{}} when met)")

def world_effect(gives):
    """Does this skill's product land in the world (a state it leaves: "state:sheltered"), not in the bag?"""
    return any(isinstance(g, str) for g in gives_of(gives))

def wanted(contract, state, args):
    """The bag an item call wants, fixed at its start: {token: held then + count asked}; None when it produces no item."""
    from .knowledge import held
    asked = _asked(contract, args)
    if asked is None:
        return None
    token, _at, n = asked
    return {token: held(state["inv"], token) + n}

def _asked(contract, args):
    """(token, index of the count in args or None, count) an item skill's call asks for; None for no item."""
    tables = [g for g in contract.gives if not isinstance(g, str)]
    if not tables:
        return None
    rest = list(args[1:])
    at = next((i for i, a in enumerate(rest) if isinstance(a, str) and any(t.get(a) is not None for t in tables)),
              None)
    if at is None:
        keys = {k for t in tables for k in t.keys()}
        if len(keys) != 1:
            return None
        token, start = keys.pop(), 0
    else:
        token, start = rest[at], at + 1
    i = next((j for j in range(start, len(rest)) if isinstance(rest[j], int) and not isinstance(rest[j], bool)), None)
    return token, (None if i is None else i + 1), (1 if i is None else rest[i])

def with_rest(contract, args, rest):
    """Pure: the args with the count set to what is left of the item; unchanged when the count is not an argument."""
    asked = _asked(contract, args)
    if asked is None or asked[1] is None or asked[0] not in rest:
        return tuple(args)
    out = list(args)
    out[asked[1]] = rest[asked[0]]
    return tuple(out)

def remaining_of(contract: Contract, state, call: Call) -> Bag | None:
    """What is left of this call read off `state` ({} done, None when only the running skill can say): its `remaining`, else the wanted bag less the held."""
    if contract.remaining is not None:
        return contract.remaining(state, call)
    want = getattr(call, "want", None)
    if want is None:
        return None
    return have_remainder(state["inv"], [[t, n] for t, n in want.items()])

def gives_of(gives):
    """A skill's `gives` as a list: producing tables (knowledge.Produces) and states it leaves ("state:sheltered")."""
    if isinstance(gives, dict):
        return [f"state:{k}" if not str(k).startswith("state:") else k for k in gives] if gives else []
    return list(gives) if isinstance(gives, (list, tuple)) else [gives]

def skill(name=None, **options):
    """`needs` states preconditions as {dimension: minimum} so the planner can price them; `pre` stays the runtime guard."""
    spec = Spec(**options)

    def wrap(fn) -> Callable[..., Any]:
        declared(name or fn.__name__, spec.needs, spec.speed, spec.gives, spec.remaining)
        contract = Contract(name or fn.__name__, fn, spec)
        # a need that depends on the call is a fn of its args; `needs` is then the no-tier default
        contract.needs_fn = spec.needs if callable(spec.needs) else None
        contract.needs = {} if callable(spec.needs) else dict(spec.needs or {})
        contract.speed = dict(spec.speed or {})
        contract.gives = gives_of(spec.gives)
        contract.remaining = spec.remaining
        from .knowledge import PRODUCERS
        for g in contract.gives:
            if not isinstance(g, str) and g not in PRODUCERS:
                PRODUCERS.append(g)
        # a gatherer: True, or c → the item ids it gathers
        contract.fills_bag = spec.fills_bag
        REGISTRY[contract.name] = contract

        @functools.wraps(fn)
        def runner(*args, **kwargs):
            p0 = time.perf_counter()
            key = _resume_key(contract, args)
            c = Call(args, kwargs)
            c.contract = contract
            missing = unmet(contract, args, skillcore.Inventory)     # every caller: a plan step, a reflex, a direct call
            if missing:
                raise skillcore.NeedMissing(missing)
            for check in contract.pre:
                check(c)
            kept = _resumed(contract, args)
            if kept is not None:
                c.base, c.want, c.keep = kept
            elif contract.start:
                c.base = contract.start(c)
            if c.want is None and any(not isinstance(g, str) for g in contract.gives):
                c.want = wanted(contract, {"inv": skillcore.Inventory()}, args)
            elif kept is not None:
                # resumed: the rest read off the world — done while away, or asked again for only what is missing
                rest = remaining_of(contract, skillcore.body_state(args[0] if args else None), c)
                if rest == {}:
                    return None
                args = with_rest(contract, args, rest or {})
                c.args = args
            if contract.done and contract.done(c):
                return None
            bag_check(contract, c)
            t0 = time.time()
            c.times = {"pre": (time.perf_counter() - p0) * 1000, "body": 0.0, "checks": 0.0, "n": 0}
            p1 = time.perf_counter()
            prev_soft, prev_skill = api.soft(), tape.SKILL
            api.set_soft(contract.soft or prev_soft)  # nested skills (eat inside a fight) inherit the protection
            tape.SKILL = contract.name                # whose post-action readings the tape is recording
            CALLS.append(c)
            try:
                out = fn(*args, **kwargs)
                if inspect.isgenerator(out):
                    out = _drive(contract, c, out)
            except api.INTERRUPTIONS:
                RESUME[key] = (time.time(), c.base, c.want, c.keep)
                raise
            except McError as e:
                why = bag_full_reason(str(e), _free_slots()) if contract.fills_bag else None
                if why is None:
                    raise
                raise (type(e)(why) if _same_shape(e) else McError(why)) from e
            finally:
                CALLS.pop()
                api.set_soft(prev_soft)
                tape.SKILL = prev_skill
            c.result = out
            if not c.times["n"]:
                c.times["body"] = (time.perf_counter() - p1) * 1000      # a plain function: all of it is body
            p2 = time.perf_counter()
            # judge the effect once the world caught up (a drop in the air, a slot filling next update)
            prev_skill, tape.SKILL = tape.SKILL, contract.name
            try:
                verify = contract.verify
                verified = not verify or skillcore.settle(lambda: verify(c), bool,
                                                                   timeout=VERIFY_SETTLE_S, stable_s=0)
            finally:
                tape.SKILL = prev_skill
            c.times["verify"] = (time.perf_counter() - p2) * 1000
            api.detail(skill_line(contract.name, c.times))
            if not verified:
                msg = f"{contract.name}: finished without reaching its goal"
                raise McError((bag_full_reason(msg, _free_slots()) if contract.fills_bag else None) or msg)
            LAST_S[contract.name] = time.time() - t0
            if STATS is not None:
                try:
                    STATS.record_duration(contract.key(c), time.time() - t0, max(1, contract.units(c)))
                except (IndexError, KeyError, TypeError):
                    pass
            return out

        cast(Any, runner).contract = contract
        contract.runner = runner     # directives call any registered skill by name, whatever module it lives in
        cast(Any, runner).contract = contract      # so the planner can ask `can_run` before it offers the work
        return runner

    return wrap

def bag_full_reason(message, free):
    """Pure: a failure re-said with "bag full" when there was no free slot; None when the bag had room or the message says so already."""
    if free is None or free > 0 or message.lower().startswith("bag full"):
        return None
    return f"bag full (no free slot): {message}"

def bag_check(contract, c):
    """A gatherer with no room stops now, saying so (before it starts and between batches): otherwise it breaks what it cannot pick up."""
    if not contract.fills_bag:
        return
    ids = () if isinstance(contract.fills_bag, bool) else contract.fills_bag(c)     # True: any item
    try:
        inv = skillcore.Inventory()
    except McError:
        return
    if not has_room(inv.slots, inv.free_slots(), set(ids)):
        what = ", ".join(sorted(i.split(":")[-1] for i in ids)[:3]) or "anything"
        raise McError(bag_full_reason(f"{contract.name}: no room for {what}", 0))

def _free_slots():
    try:
        return skillcore.Inventory().free_slots()
    except McError:
        return None

def _same_shape(e):
    """The failure's own type can carry the new message (a one-argument McError subclass)."""
    try:
        type(e)("")
        return True
    except TypeError:
        return False

HEARTBEAT = paths.data("skill-heartbeat")

def _heartbeat(name):
    """Python-side skills run no mod task; the supervisor reads this file so they don't look idle."""
    try:
        with open(HEARTBEAT, "w") as f:
            f.write(f"{time.time():.0f} {name}\n")
    except OSError:
        pass

def _drive(contract, c, gen):
    t0 = time.time()
    dim0 = body_now().get("dimension")
    last, since = world_signature(), t0
    times = getattr(c, "times", None) or {"body": 0.0, "checks": 0.0, "n": 0}
    try:
        while True:
            b0 = time.perf_counter()
            try:
                marker = next(gen)
            except StopIteration as stop:
                times["body"] += (time.perf_counter() - b0) * 1000
                times["n"] += 1
                return stop.value
            b1 = time.perf_counter()
            times["body"] += (b1 - b0) * 1000
            times["n"] += 1
            try:
                out = _drive_checks(contract, c, marker, t0, dim0, last, since)
            finally:
                times["checks"] += (time.perf_counter() - b1) * 1000
            if out is not None:
                last, since = out
            else:
                return None
    finally:
        gen.close()

def skill_line(name, t):
    """Pure: a skill's timing line for detail.log — `skill NAME pre= body= checks= verify= n=` in whole ms: where the
    time between two chains goes (checks: the driver's reads between the body's yields)."""
    parts = [f"{k}={t[k]:.0f}" for k in ("pre", "body", "checks", "verify") if k in t]
    return f"skill {name} " + " ".join(parts) + f" n={t.get('n', 0)}"

def _drive_checks(contract, c, marker, t0, dim0, last, since):
    """The driver's checks after one yield: (last, since) to go on, None when the goal is met."""
    now = time.time()
    _heartbeat(contract.name)
    api.check_interrupt(t0, contract.soft)   # Python-side loops stop too, not only mod tasks
    s = body_now()
    if skillcore.dead(s):
        # dead ends every skill (a dragon fight kept travelling after dying); an interruption, not a failure
        raise api.Died(f"{contract.name}: died")
    if contract.done and contract.done(c):
        return None          # before the dimension: a portal skill's goal IS the other dimension
    if s.get("dimension") and dim0 and s["dimension"] != dim0:
        raise api.DimensionChanged(f"{contract.name}: now in {s['dimension']}, begun in {dim0}")
    bag_check(contract, c)
    metric = marker if marker is not None else world_signature()
    if metric != last:
        last, since = metric, now
    elif now - since >= contract.stall:
        raise TaskStuck(f"{contract.name}: no progress toward its goal for {int(now - since)}s")
    if now - t0 > contract.budget:
        raise TaskStuck(f"{contract.name}: over its {contract.budget}s budget")
    return last, since

_wire_planner()

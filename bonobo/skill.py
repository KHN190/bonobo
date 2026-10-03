"""Skill contracts: every skill declares what it is for and how it is judged, and one runner enforces that.

Contract fields: needs/gives (what the planner prices), remaining (what is left after an interruption), pre,
start, done, verify, budget, stall, provides (effect → the call a plan step makes), prefer, and commands — the pure
batch an open-loop skill sends, so a fight can post the same batch itself. The runner watches the goal metric across
tasks; api.await_task watches one mod task."""
import dataclasses
import functools
import inspect
import time
from typing import Any, Callable, cast

from . import api, arbiter, lifecycle, paths, skillcore, tape, knowledge
from .api import McError, TaskStuck
from .knowledge import have_remainder, needs_rows
from .bag import has_room
from .world import Versioned

REGISTRY: "Versioned[str, Contract]" = Versioned()
Needs = dict[str, int]         # {dimension: minimum}: "tool:pickaxe:2", "item:minecraft:bucket", ... (knowledge.needs_rows)
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
        self.began = time.time()

# an interrupted call's base, wanted bag and anchors, for its resume (read off the world, never a step index); stale after RESUME_TTL_S
RESUME = {}
lifecycle.on_reset(lambda: RESUME.clear(), covers=("RESUME",))     # an anchor from the last life (another site) resumes nothing
RESUME_TTL_S = 300
CALLS = []
# skill → fn (state, args) → the keys a call fixes at its first start, kept so its resume rebuilds against the same anchor
ANCHORS = {}

# what the night's way leaves: ashore first (shelters are made from land: survive.reach_land), then covered or day
NIGHT_WAY_STATES = ("state:ashore", "state:sheltered", "state:day")


def _night_way_running():
    """Is the work running now the night's way itself (a shelter, sleep, waiting for day)? Then nightfall's
    boundary request waits — it must never cut the shelter it asks for."""
    return any(c.contract is not None and (c.contract.name == "sleep"
                                           or set(NIGHT_WAY_STATES) & set(c.contract.gives))
               for c in CALLS)


api.BOUNDARY_EXEMPT = lambda: not CALLS or _night_way_running()     # only a skill's work stops; never the night's way


def current():
    """The call running now (innermost); its `keep` survives an interruption for its resume."""
    return CALLS[-1] if CALLS else None

def budget_end():
    """When the running call's budget runs out: a loop waiting on the world stops by time, not by a count."""
    c = current()
    return c.began + c.contract.budget if c is not None and c.contract is not None else float("inf")

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

# E5: what follows work given up — "cover" (needs.cover, the cheapest shelter that runs here) or "replan" (the next
# round plans again from the world); by its cause (brain.abandon_after), a skill's `abandon` overriding it
ABANDON_WAYS = ("cover", "replan")

@dataclasses.dataclass(frozen=True)
class Spec:
    """What the `skill` decorator declares: the planner's prices and the runner's checks."""
    pre: tuple = ()
    needs: Needs | Callable[[tuple], Needs] | None = None    # a fn of the call's args when the need depends on them
    gives: Any = None               # producing tables (knowledge.Produces) and states it leaves ("state:sheltered")
    start: Callable[[Call], Any] | None = None
    done: Callable[[Call], bool] | None = None
    verify: Callable[[Call], bool] | None = None
    budget: float = 300
    stall: float = 45
    abandon: str | None = None      # E5: what follows a give-up, overriding the cause's (brain.abandon_after)
    units: Callable[[Call], int] | None = None
    key: Callable[[Call], str] | None = None
    soft: bool = False
    commands: Callable[..., list] | None = None
    provides: dict[str, Callable[[Any, Any], Any]] | None = None     # effect → (ctx, step) → the call's args, None
    prefer: float = 0
    fills_bag: bool | Callable[[Call], list[str]] = False
    remaining: Callable[[Any, Call], "Bag | None"] | None = None      # (state, call) → what is left, {} when met
    fights: Callable[[Call], Any] | None = None     # an optional fight's mob kinds (S5: its pre holds the fight line)
    # the planner's facts: when → (step, facts) → [(fact, value)] it needs first, or why it cannot run from here;
    # sets → {token or "*": {fact: value ("token": the step's token)}} what a run leaves (gives' states set True)
    when: Callable[[Any, dict], "list | str"] | None = None
    sets: dict | None = None
    uses: "Needs | Callable[[tuple], Needs] | None" = None      # of its needs, what a run uses up (a fn of its args)
    station: str | None = None      # a block it works at, carried or standing

class Contract:
    # set by the `skill` decorator once built
    needs_fn: Callable[[tuple], Needs] | None
    needs: Needs
    gives: list
    remaining: Callable[[Any, Call], "Bag | None"] | None
    fills_bag: bool | Callable[[Call], list[str]]
    runner: Callable[..., Any]

    def __init__(self, name: str, fn: Callable[..., Any], spec: Spec):
        self.commands = spec.commands
        self.provides = dict(spec.provides or {})
        self.prefer = spec.prefer
        self.when = spec.when
        self.sets = dict(spec.sets or {})
        self.uses = spec.uses
        self.station = spec.station
        # soft: perception's interrupt is left for the body to read instead of ending the skill (a fight takes cover and retries)
        self.soft = spec.soft
        self.name, self.fn, self.pre, self.start, self.done = name, fn, tuple(spec.pre), spec.start, spec.done
        self.fights = spec.fights        # judged where the skill is offered (brain.fight_line_holds), never here
        self.verify = spec.verify if spec.verify is not None else spec.done
        self.budget, self.stall = spec.budget, spec.stall
        if spec.abandon is not None and spec.abandon not in ABANDON_WAYS:
            raise TypeError(f"skill {name}: abandon {spec.abandon!r} is not one of {ABANDON_WAYS}")
        self.abandon = spec.abandon
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
    """Contracts that provide `effect`, preferred first (kept per registry version)."""
    return list(_providers_at(effect, REGISTRY.version))


@functools.lru_cache(maxsize=4096)
def _providers_at(effect, _version):
    return tuple(sorted((c for c in REGISTRY.values() if effect in c.provides), key=lambda c: -c.prefer))

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

def call_of_step(step) -> Needs:
    """Pure: the needs of what carries out `step`, merged over its providers; a call-dependent need reads the args `provides` builds, without ctx."""
    if step.kind == "skill" and step.token in REGISTRY:
        # a skill asked for by name: that skill with the goal's args
        c = REGISTRY[step.token]
        try:
            needs = needs_of(c, (None,) + tuple(step.detail.get("args") or ()))
        except (IndexError, KeyError, TypeError):
            needs = dict(c.needs)            # args it cannot read: its no-call default (the runner refuses them)
        return needs
    for effect in step_keys(step):
        found = providers(effect)
        if not found:
            continue
        needs: Needs = {}
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
        return needs
    return {}

class _Blank(dict):
    def __missing__(self, key):
        return None

def _lenient(step):
    """The step with any missing detail read as None: a missing argument is decompose.missing_detail's refusal, not this one's."""
    import types
    return types.SimpleNamespace(kind=step.kind, token=step.token, count=step.count, detail=_Blank(step.detail))

def _provider(step):
    """The contract that carries out `step` (preferred first), or None."""
    if step.kind == "skill" and step.token in REGISTRY:
        return REGISTRY[step.token]
    return next((c for e in step_keys(step) for c in providers(e)), None)


def when_of_step(step, facts):
    """Pure: the facts `step` needs first ([(fact, value)]) over the contracts that may carry it out (as its needs
    are merged, `step_call`), or why none of them can run from `facts` — or that none is registered."""
    if step.kind == "skill" and step.token in REGISTRY:
        found = [REGISTRY[step.token]]
    else:
        found = next((providers(e) for e in step_keys(step) if providers(e)), [])
    if not found:
        return f"no skill carries out {step.kind} {step.token}"
    asks, reasons = [], []
    for c in found:
        got = [] if c.when is None else c.when(step, facts)
        if isinstance(got, str):
            reasons.append(got)
        else:
            asks += [a for a in got if a not in asks]
    return reasons[0] if reasons and len(reasons) == len(found) else asks


def provider_of(step):
    """The contract that carries out `step` (preferred first), or None."""
    return _provider(step)


def step_contract(step):
    """(contract, the call's args) that carries out `step` — args as its `provides` builds them without a world —
    or None."""
    c = _provider(step)
    if c is None:
        return None
    if step.kind == "skill":
        return c, (None,) + tuple(step.detail.get("args") or ())
    effect = next((e for e in step_keys(step) if e in c.provides), None)
    got = c.provides[effect](None, _lenient(step)) if effect is not None else ()
    return c, (None,) + tuple(got or ())


def step_uses(step):
    """Pure: {item: n} of a step's needs its run uses up (its contract's `uses`, read with the args its `provides`
    builds, as `step_call` reads needs)."""
    c = _provider(step)
    if c is None or c.uses is None:
        return {}
    of = c.uses
    if isinstance(of, dict):
        return dict(of)
    found = step_contract(step)
    try:
        uses = dict(of(found[1])) if found is not None else {}
    except (IndexError, KeyError, TypeError):
        uses = {}                         # args it cannot read: the runner refuses them, nothing to plan around
    return uses


def sets_of_step(step):
    """Pure: {fact: value} a run of `step` leaves — its contract's states given and its `sets` for this token."""
    c = _provider(step)
    if c is None:
        return {}
    out = {g: True for g in c.gives if isinstance(g, str)}
    for key in ("*", step.token):
        for fact, value in c.sets.get(key, {}).items():
            out[fact] = step.token if value == "token" else value
    return out


def station_of_step(step):
    """Pure: the station block (or group) the contract carrying out `step` works at, carried or standing; None."""
    c = _provider(step)
    return None if c is None else c.station


def steps_for_fact(fact, value):
    """Pure: [(kind, token)] of the steps whose run sets `fact` to `value`: a contract giving it as a state, or one whose
    `sets` says so — each named by the contract's own effect keys."""
    out = []
    for c in sorted(REGISTRY.values(), key=lambda c: -c.prefer):
        tokens = []
        if value is True and fact in c.gives:
            tokens.append(None)
        for key, facts in c.sets.items():
            if fact in facts and (facts[fact] == value or facts[fact] == "token" and isinstance(value, str)):
                tokens.append((value if facts[fact] == "token" else None) if key == "*" else key)
        for token in tokens:
            for effect in c.provides:
                kind, _, own = effect.partition(":")
                if own and (token is None or own == token):
                    out.append((kind, own))
                elif not own and token is not None:
                    out.append((kind, token))
    return list(dict.fromkeys(out))


def _wire_planner():
    knowledge.STEP_CALL = call_of_step
    knowledge.STEP_WHEN = when_of_step
    knowledge.STEP_SETS = sets_of_step
    knowledge.STEP_USES = step_uses
    knowledge.FACT_STEPS = steps_for_fact
    knowledge.STEP_STATION = station_of_step

def declared(name, needs, gives=(), remaining=None):
    """Refuse at import a skill that does not state `needs` and `gives` ({} when none), or leaves a world state without `remaining`."""
    missing = [k for k, v in (("needs", needs), ("gives", gives)) if v is None]
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
    from .knowledge import held_count
    asked = _asked(contract, args)
    if asked is None:
        return None
    token, _at, n = asked
    return {token: held_count(state["inv"], token) + n}

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
        declared(name or fn.__name__, spec.needs, spec.gives, spec.remaining)
        contract = Contract(name or fn.__name__, fn, spec)
        # a need that depends on the call is a fn of its args; `needs` is then the no-tier default
        contract.needs_fn = spec.needs if callable(spec.needs) else None
        contract.needs = {} if callable(spec.needs) else dict(spec.needs or {})
        contract.gives = gives_of(spec.gives)
        contract.remaining = spec.remaining
        from .knowledge import CONTRACT_FACTS, PRODUCERS
        for g in contract.gives:
            if not isinstance(g, str) and g not in PRODUCERS:
                PRODUCERS.append(g)
        CONTRACT_FACTS.update(g for g in contract.gives if isinstance(g, str) and g.startswith("state:"))
        for v in contract.sets.values():
            CONTRACT_FACTS.update(v if isinstance(v, dict) else ())
        # a gatherer: True, or c → the item ids it gathers
        contract.fills_bag = spec.fills_bag
        REGISTRY[contract.name] = contract

        @functools.wraps(fn)
        def runner(*args, **kwargs):
            # a fight holding the body pauses the skill: wait it out, resume from the world (no try spent)
            deadline = time.monotonic() + contract.budget
            while True:
                try:
                    return once(*args, **kwargs)
                except api.FightHolds as e:
                    t0 = time.monotonic()
                    over = fight_over(deadline)
                    held_log(contract.name, str(e), time.monotonic() - t0, over)
                    if not over:
                        raise

        def once(*args, **kwargs):
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
                why = bag_full_reason(str(e), _bag_slots_free()) if contract.fills_bag else None
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
                raise McError((bag_full_reason(msg, _bag_slots_free()) if contract.fills_bag else None) or msg)
            LAST_S[contract.name] = time.time() - t0
            if STATS is not None:
                try:
                    STATS.record_duration(contract.key(c), time.time() - t0, max(1, contract.units(c)))
                except (IndexError, KeyError, TypeError) as e:
                    api.swallowed("skill.once", e)
                    pass
            return out

        cast(Any, runner).contract = contract
        contract.runner = runner     # directives call any registered skill by name, whatever module it lives in
        cast(Any, runner).contract = contract      # so the planner can ask `can_run` before it offers the work
        return runner

    return wrap

FIGHT_POLL_S = 0.5      # how often a paused skill looks whether the fight let the body go
HELD_WAITS = []         # each fight wait a skill sat out: skill, why, seconds, whether the body came back (a readout)
HELD_KEEP = 50          # the most recent kept
lifecycle.on_reset(lambda: HELD_WAITS.clear(), covers=("HELD_WAITS",))


def held_log(name, why, waited_s, over):
    """Record one fight wait (the report and detail.log read it)."""
    HELD_WAITS.append({"skill": name, "why": why, "waited_s": round(waited_s, 2), "resumed": over,
                       "t": round(time.time(), 1)})
    del HELD_WAITS[:-HELD_KEEP]
    api.detail(f"skill {name} held by a fight {waited_s:.1f}s: {'resumed' if over else 'over budget'} ({why})")


def fight_over(deadline, sleep=time.sleep, now=time.monotonic):
    """Poll until no lease holds the body; False when the deadline came first."""
    while arbiter.BODY.holder() is not None:
        if now() >= deadline:
            return False
        sleep(FIGHT_POLL_S)
    return True

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
    except McError as e:
        api.swallowed("skill.bag_check", e)
        return
    if not has_room(inv.slots, inv.free_slots(), set(ids)):
        what = ", ".join(sorted(i.split(":")[-1] for i in ids)[:3]) or "anything"
        raise McError(bag_full_reason(f"{contract.name}: no room for {what}", 0))

def _bag_slots_free():
    try:
        return skillcore.Inventory().free_slots()
    except McError as e:
        api.swallowed("skill._bag_slots_free", e)
        return None

def _same_shape(e):
    """The failure's own type can carry the new message (a one-argument McError subclass)."""
    try:
        type(e)("")
    except TypeError as wrong:
        api.detail(f"   {type(e).__name__} takes more than a message ({wrong}): raised as it was")
        return False
    return True

HEARTBEAT = paths.data("skill-heartbeat")

def _heartbeat(name):
    """Python-side skills run no mod task; the supervisor reads this file so they don't look idle."""
    try:
        with open(HEARTBEAT, "w") as f:
            f.write(f"{time.time():.0f} {name}\n")
    except OSError as e:
        api.swallowed("skill._heartbeat", e)
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
    if skillcore.really_dead(s):
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
        raise TaskStuck(f"{contract.name}: no progress toward its goal for {int(now - since)}s",
                        then=contract.abandon or "replan")
    if now - t0 > contract.budget:
        raise TaskStuck(f"{contract.name}: over its {contract.budget}s budget", then=contract.abandon or "replan")
    return last, since

_wire_planner()

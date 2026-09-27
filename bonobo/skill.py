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
  provides  {effect: adapter}: what running this skill makes true, and how a plan step asks for it. An effect is
            "<step kind>:<token>", "item:<token>" or "<step kind>" (most specific first, see `step_keys`); the adapter
            is (ctx, step) -> the arguments after ctx, or None when this skill cannot serve that step here. Adding a
            skill is one decorated function in any imported module: the brain finds it here (`provider`), never
            through a switch of its own.
  prefer    order among skills that provide the same effect (higher first)
  commands  optional, pure: (state, args) -> [task]. The whole command batch an OPEN-LOOP skill would send, built
            from a state dict and executed by nobody. The skill's own body runs that batch (`api.run_chain`) and
            then its verify, so a fight can take the same batch and post it itself — append to it, or /stop and
            post again — without a second definition of the skill. Closed-loop skills (seek, chop, hunt: they look
            as they go) leave it None.

Two levels of stuck detection: api.await_task watches one mod task (10 s without visible movement), the runner
watches the skill's own goal metric across tasks (a hunt that walks around forever without closing in)."""
import functools
import inspect
import time

from . import api, paths, skillcore, tape
from .api import McError, TaskStuck

REGISTRY = {}
VERIFY_SETTLE_S = 3.0      # how long a finished skill's effect may take to show up in the world

class Call:
    """What contract callables see: the call's args, the baseline and (for verify) the result."""

    def __init__(self, args, kwargs):
        self.args, self.kwargs, self.base, self.result = args, kwargs, None, None
        self.want = None            # an item skill's desired bag, fixed at its start (skill.wanted)
        self.keep = {}              # what the skill fixed at its first start (an anchor: a column, a direction)

# An interrupted call's desired state — its base, the bag it wants, its anchors — kept for the same call's resume: the rest is then read off the world against it (`remaining_of`), never a step index. Stale after RESUME_TTL_S.
RESUME = {}
RESUME_TTL_S = 300
CALLS = []
# skill → pure fn (state, args) → the state keys a call fixes at its first start ({"anchor": …}, {"started": …}): kept across an interruption, so its resume is rebuilt from the world against the same anchor.
ANCHORS = {}

def current():
    """The call running now (innermost): its `keep` holds what it fixed at its first start, kept across an interruption for its resume."""
    return CALLS[-1] if CALLS else None

def _resume_key(contract, args):
    return contract.name, repr(args[1:])

def _resumed(contract, args):
    """(base, want, keep) this call left when interrupted, if fresh; else None. Taken: one resume per interruption."""
    kept = RESUME.pop(_resume_key(contract, args), None)
    if kept is None or time.time() - kept[0] > RESUME_TTL_S:
        return None
    return kept[1:]

def body_now():
    """The body's reading this round (one /state): the runner's own seam for death and a dimension change."""
    return api.get("/state")

def world_signature():
    from .world import Inventory
    s = api.get("/state")
    inv = Inventory()
    return ((s["blockX"], s["blockY"], s["blockZ"]),
            tuple(sorted((x["id"], x.get("count", 1), x.get("damage", 0)) for x in inv.slots)))

class Contract:
    def __init__(self, name, fn, pre, start, done, verify, budget, stall, units, key, soft=False,
                 commands=None, provides=None, prefer=0):
        self.commands = commands
        self.provides = dict(provides or {})
        self.prefer = prefer
        # soft: perception's interrupt is left standing for the body to read (`api.INTERRUPT`) instead of ending the skill. A fight answers danger by taking cover and trying again — four "dragon breath close" interrupts in a row otherwise killed whole bench runs before anything was built.
        self.soft = soft
        self.name, self.fn, self.pre, self.start, self.done = name, fn, pre, start, done
        self.verify = verify if verify is not None else done
        self.budget, self.stall = budget, stall
        # Measured durations (STATS, which the cost model reads back): `units(c)` how many units a call does (blocks, logs, kills, items), `key(c)` the statistics key (e.g. "mine:minecraft:raw_iron").
        self.units = units or (lambda c: 1)
        self.key = key or (lambda c: name)
        self.doc = (inspect.getdoc(fn) or "").split("\n")[0]

    def describe(self):
        return f"{self.name:<18} budget {self.budget:>4}s  stall {self.stall:>3}s  {self.doc}"

# Set by the brain to the memory object: record_duration(key, seconds, units) / duration(key).
STATS = None
LAST_S = {}     # skill name → seconds its last verified run took, preconditions and planning excluded
MIN_SAMPLES = 3

def can_run(fn, *args, **kwargs):
    """Would this skill's preconditions pass right now? (ok, why not). The same `pre` the runner checks, asked BEFORE the planner offers the work rather than after it fails. A skill that says "no torches to spare" already knew it could not run; nobody asked, so the pool priced it, chose it, and learned by failing — ninety times in four minutes, because the idle rule kept thawing it."""
    contract = getattr(fn, "contract", None)
    if contract is None:
        return True, None
    c = Call(args, kwargs)
    for check in contract.pre:
        try:
            check(c)
        except Exception as e:
            return False, str(e) or type(e).__name__
    missing = unmet(contract, args, skillcore.Inventory)
    if missing:
        return False, f"{contract.name}: {skillcore.NeedMissing(missing)}"
    return True, None

def unmet(contract, args, bag):
    """Pure given the bag: what of this call's hard needs (needs_of) the bag (`bag()`, read only when there are needs) does not hold — {} when it can start. The one start check of `needs`: the runner refuses the call on it (NeedMissing, whoever calls), can_run asks it before the work is offered (dispatch.can_start)."""
    from .knowledge import have_remainder, needs_rows
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

def needs_of(contract, args):
    """The hard prerequisites of this call: the static `needs`, or the function of the call's args."""
    return dict(contract.needs_fn(args)) if getattr(contract, "needs_fn", None) else dict(contract.needs)

def step_call(step):
    """Pure: (needs, speed) of what carries out a planned `step` — every skill that provides it (step_keys, first effect anyone provides), merged: the needs of the call each would make (needs_of; a need that depends on the call reads the args its `provides` builds from the step, with no context — None: it would not take this step), the most per dimension; its speed tools that help this call, the most saved per unit. The planner's and the cost model's one reading of both (knowledge.step_call)."""
    if step.kind == "skill" and step.token in REGISTRY:
        # A skill asked for by name (goals' "skill" template): that skill, called with the goal's args.
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
        needs, speed = {}, {}
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
            # A shovel speeds up soft ground only: not a call that needs a pickaxe (stone, ore).
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
    """The step as a provider reads it for its needs: a detail it lacks reads None (the need is the call's; a missing argument is decompose.missing_detail's refusal, not this one's)."""
    import types
    return types.SimpleNamespace(kind=step.kind, token=step.token, count=step.count, detail=_Blank(step.detail))

def _wire_planner():
    from . import knowledge
    knowledge.STEP_CALL = step_call

def declared(name, needs, speed, gives=(), remaining=None):
    """Every skill states its hard prerequisites (`needs`, {dimension: minimum}) and the optional tools that speed it up (`speed`, {tool kind: seconds saved per unit}) — written out, `{}` when there are none. A skill that says neither is refused at import: an unstated need is one the planner can never price. A skill whose product lands in the world rather than the bag (`world_effect`: a state in its gives) also states how what is left of it is measured (`remaining`, a pure fn (state, call) → {what: missing}, {} when met): after any interruption the rest is read off the world, and a skill that cannot say what is left would redo it."""
    missing = [k for k, v in (("needs", needs), ("speed", speed), ("gives", gives)) if v is None]
    if missing:
        raise TypeError(f"skill {name!r} declares no {' and no '.join(missing)} (write {{}} when there are none)")
    if world_effect(gives) and not callable(remaining):
        raise TypeError(f"skill {name!r} leaves {', '.join(g for g in gives_of(gives) if isinstance(g, str))} in the "
                        f"world and declares no remaining= (a pure fn (state, call) → what is still missing, {{}} "
                        f"when met)")
    # Nothing it gives is an item (whose rest is derived from the bag) and no remaining= of its own: then nothing could say what is left after an interruption. `gives={}` slipped 51 world-effect skills past the rule above.
    if not any(not isinstance(g, str) for g in gives_of(gives)) and not callable(remaining):
        raise TypeError(f"skill {name!r} gives no item and declares no remaining= (a state it leaves in gives, and a "
                        f"pure fn (state, call) → what is still missing, {{}} when met)")

def world_effect(gives):
    """Does this skill's product land in the world (a state it leaves: "state:sheltered"), not in the bag?"""
    return any(isinstance(g, str) for g in gives_of(gives))

def wanted(contract, state, args):
    """The bag an item skill's call wants, fixed when it starts: {token: held then + the count asked} — from its gives (producing tables) and its args: the token is the first argument a table produces (else the table's only key), the count the first whole number after it (else 1). None for a skill that produces no item."""
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
    """Pure: the call's args with its count set to what is left of its item (`rest`, remaining_of), so a resumed call asks only for the rest; the args unchanged when the count is not an argument."""
    asked = _asked(contract, args)
    if asked is None or asked[1] is None or asked[0] not in rest:
        return tuple(args)
    out = list(args)
    out[asked[1]] = rest[asked[0]]
    return tuple(out)

def remaining_of(contract, state, call):
    """What is left of this call, read off `state` (skillcore.body_state's shape) — {what: missing}, {} when done, None when the world cannot say (only the skill running can): the skill's own `remaining`, else for an item skill the bag it wants (`call.want`, `wanted`) less what the bag holds (knowledge.have_remainder)."""
    if contract.remaining is not None:
        return contract.remaining(state, call)
    want = getattr(call, "want", None)
    if want is None:
        return None
    from .knowledge import have_remainder
    return have_remainder(state["inv"], [[t, n] for t, n in want.items()])

def gives_of(gives):
    """A skill's `gives` as a list: producing tables (knowledge.Produces) and states it leaves ("state:sheltered")."""
    if isinstance(gives, dict):
        return [f"state:{k}" if not str(k).startswith("state:") else k for k in gives] if gives else []
    return list(gives) if isinstance(gives, (list, tuple)) else [gives]

def skill(name=None, *, pre=(), needs=None, speed=None, gives=None, start=None, done=None, verify=None, budget=300, stall=45,
          units=None, key=None, soft=False, commands=None, provides=None, prefer=0,
          fills_bag=False, remaining=None):
    """`needs` is the same preconditions stated as STATE — {dimension: minimum} — instead of as a check. A check can only answer "no". A dimension can be priced: `solve.reach_cost` walks the requirement graph and says what it costs to get there, so "no torches" stops being a refusal and becomes "three torches first, about forty seconds". The checks in `pre` stay as the runtime guard; `needs` is what the planner reads."""
    def wrap(fn):
        declared(name or fn.__name__, needs, speed, gives, remaining)
        contract = Contract(name or fn.__name__, fn, tuple(pre), start, done, verify, budget, stall, units,
                            key, soft, commands, provides, prefer)
        # A need that depends on the call (the pickaxe tier of the block mined) is a function of the call's args; `needs` is then what the call with no tier asks, `needs_of(args)` what this call asks.
        contract.needs_fn = needs if callable(needs) else None
        contract.needs = {} if callable(needs) else dict(needs)
        contract.speed = dict(speed)
        contract.gives = gives_of(gives)
        contract.remaining = remaining
        from .knowledge import PRODUCERS
        for g in contract.gives:
            if not isinstance(g, str) and g not in PRODUCERS:
                PRODUCERS.append(g)
        # A gatherer: True (anything it takes needs a free slot) or c -> the item ids it gathers. Checked before it starts and between its batches (bag_check), and its failures on a full bag say so (bag_full_reason).
        contract.fills_bag = fills_bag
        REGISTRY[contract.name] = contract

        @functools.wraps(fn)
        def runner(*args, **kwargs):
            key = _resume_key(contract, args)
            c = Call(args, kwargs)
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
                # Resumed: the rest read off the world against what this call wanted (its own `remaining`, or the bag it wanted) — done while away, or asked again for only what is missing.
                rest = remaining_of(contract, skillcore.body_state(args[0] if args else None), c)
                if rest == {}:
                    return None
                args = with_rest(contract, args, rest or {})
                c.args = args
            if contract.done and contract.done(c):
                return None
            bag_check(contract, c)
            t0 = time.time()
            prev_soft, prev_skill = api.SOFT, tape.SKILL
            api.SOFT = contract.soft or prev_soft     # nested skills (eat inside a fight) inherit the protection
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
                api.SOFT, tape.SKILL = prev_soft, prev_skill
            c.result = out
            # The effect is judged once the world has caught up with it, not the instant the body returns: a drop still in the air, a slot that fills on the next update. Stops at the first reading that holds.
            prev_skill, tape.SKILL = tape.SKILL, contract.name
            try:
                verified = not contract.verify or skillcore.settle(lambda: contract.verify(c), bool,
                                                                   timeout=VERIFY_SETTLE_S, stable_s=0)
            finally:
                tape.SKILL = prev_skill
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

        runner.contract = contract
        contract.runner = runner     # directives call any registered skill by name, whatever module it lives in
        runner.contract = contract      # so the planner can ask `can_run` before it offers the work
        return runner

    return wrap

def bag_full_reason(message, free):
    """Pure: a failure said again with its cause named when the bag had no free slot (`free` = 0) — a gatherer that could not pick up what it made failed "for no reason" (chop, hunt, mine, loot on a full bag). None when the bag had room (the failure is its own) or the message already names the bag."""
    if free is None or free > 0 or message.lower().startswith("bag full"):
        return None
    return f"bag full (no free slot): {message}"

def bag_check(contract, c):
    """A gatherer with nowhere to put what it gathers stops now, saying so: a full-bag chop broke logs it could not pick up, found the trunk empty and moved on to the next tree until its time ran out. One check for every gatherer (`fills_bag`), before it starts and between its batches — none of them re-checks it."""
    if not contract.fills_bag:
        return
    from .bag import has_room
    ids = contract.fills_bag(c) if callable(contract.fills_bag) else ()
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
    """Skills that work on the Python side (watching a furnace) run no mod task; the supervisor reads this file so it doesn't mistake them for an idle agent."""
    try:
        with open(HEARTBEAT, "w") as f:
            f.write(f"{time.time():.0f} {name}\n")
    except OSError:
        pass

def _drive(contract, c, gen):
    t0 = time.time()
    dim0 = body_now().get("dimension")
    last, since = world_signature(), t0
    try:
        while True:
            try:
                marker = next(gen)
            except StopIteration as stop:
                return stop.value
            now = time.time()
            _heartbeat(contract.name)
            api.check_interrupt(t0, contract.soft)   # Python-side loops stop too, not only mod tasks
            s = body_now()
            if skillcore.dead(s):
                # Dead ends every skill now: a dragon fight kept issuing 20+ "travel: no route" after dying. An interruption, not the skill's failure (brain.outcome_of: recover first, then replan).
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
    finally:
        gen.close()

_wire_planner()

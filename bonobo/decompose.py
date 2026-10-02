"""L2: a goal and a bag in, an ordered list of steps out. The brain calls only this. decompose(inv, goal, cost, solver=None, pending=None) -> [Step] Item goals (have, craft, milestone) go to a SOLVER; the rest become action steps, after whatever materials they need (build). Solvers are registered by name: `planner` (planner.py, recursive descent over the requirement graph) is the default; `solve` (solve.py over the action columns) is tried when it cannot plan, or chosen by name per task. Every solver gets the same cost model (cost.Cost), so their steps are priced in the same ticks. Pure apart from what the cost model reads (one cached /find per kind)."""

import math
import time

from . import beliefs, blueprints, goals, knowledge, actions as act, skill
from .api import McError
from .cost import TICKS_PER_S
from .data import DAY_TICKS, NIGHT_END, POD_BLOCKS, is_night, mid
from .planner import Planner, Step, Unplannable
from .solve import Unsolvable, solve
from .knowledge import members
from .data import TAKEABLE

SOLVERS = {}          # name -> fn(inv, needs, cost, pending) -> [Step]
ORDER = []            # fallback order when no solver is named

def register(name, fn):
    """Add a solver. The first registered is the default."""
    SOLVERS[name] = fn
    if name not in ORDER:
        ORDER.append(name)

def _planner(inv, needs, cost, pending=None, jobs=None):
    return Planner.from_inventory(inv, cost, pending, jobs).plan(needs)

register("planner", _planner)

def _solve(inv, needs, cost, pending=None, jobs=None):
    """The column solver: slower, sees further — where to go, what to take ready-made, which half-done work to finish."""

    target = act.target_of(needs)
    if not target:
        return []
    if getattr(cost, "snap", None) is None or getattr(cost, "mem", None) is None:
        raise Unplannable("the column solver needs a snapshot and a memory")
    vector = act.state_of(cost.snap, cost.mem, reachable=cost.reachable)
    try:
        found = solve(act.table(cost, vector), vector, target)
    except Unsolvable as e:
        raise Unplannable(str(e))
    return [act.to_step(a, n, cost) for a, n in found.steps()]

register("solve", _solve)

def solve_needs(inv, needs, cost, solver=None, pending=None, jobs=None, taken=()):
    """Steps that make `needs` held. The named solver, else each registered one in turn until one plans. `pending`:
    counted as held (planned sources' and jobs' outputs); `jobs`: of it, what running jobs make (awaited when used);
    `taken`: withdraws already planned."""
    if not needs:
        return []
    names = [solver] if solver else list(ORDER)
    last = None
    for name in names:
        fn = SOLVERS.get(name)
        if fn is None:
            last = Unplannable(f"no solver named {name!r}")
            continue
        try:
            return take_stored(fn(inv, needs, cost, pending, **({"jobs": jobs} if jobs else {})), cost, taken)
        except Unplannable as e:
            last = e
    raise last or Unplannable("no solver could plan this")

def container_p(record, ids, age_s, rate):
    """Pure: the chance a container holds one of `ids`, from its record: held then, discounted by the change rate
    over the record's age; not held then, the chance it changed since (an unopened one: p_unknown)."""
    held = any(record["items"].get(i, 0) > 0 for i in ids)
    return math.exp(-rate * age_s) if held else 1.0 - math.exp(-rate * age_s)

MATERIAL_STEPS = ("gather", "mine", "hunt")


def take_stored(steps, cost, already=()):
    """Material steps replaced, wholly or in part, by withdrawing from a known container where cheaper."""
    import copy
    mem, snap = getattr(cost, "mem", None), getattr(cost, "snap", None)
    if mem is None or snap is None:
        return steps
    taken = {}
    for w in already:
        if w.kind == "withdraw":
            key = (tuple(w.detail.get("pos") or ()), mid(w.token))
            taken[key] = taken.get(key, 0) + int(w.count)
    rate, now, out = mem.container_change_rate(), time.time(), []
    for st in steps:
        stored = mem.stored(st.token, snap.dimension) if st.kind in MATERIAL_STEPS and st.count > 0 else []
        left, per = int(st.count), (st.est / st.count if st.count else 0)
        for pos, item, have in sorted(stored, key=lambda r: math.dist(r[0], snap.feet)):
            free = have - taken.get((tuple(pos), mid(item)), 0)
            if left <= 0 or free <= 0:
                continue
            take = _action("withdraw", item, cost, pos=list(pos))
            take.count = min(left, free)
            rec = mem.container_record(pos)
            p = container_p(rec, set(members(st.token)), now - rec.get("at", now), rate) if rec else 1.0
            if p > 0 and take.est / p < per * take.count:
                out.append(take)
                taken[(tuple(pos), mid(item))] = taken.get((tuple(pos), mid(item)), 0) + take.count
                left -= take.count
        if left == st.count:
            out.append(st)
        elif left > 0:
            rest = copy.deepcopy(st)
            scale = left / st.count
            rest.count = left
            for key in ("breaks", "kills"):
                if key in rest.detail:
                    rest.detail[key] = max(1, math.ceil(rest.detail[key] * scale))
            rest.est = cost.estimate(rest)
            out.append(rest)
    return out


def p_unknown(k, n):
    """Pure: the chance an unopened container holds the item, from what the opened ones held: k of n (the rule of
    succession: 1/2 before any is opened)."""
    return (k + 1) / (n + 2)

def from_containers(inv, needs, cost, solver=None, pending=None):
    """Take what containers hold where that is cheaper than making it, each record weighed by the chance it still
    holds it (memory.container_p); when the records cannot cover a need, look in the unopened home container whose
    expected saving pays its walk and open best (p_unknown × the make it saves − the look): (steps, pending)."""

    mem, snap = getattr(cost, "mem", None), getattr(cost, "snap", None)
    extra = dict(pending or {})
    if mem is None or snap is None or not hasattr(mem, "stored"):
        return [], extra
    steps = []
    rate = mem.container_change_rate()
    now = time.time()
    for need in needs:
        if need[0] == "tool":
            continue
        token, n = need[0], int(need[1])
        ids = set(members(token))
        on_way = {token: sum(v for k, v in extra.items() if k in ids)}
        short = goals.have_remainder(inv, [[token, n]], on_way).get(token, 0)

        def make_s(k):
            try:
                return cost.plan_s(solve_needs(inv, [(token, k)], cost, solver, extra)) * TICKS_PER_S
            except Unplannable:
                return math.inf

        for pos, item, have in sorted(mem.stored(token, snap.dimension), key=lambda r: math.dist(r[0], snap.feet)):
            if short <= 0:
                break
            take = min(short, have)
            step = _action("withdraw", item, cost, pos=list(pos))
            step.count = take
            rec = mem.container_record(pos)
            p = container_p(rec, ids, now - rec.get("at", now), rate) if rec else 1.0
            if p > 0 and step.est / p < make_s(take):
                steps.append(step)
                extra[item] = extra.get(item, 0) + take
                short -= take
        if short > 0:
            look = _best_look(mem, ids, snap, cost, make_s(short))
            if look is not None:
                steps.append(look)
                break                      # what it holds decides the rest: planned again once it is seen
    return steps, extra

def _best_look(mem, ids, snap, cost, make_ticks):
    """The look into an unopened home container that pays best (p_unknown from the opened ones × the make it saves −
    the look's own est), or None when none pays."""
    homes = mem.home_containers(snap.dimension)
    opened = [c for c in homes if mem.container_record(c) is not None]
    k = sum(1 for c in opened if any(mem.container_record(c)["items"].get(i, 0) > 0 for i in ids))
    p = p_unknown(k, len(opened))
    best = None
    for c in homes:
        if mem.container_record(c) is not None:
            continue
        step = _action("look", "container", cost, pos=list(c))
        worth = p * make_ticks - step.est
        if worth > 0 and (best is None or worth > best[0]):
            best = (worth, step)
    return best[1] if best else None

def effect_detail(kind, token, count):
    """What an effect step's skill reads from `detail`, filled where the tables know it."""

    if kind == "hunt" and token in knowledge.HUNT:
        return {"types": list(knowledge.HUNT[token])}
    if kind == "mine" and mid(token) in knowledge.MINE:
        blocks, tier = knowledge.MINE[mid(token)]
        return {"blocks": list(blocks), "tier": tier}
    if kind == "take" and token in TAKEABLE:
        return {"blocks": list(TAKEABLE[token]["blocks"])}
    if kind == "craft":
        return {"times": count, "inputs": {}}
    return {}

def missing_detail(step):
    """The detail key the step's providers ask for and the step lacks, or None."""


    class _Detail(dict):
        def __missing__(self, key):
            raise LookupError(key)
    probe = Step(step.kind, step.token, step.count, _Detail(step.detail))
    missing = None
    for effect in skill.step_keys(step):
        for contract in skill.providers(effect):
            said = _offline(contract.provides[effect], probe)
            if said[0] != "missing":
                return None                  # served from the step, or the world must say: not refusable offline
            missing = missing or said[1]
    return missing

def _offline(provide, probe):
    """What a provider says with no world: ("served",), ("missing", key) or ("world", why)."""
    try:
        provide(None, probe)
        return ("served",)
    except LookupError as e:
        return ("missing", str(e.args[0]))
    except (AttributeError, TypeError, McError) as e:
        return ("world", type(e).__name__)

# things found in one place: (kind, token) → the steps to get there first, unless already there
LIVES_IN = {("hunt", "minecraft:blaze_rod"): [("portal", "minecraft:the_nether", {}), ("seek", "fortress", {})],
            ("barter", "piglin"): [("portal", "minecraft:the_nether", {})]}

# other ways to get or build a thing beside the solver's: each run's steps, yield, needs and what must be known; priced, never ranked here
SOURCES = {
    # Trade gold with piglins in the Nether (nether.barter_piglin) instead of hunting endermen.
    "minecraft:ender_pearl": [{"name": "barter", "steps": [("barter", "piglin", {"ingots": 8})], "yields": 1,
                               "needs": [("minecraft:gold_ingot", 8), ("minecraft:golden_helmet", 1)]}],
    # Cast the frame where it stands (building.cast_portal): no obsidian carried, no diamond pickaxe.
    "build:nether_portal": [{"name": "cast", "steps": [("cast", "nether_portal", {})], "yields": 1,
                             "needs": [("minecraft:water_bucket", 1), ("minecraft:bucket", 1),
                                       ("minecraft:flint_and_steel", 1), ("building", 16)],
                             "known": ("lava", "no lava known and no lava bucket", "minecraft:lava_bucket"),
                             "not_in": ("minecraft:the_nether", "water cannot be poured in the Nether")}],
    # Blocks to build with, dug by hand where dirt or grass is in sight: no pickaxe, no tree for one.
    "building": [{"name": "dig by hand", "steps": [("mine", "minecraft:dirt",
                                                    {"blocks": ["dirt", "grass_block"], "tier": None, "breaks": 1})],
                  "yields": 1, "needs": [], "near": (["dirt", "grass_block"], "no dirt or grass in sight"),
                  "gives": "minecraft:dirt"}],
    # A night without a bed (needs.overnight): the default is the bed's plan; these are the other ways through it.
    # A night with a bed (needs.overnight asks these first: a shelter is only for a night no bed can end): the home's
    # bed when a walk reaches it (bed_reach: night_facts home_bed / home_walk_s), priced by that walk; a carried bed
    # (or one the default plans) is the default's — the sleep skill gives it a room, light, the gate and takes it back.
    "overnight bed": [{"name": "home", "steps": [("shelter", "home", {})], "yields": 1, "needs": [],
                       "when": ("home_bed", "no home bed a walk reaches"), "extra_s": ("home_walk_s",)}],
    # A night no bed can end: a shelter to wait in (no sleep is paired with it).
    "overnight": [{"name": "dig in", "steps": [("shelter", "dig_in", {})], "yields": 1,
                   "needs": [("tool", "pickaxe", 0)],
                   "unless": ("no_dig_site", "the ground here takes no lid below the ground line"), "extra_s": ("wait_s",)},
                  {"name": "dig in by hand", "steps": [("shelter", "dig_in", {})], "yields": 1, "needs": [],
                   "when": ("soft_ground", "no ground near digs by hand: it needs a pickaxe"),
                   "extra_s": ("soft_walk_s", "wait_s")},   # the walk to that ground (survive.soft_spot), the night
                  {"name": "wall in", "steps": [("shelter", "pod", {})], "yields": 1, "needs": [("building", POD_BLOCKS)],
                   "extra_s": ("wait_s",)},
                  # the hut's needs are read from its blueprint (a hand copy named the wrong stone)
                  {"name": "hut", "steps": [("shelter", "hut", {})], "yields": 1,
                   "needs": sorted(blueprints.materials(blueprints.SHELTER).items()), "extra_s": ("wait_s",)}],
}

def cheapest(key, amount, default, inv, cost, solver=None, extra=None, facts=None, priced=False):
    """The cheapest way to `key` × amount: the solver's steps, or a SOURCES[key] source's runs plus their needs;
    (steps, name), with its seconds (extras included) when `priced`."""

    mem, snap = getattr(cost, "mem", None), getattr(cost, "snap", None)
    why = []
    try:
        best, best_steps, name = cost.plan_s(default()), None, "default"
    except Unplannable as e:
        best, best_steps, name = math.inf, None, None
        why.append(f"default: {e}")
    for src in SOURCES.get(key, ()):
        if src["name"] in (facts or {}).get("cooled", ()):
            why.append(f"{src['name']}: failed here lately (cooling)")
            continue                 # a way that just failed is not priced again tonight: the next way is
        unless = src.get("unless")
        if unless and (facts or {}).get(unless[0]):
            why.append(f"{src['name']}: {unless[1]}")
            continue                 # only what can finish here is offered
        when = src.get("when")
        if when and not (facts or {}).get(when[0]):
            why.append(f"{src['name']}: {when[1]}")
            continue
        away = src.get("not_in")
        if away and snap is not None and getattr(snap, "dimension", None) == away[0]:
            why.append(f"{src['name']}: {away[1]}")
            continue
        near = src.get("near")
        if near and cost.distance(near[0]) is None:
            why.append(f"{src['name']}: {near[1]}")
            continue
        known = src.get("known")
        if known and not ((mem is not None and snap is not None and mem.seen(known[0], snap.dimension))
                          or (len(known) > 2 and inv.count(known[2]))):
            why.append(f"{src['name']}: {known[1]}")
            continue
        runs = math.ceil(amount / src["yields"])
        try:
            needs = [n if n[0] == "tool" or n[0].endswith("_helmet") else (n[0], n[1] * runs) for n in src["needs"]]
            sourced, got = from_sources(inv, needs, cost, solver, extra)
            pre = sourced + solve_needs(inv, needs, cost, solver, got)     # the way _decompose plans a goal
        except Unplannable as e:
            why.append(f"{src['name']}: {e}")
            continue
        own = []
        for kind, tok, detail in src["steps"]:
            # priced one run at a time; the step does every run
            step = Step(kind, tok, runs, {**detail, **({"breaks": detail["breaks"] * runs} if "breaks" in detail
                                                       else {})})
            step.est = cost.estimate(Step(kind, tok, 1, dict(detail))) * runs
            own.append(step)
        # A source's own extra seconds the place facts say (a walk, the night waited), added to its plan.
        seconds = cost.plan_s(pre + own) + sum(float((facts or {}).get(k, 0.0)) for k in src.get("extra_s", ()))
        if seconds < best:
            best, best_steps, name = seconds, pre + own, src["name"]
    if best == math.inf:
        raise Unplannable(f"no way to {key}: " + "; ".join(why))
    return (best_steps, name, best) if priced else (best_steps, name)

# Milestones that end in doing, not holding: after their items, these steps (run once — goals.done says None).
THEN = {"end portal": [("seek", "stronghold", {}), ("seek", "portal_room", {}), ("activate", "end_portal", {})]}

def from_sources(inv, needs, cost, solver=None, pending=None):
    """For each need with other sources (SOURCES), the cheapest way from this bag (`cheapest`)."""

    extra, steps = dict(pending or {}), []
    for need in needs:
        if need[0] == "tool" or need[0] not in SOURCES:
            continue
        token, n = need[0], int(need[1])
        short = goals.have_remainder(inv, [[token, n]], extra).get(token, 0)
        if short <= 0:
            continue
        chosen, name = cheapest(token, short, lambda: solve_needs(inv, [(token, short)], cost, solver, extra),
                                inv, cost, solver, extra)
        if chosen is not None:
            steps += chosen
            # Counted as on its way under the item it brings (a group like "building" is counted by its items).
            gives = next(src.get("gives", token) for src in SOURCES[token] if src["name"] == name)
            extra[gives] = extra.get(gives, 0) + short
    return steps, extra

def portal_known(mem, dimension):
    """Pure over memory: a portal remembered in `dimension` (a built one or a site) — what a portal step is priced by."""
    return bool(mem.machines(dimension, "portal") or mem.sites(dimension, kinds=["portal"]))

def where_it_lives(steps, cost):
    """Put the way to where a thing lives before the step that gets it (blaze rods: portal, fortress, then collect)."""

    snap, mem = getattr(cost, "snap", None), getattr(cost, "mem", None)
    out = []
    for step in steps:
        for kind, token, detail in LIVES_IN.get((step.kind, step.token), ()):
            if kind == "portal" and snap is not None and getattr(snap, "dimension", None) == token:
                continue
            if kind == "portal" and mem is not None and snap is not None and not portal_known(mem, snap.dimension) \
                    and not any((s.kind, s.token) == ("cast", "nether_portal") for s in out):
                out.append(_action("cast", "nether_portal", cost))     # no portal known here: cast one first
            if kind == "seek" and mem is not None and mem.sites(None, kinds=[token]):
                continue
            if any((s.kind, s.token) == (kind, token) for s in out):
                continue
            out.append(_action(kind, token, cost, **detail))
        out.append(step)
    return out

def _action(kind, token, cost, **detail):
    step = Step(kind, token, 1, detail)
    step.est = cost.estimate(step)
    return step

def decompose(inv, goal, cost, solver=None, pending=None) -> list[Step]:
    """Ordered steps for `goal` from this bag."""

    steps = _decompose(inv, goal, cost, solver, pending)
    missing = [s for s in steps if not skill.handles(s)]
    if missing:
        raise Unplannable(f"no skill provides {missing[0].kind} {missing[0].token}")
    return steps

def _decompose(inv, goal, cost, solver, pending) -> list[Step]:
    template, args = goal["goal"], goal.get("args", {})
    jobs = dict(pending or {})           # what the caller passed: running jobs' outputs (memory.pending_outputs)
    if template in goals.ITEM_GOALS:
        needs = goals.needs(goal, inv)
        taken, pending = from_containers(inv, needs, cost, solver, pending)
        sourced, pending = from_sources(inv, needs, cost, solver, pending)
        mem = getattr(cost, "mem", None)
        then = [_action(k, t, cost, **d) for k, t, d in (THEN.get(args.get("name"), ()) if template == "milestone"
                                                         else ())
                if not (k == "seek" and mem is not None and mem.sites(None, kinds=[t]))]    # already found
        made = solve_needs(inv, needs, cost, solver, pending, jobs, taken)
        return where_it_lives(taken + sourced + made + then, cost)
    if template == "goto":
        return [_action("goto", "pos", cost, pos=list(args["pos"]), range=float(args.get("range", 2)))]
    if template == "road":
        return [_action("goto", "pos", cost, pos=list(args["a"]), range=4.0),
                _action("goto", "pos", cost, pos=list(args["b"]), range=4.0)]
    if template == "build":
        bp = args["bp"]
        if bp != "shelter" and bp not in blueprints.REGISTRY:
            raise Unplannable(f"no blueprint {bp!r} to build")
        materials = blueprints.materials(blueprints.SHELTER if bp == "shelter" else blueprints.REGISTRY[bp])
        if bp == "nether_portal":
            materials = dict(materials, **{"minecraft:flint_and_steel": 1})
        def carry_and_build():
            return (solve_needs(inv, [(t, n) for t, n in materials.items()], cost, solver, pending, jobs)
                    + [_action("build", bp, cost, at=args.get("at"))])
        chosen, _name = cheapest(f"build:{bp}", 1, carry_and_build, inv, cost, solver, pending)
        return where_it_lives(chosen if chosen is not None else carry_and_build(), cost)
    if template == "sleep":
        return _prepared(inv, _action("sleep", "bed", cost), cost, solver, pending)
    if template == "skill":
        return _prepared(inv, _action("skill", args["name"], cost, args=list(args.get("args", []))), cost, solver,
                         pending)
    if template == "effect":
        # any effect a skill provides, by name ("repair:pickaxe" → Step("repair", "pickaxe")); refused when nobody provides it
        kind, _, token = args["effect"].partition(":")
        count = int(args.get("count", 1))
        detail = dict(effect_detail(kind, token or kind, count), **dict(args.get("detail") or {}))
        step = Step(kind, token or kind, count, detail)
        missing = missing_detail(step)
        if missing:
            raise Unplannable(f"effect {args['effect']} needs {missing} in its detail")
        step.est = cost.estimate(step)
        return _prepared(inv, step, cost, solver, pending)
    raise Unplannable(f"no way to decompose a {template!r} goal")

def _prepared(inv, step, cost, solver, pending):
    """The steps that get `step`'s skill needs held for its call, then the step (planner.before's rule for decompose's steps)."""

    needs = knowledge.step_call(step)
    return solve_needs(inv, [tuple(r) for r in knowledge.needs_rows(needs)], cost, solver, pending) + [step]

def to_dict(step: Step) -> dict:
    return {"kind": step.kind, "token": step.token, "count": step.count, "detail": step.detail, "est": step.est}

def from_dict(d) -> Step:
    return Step(d["kind"], d["token"], int(d["count"]), dict(d.get("detail") or {}), int(d.get("est", 0)))

def night_left_s(snap):
    """Seconds of night still ahead, or None when it is not night."""
    t = int(snap.time) % DAY_TICKS
    return (NIGHT_END - t) / TICKS_PER_S if is_night(t, getattr(snap, "dimension", "minecraft:overworld")) else None

def night_facts(soft, cooled=(), dig_site=True, home_walk_s=None, night_left_s=None):
    """The place facts the night's pricing reads: the soft-ground reading (seconds to hand-diggable ground, or None),
    the ways that failed here lately (`cooled`: their names, dropped from the pricing), whether a dig-in can finish
    here (`dig_site`, survive.dig_in_site: False → dig in is not offered), the home bed's walk (`home_walk_s`) and the
    night still ahead (`night_left_s`, a whole night when the clock is not read). Priced in seconds with the night's
    death risk (beliefs risk.*, time.death_cost_s): a walk in the open costs its seconds plus their share of an open
    night's risk; a shelter costs the night waited in it plus a sheltered night's risk."""

    out: dict = {"soft_ground": False} if soft is None or soft is False else \
        {"soft_ground": True, "soft_walk_s": 0.0 if soft is True else float(soft)}
    if not dig_site:
        out["no_dig_site"] = True
    night_s, death_s = beliefs.value("time.night_s"), beliefs.value("time.death_cost_s")
    left = float(night_s if night_left_s is None else night_left_s)
    if home_walk_s is not None:
        open_rate = beliefs.value("risk.night_open") / night_s * death_s     # seconds of risk per second exposed
        out.update(home_bed=True, home_walk_s=float(home_walk_s) * (1.0 + open_rate))
    out["wait_s"] = left + beliefs.value("risk.night_sheltered") * death_s
    if cooled:
        out["cooled"] = sorted(cooled)
    return out


def way_key(way):
    """The retry key a night way's failure cools under (reflexes.Maintain.shelter)."""
    return f"shelter:{way}"


def cooled_ways(ready):
    """Pure given `ready(key)`: the night's ways (SOURCES["overnight"]) cooling after a failure here."""
    return [s["name"] for k in ("overnight bed", "overnight") for s in SOURCES[k] if not ready(way_key(s["name"]))]

"""L2: a goal and a bag in, an ordered list of steps out. The brain calls only this. decompose(inv, goal, cost, solver=None, pending=None) -> [Step] Item goals (have, craft, milestone) go to a SOLVER; the rest become action steps, after whatever materials they need (build). Solvers are registered by name: `planner` (planner.py, recursive descent over the requirement graph) is the default; `solve` (solve.py over the action columns) is tried when it cannot plan, or chosen by name per task. Every solver gets the same cost model (cost.Cost), so their steps are priced in the same ticks. Pure apart from what the cost model reads (one cached /find per kind)."""

import math

from . import blueprints, goals, knowledge, actions as act, skill
from .cost import TICKS_PER_S
from .data import POD_BLOCKS, mid
from .planner import Planner, Step, Unplannable
from .solve import Unsolvable, solve
from .knowledge import members

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
    vector = act.state_of(cost.snap, cost.mem)
    try:
        found = solve(act.table(cost, vector), vector, target)
    except Unsolvable as e:
        raise Unplannable(str(e))
    return [act.to_step(a, n) for a, n in found.steps()]

register("solve", _solve)

def solve_needs(inv, needs, cost, solver=None, pending=None, jobs=None):
    """Steps that make `needs` held. The named solver, else each registered one in turn until one plans. `pending`:
    counted as held (planned sources' and jobs' outputs); `jobs`: of it, what running jobs make (awaited when used)."""
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
            return fn(inv, needs, cost, pending, **({"jobs": jobs} if jobs else {}))
        except Unplannable as e:
            last = e
    raise last or Unplannable("no solver could plan this")

def from_containers(inv, needs, cost, solver=None, pending=None):
    """Take what containers hold (memory.stored) where that is cheaper than making it: (withdraw steps, pending)."""

    mem, snap = getattr(cost, "mem", None), getattr(cost, "snap", None)
    extra = dict(pending or {})
    if mem is None or snap is None or not hasattr(mem, "stored"):
        return [], extra
    steps = []
    for need in needs:
        if need[0] == "tool":
            continue
        token, n = need[0], int(need[1])
        ids = set(members(token))
        on_way = {token: sum(v for k, v in extra.items() if k in ids)}
        short = goals.have_remainder(inv, [[token, n]], on_way).get(token, 0)
        for pos, item, have in sorted(mem.stored(token, snap.dimension), key=lambda r: math.dist(r[0], snap.feet)):
            if short <= 0:
                break
            take = min(short, have)
            step = _action("withdraw", item, cost, pos=list(pos))
            step.count = take
            try:
                make = cost.plan_s(solve_needs(inv, [(token, take)], cost, solver, extra)) * TICKS_PER_S
            except Unplannable:
                make = math.inf
            if step.est < make:
                steps.append(step)
                extra[item] = extra.get(item, 0) + take
                short -= take
    return steps, extra

def effect_detail(kind, token, count):
    """What an effect step's skill reads from `detail`, filled where the tables know it."""

    if kind == "hunt" and token in knowledge.HUNT:
        return {"types": list(knowledge.HUNT[token])}
    if kind == "mine" and mid(token) in knowledge.MINE:
        blocks, tier = knowledge.MINE[mid(token)]
        return {"blocks": list(blocks), "tier": tier}
    if kind == "take" and token in knowledge.TAKEABLE:
        return {"blocks": list(knowledge.TAKEABLE[token]["blocks"])}
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
            try:
                contract.provides[effect](None, probe)
                return None                  # one provider can serve it from what the step carries
            except LookupError as e:
                missing = missing or str(e.args[0])
            except Exception:
                return None                  # it needs the world (ctx) to say: not refusable offline
    return missing

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
    "overnight": [{"name": "dig in", "steps": [("shelter", "dig_in", {})], "yields": 1,
                   "needs": [("tool", "pickaxe", 0)],
                   "unless": ("no_dig_site", "the ground here takes no lid below the ground line")},
                  {"name": "dig in by hand", "steps": [("shelter", "dig_in", {})], "yields": 1, "needs": [],
                   "when": ("soft_ground", "no ground near digs by hand: it needs a pickaxe"),
                   "extra_s": "soft_walk_s"},        # the walk to that ground (survive.soft_spot) is part of it
                  {"name": "wall in", "steps": [("shelter", "pod", {})], "yields": 1, "needs": [("building", POD_BLOCKS)]},
                  # the hut's needs are read from its blueprint (a hand copy named the wrong stone)
                  {"name": "hut", "steps": [("shelter", "hut", {})], "yields": 1,
                   "needs": sorted(blueprints.materials(blueprints.SHELTER).items())}],
}

def cheapest(key, amount, default, inv, cost, solver=None, extra=None, facts=None):
    """The cheapest way to `key` × amount: the solver's steps, or a SOURCES[key] source's runs plus their needs."""

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
        # A source's own extra seconds the place facts say (the walk to soft ground), added to its plan.
        seconds = cost.plan_s(pre + own) + float((facts or {}).get(src.get("extra_s"), 0.0) if src.get("extra_s")
                                                 else 0.0)
        if seconds < best:
            best, best_steps, name = seconds, pre + own, src["name"]
    if best == math.inf:
        raise Unplannable(f"no way to {key}: " + "; ".join(why))
    return best_steps, name

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

def where_it_lives(steps, cost):
    """Put the way to where a thing lives before the step that gets it (blaze rods: portal, fortress, then collect)."""

    snap, mem = getattr(cost, "snap", None), getattr(cost, "mem", None)
    out = []
    for step in steps:
        for kind, token, detail in LIVES_IN.get((step.kind, step.token), ()):
            if kind == "portal" and snap is not None and getattr(snap, "dimension", None) == token:
                continue
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

def decompose(inv, goal, cost, solver=None, pending=None):
    """Ordered steps for `goal` from this bag."""

    steps = _decompose(inv, goal, cost, solver, pending)
    missing = [s for s in steps if not skill.handles(s)]
    if missing:
        raise Unplannable(f"no skill provides {missing[0].kind} {missing[0].token}")
    return steps

def _decompose(inv, goal, cost, solver, pending):
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
        return where_it_lives(taken + sourced + solve_needs(inv, needs, cost, solver, pending, jobs) + then, cost)
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

    needs, _speed = knowledge.step_call(step)
    return solve_needs(inv, [tuple(r) for r in knowledge.needs_rows(needs)], cost, solver, pending) + [step]

def to_dict(step):
    return {"kind": step.kind, "token": step.token, "count": step.count, "detail": step.detail, "est": step.est}

def from_dict(d):
    return Step(d["kind"], d["token"], int(d["count"]), dict(d.get("detail") or {}), int(d.get("est", 0)))

def night_facts(soft, cooled=(), dig_site=True):
    """The place facts the night's pricing reads: the soft-ground reading (seconds to hand-diggable ground, or None),
    the ways that failed here lately (`cooled`: their names, dropped from the pricing), and whether a dig-in can
    finish here (`dig_site`, survive.dig_in_site: False → dig in is not offered)."""

    out = {"soft_ground": False} if soft is None or soft is False else \
        {"soft_ground": True, "soft_walk_s": 0.0 if soft is True else float(soft)}
    if not dig_site:
        out["no_dig_site"] = True
    if cooled:
        out["cooled"] = sorted(cooled)
    return out


def way_key(way):
    """The retry key a night way's failure cools under (reflexes.Maintain.shelter)."""
    return f"shelter:{way}"


def cooled_ways(ready):
    """Pure given `ready(key)`: the night's ways (SOURCES["overnight"]) cooling after a failure here."""
    return [s["name"] for s in SOURCES["overnight"] if not ready(way_key(s["name"]))]

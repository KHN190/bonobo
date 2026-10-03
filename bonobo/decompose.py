"""L2: a goal and a bag in, an ordered list of steps out. The brain calls only this. decompose(inv, goal, cost, pending=None) -> [Step]. Item goals (have, craft, milestone) go to the one planner (planner.plan_needs); the rest become action steps, after whatever materials they need (build). Pure apart from what the cost model reads (one cached /find per kind)."""

import time

from . import beliefs, blueprints, goals, knowledge, skill
from .api import McError
from .cost import TICKS_PER_S
from .data import DAY_TICKS, NIGHT_END, POD_BLOCKS, is_night, mid
from .planner import Step, Unplannable, plan_needs
from .planner import look_first as planner_look_first
from .knowledge import members

def solve_needs(inv, needs, cost, pending=None, jobs=None):
    """Steps that make `needs` held (the one planner). `pending`: counted as held (planned sources' and jobs'
    outputs); `jobs`: of it, what running jobs make (awaited when used)."""
    return plan_needs(inv, needs, cost, pending, jobs)

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

SOURCES = {
    # A night without a bed (needs.overnight): the default is the bed's plan; these are the other ways through it.
    # A night with a bed (needs.overnight asks these first: a shelter is only for a night no bed can end): the home's
    # bed when a walk reaches it (bed_reach: night_facts home_bed / home_walk_s), priced by that walk; a carried bed
    # (or one the default plans) is the default's — the sleep skill gives it a room, light, the gate and takes it back.
    "overnight bed": [{"name": "home", "steps": [("shelter", "home", {})], "needs": [],
                       "when": ("home_bed", "no home bed a walk reaches"), "extra_s": ("home_walk_s",)}],
    # A night no bed can end: a shelter to wait in (no sleep is paired with it).
    "overnight": [{"name": "dig in", "steps": [("shelter", "dig_in", {})],
                   "needs": [("tool", "pickaxe", 0)],
                   "unless": ("no_dig_site", "the ground here takes no lid below the ground line"), "extra_s": ("wait_s",)},
                  {"name": "dig in by hand", "steps": [("shelter", "dig_in", {})], "needs": [],
                   "when": ("soft_ground", "no ground near digs by hand: it needs a pickaxe"),
                   "extra_s": ("soft_walk_s", "wait_s")},   # the walk to that ground (survive.soft_spot), the night
                  {"name": "wall in", "steps": [("shelter", "pod", {})], "needs": [("building", POD_BLOCKS)],
                   "extra_s": ("wait_s",)},
                  # the hut's needs are read from its blueprint (a hand copy named the wrong stone)
                  {"name": "hut", "steps": [("shelter", "hut", {})],
                   "needs": sorted(blueprints.materials(blueprints.SHELTER).items()), "extra_s": ("wait_s",)}],
}

NIGHT_ITSELF = ("wait_s",)        # spent in the night, not before it


def extra_s(keys, facts):
    """Pure: a source's own extra seconds the place facts say (a walk, the night waited)."""
    return sum(float((facts or {}).get(k, 0.0)) for k in keys)


def day_extra_s(keys, facts):
    """Pure: a source's extra seconds spent before dark (a walk), the night itself left out."""
    return extra_s(tuple(k for k in keys if k not in NIGHT_ITSELF), facts)

def free_ways(key, cost, facts=None):
    """[(name, seconds, steps, extra keys)] of SOURCES[key] ways needing nothing first."""
    return [(src["name"], cost.plan_s(own), own, tuple(src.get("extra_s", ())))
            for src, needs, own in offered_sources(key, cost, facts)[0] if not needs]

def offered_sources(key, cost, facts=None):
    """([(source, needs, own steps)], why not) of SOURCES[key] ways offered here."""

    why, out = [], []
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
        own = []
        for kind, tok, detail in src["steps"]:
            step = Step(kind, tok, 1, dict(detail))
            step.est = cost.estimate(Step(kind, tok, 1, dict(detail)))
            own.append(step)
        out.append((src, list(src["needs"]), own))
    return out, why

def _action(kind, token, cost, **detail):
    step = Step(kind, token, 1, detail)
    step.est = cost.estimate(step)
    return step

def decompose(inv, goal, cost, pending=None) -> list[Step]:
    """Ordered steps for `goal` from this bag."""

    steps = _decompose(inv, goal, cost, pending)
    missing = [s for s in steps if not skill.handles(s)]
    if missing:
        raise Unplannable(f"no skill provides {missing[0].kind} {missing[0].token}")
    return steps

# Milestones that end in doing, not holding: after their items, the fact their last step leaves.
THEN = {"end portal": ("state:end_portal_open", True)}

def _decompose(inv, goal, cost, pending) -> list[Step]:
    template, args = goal["goal"], goal.get("args", {})
    jobs = dict(pending or {})           # what the caller passed: running jobs' outputs (memory.pending_outputs)
    if template in goals.ITEM_GOALS:
        look = planner_look_first(inv, goals.needs(goal, inv), cost, pending)
        if look:
            return look
    if template in ("goto", "road"):
        return [need[1] for need in round_needs(goal, inv, cost) if isinstance(need[1], Step)]
    return solve_needs(inv, round_needs(goal, inv, cost), cost, pending, jobs)

def round_needs(goal, inv, cost):
    """`goal` as the planner's needs."""
    template, args = goal["goal"], goal.get("args", {})
    if template in goals.ITEM_GOALS:
        then = THEN.get(args.get("name")) if template == "milestone" else None
        return list(goals.needs(goal, inv)) + ([("fact",) + then] if then else [])
    if template == "goto":
        return [("do", _action("goto", "pos", cost, pos=list(args["pos"]), range=float(args.get("range", 2))))]
    if template == "road":
        return [("do", _action("goto", "pos", cost, pos=list(args["a"]), range=4.0)),
                ("do", _action("goto", "pos", cost, pos=list(args["b"]), range=4.0))]
    if template == "build":
        bp = args["bp"]
        if bp != "shelter" and bp not in blueprints.REGISTRY:
            raise Unplannable(f"no blueprint {bp!r} to build")
        if bp == "nether_portal":
            return [("fact", "portal", True)]
        return [("do", Step("build", bp, 1, {"at": args.get("at")}))]
    if template == "sleep":
        return [("do", Step("sleep", "bed", 1, {}))]
    if template == "skill":
        return [("do", Step("skill", args["name"], 1, {"args": list(args.get("args", []))}))]
    if template == "effect":
        # any effect a skill provides, by name ("repair:pickaxe" → Step("repair", "pickaxe")); refused when nobody provides it
        kind, _, token = args["effect"].partition(":")
        count = int(args.get("count", 1))
        detail = dict(effect_detail(kind, token or kind, count), **dict(args.get("detail") or {}))
        step = Step(kind, token or kind, count, detail)
        missing = missing_detail(step)
        if missing:
            raise Unplannable(f"effect {args['effect']} needs {missing} in its detail")
        return [("do", step)]
    raise Unplannable(f"no way to decompose a {template!r} goal")

def night_left_s(snap):
    """Seconds of night still ahead: (NIGHT_END − timeOfDay) / 20 at night, a whole night before dusk (None)."""
    t = int(snap.time) % DAY_TICKS
    return (NIGHT_END - t) / TICKS_PER_S if is_night(t, getattr(snap, "dimension", "minecraft:overworld")) else None

def night_facts(soft, cooled=(), dig_site=True, home_walk_s=None, night_left_s=None, covered_work_s=0.0):
    """The place facts the night's pricing reads: the soft-ground reading (seconds to hand-diggable ground, or None),
    the ways that failed here lately (`cooled`: their names, dropped from the pricing), whether a dig-in can finish
    here (`dig_site`, survive.dig_in_site: False → dig in is not offered), the home bed's walk (`home_walk_s`) and the
    night still ahead (`night_left_s`, a whole night when the clock is not read). Priced in seconds with the night's
    death risk (beliefs risk.*, time.death_cost_s): a walk in the open costs its seconds plus their share of an open
    night's risk; a shelter costs the night waited in it — the part no work under cover fills (`covered_work_s`:
    the plan's night work, F1i) — plus a sheltered night's risk."""

    out: dict = {"soft_ground": False} if soft is None or soft is False else \
        {"soft_ground": True, "soft_walk_s": 0.0 if soft is True else float(soft)}
    if not dig_site:
        out["no_dig_site"] = True
    night_s, death_s = beliefs.value("time.night_s"), beliefs.value("time.death_cost_s")
    left = float(night_s if night_left_s is None else night_left_s)
    if home_walk_s is not None:
        open_rate = beliefs.value("risk.night_open") / night_s * death_s     # seconds of risk per second exposed
        out.update(home_bed=True, home_walk_s=float(home_walk_s) * (1.0 + open_rate))
    out["wait_s"] = max(0.0, left - float(covered_work_s)) + beliefs.value("risk.night_sheltered") * death_s
    if cooled:
        out["cooled"] = sorted(cooled)
    return out


def way_key(way):
    """The retry key a night way's failure cools under (reflexes.Maintain.shelter)."""
    return f"shelter:{way}"


def cooled_ways(ready):
    """Pure given `ready(key)`: the night's ways (SOURCES["overnight"]) cooling after a failure here."""
    return [s["name"] for k in ("overnight bed", "overnight") for s in SOURCES[k] if not ready(way_key(s["name"]))]

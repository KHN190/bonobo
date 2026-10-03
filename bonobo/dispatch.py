"""One plan step, carried out: the skill that provides it (`skill.provider`), and where to look when nothing is in range. No switch on kinds here — a new skill is one decorated function. Everything it needs comes on the Context."""

import json
import math
import os
import time

from . import api, explore, gather, lifecycle, nav, paths, retry, skillcore
from . import world
from . import skill as skillkit
from .api import GameUnreachable, McError, NotAvailable, log, swallowed
from .data import TICKS_PER_S, bare, mid, seen_class
from .knowledge import FIND_AT, PRICE_SOURCE, PRIOR_TICKS, find_class, step_kinds

SEEK_KINDS = ("mine", "gather", "hunt", "fill")      # steps whose "nothing in range" is answered by looking elsewhere

PRICES = paths.data("prices.jsonl", env="MC_PRICES")
PER_UNIT = {"smelt": "smelt_each", "mine": "mine_each", "gather": "gather_each", "hunt": "hunt_each"}
# where each estimate part's price comes from (an item of knowledge.PRICE_SOURCE or a play.toml key)
PART_SOURCE = {"walk": "data.WALK_BLOCKS_PER_TICK", "dig": "data.HARDNESS", "surface": "PRIOR_TICKS.surface",
               "wait": "knowledge.NIGHT_S", "hunger": "risk.food_drain_s", "chance": "policy"}


def price_source(item):
    """Pure: an item's source tag (knowledge.PRICE_SOURCE; a play.toml key is a prior)."""
    table, _, key = item.partition(".")
    if table == "PRIOR_TICKS":
        return PRICE_SOURCE["knowledge.PRIOR_TICKS"].get(key, "prior")
    if item in PRICE_SOURCE:
        return PRICE_SOURCE[item] if isinstance(PRICE_SOURCE[item], str) else "prior"
    head, _, sub = item.rpartition(".")
    got = PRICE_SOURCE.get(head)
    return got.get(sub, "prior") if isinstance(got, dict) else "policy" if item == "policy" else "prior"


def work_item(step):
    """Pure: the price item a step's own work came from."""
    if step.kind == "await":
        return "knowledge.GROW_S.crop" if "wheat" in step.token else "knowledge.GROW_S.animal"
    if step.kind == "seek":
        return seek_item(step)
    return f"PRIOR_TICKS.{PER_UNIT.get(step.kind, step.kind if step.kind in PRIOR_TICKS else 'skill')}"


def seek_item(step):
    """Pure: the price item a search for the step's source rests on (knowledge.find_class of its first kind)."""
    return find_class((step_kinds(step) or ["other"])[0])[1]


def price_line(step, night, dimension, actual_s, why=None, row=None, phases=None):
    """Pure: one step as priced and as run (E4's sample): its estimate by part (Cost.estimate's), each part's source,
    the seconds it took and, when measured, by phase (`phases`: walk, seek, arrived — the work is the rest; game_s:
    the game's own seconds), what it was, the conditions."""
    detail = {k: step.detail[k] for k in ("breaks", "kills", "p", "pos", "blocks", "types") if k in step.detail}
    est = int(getattr(step, "est", 0) or 0)
    parts = {k: v for k, v in (getattr(step, "parts", None) or {}).items() if v}
    if parts and est > sum(parts.values()):
        parts["hunger"] = est - sum(parts.values())       # planner.price_as_run's hunger share
    items = {"work": work_item(step), "seek": seek_item(step), **PART_SOURCE}
    price = {p: f"{items[p]}:{price_source(items[p])}" for p in ("work", *parts) if p in items}
    out = {"t": round(time.time(), 1), "row": row, "kind": step.kind, "token": step.token, "count": step.count,
           "est": est, "est_parts": parts, "actual_s": round(actual_s, 2), "ok": why is None, "why": why,
           "price": price, "cond": {"night": bool(night), "dim": dimension, **detail}}
    if phases:
        walk, seek = round(phases.get("walk", 0.0), 2), round(phases.get("seek", 0.0), 2)
        out["actual_parts"] = {"walk": walk, "seek": seek, "work": round(max(0.0, actual_s - walk - seek), 2)}
        out.update({k: round(phases[k], 2) for k in ("arrived_s", "game_s", "path_m", "straight_m", "bar_drop")
                    if phases.get(k) is not None})
        if phases.get("ticks"):
            out["ticks"] = phases["ticks"]
    return out


def trace(step, night, dimension, t0, why=None, phases=None):
    """The step's price line appended to prices.jsonl (MC_DATA); a bench row names itself (MC_BENCH_ROW)."""
    line = price_line(step, night, dimension, time.time() - t0, why, os.environ.get("MC_BENCH_ROW"), phases)
    line["cond"]["tick_rate"] = float(os.environ.get("MC_BENCH_TICK_RATE") or TICKS_PER_S)     # a bench row's fast clock
    try:
        with open(PRICES, "a") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
    except OSError as e:
        swallowed("dispatch.trace", e)


PHASES: dict = {}       # the step running now: seconds spent seeking (go_find), what nav had walked when it began
lifecycle.in_place(__name__, "PHASES")


def _game_tick():
    """The game's clock, read only on a bench row (MC_BENCH_ROW): the game seconds a step under a fast clock took."""
    if not os.environ.get("MC_BENCH_ROW"):
        return None
    try:
        return api.get("/state").get("gameTime")
    except McError as e:
        swallowed("dispatch._game_tick", e)
        return None


def step_moved(start, now):
    """Pure: what the body did over a step from the /state reads (api.STATE at its start, `start`, and `now`): the
    path walked (every move, a skill's own included), the straight line from where it began to where it is, the drop
    of food plus saturation."""
    out = {"path_m": now.moved_m - start.get("moved", now.moved_m)}
    if start.get("feet") is not None and now.feet_seen is not None:
        out["straight_m"] = math.dist(start["feet"], now.feet_seen)
    if start.get("bar") is not None and now.bar_seen is not None:
        out["bar_drop"] = sum(start["bar"]) - sum(now.bar_seen)
    return out


def run_priced(dimension, step, night, run, budget=True):
    """`run()` carried out as `step`, its price line written however it ends: every way a step is run (a task's step,
    a reflex's shelter, a bench row's) goes through here, so each is priced and timed the same way. `budget`: the
    step held to its overrun budget (api.step_budget) — off for a reflex's (S1/S7)."""
    t0, g0 = time.time(), _game_tick()
    PHASES.clear()
    PHASES.update(ticks_after=api.ticks_mark())
    PHASES.update(seek=0.0, walked=nav.WALKED["s"], arrived=nav.WALKED["arrived"], moved=api.STATE.moved_m,
                  feet=api.STATE.feet_seen, bar=api.STATE.bar_seen)

    def phases():
        g1 = _game_tick() if g0 is not None else None
        arrived = nav.WALKED["arrived"] if nav.WALKED["arrived"] != PHASES.get("arrived") else None
        return {"walk": max(0.0, nav.WALKED["s"] - PHASES.get("walked", 0.0) - PHASES.get("seek_walk", 0.0)),
                "seek": PHASES.get("seek", 0.0), "arrived_s": None if arrived is None else arrived - t0,
                "game_s": None if g1 is None else (g1 - g0) / TICKS_PER_S, **step_moved(PHASES, api.STATE),
                "ticks": api.ticks_since(api.STATE.ticks, PHASES["ticks_after"])}
    try:
        with api.step_budget(step.est if budget else None):      # OVERRUN × its as-run price, every try
            out = run()
    except GameUnreachable:
        raise
    except api.INTERRUPTIONS as e:
        trace(step, night, dimension, t0, f"interrupted: {type(e).__name__}", phases())
        raise
    except (McError, api.ToolMissing) as e:
        trace(step, night, dimension, t0, f"failed: {type(e).__name__}", phases())
        raise
    trace(step, night, dimension, t0, None, phases())
    return out


def execute(ctx, step, night):
    log(f"   → {step} at {api.feet_seen()}")          # where the pick was made: the cooling place, proven
    key = f"{step.kind}:{step.token}"

    def run():
        out = run_step(ctx, step, night)
        if isinstance(out, NotAvailable):
            s0, w0 = time.time(), nav.WALKED["s"]
            try:
                found = go_find(ctx, step)
            finally:
                PHASES["seek"] = PHASES.get("seek", 0.0) + time.time() - s0
                PHASES["seek_walk"] = PHASES.get("seek_walk", 0.0) + nav.WALKED["s"] - w0
            if not found:
                raise out
            out = run_step(ctx, step, night, seek=False)
        return out
    try:
        out = run_priced(ctx.dimension, step, night, run)
    except GameUnreachable:
        raise
    except api.Overrun as e:
        # the one writer of a refuted price (Cost reads it): the step's measured rest at its target, while the
        # state it was measured in holds (E5); no outcome counted — an interruption, re-planned, nothing failed.
        # floored by what the step already spent plus its own price — a re-estimate can't undercut sunk cost
        price_s = max(e.remaining_s, (e.spent or 0.0) + (getattr(step, "est", 0) or 0) / TICKS_PER_S)
        ctx.mem.refute(step, e.pos, price_s,
                       skillcore.ban_state(api.STATE.feet_seen, api.STATE.kinds_seen))
        raise
    except api.INTERRUPTIONS:
        raise      # no statistics
    except (McError, api.ToolMissing) as e:
        ctx.mem.record_outcome(f"nav:{step.kind}" if retry.cause_of(e) == "nav" else key, False)
        raise
    if not (isinstance(out, dict) and "ordered" in out):
        ctx.mem.record_outcome(key, True)   # a furnace loaded is not a step done: that is when it is held

def runner_for(ctx, step) -> tuple | None:
    """(runner, args) of the skill that carries out `step` here, or None when no registered skill can."""
    if step.kind == "skill":
        contract = skillkit.REGISTRY.get(step.token)
        return None if contract is None else (contract.runner, tuple(step.detail.get("args", ())))
    return skillkit.provider(ctx, step)

def can_start(ctx, step, bag):
    """Would the skill for `step` pass its own preconditions now, its needs held in `bag` (the round's)? Asked before
    the step is offered."""

    found = runner_for(ctx, step)
    if found is None:
        return False
    runner, args = found
    return skillkit.can_run(runner, ctx, *args, held_bag=bag)[0]

def still_there(blocks, spot):
    """Is one of `blocks` at the noted `spot`?"""

    from .world import Region
    try:
        name = bare(Region(spot, spot).name(tuple(spot)))
    except McError as e:
        swallowed("dispatch.still_there", e)
        return False
    return name in {bare(b) for b in blocks}

def run_step(ctx, step, night, seek=True):
    """Carry out one step with the skill that provides it."""

    ctx.night = night
    found = runner_for(ctx, step)
    if found is None:
        raise McError(f"no skill provides {step.kind} {step.token}")
    runner, args = found
    try:
        return runner(ctx, *args)
    except NotAvailable as e:
        if seek and step.kind in SEEK_KINDS and not isinstance(e, api.NavFailed):
            return e
        raise

def first_sought(steps):
    """Pure: the plan's first step whose source a seek looks for (SEEK_KINDS), or None."""
    return next((st for st in steps if st.kind in SEEK_KINDS), None)


def go_find(ctx, step):
    """Where to look when nothing is in range: memory's sightings, then the kind's richest depth, then a spiral."""

    here, dim, mem = world.feet(), ctx.dimension, ctx.mem
    blocks = list(step.detail.get("blocks", ()))
    names = {"gather": ["tree"], "mine": blocks, "hunt": list(step.detail.get("types", ())), "fill": ["water"]}.get(step.kind, [])
    notes = sorted(((r["kind"], tuple(r["pos"])) for n in names for r in mem.seen(n, dim)),
                   key=lambda kp: math.dist(kp[1], here))
    tried = 0
    for kind, spot in notes:
        if tried >= 2:
            break
        if ctx.blocked(spot) or math.dist(spot, here) <= 4:
            continue
        tried += 1
        if not nav.arrived_near(spot, ctx.policy, range_=4 if seen_class(kind) != "mobile" else 8):
            ctx.ban(spot)
            continue
        if seen_class(kind) == "mobile" or mem.confirm(kind, spot, dim, still_there(mem.blocks_of(kind), spot)):
            return True
    token = "log" if step.kind == "gather" else ("food" if step.kind == "hunt" else mid(step.token))
    depth = FIND_AT.get(token)
    if depth is not None and abs(here[1] - depth) > 6 and dim == "minecraft:overworld":
        try:
            return nav.arrive((here[0], depth, here[2]), ctx.policy, range_=3)
        except api.NavFailed as e:
            swallowed("dispatch.go_find", e)
    if step.kind == "mine" and depth is not None and dim == "minecraft:overworld":
        try:                                    # at the richest depth and nothing in sight: tunnel to reveal ore
            gather.strip_mine_step(ctx)
            return True
        except NotAvailable as e:
            swallowed("dispatch.go_find", e)
    try:           # a search that found nothing fails its verify: that is "nowhere new", not a crash
        if step.kind == "hunt":
            return bool(explore.explore_for(ctx, list(step.detail["types"])))
        return bool(explore.seek_blocks(ctx, step_kinds(step)))
    except api.INTERRUPTIONS:
        raise
    except McError as e:
        swallowed("dispatch.go_find", e)
        return False

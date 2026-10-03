"""One plan step, carried out: the skill that provides it (`skill.provider`), and where to look when nothing is in range. No switch on kinds here — a new skill is one decorated function. Everything it needs comes on the Context."""

import json
import math
import os
import time

from . import api, explore, gather, nav, paths, retry, skillcore
from . import world
from . import skill as skillkit
from .api import GameUnreachable, McError, NotAvailable, log, swallowed
from .data import GROUPS, TICKS_PER_S, bare, mid, seen_class
from .knowledge import FIND_AT, PRICE_SOURCE, PRIOR_TICKS

SEEK_KINDS = ("mine", "gather", "hunt")      # steps whose "nothing in range" is answered by looking elsewhere

PRICES = paths.data("prices.jsonl", env="MC_PRICES")
PER_UNIT = {"smelt": "smelt_each", "mine": "mine_each", "gather": "gather_each", "hunt": "hunt_each"}


def price_line(step, night, dimension, actual_s, why=None, row=None):
    """Pure: one step as priced and as run (E4's sample): its estimate, the seconds it took, what it was, the
    conditions, and where its work's price came from (knowledge.PRICE_SOURCE)."""
    prior = PER_UNIT.get(step.kind, step.kind if step.kind in PRIOR_TICKS else "skill")
    detail = {k: step.detail[k] for k in ("breaks", "kills", "p", "pos", "blocks", "types") if k in step.detail}
    return {"t": round(time.time(), 1), "row": row, "kind": step.kind, "token": step.token, "count": step.count,
            "est": int(getattr(step, "est", 0) or 0), "actual_s": round(actual_s, 2), "ok": why is None, "why": why,
            "price": {"work": f"PRIOR_TICKS.{prior}:{PRICE_SOURCE['knowledge.PRIOR_TICKS'].get(prior, 'prior')}"},
            "cond": {"night": bool(night), "dim": dimension, **detail}}


def trace(step, night, dimension, t0, why=None):
    """The step's price line appended to prices.jsonl (MC_DATA); a bench row names itself (MC_BENCH_ROW)."""
    line = price_line(step, night, dimension, time.time() - t0, why, os.environ.get("MC_BENCH_ROW"))
    line["cond"]["tick_rate"] = float(os.environ.get("MC_BENCH_TICK_RATE") or TICKS_PER_S)     # a bench row's fast clock
    try:
        with open(PRICES, "a") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
    except OSError as e:
        swallowed("dispatch.trace", e)


def execute(ctx, step, night):
    log(f"   → {step} at {api.feet_seen()}")          # where the pick was made: the cooling place, proven
    key = f"{step.kind}:{step.token}"
    t0 = time.time()
    try:
        out = run_step(ctx, step, night)
        if isinstance(out, NotAvailable):
            if not go_find(ctx, step):
                raise out
            out = run_step(ctx, step, night, seek=False)
    except GameUnreachable:
        raise
    except api.INTERRUPTIONS as e:
        trace(step, night, ctx.dimension, t0, f"interrupted: {type(e).__name__}")
        raise      # no statistics
    except (McError, skillcore.ToolMissing) as e:
        trace(step, night, ctx.dimension, t0, f"failed: {type(e).__name__}")
        ctx.mem.record_outcome(f"nav:{step.kind}" if retry.cause_of(e) == "nav" else key, False)
        raise
    trace(step, night, ctx.dimension, t0)
    if not (isinstance(out, dict) and "ordered" in out):
        ctx.mem.record_outcome(key, True)   # a furnace loaded is not a step done: that is when it is held

def runner_for(ctx, step) -> tuple | None:
    """(runner, args) of the skill that carries out `step` here, or None when no registered skill can."""
    if step.kind == "skill":
        contract = skillkit.REGISTRY.get(step.token)
        return None if contract is None else (contract.runner, tuple(step.detail.get("args", ())))
    return skillkit.provider(ctx, step)

def can_start(ctx, step):
    """Would the skill for `step` pass its own preconditions now? Asked before the step is offered."""

    found = runner_for(ctx, step)
    if found is None:
        return False
    runner, args = found
    return skillkit.can_run(runner, ctx, *args)[0]

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

def go_find(ctx, step):
    """Where to look when nothing is in range: memory's sightings, then the kind's richest depth, then a spiral."""

    here, dim, mem = world.feet(), ctx.dimension, ctx.mem
    blocks = list(step.detail.get("blocks", ()))
    names = {"gather": ["tree"], "mine": blocks, "hunt": list(step.detail.get("types", ()))}.get(step.kind, [])
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
        return bool(explore.seek_blocks(ctx, GROUPS["log"] if step.kind == "gather" else blocks))
    except api.INTERRUPTIONS:
        raise
    except McError as e:
        swallowed("dispatch.go_find", e)
        return False

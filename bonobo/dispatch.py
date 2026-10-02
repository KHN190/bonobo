"""One plan step, carried out: the skill that provides it (`skill.provider`), and where to look when nothing is in range. No switch on kinds here — a new skill is one decorated function. Everything it needs comes on the Context."""

import math

from . import api, explore, gather, nav, retry, skillcore
from . import world
from . import skill as skillkit
from .api import GameUnreachable, McError, NotAvailable, log, swallowed
from .data import GROUPS, bare, mid, seen_class
from .knowledge import FIND_AT

SEEK_KINDS = ("mine", "gather", "hunt")      # steps whose "nothing in range" is answered by looking elsewhere

def execute(ctx, step, night):
    log(f"   → {step} at {api.feet_seen()}")          # where the pick was made: the cooling place, proven
    key = f"{step.kind}:{step.token}"
    try:
        out = run_step(ctx, step, night)
        if isinstance(out, NotAvailable):
            if not go_find(ctx, step):
                raise out
            out = run_step(ctx, step, night, seek=False)
    except GameUnreachable:
        raise
    except api.INTERRUPTIONS:
        raise      # no statistics
    except (McError, skillcore.ToolMissing) as e:
        ctx.mem.record_outcome(f"nav:{step.kind}" if retry.cause_of(e) == "nav" else key, False)
        raise
    if not (isinstance(out, dict) and "ordered" in out):
        ctx.mem.record_outcome(key, True)   # a furnace loaded is not a step done: that is when it is held

def runner_for(ctx, step):
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

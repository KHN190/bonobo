"""One plan step, carried out: the skill that provides it (`skill.provider`), and where to look when nothing is in
range. No switch on kinds here — a new skill is one decorated function. Everything it needs comes on the Context."""
import math

from . import api, nav, retry, skills
from . import skill as skillkit
from .api import GameUnreachable, McError, NotAvailable, log
from .data import GROUPS, RARE_SIGHTINGS, bare, mid
from .knowledge import FIND_AT
from .world import find

SEEK_KINDS = ("mine", "gather", "hunt")      # steps whose "nothing in range" is answered by looking elsewhere


def execute(ctx, step, night):
    log(f"   → {step}")
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
    except (McError, skills.ToolMissing) as e:
        ctx.mem.record_outcome(f"nav:{step.kind}" if retry.cause_of(e) == "nav" else key, False)
        raise
    if not (isinstance(out, dict) and "ordered" in out):
        ctx.mem.record_outcome(key, True)   # a furnace loaded is not a step done: that is when it is held


def run_step(ctx, step, night, seek=True):
    """Carry out one step with the skill that provides it. A seeking step that finds nothing in range returns the
    NotAvailable instead of raising it, so `execute` can look elsewhere first."""
    ctx.night = night
    if step.kind == "skill":
        if step.token not in skillkit.REGISTRY:
            raise McError(f"{step.token} is not a registered skill")
        runner, args = skillkit.REGISTRY[step.token].runner, tuple(step.detail.get("args", ()))
    else:
        found = skillkit.provider(ctx, step)
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
    """Where to look when nothing is in range, in a fixed order: a rare block seen before (data.RARE_SIGHTINGS,
    checked on arrival), what memory says is there, the depth the kind is richest at (knowledge.FIND_AT), a spiral.
    Half-done work is not remembered: the search from wherever the body is (/find sees 48 blocks) finds it again.
    True when the body got somewhere new to look."""
    here, dim, mem = nav.feet_now(), ctx.dimension, ctx.mem
    blocks = list(step.detail.get("blocks", ()))
    rare = sorted(((bare(b), tuple(x["pos"])) for b in blocks if bare(b) in RARE_SIGHTINGS
                   for x in mem.sightings(bare(b), dim)), key=lambda kp: math.dist(kp[1], here))
    for kind, spot in rare[:2]:
        if ctx.blocked(spot):
            continue
        if not nav.arrived(spot, ctx.policy, range_=4):
            ctx.ban(spot)
            continue
        if mem.confirm(kind, spot, dim, bool(find([kind], radius=8, limit=1))):
            return True
    names = {"gather": ["tree"], "mine": blocks, "hunt": list(step.detail.get("types", ()))}.get(step.kind, [])
    spots = [tuple(p) for n in names for p in mem.resources(bare(n), dim)]
    spots += [tuple(x["pos"]) for n in names if bare(n) not in RARE_SIGHTINGS for x in mem.sightings(n, dim)]
    spots = sorted((p for p in spots if not ctx.blocked(p) and math.dist(p, here) > 4),
                   key=lambda p: math.dist(p, here))
    for spot in spots[:2]:
        if nav.arrived(spot, ctx.policy, range_=4):
            return True
        ctx.ban(spot)
    token = "log" if step.kind == "gather" else ("food" if step.kind == "hunt" else mid(step.token))
    depth = FIND_AT.get(token)
    if depth is not None and abs(here[1] - depth) > 6 and dim == "minecraft:overworld":
        try:
            return nav.arrive((here[0], depth, here[2]), ctx.policy, range_=3)
        except api.NavFailed:
            pass
    if step.kind == "hunt":
        return bool(skills.explore_for(ctx, list(step.detail["types"])))
    return bool(skills.seek_blocks(ctx, GROUPS["log"] if step.kind == "gather" else blocks))

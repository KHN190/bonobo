"""One model for everything that runs on its own after it is started: a furnace smelting, crops growing, a sapling turning into a tree, animals' breeding cooldown. A job is {id, kind, pos, dimension, item, count, ready_at, ...} in memory; the upkeep table collects the nearest ready one (`upkeep` "collect job"), and `collect` dispatches by kind. New long waits are new kinds here — never blocking loops."""

import time

from .api import NotAvailable, log

# seconds each kind takes; furnace jobs compute theirs from the item count
DURATION = {"crop": 15 * 60, "sapling": 20 * 60, "breed": 5 * 60}
COLLECT = {}

def start(mem, kind, pos, dimension, item=None, count=0, seconds=None, **extra):
    """Record a job; `seconds` defaults to the kind's typical duration."""
    ready = time.time() + (seconds if seconds is not None else DURATION.get(kind, 600))
    job = mem.add_job(kind, pos, dimension, item, count, ready, extra.pop("carried", False))
    if extra:
        for j in mem.data["jobs"]:
            if j["id"] == job["id"]:
                j.update(extra)
        mem.save()
    return job

def collect(ctx, job):
    """Finish a ready job by kind. Kinds without a pickup (breeding cooldown) just expire."""
    from . import craft
    kind = job["kind"]
    if kind == "furnace":
        return craft.collect_job(ctx, job)
    if kind in COLLECT:
        return COLLECT[kind](ctx, job)
    if kind == "breed":
        ctx.mem.finish_job(job["id"])
        log(f"animals at {tuple(job['pos'])} can breed again")
        return None
    ctx.mem.finish_job(job["id"])
    raise NotAvailable(f"unknown job kind {kind}; dropped")

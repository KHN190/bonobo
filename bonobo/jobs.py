"""One model for everything that runs on its own after it is started: a furnace smelting, crops growing, a sapling
turning into a tree, animals' breeding cooldown. A job is {id, kind, pos, dimension, item, count, ready_at, ...}
in memory; the priority pool offers each ready job as a candidate (with a proximity bonus), and `collect` dispatches
by kind. New long waits are new kinds here — never blocking loops."""
import time

from .api import NotAvailable, log

# Base value of collecting each kind (the pool multiplies by proximity and divides by the trip).
JOB_VALUE = {"furnace": 4, "crop": 3.5, "sapling": 2, "breed": 2.5}
# How long each kind takes when started (seconds); furnace jobs compute theirs from the item count.
DURATION = {"crop": 15 * 60, "sapling": 20 * 60, "breed": 5 * 60}


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
    from . import farming, skills
    kind = job["kind"]
    if kind == "furnace":
        return skills.collect_job(ctx, job)
    if kind == "crop":
        return farming.harvest(ctx, job)
    if kind == "sapling":
        return farming.check_sapling(ctx, job)
    if kind == "breed":
        ctx.mem.finish_job(job["id"])
        log(f"animals at {tuple(job['pos'])} can breed again")
        return None
    ctx.mem.finish_job(job["id"])
    raise NotAvailable(f"unknown job kind {kind}; dropped")

"""A background job in memory near the feet (memory.jobs: a furnace smelting): still cooking, or due
(world.job_ready: the collect row and the plan's await read it)."""
from bonobo.world import job_ready

NAME = "job"
VALUES = ("none", "cooking", "due")
FURNACE = (6, 64, -6)               # near the feet (reflexes.JOB_RANGE), outside the home box (not one of its stations)
LATER_S = 10 ** 9                   # ready long after any round


def domain():
    return VALUES


def alpha(a):
    jobs = a.mem.jobs(a.snap.dimension)
    if not jobs:
        return "none"
    return "due" if any(job_ready(j, a.snap.state.get("gameTime")) for j in jobs) else "cooking"


def gamma(value, f, g):
    if value == "none":
        return
    import time
    ready_at = 0.0 if value == "due" else time.time() + LATER_S
    g.blocks[FURNACE] = "furnace"
    g.mem.add_job("furnace", FURNACE, f["dimension"], "minecraft:cooked_beef", 4, ready_at, False)


def step(facts, d, ctx):
    """Collecting the job (reflexes' "collect job") takes it out of memory."""
    return {NAME: "none"} if "collect job" in (d.name or "").lower() else {}

"""Background work in memory near the feet: a furnace job still cooking, or due (world.job_ready: the collect row
and the plan's await read it); a crop job growing (memory.jobs kind crop: cost.ripe, the await); an auto smelter
whose loaded order is due (memory.machines pending: craft.pending_ready, the collect-machine row)."""
import time

from bonobo.craft import pending_ready
from bonobo.world import job_ready

NAME = "job"
VALUES = ("none", "cooking", "due", "growing", "machine_due")
FURNACE = (6, 64, -6)               # near the feet (reflexes.JOB_RANGE), outside the home box (not one of its stations)
FIELD = (-6, 64, -6)                # the plot the crop job names
SMELTER = (-8, 64, 8)               # the machine's origin
SMELTER_BLUEPRINT, SMELTER_TAGS = "auto_smelter", ("smelting",)
LATER_S = 10 ** 9                   # ready long after any round


def domain():
    return VALUES


def alpha(a):
    if any(pending_ready(m) for m in a.mem.machines(a.snap.dimension)):
        return "machine_due"
    jobs = a.mem.jobs(a.snap.dimension)
    if not jobs:
        return "none"
    tick = a.snap.state.get("gameTime")
    if any(j["kind"] == "crop" for j in jobs):
        return "growing"
    return "due" if any(job_ready(j, tick) for j in jobs) else "cooking"


def gamma(value, f, g):
    if value == "none":
        return
    dim = f["dimension"]
    if value == "machine_due":
        name = g.mem.add_machine(SMELTER_BLUEPRINT, SMELTER, 0, dim, SMELTER_TAGS)
        g.mem.add_pending(name, "minecraft:iron_ingot", 8, 0.0)
    elif value == "growing":
        g.mem.add_job("crop", FIELD, dim, "minecraft:wheat", 3, time.time() + LATER_S, False)
    else:
        g.blocks[FURNACE] = "furnace"
        g.mem.add_job("furnace", FURNACE, dim, "minecraft:cooked_beef", 4,
                      0.0 if value == "due" else time.time() + LATER_S, False)


def step(facts, d, ctx):
    """Collecting the job or the machine's output (reflexes' "collect job" / "collect machine") clears it."""
    name = (d.name or "").lower()
    return {NAME: "none"} if "collect job" in name or "collect machine" in name else {}

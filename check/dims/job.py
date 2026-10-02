"""Background work in memory (one fact: memory.jobs and memory.machines): none; a furnace job still running — meat
cooking (food) or iron smelting (iron): memory.pending_outputs, needs.food_on_its_way, the planner's awaited outputs;
a furnace job due (world.job_ready: the collect row); a crop job growing (kind crop: cost.ripe, the await); an auto
smelter whose loaded order is due (craft.pending_ready: the collect-machine row)."""
import time

from bonobo.craft import pending_ready
from bonobo.world import job_ready

NAME = "job"
VALUES = ("none", "food", "iron", "due", "growing", "machine_due")
FURNACE = (20, 64, -20)      # out of the home and past actions.STATION_R: the base `station` fact stays its own
FIELD = (-6, 64, -6)                # the plot the crop job names
SMELTER = (-8, 64, 8)               # the machine's origin
SMELTER_BLUEPRINT, SMELTER_TAGS = "auto_smelter", ("smelting",)
JOB = {"food": ("minecraft:cooked_beef", 4), "iron": ("minecraft:iron_ingot", 3), "due": ("minecraft:cooked_beef", 4)}
LATER_S = 10 ** 9                   # ready long after any round


def domain():
    return VALUES


def alpha(a):
    if any(pending_ready(m) for m in a.mem.machines(a.snap.dimension)):
        return "machine_due"
    jobs = a.mem.jobs(a.snap.dimension)
    if any(j["kind"] == "crop" for j in jobs):
        return "growing"
    if any(job_ready(j, a.snap.state.get("gameTime")) for j in jobs):
        return "due"
    out = a.mem.pending_outputs(a.snap.dimension)
    return next((k for k in ("food", "iron") if out.get(JOB[k][0])), "none")


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
        item, n = JOB[value]
        g.blocks[FURNACE] = "furnace"
        g.mem.add_job("furnace", FURNACE, dim, item, n, 0.0 if value == "due" else time.time() + LATER_S, False)


def step(facts, d, ctx):
    """Collecting the job or the machine's output (reflexes' "collect job" / "collect machine") clears it."""
    name = (d.name or "").lower()
    return {NAME: "none"} if "collect job" in name or "collect machine" in name else {}

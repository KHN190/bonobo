"""Work running on its own (memory jobs and machines): a furnace still smelting (its output on the way:
memory.pending_outputs), one done (reflexes' collect job), a crop growing, an auto smelter's order due (collect
machine). α: memory.jobs / machines read through world.job_ready and craft.pending_ready."""
import time

NAME = "jobs"
VALUES = ("none", "smelting", "done", "crop", "machine")
FURNACE = (-6, 64, -6)             # the job's block, out of the way of the other dimensions' cells
FIELD = (-6, 64, -3)
RUN_S = 600                         # a job this far from done
ORDER = ("minecraft:iron_ingot", 3)


def domain():
    return VALUES


def alpha(a):
    from bonobo import craft, world
    dim, now = a.snap.dimension, time.time()
    if any(craft.pending_ready(m) for m in a.mem.machines(dim)):
        return "machine"
    jobs = a.mem.jobs(dim)
    if any(j["kind"] == "crop" for j in jobs):
        return "crop"
    if any(world.job_ready(j, now=now) for j in jobs):
        return "done"
    return "smelting" if jobs else "none"


def gamma(value, facts, g):
    if value == "none":
        return
    dim, now = facts["dimension"], time.time()
    item, n = ORDER
    if value in ("smelting", "done"):
        g.blocks[FURNACE] = "furnace"
        g.mem.add_job("furnace", FURNACE, dim, item, n, now + RUN_S if value == "smelting" else now - 1, False)
    elif value == "crop":
        g.blocks[(FIELD[0], FIELD[1] - 1, FIELD[2])] = "farmland"
        g.blocks[FIELD] = "wheat[age=3]"
        g.mem.add_job("crop", FIELD, dim, "minecraft:wheat", 1, now + RUN_S, False)
    else:
        name = g.mem.add_machine("auto_smelter", FURNACE, 0, dim, ["smelter"])
        g.mem.add_pending(name, item, n, now - 1)

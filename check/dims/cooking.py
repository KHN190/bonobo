"""cooking: a furnace job left running here (memory.pending_outputs: needs.food_on_its_way, the planner's awaited
outputs) — none, meat cooking, iron smelting."""
NAME = "cooking"
VALUES = ("none", "food", "iron")

FURNACE = (20, 64, -20)      # out of the home and past actions.STATION_R: the base `station` fact stays its own
JOB = {"food": ("minecraft:cooked_beef", 4), "iron": ("minecraft:iron_ingot", 3)}


def domain():
    return VALUES


def alpha(a):
    out = a.mem.pending_outputs(a.snap.dimension)
    return next((k for k, (item, _n) in JOB.items() if out.get(item)), "none")


def gamma(value, facts, g):
    if value == "none":
        return
    import time
    item, n = JOB[value]
    g.blocks[FURNACE] = "furnace"
    g.mem.add_job("furnace", FURNACE, facts["dimension"], item, n, time.time() + 10 * n + 5, False)

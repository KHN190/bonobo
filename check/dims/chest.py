"""chest: what the containers here are known to hold — none known, one recorded holding the run's early
materials, one holding iron (taken rather than smelted) (memory.stored: decompose.from_containers), or a home chest never opened (memory.home_containers
without a record: decompose._best_look)."""
NAME = "chest"
VALUES = ("none", "stocked", "iron", "unopened")

STORE = ((10, 64, 10), (13, 66, 13))     # a store room away from the feet: not the base `place`
CHEST = (11, 64, 11)
STOCK = {"minecraft:oak_log": 8, "minecraft:cobblestone": 32, "minecraft:bread": 4}       # the early materials
IRON = {"minecraft:iron_ingot": 3, "minecraft:raw_iron": 6}     # taken vs smelted (decompose.from_containers)


def domain():
    return VALUES


def alpha(a):
    snap, mem = a.snap, a.mem
    held = {i for c in (mem.data.get("containers") or {}).values() if c["dimension"] == snap.dimension
            for i, n in c["items"].items() if n > 0}
    if held & set(IRON):
        return "iron"
    if held:
        return "stocked"
    return "unopened" if any(mem.container_record(c) is None for c in mem.home_containers(snap.dimension)) else "none"


def gamma(value, facts, g):
    if value == "none":
        return
    g.blocks[CHEST] = "chest"
    if value in ("stocked", "iron"):
        held = STOCK if value == "stocked" else IRON
        g.mem.note_container(CHEST, facts["dimension"], [{"slot": i, "id": item, "count": n, "owner": "container"}
                                                        for i, (item, n) in enumerate(held.items())])
    else:
        g.mem.add_home("store", [STORE], facts["dimension"], {CHEST: "chest"})

"""A container the memory knows: one noted holding logs or iron (decompose.from_containers: withdraw against make),
or a home chest never opened (decompose._best_look: worth a look). α: memory.stored, home_containers +
container_record — the reads from_containers makes."""
NAME = "chest"
VALUES = ("none", "logs", "iron", "unopened")
AWAY = (6, 64, -6)                  # a store out of the way of the feet, the home and the other dimensions' cells
IN_HOME = (-2, 64, -2)              # inside check.gamma.HOME's room
HELD = {"logs": ("minecraft:oak_log", 16), "iron": ("minecraft:iron_ingot", 3)}


def domain():
    return VALUES


def alpha(a):
    dim = a.snap.dimension
    for value, (item, _n) in HELD.items():
        if a.mem.stored(item, dim):
            return value
    unopened = [c for c in a.mem.home_containers(dim) if a.mem.container_record(c) is None]
    return "unopened" if unopened else "none"


def gamma(value, facts, g):
    if value == "none":
        return
    dim = facts["dimension"]
    if value in HELD:
        item, n = HELD[value]
        g.blocks[AWAY] = "chest"
        g.mem.note_container(AWAY, dim, [{"slot": 0, "id": item, "count": n}])
        return
    if facts["place"] == "home":            # check.gamma registers the home with what its room holds
        g.blocks[IN_HOME] = "chest"
        return
    g.blocks[AWAY] = "chest"                # a store of its own: a home holding only the chest
    lo, hi = tuple(c - 1 for c in AWAY), tuple(c + 1 for c in AWAY)
    g.mem.add_home("store", [(lo, hi)], dim, {AWAY: "chest"})

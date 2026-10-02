"""What the bag holds for the day's upkeep beyond the base facts: torches (goals.PREPARE's last row) and the station
kit carried (goals.MILESTONES "station kit"). With every PREPARE row met the brain's upkeep proposes nothing and the
plan's own proposals are asked (brain.plan_proposals: night_stock under cover, prepare's nothing-left).
α: the bag's counts of those items."""
NAME = "stock"
VALUES = ("none", "torches", "kit")
TORCHES = ("minecraft:torch", 8)
KIT = (("minecraft:crafting_table", 1), ("minecraft:furnace", 1))


def domain():
    return VALUES


def alpha(a):
    inv = a.snap.inv
    if not inv.count(TORCHES[0]) >= TORCHES[1]:
        return "none"
    return "kit" if all(inv.count(i) >= n for i, n in KIT) else "torches"


def gamma(value, facts, g):
    if value == "none":
        return
    g.give(TORCHES[0].removeprefix("minecraft:"), TORCHES[1])
    if value == "kit":
        for item, n in KIT:
            g.give(item.removeprefix("minecraft:"), n)

"""Armour worn (the /inventory equipment slots: planner.from_inventory counts what is worn, perception's kit and the
threat's protection read it). α: an armour slot filled."""
NAME = "armour"
VALUES = ("none", "iron", "gold")
WORN = {"head": "minecraft:iron_helmet", "chest": "minecraft:iron_chestplate"}
GOLD = "minecraft:golden_helmet"
POINTS = 8                          # /state "armor": what an iron helmet and chestplate give


def domain():
    return VALUES


def alpha(a):
    from check.facts import gold_worn
    if gold_worn(a.snap.inv):
        return "gold"                  # a piglin leaves the gold-clad alone (threat.aggro)
    return "iron" if any(a.snap.inv.equipment.get(slot) for slot in WORN) else "none"


def gamma(value, facts, g):
    if value == "iron":
        for slot, item in WORN.items():
            g.equipment[slot] = {"id": item, "count": 1}
        g.state["armor"] = POINTS
    elif value == "gold":
        g.equipment["head"] = {"id": GOLD, "count": 1}

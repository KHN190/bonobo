"""What the body carries to fight with (perception.kit's readings), threat or not (a goal fight reads it too): the hand,
a sword, a bow with arrows, a shield in the offhand, gold worn (a piglin leaves it alone), armour worn (/state armor:
threat.protection), a golden apple (the fight's eat answer)."""
from bonobo import knowledge

NAME = "kit"
VALUES = ("hand", "sword", "bow", "shield", "gold", "armor", "apple")
ARMOR_POINTS = 6                    # an iron chestplate (Minecraft Wiki, Armor: iron chestplate 6)


def domain():
    return VALUES


def alpha(a):
    inv = a.snap.inv
    if knowledge.held_tiers(inv).get("sword", -1) >= 0:
        return "sword"
    if inv.count("minecraft:bow") and inv.count("minecraft:arrow"):
        return "bow"
    if inv.offhand() == "minecraft:shield":
        return "shield"
    from check.facts import gold_worn
    if gold_worn(inv):
        return "gold"
    if float(a.snap.state.get("armor", 0) or 0) > 0:
        return "armor"
    return "apple" if inv.count("minecraft:golden_apple") else "hand"


def gamma(value, f, g):
    if value == "hand":
        return
    if value == "sword":
        g.give("iron_sword")
    elif value == "bow":
        g.give("bow")
        g.give("arrow", 16)
    elif value == "shield":
        g.equipment["offhand"] = {"id": "minecraft:shield", "count": 1}
    elif value == "gold":
        g.equipment["head"] = {"id": "minecraft:golden_helmet", "count": 1}
    elif value == "armor":
        g.equipment["chest"] = {"id": "minecraft:iron_chestplate", "count": 1}
        g.state["armor"] = ARMOR_POINTS
    else:
        g.give("golden_apple", 2)

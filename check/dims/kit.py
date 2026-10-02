"""What the body carries to fight with (perception.kit's readings), threat or not (a goal fight reads it too): the hand,
a sword, a bow with arrows, a shield in the offhand, a golden apple (the fight's eat answer). What is worn is the
`armour` dimension's."""
from bonobo import knowledge

NAME = "kit"
VALUES = ("hand", "sword", "bow", "shield", "apple")


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
    else:
        g.give("golden_apple", 2)

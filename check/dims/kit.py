"""What the body carries to fight with (perception.kit's readings), threat or not (a goal fight reads it too): the hand,
a sword, a bow with arrows, a shield in the offhand, a sword with a shield (fight_shielded), a golden apple (the
fight's eat answer). What is worn is the `armour` dimension's."""
from bonobo import knowledge

NAME = "kit"
VALUES = ("hand", "sword", "bow", "shield", "apple", "sword_shield")
SHIELD = {"id": "minecraft:shield", "count": 1}


def domain():
    return VALUES


def alpha(a):
    inv = a.snap.inv
    sword = knowledge.held_tiers(inv).get("sword", -1) >= 0
    shield = inv.offhand() == SHIELD["id"]
    if sword:
        return "sword_shield" if shield else "sword"
    if inv.count("minecraft:bow") and inv.count("minecraft:arrow"):
        return "bow"
    if shield:
        return "shield"
    return "apple" if inv.count("minecraft:golden_apple") else "hand"


def gamma(value, f, g):
    if value in ("sword", "sword_shield"):
        g.give("iron_sword")
    if value in ("shield", "sword_shield"):
        g.equipment["offhand"] = dict(SHIELD)
    if value == "bow":
        g.give("bow")
        g.give("arrow", 16)
    elif value == "apple":
        g.give("golden_apple", 2)

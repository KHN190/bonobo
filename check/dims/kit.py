"""What the fight carries (perception.kit's readings): the hand, a sword, a bow with arrows, a shield in the offhand."""
from bonobo import knowledge

NAME = "kit"
VALUES = ("hand", "sword", "bow", "shield")
DEPENDS = (lambda f: f["threat"], {"threat": True})


def domain():
    return VALUES


def alpha(a):
    inv = a.snap.inv
    if knowledge.held_tiers(inv).get("sword", -1) >= 0:
        return "sword"
    if inv.count("minecraft:bow") and inv.count("minecraft:arrow"):
        return "bow"
    return "shield" if inv.offhand() == "minecraft:shield" else "hand"


def gamma(value, f, g):
    if not f["threat"] or value == "hand":
        return
    if value == "sword":
        g.give("iron_sword")
    elif value == "bow":
        g.give("bow")
        g.give("arrow", 16)
    else:
        g.equipment["offhand"] = {"id": "minecraft:shield", "count": 1}

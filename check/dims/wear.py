"""A second pickaxe carried nearly broken beside the working one (needs.durability_left below
knowledge.TOOL_MIN_DURABILITY: held_tiers skips it, combining two tools reads it). Read only with a pickaxe carried
(the base `pickaxe` fact, which the working one keeps)."""
from bonobo.knowledge import TOOL_MIN_DURABILITY

NAME = "wear"
VALUES = ("fresh", "worn")
DEPENDS = (lambda f: f["pickaxe"] >= 0, {"pickaxe": 0})


def domain():
    return VALUES


def alpha(a):
    from bonobo.needs import durability_left
    left = durability_left(a.snap.inv).get("pickaxe")
    return "worn" if left is not None and left < TOOL_MIN_DURABILITY else "fresh"


def gamma(value, facts, g):
    if value != "worn":
        return
    pick = next(sl for sl in g.slots if sl["id"].endswith("_pickaxe"))
    g.give(pick["id"].removeprefix("minecraft:"))
    worn = g.slots[-1]
    worn["damage"] = worn["maxDamage"] - (TOOL_MIN_DURABILITY - 1)

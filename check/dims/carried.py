"""carried: raw stuff in the bag that a plan can finish rather than gather — raw meat (planner.cooked_from_carried),
raw iron and coal (a smelt), logs (planks without a tree). None of it is a base fact (food is cooked food, building
is blocks)."""
NAME = "carried"
VALUES = ("none", "raw_meat", "raw_iron", "logs", "iron_planks", "meat_fuel", "stocked")

# iron_planks: raw iron with planks and no coal (planner._smelt's other fuel); checked first, it holds no coal
# meat_fuel: raw meat with fuel (reflexes.can_cook: cooked rather than eaten raw), checked before raw_meat
# stocked: the idle stock beyond the base facts (goals.PREPARE: a sword, torches; the base gives the pickaxe and the
# food, the `kit` dimension the sword: one fact, one dimension) — prepare passes every need and walks the milestones.
ITEMS = {"stocked": (("torch", 8),), "iron_planks": (("raw_iron", 3), ("oak_planks", 8)), "meat_fuel": (("beef", 6), ("coal", 2)),
         "raw_meat": (("beef", 6),),
         "raw_iron": (("raw_iron", 3), ("coal", 2)), "logs": (("oak_log", 4),)}


def domain():
    return VALUES


def valid(value, facts):
    return value != "stocked" or (not facts["threat"] and facts["kit"] in ("sword", "sword_shield"))


def alpha(a):
    inv = a.snap.inv
    from bonobo.knowledge import held_tiers
    if held_tiers(inv).get("sword", -1) >= 0 and inv.count("minecraft:torch") >= 8:
        return "stocked"
    if inv.count("minecraft:raw_iron") and not inv.count("minecraft:coal") and inv.count("minecraft:oak_planks"):
        return "iron_planks"
    return next((k for k, items in ITEMS.items() if k not in ("iron_planks", "stocked")
                 and all(inv.count(f"minecraft:{i}") >= n for i, n in items)), "none")


def gamma(value, facts, g):
    for item, n in ITEMS.get(value, ()):
        g.give(item, n)

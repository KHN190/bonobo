"""Raw meat carried (knowledge.RAW_MEAT: the eat row eats it raw only starving or with no way to cook), with or
without fuel to cook it (reflexes.can_cook, FUELS)."""
from bonobo.knowledge import RAW_MEAT
from bonobo.reflexes import FUELS

NAME = "meat"
VALUES = ("none", "raw", "raw_fuel")


def domain():
    return VALUES


def valid(value, f):
    """Raw meat without fuel: not while the queued craft's inputs (gamma.inputs) carry a fuel (planks for sticks)."""
    from check.gamma import inputs
    return value != "raw" or f["queued"] == "none" or not any(
        fuel in item for item in inputs(f["queued"]) for fuel in FUELS)


def alpha(a):
    inv = a.snap.inv
    if not any(inv.count(m) for m in RAW_MEAT):
        return "none"
    return "raw_fuel" if any(inv.count(f) for f in FUELS) else "raw"


def gamma(value, f, g):
    if value != "none":
        g.give(RAW_MEAT[0].removeprefix("minecraft:"), 4)
    if value == "raw_fuel":
        g.give(FUELS[0], 4)

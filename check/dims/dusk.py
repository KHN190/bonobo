"""dusk: day in the Overworld, and the night's cheapest way no longer fits in the light left — needs.dusk_s against
needs.overnight's seconds × needs.LEAD (the one margin). Read only by day in the Overworld (by night it is night), without a carried bed."""
from bonobo.data import DAY_END

NAME = "dusk"
VALUES = (False, True)
# a carried bed is the 0 s way (the sleep row): nothing is ever due before it, so no dusk with one
DEPENDS = (lambda f: not f["night"] and f["dimension"] == "minecraft:overworld" and f["bed"] != "carried",
           {"night": False})
WORLD = True         # the clock moves it

DUSK_T = DAY_END - 1                 # one tick of light left: no way fits
DAWN_T = 0                           # the whole day ahead (625 s): every way of the bags here fits (dig in, 371 s × LEAD)


def domain():
    return VALUES


def alpha(a):
    from bonobo.cost import Cost
    from bonobo.needs import LEAD, dusk_s, overnight
    snap = a.snap
    if snap.night or snap.dimension != "minecraft:overworld":
        return False
    if a.brain is not None:
        # the brain's own night table (needs.overnight, priced once for the round): not priced while a way needing
        # nothing already ends the night inside the light left
        a.brain.needs.night_facts(snap)
        free = a.brain.needs.night_free_s(snap)
        if free is not None and dusk_s(snap) >= free * LEAD:
            return False
        way, seconds, _steps = a.brain.needs.overnight(snap)
    else:
        way, seconds, _steps = overnight(snap.inv, Cost(snap, a.mem))
    return way is not None and dusk_s(snap) < seconds * LEAD


def gamma(value, facts, g):
    if facts["dimension"] == "minecraft:overworld" and not facts["night"]:
        g.state["timeOfDay"] = DUSK_T if value else DAWN_T


def step(facts, d, ctx):
    """A night slept or waited through ends in the next morning: not dusk."""
    name = (d.name or "").lower()
    return {"dusk": False} if facts["dusk"] and ("sleep" in name or "wait for day" in name) else {}

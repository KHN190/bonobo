"""dusk: day in the Overworld, and the night's cheapest way no longer fits in the light left — needs.dusk_s against
needs.overnight's seconds × needs.LEAD (the one margin). Read only by day in the Overworld (by night it is night), without a carried bed."""
from bonobo.data import DAY_END, DAY_TICKS, NIGHT_END

NAME = "dusk"
VALUES = (False, True, "dawn")      # dawn: the day's last ticks after NIGHT_END, before the clock wraps
# a carried bed is the 0 s way (the sleep row): nothing is ever due before it, so no dusk with one
DEPENDS = (lambda f: not f["night"] and f["dimension"] == "minecraft:overworld" and f["bed"] != "carried",
           {"night": False})
WORLD = True         # the clock moves it

DUSK_T = DAY_END - 1                 # one tick of light left: no way fits
DAWN_T = 0                           # the whole day ahead (625 s): the cheapest way's prep fits
DAYBREAK_T = NIGHT_END + 300         # day again, past NIGHT_END: dusk_s reads it as 0


def valid(value, f):
    # every shelter cooling (the bed alone): no way's prep fits a day, so never not-dusk
    return value or not DEPENDS[0](f) or not f["cooled"]


def instead(f):
    return True


def domain():
    return VALUES


def alpha(a):
    from bonobo.cost import Cost
    from bonobo.needs import LEAD, dusk_s, overnight
    snap = a.snap
    if snap.night or snap.dimension != "minecraft:overworld":
        return False
    if int(snap.time) % DAY_TICKS > NIGHT_END:
        return "dawn"
    if a.brain is not None:
        a.brain.needs.night_facts(snap)
        prep = a.brain.needs.night_prep_s(snap)
        return prep is not None and dusk_s(snap) < prep * LEAD
    way, seconds, _steps = overnight(snap.inv, Cost(snap, a.mem))
    return way is not None and dusk_s(snap) < seconds * LEAD


def gamma(value, facts, g):
    if facts["dimension"] == "minecraft:overworld" and not facts["night"]:
        g.state["timeOfDay"] = DAYBREAK_T if value == "dawn" else DUSK_T if value else DAWN_T


def step(facts, d, ctx):
    """A night slept or waited through ends in the next morning: not dusk."""
    name = (d.name or "").lower()
    return {"dusk": False} if facts["dusk"] and ("sleep" in name or "wait for day" in name) else {}

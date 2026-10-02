"""A neutral mob near (within the fight's notice radius) that is not after us (threat.aggro says no): a calm enderman, a spider by day — seen,
priced at no awareness, never a threat row."""
from bonobo.threat import ENGAGE, NEUTRAL_MOBS, aggro, context_of

NAME = "bystander"
VALUES = ("none", "enderman", "spider")
SPOT = (-6.5, 64.0, -6.5)                   # off to the side, inside the look
NOTICE = float(ENGAGE["notice_r"])          # near: within the fight's notice radius (a far one is quarry, not company)


def domain():
    return VALUES


def valid(value, f):
    """A spider is calm by day only — the Overworld's day (threat.context_of)."""
    return value != "spider" or (not f["night"] and f["dimension"] == "minecraft:overworld")


def alpha(a):
    ctx = context_of(a.snap.state, None)
    calm = [e for e in a.world._entities({"radius": "48"})["entities"]
            if e["type"] in NEUTRAL_MOBS and not aggro(e, ctx) and e["distance"] <= NOTICE]
    return calm[0]["type"].removeprefix("minecraft:") if calm else "none"


def gamma(value, f, g):
    if value != "none":
        x, y, z = SPOT
        g.entities.append({"id": 70, "type": f"minecraft:{value}", "x": x, "y": y, "z": z, "health": 20.0})

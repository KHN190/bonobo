"""The shape we stand in (field.shape_at, what the threat answers and the pit reflex read): open ground, a column
above the ground round it (under: a walker reaches up less), a hole walled two high (down) or three (walled: a
ranged one hidden more, reaches_share), a ceiling two up over
the ground round us (roof: no tall mob stands under it — an enderman's answer, field.low_cover_at)."""
from bonobo.field import shape_at
from bonobo.threat import ENGAGE
from bonobo.world import Region

NAME = "ground"
VALUES = ("open", "pillar", "hole", "roof", "walled")
SIDES = ((1, 0), (-1, 0), (0, 1), (0, -1))
DEEP = int(float(ENGAGE["melee_stop_blocks"])) + 1     # walls past a walker's stop: a ranged one is hidden more (shape_at counts 3)
SPAN = 5          # the read round the feet: shape_at's stand_level looks 4 up and down


def domain():
    return VALUES


def alpha(a):
    x, y, z = a.snap.feet
    lo, hi = (x - SPAN, y - SPAN, z - SPAN), (x + SPAN, y + SPAN, z + SPAN)
    shapes = dict(shape_at(Region.of(lo, hi, dict(a.world._cells(lo, hi))), (x, y, z)))
    if "down" in shapes:
        return "walled" if shapes["down"] >= DEEP else "hole"
    return "pillar" if "under" in shapes else "roof" if "roof" in shapes else "open"


def gamma(value, f, g):
    if value == "open":
        return
    x, y, z = int(g.state["blockX"]), int(g.state["blockY"]), int(g.state["blockZ"])
    if value == "roof":
        g.blocks.update({(x + dx, y + 2, z + dz): "stone" for dx in range(-SPAN, SPAN + 1)
                         for dz in range(-SPAN, SPAN + 1)})
        return
    for dx, dz in SIDES:
        if value == "pillar":
            g.blocks[(x + dx, y - 1, z + dz)] = "air"         # the ground round the column one lower
        else:
            g.blocks.update({(x + dx, y + k, z + dz): "stone" for k in range(DEEP if value == "walled" else 2)})


def step(facts, d, ctx):
    """Leaving the pit (reflexes' "leave the pit": a step dug out of the hole) stands on open ground."""
    return {NAME: "open"} if "leave the pit" in (d.name or "").lower() else {}

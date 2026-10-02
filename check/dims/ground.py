"""The shape we stand in (field.shape_at, what the threat answers and the pit reflex read): open ground, a column
above the ground round it (under: a walker reaches up less), a hole walled two high (down)."""
from bonobo.field import shape_at
from bonobo.world import Region

NAME = "ground"
VALUES = ("open", "pillar", "hole")
SIDES = ((1, 0), (-1, 0), (0, 1), (0, -1))
SPAN = 5          # the read round the feet: shape_at's stand_level looks 4 up and down


def domain():
    return VALUES


def alpha(a):
    x, y, z = a.snap.feet
    lo, hi = (x - SPAN, y - SPAN, z - SPAN), (x + SPAN, y + SPAN, z + SPAN)
    shapes = dict(shape_at(Region.of(lo, hi, dict(a.world._cells(lo, hi))), (x, y, z)))
    return "pillar" if "under" in shapes else "hole" if "down" in shapes else "open"


def gamma(value, f, g):
    if value == "open":
        return
    x, y, z = int(g.state["blockX"]), int(g.state["blockY"]), int(g.state["blockZ"])
    for dx, dz in SIDES:
        if value == "pillar":
            g.blocks[(x + dx, y - 1, z + dz)] = "air"         # the ground round the column one lower
        else:
            g.blocks.update({(x + dx, y, z + dz): "stone", (x + dx, y + 1, z + dz): "stone"})


def step(facts, d, ctx):
    """Leaving the pit (reflexes' "leave the pit": a step dug out of the hole) stands on open ground."""
    return {NAME: "open"} if "leave the pit" in (d.name or "").lower() else {}

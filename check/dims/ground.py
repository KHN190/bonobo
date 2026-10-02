"""The shape we stand in (field.shape_at, what the threat answers and the pit reflex read): open ground, a column
above the ground round it (under: a walker reaches up less), a hole walled two high (down) or three (walled: a
ranged one hidden more, reaches_share), a ceiling two up over
the ground round us (roof: no tall mob stands under it — an enderman's answer, field.low_cover_at)."""
from bonobo.field import shape_at, stand_level
from bonobo.threat import ENGAGE
from bonobo.world import Region

NAME = "ground"
VALUES = ("open", "pillar", "hole", "roof", "walled", "edge")
SIDES = ((1, 0), (-1, 0), (0, 1), (0, -1))
DEEP = int(float(ENGAGE["melee_stop_blocks"])) + 1     # walls past a walker's stop: a ranged one is hidden more (shape_at counts 3)
EDGE_R, EDGE_DROP = 8, 20                 # the ledge's drop: out to the field's read (GRID_R), the γ floor's depth
SPAN = 5          # the read round the feet: shape_at's stand_level looks 4 up and down


def domain():
    return VALUES


def alpha(a):
    x, y, z = a.snap.feet
    lo, hi = (x - SPAN, y - SPAN, z - SPAN), (x + SPAN, y + SPAN, z + SPAN)
    region = Region.of(lo, hi, dict(a.world._cells(lo, hi)))
    shapes = dict(shape_at(region, (x, y, z)))
    if all(stand_level(region.solid, x + dx * 2, z + dz * 2, y) is None for dx, dz in SIDES):
        return "edge"
    if "down" in shapes:
        return "walled" if shapes["down"] >= DEEP else "hole"
    return "pillar" if "under" in shapes else "roof" if "roof" in shapes else "open"


def gamma(value, f, g):
    if value == "open":
        return
    x, y, z = int(g.state["blockX"]), int(g.state["blockY"]), int(g.state["blockZ"])
    if value == "edge":
        # a 3×3 ledge over a drop past any safe fall: every column off it open down to the floor's foot
        for dx in range(-EDGE_R, EDGE_R + 1):
            for dz in range(-EDGE_R, EDGE_R + 1):
                if max(abs(dx), abs(dz)) > 1:
                    for dy in range(1, EDGE_DROP + 1):
                        g.blocks.setdefault((x + dx, y - dy, z + dz), "air")
        return
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

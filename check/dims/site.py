"""site: a site of ours with an interior (memory.sites' "interior" cells: a hut built here) — none; one a few blocks off
(perception.cover_near: the nearest interior cell, threat.escape_spot's cover); the feet inside it (reflexes.in_site:
the night way's shelter, perception.in_site_here)."""
import math

NAME = "site"
VALUES = ("none", "near", "inside")
NEAR = (4, 64, 4)          # the hut's first interior cell when it is near: off the feet, inside the cover radius
NEAR_R = 8                 # blocks: "near" as this dimension reads it back


def domain():
    return VALUES


def _cells(origin):
    x, y, z = origin
    return [[x + dx, y, z + dz] for dx in (0, 1) for dz in (0, 1)]


def alpha(a):
    feet = [int(c) for c in a.snap.feet]
    cells = [c for s in a.mem.sites(a.snap.dimension) for c in s.get("interior", [])]
    if feet in cells:
        return "inside"
    return "near" if any(math.dist(c, feet) <= NEAR_R for c in cells) else "none"


def gamma(value, f, g):
    if value == "none":
        return
    origin = tuple(int(c) for c in (g.state["blockX"], g.state["blockY"], g.state["blockZ"])) if value == "inside" \
        else NEAR
    site = g.mem.add_site("shelter", origin, f["dimension"], name="check-hut")
    g.mem.update_site(site["name"], interior=_cells(origin))

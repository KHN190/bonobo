"""Terrain reading: pure planners over a Region — where land is, where to shelter or burrow, how to get out of an
enclosure or up to air, where there is room to sort the bag. No game access; skills.py and brain.py execute."""
import math

from . import nav
from .bag import throw_direction
from .world import add


def find_open_spot(region, here, radius=12):
    """Pure: the nearest standable cell where inventory work can happen — a direction with 3+ blocks of room to
    throw into and air above a neighbouring floor cell for a chest. Shafts and 1-wide tunnels have neither."""
    best = None
    for (x, y, z), name in region.blocks.items():
        cell = (x, y + 1, z)
        if math.dist(cell, here) > radius or not region.solid((x, y, z)) or region.hazard((x, y, z)):
            continue
        if region.name(cell) not in ("air", "cave_air") or region.name((x, y + 2, z)) not in ("air", "cave_air"):
            continue
        if throw_direction(region, cell) is None:
            continue
        chest_ok = any(region.solid((x + dx, y, z + dz)) and chest_spot_ok(region, (x + dx, y + 1, z + dz))
                       for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)])
        if not chest_ok:
            continue
        d = math.dist(cell, here)
        if best is None or d < best[0]:
            best = (d, cell)
    return None if best is None else best[1]


def chest_spot_ok(region, spot):
    """Pure: a chest only opens with no solid block directly above it."""
    return not region.solid(spot) and not region.solid(add(spot, (0, 1, 0)))


LAND = ["grass_block", "dirt", "stone", "sand", "gravel", "deepslate", "andesite", "diorite", "granite", "tuff",
        "podzol", "coarse_dirt", "snow_block", "cobblestone", "moss_block", "clay"]


def pick_land(region, here):
    """Pure: the nearest cell to stand on dry land — a solid, non-hazard floor with two free (not water) cells
    above. None when there is no land in the region."""
    best = None
    for (x, y, z), name in region.blocks.items():
        if name not in LAND:
            continue
        feet_c, head_c = (x, y + 1, z), (x, y + 2, z)
        if not region.inside(head_c) or region.name(feet_c) not in ("air", "cave_air") \
                or region.name(head_c) not in ("air", "cave_air"):
            continue
        d = math.dist(feet_c, here)
        if best is None or d < best[0]:
            best = (d, feet_c)
    return None if best is None else best[1]


def underground_target(region, here, depth=(3, 6), radius=3):
    """Pure: a cell to tunnel to for the night — a 2-high space-to-be with at least 2 solid, non-hazard blocks
    straight above its head, so arriving there means no sky. Walking "6 blocks lower" on a hillside only reached
    open ground further down. Nearest first; None when the ground here is too thin or wet."""
    x, y, z = here
    best = None
    for dy in range(depth[0], depth[1] + 1):
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                c = (x + dx, y - dy, z + dz)
                roof = [(c[0], c[1] + k, c[2]) for k in (2, 3)]
                cells = [c, (c[0], c[1] + 1, c[2])] + roof + [(c[0], c[1] - 1, c[2])]
                if not all(region.inside(p) for p in cells):
                    continue
                if not all(region.solid(p) for p in roof) or not region.solid((c[0], c[1] - 1, c[2])):
                    continue
                # Inside rock, not a ravine ledge: the head cell is walled on at least 3 sides (one is the way in).
                head = (c[0], c[1] + 1, c[2])
                sides = [(head[0] + ddx, head[1], head[2] + ddz) for ddx, ddz in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                if not all(region.inside(p) for p in sides) or sum(region.solid(p) for p in sides) < 3:
                    continue
                if any(region.hazard(add(p, d)) for p in (c, (c[0], c[1] + 1, c[2])) for d in NEIGHBOURS6_LOCAL):
                    continue
                d = abs(dx) + abs(dz) + dy
                if best is None or d < best[0]:
                    best = (d, c)
    return None if best is None else best[1]


def shelter_method_at(region, cell, protected=()):
    """Pure: which night shelter works with the body standing at `cell`, or None.
    "burrow" = a solid hillside beside it; "dig" = 3 solid, safe blocks straight below (dig in and seal above);
    "pod" = every open wall/roof cell has a solid neighbour to place against (so walling in can actually finish)."""
    x, y, z = cell
    if choose_burrow(region, cell, protected):
        return "burrow"
    below = [(x, y - k, z) for k in (1, 2, 3)]
    # After digging 3 down the body stands in the two lowest cells: they need solid sides too, or a pillar top
    # just becomes a hole open to the air around it.
    body_sides = [(x + dx, y - k, z + dz) for k in (2, 3) for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]]
    if all(region.solid(c) and not region.unbreakable(c) and c not in protected for c in below) \
            and region.solid((x, y - 4, z)) and all(region.solid(c) for c in body_sides) \
            and not any(region.hazard(add(c, d)) for c in below for d in NEIGHBOURS6_LOCAL):
        return "dig"
    walls = [(x + dx, y + dy, z + dz) for dy in (0, 1) for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]]
    walls.append((x, y + 2, z))
    for c in walls:
        if region.solid(c):
            continue
        if region.hazard(c):
            return None
        if not any(region.solid(add(c, d)) for d in NEIGHBOURS6_LOCAL if add(c, d) not in (cell, (x, y + 1, z))):
            return None
    return "pod"


def find_shelter_spot(region, here, radius=10, protected=()):
    """Pure: the nearest standable cell (solid floor, two free cells above, no water) where some shelter method
    works, as (cell, method). Standing on a thin pillar or a peak, nothing works in place: walk to a better spot."""
    best = None
    for (x, y, z), name in region.blocks.items():
        cell = (x, y + 1, z)
        if math.dist(cell, here) > radius or not region.solid((x, y, z)) or region.hazard((x, y, z)):
            continue
        if region.name(cell) not in ("air", "cave_air") or region.name((x, y + 2, z)) not in ("air", "cave_air"):
            continue
        method = shelter_method_at(region, cell, protected)
        if method is None:
            continue
        rank = (math.dist(cell, here), {"burrow": 0, "dig": 1, "pod": 2}[method])
        if best is None or rank < best[0]:
            best = (rank, cell, method)
    return None if best is None else (best[1], best[2])


def choose_burrow(region, inside, protected=()):
    """Pure: a horizontal direction to tunnel 2 blocks into solid ground for the night — both cells of both steps
    solid (feet and head), a floor under them, a roof over them, nothing hazardous or protected around. The
    classic hillside shelter: works on terrain where walling in on the spot can't be done."""
    x, y, z = inside
    best = None
    for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        cells = [(x + dx * k, y + dy, z + dz * k) for k in (1, 2) for dy in (0, 1)]
        if not all(region.solid(c) and not region.unbreakable(c) and c not in protected for c in cells):
            continue
        floors = [(x + dx * k, y - 1, z + dz * k) for k in (1, 2)]
        roofs = [(x + dx * k, y + 2, z + dz * k) for k in (1, 2)]
        back = [(x + dx * 3, y, z + dz * 3), (x + dx * 3, y + 1, z + dz * 3)]
        sides = [(x + dx * 2 + dz * s, y + dy, z + dz * 2 + dx * s) for s in (-1, 1) for dy in (0, 1)]
        if not all(region.solid(c) for c in floors + roofs + back + sides):
            continue
        if any(region.hazard(add(c, d)) for c in cells for d in NEIGHBOURS6_LOCAL):
            continue
        depth = sum(region.solid((x + dx * k, y, z + dz * k)) for k in range(1, 6))
        if best is None or depth > best[0]:
            best = (depth, (dx, dz))
    return None if best is None else best[1]


NEIGHBOURS6_LOCAL = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]


def choose_exit(region, inside, protected=()):
    """Pure: the best way out of a 1×2 enclosure. Returns (cells to mine, where to stand after) or None.
    Prefers a side whose outside cell is standable; never opens a wall touching lava/water or a protected cell."""
    x, y, z = inside
    best = None
    for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        walls = [(x + dx, y, z + dz), (x + dx, y + 1, z + dz)]
        if any(c in protected or region.unbreakable(c) or region.hazard(c) for c in walls):
            continue
        if any(region.hazard(add(c, d)) for c in walls for d in nav.NEIGHBOURS6):
            continue
        out = walls[0]
        floor_ok = region.solid(add(out, (0, -1, 0))) and not region.hazard(add(out, (0, -1, 0)))
        beyond = (x + 2 * dx, y, z + 2 * dz)
        open_beyond = not region.solid(beyond) and not region.solid(add(beyond, (0, 1, 0)))
        score = (floor_ok, open_beyond, -sum(region.solid(c) for c in walls))
        if best is None or score > best[0]:
            best = (score, [c for c in walls if region.solid(c)], out)
    return None if best is None else (best[1], best[2])


def air_route(region, head):
    """Pure: nearest cell with air reachable by swimming from `head` (breadth-first through water, upward moves
    first), or the first solid block capping the water column above when there is none."""
    from collections import deque
    order = [(0, 1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, -1, 0)]
    frontier, seen = deque([head]), {head}
    while frontier:
        c = frontier.popleft()
        name = region.name(c)
        if name in ("air", "cave_air") and c != head:
            return ("swim", c)
        if c != head and name != "water":
            continue
        for d in order:
            n = add(c, d)
            if n not in seen and region.inside(n):
                seen.add(n)
                frontier.append(n)
    c = head
    while region.inside(c) and region.name(c) == "water":
        c = add(c, (0, 1, 0))
    return ("dig", c) if region.inside(c) and region.solid(c) else None


def is_enclosed(region, inside):
    """Pure: no 2-high opening on any side and a solid roof. A torch or flower in one side cell doesn't make an exit
    when the cell above it is solid: neither the player nor a zombie fits through a 1-high gap."""
    x, y, z = inside
    for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        if not region.solid((x + dx, y, z + dz)) and not region.solid((x + dx, y + 1, z + dz)):
            return False
    return region.solid((x, y + 2, z))

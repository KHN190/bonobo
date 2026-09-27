"""Terrain reading: pure planners over a Region — where land is, where to shelter or burrow, how to get out of an enclosure or up to air, where there is room to sort the bag. No game access; skills.py and brain.py execute."""

import math

from . import nav
from .bag import throw_direction
from .world import add, is_enclosed, openings  # noqa: F401  (the walls: world's, re-exported)

def standing_cells(region, here, radius):
    """Pure: cells within `radius` of `here` one could stand in — solid harmless floor, two free cells up — in region order."""

    for (x, y, z), _name in region.blocks.items():
        cell = (x, y + 1, z)
        if math.dist(cell, here) > radius or not region.solid((x, y, z)) or region.hazard((x, y, z)):
            continue
        if region.name(cell) not in ("air", "cave_air") or region.name((x, y + 2, z)) not in ("air", "cave_air"):
            continue
        yield cell

def find_open_spot(region, here, radius=12):
    """Pure: the nearest standable cell with room to throw into (3+ blocks) and a chest spot beside it."""

    best = None
    for cell in standing_cells(region, here, radius):
        x, y, z = cell[0], cell[1] - 1, cell[2]
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

def underground_target(region, here, depth=(3, 6), radius=3):
    """Pure: a cell to tunnel to for the night with 2+ solid non-hazard blocks overhead: arriving means no sky."""

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
                # inside rock, not on a ledge: the head cell walled on 3+ sides
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
    """Pure: which night shelter works with the body standing at `cell`, or None."""

    x, y, z = cell
    if choose_burrow(region, cell, protected):
        return "burrow"
    below = [(x, y - k, z) for k in (1, 2, 3)]
    # after digging 3 down the body stands in the two lowest cells: they need solid sides too
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
    """Pure: the nearest standable dry cell where some shelter method works, as (cell, method)."""

    best = None
    for cell in standing_cells(region, here, radius):
        method = shelter_method_at(region, cell, protected)
        if method is None:
            continue
        rank = (math.dist(cell, here), {"burrow": 0, "dig": 1, "pod": 2}[method])
        if best is None or rank < best[0]:
            best = (rank, cell, method)
    return None if best is None else (best[1], best[2])

def choose_burrow(region, inside, protected=()):
    """Pure: a direction to tunnel 2 into solid ground — feet and head solid, floored, roofed, nothing hazardous or protected."""

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
    """Pure: the best way out of a 1×2 enclosure."""

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

def stands(region, feet_c):
    """Pure: a cell a body can stand in on dry land — a LAND floor and two non-water free cells up."""

    head_c, floor = (feet_c[0], feet_c[1] + 1, feet_c[2]), (feet_c[0], feet_c[1] - 1, feet_c[2])
    return region.inside(head_c) and region.inside(floor) and region.name(floor) in LAND \
        and region.name(feet_c) in ("air", "cave_air") and region.name(head_c) in ("air", "cave_air")

def soft_below(region, feet, depth):
    """Pure: the `depth` cells under the feet all dig by hand with something solid below — no pickaxe needed to hide."""

    from .data import HAND_MINEABLE_SUFFIX, bare
    x, y, z = feet
    names = [bare(region.name((x, y - k, z))) for k in range(1, depth + 1)]
    return all(n not in ("air", "cave_air", "water") and n.endswith(HAND_MINEABLE_SUFFIX) for n in names) \
        and region.solid((x, y - depth - 1, z))

SOFT_RADIUS = 16          # how far along the ground a spot to dig in by hand is looked for

def nearest_soft(region, feet, depth, radius=SOFT_RADIUS):
    """Pure: (cell, steps) of the nearest connected spot whose `depth` cells below dig by hand; (feet, 0) here; None when none."""

    from collections import deque
    if soft_below(region, feet, depth):
        return feet, 0
    frontier, seen = deque([(feet, 0)]), {feet}
    while frontier:
        c, d = frontier.popleft()
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            for dy in (0, 1, -1):
                n = (c[0] + dx, c[1] + dy, c[2] + dz)
                if n in seen or abs(n[0] - feet[0]) > radius or abs(n[2] - feet[2]) > radius:
                    continue
                seen.add(n)
                if not stands(region, n):
                    continue
                if soft_below(region, n, depth):
                    return n, d + 1
                frontier.append((n, d + 1))
    return None

def air_route(region, head):
    """Pure: where a drowning body goes to breathe, as (kind, cell, why) or None."""

    from collections import deque
    order = [(0, 1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, -1, 0)]
    frontier, seen, surface = deque([head]), {head}, None
    while frontier:
        c = frontier.popleft()
        if c != head and stands(region, c):
            return ("land", c, "")
        name = region.name(c)
        if name in ("air", "cave_air") and c != head:
            surface = surface or c
            if region.name(add(c, (0, -1, 0))) != "water":
                continue                                # only the air lying on the water is swum through
        elif c != head and name != "water":
            continue
        for d in order:
            n = add(c, d)
            if n not in seen and region.inside(n):
                seen.add(n)
                frontier.append(n)
    if surface is not None:
        return ("pillar", surface, "no land within reach: a block placed underfoot at the surface")
    c = head
    while region.inside(c) and region.name(c) == "water":
        c = add(c, (0, 1, 0))
    return ("dig", c, "water capped, no air within reach: dig the cap") if region.inside(c) and region.solid(c) \
        else None

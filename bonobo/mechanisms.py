"""Taught mechanisms: "press this cell and those cells open" (a button and its piston door). Taught per save by the
player (mc.py mech add), never derived and never written in code. The store is plain data; whether a door stands
open is always read off the world, never remembered. Two buttons of one door (outside, inside) are two entries with
the same opens cells.

    [{"dimension": ..., "press": [x, y, z], "opens": [[x, y, z], ...]}]
"""
from __future__ import annotations

import json
import math
import os

from . import api, paths
from .knowledge import left
from .skill import skill
from .world import Region

FILE = paths.data("mechanisms.json")
DOOR_NEAR = 2.0           # an opens cell this near the straight way from here to there puts the door on the way
PRESS_S = 1.0             # the use itself, once in reach


# -- the store
def load(path=None):
    try:
        with open(path or FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save(mechs, path=None):
    path = path or FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(mechs, f, indent=1)


def add(dimension, press, opens, path=None):
    """Teach one: a press cell and the cells it opens (the old lesson for the same press replaced)."""
    mechs = [m for m in load(path) if not (m["dimension"] == dimension and tuple(m["press"]) == tuple(press))]
    mechs.append({"dimension": dimension, "press": list(press), "opens": [list(c) for c in opens]})
    save(mechs, path)
    return mechs[-1]


def remove(dimension, press, path=None):
    mechs = load(path)
    kept = [m for m in mechs if not (m["dimension"] == dimension and tuple(m["press"]) == tuple(press))]
    save(kept, path)
    return len(mechs) - len(kept)


def in_dimension(dimension, path=None):
    return [m for m in load(path) if m["dimension"] == dimension]


# -- pure
def is_open(opens, solid):
    """Pure: every opens cell passable now (`solid`: the world's reading)."""
    return all(not solid(tuple(c)) for c in opens)


def _to_segment(p, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    n = sum(v * v for v in ab)
    t = 0.0 if n == 0 else max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / n))
    return math.dist(p, [a[i] + t * ab[i] for i in range(3)])


def centre(cells):
    return tuple(sum(c[i] for c in cells) / len(cells) for i in range(3))


def on_the_way(mechs, here, there, near=DOOR_NEAR):
    """Pure: the doors (their opens cells, a tuple) with a cell near the straight way here → there, nearest first."""
    doors = {tuple(map(tuple, m["opens"])) for m in mechs}
    hit = [d for d in doors if any(_to_segment(c, here, there) <= near for c in d)]
    return sorted(hit, key=lambda d: math.dist(centre(d), here))


def press_for(mechs, door, here):
    """Pure: the press of `door` on our side (the side `here` stands on, seen from the door's centre), nearest;
    None when none of its presses is on our side."""
    c = centre(door)
    ours = [tuple(m["press"]) for m in mechs if tuple(map(tuple, m["opens"])) == door
            and sum((m["press"][i] - c[i]) * (here[i] - c[i]) for i in (0, 2)) > 0]
    return min(ours, key=lambda p: math.dist(p, here), default=None)


def door_route_s(mechs, here, there, walk_s):
    """Pure: seconds here → there through the first taught door on the way — the walk to our side's press, the press,
    the walk through (`walk_s`: blocks → seconds); None when no door is on the way or none presses from our side."""
    doors = on_the_way(mechs, here, there)
    if not doors:
        return None
    press = press_for(mechs, doors[0], here)
    if press is None:
        return None
    return walk_s(math.dist(here, press)) + PRESS_S + walk_s(math.dist(press, there))


def route_s(here, there, walk_s, dimension=None):
    """cost's and nav's wire: door_route_s over this save's taught mechanisms — a file read; the dimension read off
    the game only when something is taught and the caller did not say it (cost says it: an estimate reads nothing)."""
    mechs = load()
    if not mechs:
        return None
    dimension = dimension or api.get("/state")["dimension"]
    return door_route_s([m for m in mechs if m["dimension"] == dimension], here, there, walk_s)


# -- the world
OPEN_PROP = "open"         # a block state a door, trapdoor or gate stands open by: passable though still there


def passable_now(solid, prop):
    """Pure: a cell passes when not solid, or when it stands `open` (a door keeps its name open or shut)."""
    return lambda c: solid(c) and prop(c, OPEN_PROP) != "true"


def solid_map(cells):
    """{cell: solid} over `cells`, one region read with block states (an open door is no wall)."""
    cells = [tuple(c) for c in cells]
    lo = tuple(min(c[i] for c in cells) for i in range(3))
    hi = tuple(max(c[i] for c in cells) for i in range(3))
    r = Region(lo, hi, props=True)
    shut = passable_now(r.solid, r.prop)
    return {c: shut(c) for c in cells}


def opens_left(st, c):
    """`remaining` of press_mechanism: the opens cells passable in the region read ({} open)."""
    region, opens = st.get("region"), c.args[2]
    if region is None:
        return {"unread:mechanism": 1}
    shut = passable_now(region.solid, getattr(region, "prop", lambda c, k: None))
    return left(is_open(opens, shut), "state:opened")


def _opened_now(c):
    got = solid_map(c.args[2])
    return is_open(c.args[2], lambda p: got[tuple(p)])


@skill(gives=["state:opened"], remaining=opens_left, needs={}, speed={}, budget=60, stall=30, verify=_opened_now)
def press_mechanism(ctx, press, opens):
    """Open a taught door: already open (read off the world) → nothing pressed; else into reach of the press, use it."""
    got = solid_map(opens)
    if is_open(opens, lambda p: got[tuple(p)]):
        return "open"
    api.run({"type": "use", "x": press[0], "y": press[1], "z": press[2]}, wait=30,
            awaits="the use done: the opens cells are read next")
    return "pressed"


def doors_on_way(here, there, policy=None, dimension=None):
    """nav's wire: the taught door on the way here → there opened from our side's press first (pressed only when
    closed); returns every taught opens cell, which a walk may cross but never dig."""
    mechs = load()
    if not mechs:
        return []              # nothing taught: no read at all
    dimension = dimension or api.get("/state")["dimension"]
    mechs = [m for m in mechs if m["dimension"] == dimension]
    doors = on_the_way(mechs, here, there)
    press = press_for(mechs, doors[0], here) if doors else None
    if press is not None:
        press_mechanism(None, press, [list(c) for c in doors[0]])
    return [tuple(c) for m in mechs for c in m["opens"]]

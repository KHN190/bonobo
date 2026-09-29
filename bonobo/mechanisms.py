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
from .data import EYE_HEIGHT
from .world import Region, to_segment

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


def forget_door(dimension, opens, path=None):
    """Every lesson whose opens cells are `opens` removed (whatever its press): a door re-taught from scratch."""
    want = sorted(tuple(c) for c in opens)
    mechs = load(path)
    kept = [m for m in mechs if not (m["dimension"] == dimension and sorted(map(tuple, m["opens"])) == want)]
    save(kept, path)
    return len(mechs) - len(kept)


def in_dimension(dimension, path=None):
    return [m for m in load(path) if m["dimension"] == dimension]


# -- pure
def is_open(opens, solid):
    """Pure: every opens cell passable now (`solid`: the world's reading)."""
    return all(not solid(tuple(c)) for c in opens)


def centre(cells):
    return tuple(sum(c[i] for c in cells) / len(cells) for i in range(3))


def on_the_way(mechs, here, there, near=DOOR_NEAR):
    """Pure: the doors (their opens cells, a tuple) with a cell near the straight way here → there, nearest first."""
    doors = {tuple(map(tuple, m["opens"])) for m in mechs}
    hit = [d for d in doors if any(to_segment(c, here, there) <= near for c in d)]
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


def through_cell(door, there):
    """Pure: the cell one step past the door toward `there` — its foot cell moved along the axis `there` lies
    farthest on (the walk's third leg ends there: through, not round)."""
    foot = min(door, key=lambda c: c[1])
    c = centre(door)
    axis = max((0, 2), key=lambda i: abs(there[i] - c[i]))
    step = 1 if there[axis] > c[axis] else -1
    return tuple(foot[i] + (step if i == axis else 0) for i in range(3))


CROSS_TRIES = 2           # a pulse door (a button) may shut before the body is through: pressed again, once


def _walk(cell, range_, why):
    """One leg that breaks and builds nothing; its result said (detail), a refusal returned, never raised."""
    try:
        r = api.run({"type": "travel", "x": cell[0], "y": cell[1], "z": cell[2], "range": range_, "break": False,
                     "place": False, "voidBridge": False, "placeBudget": 0}, wait=30, awaits=why)
    except api.NotAvailable as e:          # "target unreachable": a shut door is a wall to the walker
        r = {"status": "failed", "message": str(e)}
    api.detail(f"   door leg to {tuple(cell)}: {r.get('status')} {r.get('message', '')}".rstrip())
    return r.get("status") == "succeeded"


def cross(press, door, stand, past):
    """A taught door in legs: onto `stand`, the cell before the door on our side (a pulse door must still be open
    when the body steps), press from there, straight through to `past`; shut again first → pressed once more."""
    _walk(stand, 0.5, "before the door: the use next")
    for n in range(CROSS_TRIES):
        try:
            how = press_mechanism(None, press, door)
        except api.NotAvailable as e:     # the jar's use refused: where we stood and how far, said
            st = api.get("/state")
            eye = (st["x"], st["y"] + EYE_HEIGHT, st["z"])
            api.detail(f"   door press {tuple(press)} refused from feet {(st['blockX'], st['blockY'], st['blockZ'])}"
                       f" (eye {math.dist(eye, [c + 0.5 for c in press]):.2f} from its centre): {e}")
            raise
        got = solid_map(door)
        api.detail(f"   door {tuple(map(tuple, door))}: {how}, read {'open' if is_open(door, lambda p: got[tuple(p)]) else 'shut'}"
                   f" (try {n + 1})")
        if _walk(past, 0.5, "through the door: read before the rest of the walk"):
            return True
    return False


def doors_on_way(here, there, policy=None, dimension=None):
    """nav's wire: a taught door on the way here → there is crossed in legs — into reach of our side's press and
    pressed (only when shut), then straight through with nothing broken, pressed again if it shut first; returns
    every taught opens cell, which the rest of the walk may cross but never dig."""
    mechs = load()
    if not mechs:
        return []              # nothing taught: no read at all
    dimension = dimension or api.get("/state")["dimension"]
    mechs = [m for m in mechs if m["dimension"] == dimension]
    doors = on_the_way(mechs, here, there)
    press = press_for(mechs, doors[0], here) if doors else None
    if press is not None and not cross(press, [list(c) for c in doors[0]], through_cell(doors[0], here),
                                       through_cell(doors[0], there)):
        # never an ordinary walk round a taught door: it would dig beside it (024258: the wall and comparator dug)
        raise api.NavFailed(f"door crossing failed at {doors[0][0]}: pressed from {press}, not through")
    return [tuple(c) for m in mechs for c in m["opens"]]

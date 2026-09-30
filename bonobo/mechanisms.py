"""Taught mechanisms: "press this cell and those cells open" (a button and its piston door). Taught per save by the
player (mc.py mech add), never derived and never written in code. The store is plain data; whether a door stands
open is always read off the world, never remembered. Two buttons of one door (outside, inside) are two entries with
the same opens cells.

    [{"dimension": ..., "press": [x, y, z], "opens": [[x, y, z], ...], "close": bool}]
"""
from __future__ import annotations

import json
import math
import os
from typing import NoReturn

from . import api, paths
from .knowledge import left
from .skill import skill
from .data import EYE_HEIGHT, home_box_of
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


def add(dimension, press, opens, close=False, path=None):
    """Teach one: a press cell and the cells it opens (the old lesson for the same press replaced); `close`: taught,
    shut it behind after crossing."""
    mechs = [m for m in load(path) if not (m["dimension"] == dimension and tuple(m["press"]) == tuple(press))]
    mechs.append({"dimension": dimension, "press": list(press), "opens": [list(c) for c in opens],
                  "close": bool(close)})
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


def crossing_axis(door, toward, solid=None):
    """Pure given `solid`: the axis a body crosses `door` along — a flat layer (a hatch) by y; a wall's door by the
    horizontal axis its cells do not run along; a single column by its walls: the horizontal axis open on both
    sides of its foot (`solid`: the world's reading), where `toward` lies only when the walls do not say."""
    ys, xs, zs = ({c[i] for c in door} for i in (1, 0, 2))
    if len(door) > 1 and len(ys) == 1:
        return 1
    if len(xs) > 1:
        return 2
    if len(zs) > 1:
        return 0
    if solid is not None:
        foot = min(door, key=lambda c: c[1])
        open_ = [a for a in (0, 2) if not any(solid(tuple(foot[i] + (s if i == a else 0) for i in range(3)))
                                              for s in (1, -1))]
        if len(open_) == 1:
            return open_[0]
    c = centre(door)
    return max((0, 2) if len(ys) > 1 else (0, 1, 2), key=lambda i: abs(toward[i] - c[i]))


def door_sides(door):
    """The world's reading of the cells either side of a door's foot (one read): what a column door's axis is
    decided by (crossing_axis)."""
    foot = min(door, key=lambda c: c[1])
    cells = [tuple(foot[i] + (s if i == a else 0) for i in range(3)) for a in (0, 2) for s in (1, -1)]
    got = solid_map(cells)
    return lambda c: got.get(tuple(c), False)


def side(door, p, axis):
    """Pure: which side of the door `p` is on along `axis`: +1, -1, or 0 (in its plane)."""
    d = p[axis] - centre(door)[axis]
    return (d > 0) - (d < 0)


def press_for(mechs, door, here, solid=None):
    """Pure: the press of `door` on our side (along the door's crossing axis), nearest; None when none is."""
    axis = crossing_axis(door, here, solid)
    ours = [tuple(m["press"]) for m in mechs if tuple(map(tuple, m["opens"])) == door
            and side(door, m["press"], axis) == side(door, here, axis) != 0]
    return min(ours, key=lambda p: math.dist(p, here), default=None)


def closes(mechs, door):
    """Pure: taught to be shut behind (any lesson of this door says so)."""
    return any(m.get("close") for m in mechs if tuple(map(tuple, m["opens"])) == door)


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


def passable_now(solid, open_door):
    """Pure: a cell passes when not solid, or when a door there stands open (it keeps its name open or shut)."""
    return lambda c: solid(c) and not open_door(c)


def solid_map(cells):
    """{cell: solid} over `cells`, one region read with block states (an open door is no wall)."""
    cells = [tuple(c) for c in cells]
    lo = tuple(min(c[i] for c in cells) for i in range(3))
    hi = tuple(max(c[i] for c in cells) for i in range(3))
    r = Region(lo, hi, props=True)
    shut = passable_now(r.solid, r.open_door)
    return {c: shut(c) for c in cells}


def opens_left(st, c):
    """`remaining` of press_mechanism: the opens cells passable in the region read ({} open)."""
    region, opens = st.get("region"), c.args[2]
    if region is None:
        return {"unread:mechanism": 1}
    shut = passable_now(region.solid, getattr(region, "open_door", lambda c: False))
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


def through_cell(door, there, solid=None):
    """Pure: where a body stands clear of the door on `there`'s side — along its crossing axis: a wall's door one
    step past its foot cell; a hatch, feet on it (above) or head under it (below), at its cell nearest `there`."""
    axis = crossing_axis(door, there, solid)
    step = side(door, there, axis) or 1
    if axis == 1:
        cell = min(door, key=lambda c: math.dist((c[0], c[2]), (there[0], there[2])))
        return (cell[0], cell[1] + 1 if step > 0 else cell[1] - 2, cell[2])
    foot = min(door, key=lambda c: c[1])
    return tuple(foot[i] + (step if i == axis else 0) for i in range(3))


CROSS_TRIES = 2           # a pulse door (a button) may shut before the body is through: pressed again, once
CROSS_RANGE = 0.5         # the through leg ends this near its cell: clear of the door, not in it


def _walk(cell, range_, why):
    """One leg that breaks and builds nothing; its result said (detail), a refusal returned, never raised."""
    try:
        r = api.run({"type": "travel", "x": cell[0], "y": cell[1], "z": cell[2], "range": range_, "break": False,
                     "place": False, "voidBridge": False, "placeBudget": 0}, wait=30, awaits=why)
    except api.NotAvailable as e:          # "target unreachable": a shut door is a wall to the walker
        r = {"status": "failed", "message": str(e)}
    api.detail(f"   door leg to {tuple(cell)}: {r.get('status')} {r.get('message', '')}".rstrip())
    return r.get("status") == "succeeded"


def _press(press, why):
    return api.run({"type": "use", "x": press[0], "y": press[1], "z": press[2]}, wait=30, awaits=why)


def close_behind(mechs, door, here):
    """After a crossing, a door taught `close` is shut from this side: its press here used, then read shut.
    No press on this side → left open, said. Returns True shut, False left open or still open."""
    press = press_for(mechs, tuple(map(tuple, door)), here, door_sides(door))
    if press is None:
        api.detail(f"   door {tuple(map(tuple, door))} left open: no press on this side")
        return False
    got = solid_map(door)
    if not is_open(door, lambda p: got[tuple(p)]):
        return True                        # shut already (a pulse door): nothing to press
    _press(press, "the door shut behind: read next")
    got = solid_map(door)
    shut = all(got[tuple(c)] for c in door)
    api.detail(f"   door {tuple(map(tuple, door))} closed behind from {press}: read {'shut' if shut else 'open'}")
    return shut


def cross(press, door, stand, past, close=False, mechs=()):
    """A taught door in legs: onto `stand`, the cell before the door on our side (a pulse door must still be open
    when the body steps), press from there, straight through to `past`; shut again first → pressed once more.
    Through, a door taught `close` is shut behind from the far side."""
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
        if _walk(past, CROSS_RANGE, "through the door: read before the rest of the walk"):
            if close:
                close_behind(mechs, door, past)
            return True
    return False


WALK_TO = None            # nav.go_to, wired by the brain: the walk outside a home to its door


def door_ends(door, solid=None):
    """Pure given `solid`: the cells a body stands in either side of `door` along its crossing axis (through_cell)."""
    axis = crossing_axis(door, centre(door), solid)
    c = centre(door)
    return tuple(through_cell(door, tuple(c[i] + (s * 8 if i == axis else 0) for i in range(3)), solid)
                 for s in (1, -1))


def home_exit_door(mechs, home, sides=None):
    """(door, its end inside the home, its end outside) of the home's exit: a taught door in its boxes with one side
    outside every box (the hatch); a door with both sides inside is the home's own, never a way out (11:01: the
    side room's door taken to leave, stand and through one cell). None when there is none. `sides(door)`: the
    world's reading round a column door (door_sides)."""
    for door in sorted({tuple(map(tuple, m["opens"])) for m in mechs}):
        if any(home_box_of(home, c) is None for c in door):
            continue
        a, b = door_ends(door, (sides or door_sides)(door))
        ins = [e for e in (a, b) if home_box_of(home, e) is not None]
        if len(ins) == 1:
            return door, ins[0], b if ins[0] == a else a
    return None


def _no_way(why) -> NoReturn:
    api.detail(f"   home: {why}")
    raise api.NavFailed(why)


def home_leg(mechs, home, here, there, policy):
    """A walk out of a home or into one goes by its taught door (in any of its boxes): to the door's side inside by
    a walk that digs nothing (across the home's boxes), pressed from our side (a shut door), through, shut behind
    when taught; entering, the outside leg first (the walker's own, digging allowed round the home). No taught door
    → no way (never dug)."""
    found = home_exit_door(mechs, home)
    if found is None:
        _no_way(f"no taught door out of the home {home} (one side outside it): never dug through")
    door, inside, outside = found
    walls = door_sides(door)
    leaving = home_box_of(home, here) is not None
    stand, past = (inside, outside) if leaving else (outside, inside)
    if stand == past:
        _no_way(f"the home's door {door[0]}: stand and through one cell {stand}")
    api.detail(f"   home: by the door {door[0]}: stand {stand}, through to {past}")
    if not leaving and WALK_TO is not None:
        WALK_TO(stand, policy, range_=CROSS_RANGE)
    got = solid_map(door)
    if is_open(door, lambda p: got[tuple(p)]):
        if not (_walk(stand, CROSS_RANGE, "before the open door") and _walk(past, CROSS_RANGE, "through it")):
            _no_way(f"through the home's open door {door[0]}: not through")
        return
    press = press_for(mechs, door, stand, walls)
    api.detail(f"   home: door {door[0]} shut, press on our side {press}")
    if press is None or not cross(press, [list(c) for c in door], stand, past, closes(mechs, door), mechs):
        _no_way(f"the home's door {door[0]}: " + ("no press on this side" if press is None
                                                  else f"pressed from {press}, not through"))


def home_exit(here, there, policy):
    """nav's wire (HOME_DOOR): no walk out of (into) the home that digs nothing: by the taught door of the home (any
    of its boxes) an end lies in (home_leg)."""
    homes = getattr(getattr(policy, "protected", None), "homes", ())
    home = next((h for h in homes if home_box_of(h, here) is not None), None) or \
        next((h for h in homes if home_box_of(h, there) is not None), None)
    if home is None:
        _no_way(f"no way to {tuple(there)} that digs no home")
    home_leg([m for m in load() if m["dimension"] == api.get("/state")["dimension"]], home, here, there, policy)


def doors_on_way(here, there, policy=None, dimension=None):
    """nav's wire: a taught door on the way here → there. Open (read off the world) → walked through, nothing
    pressed. Shut → crossed in legs from our side's press (cross), shut behind when taught `close`; no press on our
    side leaves it to the walk. Returns every taught opens cell, which the rest of the walk may cross, never dig."""
    mechs = load()
    if not mechs:
        return []              # nothing taught: no read at all
    dimension = dimension or api.get("/state")["dimension"]
    mechs = [m for m in mechs if m["dimension"] == dimension]
    cells = [tuple(c) for m in mechs for c in m["opens"]]
    doors = on_the_way(mechs, here, there)
    if not doors:
        return cells
    door = doors[0]
    got = solid_map(door)
    if is_open(door, lambda p: got[tuple(p)]):
        return cells           # open: walk through, press nothing (a press would shut it)
    walls = door_sides(door)
    press = press_for(mechs, door, here, walls)
    if press is not None and not cross(press, [list(c) for c in door], through_cell(door, here, walls),
                                       through_cell(door, there, walls), closes(mechs, door), mechs):
        # never an ordinary walk round a taught door: it would dig beside it (024258: the wall and comparator dug)
        raise api.NavFailed(f"door crossing failed at {door[0]}: pressed from {press}, not through")
    return cells

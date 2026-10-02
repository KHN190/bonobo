"""Door words: one family of taught-door rows (a wall door or a floor hatch) built from DOORS, its words and checks."""
import threading as _threading
import time

from ... import lifecycle as _lifecycle
from ..core import SetupInvalid
from .checks import BASE, _skill
from .scene import _row
from ...api import swallowed
from ...mechanisms import CROSS_RANGE     # through the door = within the crossing's own end range (one success)

# every door the bench builds: cells relative to the door's foot `at` (one copy each). `door`: the door's own blocks
# per cell (an iron door), None for a piston door (its cells built air, pushed in by the pistons); `blocks` in build
# order (support before what stands on it); `bulb`: the toggle, lit to shut a piston door; `presses`: outside first.
DOORS = {
    ("wall", "iron"): {
        "at": (4, 0, 0), "room": ((1, 0, -2), (3, 2, 2)), "floor": True,
        "door": ("iron_door[facing=east,half=lower]", "iron_door[facing=east,half=upper]"),
        "blocks": [("comparator[facing=north]", [(0, 1, -1)]), ("chest", [(3, 0, 0)])],
        "bulb": (0, 1, -2),
        "presses": [((-1, 1, -2), "oak_button[face=wall,facing=west]"), ((1, 1, -2), "oak_button[face=wall,facing=east]")],
        "points": {"outside": (-4, 0, 0), "inside": (2, 0, 0)}},
    ("wall", "piston"): {
        "at": (4, 0, 1), "room": ((1, 0, -1), (3, 2, 1)), "floor": True, "door": None,
        "blocks": [("sticky_piston[facing=south]", [(0, 0, -2), (0, 1, -2)]), ("repeater[facing=north]", [(0, 0, -3)]),
                   ("stone", [(-1, 0, -2), (-2, 0, -2)]), ("repeater[facing=west]", [(-1, 1, -2)]),
                   ("redstone_wire", [(0, 0, -4), (-2, 1, -2)]),
                   ("comparator[facing=west]", [(-1, 0, -4)]), ("comparator[facing=north]", [(-2, 0, -3)])],
        "bulb": (-2, 0, -4),
        "presses": [((-3, 0, -4), "oak_button[face=wall,facing=west]")],
        "points": {"outside": (-3, 0, 0), "inside": (2, 0, 0)}},
    ("hatch", "piston"): {
        "at": (1, -1, 0), "room": ((0, -2, 0), (2, -1, 3)), "floor": False, "ground": ((-5, -4, -3), (6, 0, 5)),
        "door": None,
        "blocks": [("stone", [(1, -2, 1), (1, -1, 0)]),
                   ("sticky_piston[facing=east]", [(-2, 0, 0), (-2, 0, 1)]),
                   ("sticky_piston[facing=west]", [(3, 0, 0), (3, 0, 1)]),
                   ("repeater[facing=west]", [(-3, 0, 0), (-3, 0, 1)]), ("repeater[facing=east]", [(4, 0, 0), (4, 0, 1)]),
                   ("redstone_wire", [(4, 0, 3), (5, 0, 3), (5, 0, 2), (5, 0, 1), (5, 0, 0), (4, 0, 4)]
                    + [(x, 0, 4) for x in range(3, -5, -1)] + [(-4, 0, z) for z in (3, 2, 1, 0)]),
                   ("comparator[facing=west]", [(3, 0, 3)])],
        "bulb": (2, 0, 3),
        "presses": [((2, 1, 3), "oak_button[face=floor,facing=north]"), ((2, -1, 3), "oak_button[face=ceiling,facing=north]")],
        "points": {"outside": (1, 1, -2), "inside": (2, -2, 2)}},
}
DOOR_S = 25


def _at(spec, off):
    return ("@",) + tuple(a + o for a, o in zip(spec["at"], off))


def door_parts(shape, door, presses):
    """Pure: the door's cells, box, bulb, presses and points from DOORS (every one '@'-relative)."""
    spec = DOORS[(shape, door)]
    steps = [(0, 0, 0), (0, 1, 0)] if shape == "wall" else [(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)]
    (rlo, rhi), down = spec["room"], 0 if spec["floor"] else 1
    if presses > len(spec["presses"]):
        raise ValueError(f"{shape} {door} door has {len(spec['presses'])} press(es), {presses} asked")
    return {"cells": [_at(spec, s) for s in steps],
            "room": (_at(spec, rlo), _at(spec, rhi)),
            "box": (_at(spec, (rlo[0] - 1, rlo[1] - down, rlo[2] - 1)), _at(spec, (rhi[0] + 1, rhi[1] + 1, rhi[2] + 1))),
            "bulb": _at(spec, spec["bulb"]),
            "presses": [(_at(spec, p), b) for p, b in spec["presses"][:presses]],
            "points": {k: _at(spec, p) for k, p in spec["points"].items()},
            "moves": spec["door"] is None}


def door_scene(shape, door, presses, start):
    """Pure: the scene words that build the door shut, the body at `start`."""
    spec, p = DOORS[(shape, door)], door_parts(shape, door, presses)
    ground = ([("floor",), ("fill", *p["box"], "stone")] if spec["floor"]
              else [("fill", _at(spec, spec["ground"][0]), _at(spec, spec["ground"][1]), "stone")])
    cells = ([("setblock", c, "air") for c in p["cells"]] if spec["door"] is None
             else [("setblock", c, b) for c, b in zip(p["cells"], spec["door"])])
    blocks = [("setblock", _at(spec, o), b) for b, offs in spec["blocks"] for o in offs]
    shut_lit = spec["door"] is None           # a piston door shuts on power, an iron door opens on it
    return (ground + [("fill", *p["room"], "air")] + cells + blocks
            + [("setblock", p["bulb"], "waxed_copper_bulb[lit=false,powered=false]")]
            + [("setblock", c, b) for c, b in p["presses"]]
            + ([("setblock", p["bulb"], "waxed_copper_bulb[lit=true,powered=false]")] if shut_lit else [])
            + [("stand",) + tuple(p["points"][start][1:]), ("cmd", "clear @p")])


def door_row(name, shape, door, presses, close, taught, start, goal, back):
    """A shut door, `presses` buttons (taught or not, `close`: shut again behind) → from `start` to `goal` (and
    `back`); judged: where the body ends, the door's end state, the box standing, the door seen open."""
    p = door_parts(shape, door, presses)
    to, home = p["points"][goal], p["points"][start]
    end_state = "shut" if close or not taught else "open"
    keep = p["cells"] if p["moves"] else []
    check = ([("arrived", home if back else to, CROSS_RANGE), ("door_state", p["cells"], end_state),
              ("unchanged", *p["box"], keep), ("door_seen",)] if taught else
             [("not", ("!arrived", to, CROSS_RANGE)), ("door_state", p["cells"], "shut"), ("unchanged", *p["box"], keep)])
    doc = (f"A shut {door} {shape} door, {presses} button(s) {'taught' + (' (close)' if close else '') if taught else 'NOT taught (must fail to pass)'}"
           f" → {start} to {goal}{' and back' if back else ''}; door {end_state} at the end, nothing dug")
    return _row(name, doc, "skills", door_scene(shape, door, presses, start),
                ("walk", to, CROSS_RANGE, home if back else None), check, budget=DOOR_S,
                before=[("teach", [c for c, _b in p["presses"]] if taught else [], p["cells"], close)],
                skills=["press_mechanism"] if taught else [], stochastic=False, tier_fixed="common",
                tags={"base": "door"}, expect=[(p["bulb"], p["bulb"], "waxed_copper_bulb", 1, 1)])


# a watcher per row notes the door read open at some sample of the run (pressed, not dug)
DOOR_SEEN: dict = {}
_lifecycle.on_reset(lambda: DOOR_SEEN.clear(), covers=("DOOR_SEEN",))
DOOR_WATCH_S = 30


def teach(presses, cells, close):
    """before: each press taught to open `cells` (`close`: shut again behind); no presses → nothing taught."""
    def hook(ctx):
        from ... import mechanisms as mech
        from ...api import McError
        from ...world import Region
        name = BASE["name"]
        for press in presses:
            r = Region(press, press, props=True)
            if "powered" not in r.props.get(tuple(press), {}):
                raise SetupInvalid(f"taught press {tuple(press)} is {r.name(tuple(press))}: nothing to press")
            mech.learn("minecraft:overworld", press, cells, close=close)
        DOOR_SEEN[name] = False
        end = time.time() + DOOR_WATCH_S

        def watch():
            while time.time() < end and DOOR_SEEN.get(name) is False:     # gone: the next row began
                try:
                    if not all(mech.solid_map(cells).values()):
                        DOOR_SEEN[name] = True
                except McError as e:
                    swallowed("door.watch", e)
                time.sleep(0.1)
        _threading.Thread(target=watch, daemon=True).start()
    return hook


def walk(goal, range_, back=None):
    """run: to `goal`, then to `back` when given; a walk that cannot ends there (the check judges the world)."""
    def run(ctx):
        from ...api import McError
        try:
            got = _skill("travel_to")(ctx, goal, range_)
            return got if back is None else _skill("travel_to")(ctx, back, range_)
        except McError as e:
            swallowed("door.run", e)
            return None
    return run


def door_state(cells, want):
    """check: the door cells read `want` — "shut" every cell solid, "open" none."""
    def check(api, inv):
        from ... import mechanisms as mech
        solid = list(mech.solid_map(cells).values())
        return all(solid) if want == "shut" else not any(solid)
    return check


def unchanged(lo, hi, cells):
    """check: every wall and roof block of the box standing, bar `cells` (a piston door's own, judged by door_state)."""
    skip = {tuple(c) for c in cells}
    shell = [(x, y, z) for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1) for z in range(lo[2], hi[2] + 1)
             if not (lo[0] < x < hi[0] and y < hi[1] and lo[2] < z < hi[2]) and (x, y, z) not in skip]

    def check(api, inv):
        from ...world import Region
        r = Region(lo, hi)
        return all(r.name(p) not in ("air", "cave_air") for p in shell)
    return check


def door_seen():
    """check: the door read open at some sample while the row ran (pressed, not dug)."""
    return lambda api, inv: DOOR_SEEN.get(BASE.get("name"), False)


TEMPLATES = {"door": door_row}
NAMES = {"door": lambda name, *p: name}

__all__ = ['DOORS', 'DOOR_S', 'DOOR_SEEN', 'DOOR_WATCH_S', 'door_parts', 'door_row', 'door_scene',
           'door_seen', 'door_state', 'teach', 'unchanged', 'walk']

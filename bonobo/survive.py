"""Staying alive: light, food, water and bridges, burrows and digging out, air, sleep, shelter."""
from __future__ import annotations

import math
import time
from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import events
from . import api, beliefs, nav
from .api import McError, NotAvailable, log
from .skill import ANCHORS, skill, current as current_call
from .data import (BED_BOX, BED_REACH, SLEEP_BLOCKERS, SLEEP_BLOCKERS_ANGRY, TORCH_LIGHT, BASE_MARKERS, FULL_BAR, GROUPS, NUTRITION, PLACEABLE_AS, POD_BLOCKS, bare, mid, DAY_END, NIGHT_END,
                   DAY_TICKS, DAYLIT_SKY, EYE_HEIGHT, SPAWN_BLOCK_LIGHT, WALK_BLOCKS_PER_S, MAX_HP, critical_hp)
from .knowledge import RAW_MEAT, ALL_FOOD
from .world import Inventory, Region, add, dark_spots, entities, find
from .bag import throw_direction
from .terrain import (choose_burrow, choose_exit, air_route, is_enclosed, openings, find_open_spot, SOFT_RADIUS,
                      nearest_soft)
from .skillcore import free_spots_here, place, mine_cell, settle, body_state, head_buried, head_underwater
from .world import feet
from .fluids import AIR_FULL, swimming
from .craft import run_split
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .shapes import BodyState, EatTask, PlaceTask, Task

@skill(gives=["state:open_room"], remaining=lambda st, c: open_room_left(st, c), needs={}, budget=120, stall=45,
       verify=lambda c: c.result is not None and math.dist(feet(), c.result) <= 2
       and len(free_spots_here(limit=2)) >= 2,
       provides={"reach:open": lambda ctx, s: ()})
def move_to_open_space(ctx):
    """Full bag in a shaft or tunnel: walk to the nearest spot with room to throw and to put down a chest."""
    x, y, z = feet()
    spot = find_open_spot(Region((x - 12, y - 6, z - 12), (x + 12, y + 8, z + 12)), (x, y, z))
    if spot is None:
        raise NotAvailable("no open space within 12 blocks")
    if spot == (x, y, z):
        return spot
    log(f"   moving to open space at {spot} to sort the inventory")
    if not nav.arrived(spot, ctx.policy, range_=0.8, attempts=2):
        raise api.NavFailed(f"open space at {spot} not reachable")
    yield feet()
    return spot

def _night_policy(ctx):
    import dataclasses
    return dataclasses.replace(ctx.policy, allow_surface=False)

def require_pickaxe_ok():
    return any(_k.working(d) for _, d, _ in Inventory().tools("pickaxe"))

# -- light, food, night

def dark_here(s):
    """Pure over /state: standing where mobs spawn — block light 0, and not under open sky by day."""
    return "blockLight" in s and s["blockLight"] <= SPAWN_BLOCK_LIGHT and \
        not (s["skyLight"] > DAYLIT_SKY and 0 < s["timeOfDay"] < DAY_END)

LIGHT_R = 4                # blocks round the feet a lighting looks over
LIGHT_FIRST = 4            # torches placed where we start work in the dark: lit first, then one a segment
OPEN_DARK_SPOTS = 4        # dark floor cells within LIGHT_R that make an open dark area: a 1-wide shaft's floor is
                           # the body's own cell, a 2-high tunnel's is LIGHT_R cells each way

def light_due(s, sealed, dark_spots_near):
    """Pure: light before working here — under rock, standing dark, not sealed in (a night hole), and open (a tunnel,
    a cave, a vein, a base; not a short shaft). Upkeep, not danger: darkness alone never raises a threat."""
    return (_k.under_rock(s.get("skyLight", 15)) and dark_here(s) and not sealed
            and dark_spots_near >= OPEN_DARK_SPOTS)

def torch_commands(state: "BodyState", args=(4, 1)) -> "list[PlaceTask]":
    """Pure: place tasks for up to `limit` of the darkest floor spots within `radius`, never the body's cells; [] when none."""

    radius, limit = (tuple(args) + (4, 1)[len(args):])[:2]
    s = state["state"]
    if state["inv"].usable("minecraft:torch") == 0:   # a torch in the offhand can't be placed by tasks
        return []
    here = state["feet"]
    spots = [p for p in state.get("spots") or ()
             if math.dist((p["x"], p["y"], p["z"]), here) <= radius
             and not (p["y"] in (here[1], here[1] + 1) and abs(p["x"] + 0.5 - s["x"]) < 0.8
                      and abs(p["z"] + 0.5 - s["z"]) < 0.8)]
    return [{"type": "place", "item": "minecraft:torch", "x": p["x"], "y": p["y"], "z": p["z"]}
            for p in spots[:limit]]

def bites_to_full(food, carried, raw_ok=False):
    """Pure: (item, bites) to fill the bar from `food`: the best fit for the gap, raw meat only when `raw_ok`."""

    gap = FULL_BAR - food
    allowed = [f for f in ALL_FOOD + (RAW_MEAT if raw_ok else []) if carried.get(f, 0) > 0]
    if gap <= 0 or not allowed:
        return None, 0
    points = lambda f: NUTRITION[f.split(":")[-1]]      # noqa: E731
    fits = [f for f in allowed if points(f) <= gap]
    item = max(fits, key=points) if fits else min(allowed, key=points)
    return item, math.ceil(gap / points(item))

def bite_plan(food, carried, raw_ok=False):
    """Pure: the bites, in order, to fill the bar from `food`, no more of an item than is carried."""

    left, out = dict(carried), []
    while True:
        item, _n = bites_to_full(food, left, raw_ok)
        if item is None:
            return out
        out.append(item)
        left[item] -= 1
        food = min(FULL_BAR, food + NUTRITION[item.split(":")[-1]])

def eat_commands(state: "BodyState", args) -> "list[EatTask]":
    """`commands` for eat: one eat task per planned bite, back to back (one bite a round left the bar hungry)."""

    raw_ok = bool(args[0]) if args else False
    inv = state["inv"]
    carried = {f: inv.count(f) for f in ALL_FOOD + RAW_MEAT}
    return [{"type": "eat", "item": item}
            for item in bite_plan(state["state"].get("food", 0), carried, raw_ok)]

def _fed_as_planned(c):
    """The bar rose by the points of the bites eaten (capped at a full bar)."""
    target = c.result
    return isinstance(target, int) and not isinstance(target, bool) and api.get("/state")["food"] >= target

# needs: none the bag can state — a cooked meal, or raw meat when starving
@skill(gives=["state:fed"], remaining=_k.fed, needs={}, start=lambda c: api.get("/state")["food"], verify=_fed_as_planned,
       commands=lambda state, args: eat_commands(state, args), budget=30, stall=30,
       provides={"eat": lambda ctx, s: (bool(s.detail.get("raw_ok")),)})
def eat(ctx=None, raw_ok=False):
    """Eat until the bar is full, every planned bite as one chain; an interruption stops it where it is."""

    st = body_state(ctx) if ctx is not None else cast("BodyState", {"state": api.get("/state"), "inv": Inventory()})
    tasks = eat_commands(st, (raw_ok,))
    carried = {f: st["inv"].count(f) for f in ALL_FOOD + (RAW_MEAT if raw_ok else [])}
    if not tasks:
        if any(carried.values()):
            return False                    # full: nothing to eat for
        raise NotAvailable("nothing edible carried" + ("" if raw_ok else " (raw meat not allowed: not starving)"))
    food = st["state"].get("food", 0)
    target = min(FULL_BAR, food + sum(NUTRITION[str(t.get("item", "")).split(":")[-1]] for t in tasks))
    started = time.time()
    api.run_chain(tasks, stop_on_failure=True)
    for t in tasks:
        events.ate(str(t.get("item", "")).split(":")[-1], t=started)     # no clock read of its own
    return target

def _on_land():
    return not swimming(api.get("/state"))

@skill(gives=["state:footing"], remaining=_k.standing, needs={"building": 1}, done=lambda c: bool(api.get("/state").get("onGround")), budget=30, stall=20,
       provides={"reach:footing": lambda ctx, s: ()})
def stand_on_a_block(ctx):
    """Footing, made rather than travelled to: one block under the feet."""

    block = nav.building_item()
    if not block:
        raise NotAvailable("nothing to stand on and nothing to place")
    x, y, z = feet()
    place(block, (x, y - 1, z))
    return bool(api.get("/state").get("onGround"))

BRIDGE_REACH = 12      # cells one bridge call lays toward its target

BRIDGE_CLIMB = 4       # blocks one call pillars up toward a target above the feet

def bridge_region(feet_, target):
    """The box `bridge_commands` reads: the lane toward the target, its floor and the climb above it."""
    x, y, z = feet_
    tx = x + max(-BRIDGE_REACH, min(BRIDGE_REACH, target[0] - x))
    tz = z + max(-BRIDGE_REACH, min(BRIDGE_REACH, target[2] - z))
    return Region((min(x, tx) - 1, y - 2, min(z, tz) - 1), (max(x, tx) + 1, y + BRIDGE_CLIMB + 2, max(z, tz) + 1))

def bridge_commands(state, args) -> "list[Task]":
    """Pure: a way made toward `args[0]`: pillar up if above, then cell by cell — dig feet and head, lay a floor, step on."""

    target, region, inv, protected = args[0], state["region"], state["inv"], state["protected"]
    block = next((b for b in GROUPS["building"] if inv.count(b)), None)
    if block is None:
        return []
    x, y, z = state["feet"]
    tasks = []
    for _ in range(max(0, min(BRIDGE_CLIMB, int(target[1]) - y))):
        tasks.append({"type": "pillar", "item": block})
        y += 1
    for _ in range(BRIDGE_REACH):
        dx, dz = int(target[0]) - x, int(target[2]) - z
        if dx == 0 and dz == 0:
            break
        if abs(dx) >= abs(dz):
            x += 1 if dx > 0 else -1
        else:
            z += 1 if dz > 0 else -1
        for cell in ((x, y, z), (x, y + 1, z)):
            if region.solid(cell):
                if cell in protected:
                    return tasks
                tasks.append(nav.mine_task(cell))
        if not region.solid((x, y - 1, z)):
            tasks.append({"type": "place", "item": block, "x": x, "y": y - 1, "z": z})
        tasks.append({"type": "goto", "x": x, "y": y, "z": z, "range": 0.5, "partial": True})
    return tasks

def _bridged_nearer(c):
    target = c.args[1]
    return math.dist(feet(), target) < math.dist(c.base, target) - 1

@skill(gives=["state:bridged"], remaining=_k.near(lambda c: c.args[1], lambda c: BRIDGE_REACH), needs={"building": 1}, start=lambda c: feet(), verify=_bridged_nearer, commands=bridge_commands, budget=120, stall=45)
def bridge_toward(ctx, target):
    """Path blocked: make the way toward `target` by hand instead of asking the walker again."""

    target = tuple(target)
    tasks = bridge_commands(body_state(ctx, bridge_region(feet(), target)), (target,))
    if not tasks:
        raise NotAvailable("path blocked and nothing to bridge with")
    api.run_chain(tasks, stop_on_failure=True, before_segment=ctx.policy.before_segment)
    return feet()

@skill(gives=["state:ashore"], remaining=_k.on_dry_ground, needs={}, done=lambda c: _on_land(), budget=180, stall=45, provides={"reach:land": lambda ctx, s: ()})
def reach_land(ctx):
    """Night in the water: swim (or boat) to the nearest dry standing spot first; shelters are made from land."""

    x, y, z = feet()
    # the land a swim reaches (terrain.air_route), not the nearest dry block (once behind a tank wall)
    route = air_route(Region((x - 24, y - 6, z - 24), (x + 24, y + 10, z + 24)), (x, y, z))
    if route is None or route[0] != "land":
        raise NotAvailable(route[2] if route else "no water to swim through and no land within 24 blocks")
    land = route[1]
    api.run({"type": "goto", "x": land[0], "y": land[1], "z": land[2], "range": nav.ASHORE_RANGE, "partial": True,
             "useBoat": True}, wait=120, awaits="ashore or not (nav.ashore) decides the climb out")
    # judged by where the body is: the walker calls a body beside the bank arrived
    if not nav.ashore(api.get("/state"), land) and not nav.climb_out(land):
        # the walker can't climb out: dig or pillar out instead
        nav.arrived(land, ctx.policy, range_=nav.ASHORE_RANGE, attempts=1)
        if not nav.ashore(api.get("/state"), land):
            raise api.NavFailed(f"land at {land} not reachable")
    yield feet()

def _burrow_here(ctx):
    """A solid hillside beside the body to tunnel into (terrain.choose_burrow), or None."""
    x, y, z = feet()
    return choose_burrow(Region((x - 4, y - 2, z - 4), (x + 4, y + 3, z + 4)), (x, y, z), ctx.policy.protected)

def burrow_anchor(state, args=()):
    """Pure: {"anchor": (feet, direction)} a burrow fixes at its first start."""

    x, y, z = state["feet"]
    d = args[0] if args else choose_burrow(state["region"], (x, y, z), state["protected"])
    if d is None:
        raise NotAvailable("no solid hillside to burrow into here")
    return {"anchor": ((x, y, z), tuple(d))}

ANCHORS["burrow"] = burrow_anchor

def burrow_commands(state, args=()):
    """Pure: the burrow as one chain — two cells into the hillside, a step to the end, the entrance sealed behind."""

    (x, y, z), (dx, dz) = (state.get("anchor") or burrow_anchor(state, args)["anchor"])
    block = next((b for b in GROUPS["building"] if state["inv"].usable(b)), None)
    if block is None:
        raise NotAvailable("no blocks to seal the burrow")
    region = state.get("region")
    end = (x + dx * 2, y, z + dz * 2)
    seal = [{"type": "place", "item": block, "x": x + dx, "y": y + dy, "z": z + dz} for dy in (0, 1)]
    if region is not None and tuple(state["feet"]) == end:
        # inside at the end: only entrance cells still open are sealed (a sealed one is never dug again)
        return [t for t in seal if not region.solid((t["x"], t["y"], t["z"]))]
    # only what still stands: resumed, the chain is rebuilt from the world
    dig = [nav.mine_task(c) for c in ((x + dx * k, y + dy, z + dz * k) for k in (1, 2) for dy in (1, 0))
           if region is None or region.solid(c)]
    return dig + [{"type": "goto", "x": end[0], "y": end[1], "z": end[2], "range": 0.4, "partial": False}] + seal

@skill(gives=["state:sheltered"], needs={"tool:pickaxe:0": 1}, remaining=lambda st, c: shelter_left(st, c), done=lambda c: enclosed(), budget=90, stall=40, commands=lambda st, a: burrow_commands(st, a),
       provides={"state:sheltered": lambda ctx, s: () if _burrow_here(ctx) else None,
                 "shelter:burrow": lambda ctx, s: ()})
def burrow(ctx):
    """Night shelter in a hillside: tunnel 2 in, step to the end, seal the entrance (both faces visible from inside), one chain."""

    call = current_call()
    assert call is not None, "a skill body runs inside its runner"
    keep = call.keep
    if "anchor" not in keep:
        d = _burrow_here(ctx)
        if d is None:
            raise NotAvailable("no solid hillside to burrow into here")
        keep["anchor"] = (feet(), d)
    (x, y, z), d = keep["anchor"]
    run_split(burrow_commands(body_state(ctx, Region((x - 4, y - 2, z - 4), (x + 4, y + 3, z + 4)),
                                         anchor=keep["anchor"])), wait=60)
    yield feet()
    log(f"burrowed into the hillside at {feet()}")

def dig_out_commands(state, args=()):
    """Pure: out of a sealed pod as one chain — the exit side's cells (terrain.choose_exit), then a step out."""

    exit_ = choose_exit(state["region"], tuple(state["feet"]), state["protected"])
    if exit_ is None:
        raise NotAvailable("no safe side to dig out of")
    cells, out = exit_
    return [nav.mine_task(c) for c in cells] + [{"type": "goto", "x": out[0], "y": out[1], "z": out[2],
                                                 "range": 0.6, "partial": True}]

@skill(gives=["state:outside"], remaining=lambda st, c: outside_left(st, c), needs={}, done=lambda c: not enclosed(), budget=90, stall=45, commands=lambda st, a: dig_out_commands(st, a),
       provides={"reach:outside": lambda ctx, s: ()})
def dig_out(ctx):
    """Morning in a sealed pod: open one side (by hand if no pickaxe) and step out, one chain."""

    x, y, z = feet()
    tasks = dig_out_commands(body_state(ctx, Region((x - 3, y - 2, z - 3), (x + 3, y + 3, z + 3))))
    run_split(tasks, wait=60)
    yield feet()
    log(f"dug out of the shelter toward {tuple(tasks[-1][k] for k in 'xyz')}")

@skill(gives=["state:head_clear"], remaining=_k.head_clear, needs={}, done=lambda c: not head_buried(), budget=30, stall=15)
def unbury(ctx):
    """Suffocating in a block: step out to a free side cell first; else break the block at eye level (then the one
    above it if sand/gravel keeps falling). A home block is broken only at critical hp, said as an event (api)."""
    for _ in range(4):
        s = api.get("/state")
        here = (s["blockX"], s["blockY"], s["blockZ"])
        out = step_out_cell(Region(add(here, (-1, -1, -1)), add(here, (1, 2, 1))), here)
        if out is not None:
            api.run({"type": "goto", "x": out[0] + 0.5, "y": out[1], "z": out[2] + 0.5, "range": 0.5},
                    wait=10, awaits="the head read again after the step out")
            yield out
            continue
        eye = (s["blockX"], math.floor(s["y"] + EYE_HEIGHT), s["blockZ"])
        if eye not in ctx.policy.protected:
            api.run(nav.mine_task(eye), wait=15, awaits="the eye cell read again (sand keeps falling)")
        elif s.get("health", MAX_HP) <= critical_hp(s):
            with api.home_break_allowed(f"buried at {s.get('health')} hp"):
                api.run(nav.mine_task(eye), wait=15, awaits="the eye cell read again (sand keeps falling)")
        else:
            raise NotAvailable(f"buried in a home block at {eye}, no side to step out to: not broken above critical hp")
        yield eye


def step_out_cell(region, feet_at):
    """Pure: a side cell the body can stand in — feet and head free, a floor under it — or None."""
    x, y, z = feet_at
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        c = (x + dx, y, z + dz)
        if all(region.inside(p) for p in (c, add(c, (0, 1, 0)), add(c, (0, -1, 0)))) \
                and not region.solid(c) and not region.solid(add(c, (0, 1, 0))) and region.solid(add(c, (0, -1, 0))):
            return c
    return None

BREATH_HOLD_S = 2.0     # the head out of the water this long, lungs full: breathing, not a surfacing that sinks back

BREATH_WAIT_S = 8.0     # how long the verify watches for that (lungs refill in about 4 s)

def breathed(samples):
    """Pure: the head has been out unbroken for BREATH_HOLD_S up to the last sample, which reads full lungs."""

    if not samples:
        return False
    t_end, under, air = samples[-1]
    if under or air < AIR_FULL:
        return False
    out_since = t_end
    for t, under, _ in reversed(samples):
        if under:
            break
        out_since = t
    return t_end - out_since >= BREATH_HOLD_S

def _breathing():
    """Watch the body up to BREATH_WAIT_S: True once `breathed`, False the moment the head goes back under."""
    samples, end = [], time.time() + BREATH_WAIT_S
    while True:
        s = api.get("/state")
        samples.append((time.time(), head_underwater(s), s.get("air", AIR_FULL)))
        if breathed(samples):
            return True
        if samples[-1][1] or time.time() >= end:
            return False
        time.sleep(0.25)

def _breathing_now():
    """One reading: head out with full lungs (`done` must not wait; the 2 s hold is the verify's)."""

    s = api.get("/state")
    return not head_underwater(s) and s.get("air", AIR_FULL) >= AIR_FULL

@skill(gives=["state:air"], remaining=_k.breathing, needs={}, done=lambda c: _breathing_now(), verify=lambda c: _breathing(), budget=45, stall=12,
       provides={"reach:air": lambda ctx, s: ()})
def find_air(ctx):
    """Out of breath underwater: swim to the nearest dry cell (surfacing in place sank back), else a block at the surface, else dig the cap."""

    for _ in range(4):
        x, y, z = feet()
        region = Region((x - 8, y - 2, z - 8), (x + 8, y + 16, z + 8))
        route = air_route(region, (x, y + 1, z))
        if route is None:
            raise NotAvailable("no air within reach: no land, no surface, no cap to dig")
        kind, c, why = route
        if why:
            log(f"   find_air: {why}")
        if kind == "dig":
            api.run(nav.mine_task(c), wait=15, awaits="the air route read again after each step")
        else:
            api.run({"type": "goto", "x": c[0], "y": c[1], "z": c[2], "range": 0.5, "partial": True,
                     "useBoat": False}, wait=20, awaits="the air route read again after each step")
            if kind == "land":
                nav.climb_out(c)                   # beside the rim or the shore: onto it
            if kind == "pillar" and nav.building_item():
                fx, fy, fz = feet()
                place(nav.building_item(), (fx, fy - 1, fz))
        yield kind

BED_ROOM_REACH = 3        # a bed is placed this far at most (trySleep's reach along x and z)

def bed_cells(feet, dist, d, dy):
    """Pure: (foot, head) of a bed `dist` along direction `d` (dx, dz) from the feet, `dy` up — the head away from us."""
    foot = (feet[0] + dist * d[0], feet[1] + dy, feet[2] + dist * d[1])
    return foot, (foot[0] + d[0], foot[1], foot[2] + d[1])

def bed_room(region, foot, head):
    """Pure: a bed fits at foot/head — both cells air on a solid floor, neither cell above them burying a head
    (trySleep's isBedObstructed)."""
    return all(region.name(c) == "air" and region.solid(add(c, (0, -1, 0))) and not region.buries(add(c, (0, 1, 0)))
               for c in (foot, head))

def bed_room_tasks(region, feet, protected, places, inv):
    """Pure: (tasks, (foot, head), seconds, why) of the cheapest room for a bed within BED_ROOM_REACH — an existing
    one (no tasks), else its cells and the cells above them opened top down and a missing floor placed
    (nav.open_tasks), priced by nav.way_s; tasks None (and why) when no room can be made here."""
    best, why = None, "no bed room within reach"
    for dist in range(1, BED_ROOM_REACH + 1):
        for d in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            for dy in (0, 1, -1):
                foot, head = bed_cells(feet, dist, d, dy)
                if foot in protected or head in protected:
                    continue                 # a carried bed is never placed in a home: its own beds are used
                if region.inside(add(head, (0, 1, 0))) and bed_room(region, foot, head):
                    return [], (foot, head), 0.0, None
                cells = [foot, head, add(foot, (0, 1, 0)), add(head, (0, 1, 0))]
                if not all(region.inside(c) for c in cells + [add(foot, (0, -1, 0)), add(head, (0, -1, 0))]):
                    continue
                tasks, got = nav.open_tasks(region, cells, [add(foot, (0, -1, 0)), add(head, (0, -1, 0))], feet,
                                            protected, list(places))
                if tasks is None:
                    why = got
                    continue
                seconds = nav.way_s(region, feet, tasks, inv)
                if best is None or seconds < best[2]:
                    best = (tasks, (foot, head), seconds)
    return (best + (None,)) if best is not None else (None, None, None, why)


def sleep_gate(state, foot, head, region, hostiles):
    """Pure: why vanilla refuses this bed now (1.21.11 ServerPlayerEntity.trySleep, in its order), None when it
    lets us sleep: the dimension and the night (can_sleep), the reach (BED_REACH of either half's bottom centre), the
    cells above the bed (isBedObstructed), a monster within BED_BOX of it — any SLEEP_BLOCKERS, through walls, a
    zombified piglin only when angry."""
    why = can_sleep(state)
    body = (state["x"], state["y"], state["z"])
    centre = lambda c: (c[0] + 0.5, c[1], c[2] + 0.5)     # noqa: E731
    within = lambda p, c, r: all(abs(p[i] - centre(c)[i]) <= r[i] for i in range(3))     # noqa: E731
    if why and "explodes" in why:
        return why
    if not (within(body, foot, BED_REACH) or within(body, head, BED_REACH)):
        return "too far from the bed"
    if region.buries(add(foot, (0, 1, 0))) or region.buries(add(head, (0, 1, 0))):
        return "the bed is obstructed"
    if why:
        return why
    for e in hostiles:
        if (e["type"] in SLEEP_BLOCKERS or e["type"] in SLEEP_BLOCKERS_ANGRY and e.get("angry")) \
                and within((e["x"], e["y"], e["z"]), foot, BED_BOX):
            return f"a {bare(e['type'])} within the bed's box (8/5/8) at {(round(e['x']), round(e['y']), round(e['z']))}"
    return None

def torch_cover(dark):
    """Pure: the fewest torch cells (of the dark spots themselves) that bring every dark spot to block light > 0 —
    a torch lights a cell TORCH_LIGHT − manhattan distance (greedy cover)."""
    left = [tuple(c) for c in dark]
    lit = lambda t, c: TORCH_LIGHT - sum(abs(t[i] - c[i]) for i in range(3)) > 0     # noqa: E731
    out = []
    while left:
        best = max(left, key=lambda t: (sum(lit(t, c) for c in left), -left.index(t)))
        out.append(best)
        left = [c for c in left if not lit(best, c)]
    return out

def light_pays(dark_n, floor_n, night_left_s, torch_s):
    """Pure: torches round the bed pay when what they cost is less than the night a monster in the box would keep
    us awake for, weighed by the share of the box's floor that can spawn one (dark_n of floor_n)."""
    return dark_n > 0 and floor_n > 0 and torch_s < night_left_s * dark_n / floor_n

# when a bed works (Mojang's rule): outside it using a bed does nothing
SLEEP_FROM_TICKS, SLEEP_TO_TICKS = 12541, 23458

MORNING_S = 7.0          # how long a lain-in bed is read for the morning (the night skipped in ~5 s)

def _morning(timeout=MORNING_S):
    """Lain in a bed: read the clock until it is morning (the night skipped, ~5 s), at most `timeout` s."""
    day = lambda t: int(t) % DAY_TICKS < 12500      # noqa: E731
    return day(settle(lambda: api.get("/state")["timeOfDay"], day, timeout=timeout, stable_s=0.0, soft=True))

def can_sleep(state):
    """None when a bed would work now, else why it would not."""

    if state.get("dimension", "minecraft:overworld") != "minecraft:overworld":
        return "a bed explodes outside the Overworld"
    if state.get("thundering"):
        return None
    t = int(state.get("timeOfDay", 0)) % DAY_TICKS
    if SLEEP_FROM_TICKS <= t <= SLEEP_TO_TICKS:
        return None
    return "a bed only works at night (or in a thunderstorm)"

def _day_now():
    t = int(api.get("/state")["timeOfDay"]) % DAY_TICKS
    return not DAY_END <= t <= NIGHT_END

DAY_WAIT_TICKS = 200      # one wait while sitting the night out

@skill(gives=["state:day"], remaining=_k.daytime, needs={}, done=lambda c: _day_now(), budget=600, stall=60, provides={"wait:day": lambda ctx, s: ()})
def wait_for_day(ctx):
    """Sit the night out where we are, in ten-second waits, until the sun is up."""
    t = int(api.get("/state")["timeOfDay"]) % DAY_TICKS
    # bound: the night left on the clock plus one wait
    deadline = time.time() + ((NIGHT_END - t) % DAY_TICKS + DAY_WAIT_TICKS) / beliefs.TICKS_PER_S
    while not _day_now():
        if time.time() > deadline:
            raise NotAvailable("the night outlasted its clock (daylight cycle stopped?)")
        api.run({"type": "wait", "ticks": DAY_WAIT_TICKS}, wait=15, awaits="the time of day")
        yield api.get("/state")["timeOfDay"]

# needs: none the bag can state — a bed carried or one standing nearby
@skill(gives=["state:day"], remaining=_k.daytime, needs={}, verify=lambda c: _k.daytime({"state": api.get("/state")}, c) == {}, budget=240, stall=60,
       provides={"sleep": lambda ctx, s: (_night_policy(ctx),)})
def sleep(ctx, night_policy):
    """Sleep through the night: the home's bed when it is within reach of the night (HOME_BED_R), else a carried bed
    (placed next to us, picked up after), else a nearby site bed."""
    s = api.get("/state")
    why = can_sleep(s)
    if why:
        raise NotAvailable(why)
    home = ctx.mem.home_part("beds", s["dimension"], feet(), anywhere=True) if getattr(ctx, "mem", None) else None
    if home is not None and math.dist(home, feet()) <= HOME_BED_R:
        return _sleep_in(ctx, home, night_policy, "home bed")
    inv = Inventory()
    bed = next((b for b in GROUPS["bed"] if inv.count(b)), None)
    if bed:
        return _sleep_carried(ctx, bed, s, inv)
    beds = find(BASE_MARKERS["bed"], radius=HOME_BED_R, limit=1)
    if not beds:
        raise NotAvailable("no bed carried or nearby")
    return _sleep_in(ctx, (beds[0]["x"], beds[0]["y"], beds[0]["z"]), night_policy, "site bed")


def _sleep_carried(ctx, bed, s, inv):
    """A carried bed: a room made for it when none stands within reach (bed_room_tasks), its box lit when that pays
    (light_box), the gate read (sleep_gate: a refused sleep places nothing), then placed, lain in, and taken back in
    the morning (take_bed: always, however the night went)."""
    here = (s["blockX"], s["blockY"], s["blockZ"])
    region = Region(add(here, (-4, -2, -4)), add(here, (4, 3, 4)))
    places = [nav.building_of(inv)] * nav.place_budget(inv.count("building")) if nav.building_of(inv) else []
    tasks, cells, seconds, why = bed_room_tasks(region, here, ctx.policy.protected, places, inv)
    if tasks is None or cells is None:
        raise NotAvailable(f"no room for the bed: {why}")
    foot, head = cells
    if tasks:
        api.detail(f"   bed room at {foot}: {len(tasks)} tasks, ~{seconds or 0:.0f}s")
        api.run_chain(tasks, stop_on_failure=True, wait=60)
        region = Region(add(here, (-4, -2, -4)), add(here, (4, 3, 4)))
    light_box(ctx, (foot, head, here, add(foot, (0, 1, 0)), add(head, (0, 1, 0))), s, inv)   # bed and body: no spawn
    s = api.get("/state")
    why = sleep_gate(s, foot, head, region, entities(int(max(BED_BOX)) * 2))
    if why:
        raise NotAvailable(why)
    use = {"type": "use", "x": foot[0], "y": foot[1], "z": foot[2]}
    try:
        # placed and lain in as one chain; morning is read, not waited for
        chain = [{"type": "place", "item": bed, "x": foot[0], "y": foot[1], "z": foot[2]}, use]
        for _ in range(3):
            api.run_chain(chain, stop_on_failure=True, wait=30)
            if _morning():
                ctx.mem.slept()
                log("slept (carried bed)")
                return
            chain = [use]
        raise NotAvailable("could not fall asleep in the carried bed")
    finally:
        take_bed(ctx, foot)

def take_bed(ctx, foot):
    """The carried bed back in the bag: one break on its foot (both halves drop it)."""
    mine_cell(ctx.policy, foot, wait=60)

def light_box(ctx, taken, s, inv):
    """Torches round the bed when they pay (light_pays): every dark spot (block light 0, a floor under it) in the
    bed's box lit (torch_cover), with the torches carried — the bed's cells and the body's (`taken`: foot, head,
    feet) are no spawn spots: a sealed pod with nothing else dark lights nothing."""
    foot = taken[0]
    taken = {tuple(c) for c in taken}
    within = lambda p: all(abs(p[i] - foot[i]) <= BED_BOX[i] for i in range(3))     # noqa: E731
    r = int(max(BED_BOX))
    cells = lambda light: [(p["x"], p["y"], p["z"]) for p in dark_spots(radius=r, max_light=light, limit=1000)  # noqa: E731
                           if within((p["x"], p["y"], p["z"])) and (p["x"], p["y"], p["z"]) not in taken]
    dark, floor = cells(0), cells(TORCH_LIGHT)
    torches = torch_cover(dark)
    left_s = ((NIGHT_END - int(s["timeOfDay"])) % DAY_TICKS) / beliefs.TICKS_PER_S
    if not light_pays(len(dark), len(floor), left_s, len(torches) * nav.PLACE_S):
        return []
    if inv.usable("minecraft:torch") < len(torches):
        api.detail(f"   bed's box: {len(dark)} dark spots, {len(torches)} torches wanted, "
                   f"{inv.usable('minecraft:torch')} carried: unlit")
        return []
    api.run_chain([{"type": "place", "item": "minecraft:torch", "x": c[0], "y": c[1], "z": c[2]} for c in torches],
                  stop_on_failure=False, wait=60)
    return torches

HOME_BED_R = 48       # a bed this near is walked to for the night (the site-bed search radius)


def sleep_at_home(ctx):
    """The night's way "home": walk to the home's bed, however far, and sleep in it."""
    bed = ctx.mem.home_part("beds", ctx.dimension, feet(), anywhere=True)
    if bed is None:
        raise NotAvailable("no home bed in this dimension")
    return _sleep_in(ctx, bed, ctx.policy, "home bed")


def _sleep_in(ctx, b, night_policy, label):
    """Walk to the standing bed `b` and sleep in it."""
    if not nav.arrived(b, night_policy, range_=2.5, attempts=2):
        raise NotAvailable("bed not walkable tonight")
    why = sleep_gate(api.get("/state"), b, b, Region(add(b, (-1, -1, -1)), add(b, (1, 2, 1))),
                     entities(int(max(BED_BOX)) * 2))
    if why:
        raise NotAvailable(f"the {label}: {why}")          # the refusal named (17:49: 'unavailable', no reason)
    for _ in range(3):
        api.run_chain([{"type": "use", "x": b[0], "y": b[1], "z": b[2]}], wait=30)
        if _morning():
            ctx.mem.slept()
            log(f"slept ({label})")
            return
    raise NotAvailable(f"could not fall asleep in the {label}")

DIG_IN_DEPTH = 3

def dig_in_start(region, feet):
    """Pure: where a dig-in began: one above the top of the walled shaft the body stands in, else the feet."""

    x, y, z = feet
    top = y
    while top - y < DIG_IN_DEPTH and not region.solid((x, top, z)) and \
            all(region.solid((x + dx, top, z + dz)) for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]):
        top += 1
    return x, top, z

def dig_in_commands(state, args=()) -> "list[Task]":
    """Pure: dig DIG_IN_DEPTH straight down, stand at the bottom, and seal the first dug cell."""

    region, inv = state["region"], state["inv"]
    x, y, z = start = dig_in_start(region, tuple(state["feet"]))
    tasks, safe = nav.dig_down_tasks(region, start, DIG_IN_DEPTH, state["protected"], False, dug_to=state["feet"][1])
    if safe < DIG_IN_DEPTH:
        raise NotAvailable(f"only {safe} of {DIG_IN_DEPTH} safe to dig here: no lid below the ground line")
    block = next((b for b in GROUPS["building"] if inv.count(b)), None)
    if block is None:
        # nothing to seal with: the roof is what the dig brings up (an empty bag dug a hole with no lid)
        dug = [t for t in tasks if t["type"] == "mine"]
        if not dug:
            raise NotAvailable("nothing to seal the hole with: no block carried, none dug")
        if inv.free_slots() < 1:
            raise NotAvailable("nothing to seal the hole with, and no room in the bag for the block dug")
        for t in dug:
            t["collect"] = True
        name = bare(region.name((dug[0]["x"], dug[0]["y"], dug[0]["z"])))
        block = mid(PLACEABLE_AS.get(name, name))
    tasks.append({"type": "place", "item": block, "x": x, "y": y - 1, "z": z})      # the first dug cell
    return tasks

def soft_spot():
    """(cell, steps) of the nearest hand-diggable ground to DIG_IN_DEPTH on our ground, or None."""

    x, y, z = feet()
    region = Region((x - SOFT_RADIUS, y - DIG_IN_DEPTH - 2, z - SOFT_RADIUS), (x + SOFT_RADIUS, y + 3, z + SOFT_RADIUS))
    return nearest_soft(region, (x, y, z), DIG_IN_DEPTH)


def dig_in_site(region, feet_at, protected=()):
    """Pure: can a dig-in finish here — DIG_IN_DEPTH cells safe to dig under the column (nav.safe_depth, from
    where a started dig began: dig_in_start), so its lid sits below the ground line? A 3-thick floor over air
    gives 2: not offered (search_night_resume chose it, then 'only 2 of 3 safe')."""
    start = dig_in_start(region, tuple(feet_at))
    return nav.safe_depth(region, start, DIG_IN_DEPTH, protected, dug_to=feet_at[1]) >= DIG_IN_DEPTH

def night_ground():
    """One region read around the feet for the night's pricing: (seconds' walk to hand-diggable ground or None,
    whether a dig-in can finish right here)."""

    x, y, z = feet()
    region = Region((x - SOFT_RADIUS, y - DIG_IN_DEPTH - 2, z - SOFT_RADIUS), (x + SOFT_RADIUS, y + 3, z + SOFT_RADIUS))
    spot = nearest_soft(region, (x, y, z), DIG_IN_DEPTH)
    return (None if spot is None else spot[1] / WALK_BLOCKS_PER_S), dig_in_site(region, (x, y, z))

@skill(gives=["state:sheltered"], needs={}, remaining=lambda st, c: dug_in_left(st, c), start=lambda c: feet(), verify=lambda c: feet()[1] < c.base[1] and enclosed(), commands=dig_in_commands,
       provides={"state:sheltered": lambda ctx, s: () if require_pickaxe_ok() else None,
                 "shelter:dig in": lambda ctx, s: ()}, prefer=1,
       budget=60, stall=30)
def dig_in(ctx):
    """On the surface at night without a bed: dig up to 3 down under the feet and seal the opening overhead."""

    if not require_pickaxe_ok():
        spot = soft_spot()
        if spot is None:
            raise NotAvailable("no pickaxe and no ground near that digs by hand")
        # the exact cell (range 0): at 0.5 the dig started a block off
        if tuple(feet()) != tuple(spot[0]):
            nav.arrived(spot[0], ctx.policy, range_=0, attempts=1)
        if tuple(feet()) != tuple(spot[0]):
            raise api.NavFailed(f"not on the soft ground at {spot[0]} (at {feet()})")
    x, y, z = feet()
    shaft = Region((x - 1, y - 1, z - 1), (x + 1, y + DIG_IN_DEPTH + 1, z + 1))
    y = dig_in_start(shaft, (x, y, z))[1]                  # resumed after a fall: the same column, from its top
    tasks = dig_in_commands(body_state(ctx, nav.dig_down_region((x, y, z), DIG_IN_DEPTH)))
    api.run_chain(tasks, stop_on_failure=True, before_segment=ctx.policy.before_segment)
    fx, fy, fz = feet()
    if fy >= y:
        raise NotAvailable("digging down stopped: a block couldn't be reached")
    log("dug in for the night")

def enclosed():
    """True when the body has no way out: every side blocked at feet or head height, and covered overhead."""
    s = api.get("/state")
    x, y, z = s["blockX"], s["blockY"], s["blockZ"]
    return is_enclosed(Region((x - 1, y - 1, z - 1), (x + 1, y + 2, z + 1)), (x, y, z))

def open_room_left(st, c):
    """`remaining` of move_to_open_space: an open side to throw into (bag.throw_direction) where the body stands."""
    region = st.get("region")
    ok = region is not None and throw_direction(region, tuple(st["feet"])) is not None
    return {} if ok else {"state:open_room": 1}

def outside_left(state, call=None):
    """`remaining` of dig_out: out of the sealed shelter — some side opens (terrain.openings), read off the region."""
    if state.get("region") is None:
        return {"unread:shelter": 1}
    return {} if openings(state["region"], tuple(state["feet"])) else {"state:outside": 1}

def shelter_left(state, call=None):
    """`remaining` of a body shelter: its openings around the feet ({} walled in; everything when no region read)."""

    if state.get("region") is None:
        return {"state:sheltered": 1}
    return openings(state["region"], tuple(state["feet"]))

def dug_in_left(state, call=None):
    """`remaining` of dig_in: the openings, and the dig itself while the feet are not yet below where it began."""
    left = shelter_left(state, call)
    base = getattr(call, "base", None)
    if base is not None and state["feet"][1] >= base[1]:
        left["down"] = 1
    return left

def _pod_cells(feet_at):
    """The walls at feet and head height on four sides, then the roof."""
    x, y, z = feet_at
    cells = [(x + dx, y + dy, z + dz) for dy in (0, 1) for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]]
    cells.append((x, y + 2, z))
    return cells

def _pod_region(feet_at):
    x, y, z = feet_at
    return Region((x - 2, y - 3, z - 2), (x + 2, y + 2, z + 2))

def pod_commands(state, args=()) -> "list[Task]":
    """Pure: the tasks that wall the body in — feet level first, head level next, the roof last."""

    x, y, z = state["feet"]
    region, inv = state["region"], state["inv"]
    placed = set()

    def solid(c):
        return c in placed or region.solid(c)

    cells = _pod_cells((x, y, z))
    todo = sorted((c for c in cells if not region.solid(c)), key=lambda c: c[1])
    blocks = [b for b in GROUPS["building"] + GROUPS["planks"] if inv.count(b)]
    carried = sum(inv.count(b) for b in blocks)
    # planned against unlimited stock first so the supports count (a roof on open ground needs a cap: 10 blocks, not 9)
    stock = [[b, inv.count(b)] for b in blocks] + [["?", 1 << 30]]
    tasks = []

    def put(cell):
        while stock and stock[0][1] == 0:
            stock.pop(0)
        if not stock:
            return False             # ran out: what is left open is the verify's to report
        stock[0][1] -= 1
        tasks.append({"type": "place", "item": stock[0][0], "x": cell[0], "y": cell[1], "z": cell[2]})
        placed.add(cell)
        return True

    def has_support(cell):
        return any(solid(add(cell, d)) for d in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)])

    for c in todo:
        if solid(c):
            continue
        name = region.name(c)
        if not name.endswith(("air", "water")) and not region.hazard(c) and c not in state["protected"]:
            # something non-solid occupies the cell: break it first
            tasks.append(nav.mine_task(c, collect=True))
        if not has_support(c) and c == (x, y + 2, z):
            # the roof has nothing to click: cap a side wall first, then the roof goes against the cap
            for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                wall, cap = (x + dx, y + 1, z + dz), (x + dx, y + 2, z + dz)
                if solid(wall) and not solid(cap):
                    put(cap)
                    break
        if not has_support(c):
            # nothing to click against: build a support column up first
            below = add(c, (0, -1, 0))
            stack = []
            while not solid(below) and below[1] > y - 3:
                stack.append(below)
                below = add(below, (0, -1, 0))
            for s_cell in reversed(stack):
                put(s_cell)
        put(c)
    placed_n = sum(1 for t in tasks if t["type"] == "place")
    if placed_n > carried:
        raise NotAvailable(f"need {placed_n} blocks to wall in (supports included), {carried} carried")
    return tasks

@skill(gives=["state:sheltered"], needs={"building": POD_BLOCKS}, remaining=lambda st, c: shelter_left(st, c), done=lambda c: enclosed(), commands=pod_commands, budget=120, stall=40,
       provides={"state:sheltered": lambda ctx, s: (), "shelter:wall in": lambda ctx, s: ()}, prefer=-1)
def pod(ctx):
    """Night fallback where digging in is unsafe (water/caves below): wall in the body — four sides at feet and head, a roof."""

    x, y, z = feet()
    tasks = pod_commands(body_state(ctx, _pod_region((x, y, z))))
    for r in api.run_chain(tasks):
        if r["status"] != "succeeded":
            log(f"pod: {r['type']} failed: {r.get('message')}")
    cells = _pod_cells((x, y, z))
    region = Region((x - 1, y - 1, z - 1), (x + 1, y + 2, z + 1))
    open_cells = [c for c in cells if not region.solid(c)]
    if open_cells:
        raise McError(f"pod left {len(open_cells)} openings")
    log("walled in for the night")


def _has_torches_to_spare(c):
    if Inventory().usable("minecraft:torch") <= 2:
        raise NotAvailable("no torches to spare")

def _torches_standing(radius=12):
    return len(find(["torch", "wall_torch"], radius=radius, limit=64) or ())

@skill(gives=["state:lit"], remaining=_k.few_dark, pre=[_has_torches_to_spare], needs={"minecraft:torch": 3}, start=lambda c: _torches_standing(),
       verify=lambda c: _torches_standing() > c.base, commands=torch_commands, budget=180, stall=60,
       provides={"light": lambda ctx, s: (int(s.detail.get("radius", 10)), max(1, s.count))})
def light_area(ctx, radius=10, limit=6, spots=None):
    """Spawn-proof the surroundings: torches on the darkest reachable spots (block light 0) nearby, keeping 2.
    `spots`: the dark spots already read (light_due's look), else read here."""

    if spots is None and enclosed():
        raise NotAvailable("sealed in: nothing outside to light")
    spots = [p for p in (spots if spots is not None else dark_spots(radius=radius, max_light=0, limit=40))
             if not ctx.blocked((p["x"], p["y"], p["z"]))]
    tasks = torch_commands(body_state(ctx, spots=spots), (radius, limit * 2))
    if not tasks:
        raise NotAvailable("nothing dark nearby")
    lit, misses = 0, 0
    for task in tasks:
        if lit >= limit or Inventory().usable("minecraft:torch") <= 2 or misses >= 3:
            break   # three unreachable spots in a row: the rest are behind walls too
        pos = (task["x"], task["y"], task["z"])
        try:
            r = api.run(task, wait=40, awaits="the torch placed or the spot unreachable decides the next spot")
        except api.Unreachable:
            misses += 1                    # a dark spot behind a wall: the next one, as before
            continue
        if r["status"] == "succeeded":
            lit, misses = lit + 1, 0
        else:
            misses += 1
            ctx.ban(pos, 900)
        yield lit
    if not lit:
        raise NotAvailable("no dark spot could be lit")
    log(f"lit {lit} dark spots")

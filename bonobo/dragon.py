"""The dragon fight, the speedrunner's way: a 2-deep pit beside the exit portal, beds blown at the perched head from
inside it, endermen shaken off with water. `slay_dragon` only dispatches `fight_plan`'s intent; numbers live in
fight.toml. Pure parts (`fight_state`, `pit_geometry`, `phase_clock`, …) are offline-tested."""

import math
import time

from . import knowledge as _k
from . import api, combat, fight_plan, fluids, lifecycle, nav, threat
from .api import McError, NotAvailable, log
from .combat_model import HAZARD_R
from .data import GROUPS, THE_END
from .skill import budget_end, skill
from .world import Inventory, Region, entities, find

DRAGON = "minecraft:ender_dragon"
CRYSTAL = "minecraft:end_crystal"
PERCH_PHASES = frozenset({5, 6, 7})          # the game's sitting phases (flaming, scanning, attacking)
WINDOW_PHASES = frozenset(fight_plan.CONFIG["phases"]["window_phases"])
GEO = fight_plan.CONFIG["geometry"]
FIGHT = fight_plan.Fight()
SEEN_R = 256
ELAPSED_MAX_S = fight_plan.LIMITS[("boss", "phase_elapsed_s")][1]

PIT: dict = {}                               # the last build_bed_pit's geometry (pit_geometry)
CLOCK: dict = {"phase": None, "since": 0.0}  # the dragon's phase and when it began (phase_clock)
lifecycle.in_place(__name__, "PIT", "CLOCK")


def dragon_entry(near):
    """Pure: the dragon itself — its parts share the type but carry no health."""
    return next((e for e in near if e.get("type") == DRAGON and e.get("health") is not None), None)


def pillar_top(bedrock, centre=(0, 0)):
    """Pure: one above the highest bedrock of the exit portal's pillar (within 3 of the centre)."""
    tops = [b[1] for b in bedrock if abs(b[0] - centre[0]) <= 3 and abs(b[2] - centre[1]) <= 3]
    return max(tops) + 1 if tops else None


def choose_side(here, centre=(0, 0)):
    """Pure: the axis side of the portal the body is on (the perched head turns toward it)."""
    dx, dz = here[0] - centre[0], here[2] - centre[1]
    if abs(dx) >= abs(dz):
        return (1 if dx >= 0 else -1, 0)
    return (0, 1 if dz >= 0 else -1)


def pit_geometry(side, floor_y, centre=(0, 0)):
    """Pure: the pit (fight.toml [geometry]): its mouth on the floor at mouth_r out on `side`, feet pit_depth below,
    the bed on the floor at bed_r out (in reach from the pit, under the head that turns to us)."""
    mx, mz = centre[0] + side[0] * GEO["mouth_r"], centre[1] + side[1] * GEO["mouth_r"]
    bx, bz = centre[0] + side[0] * GEO["bed_r"], centre[1] + side[1] * GEO["bed_r"]
    return {"side": side, "floor": floor_y, "mouth": (mx, floor_y, mz),
            "feet": (mx, floor_y - GEO["pit_depth"], mz), "bed": (bx, floor_y, bz)}


def pit_holds(here, pit):
    """Pure: the feet in the pit's column, below the floor."""
    return bool(pit) and (here[0], here[2]) == (pit["feet"][0], pit["feet"][2]) and here[1] < pit["floor"]


def phase_clock(clock, phase, now):
    """Pure: (phase, since) — `since` kept while the phase holds, reset to `now` when it changes."""
    if clock.get("phase") == phase and clock.get("since"):
        return phase, clock["since"]
    return phase, now


def fight_state(me, near, inv, pit, clock, now):
    """Pure: fight_plan's state from the readings — `me` the /state, `near` the /entities, `inv` the bag's
    counts {token: n}, `pit` (pit_geometry or {}), `clock` (phase_clock's), `now` the time."""
    d = dragon_entry(near) or {}
    here = (math.floor(me["x"]), math.floor(me["y"]), math.floor(me["z"]))
    covered = pit_holds(here, pit)
    threats = [((e["x"], e["y"], e["z"]), HAZARD_R[e["type"]], (0.0, 0.0, 0.0), e["type"]) for e in near
               if e.get("type") in HAZARD_R and threat.aggro(e)]
    return {
        "self": {"pos": (me["x"], me["y"], me["z"]), "hp": float(me["health"]), "in_cover": covered,
                 "cover": pit.get("feet") if pit else None, "hp_floor": fight_plan.CONFIG["combat"]["hp_floor"],
                 "speed": fight_plan.CONFIG["combat"]["sprint_speed"]},
        "boss": {"phase": d.get("phase"), "phase_elapsed_s": min(max(0.0, now - clock.get("since", now)), ELAPSED_MAX_S),
                 "hp": float(d.get("health", 0.0))},
        "threats": threats,
        "resources": {"beds": inv.get("bed", 0), "obsidian": inv.get("minecraft:obsidian", 0),
                      "water": inv.get("minecraft:water_bucket", 0), "bow": inv.get("minecraft:bow", 0),
                      "arrows": inv.get("minecraft:arrow", 0)},
        # bed_bomb places and uses in one task: a carried bed counts as placed once the pit stands
        "terrain": {"tunnel_ready": bool(pit), "bed_placed": bool(pit) and inv.get("bed", 0) > 0,
                    "reinforced": bool(pit) and pit.get("reinforced", False),
                    "crystals_open": sum(1 for e in near if e.get("type") == CRYSTAL)},
    }


def reinforce_cells(pit):
    """Pure: the pit's walls at the floor's level below it, and the block under the bed — what a blast eats."""
    x, y, z = pit["mouth"]
    walls = [(x + dx, y - 1, z + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    bx, by, bz = pit["bed"]
    return walls + [(bx, by - 1, bz)]


def _carried():
    inv = Inventory()
    return {"bed": inv.count("bed"), **{i: inv.count(i) for i in (
        "minecraft:obsidian", "minecraft:water_bucket", "minecraft:bow", "minecraft:arrow")}}


def _bed_item():
    inv = Inventory()
    return next((b for b in GROUPS["bed"] if inv.count(b)), None)


def _here():
    s = api.get("/state")
    return s, (s["blockX"], s["blockY"], s["blockZ"])


def _floor_at(x, z, top):
    """The standing y on the column (x, z) near the pillar's top: one above its highest solid block."""
    lo, hi = (x, top - 8, z), (x, top + 2, z)
    region = Region(lo, hi)
    return next((y + 1 for y in range(hi[1], lo[1] - 1, -1) if region.solid((x, y, z))), None)


@skill(gives=["state:in_pit"], remaining=_k.walled_sides, needs={}, budget=240, stall=90, soft=True, abandon="cover")
def build_bed_pit(ctx):
    """Dig the pit beside the exit portal on our side and stand in it."""
    if api.get("/state")["dimension"] != THE_END:
        raise NotAvailable("not in the End")
    top = pillar_top([(h["x"], h["y"], h["z"]) for h in find(["bedrock"], radius=48, limit=80)])
    if top is None:
        raise NotAvailable("no exit portal pillar in range")
    _s, here = _here()
    side = choose_side(here)
    probe = pit_geometry(side, top)
    floor_y = _floor_at(probe["mouth"][0], probe["mouth"][2], top)
    if floor_y is None:
        raise NotAvailable(f"no floor at the pit's mouth {probe['mouth']}")
    pit = pit_geometry(side, floor_y)
    if not nav.arrived_near(pit["mouth"], ctx.policy, range_=0.6, attempts=2):
        raise api.NavFailed(f"the pit's mouth {pit['mouth']} not reached", pos=pit["mouth"])
    yield "mouth"
    nav.dig_down(GEO["pit_depth"], ctx.policy, False)
    PIT.clear()
    PIT.update(pit)
    log(f"   bed pit: feet {pit['feet']}, bed {pit['bed']}")
    return True


@skill(gives=["state:dragon_perched"], remaining=_k.dragon_phase(PERCH_PHASES), needs={}, budget=180, stall=120, soft=True, abandon="cover")
def await_perch(ctx):
    """Wait in the pit until the dragon sits on the portal."""
    end = budget_end()
    while time.time() < end:
        _s, here = _here()
        if not pit_holds(here, PIT):
            raise NotAvailable("not in the bed pit")
        d = dragon_entry(entities(SEEN_R, [DRAGON]))
        if d is None:
            raise NotAvailable("no dragon in sight")
        if d.get("phase") in PERCH_PHASES:
            return True
        api.run({"type": "wait", "ticks": 10}, wait=5, awaits="the dragon's next phase (a perch)")
        yield d.get("phase")
    raise McError("the dragon never perched")


@skill(gives=["state:window_used"], remaining=_k.window_over(WINDOW_PHASES), needs={"bed": 1}, budget=120, stall=60, soft=True, abandon="cover")
def bed_bomb_window(ctx):
    """Every bed carried into this one window (one-cycle), each placed and used from the pit."""
    if not PIT:
        raise NotAvailable("no bed pit built yet")
    blown = 0
    while True:
        item = _bed_item()
        d = dragon_entry(entities(SEEN_R, [DRAGON]))
        if item is None or d is None or d.get("phase") not in WINDOW_PHASES:
            break
        ok, why = FIGHT.admissible(_round_state(), FIGHT.action("fire_window"))
        if not ok:
            raise NotAvailable(f"no bed now: {why}")
        bed = PIT["bed"]
        r = api.run({"type": "bed_bomb", "x": bed[0], "y": bed[1], "z": bed[2], "item": item}, wait=5,
                    awaits="one bed placed and used in two ticks")
        after = dragon_entry(entities(SEEN_R, [DRAGON])) or {}
        log(f"   bed bomb {r['status']}: dragon {d.get('health')} → {after.get('health')} hp")
        if r["status"] != "succeeded":
            raise McError(f"bed bomb failed: {r['message']}")
        blown += 1
        yield after.get("health")
    if not blown:
        raise NotAvailable("no window to bomb (not perched, or no bed)")
    return True


@skill(gives=["state:enderman_off"], remaining=_k.none_of("minecraft:enderman", within=8.0), needs={}, budget=90, stall=45, soft=True, abandon="cover")
def shake_enderman(ctx):
    """Water at the feet: an enderman in water takes damage and teleports off; the water taken back after."""
    if not Inventory().count("minecraft:water_bucket"):
        raise NotAvailable("no water bucket to shake the enderman off")
    _s, here = _here()
    results = api.run_chain([fluids.use_task("minecraft:water_bucket", fluids.floor_aim(here), True),
                             {"type": "wait", "ticks": 20},
                             fluids.use_task("minecraft:bucket", fluids.surface_aim(here), False)], wait=10)
    failed = next((r for r in results if r["status"] != "succeeded"), None)
    if failed is not None:
        raise McError(f"water at the feet failed: {failed['message']}")
    yield "watered"
    return True


def shoot_crystal(ctx):
    """An arrow at the nearest end crystal in sight."""
    crystals = sorted(entities(SEEN_R, [CRYSTAL]), key=lambda e: e.get("distance", 0.0))
    if not crystals:
        raise NotAvailable("no end crystal in sight")
    combat.shoot(crystals[0])


def reinforce(ctx):
    """Obsidian in place of the end stone a blast eats around the pit and under the bed."""
    tasks = []
    for c in reinforce_cells(PIT):
        if Region(c, c).name(c) != "obsidian":
            tasks += [nav.mine_task(c), {"type": "place", "item": "minecraft:obsidian", "x": c[0], "y": c[1], "z": c[2]}]
    failed = next((r for r in api.run_chain(tasks, stop_on_failure=True, wait=60) if r["status"] != "succeeded"), None)
    if failed is not None:
        raise McError(f"reinforcing the pit failed: {failed['message']}")
    PIT["reinforced"] = True


def retreat(ctx):
    """Into the pit when out of it; inside, the wait for the next perch."""
    _s, here = _here()
    if PIT and not pit_holds(here, PIT):
        if not nav.arrived_near(PIT["feet"], ctx.policy, range_=0.6, attempts=1):
            raise api.NavFailed(f"the pit {PIT['feet']} not reached", pos=PIT["feet"])
        return
    api.run({"type": "wait", "ticks": 10}, wait=5, awaits="the next round's reading of the dragon")


# fight_plan's intents → what carries each out; the planner decides, this only dispatches
DISPATCH = {"dig_tunnel": build_bed_pit, "place_bed": retreat, "reinforce": reinforce,
            "shoot_crystal": shoot_crystal, "water_bucket": shake_enderman, "fire_window": bed_bomb_window,
            "retreat": retreat}


def _round_state():
    s = api.get("/state")
    d = dragon_entry(entities(SEEN_R, [DRAGON])) or {}
    now = time.time()
    CLOCK["phase"], CLOCK["since"] = phase_clock(CLOCK, d.get("phase"), now)
    return fight_state(s, entities(SEEN_R), _carried(), PIT, CLOCK, now)


def dragon_dead(near, portal_open):
    """Pure: no dragon left with health, and the exit portal lit (out of sight is not dead)."""
    d = dragon_entry(near)
    return (d is None or not d["health"]) and portal_open


@skill(gives=["state:dragon_dead"], remaining=_k.none_of("minecraft:ender_dragon", within=512.0), needs={}, budget=1800, stall=300, soft=True, abandon="cover",
       provides={"slay:dragon": lambda ctx, s: ()}, when=lambda s, f: [("dimension", THE_END)])
def slay_dragon(ctx):
    """The fight: each round fight_plan's intent over the readings, carried out by DISPATCH."""
    end = budget_end()
    said = None
    while time.time() < end:
        if dragon_dead(entities(SEEN_R, [DRAGON]), bool(find(["end_portal"], radius=32, limit=1))):
            return True
        state = _round_state()
        plan = FIGHT.plan(state)
        if plan["fault"]:
            raise McError(f"fight state unread: {plan['fault']}")
        if said is None:
            log(f"   dragon fight on assumptions: {', '.join(plan['assumptions'])}")
        if plan["intent"] != said:
            log(f"   dragon: {plan['intent']} (phase {state['boss']['phase']}, {state['boss']['hp']:.0f} hp)")
            said = plan["intent"]
        try:
            DISPATCH[plan["intent"]](ctx)
        except NotAvailable as e:
            log(f"   {plan['intent']} not now: {e}")
            retreat(ctx)
        yield round(state["boss"]["hp"])
    raise McError("the dragon still alive at the budget's end")

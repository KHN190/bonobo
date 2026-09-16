"""Look-ahead: simulate a plan forward (clock, place, tools, food) and turn the problems it would run into into
preparation needs the planner adds up front. One mechanism for "plan the furnace before leaving", "carry a shelter
kit when the trip runs into the night", "bring a spare pickaxe", "pack food". Pure functions: offline-testable.

Invariants checked along the simulated timeline:
  1. Stations: a smelt/craft step that comes after a step which moves us (mine / gather / hunt) happens somewhere
     else, so the station must be carried — "a furnace nearby now" doesn't count.
  2. Night: if the plan ends after dusk and no carried bed, carry a shelter kit (blueprints.SHELTER materials).
  3. Tools: blocks planned to break (plus tunnelling allowance) must fit the pickaxe durability left, else a spare.
  4. Food: about one food item per 90 s of work, plus a reserve.
"""
import math

from .data import DAY_END, NIGHT_END, RECIPES, TIER_OF_MATERIAL, bare, mid

MOVING = {"mine", "gather", "hunt"}
TUNNEL_ALLOWANCE = 2.0          # blocks dug to reach each planned block, plus a flat 16
FOOD_TICKS = 1800               # one food item per 90 s of work
FOOD_RESERVE = 2
WOOD_RESERVE = 8                # planks-equivalent carried before deep work: two pickaxes' worth of sticks
STATIONS = ("minecraft:furnace", "minecraft:crafting_table")
AXE_FROM_LOGS = 6               # planned logs from which crafting a wooden axe first pays off
MOVE_BLOCKS = 16                # building blocks carried on any plan that walks (pillars, bridges)


def escape_ready(inv):
    """Pure: if the pickaxe in use broke right now, could another be made without the surface? A second working
    pickaxe, or sticks (planks/logs count) plus 3 of a head material and a table (carried or 4 more planks)."""
    if sum(1 for _, d, _ in inv.tools("pickaxe") if d >= 3) >= 2:
        return True
    sticks = inv.count("minecraft:stick") + 2 * inv.count("planks") + 8 * inv.count("log")
    head = max(inv.count("minecraft:cobblestone") + inv.count("minecraft:cobbled_deepslate")
               + inv.count("minecraft:blackstone"), inv.count("minecraft:iron_ingot"), inv.count("minecraft:diamond"))
    table = inv.count("minecraft:crafting_table") > 0 or sticks >= 10
    return sticks >= 2 and head >= 3 and table


def _station_of(step):
    if step.kind == "smelt":
        return "minecraft:furnace"
    if step.kind == "craft":
        pattern = RECIPES.get(mid(step.token), ([None] * 4, 1))[0]
        return "minecraft:crafting_table" if len(pattern) == 9 else None
    return None


def prepare(plan, inv, time_of_day, has_bed, kit, site_eta=None):
    """Extra needs [(token, count) | ("tool", kind, tier)] so the plan can run to its end without a crisis.

    inv          world.Inventory-like (count, usable, tools)
    time_of_day  current tick of the day (0–24000)
    has_bed      a bed is carried
    kit          {token: count} a shelter needs (blueprints.materials(SHELTER))
    site_eta     ticks to the nearest home/shelter from here, or None when there is none
    """
    extra = {}

    def want(token, total):
        if inv.count(token) < total:
            extra[token] = max(extra.get(token, 0), total)

    # 1. stations after moving
    moved = False
    for step in plan:
        if step.kind in MOVING:
            moved = True
            continue
        station = _station_of(step)
        if moved and station:
            want(station, 1)

    # 2. night
    duration = sum(getattr(s, "est", 0) for s in plan)
    end = time_of_day + duration
    runs_into_night = time_of_day < DAY_END <= end or (DAY_END <= time_of_day <= NIGHT_END)
    if runs_into_night and not has_bed:
        home_in_time = site_eta is not None and time_of_day + site_eta < DAY_END
        if not home_in_time:
            for token, n in kit.items():
                want(token, n)

    # 3. pickaxe durability
    breaks = sum(s.detail.get("breaks", s.count) for s in plan if s.kind == "mine")
    if breaks:
        tier = max((s.detail.get("tier") or 0) for s in plan if s.kind == "mine")
        budget = math.ceil(breaks * TUNNEL_ALLOWANCE) + 16
        left = sum(d for t, d, _ in inv.tools("pickaxe") if t >= tier)
        if left < budget:
            material = next((m for m, t in sorted(TIER_OF_MATERIAL.items(), key=lambda kv: kv[1])
                             if t == max(tier, 1) and m != "golden"), "stone")
            item = f"minecraft:{material}_pickaxe"
            want(item, inv.count(item) + 1)

    # 5. wood for tools: sticks only come from logs on the surface. Deep work without any stranded the agent with
    #    10 diamonds, 3 ingots and three broken pickaxes at y=46.
    if breaks and not escape_ready(inv):
        want("planks", WOOD_RESERVE)

    # 7. blocks to move with: travel pillars and bridges out of the bag. A tree out of reach with an empty bag ended
    #    "no building blocks to pillar with" → nothing runnable → idle (20-min slice). Any moving plan carries 16.
    if any(s.kind in MOVING for s in plan) and inv.count("building") < MOVE_BLOCKS:
        want("building", MOVE_BLOCKS)

    # 6. an axe before real woodcutting: logs by hand take ~3.9 s each (bench), a wooden axe halves it and costs two
    #    logs' worth of planks — worth it from 6 logs on (a speedrun's early wood is ~10).
    logs = sum(s.count for s in plan if s.kind == "gather")
    if logs >= AXE_FROM_LOGS and not inv.tools("axe"):
        want("minecraft:wooden_axe", 1)

    # 4. food
    food_items = math.ceil(duration / FOOD_TICKS) + FOOD_RESERVE
    if duration > FOOD_TICKS:
        want("food", food_items)

    return [(token, n) for token, n in extra.items()]


def describe(extra):
    return ", ".join(f"{n}× {bare(t)}" for t, n in extra)

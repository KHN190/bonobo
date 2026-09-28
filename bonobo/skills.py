"""Verified routines built on the mod's primitives. Every skill carries a contract (see skill.py): preconditions, goal check, verification, time budget and a stall limit on its own goal metric. Skills never plan: inputs must be present. `python3 mc.py skills` lists the contracts."""

import math
import time

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import knowledge as K
from . import api, beliefs, nav, world
from .api import McError, NotAvailable, log
from .skill import ANCHORS, skill, current as current_call
from .data import (HAND_MINEABLE_SUFFIX, cannot_reach, ARMOR_RANK, ARMOR_SLOTS, BASE_MARKERS, GROUPS, LOG_TO_PLANKS,
                   MARKER_WEIGHT, PLACEABLE_AS, POD_BLOCKS, RECIPES, bare, mid)
from .knowledge import DIG_SHOVEL_S, GROUP_RECIPES, HUNT_SWORD_S, members
from .bag import mineable, opener, pickup_whitelist, refused

from .world import BAG_SLOTS, Inventory, Region, screen_slot, add, connected, dark_spots, entities, find, job_ready, region_around, ripe_near  # noqa: F401  (job_ready: re-exported)
from .bag import FLOOR, let_go, free_slots_plan, FREE_SLOTS_TARGET, throw_direction, store_plan  # noqa: F401  (moved; re-exported for skills.X callers)
from .terrain import LAND, soft_below, underground_target, shelter_method_at, find_shelter_spot, choose_burrow, NEIGHBOURS6_LOCAL, choose_exit, air_route, is_enclosed, openings, find_open_spot, chest_spot_ok  # noqa: F401  (moved; re-exported for skills.X callers)
from .skillcore import (_collect_only, StationMissing, ToolMissing, Context, feet, close_screen, free_spots,  # noqa: F401,E402
                        free_spot, free_spots_here, spot_region, place, snapshot, mine_cell, gained, lost, settle,
                        body_state, head_buried, head_underwater,
                        carried_total)   # (split out; re-exported for skills.X callers)
from .explore import surface_first, explore_for, seek_blocks, approach_policy  # noqa: F401,E402  (split out; re-exported for skills.X callers)
from .wood import chop  # noqa: F401,E402  (split out; re-exported for skills.X callers)
from .building import _mod_at_least, _open_container, _empty_container_slot, _machine_roles, _go_to_machine, find_machine_spot, resolve_item, block_matches, place_oriented, materials_missing, _build_parts, build_blueprint, build_shelter  # noqa: F401,E402  (split out; re-exported for skills.X callers)

# -- placing things near us

def make_room(ctx):
    """Boxed in a 1-wide shaft: dig the side cell at head height so there is a free spot for a station."""

    s = api.get("/state")
    fx, fy, fz = s["blockX"], s["blockY"], s["blockZ"]
    region = Region((fx - 2, fy - 2, fz - 2), (fx + 2, fy + 3, fz + 2))
    for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        cell, floor = (fx + dx, fy, fz + dz), (fx + dx, fy - 1, fz + dz)
        if not region.solid(floor) or region.hazard(floor) or cell in ctx.policy.protected:
            continue
        if region.solid(cell) and (region.unbreakable(cell) or region.player_made(cell)
                                   or any(region.hazard(add(cell, d)) for d in nav.NEIGHBOURS6)):
            continue
        above = add(cell, (0, 1, 0))
        if region.solid(above) and (region.unbreakable(above) or region.player_made(above)
                                    or any(region.hazard(add(above, d)) for d in nav.NEIGHBOURS6)):
            continue
        # the cell and the one above (a chest under a block won't open), in one chain
        dig = [nav.mine_task(c) for c in (cell, above) if region.solid(c)]
        if dig:
            api.run_chain(dig, stop_on_failure=True, wait=60)
        check = Region(cell, above)
        if not check.solid(cell) and not check.solid(above):
            log(f"   made room for a station at {cell}")
            return [cell]
    return []

def openable_container(pos):
    """A chest opens only with no solid block right above it (barrels always open)."""
    above = add(pos, (0, 1, 0))
    r = Region(pos, above)
    return r.name(pos) == "barrel" or not r.solid(above)

@skill(gives=["state:open_room"], remaining=lambda st, c: open_room_left(st, c), needs={}, speed={}, budget=120, stall=45,
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

# -- stations

def takes_back(block, has_pickaxe):
    """Pure: may a placed station be broken to carry it on?"""

    return has_pickaxe or bare(block).endswith(HAND_MINEABLE_SUFFIX)

def take_back_verdict(gained, standing):
    """Pure: a picked-up station was "taken" (bag gained it), "left" (still standing) or "lost" (gone, not in the bag)."""

    if gained:
        return "taken"
    return "left" if standing else "lost"

def _standing(block, pos, tries=10):
    """The world shows `block` at `pos`, read again a few ticks apart (the client sees a placement late)."""

    for i in range(tries):
        if any((b["x"], b["y"], b["z"]) == tuple(pos) for b in find([block], radius=6, limit=20)):
            return True
        if i < tries - 1:
            time.sleep(0.1)
    return False

class Station:
    def __init__(self, ctx, block):
        self.ctx, self.block, self.pos, self.placed = ctx, block, None, False

    def __enter__(self):
        close_screen()
        near = find([self.block], radius=6, limit=1)
        if near:
            self.pos = (near[0]["x"], near[0]["y"], near[0]["z"])
        elif Inventory().count(self.block):
            last = "no free spot"
            spots = free_spots_here(limit=3) or make_room(self.ctx)
            for spot in spots:
                try:
                    place(self.block, spot)
                    self.pos = spot
                    break
                except api.INTERRUPTIONS:
                    raise              # an interruption is not a failure to shrug off here
                except McError as e:
                    last = str(e)
            if self.pos is None:
                raise NotAvailable(f"no room to place {bare(self.block)} ({last})")
            self.placed = True
            self.ctx.mem.add_station(self.block, self.pos, self.ctx.dimension)
            if not _standing(self.block, self.pos):
                raise McError(f"placed {bare(self.block)} at {self.pos} but the world does not show it")
        else:
            # gone from where memory has it: forgotten, so the repaired plan makes one again
            for s in self.ctx.mem.stations(self.ctx.dimension, near=feet(), within=8):
                if bare(s["block"]) == bare(self.block):
                    self.ctx.mem.remove_station(s["pos"])
            raise StationMissing(self.block)
        for _ in range(3):
            try:
                r = api.run({"type": "use", "x": self.pos[0], "y": self.pos[1], "z": self.pos[2]}, wait=60, awaits="the station's screen open")
            except api.Unreachable:
                break                      # behind a wall or a fence: the station is not usable from here
            if r["status"] == "succeeded" and r["result"].get("screen") not in (None, "none"):
                return self
        self.__exit__(None, None, None)
        raise McError(f"could not open {bare(self.block)}")

    def __exit__(self, *exc):
        api.post("/close")
        if self.placed and not takes_back(self.block, bool(Inventory().tools("pickaxe"))):
            # left standing, remembered as a station: a furnace broken by hand takes ~17 s and drops nothing
            log(f"   left the {bare(self.block)} standing: no pickaxe to take it back")
            return False
        if self.placed:
            before = Inventory().count(self.block)
            held = lambda: Inventory().count(self.block)   # noqa: E731
            try:
                mine_cell(self.ctx.policy, self.pos, wanted=[self.block], collect=True, require_drops=True, wait=60)
            except api.INTERRUPTIONS:
                raise
            except McError as e:
                log(f"   could not break the {bare(self.block)} to take it back: {e}")
            if gained(held, before) <= before and not _standing(self.block, self.pos, tries=1):
                api.run({"type": "collect", "radius": 6}, wait=30, awaits="the drop the break's own sweep missed")   # the drop can land out of the sweep
            verdict = take_back_verdict(gained(held, before) > before, _standing(self.block, self.pos, tries=1))
            if verdict != "left":
                self.ctx.mem.remove_station(self.pos)      # in the bag, or gone: not a station here any more
            if verdict == "left":
                log(f"   left the {bare(self.block)} standing at {self.pos}: remembered as a station")
            elif verdict == "lost":
                log(f"   !! lost the carried {bare(self.block)} while picking it up")
        return False

# -- crafting and smelting

def resolve_pattern(pattern, times, inv):
    """Replace group tokens with one owned member each, with enough for all cells × times."""
    need = {}
    for tok in pattern:
        if tok:
            need[tok] = need.get(tok, 0) + times
    chosen = {}
    for tok, n in need.items():
        options = sorted(members(tok), key=inv.count, reverse=True)
        pick = next((m for m in options if inv.count(m) >= n), None)
        if pick is None:
            raise McError(f"missing {n}× {tok} for crafting")
        chosen[tok] = pick
    return [chosen[t] if t else None for t in pattern]

def output_of(token, concrete_pattern):
    """For group recipes the output variant follows the ingredient variant."""
    if token not in GROUP_RECIPES:
        return mid(token)
    if token == "planks":
        return LOG_TO_PLANKS[concrete_pattern[0]]
    if token == "bed":
        return concrete_pattern[0].replace("_wool", "_bed")
    planks = next(p for p in concrete_pattern if p)
    return planks.replace("_planks", "_boat" if token == "boat" else "_door")

def recipe_of(token):
    """(pattern, output per craft) of a recipe token: a group recipe (planks, bed, boat) or a plain item."""
    return GROUP_RECIPES[token] if token in GROUP_RECIPES else RECIPES[mid(token)]

class _BagAfter:
    """A bag as it will be after some crafts: the real counts plus a delta (ids → change)."""

    def __init__(self, inv, delta):
        self.inv, self.delta = inv, delta

    def count(self, item_or_group):
        return self.inv.count(item_or_group) + sum(self.delta.get(i, 0)
                                                   for i in GROUPS.get(item_or_group, [mid(item_or_group)]))

def craft_plan(recipes, inv):
    """Pure: one sitting's crafts from the bag `inv` — ([(concrete pattern, item, count)], needs a table, net bag delta)."""

    delta, steps = {}, []
    for token, times in recipes:
        pattern, out = recipe_of(token)
        concrete = resolve_pattern(pattern, times, _BagAfter(inv, delta))
        item = output_of(token, concrete)
        for c in concrete:
            if c:
                delta[c] = delta.get(c, 0) - times
        delta[item] = delta.get(item, 0) + out * times
        steps.append((concrete, item, out * times))
    return steps, any(needs_table(c) for c, _, _ in steps), {i: n for i, n in delta.items() if n}

def needs_table(concrete):
    """Pure: a resolved pattern needs the 3×3 grid (a crafting table); a 2×2 one is made in the bag."""
    return len(concrete) == 9

def sittings(steps):
    """Pure: `craft_plan`'s steps cut where the grid changes — [(needs a table, [steps])], in plan order."""

    out = []
    for st in steps:
        table = needs_table(st[0])
        if out and out[-1][0] == table:
            out[-1][1].append(st)
        else:
            out.append((table, [st]))
    return out

def _plan_start(recipes):
    """`start` of both craft skills: the plan (raises on a missing input before anything moves) and the starting counts."""

    inv = Inventory()
    _, _, delta = craft_plan(recipes, inv)
    return {i: (inv.count(i), n) for i, n in delta.items() if n > 0}

def _plan_made(c):
    """`verify` of both craft skills: the real bag gained every item the plan nets, by at least what it nets."""
    return all(Inventory().count(i) >= have + n for i, (have, n) in c.base.items())

CLOSE = {"type": "_close"}     # a split point in a chain: the screen is closed between two sends (no close task)

def run_split(tasks, **kw):
    """Run a `*_commands` chain: each stretch between CLOSE markers in one run_chain, the screen closed between them."""

    results, part = [], []
    for t in list(tasks) + [None]:
        if t is None or t == CLOSE:
            if part:
                results += api.run_chain(part, stop_on_failure=True, **kw)
                part = []
            if t is not None and results:
                api.post("/close")
            continue
        part.append(t)
    bad = next((r for r in results if r.get("status") != "succeeded"), None)
    if bad is not None:
        raise McError(f"{bad.get('type', 'task')} failed: {bad.get('message')}")
    return results

def craft_commands(state, args):
    """Pure: the crafting session as one chain per sitting — 2×2 in the bag, 3×3 at a table near or placed and taken back."""

    (recipes,) = args
    inv = state["inv"]
    steps, _, _ = craft_plan(recipes, inv)
    out, made_table = [], False
    for table, part in sittings(steps):
        crafts = [{"type": "craft", "pattern": concrete, "count": n} for concrete, _item, n in part]
        if not table:
            out += [CLOSE] + crafts
            made_table = made_table or any(item == "minecraft:crafting_table" for _c, item, _n in part)
            continue
        pos, placed = state.get("table"), False
        if pos is None:
            if not (inv.count("minecraft:crafting_table") or made_table) or state.get("spot") is None:
                raise StationMissing("minecraft:crafting_table")
            pos, placed = tuple(state["spot"]), True
            out.append({"type": "place", "item": "minecraft:crafting_table", "x": pos[0], "y": pos[1], "z": pos[2]})
        out += [{"type": "use", "x": pos[0], "y": pos[1], "z": pos[2]}] + crafts + [CLOSE]
        if placed and takes_back("minecraft:crafting_table", bool(inv.tools("pickaxe"))):
            out.append(nav.mine_task(pos, collect=True))
    return out

def room_clicks(slots, need, price=None):
    """Pure: the /click bodies that throw the `need` least valuable stacks (bag.free_slots_plan: priced by what
    each costs to get again) — room for a result that needs a slot."""
    return [{"slot": screen_slot(s["slot"]), "button": 1, "action": "THROW"}
            for s in free_slots_plan(slots, need=need, price=price)[:need]]


def make_bag_room(ctx, need):
    """Throw the `need` least valuable stacks now (room_clicks), the screen closed first. Returns the clicks."""
    close_screen()
    clicks = room_clicks(Inventory().slots, need, ctx.prices().get if ctx else None)
    for body in clicks:
        api.post("/click", body)
    return clicks


def _sitting(ctx, recipes):
    """Craft `recipes` in one sitting: the table opened (or placed) once and closed (or taken back) once."""

    inv = Inventory()
    if inv.used_slots() >= BAG_SLOTS:
        # the result needs a slot: drop the least valuable stack first
        if make_bag_room(ctx, 1):
            log("   dropped a stack to make room for crafting")
        inv = Inventory()
    steps, _, _ = craft_plan(recipes, inv)
    # world reads only when a table sitting is planned: a table near, else a spot for one
    state = {"inv": inv, "table": None, "spot": None}
    if any(table for table, _part in sittings(steps)):
        near = find(["crafting_table"], radius=6, limit=1)
        if near:
            state["table"] = (near[0]["x"], near[0]["y"], near[0]["z"])
        else:
            spots = free_spots_here(limit=1) or make_room(ctx)
            state["spot"] = spots[0] if spots else None
    tasks = craft_commands(state, (recipes,))
    placed = next(((t["x"], t["y"], t["z"]) for t in tasks if t.get("type") == "place"), None)
    before = Inventory().count("minecraft:crafting_table")
    close_screen()
    try:
        run_split(tasks, wait=120)
    finally:
        if placed is not None:
            # Memory knows where our table stands: kept when it was left there, dropped once it is back in the bag.
            if Inventory().count("minecraft:crafting_table") > before or not _standing("crafting_table", placed, 1):
                ctx.mem.remove_station(placed)
            else:
                ctx.mem.add_station("minecraft:crafting_table", placed, ctx.dimension)
    return [(t, times) for t, times in recipes]

@skill(gives=[K.GIVES_CRAFT_GROUP, K.GIVES_CRAFT], needs={}, speed={}, start=lambda c: _plan_start([(c.args[1], c.args[2])]), verify=_plan_made, budget=90, stall=60,
       key=lambda c: "craft", provides={"craft": lambda ctx, s: (s.token, s.detail["times"])},
       commands=lambda state, args: craft_commands(state, ([(args[0], args[1])],)))
def craft(ctx, token, times):
    """Craft `times` batches of a recipe (2×2 in the inventory, 3×3 at a found or carried crafting table)."""
    return _sitting(ctx, [(token, times)])

@skill(gives=["state:crafted"], remaining=_k.planned_items, needs={}, speed={}, start=lambda c: _plan_start(c.args[1]), verify=_plan_made, budget=120, stall=60, key=lambda c: "craft",
       commands=lambda state, args: craft_commands(state, (args[0],)))
def craft_chain(ctx, recipes):
    """Consecutive crafts of one plan in one sitting (`_sitting`). `recipes`: [(token, times)] in plan order."""
    return _sitting(ctx, recipes)

def move_into(ids, target_slot, amount):
    moved = 0
    while moved < amount:
        c = world.container()
        src = next((s for s in c["slots"] if s["owner"] == "player" and s["id"] in ids), None)
        if src is None:
            raise McError(f"ran out of {ids[0]} while loading the furnace")
        take = min(amount - moved, src["count"])
        c = api.post("/click", {"slot": src["slot"], "button": 0, "action": "PICKUP"})
        if take == src["count"]:
            c = api.post("/click", {"slot": target_slot, "button": 0, "action": "PICKUP"})
        else:
            for _ in range(take):
                c = api.post("/click", {"slot": target_slot, "button": 1, "action": "PICKUP"})
        if c["cursor"]["count"] > 0:
            api.post("/click", {"slot": src["slot"], "button": 0, "action": "PICKUP"})
        moved += take

def _furnace_slots():
    return {s["slot"]: s.get("count", 0) for s in world.container()["slots"]
            if s["owner"] != "player" and s["id"] != "minecraft:air"}

@skill(gives=K.GIVES_SMELT, needs={}, speed={}, start=lambda c: Inventory().count(c.args[1]), verify=lambda c: Inventory().count(c.args[1]) > c.base,
       budget=900, stall=30, units=lambda c: min(64, c.args[3]), key=lambda c: "smelt",
       provides={"smelt": lambda ctx, s: _smelt_args(s)}, prefer=-1)
def smelt(ctx, output, input_token, count, fuel):
    """One furnace session: load input + fuel, watch the output slot fill (10 s/item), take everything out."""
    count = min(64, count)
    inv = Inventory()
    inputs = [m for m in members(input_token) if inv.count(m)]
    fuels = [m for m in members(fuel) if inv.count(m)]
    # Said before the furnace is touched: loading nothing waited out the stall with no reason given.
    if not inputs:
        raise NotAvailable(f"no {bare(input_token)} to smelt")
    if not fuels:
        raise NotAvailable(f"no {bare(fuel)} to burn")
    fuel_n = math.ceil(count / 8) if fuel == "coal" else math.ceil(count / 1.5)
    with Station(ctx, "minecraft:furnace"):
        try:
            move_into(inputs, 0, count)
            move_into(fuels, 1, fuel_n)
            loaded = _furnace_slots().get(0, 0)
            while True:
                slots = _furnace_slots()
                made = slots.get(2, 0)
                # done when every loaded item came out: an empty input still has the last item cooking
                if made >= loaded:
                    break
                yield made
                api.waiting_for_clock(10 * (loaded - made))     # 10 s an item: only the clock is waited on
                time.sleep(3)
        finally:
            api.post("/click", {"slot": 2, "button": 0, "action": "QUICK_MOVE"})

# every smelt runs in the background: standing at a furnace was the iron bench's main time sink
ASYNC_SMELT_MIN = 1

FURNACE_REACH = 16          # every furnace this close shares a batch
ITEMS_PER_FUEL = {"coal": 8, "charcoal": 8, "planks": 1.5, "log": 1.5}

def split_smelt(furnaces, n, fuel, per):
    """Pure: [(pos, items, fuel)] — `n` items spread over free furnaces, each fuelled for its share, as many as the fuel lights."""

    free = [pos for pos, state in furnaces if state == "free"]
    if not free:
        raise NotAvailable("no free furnace within reach")
    total = min(int(n), int(fuel * per))
    if total < 1:
        raise NotAvailable("no fuel to burn")
    for used in range(min(len(free), total, int(fuel)), 0, -1):
        shares = [total // used + (1 if i < total % used else 0) for i in range(used)]
        burns = [math.ceil(k / per) for k in shares]
        if sum(burns) <= fuel:
            return [(pos, k, f) for pos, k, f in zip(free, shares, burns)]
    return [(free[0], total, math.ceil(total / per))]

def furnace_takes(slots, input_ids, output):
    """Pure: an open furnace takes this batch — input and output empty or the same item."""

    inp, out = slots.get(0, "minecraft:air"), slots.get(2, "minecraft:air")
    return inp in ("minecraft:air", *input_ids) and out in ("minecraft:air", output)

@skill(gives=["state:smelting"], remaining=_k.less_than_at_start(lambda c: c.args[2], lambda c: min(64, c.args[3])), needs={}, speed={}, start=lambda c: Inventory().count(c.args[2]), verify=lambda c: Inventory().count(c.args[2]) < c.base,
       budget=180, stall=40,
       provides={"smelt": lambda ctx, s: _smelt_args(s) if s.count >= ASYNC_SMELT_MIN else None})
def start_smelt_job(ctx, output, input_token, count, fuel):
    """Spread the batch over the free furnaces within FURNACE_REACH (placing one if none), fuel each, and walk away."""

    from .building import _open_container
    count = min(64, count)
    inv = Inventory()
    inputs = [m for m in members(input_token) if inv.count(m)]
    fuels = [m for m in members(fuel) if inv.count(m)]
    per = ITEMS_PER_FUEL.get(bare(fuel), 1.5)
    placed = None
    near = [(h["x"], h["y"], h["z"]) for h in find(["furnace"], radius=FURNACE_REACH, limit=8)
            if not ctx.blocked((h["x"], h["y"], h["z"]))]
    if not near:
        station = Station(ctx, "minecraft:furnace")          # places the carried furnace
        station.__enter__()
        api.post("/close")
        near, placed = [station.pos], station.pos
    # busy furnaces are memory's; each is opened once, read, loaded, closed (the clicks need its screen, the jar has no click task)
    busy = {tuple(j["pos"]) for j in ctx.mem.jobs(ctx.dimension) if j.get("kind") == "furnace"}
    states = [(pos, "busy" if tuple(pos) in busy else "free") for pos in near]
    plan = split_smelt(states, count, sum(inv.count(f) for f in fuels), per)
    ready = []
    for pos, k, f in plan:
        _open_container(pos)
        try:
            slots = {x["slot"]: x["id"] for x in world.container()["slots"] if x["owner"] != "player"}
            if not furnace_takes(slots, inputs, output):
                log(f"   the furnace at {pos} holds something else: skipped")
                continue
            move_into(inputs, 0, k)
            move_into(fuels, 1, f)
        finally:
            api.post("/close")    # leave the furnace standing: that's the point
        ready_at = time.time() + 10 * k + 5
        tick = api.get("/state").get("gameTime")
        job = ctx.mem.add_job("furnace", pos, ctx.dimension, output, k, ready_at, pos == placed,
                              input=inputs[0] if inputs else None, input_count=k,
                              fuel=fuels[0] if fuels else None, fuel_count=f)
        if tick is not None:
            job["ready_tick"] = tick + TICKS_PER_ITEM * k + 20
            ctx.mem.save()
        ready.append(ready_at)
        yield k
    total = sum(k for _, k, _ in plan)
    log(f"ordered {total}× {bare(output)} smelting in the background in {len(plan)} furnace(s) "
        f"(ready in ~{int(max(ready) - time.time())}s)")
    return {"ordered": output, "count": total, "ready_at": max(ready)}

def _smelt_args(s):
    return mid(s.token), s.detail["input"], s.count, s.detail["fuel"]

def _night_policy(ctx):
    import dataclasses
    return dataclasses.replace(ctx.policy, allow_surface=False)

def require_pickaxe_ok():
    return any(d >= 3 for _, d, _ in Inventory().tools("pickaxe"))

def _smelter_for(ctx, s):
    """(machine, input, count, fuel, output) when an auto smelter within 64 blocks can take this batch, else None."""
    if s.count < 8:
        return None
    here = feet()
    near = [m for m in ctx.mem.machines(ctx.dimension, "smelting") if math.dist(m["origin"], here) <= 64]
    if not near:
        return None
    machine = min(near, key=lambda m: math.dist(m["origin"], here))
    return machine, s.detail["input"], s.count, s.detail["fuel"], mid(s.token)

TICKS_PER_ITEM = 200        # a furnace smelts one item in 200 game ticks (10 s at 20 tps)

def after_take(job, got, still_cooking, now, tick=None):
    """Pure: a furnace job after taking `got` with `still_cooking` left — None when over, else the fields to update."""

    if not still_cooking:
        return None
    out = {"count": max(0, job["count"] - got), "input_count": still_cooking,
           "ready_at": now + 10 * still_cooking + 5}
    if tick is not None:
        out["ready_tick"] = tick + TICKS_PER_ITEM * still_cooking + 20
    return out

@skill(gives=["state:job_collected"], remaining=_k.more_than_at_start(lambda c: c.args[1]["item"], lambda c: c.args[1]["count"]), needs={}, speed={}, start=lambda c: Inventory().count(c.args[1]["item"]),
       verify=lambda c: Inventory().count(c.args[1]["item"]) > c.base, budget=240, stall=60)
def collect_job(ctx, job):
    """Go back to a background furnace job, take the output (and leftovers), pick the furnace up if it was ours."""
    pos = tuple(job["pos"])
    if Region(pos, pos).name(pos) not in ("furnace", "blast_furnace", "smoker"):
        # Picked back up, broken or never placed there: the job is stale, not a navigation problem.
        ctx.mem.finish_job(job["id"])
        raise NotAvailable(f"no furnace at {pos} any more; job dropped")
    if not nav.arrived(pos, ctx.policy, range_=3, attempts=2):
        raise api.NavFailed(f"furnace job at {pos} not reachable")
    before = Inventory().count(job["item"])
    r = api.run({"type": "use", "x": pos[0], "y": pos[1], "z": pos[2]}, wait=40, awaits="the furnace's slots read on its screen")
    if r["status"] != "succeeded" or r["result"].get("screen") in (None, "none"):
        if not find(["furnace"], radius=4, limit=1):
            ctx.mem.finish_job(job["id"])      # furnace is gone (broken, burnt down area): forget the job
            raise NotAvailable(f"furnace at {pos} is gone")
        raise McError("could not open the furnace")
    try:
        slots = _furnace_slots()
        still_cooking = slots.get(0, 0)
        for slot in (2, 0, 1) if not still_cooking else (2,):
            api.post("/click", {"slot": slot, "button": 0, "action": "QUICK_MOVE"})
        yield slots.get(2, 0)
    finally:
        api.post("/close")
    got = gained(lambda: Inventory().count(job["item"]), before) - before
    left = after_take(job, got, still_cooking, time.time(), api.get("/state").get("gameTime"))
    if left is not None:
        ctx.mem.update_job(job["id"], **left)
        log(f"took {got}× {bare(job['item'])}; {still_cooking} still cooking")
        return
    if job.get("carried"):
        mine_cell(ctx.policy, pos, wanted=["minecraft:furnace", job["item"]], require_drops=True, wait=40)
    ctx.mem.finish_job(job["id"])
    log(f"collected {got}× {bare(job['item'])} from the background furnace")

# -- tools and armor

def require_pickaxe(tier, min_left=3):
    if tier is None:
        return
    if not any(t >= tier and d >= min_left for t, d, _ in Inventory().tools("pickaxe")):
        raise ToolMissing("pickaxe", tier)

def shield_wanted_in_offhand():
    inv = Inventory()
    return inv.offhand() != "minecraft:shield" and inv.usable("minecraft:shield") > 0

def shield_to_offhand():
    """Swap the shield into the offhand (F-swap); whatever was there goes back to the shield's slot."""

    close_screen()
    inv = Inventory()
    s = next((s for s in inv.slots if s["id"] == "minecraft:shield"), None)
    if s is None:
        return False
    api.post("/click", {"slot": screen_slot(s["slot"]), "button": 40, "action": "SWAP"})
    ok = Inventory().offhand() == "minecraft:shield"
    log("shield moved to the offhand" if ok else "   !! could not move the shield to the offhand")
    return ok

def better_armor_carried():
    inv = Inventory()
    for s in inv.slots:
        material, _, piece = bare(s["id"]).rpartition("_")
        if piece in ARMOR_SLOTS and material in ARMOR_RANK:
            worn = bare(inv.worn(ARMOR_SLOTS[piece]))
            if worn == "air" or ARMOR_RANK.get(worn.rpartition("_")[0], -1) < ARMOR_RANK[material]:
                return True
    return False

def equip_armor():
    close_screen()
    inv = Inventory()
    changed = False
    for s in inv.slots:
        material, _, piece = bare(s["id"]).rpartition("_")
        if piece not in ARMOR_SLOTS or material not in ARMOR_RANK:
            continue
        worn = bare(inv.worn(ARMOR_SLOTS[piece]))
        if worn == "air" or ARMOR_RANK.get(worn.rpartition("_")[0], -1) < ARMOR_RANK[material]:
            api.post("/click", {"slot": screen_slot(s["slot"]), "button": 0,
                                "action": "QUICK_MOVE"})
            log(f"equipped {bare(s['id'])}")
            changed = True
    return changed

# -- gathering

MINE_BATCH = 32  # blocks one mine_many takes: smaller batches re-planned too often (1.4 s each)
BESIDE = 0.5            # travel range that ends face to face with a block (the walker's arrival: range + 0.5)
REACH_BUDGET = 3         # ways of not getting there, per call, before the place itself is the problem

def _reach_budget(spent, blocks, why=None):
    """Raise once the budget is gone."""

    if spent >= REACH_BUDGET:
        raise api.NavFailed(why or f"{blocks[0]}: {spent} unreachable in a row — not from this spot")

SEEK_RADII = (24, 48)     # a mining pass looks near first, then once wider

def seek_hits(blocks, found, radius, blocked, protected):
    """Pure: what a mining pass may go for, from what `/find` saw — sealed or exposed alike, minus bans and our own builds."""

    at = [(h, (h["x"], h["y"], h["z"])) for h in found]
    hits = [h for h, p in at if not blocked(p) and p not in protected]
    if hits:
        return hits
    if radius < SEEK_RADII[-1]:
        return None
    banned = sum(1 for _, p in at if blocked(p))
    why = f": {len(found)} in range but {banned} banned, {len(found) - banned} protected" if found else ""
    raise NotAvailable(f"no {blocks[0]} within {SEEK_RADII[-1]} blocks{why}")

def noted_hits(notes, blocks, blocked, protected):
    """Pure: remembered cells of `blocks` a mining pass may go straight to, shaped like /find's answer, minus bans and builds."""

    names = {bare(b) for b in blocks}
    out = []
    for n in notes:
        p = tuple(n["pos"])
        if bare(n["kind"]) in names and not blocked(p) and p not in protected:
            out.append({"x": p[0], "y": p[1], "z": p[2], "noted": True})
    return out

def mine_segment_commands(state, args):
    """Pure: one open-loop mining segment — the cells as single mines in the batch's order (nav.mine_batch: top of a
    column first, the nearest next), then one sweep with the pickup filter a nearly full bag needs."""

    cells, drop, tier = args
    only = pickup_whitelist(state["inv"].used_slots(), [drop])
    return nav.mine_batch(cells, state.get("feet"), require_drops=tier is not None, collect=True, only=only or None)

def spent_cells(sent, name_at, blocks):
    """Pure: the sent cells whose block is gone in a fresh read (`name_at(cell)`) — their notes are spent; a cell
    still standing (sent, not broken) keeps its note. No read (`name_at` None): nothing is judged spent."""
    if name_at is None:
        return []
    kinds = {bare(b) for b in blocks}
    return sorted(c for c in sent if bare(name_at(c) or "air") not in kinds)


@skill(gives=K.GIVES_MINE, needs=lambda a: {} if a[4] is None else {f"tool:pickaxe:{a[4]}": 1}, speed={"shovel": DIG_SHOVEL_S},
       start=lambda c: Inventory().count(c.args[1]),
       done=lambda c: Inventory().count(c.args[1]) >= c.base + c.args[2], budget=900, stall=90,
       units=lambda c: c.args[2], key=lambda c: f"mine:{c.args[1]}",
       provides={"mine": lambda ctx, s: (s.token, s.count, s.detail["blocks"], s.detail["tier"],
                                          s.detail.get("breaks"))}, fills_bag=lambda c: members(c.args[1]))
def mine(ctx, token, count, blocks, tier, breaks=None):
    """Tunnel to the nearest reachable vein of `blocks` and mine it until `count` more `token` are held."""
    drop = token
    target = Inventory().count(drop) + count
    radius = SEEK_RADII[0]
    # unreachable is about where we stand, not the block: one budget for every way of not getting there, then fail as nav (the retry waits for a change of place)
    unreachable = 0
    empty_batches = 0    # batches the mod could not break at all; a few in a row means the seam really is dead
    tried = set()        # cells a batch already broke none of: a second refusal drops them (bag.refused)
    no_cell = set()      # seeds whose vein has no mineable cell from here: the next pass takes the next seed
    opened = set()       # cells the jar could not hold a stand at, given a side face once (then banned if refused again)
    sent = set()         # cells sent to the jar: their notes are retired once the count is met
    dug_out = False      # a batch that broke its cells but brought nothing in gets one dig-out and sweep
    for _ in range(10):
        have = Inventory().count(drop)
        if have >= target:
            # the count met: the notes of what was mined are spent (only a whole pass's end retired them — a count met
            # at the top of the next pass kept a mined diamond's note: seen_store__noted 10:16 "the note retired" False)
            if ctx.mem is not None and sent:
                seen_now = region_around(sorted(sent), pad=0)
                for p in spent_cells(sent, (lambda c: seen_now.name(c)) if seen_now is not None else None, blocks):
                    for b in blocks:
                        ctx.mem.forget_seen(b, p, ctx.dimension, radius=0.5)
            return
        yield None
        require_pickaxe(tier)
        # sealed or exposed alike (the jar's approach digs to buried blocks); remembered first, /find only when no noted cell is left
        notes = [n for b in blocks for n in ctx.mem.seen(b, ctx.dimension)] if ctx.mem is not None else []
        noted = [h for h in noted_hits(notes, blocks, ctx.blocked, ctx.policy.protected)
                 if (h["x"], h["y"], h["z"]) not in no_cell]
        raw = noted or find(blocks, radius=radius, limit=60)
        digs = "approach_dig" in nav.mod_features()
        exposed_cells = None if digs else {(h["x"], h["y"], h["z"])
                                           for h in find(blocks, radius=radius, limit=60, exposed=True)}
        fresh = [h for h in raw if (h["x"], h["y"], h["z"]) not in no_cell]
        if raw and not fresh:
            if radius < SEEK_RADII[-1]:
                radius = SEEK_RADII[-1]
                continue
            raise NotAvailable(f"no {blocks[0]} vein mineable from here ({len(no_cell)} seen, none with a cell to break)")
        hits = seek_hits(blocks, fresh, radius, ctx.blocked, ctx.policy.protected)
        if hits is None:
            api.detail(f"  mine {bare(drop)}: none within {radius}, looking wider")
            radius = SEEK_RADII[-1]
            continue
        start = feet()
        if swimming(api.get("/state")):
            raise NotAvailable("in the water: no digging until back on land")
        # which blocks have an open face is the world's answer (/find exposed), not a second model here
        seed = (hits[0]["x"], hits[0]["y"], hits[0]["z"])
        region = region_around([start, seed], pad=nav.SAFE_DROP + 2)   # deep enough to see a drop (bag.floored)
        if region is None and seed[1] < start[1] - 2 and ctx.policy.allow_dig:
            # too far to read at once and far below: the ground a staircase segment crosses is read, and the segment
            # dug — the seed read again from the foot of it (no blind shaft)
            d = nav.stair_dir(start, seed)
            leg_end = (start[0] + d[0] * nav.STAIR_STEPS, start[1] - nav.STAIR_STEPS, start[2] + d[1] * nav.STAIR_STEPS)
            leg = region_around([start, leg_end], pad=2)
            stairs = nav.stair_down_tasks(leg, start, seed, ctx.policy.protected) if leg is not None else []
            if stairs:
                api.detail(f"  mine {bare(drop)}: {seed} too far to read from {start}: a staircase segment down")
                api.run_chain(stairs, stop_on_failure=True, wait=120)
                continue
        if region is None:
            # too far to read the ground between: walk closer or make a way; only when neither works is the place the problem
            if not nav.arrived(seed, ctx.policy, range_=12) and not nav.way_to(ctx, {seed}):
                ctx.ban(seed)
                raise api.NavFailed(f"{blocks[0]} at {seed}: no way there and no tunnel")
            api.detail(f"  mine {bare(drop)}: {seed} too far to read from {start}, walked closer")
            continue
        if hits[0].get("noted") and bare(region.name(seed)) not in {bare(b) for b in blocks}:
            for b in blocks:
                ctx.mem.forget_seen(b, seed, ctx.dimension, radius=0.5)     # gone from where it was noted
            api.detail(f"  mine {bare(drop)}: noted {seed} is {region.name(seed)} now, note forgotten")
            continue
        vein = set(mineable((p for p in connected(region, seed, blocks) if not ctx.blocked(p)), start,
                            region, nav.SAFE_DROP))
        if not vein:
            # the whole connected vein is already proven unreachable: next seed
            api.detail(f"  mine {bare(drop)}: vein at {seed} ({region.name(seed)}) has no mineable cell from {start}")
            no_cell.add(seed)
            continue
        # never open a block touching lava or water unless the goal wants the fluid; surface blocks keep 2 from any fluid
        want = breaks or max(1, target - have)
        vein = set(sorted(vein, key=lambda p: math.dist(p, start))[: max(want, len(vein) if tier else want)])
        # reach a vein by walking if there is a way, else by digging one: buried ore has no path, and banning it left coal inside a wall forever
        near = min(vein, key=lambda p: math.dist(p, start))
        if near[1] < start[1] - 2 and ctx.policy.allow_dig:
            # far below: a staircase the body can walk back up, never travel's 1-wide shaft (brain__base: 9 deep,
            # no way out and no sight of the ore 2 blocks off)
            stairs = nav.stair_down_tasks(region, start, near, ctx.policy.protected)
            if stairs:
                api.run_chain(stairs, stop_on_failure=True, wait=120)
                continue
        walked = nav.arrived(near, ctx.policy, range_=3.5, attempts=1) if "travel" in nav.mod_features() else False
        if not walked and not nav.way_to(ctx, vein):
            for p in vein:
                ctx.ban(p)
            # the next vein is another target, not a retry: failing the step over one vein cooled the goal
            unreachable += 1
            _reach_budget(unreachable, blocks, f"no way and no tunnel to the {blocks[0]} vein at {seed}")
            continue
        # only blocks within reach of where travel left us, a dozen at a time
        here_now = feet()

        def workable(at):
            """The vein cells within reach of the stand at `at`, nearest first: the ones to work from here — sent
            to the jar when it can hold them (`holdable`), else opened / walked beside by the buried path below."""
            return sorted((p for p in vein if math.dist(p, at) <= nav.REACH), key=lambda p: math.dist(p, at))

        def holdable(at, cells):
            """The jar's own rule (nav.holds: sight, not distance — a pit 2 down cannot see an ore at its rim,
            search_night_resume 09:46:50 NO_STAND ×3; a buried ore has no face to see), the vein's other cells not in
            the way (they break in the same batch). Distance alone as the reach dropped buried ore before the opener
            (a2d37ae: the diamond rows never sent theirs)."""
            x0, y0, z0 = at
            sight = Region((x0 - 5, y0 - 4, z0 - 5), (x0 + 5, y0 + 6, z0 + 5))
            return {p for p in cells if nav.holds(sight, at, p, through=set(vein))}
        in_reach = workable(here_now)
        if not in_reach:
            near_cell = min(vein, key=lambda p: math.dist(p, here_now))
            if not nav.arrived(near_cell, ctx.policy, range_=2.0, attempts=1) and not nav.way_to(ctx, {near_cell}):
                for p in vein:
                    ctx.ban(p)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"{blocks[0]} at {near_cell}: no way there and no tunnel")
                continue
            here_now = feet()
            in_reach = workable(here_now)
            if not in_reach and not nav.way_to(ctx, vein):
                for p in vein:
                    ctx.ban(p)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"got near {near_cell} but no way in to the {blocks[0]}")
                continue
        # distance is not reachability: only open-faced blocks go to mine_many; travel digs a way to the nearest buried one
        held = holdable(here_now, in_reach)
        open_faced = [p for p in mineable(in_reach, here_now, region, nav.SAFE_DROP)
                      if (exposed_cells is None or p in exposed_cells) and p in held]
        if not open_faced:
            buried = in_reach[0]
            if not nav.arrived(buried, ctx.policy, range_=BESIDE, attempts=1):
                ctx.ban(buried)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"{blocks[0]} at {buried}: buried, and no way dug to it")
            continue
        vein = set(open_faced[:MINE_BATCH])
        if not ctx.policy.lava_ok:
            # seal every fluid face of what is about to break (seal_plan); with nothing to seal with, ban those cells (unless the goal wants the fluid)
            try:
                seal = seal_plan(region, sorted(vein), Inventory())
            except NotAvailable as e:
                wet = {c for c in vein if fluid_faces(region, c, vein)}
                log(f"   {len(wet)} {blocks[0]} cells not mined: {e}")
                for p in wet:
                    ctx.ban(p)
                vein -= wet
                if not vein:
                    continue
                seal = []
            if seal:
                api.run_chain(seal, stop_on_failure=True, wait=60)
        # a cell open only into pockets no body can stand in: the jar's mine never digs for a line of sight, so the
        # block on the body's side goes first (bag.opener; down-flagged when it lies below the feet)
        openers = sorted({op for c in vein if (op := opener(region, c, here_now, nav.SAFE_DROP)) is not None
                          and op not in ctx.policy.protected})
        if openers:
            api.run_chain([nav.mine_task(op, down=op[1] < here_now[1]) for op in openers], stop_on_failure=True, wait=60)
        before = Inventory().count(drop)
        sent |= set(vein)
        try:
            batch = mine_segment_commands({"inv": Inventory(), "feet": here_now}, (vein, drop, tier))
            r = nav.run_cells("mine_many", batch[:-1], then=batch[-1], wait=900)
        except api.Unreachable as out:
            around = {c: region.name(c) for c in out.cells or ()}
            api.detail(f"  mine {bare(drop)} refused by the jar ({out}): feet {feet()}, cells "
                       + "; ".join(f"{c} {n} faces {[region.name(add(c, d)) for d in nav.NEIGHBOURS6]}" for c, n in around.items()))
            # "cannot hold a stand spot": the jar found no spot that keeps the cell in sight (a top face seen only
            # from a pit's rim, search_night_resume 09:17:19) — open a side face once, then ask again; a ban only
            # when the opened cell is refused too
            held = [c for c in (out.cells or ()) if c not in opened and "cannot hold a stand spot" in str(out)]
            sides = [(c, op) for c in held if (op := opener(region, c, feet(), nav.SAFE_DROP, forced=True)) is not None
                     and op not in ctx.policy.protected]
            if sides:
                opened.update(c for c, _op in sides)
                api.run_chain([nav.mine_task(op, down=op[1] < feet()[1]) for _c, op in sides], stop_on_failure=True,
                              wait=60)
                continue
            # "cannot reach": make a standing spot and ask again; a way that changed nothing counts against the budget (from jar 0.1.40 its approach already dug)
            if "approach_dig" not in nav.mod_features() and nav.way_to(ctx, out.cells or vein):
                unreachable += 1
                _reach_budget(unreachable + 1, blocks, str(out))    # one more try than a plain refusal gets
                continue
            for p in (out.cells or vein):
                ctx.ban(p)
            unreachable += 1
            _reach_budget(unreachable, blocks, str(out))
            continue
        except api.TaskStuck as out:
            # the jar circled this batch (approach ↔ mine): refuse it, charge the budget, try the next vein
            for p in vein:
                ctx.ban(p)
            unreachable += 1
            _reach_budget(unreachable, blocks, str(out))
            continue
        if gained(lambda: Inventory().count(drop), before) <= before:
            from .knowledge import MINE_YIELD
            if MINE_YIELD.get(mid(drop), 1) < 1 and "failed" not in r["message"]:
                continue   # a chance drop (grass → seeds ~1 in 8): an empty break is expected, keep breaking
            # partial success is progress: some blocks broke, keep the batch (banning all over one block stalled the kit)
            import re as _re
            part = _re.search(r"(\d+) of (\d+) steps failed", r.get("message") or "")
            if part and int(part.group(1)) < int(part.group(2)):
                bad = cannot_reach(r.get("message"))
                # "no path" to a block inside rock: dig one face open and it is ordinary
                again, _ = refused(bad, tried, "approach_dig" in nav.mod_features())
                tried |= bad
                if again and ctx.policy.allow_dig and nav.way_to(ctx, again):
                    continue           # a face is open now: the same blocks, asked again — once
                for p in bad:
                    ctx.ban(p)      # only the blocks the mod named as unreachable, and only with no way in
                if bad:
                    # each mod refusal costs a full path search: charged to the same budget
                    unreachable += 1
                    _reach_budget(unreachable, blocks)
                continue
            if not dug_out and "failed" not in (r.get("message") or ""):
                # the batch broke its cells ("succeeded") and the bag gained nothing: the drop lies where the pickup
                # never went (a sealed cavity: brain__base "collecting items (0)") — open the cavity toward the body
                # once and sweep, not a silent success nor a ban of cells that are air now
                now = Region(tuple(min(c[i] for c in vein) - 1 for i in range(3)),
                             tuple(max(c[i] for c in vein) + 1 for i in range(3)))
                broken = [c for c in vein if not now.solid(c)]
                if broken:
                    dug_out = True
                    here = feet()
                    ops = sorted({op for c in broken if (op := opener(now, c, here, nav.SAFE_DROP, forced=True))
                                  is not None and op not in ctx.policy.protected})
                    api.detail(f"  mine {bare(drop)}: {len(broken)} broken, nothing in the bag: dig-out {ops}, then a sweep")
                    api.run_chain([nav.mine_task(op, down=op[1] < here[1]) for op in ops]
                                  + [{"type": "collect", "radius": 6}], wait=60)
                    continue
            again, _ = refused(vein, tried, "approach_dig" in nav.mod_features())
            tried |= vein
            if again and ctx.policy.allow_dig and nav.way_to(ctx, again):
                continue               # nothing broke because nothing could be stood next to: now it can, once
            for p in vein:
                ctx.ban(p)             # refused: dropped, never the same batch again
            # ban this batch and try the next: one unmineable batch is not a dead seam
            empty_batches += 1
            if empty_batches >= 3:
                raise NotAvailable(f"{blocks[0]} vein yielded nothing: {r['message']}")
            continue
        else:
            for b in blocks:                  # this vein is mined: its notes are spent
                ctx.mem.forget_seen(b, seed, ctx.dimension, radius=4)
    raise McError(f"could not mine enough {bare(drop)}")

STRIP_ORES = [("minecraft:diamond", ["diamond_ore", "deepslate_diamond_ore"], 2),
              ("minecraft:raw_iron", ["iron_ore", "deepslate_iron_ore"], 1),
              ("minecraft:raw_gold", ["gold_ore", "deepslate_gold_ore"], 2),
              ("minecraft:redstone", ["redstone_ore", "deepslate_redstone_ore"], 2),
              ("minecraft:lapis_lazuli", ["lapis_ore", "deepslate_lapis_ore"], 1),
              ("coal", ["coal_ore", "deepslate_coal_ore"], 0)]

def _stone_held():
    """What a tunnel yields whatever else it finds: stone of any kind in the bag."""
    return Inventory().count("stone") + Inventory().count("minecraft:cobbled_deepslate")

def _not_in_water(c):
    if api.get("/state")["inWater"]:
        raise NotAvailable("standing in water: no strip mining here")

@skill(gives=["state:tunnelled"], remaining=_k.tunnelled(lambda c: c.args[1] if len(c.args) > 1 else 16), speed={}, pre=[_not_in_water], needs={"tool:pickaxe:0": 1},
       start=lambda c: (feet()[1], _stone_held()),
       verify=lambda c: feet()[1] != c.base[0] or _stone_held() > c.base[1], budget=300, stall=60)
def strip_mine_step(ctx, length=16):
    """Always-available work: descend toward iron (diamond once an iron pickaxe exists) and tunnel, taking the ore revealed."""

    s = api.get("/state")
    fx, fy, fz = s["blockX"], s["blockY"], s["blockZ"]
    tools = Inventory().tools("pickaxe")
    best_tier = max((t for t, d, _ in tools if d >= 3), default=0)
    # diamonds peak near -58, bedrock starts at -60..-64: tunnel at -54
    depth = -54 if any(t >= 2 and d >= 20 for t, d, _ in tools) else 16
    if fy < depth - 3:
        # drifted below the band: staircase back up rather than tunnel into bedrock
        if not nav.arrived((fx, depth, fz), ctx.policy, range_=2, attempts=1):
            raise api.NavFailed(f"can't climb back up to mining depth {depth}")
        return "climbed"
    if fy > depth + 4:
        try:
            nav.dig_down(min(12, fy - depth), ctx.policy, use_ladders=Inventory().count("minecraft:ladder") >= 12)
        except NotAvailable:
            # unsafe here (water/lava/cave below): find dry solid ground nearby
            stone = [h for h in find(["stone", "deepslate", "dirt", "grass_block"], radius=24, limit=40)
                     if h["y"] <= fy and math.dist((h["x"], h["z"]), (fx, fz)) >= 6]
            if not stone:
                raise NotAvailable("no safe ground to dig down nearby")
            t = stone[0]
            if not nav.arrived((t["x"], t["y"] + 1, t["z"]), ctx.policy, range_=2, attempts=1):
                raise NotAvailable("can't reach safe ground to dig down")
        return
    facing = TUNNEL_DIRS[int(((s["yaw"] % 360) + 45) // 90) % 4]
    region = Region((fx - length - 1, fy - 1, fz - length - 1), (fx + length + 1, fy + 2, fz + length + 1))
    # mobs and caves are read through the walls: the tunnel goes where neither is
    hostiles = [(e["x"], e["y"], e["z"]) for e in entities(24) if beliefs.fights_back([e["type"]])]
    (dx, dz), end, cave = plan_tunnel(region, (fx, fy, fz), length, ctx.policy.protected, hostiles, facing)
    tasks = []
    if cave:
        # every way opens onto a cave at once: its faces sealed first (the one sealing rule), then the step dug
        own = {(fx, fy, fz), (fx, fy + 1, fz)}
        tasks = seal_plan(region, cave, Inventory(), own=own, cave=True) + [nav.mine_task(c) for c in cave
                                                                         if region.solid(c)]
        end = 1
    for i in range(1 if not cave else 2, end + 1):
        tasks += [nav.mine_task(c) for c in [(fx + dx * i, fy + 1, fz + dz * i), (fx + dx * i, fy, fz + dz * i)]
                  if region.solid(c)]
    if end == 0:
        # turning in place is not progress: say so and let the retry policy decide
        api.run({"type": "look", "yaw": (s["yaw"] + 90) % 360, "pitch": 0}, awaits="the next sight line read")
        raise NotAvailable("tunnel blocked ahead (turned to try another direction)")
    tasks.append({"type": "goto", "x": fx + dx * end, "y": fy, "z": fz + dz * end, "range": 1, "partial": True})
    log(f"strip mining {end} blocks at y={fy}")
    api.run_chain(tasks, stop_on_failure=True, before_segment=ctx.policy.before_segment)
    for token, blocks, tier in STRIP_ORES:
        if tier <= best_tier and find(blocks, radius=5, limit=1):
            try:
                mine(ctx, token, 1, blocks, tier)
            except NotAvailable:
                pass

def _hunt_progress(token, types):
    near = entities(64, types)
    # closing in (4-block bins) or collecting drops is progress; circling is not
    return Inventory().count(token), (int(near[0]["distance"] // 4) if near else None)

@skill(gives=K.GIVES_HUNT, needs=lambda a: {"tool:sword:1": 1} if beliefs.fights_back(a[3]) else {},
       speed={"sword": HUNT_SWORD_S}, start=lambda c: Inventory().count(c.args[1]), done=lambda c: Inventory().count(c.args[1]) >= c.base + c.args[2],
       budget=480, stall=60, units=lambda c: c.args[2], key=lambda c: f"hunt:{c.args[1]}",
       provides={"hunt": lambda ctx, s: (s.token, s.count, s.detail["types"], getattr(ctx, "night", False))},
       fills_bag=lambda c: members(c.args[1]))
def hunt(ctx, token, count, types, night):
    """Kill animals of `types` (reach them with the navigator first) until `count` more `token` drops are held."""
    target = Inventory().count(token) + count
    for _ in range(count * 3 + 3):
        if Inventory().count(token) >= target:
            return
        yield _hunt_progress(token, types)
        prey = [e for e in entities(64, types) if not ctx.blocked((e["id"], 0, 0))]
        if not prey:
            if night:
                raise NotAvailable(f"no {types[0]} nearby at night")
            prey = explore_for(ctx, types)
            if not prey:
                raise NotAvailable(f"no {bare(types[0])} found")
        before = Inventory().count(token)
        e = prey[0]
        # reach it with the navigator first: a blind chase from underground never lands a hit
        if e["distance"] > 4 and _k.under_rock(api.get("/state").get("skyLight", 15)) and e["y"] > feet()[1] + 3:
            # in a cave below the animal: climb out (digging allowed) first
            surface_first(ctx)
            continue
        if e["distance"] > 4:
            # walk and bridge to animals, never tunnel
            nav.arrived((math.floor(e["x"]), math.floor(e["y"]), math.floor(e["z"])), approach_policy(ctx.policy),
                      range_=3, attempts=2)
            e = next((n for n in entities(64, types) if n["id"] == e["id"]), None)
            if e is None or e["distance"] > 6:
                ctx.ban((prey[0]["id"], 0, 0), 300)
                raise api.NavFailed(f"could not get to the {bare(types[0])}")
        try:
            api.run({"type": "attack", "entity": e["id"]}, wait=30, awaits="the mob dead")
            # the drop can land where nothing stands: `sweep` makes a way to it before a kill is written off
            nav.sweep(ctx, radius=6, only=[token], wait=60)
        except api.TaskStuck:
            ctx.ban((prey[0]["id"], 0, 0), 600)  # unreachable (across water, on a ledge)
            raise api.NavFailed(f"the {bare(types[0])} is out of reach for attacks")
        if gained(lambda: Inventory().count(token), before) <= before:
            ctx.ban((prey[0]["id"], 0, 0), 120)
            raise NotAvailable(f"killing the {bare(types[0])} dropped no {bare(token)}")
    raise McError(f"could not hunt enough {bare(token)}")

# -- light, food, night

def _open_lava(region, here):
    """Pure: lava cells within reach of `here` with air beside them, nearest first."""
    x, y, z = here
    return sorted((p for p in region.blocks if region.name(p) == "lava"
                   and math.dist(p, (x, y + 1, z)) <= 4.5
                   and any(region.name(add(p, d)) in ("air", "cave_air") for d in nav.NEIGHBOURS6)),
                  key=lambda p: math.dist(p, (x, y, z)))

def _lava_region(here, radius):
    x, y, z = here
    return Region((x - radius - 1, y - radius - 1, z - radius - 1), (x + radius + 1, y + radius + 1, z + radius + 1))

def _open_lava_now(ctx, radius=4):
    """How many lava cells lie open within reach right now (0 without a world read when /find sees none)."""
    if getattr(ctx.policy, "lava_ok", False) or not find(["lava"], radius=radius, limit=1):
        return 0
    here = feet()
    return len(_open_lava(_lava_region(here, radius), here))

def fill_with_blocks(cells, inv, why, partial=False):
    """Pure: one place task per cell, in order, building blocks taken from the bag in turn."""

    stock = [[b, inv.count(b)] for b in GROUPS["building"] if inv.count(b)]
    if cells and (not stock or (not partial and sum(n for _, n in stock) < len(cells))):
        raise NotAvailable(f"{why}: {len(cells)} to cover, {sum(n for _, n in stock)} blocks carried")
    tasks = []
    for p in cells:
        while stock and stock[0][1] <= 0:
            stock.pop(0)
        if not stock:
            break
        stock[0][1] -= 1
        tasks.append({"type": "place", "item": stock[0][0], "x": p[0], "y": p[1], "z": p[2]})
    return tasks

FLUID_NAMES = ("water", "lava", "flowing_water", "flowing_lava")

CAVE_AIR = ("air", "cave_air")


def fluid_faces(region, cell, breaking=(), names=FLUID_NAMES):
    """Pure: the cells of `names` (fluids; with cave air, openings) touching `cell` face to face (below, beside,
    above) — what pours or walks in once it is broken. `breaking` (the cells broken with it) are not faces."""

    out = []
    for d in ((0, -1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0)):
        n = add(cell, d)
        if n not in breaking and region.inside(n) and bare(region.name(n)) in names:
            out.append(n)
    return out

def seal_plan(region, cells, inv, own=(), cave=False):
    """Pure: before breaking `cells`, a block into every fluid cell touching one — the one sealing rule of mining;
    with `cave`, cave air too (an opening a tunnel would break into), the tunnel's own space (`own`) excepted."""

    cells = [tuple(c) for c in cells]
    names = FLUID_NAMES + (CAVE_AIR if cave else ())
    wet = []
    for c in cells:
        for f in fluid_faces(region, c, set(cells) | set(own), names):
            if f not in wet:
                wet.append(f)
    return fill_with_blocks(wet, inv, ("an opening" if cave else "fluid")
                            + " beside the cells to break and nothing to seal it with")


TUNNEL_DIRS = [(0, 1), (-1, 0), (0, -1), (1, 0)]


def tunnel_run(region, feet_at, d, length, protected=()):
    """Pure: (end, cave_cells) — how many steps a 2-high tunnel goes from `feet_at` toward `d` before it stops
    (no floor, unbreakable, a hazard beside, ours) or would break into a cave: `cave_cells`, the step's two cells
    that open onto air not the tunnel's own (read through the walls, before a block is broken), else None."""

    fx, fy, fz = feet_at
    dx, dz = d
    own = {(fx, fy, fz), (fx, fy + 1, fz)}
    end = 0
    for i in range(1, length + 1):
        cells = [(fx + dx * i, fy + 1, fz + dz * i), (fx + dx * i, fy, fz + dz * i)]
        floor = (fx + dx * i, fy - 1, fz + dz * i)
        if (not region.solid(floor) or any(region.unbreakable(c) for c in cells)
                or any(region.hazard(c) or region.hazard(add(c, n))
                       for c in cells for n in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, -1)])
                or any(c in protected for c in cells)):
            break
        own |= set(cells)
        if any(fluid_faces(region, c, own, CAVE_AIR) for c in cells):
            return end, cells
        end = i
    return end, None


def plan_tunnel(region, feet_at, length, protected=(), hostiles=(), facing=None):
    """Pure: (direction, end, cave_cells) of the tunnel to dig — the direction running longest before a cave, ties
    to the one ending farthest from the hostiles seen through the walls, then the facing. A tunnel never breaks into
    a cave it could have avoided; when every way opens onto one at once, its cells come back to be sealed first."""

    order = sorted(TUNNEL_DIRS, key=lambda d: d != facing)
    best = None
    for d in order:
        end, cave = tunnel_run(region, feet_at, d, length, protected)
        tip = (feet_at[0] + d[0] * end, feet_at[1], feet_at[2] + d[1] * end)
        away = min((math.dist(tip, h) for h in hostiles), default=math.inf)
        score = (end, away)
        if best is None or score > best[0]:
            best = (score, d, end, cave)
    _score, d, end, cave = best
    return d, end, (cave if end == 0 else None)

def contain_lava_commands(state, args=()):
    """Pure: one place task per open lava cell, nearest first, blocks taken from the bag in turn."""
    open_lava = _open_lava(state["region"], state["feet"]) if state["region"] is not None else []
    return fill_with_blocks(open_lava, state["inv"], "lava exposed and no blocks to cover it", partial=True)

@skill(gives=["state:lava_covered"], remaining=_k.blocks_gone("lava"), needs={"building": 1}, speed={}, start=lambda c: _open_lava_now(c.args[0], c.args[1] if len(c.args) > 1 else 4),
       verify=lambda c: c.base == 0 or _open_lava_now(c.args[0], c.args[1] if len(c.args) > 1 else 4) < c.base,
       commands=contain_lava_commands, budget=120, stall=30)
def contain_lava(ctx, radius=4):
    """Cover lava exposed within reach with building blocks, nearest first, before the tunnel goes on."""

    if ctx.policy.lava_ok:
        return 0
    if not find(["lava"], radius=radius, limit=1):
        return 0
    here = feet()
    tasks = contain_lava_commands(body_state(ctx, _lava_region(here, radius)), (radius,))
    covered = sum(1 for r in api.run_chain(tasks) if r["status"] == "succeeded")
    if covered:
        log(f"covered {covered} exposed lava cells")
    return covered

def dark_here(s):
    """Pure over /state: standing where mobs spawn — block light 0, and not under open sky by day."""
    return "blockLight" in s and s["blockLight"] <= 0 and not (s["skyLight"] > 7 and 0 < s["timeOfDay"] < 12500)

def torch_commands(state, args=(4, 1)):
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

from .knowledge import RAW_MEAT  # noqa: E402  (one food table: knowledge.ALL_FOOD / RAW_MEAT)
from .data import FULL_BAR, NUTRITION  # noqa: E402

def bites_to_full(food, carried, raw_ok=False):
    """Pure: (item, bites) to fill the bar from `food`: the best fit for the gap, raw meat only when `raw_ok`."""

    from .knowledge import ALL_FOOD
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

def eat_commands(state, args):
    """`commands` for eat: one eat task per planned bite, back to back (one bite a round left the bar hungry)."""

    from .knowledge import ALL_FOOD
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
@skill(gives=["state:fed"], remaining=_k.fed, needs={}, speed={}, start=lambda c: api.get("/state")["food"], verify=_fed_as_planned,
       commands=lambda state, args: eat_commands(state, args), budget=30, stall=30,
       provides={"eat": lambda ctx, s: (bool(s.detail.get("raw_ok")),)})
def eat(ctx=None, raw_ok=False):
    """Eat until the bar is full, every planned bite as one chain; an interruption stops it where it is."""

    from .knowledge import ALL_FOOD
    st = body_state(ctx) if ctx is not None else {"state": api.get("/state"), "inv": Inventory()}
    tasks = eat_commands(st, (raw_ok,))
    carried = {f: st["inv"].count(f) for f in ALL_FOOD + (RAW_MEAT if raw_ok else [])}
    if not tasks:
        if any(carried.values()):
            return False                    # full: nothing to eat for
        raise NotAvailable("nothing edible carried" + ("" if raw_ok else " (raw meat not allowed: not starving)"))
    food = st["state"].get("food", 0)
    target = min(FULL_BAR, food + sum(NUTRITION[t["item"].split(":")[-1]] for t in tasks))
    started = time.time()
    api.run_chain(tasks, stop_on_failure=True)
    took = (time.time() - started) / len(tasks)
    if 0.05 <= took <= 30.0:            # a queued or interrupted bite times the queue, not the bite
        beliefs.note("engage.eat_s", round(took, 3), where="eat")
    return target

def swimming(state):
    """The one "in the water" test: in water and not standing, or standing with the head under (breath below full)."""

    return bool(state.get("inWater")) and (not state.get("onGround", False)
                                            or float(state.get("air", AIR_FULL) or 0) < AIR_FULL)

def _on_land():
    return not swimming(api.get("/state"))

@skill(gives=["state:footing"], remaining=_k.standing, needs={"building": 1}, speed={}, done=lambda c: bool(api.get("/state").get("onGround")), budget=30, stall=20,
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

def bridge_commands(state, args):
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

@skill(gives=["state:bridged"], remaining=_k.near(lambda c: c.args[1], lambda c: BRIDGE_REACH), needs={"building": 1}, speed={}, start=lambda c: feet(), verify=_bridged_nearer, commands=bridge_commands, budget=120, stall=45)
def bridge_toward(ctx, target):
    """Path blocked: make the way toward `target` by hand instead of asking the walker again."""

    target = tuple(target)
    tasks = bridge_commands(body_state(ctx, bridge_region(feet(), target)), (target,))
    if not tasks:
        raise NotAvailable("path blocked and nothing to bridge with")
    api.run_chain(tasks, stop_on_failure=True, before_segment=ctx.policy.before_segment)
    return feet()

@skill(gives=["state:ashore"], remaining=_k.on_dry_ground, needs={}, speed={}, done=lambda c: _on_land(), budget=180, stall=45, provides={"reach:land": lambda ctx, s: ()})
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

@skill(gives=["state:sheltered"], needs={"tool:pickaxe:0": 1}, speed={}, remaining=lambda st, c: shelter_left(st, c), done=lambda c: enclosed(), budget=90, stall=40, commands=lambda st, a: burrow_commands(st, a),
       provides={"state:sheltered": lambda ctx, s: () if _burrow_here(ctx) else None,
                 "shelter:burrow": lambda ctx, s: ()})
def burrow(ctx):
    """Night shelter in a hillside: tunnel 2 in, step to the end, seal the entrance (both faces visible from inside), one chain."""

    keep = current_call().keep
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

@skill(gives=["state:outside"], remaining=lambda st, c: outside_left(st, c), needs={}, speed={}, done=lambda c: not enclosed(), budget=90, stall=45, commands=lambda st, a: dig_out_commands(st, a),
       provides={"reach:outside": lambda ctx, s: ()})
def dig_out(ctx):
    """Morning in a sealed pod: open one side (by hand if no pickaxe) and step out, one chain."""

    x, y, z = feet()
    tasks = dig_out_commands(body_state(ctx, Region((x - 3, y - 2, z - 3), (x + 3, y + 3, z + 3))))
    run_split(tasks, wait=60)
    yield feet()
    log(f"dug out of the shelter toward {tuple(tasks[-1][k] for k in 'xyz')}")

@skill(gives=["state:head_clear"], remaining=_k.head_clear, needs={}, speed={}, done=lambda c: not head_buried(), budget=30, stall=15)
def unbury(ctx):
    """Suffocating in a block: break the block at eye level, then the one above it if sand/gravel keeps falling."""
    for _ in range(4):
        s = api.get("/state")
        eye = (s["blockX"], math.floor(s["y"] + 1.62), s["blockZ"])
        api.run(nav.mine_task(eye), wait=15, awaits="the eye cell read again (sand keeps falling)")
        yield eye

AIR_FULL = 300          # the air meter's top, in ticks
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

@skill(gives=["state:air"], remaining=_k.breathing, needs={}, speed={}, done=lambda c: _breathing_now(), verify=lambda c: _breathing(), budget=45, stall=12,
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

def bed_spot():
    s = api.get("/state")
    fx, fy, fz = s["blockX"], s["blockY"], s["blockZ"]
    region = Region((fx - 4, fy - 2, fz - 4), (fx + 4, fy + 2, fz + 4))
    for dist in (1, 2, 3):
        for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            for dy in (0, 1, -1):
                foot = (fx + dist * dx, fy + dy, fz + dist * dz)
                head = (foot[0] + dx, foot[1], foot[2] + dz)
                if all(region.name(c) == "air" and region.solid(add(c, (0, -1, 0))) for c in (foot, head)):
                    return foot
    return None

# when a bed works (Mojang's rule): outside it using a bed does nothing
SLEEP_FROM_TICKS, SLEEP_TO_TICKS = 12541, 23458

def _morning(timeout=7.0):
    """Lain in a bed: read the clock until it is morning (the night skipped, ~5 s), at most `timeout` s."""
    day = lambda t: int(t) % 24000 < 12500      # noqa: E731
    return day(settle(lambda: api.get("/state")["timeOfDay"], day, timeout=timeout, stable_s=0.0, soft=True))

def can_sleep(state):
    """None when a bed would work now, else why it would not."""

    if state.get("dimension", "minecraft:overworld") != "minecraft:overworld":
        return "a bed explodes outside the Overworld"
    if state.get("thundering"):
        return None
    t = int(state.get("timeOfDay", 0)) % 24000
    if SLEEP_FROM_TICKS <= t <= SLEEP_TO_TICKS:
        return None
    return "a bed only works at night (or in a thunderstorm)"

def _day_now():
    from .data import DAY_END, NIGHT_END
    t = int(api.get("/state")["timeOfDay"]) % 24000
    return not DAY_END <= t <= NIGHT_END

@skill(gives=["state:day"], remaining=_k.daytime, needs={}, speed={}, done=lambda c: _day_now(), budget=600, stall=60, provides={"wait:day": lambda ctx, s: ()})
def wait_for_day(ctx):
    """Sit the night out where we are, in ten-second waits, until the sun is up."""

    while True:
        api.run({"type": "wait", "ticks": 200}, wait=15, awaits="the time of day")
        yield api.get("/state")["timeOfDay"]

# needs: none the bag can state — a bed carried or one standing nearby
@skill(gives=["state:day"], remaining=_k.daytime, needs={}, speed={}, verify=lambda c: api.get("/state")["timeOfDay"] < 12500, budget=240, stall=60,
       provides={"sleep": lambda ctx, s: (_night_policy(ctx),)})
def sleep(ctx, night_policy):
    """Sleep through the night: carried bed first (placed next to us, picked up after), then a nearby site bed."""
    why = can_sleep(api.get("/state"))
    if why:
        raise NotAvailable(why)
    inv = Inventory()
    bed = next((b for b in GROUPS["bed"] if inv.count(b)), None)
    if bed:
        spot = bed_spot()
        if spot is None:
            raise NotAvailable("no flat 2-block spot for the bed")
        use = {"type": "use", "x": spot[0], "y": spot[1], "z": spot[2]}
        try:
            # placed and lain in as one chain; morning is read, not waited for
            chain = [{"type": "place", "item": bed, "x": spot[0], "y": spot[1], "z": spot[2]}, use]
            for _ in range(3):
                api.run_chain(chain, stop_on_failure=True, wait=30)
                if _morning():
                    ctx.mem.slept()
                    log("slept (carried bed)")
                    return
                chain = [use]
            raise NotAvailable("could not fall asleep (monsters nearby?)")
        finally:
            mine_cell(ctx.policy, spot, wait=60)
    beds = find(BASE_MARKERS["bed"], radius=48, limit=1)
    if not beds:
        raise NotAvailable("no bed carried or nearby")
    b = (beds[0]["x"], beds[0]["y"], beds[0]["z"])
    if not nav.arrived(b, night_policy, range_=2.5, attempts=2):
        raise NotAvailable("bed not walkable tonight")
    for _ in range(3):
        api.run_chain([{"type": "use", "x": b[0], "y": b[1], "z": b[2]}], wait=30)
        if _morning():
            ctx.mem.slept()
            log("slept (site bed)")
            return
    raise NotAvailable("could not fall asleep in the site bed")

DIG_IN_DEPTH = 3

def dig_in_start(region, feet):
    """Pure: where a dig-in began: one above the top of the walled shaft the body stands in, else the feet."""

    x, y, z = feet
    top = y
    while top - y < DIG_IN_DEPTH and not region.solid((x, top, z)) and \
            all(region.solid((x + dx, top, z + dz)) for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]):
        top += 1
    return x, top, z

def dig_in_commands(state, args=()):
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

    from .terrain import SOFT_RADIUS, nearest_soft
    x, y, z = feet()
    region = Region((x - SOFT_RADIUS, y - DIG_IN_DEPTH - 2, z - SOFT_RADIUS), (x + SOFT_RADIUS, y + 3, z + SOFT_RADIUS))
    return nearest_soft(region, (x, y, z), DIG_IN_DEPTH)

def soft_ground_here():
    """Seconds' walk to hand-diggable ground (0: underfoot), or None when none near."""

    return night_ground()[0]


def dig_in_site(region, feet_at, protected=()):
    """Pure: can a dig-in finish here — DIG_IN_DEPTH cells safe to dig under the column (nav.dig_down_tasks, from
    where a started dig began: dig_in_start), so its lid sits below the ground line? A 3-thick floor over air
    gives 2: not offered (search_night_resume chose it, then 'only 2 of 3 safe')."""
    start = dig_in_start(region, tuple(feet_at))
    try:
        _tasks, safe = nav.dig_down_tasks(region, start, DIG_IN_DEPTH, protected, False, dug_to=feet_at[1])
    except NotAvailable:
        return False
    return safe >= DIG_IN_DEPTH


def night_ground():
    """One region read around the feet for the night's pricing: (seconds' walk to hand-diggable ground or None,
    whether a dig-in can finish right here)."""

    from .data import WALK_BLOCKS_PER_TICK
    from .terrain import SOFT_RADIUS, nearest_soft
    x, y, z = feet()
    region = Region((x - SOFT_RADIUS, y - DIG_IN_DEPTH - 2, z - SOFT_RADIUS), (x + SOFT_RADIUS, y + 3, z + SOFT_RADIUS))
    spot = nearest_soft(region, (x, y, z), DIG_IN_DEPTH)
    return (None if spot is None else spot[1] / (WALK_BLOCKS_PER_TICK * 20)), dig_in_site(region, (x, y, z))

@skill(gives=["state:sheltered"], needs={}, speed={"shovel": DIG_SHOVEL_S}, remaining=lambda st, c: dug_in_left(st, c), start=lambda c: feet(), verify=lambda c: feet()[1] < c.base[1] and enclosed(), commands=dig_in_commands,
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

def _take_needs(token):
    """The tool a take of `token` needs, from the one table (knowledge.TAKEABLE's `tool`, as MINE's tier is mine's)."""
    tool = K.TAKEABLE.get(token, {}).get("tool")
    return {} if tool is None else {f"tool:{tool[0]}:{tool[1]}": 1}

@skill(gives=K.GIVES_TAKE, needs=lambda a: _take_needs(a[1]), speed={}, start=lambda c: Inventory().count(c.args[1]), verify=lambda c: Inventory().count(c.args[1]) > c.base,
       budget=180, stall=45, provides={"take": lambda ctx, s: (s.token, s.count, s.detail["blocks"])},
       fills_bag=lambda c: members(c.args[1]))
def take(ctx, token, count, blocks):
    """Break blocks that ARE the thing and pick them up: a village's bed, furnace, table, hay, crops."""

    want = int(count)
    got = 0
    for _ in range(want * 2):
        if got >= want:
            return got
        hits = [h for h in (find(blocks, radius=48, limit=20) or ())
                if not ctx.blocked((h["x"], h["y"], h["z"]))
                and (h["x"], h["y"], h["z"]) not in ctx.policy.protected]
        if bare(token) == "wheat":
            # taken ripe or not at all: green wheat breaks into seeds
            ripe = set(ripe_near(feet(), 48))
            hits = [h for h in hits if (h["x"], h["y"], h["z"]) in ripe]
        if not hits:
            raise NotAvailable(f"no {bare(blocks[0])} within reach to take")
        cell = (hits[0]["x"], hits[0]["y"], hits[0]["z"])
        if not nav.arrived(cell, ctx.policy, range_=3, attempts=2):
            ctx.ban(cell)
            continue
        mine_cell(ctx.policy, cell, wanted=[token], require_drops=False, wait=60)
        api.run({"type": "collect", "radius": 4}, wait=20, awaits="the drops the break left, counted after")
        yield None
        got += 1
        # the block is gone whether or not the drop reached the bag: forget it, or we walk back to an empty square
        ctx.mem.forget_seen(bare(hits[0]["block"]), cell, ctx.dimension, radius=1)
        log(f"took {bare(token)} at {cell} ({got}/{want})")
    return got

def enclosed():
    """True when the body has no way out: every side blocked at feet or head height, and covered overhead."""
    s = api.get("/state")
    x, y, z = s["blockX"], s["blockY"], s["blockZ"]
    return is_enclosed(Region((x - 1, y - 1, z - 1), (x + 1, y + 2, z + 1)), (x, y, z))

def stored_left(st, c):
    """`remaining` of deposit: nothing beyond the keep list left in the bag (bag.store_plan)."""
    rest_ = store_plan(st["inv"].slots)
    return {} if not rest_ else {"to store": len(rest_)}

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

def pod_commands(state, args=()):
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

@skill(gives=["state:sheltered"], needs={"building": POD_BLOCKS}, speed={}, remaining=lambda st, c: shelter_left(st, c), done=lambda c: enclosed(), commands=pod_commands, budget=120, stall=40,
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

# -- machines (blueprints.py)

def _has_torches_to_spare(c):
    if Inventory().usable("minecraft:torch") <= 2:
        raise NotAvailable("no torches to spare")

def _torches_standing(radius=12):
    return len(find(["torch", "wall_torch"], radius=radius, limit=64) or ())

@skill(gives=["state:lit"], remaining=_k.few_dark, speed={}, pre=[_has_torches_to_spare], needs={"minecraft:torch": 3}, start=lambda c: _torches_standing(),
       verify=lambda c: _torches_standing() > c.base, commands=torch_commands, budget=180, stall=60,
       provides={"light": lambda ctx, s: (int(s.detail.get("radius", 10)), max(1, s.count))})
def light_area(ctx, radius=10, limit=6):
    """Spawn-proof the surroundings: torches on the darkest reachable spots (block light 0) nearby, keeping 2."""

    if enclosed():
        raise NotAvailable("sealed in: nothing outside to light")
    spots = [p for p in dark_spots(radius=radius, max_light=0, limit=40) if not ctx.blocked((p["x"], p["y"], p["z"]))]
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

@skill(gives=["state:smelter_loaded"], remaining=_k.less_than_at_start(lambda c: c.args[2], lambda c: min(64, c.args[3])), needs={}, speed={}, start=lambda c: Inventory().count(c.args[2]), verify=lambda c: Inventory().count(c.args[2]) < c.base,
       budget=300, stall=60, provides={"smelt": _smelter_for}, prefer=1)
def load_smelter(ctx, machine, input_token, count, fuel, output):
    """Load an auto smelter's input and fuel chests and note the output as pending, so the planner counts it on its way."""

    roles = _machine_roles(machine)
    _go_to_machine(ctx, machine)
    inv = Inventory()
    inputs = [m for m in members(input_token) if inv.count(m)]
    fuels = [m for m in members(fuel) if inv.count(m)]
    count = min(64, count, sum(inv.count(m) for m in inputs))
    fuel_n = math.ceil(count / 8) if fuel == "coal" else math.ceil(count / 1.5)
    close_screen()
    _open_container(roles["input"])
    try:
        move_into(inputs, _empty_container_slot(), count)
    finally:
        api.post("/close")
    _open_container(roles["fuel"])
    try:
        move_into(fuels, _empty_container_slot(), fuel_n)
    finally:
        api.post("/close")
    ready = 10 * count + 20
    ctx.mem.add_pending(machine["name"], output, count, time.time() + ready)
    log(f"loaded {count}× {bare(input_token)} into {machine['name']} (ready in ~{ready}s)")
    return {"ordered": output, "count": count}      # an order: done when the output is held

def pending_ready(machine):
    return any(p["ready_at"] <= time.time() for p in machine.get("pending", []))

@skill(gives=["state:machine_emptied"], remaining=_k.machine_emptied(lambda c: c.args[1]["name"]), needs={}, speed={}, start=lambda c: sum(p["count"] for p in c.args[1].get("pending", [])),
       verify=lambda c: sum(p["count"] for p in c.args[1].get("pending", [])) < c.base,
       budget=300, stall=60)
def collect_machine(ctx, machine):
    """Empty a machine's output chest into the inventory and settle its pending outputs."""
    roles = _machine_roles(machine)
    _go_to_machine(ctx, machine)
    before = {p["item"]: Inventory().count(p["item"]) for p in machine.get("pending", [])}
    close_screen()
    _open_container(roles["output"])
    try:
        for s in world.container()["slots"]:
            if s["owner"] != "player" and s["id"] != "minecraft:air":
                api.post("/click", {"slot": s["slot"], "button": 0, "action": "QUICK_MOVE"})
    finally:
        api.post("/close")
    got = {item: gained(lambda item=item: Inventory().count(item), n) - n for item, n in before.items()}
    ctx.mem.settle_pending(machine["name"], got)
    log(f"collected from {machine['name']}: {got}")

# -- base life

@skill(gives=["state:room"], remaining=_k.slots_free(FREE_SLOTS_TARGET), needs={}, speed={}, start=lambda c: Inventory().used_slots(), verify=lambda c: Inventory().used_slots() < c.base,
       budget=60, stall=30, provides={"room:tidy": lambda ctx, s: ()})
def tidy_inventory(ctx):
    """Free slots with no chest: drop the least valuable stacks to FREE_SLOTS_TARGET, then step away so they aren't picked back up."""

    close_screen()
    x, y, z = feet()
    region = Region((x - 3, y - 1, z - 3), (x + 3, y + 2, z + 3))
    direction = throw_direction(region, (x, y, z))
    if direction is None:
        # a sealed shaft: dropped items land at our feet and are picked back up
        raise NotAvailable("no open side to throw items into (shaft)")
    inv = Inventory()
    # priced by what each stack costs to get again; nothing dropped with lava near
    lava = bool(find(["lava"], radius=3, limit=1))
    need = max(0, inv.used_slots() - (BAG_SLOTS - FREE_SLOTS_TARGET))
    throw = [s for s, how in let_go(inv.slots, need, ctx.prices().get, lava_near=lava) if how == "drop"]
    if not throw:
        raise NotAvailable("nothing to throw away")
    # face the open side so drops fly where we won't walk
    yaw = {(1, 0): -90.0, (-1, 0): 90.0, (0, 1): 0.0, (0, -1): 180.0}[direction]
    api.run({"type": "look", "yaw": yaw, "pitch": 0}, wait=5, awaits="facing the open side before the throw clicks (UI, not tasks)")
    for s in throw:
        api.post("/click", {"slot": screen_slot(s["slot"]), "button": 1, "action": "THROW"})
        yield s["slot"]
    log(f"threw away {len(throw)} stacks toward {direction}: {sorted({bare(s['id']) for s in throw})}")
    # step away from the drops before the pickup delay ends
    spots = [p for p in free_spots_here(reach=4, limit=12)
             if (p[0] - x) * direction[0] + (p[2] - z) * direction[1] < 0]
    if spots:
        far = max(spots, key=lambda p: math.dist(p, (x, y, z)))
        api.run({"type": "goto", "x": far[0], "y": far[1], "z": far[2], "range": 0.8, "partial": True}, wait=15, awaits="away from the thrown drops before their pickup delay ends")

def site_trek_ok(ctx, site):
    """A storage trek there has not just failed: the site's cell is not banned (the one ban list, `Context.ban`)."""
    return not ctx.blocked(tuple(site["pos"]))

def _place_cache_chest(ctx):
    """A chest right here for storage when no site with a chest is reachable; becomes a 'cache' site."""
    near = [c for c in find(["chest", "barrel"], radius=6, limit=6) if openable_container((c["x"], c["y"], c["z"]))]
    if near:
        return near[0]["x"], near[0]["y"], near[0]["z"]
    here = feet()
    if Inventory().used_slots() < BAG_SLOTS:
        # the carried chest is a last resort for a completely full bag
        raise NotAvailable("bag not completely full: no new cache chest")
    if any(math.dist(s["pos"], here) <= 24 for s in ctx.mem.sites(ctx.dimension, kinds=["cache"])):
        # one cache per area; beyond it throwing frees slots
        raise NotAvailable("a cache chest already exists within 24 blocks")
    if Inventory().usable("minecraft:chest") == 0:
        # the result needs somewhere to go: drop the two least valuable stacks first
        if Inventory().free_slots() <= 1:
            make_bag_room(ctx, 2)
        if Inventory().usable("planks") < 8:
            if Inventory().usable("log") >= 2:
                craft(ctx, "planks", 2)
            else:
                raise NotAvailable("no chest and not enough planks for a cache chest")
        craft(ctx, "minecraft:chest", 1)
    x, y, z = feet()
    around = Region((x - 5, y - 4, z - 5), (x + 5, y + 5, z + 5))
    spots = [p for p in free_spots_here(limit=8) if chest_spot_ok(around, p)][:3] or make_room(ctx)
    for spot in spots:
        try:
            place("minecraft:chest", spot)
        except api.INTERRUPTIONS:
            raise              # an interruption is not a failure to shrug off here
        except McError:
            continue
        site = ctx.mem.add_site("cache", spot, ctx.dimension)
        log(f"placed cache chest {site['name']} at {spot}")
        return spot
    raise NotAvailable("no room for a cache chest")

def _has_something_to_store(c):
    """Pure inventory: no world read, so the pool can ask it before pricing the trip."""
    if not store_plan(Inventory().slots):
        raise NotAvailable("nothing worth storing")

@skill(gives=["state:stored"], remaining=lambda st, c: stored_left(st, c), needs={}, speed={}, pre=[_has_something_to_store], start=lambda c: Inventory().used_slots(), verify=lambda c: Inventory().used_slots() < c.base,
       budget=600, stall=90, provides={"room:deposit": lambda ctx, s: ()})
def deposit(ctx, local_only=False):
    """Store everything beyond the keep list in a site chest within 96 blocks, else in a cache chest placed here (a new site)."""

    before = Inventory().used_slots()
    moving = store_plan(Inventory().slots)
    if not moving:
        raise NotAvailable("nothing worth storing")
    here, c = feet(), None
    for site in ([] if local_only else sorted(ctx.mem.sites(ctx.dimension), key=lambda s: math.dist(s["pos"], here))):
        if math.dist(site["pos"], here) > 96:
            break
        if not site_trek_ok(ctx, site):
            continue
        if not nav.arrived(tuple(site["pos"]), ctx.policy, range_=4, attempts=1):
            ctx.ban(tuple(site["pos"]))      # a failed trek costs a minute of travel replans: not again soon
            # a cache chest unreachable for 3 treks is forgotten
            if site.get("kind") == "cache":
                misses = site.get("misses", 0) + 1
                if misses >= 3:
                    ctx.mem.data["sites"] = [s for s in ctx.mem.data["sites"] if s["name"] != site["name"]]
                    ctx.mem.save()
                    log(f"forgot unreachable cache {site['name']} at {site['pos']}")
                else:
                    ctx.mem.update_site(site["name"], misses=misses)
            continue
        chests = [c for c in find(["chest", "barrel"], radius=8, limit=6)
                  if openable_container((c["x"], c["y"], c["z"]))]
        if chests:
            c = (chests[0]["x"], chests[0]["y"], chests[0]["z"])
            break
        yield site["name"]
    if c is None:
        c = _place_cache_chest(ctx)
        moving = store_plan(Inventory().slots)
    r = api.run({"type": "use", "x": c[0], "y": c[1], "z": c[2]}, wait=60, awaits="the chest's slots read on its screen")
    if r["result"].get("screen") in (None, "none"):
        raise McError("could not open the home chest")
    try:
        # container view slot numbers differ from inventory indices: match by owner and index
        view = {s["index"]: s for s in world.container()["slots"] if s["owner"] == "player"}
        for s in moving:
            v = view.get(s["slot"])
            if v and v["id"] == s["id"]:
                api.post("/click", {"slot": v["slot"], "button": 0, "action": "QUICK_MOVE"})
        ctx.mem.note_container(c, ctx.dimension, world.container()["slots"])
    finally:
        api.post("/close")
    after = lost(lambda: Inventory().used_slots(), before)
    log(f"stored {before - after} stacks in the home chest")
    if after >= before:
        raise NotAvailable("home chest full or nothing moved")

@skill(gives=["state:withdrawn"], remaining=_k.more_than_at_start(lambda c: c.args[1], lambda c: c.args[2]), needs={}, speed={}, start=lambda c: Inventory().count(c.args[1]), verify=lambda c: Inventory().count(c.args[1]) > c.base,
       budget=180, stall=60,
       provides={"withdraw": lambda ctx, s: (s.token, s.count, tuple(s.detail["pos"]))})
def withdraw(ctx, item, count, pos):
    """Take `count` of `item` from the container at `pos` and note what is left in it."""

    nav.arrive(pos, ctx.policy, range_=3)
    r = api.run({"type": "use", "x": pos[0], "y": pos[1], "z": pos[2]}, wait=30, awaits="the chest's slots read on its screen")
    if r["status"] != "succeeded" or r["result"].get("screen") in (None, "none"):
        ctx.mem.forget_container(pos)
        raise NotAvailable(f"the container at {pos} did not open")
    left = int(count)
    try:
        for s in world.container()["slots"]:
            if left <= 0:
                break
            if s["owner"] != "player" and s["id"] == item:
                api.post("/click", {"slot": s["slot"], "button": 0, "action": "QUICK_MOVE"})
                left -= int(s.get("count", 1))
        ctx.mem.note_container(pos, ctx.dimension, world.container()["slots"])
    finally:
        api.post("/close")
    if left >= int(count):
        raise NotAvailable(f"no {bare(item)} left in the container at {pos}")
    log(f"took {int(count) - max(0, left)}× {bare(item)} from the container at {pos}")

def _site_missing(site):
    """How many blocks of a site's structure snapshot the world no longer shows (0 without a snapshot)."""
    snap = site.get("snapshot")
    if not snap:
        return 0
    region = Region(tuple(snap["lo"]), tuple(snap["hi"]))
    return sum(1 for key in snap["blocks"] if not region.solid(tuple(int(v) for v in key.split(","))))

def _site_named(ctx, step):
    site = next((x for x in ctx.mem.sites() if x.get("name") == step.detail.get("site", step.token)), None)
    return (site,) if site is not None else None

@skill(gives=["state:site_whole"], remaining=_k.structure(lambda c: {tuple(int(v) for v in k.split(",")): b for k, b in c.args[1]["snapshot"]["blocks"].items()}), needs={}, speed={}, verify=lambda c: _site_missing(c.args[1]) == 0, budget=600, stall=90, provides={"repair:site": _site_named})
def repair_site(ctx, site):
    """Rebuild missing blocks (and doors) of a site from its structure snapshot."""
    snap = site.get("snapshot")
    if not snap:
        ctx.mem.update_site(site["name"], dirty=False)
        return
    region = Region(tuple(snap["lo"]), tuple(snap["hi"]))
    inv = Inventory()
    stock = {m: inv.count(m) for m in GROUPS["building"] + GROUPS["planks"] + GROUPS["door"]}
    blocks, doors_done = [], set()
    for key, name in snap["blocks"].items():
        pos = tuple(int(v) for v in key.split(","))
        if region.solid(pos) or region.hazard(pos):
            continue
        if name.endswith("_door"):
            base = (pos[0], pos[1] - 1, pos[2]) if f"{pos[0]},{pos[1] - 1},{pos[2]}" in snap["blocks"] else pos
            if base in doors_done:
                continue
            doors_done.add(base)
            door = next((d for d in GROUPS["door"] if stock.get(d, 0) > 0), None)
            if door is None:
                continue  # leave the doorway; the planner adds a door need
            stock[door] -= 1
            blocks.append({"x": base[0], "y": base[1], "z": base[2], "item": door})
            continue
        item = mid(PLACEABLE_AS.get(name, name))
        if stock.get(item, inv.count(item)) <= 0:
            item = next((b for b, n in stock.items() if n > 0 and b in GROUPS["building"]), None)
        if item is None:
            break
        stock[item] = stock.get(item, inv.count(item)) - 1
        blocks.append({"x": pos[0], "y": pos[1], "z": pos[2], "item": item})
    if blocks:
        if not nav.arrived(tuple(site["pos"]), ctx.policy, range_=3, attempts=2):
            raise NotAvailable(f"{site['name']} not reachable")
        log(f"repairing {site['name']}: {len(blocks)} blocks")
        nav.run_cells("build", nav.build_batch(blocks, feet()), wait=600)     # the site is read back after
    remaining = _site_missing(site)
    ctx.mem.update_site(site["name"], dirty=remaining > 0)
    if remaining:
        raise McError(f"{site['name']} still missing {remaining} blocks")

def find_base(radius=48):
    hits = []
    for kind, names in BASE_MARKERS.items():
        for b in find(names, radius=radius, limit=40):
            hits.append((kind, (b["x"], b["y"], b["z"])))
    if not hits:
        return None
    best = max(hits, key=lambda h: sum(MARKER_WEIGHT[k] for k, p in hits if math.dist(p, h[1]) <= 12))
    return best[1]

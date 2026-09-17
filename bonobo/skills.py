"""Verified routines built on the mod's primitives. Every skill carries a contract (see skill.py): preconditions,
goal check, verification, time budget and a stall limit on its own goal metric. Skills never plan: inputs must be
present. `python3 mc.py skills` lists the contracts."""
import math
import re
import time

from . import api, blueprints, nav, world
from .api import McError, NotAvailable, log
from .skill import skill, world_signature
from .data import (ARMOR_RANK, ARMOR_SLOTS, BASE_MARKERS, GROUPS, JUNK, KEEP_BUILDING_BLOCKS, LOG_TO_PLANKS,
                   MARKER_WEIGHT, PLACEABLE_AS, RECIPES, bare, mid)
from .knowledge import GROUP_RECIPES, members
from .bag import pickup_whitelist


from .world import Inventory, Region, add, connected, dark_spots, entities, find, region_around
from .bag import KEEP_ALWAYS_SUFFIX, KEEP_ITEMS, KEEP_GROUPS, tidy_plan, LOW_VALUE_CAPS, STACK_VALUE, _stack_value, PROTECTED_IDS, PROTECTED_SUFFIX, _protected_stack, RAW_MEAT, SURPLUS_CAP, free_slots_plan, FREE_SLOTS_TARGET, throw_direction, store_plan  # noqa: F401  (moved; re-exported for skills.X callers)
from .terrain import LAND, pick_land, underground_target, shelter_method_at, find_shelter_spot, choose_burrow, NEIGHBOURS6_LOCAL, choose_exit, air_route, is_enclosed, find_open_spot, chest_spot_ok  # noqa: F401  (moved; re-exported for skills.X callers)
from .skillcore import _collect_only, ToolMissing, Context, feet, close_screen, free_spots, free_spot, place, snapshot, mine_cell  # noqa: F401,E402  (split out; re-exported for skills.X callers)
from .explore import surface_first, explore_for, seek_blocks, approach_policy  # noqa: F401,E402  (split out; re-exported for skills.X callers)
from .wood import chop  # noqa: F401,E402  (split out; re-exported for skills.X callers)
from .building import _mod_at_least, _open_container, _empty_container_slot, _machine_roles, _go_to_machine, find_machine_spot, resolve_item, block_matches, place_oriented, materials_missing, _build_parts, build_blueprint, build_shelter  # noqa: F401,E402  (split out; re-exported for skills.X callers)


# ---------------------------------------------------------------- placing things near us


def make_room(ctx):
    """Boxed in a 1-wide shaft with nowhere to put a station: dig out the side cell at head height next to us (its
    floor stays), so there is a supported free spot. Never next to hazards or protected blocks."""
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
        if region.solid(cell):
            api.run(nav.mine_task(cell), wait=60)
        # Clear the cell above too: a chest under a solid block can't be opened, and a table there is cramped.
        if region.solid(above):
            api.run(nav.mine_task(above), wait=60)
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


@skill(budget=120, stall=45, per_unit=20,
       verify=lambda c: c.result is not None and math.dist(feet(), c.result) <= 2)
def move_to_open_space(ctx):
    """Full bag in a shaft or tunnel: walk to the nearest spot with room to throw and to put down a chest."""
    x, y, z = feet()
    spot = find_open_spot(Region((x - 12, y - 6, z - 12), (x + 12, y + 8, z + 12)), (x, y, z))
    if spot is None:
        raise NotAvailable("no open space within 12 blocks")
    if spot == (x, y, z):
        return spot
    log(f"   moving to open space at {spot} to sort the inventory")
    if not nav.go_to(spot, ctx.policy, range_=0.8, attempts=2):
        raise api.NavFailed(f"open space at {spot} not reachable")
    yield feet()
    return spot


# ---------------------------------------------------------------- stations

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
            spots = free_spots(limit=3) or make_room(self.ctx)
            for spot in spots:
                try:
                    place(self.block, spot)
                    self.pos = spot
                    break
                except McError as e:
                    last = str(e)
            if self.pos is None:
                raise NotAvailable(f"no room to place {bare(self.block)} ({last})")
            self.placed = True
            self.ctx.mem.add_station(self.block, self.pos, self.ctx.dimension)
            api.run({"type": "wait", "ticks": 5})
        else:
            raise McError(f"no {bare(self.block)} nearby or carried")
        for _ in range(3):
            r = api.run({"type": "use", "x": self.pos[0], "y": self.pos[1], "z": self.pos[2]}, wait=60)
            if r["status"] == "succeeded" and r["result"].get("screen") not in (None, "none"):
                return self
            if "cannot reach" in r["message"]:
                break
        self.__exit__(None, None, None)
        raise McError(f"could not open {bare(self.block)}")

    def reopen(self):
        api.run({"type": "use", "x": self.pos[0], "y": self.pos[1], "z": self.pos[2]}, wait=60)

    def __exit__(self, *exc):
        api.post("/close")
        if self.placed:
            before = Inventory().count(self.block)
            mine_cell(self.ctx.policy, self.pos, wanted=[self.block], require_drops=True, wait=60)
            if Inventory().count(self.block) <= before:
                api.run({"type": "collect", "radius": 6}, wait=30)   # the drop can land out of the sweep
            if Inventory().count(self.block) > before:
                self.ctx.mem.remove_station(self.pos)
            else:
                log(f"   !! lost the carried {bare(self.block)} while picking it up")
        return False


# ---------------------------------------------------------------- crafting and smelting

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


def _craft_output(c):
    token, times = c.args[1], c.args[2]
    pattern, _ = GROUP_RECIPES[token] if token in GROUP_RECIPES else RECIPES[mid(token)]
    inv = Inventory()
    item = output_of(token, resolve_pattern(pattern, times, inv))  # raises when an input is missing
    return item, inv.count(item)


@skill(start=_craft_output, verify=lambda c: Inventory().count(c.base[0]) > c.base[1], budget=90, stall=60,
       per_unit=4, key=lambda c: "craft")
def craft(ctx, token, times):
    """Craft `times` batches of a recipe (2×2 in the inventory, 3×3 at a found or carried crafting table)."""
    pattern, out = GROUP_RECIPES[token] if token in GROUP_RECIPES else RECIPES[mid(token)]
    inv = Inventory()
    if inv.used_slots() >= 36:
        # The result needs a slot: a full bag makes every craft fail ("missing ingredient"). Drop the least
        # valuable stack first.
        close_screen()
        for s in free_slots_plan(inv.slots, need=1)[:1]:
            api.post("/click", {"slot": 36 + s["slot"] if s["slot"] < 9 else s["slot"], "button": 1, "action": "THROW"})
            log(f"   dropped {bare(s['id'])} to make room for crafting")
        inv = Inventory()
    concrete = resolve_pattern(pattern, times, inv)
    item = output_of(token, concrete)
    before = inv.count(item)
    if len(pattern) == 9:
        with Station(ctx, "minecraft:crafting_table"):
            r = api.run({"type": "craft", "pattern": concrete, "count": out * times}, wait=120)
    else:
        close_screen()
        r = api.run({"type": "craft", "pattern": concrete, "count": out * times}, wait=120)
    if Inventory().count(item) <= before:
        raise McError(f"crafting {bare(item)} produced nothing: {r['message']}")


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


@skill(start=lambda c: Inventory().count(c.args[1]), verify=lambda c: Inventory().count(c.args[1]) > c.base,
       budget=900, stall=30, per_unit=10.5, units=lambda c: min(64, c.args[3]), key=lambda c: "smelt")
def smelt(ctx, output, input_token, count, fuel):
    """One furnace session: load input + fuel, watch the output slot fill (10 s/item), take everything out."""
    count = min(64, count)
    inv = Inventory()
    inputs = [m for m in members(input_token) if inv.count(m)]
    fuels = [m for m in members(fuel) if inv.count(m)]
    fuel_n = math.ceil(count / 8) if fuel == "coal" else math.ceil(count / 1.5)
    with Station(ctx, "minecraft:furnace"):
        try:
            move_into(inputs, 0, count)
            move_into(fuels, 1, fuel_n)
            loaded = _furnace_slots().get(0, 0)
            while True:
                slots = _furnace_slots()
                made = slots.get(2, 0)
                # Done when every loaded item came out. An empty input slot is not enough: the last item is still
                # cooking then. Out of fuel → the output stops growing → the stall limit cancels.
                if made >= loaded:
                    break
                yield made
                time.sleep(3)
        finally:
            api.post("/click", {"slot": 2, "button": 0, "action": "QUICK_MOVE"})


# Every smelt runs in the background: standing at a furnace for 3 ingots cost 30 s of pure waiting (the iron bench's
# main time sink); the brain mines, crafts or walks meanwhile and a job candidate collects it when ready.
ASYNC_SMELT_MIN = 1


@skill(start=lambda c: Inventory().count(c.args[2]), verify=lambda c: Inventory().count(c.args[2]) < c.base,
       budget=120, stall=40, per_unit=12)
def start_smelt_job(ctx, output, input_token, count, fuel):
    """Multitasking: load a furnace (found nearby or placed from the inventory) with input + fuel and walk away.
    The job is remembered with its expected finish time (10 s/item); its output counts as pending for the planner."""
    count = min(64, count)
    inv = Inventory()
    inputs = [m for m in members(input_token) if inv.count(m)]
    fuels = [m for m in members(fuel) if inv.count(m)]
    fuel_n = math.ceil(count / 8) if fuel == "coal" else math.ceil(count / 1.5)
    station = Station(ctx, "minecraft:furnace")
    station.__enter__()
    carried = station.placed
    try:
        move_into(inputs, 0, count)
        move_into(fuels, 1, fuel_n)
        yield count
    finally:
        api.post("/close")    # leave the furnace standing: that's the point
    ctx.mem.add_job("furnace", station.pos, ctx.dimension, output, count, time.time() + 10 * count + 5, carried)
    log(f"smelting {count}× {bare(output)} in the background at {station.pos} (ready in ~{10 * count + 5}s)")


def job_ready(job):
    return job["ready_at"] <= time.time()


@skill(start=lambda c: Inventory().count(c.args[1]["item"]),
       verify=lambda c: Inventory().count(c.args[1]["item"]) > c.base, budget=240, stall=60, per_unit=20)
def collect_job(ctx, job):
    """Go back to a background furnace job, take the output (and leftovers), pick the furnace up if it was ours."""
    pos = tuple(job["pos"])
    if Region(pos, pos).name(pos) not in ("furnace", "blast_furnace", "smoker"):
        # Picked back up, broken or never placed there: the job is stale, not a navigation problem.
        ctx.mem.finish_job(job["id"])
        raise NotAvailable(f"no furnace at {pos} any more; job dropped")
    if not nav.go_to(pos, ctx.policy, range_=3, attempts=2):
        raise api.NavFailed(f"furnace job at {pos} not reachable")
    before = Inventory().count(job["item"])
    r = api.run({"type": "use", "x": pos[0], "y": pos[1], "z": pos[2]}, wait=40)
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
    got = Inventory().count(job["item"]) - before
    if still_cooking:
        ctx.mem.postpone_job(job["id"], 10 * still_cooking + 5)
        job_left = job["count"] - got
        for j in ctx.mem.data["jobs"]:
            if j["id"] == job["id"]:
                j["count"] = max(0, job_left)
        ctx.mem.save()
        log(f"took {got}× {bare(job['item'])}; {still_cooking} still cooking")
        return
    if job.get("carried"):
        mine_cell(ctx.policy, pos, wanted=["minecraft:furnace", job["item"]], require_drops=True, wait=40)
    ctx.mem.finish_job(job["id"])
    log(f"collected {got}× {bare(job['item'])} from the background furnace")


# ---------------------------------------------------------------- tools and armor

def require_pickaxe(tier, min_left=3):
    if tier is None:
        return
    if not any(t >= tier and d >= min_left for t, d, _ in Inventory().tools("pickaxe")):
        raise ToolMissing("pickaxe", tier)


def shield_wanted_in_offhand():
    inv = Inventory()
    return inv.offhand() != "minecraft:shield" and inv.usable("minecraft:shield") > 0


def shield_to_offhand():
    """Swap the carried shield into the offhand (vanilla F-swap in the inventory screen); whatever was there
    (a torch someone parked) goes back to the shield's slot, where tasks can use it."""
    close_screen()
    inv = Inventory()
    s = next((s for s in inv.slots if s["id"] == "minecraft:shield"), None)
    if s is None:
        return False
    api.post("/click", {"slot": 36 + s["slot"] if s["slot"] < 9 else s["slot"], "button": 40, "action": "SWAP"})
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
            api.post("/click", {"slot": 36 + s["slot"] if s["slot"] < 9 else s["slot"], "button": 0,
                                "action": "QUICK_MOVE"})
            log(f"equipped {bare(s['id'])}")
            changed = True
    return changed


# ---------------------------------------------------------------- gathering


def too_wet(region, p, margin=1):
    """Pure: water or lava within `margin` blocks (box) of p — opening p would let the fluid in."""
    for dx in range(-margin, margin + 1):
        for dy in range(-1, margin + 1):
            for dz in range(-margin, margin + 1):
                if (dx, dy, dz) != (0, 0, 0) and region.hazard((p[0] + dx, p[1] + dy, p[2] + dz)):
                    return True
    return False


REACH_BUDGET = 3         # ways of not getting there, per call, before the place itself is the problem


def _reach_budget(spent, blocks, why=None):
    """Raise once the budget is gone. NavFailed is deliberate: retry.py keys on the position bin, so the goal waits
    for the agent to be somewhere else instead of paying for the same search from the same spot."""
    if spent >= REACH_BUDGET:
        raise api.NavFailed(why or f"{blocks[0]}: {spent} unreachable in a row — not from this spot")


@skill(pre=[lambda c: require_pickaxe(c.args[4])], start=lambda c: Inventory().count(c.args[1]),
       done=lambda c: Inventory().count(c.args[1]) >= c.base + c.args[2], budget=900, stall=90,
       per_unit=8, units=lambda c: c.args[2], key=lambda c: f"mine:{c.args[1]}")
def mine(ctx, token, count, blocks, tier, breaks=None):
    """Tunnel to the nearest reachable vein of `blocks` and mine it until `count` more `token` are held."""
    drop = token
    target = Inventory().count(drop) + count
    radius = 24
    # Unreachable is a property of where we STAND, not of the block: on a hillside the mod answered "cannot reach"
    # for block after block of the same seam, each answer costing a 6000-node search (5.8 s), and banning them one
    # at a time meant the next round picked the neighbour and paid again. One budget for every way of not getting
    # there — travel refusing, the mod refusing, nothing within reach — and when it runs out the whole step fails
    # as a nav failure, which is the one thing that makes the retry policy wait for a CHANGE OF PLACE.
    unreachable = 0
    empty_batches = 0    # batches the mod could not break at all; a few in a row means the seam really is dead
    for _ in range(10):
        have = Inventory().count(drop)
        if have >= target:
            return
        yield None
        require_pickaxe(tier)
        # Exposed ore first (an open face a stand spot can see): buried coal and stone ended "skipped after repeated
        # unreachable blocks" again and again; hidden veins only when no exposed one is in range.
        raw = find(blocks, radius=radius, limit=60, exposed=True) or find(blocks, radius=radius, limit=60)
        hits = [h for h in raw
                if not ctx.blocked((h["x"], h["y"], h["z"])) and (h["x"], h["y"], h["z"]) not in ctx.policy.protected]
        if not hits and raw:
            # Found but filtered out: say by what (bench iron_ingots failed in 0 s with 6 ore in plain sight).
            banned = sum(1 for h in raw if ctx.blocked((h["x"], h["y"], h["z"])))
            api.detail(f"   {len(raw)} {blocks[0]} in range but {banned} banned, {len(raw) - banned} protected")
        if not hits:
            if radius >= 48 and drop == "minecraft:obsidian":
                # Obsidian rarely exists: make it from a lava pool (water bucket), then mine what was cast.
                from . import fluids
                fluids.cast_obsidian(ctx)
                continue
            if radius >= 48:
                raise NotAvailable(f"no {blocks[0]} within 48 blocks")
            radius = 48
            continue
        start = feet()
        if swimming(api.get("/state")):
            raise NotAvailable("in the water: no digging until back on land")
        if blocks[0] in ("stone", "deepslate", "dirt", "grass_block", "sand", "gravel"):
            # Common blocks are everywhere: only take ones with an open face (buried stone 3 blocks down cost 80 s of
            # 60 000-node searches per try).
            near = [h for h in hits[:40] if h["distance"] <= 16]
            probe = region_around([start] + [(h["x"], h["y"], h["z"]) for h in near], pad=1) if near else None
            if probe is not None:
                exposed = [h for h in near if any(not probe.solid(add((h["x"], h["y"], h["z"]), d))
                                                  for d in nav.NEIGHBOURS6)]
                hits = exposed or hits
        seed = (hits[0]["x"], hits[0]["y"], hits[0]["z"])
        region = region_around([start, seed], pad=3)
        if region is None:
            if not nav.go_to(seed, ctx.policy, range_=12):
                ctx.ban(seed)
                raise api.NavFailed(f"{blocks[0]} at {seed} not reachable")
            continue
        vein = {p for p in connected(region, seed, blocks) if not ctx.blocked(p)}
        if not vein:
            continue      # the whole connected vein is already proven unreachable: next seed
        # Never open a block that touches lava or water (it floods the tunnel) unless the goal wants the fluid.
        # Surface blocks (dirt, sand, gravel, stone) keep 2 blocks from any fluid: a dirt pit dug beside a pond filled
        # with water and the agent kept digging inside it, nearly drowning.
        if not ctx.policy.lava_ok:
            margin = 2 if blocks[0] in ("stone", "deepslate", "dirt", "grass_block", "sand", "gravel") else 1
            wet = {p for p in vein if too_wet(region, p, margin)}
            vein -= wet
            for p in wet:
                ctx.ban(p)
            if not vein:
                continue   # banned the wet cells: the next vein is a different target, not a retry
        want = breaks or max(1, target - have)
        vein = set(sorted(vein, key=lambda p: math.dist(p, start))[: max(want, len(vein) if tier else want)])
        if "travel" in nav.mod_features():
            # One movement mechanism: the mod's travel reaches the vein (digging/bridging as needed).
            near = min(vein, key=lambda p: math.dist(p, start))
            if not nav.go_to(near, ctx.policy, range_=3.5, attempts=1):
                for p in vein:
                    ctx.ban(p)
                # The next vein is a different target, not a retry — the same rule the wet-cell branch above uses.
                # Failing the whole step over one unreachable vein cooled the goal for two minutes, and on a hilltop
                # landing that happened to every vein in turn ("nether kit/mine:stone: … not reachable", ×3).
                unreachable += 1
                _reach_budget(unreachable, blocks)
                continue
        else:
            digs = nav.plan_tunnel(region, start, vein, ctx.policy)
            if digs is None:
                for p in vein:
                    ctx.ban(p)
                raise api.NavFailed(f"no tunnel to the {blocks[0]} vein at {seed}")
            if digs:
                nav.dig_route(digs, ctx.policy)
        # Only blocks within reach of where travel actually left us, a dozen at a time: a 67-block gravel batch
        # walked toward a block 6 below through rock and froze the agent.
        here_now = feet()
        in_reach = sorted((p for p in vein if math.dist(p, here_now) <= 4.5), key=lambda p: math.dist(p, here_now))
        if not in_reach:
            near_cell = min(vein, key=lambda p: math.dist(p, here_now))
            if not nav.go_to(near_cell, ctx.policy, range_=2.0, attempts=1):
                for p in vein:
                    ctx.ban(p)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"{blocks[0]} at {near_cell} not reachable")
                continue
            here_now = feet()
            in_reach = sorted((p for p in vein if math.dist(p, here_now) <= 4.5), key=lambda p: math.dist(p, here_now))
            if not in_reach:
                for p in vein:
                    ctx.ban(p)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"got near {near_cell} but no {blocks[0]} within reach")
                continue
        # Distance is not reachability: on a hillside the mod answered "cannot reach … no path found" for 6 of 7
        # blocks that were all within 4.5. Only hand over blocks with an open face, judged from the region already
        # read (no extra world query here).
        if region is not None:
            open_faced = [p for p in in_reach
                          if any(not region.solid(add(p, d)) for d in nav.NEIGHBOURS6)]
            in_reach = open_faced or in_reach
        vein = set(in_reach[:12])
        before = Inventory().count(drop)
        r = api.run({"type": "mine_many", "collect": True, "requireDrops": tier is not None, **_collect_only([drop]),
                     "blocks": [{"x": p[0], "y": p[1], "z": p[2]} for p in vein]}, wait=900)
        if Inventory().count(drop) <= before:
            from .knowledge import MINE_YIELD
            if MINE_YIELD.get(mid(drop), 1) < 1 and "failed" not in r["message"]:
                continue   # a chance drop (grass → seeds ~1 in 8): an empty break is expected, keep breaking
            # Partial success is progress, not failure. The mod reports "N of M steps failed"; with N < M some
            # blocks did break (the pickup just hasn't landed yet), so keep the batch and try again — banning all
            # twelve over one unreachable block is what left the kit stuck at blocks 30/32 with the goal cooling.
            import re as _re
            part = _re.search(r"(\d+) of (\d+) steps failed", r.get("message") or "")
            if part and int(part.group(1)) < int(part.group(2)):
                bad = {(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                       for m in _re.finditer(r"cannot reach (-?\d+), (-?\d+), (-?\d+)", r.get("message") or "")}
                for p in bad or ():
                    ctx.ban(p)      # only the blocks the mod named as unreachable
                if bad:
                    # The mod's refusal costs a full path search each time, so it counts against the same budget as
                    # a refusal by travel. Without this the batch shrank by one block per 6 s for a whole minute.
                    unreachable += 1
                    _reach_budget(unreachable, blocks)
                continue
            for p in vein:
                ctx.ban(p)
            # Ban this batch, then try the next one: one unmineable batch is not proof that the whole seam is dead,
            # and failing the step here cooled the goal for two minutes on every hillside landing.
            empty_batches += 1
            if empty_batches >= 3:
                raise NotAvailable(f"{blocks[0]} vein yielded nothing: {r['message']}")
            continue
        elif tier:
            ctx.mem.log_vein(blocks[0], seed, len(vein), ctx.dimension)
    raise McError(f"could not mine enough {bare(drop)}")


STRIP_ORES = [("minecraft:diamond", ["diamond_ore", "deepslate_diamond_ore"], 2),
              ("minecraft:raw_iron", ["iron_ore", "deepslate_iron_ore"], 1),
              ("minecraft:raw_gold", ["gold_ore", "deepslate_gold_ore"], 2),
              ("minecraft:redstone", ["redstone_ore", "deepslate_redstone_ore"], 2),
              ("minecraft:lapis_lazuli", ["lapis_ore", "deepslate_lapis_ore"], 1),
              ("coal", ["coal_ore", "deepslate_coal_ore"], 0)]


def _not_in_water(c):
    if api.get("/state")["inWater"]:
        raise NotAvailable("standing in water: no strip mining here")


@skill(pre=[lambda c: require_pickaxe(0), _not_in_water], needs={"tool:pickaxe:0": 1},
       start=lambda c: world_signature(),
       verify=lambda c: world_signature() != c.base, budget=300, stall=60)
def strip_mine_step(ctx, length=16):
    """Always-available work: descend toward iron depth (diamond depth once an iron pickaxe exists), then drive a
    2-high tunnel and take any ore it reveals. Light comes from reflexes between segments."""
    s = api.get("/state")
    fx, fy, fz = s["blockX"], s["blockY"], s["blockZ"]
    tools = Inventory().tools("pickaxe")
    best_tier = max((t for t, d, _ in tools if d >= 3), default=0)
    # Diamonds peak around -58, but bedrock starts at -60..-64: tunnel at -54, clear of it, still in the band.
    depth = -54 if any(t >= 2 and d >= 20 for t, d, _ in tools) else 16
    if fy < depth - 3:
        # Drifted below the band (falls, ore pockets): staircase back up instead of tunnelling into bedrock.
        if not nav.go_to((fx, depth, fz), ctx.policy, range_=2, attempts=1):
            raise api.NavFailed(f"can't climb back up to mining depth {depth}")
        return "climbed"
    if fy > depth + 4:
        try:
            nav.dig_down(min(12, fy - depth), ctx.policy, use_ladders=Inventory().count("minecraft:ladder") >= 12)
        except NotAvailable:
            # Unsafe here (water/lava/cave below): find dry, solid ground a bit away instead of spinning in place.
            stone = [h for h in find(["stone", "deepslate", "dirt", "grass_block"], radius=24, limit=40)
                     if h["y"] <= fy and math.dist((h["x"], h["z"]), (fx, fz)) >= 6]
            if not stone:
                raise NotAvailable("no safe ground to dig down nearby")
            t = stone[0]
            if not nav.go_to((t["x"], t["y"] + 1, t["z"]), ctx.policy, range_=2, attempts=1):
                raise NotAvailable("can't reach safe ground to dig down")
        return
    dx, dz = [(0, 1), (-1, 0), (0, -1), (1, 0)][int(((s["yaw"] % 360) + 45) // 90) % 4]
    region = Region((fx + min(0, dx * length) - 1, fy - 1, fz + min(0, dz * length) - 1),
                    (fx + max(0, dx * length) + 1, fy + 2, fz + max(0, dz * length) + 1))
    tasks, end = [], 0
    for i in range(1, length + 1):
        cells = [(fx + dx * i, fy + 1, fz + dz * i), (fx + dx * i, fy, fz + dz * i)]
        floor = (fx + dx * i, fy - 1, fz + dz * i)
        if (not region.solid(floor) or any(region.unbreakable(c) for c in cells)
                or any(region.hazard(c) or region.hazard(add(c, d))
                                           for c in cells for d in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, -1)])
                or any(c in ctx.policy.protected for c in cells)):
            break
        tasks += [nav.mine_task(c) for c in cells if region.solid(c)]
        end = i
    if end == 0:
        # Turning in place is not progress: say so, and let the retry policy decide (the next try faces a new way).
        api.run({"type": "look", "yaw": (s["yaw"] + 90) % 360, "pitch": 0})
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
    # Closing in (4-block bins) or collecting drops is progress; circling at the same distance is not.
    return Inventory().count(token), (int(near[0]["distance"] // 4) if near else None)


@skill(start=lambda c: Inventory().count(c.args[1]), done=lambda c: Inventory().count(c.args[1]) >= c.base + c.args[2],
       budget=480, stall=60, per_unit=30, units=lambda c: c.args[2], key=lambda c: f"hunt:{c.args[1]}")
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
        # Reach it with the navigator first (walks, tunnels, climbs out of caves); a blind attack chase from
        # underground toward an animal on the surface never lands a hit.
        if e["distance"] > 4 and api.get("/state").get("skyLight", 15) <= 4 and e["y"] > feet()[1] + 3:
            # In a cave below the animal: climb out with the normal policy (digging allowed) first — the no-dig
            # approach below can't leave a cave at y=-20.
            surface_first(ctx)
            continue
        if e["distance"] > 4:
            # Walk (and bridge) to animals, never tunnel: a sheep behind a mountain isn't worth a pickaxe.
            nav.go_to((math.floor(e["x"]), math.floor(e["y"]), math.floor(e["z"])), approach_policy(ctx.policy),
                      range_=3, attempts=2)
            e = next((n for n in entities(64, types) if n["id"] == e["id"]), None)
            if e is None or e["distance"] > 6:
                ctx.ban((prey[0]["id"], 0, 0), 300)
                raise api.NavFailed(f"could not get to the {bare(types[0])}")
        try:
            api.run({"type": "attack", "entity": e["id"]}, wait=30)
            api.run({"type": "collect", "radius": 6, **_collect_only([token])}, wait=60)
        except api.TaskStuck:
            ctx.ban((prey[0]["id"], 0, 0), 600)  # unreachable (across water, on a ledge)
            raise api.NavFailed(f"the {bare(types[0])} is out of reach for attacks")
        if Inventory().count(token) <= before:
            ctx.ban((prey[0]["id"], 0, 0), 120)
            raise NotAvailable(f"killing the {bare(types[0])} dropped no {bare(token)}")
    raise McError(f"could not hunt enough {bare(token)}")


# ---------------------------------------------------------------- light, food, night

@skill(budget=120, stall=30)
def contain_lava(ctx, radius=4):
    """Cover lava exposed within reach (a dig broke into a pool or a flow reached us) with building blocks, nearest
    first — what players do before continuing a tunnel. Skipped when the goal wants lava. Returns cells covered."""
    if ctx.policy.lava_ok:
        return 0
    x, y, z = feet()
    if not find(["lava"], radius=radius, limit=1):
        return 0
    region = Region((x - radius - 1, y - radius - 1, z - radius - 1), (x + radius + 1, y + radius + 1, z + radius + 1))
    open_lava = sorted((p for p in region.blocks if region.name(p) == "lava"
                        and math.dist(p, (x, y + 1, z)) <= 4.5
                        and any(region.name(add(p, d)) in ("air", "cave_air") for d in nav.NEIGHBOURS6)),
                       key=lambda p: math.dist(p, (x, y, z)))
    inv = Inventory()
    blocks = [b for b in GROUPS["building"] if inv.count(b)]
    if open_lava and not blocks:
        raise NotAvailable("lava exposed and no blocks to cover it")
    covered = 0
    for p in open_lava:
        item = next((b for b in blocks if Inventory().count(b)), None)
        if item is None:
            break
        try:
            place(item, p)
            covered += 1
        except McError as e:
            log(f"   could not cover lava at {p}: {e}")
        yield covered
    if covered:
        log(f"covered {covered} exposed lava cells")
    return covered


@skill(budget=40, stall=30)
def place_torch_if_dark(ctx):
    """Mobs spawn at block light 0. Light the darkest reachable floor spot nearby when we stand in darkness."""
    s = api.get("/state")
    if "blockLight" not in s or s["blockLight"] > 0 or (s["skyLight"] > 7 and 0 < s["timeOfDay"] < 12500):
        return False
    if Inventory().usable("minecraft:torch") == 0:   # a torch in the offhand can't be placed by tasks
        return False
    here = (s["blockX"], s["blockY"], s["blockZ"])
    spots = [p for p in dark_spots(radius=4, max_light=0)
             if math.dist((p["x"], p["y"], p["z"]), here) <= 3.5
             and not (p["y"] in (here[1], here[1] + 1) and abs(p["x"] + 0.5 - s["x"]) < 0.8
                      and abs(p["z"] + 0.5 - s["z"]) < 0.8)]
    if not spots:
        return False
    p = spots[0]
    try:
        r = api.run({"type": "place", "item": "minecraft:torch", "x": p["x"], "y": p["y"], "z": p["z"]}, wait=30)
    except api.TaskStuck:
        return False
    return r["status"] == "succeeded"


RAW_MEAT = ["minecraft:beef", "minecraft:porkchop", "minecraft:mutton", "minecraft:rabbit", "minecraft:chicken"]


def edible_carried(inv):
    """Anything edible at all, cooked or raw. `food_count` counts MEALS (cooked only); this counts food."""
    from .knowledge import ALL_FOOD
    return any(inv.count(f) for f in ALL_FOOD + RAW_MEAT)


@skill(start=lambda c: api.get("/state")["food"],
       verify=lambda c: not c.result or api.get("/state")["food"] > c.base, budget=30, stall=30)
def eat(raw_ok=False):
    """Eat the best food carried (raw meat too when starving). Returns False when there is none."""
    from .knowledge import ALL_FOOD
    inv = Inventory()
    raw = ["minecraft:beef", "minecraft:porkchop", "minecraft:mutton", "minecraft:rabbit", "minecraft:chicken"]
    food = next((f for f in ALL_FOOD + (raw if raw_ok else []) if inv.count(f)), None)
    if food:
        api.run({"type": "eat", "item": food}, wait=30)
        return True
    return False


def swimming(state):
    """The one "in the water" test, shared by the brain's trigger and reach_land's done: in water and not standing
    on ground (a shore block's water overlapping the body box reads inWater while standing)."""
    return bool(state.get("inWater")) and not state.get("onGround", False)


def _on_land():
    return not swimming(api.get("/state"))


@skill(done=lambda c: _on_land(), budget=180, stall=45, per_unit=30)
def reach_land(ctx):
    """Night in the water: nothing can be dug or built there, so swim (or boat) to the nearest dry standing spot
    first; shelters are made from land. One attempt per call; the brain's retry policy decides the next."""
    x, y, z = feet()
    land = pick_land(Region((x - 24, y - 6, z - 24), (x + 24, y + 10, z + 24)), (x, y, z))
    if land is None:
        raise NotAvailable("no land within 24 blocks")
    r = api.run({"type": "goto", "x": land[0], "y": land[1], "z": land[2], "range": 1.5, "partial": True,
                 "useBoat": True}, wait=120)
    if r["status"] != "succeeded" and not _on_land():
        # The walker can't climb out (a 1-wide water shaft, a high bank): dig / pillar out instead.
        if not nav.go_to(land, ctx.policy, range_=2, attempts=1):
            raise api.NavFailed(f"land at {land} not reachable")
    yield feet()


@skill(done=lambda c: enclosed(), budget=90, stall=40, per_unit=20)
def burrow(ctx):
    """Night shelter in a hillside: tunnel 2 blocks into solid ground, step to the end, seal the entrance behind
    (feet block on the floor, head block on top of it — both faces are visible from inside)."""
    x, y, z = feet()
    region = Region((x - 4, y - 2, z - 4), (x + 4, y + 3, z + 4))
    d = choose_burrow(region, (x, y, z), ctx.policy.protected)
    if d is None:
        raise NotAvailable("no solid hillside to burrow into here")
    dx, dz = d
    for k in (1, 2):
        for dy in (1, 0):
            c = (x + dx * k, y + dy, z + dz * k)
            r = api.run(nav.mine_task(c), wait=40)
            if r["status"] != "succeeded":
                raise McError(f"burrow: could not dig {c}: {r['message']}")
            yield c
    end = (x + dx * 2, y, z + dz * 2)
    api.run({"type": "goto", "x": end[0], "y": end[1], "z": end[2], "range": 0.4, "partial": False}, wait=20)
    block = next((b for b in GROUPS["building"] if Inventory().usable(b)), None)
    if block is None:
        raise NotAvailable("no blocks to seal the burrow")
    for dy in (0, 1):
        c = (x + dx, y + dy, z + dz)
        place(block, c)
        yield c
    log(f"burrowed into the hillside at {end}")


@skill(done=lambda c: not enclosed(), budget=90, stall=45, per_unit=15)
def dig_out(ctx):
    """Morning in a sealed pod: open one side (by hand if no pickaxe — slower, same result) and step out."""
    x, y, z = feet()
    region = Region((x - 3, y - 2, z - 3), (x + 3, y + 3, z + 3))
    exit_ = choose_exit(region, (x, y, z), ctx.policy.protected)
    if exit_ is None:
        raise NotAvailable("no safe side to dig out of")
    cells, out = exit_
    for c in cells:
        api.run(nav.mine_task(c), wait=40)
        yield c
    api.run({"type": "goto", "x": out[0], "y": out[1], "z": out[2], "range": 0.6, "partial": True}, wait=20)
    log(f"dug out of the shelter toward {out}")


def head_underwater(s=None):
    """The eyes are in a water block (swimming at the surface with the head out doesn't count)."""
    s = s or api.get("/state")
    if not s["inWater"]:
        return False
    eye = (s["blockX"], math.floor(s["y"] + 1.62), s["blockZ"])
    return Region(eye, eye).name(eye) == "water"


def head_buried(s=None):
    """The eyes are inside a solid block (falling sand/gravel, a placed block): suffocating."""
    s = s or api.get("/state")
    eye = (s["blockX"], math.floor(s["y"] + 1.62), s["blockZ"])
    r = Region(eye, eye)
    return r.solid(eye) and not r.name(eye).endswith(("_slab", "_stairs", "snow", "_carpet"))


@skill(done=lambda c: not head_buried(), budget=30, stall=15, per_unit=3)
def unbury(ctx):
    """Suffocating in a block: break the block at eye level, then the one above it if sand/gravel keeps falling."""
    for _ in range(4):
        s = api.get("/state")
        eye = (s["blockX"], math.floor(s["y"] + 1.62), s["blockZ"])
        api.run(nav.mine_task(eye), wait=15)
        yield eye


def _breathing():
    s = api.get("/state")
    return not head_underwater(s) or s["air"] >= 280


@skill(done=lambda c: _breathing(), budget=45, stall=12)
def find_air(ctx):
    """Out of breath underwater: swim to the nearest air (straight up first); water capped by blocks → dig the cap."""
    for _ in range(4):
        x, y, z = feet()
        region = Region((x - 8, y - 2, z - 8), (x + 8, y + 16, z + 8))
        route = air_route(region, (x, y + 1, z))
        if route is None:
            raise NotAvailable("no air within reach")
        kind, c = route
        if kind == "swim":
            api.run({"type": "goto", "x": c[0], "y": c[1] - 1, "z": c[2], "range": 1, "partial": True,
                     "useBoat": False}, wait=20)
        else:
            api.run(nav.mine_task(c), wait=15)
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


def _night_with_a_bed(c):
    """Sleeping needs night and a bed. Both are known without reading the world — the bed from the bag, the hour
    from the last snapshot — so the pool can refuse this before it prices walking to a bedroom."""
    if Inventory().count("bed") <= 0 and not api.get("/state").get("dead"):
        pass      # a bed may still be nearby; that half needs the world and stays in the body


@skill(verify=lambda c: api.get("/state")["timeOfDay"] < 12500, budget=240, stall=60)
def sleep(ctx, night_policy):
    """Sleep through the night: carried bed first (placed next to us, picked up after), then a nearby site bed."""
    inv = Inventory()
    bed = next((b for b in GROUPS["bed"] if inv.count(b)), None)
    if bed:
        spot = bed_spot()
        if spot is None:
            raise NotAvailable("no flat 2-block spot for the bed")
        place(bed, spot)
        try:
            for _ in range(3):
                api.run({"type": "use", "x": spot[0], "y": spot[1], "z": spot[2]}, wait=30)
                api.run({"type": "wait", "ticks": 20 * 7}, wait=20)
                if api.get("/state")["timeOfDay"] < 12500:
                    ctx.mem.slept()
                    log("slept (carried bed)")
                    return
            raise NotAvailable("could not fall asleep (monsters nearby?)")
        finally:
            mine_cell(ctx.policy, spot, wait=60)
    beds = find(BASE_MARKERS["bed"], radius=48, limit=1)
    if not beds:
        raise NotAvailable("no bed carried or nearby")
    b = (beds[0]["x"], beds[0]["y"], beds[0]["z"])
    if not nav.go_to(b, night_policy, range_=2.5, attempts=2):
        raise NotAvailable("bed not walkable tonight")
    for _ in range(3):
        api.run({"type": "use", "x": b[0], "y": b[1], "z": b[2]}, wait=30)
        api.run({"type": "wait", "ticks": 20 * 7}, wait=20)
        if api.get("/state")["timeOfDay"] < 12500:
            ctx.mem.slept()
            log("slept (site bed)")
            return
    raise NotAvailable("could not fall asleep in the site bed")


@skill(start=lambda c: feet(), verify=lambda c: feet()[1] < c.base[1], budget=60, stall=30)
def dig_in(ctx):
    """On the surface at night without a bed: dig up to 3 down under the feet and seal the opening overhead.

    Records how deep it got. The hole is in the world whether or not this call finished, so the planner can price
    finishing it (actions._resume) instead of starting a new one somewhere else — which is what it did when the
    only record of the work was the fact that this function had been called.
    """
    x, y, z = feet()
    nav.dig_down(3, ctx.policy, use_ladders=False)
    fx, fy, fz = feet()
    ctx.mem.note_progress("dig_in", (x, y, z), ctx.dimension, done=max(0, y - fy), of=3)
    block = next((b for b in GROUPS["building"] if Inventory().count(b)), None)
    if block and fy < y:
        place(block, (x, fy + 2, z))
    log("dug in for the night")


@skill(start=lambda c: Inventory().used_slots(), budget=180, stall=45, per_unit=8)
def take(ctx, token, count, blocks):
    """Break blocks that ARE the thing and pick them up: a village's bed, furnace, table, hay, crops.

    The same shape as `mine` — walk to the nearest one that is not ours and not blacklisted, break it, collect —
    and deliberately the same code path for protection: `mine_cell` refuses anything inside one of our own sites,
    so "take a furnace" can never mean "take OUR furnace out of the wall we built it into".
    """
    want = int(count)
    got = 0
    for _ in range(want * 2):
        if got >= want:
            return got
        hits = [h for h in (find(blocks, radius=48, limit=20) or ())
                if not ctx.blocked((h["x"], h["y"], h["z"]))
                and (h["x"], h["y"], h["z"]) not in ctx.policy.protected]
        if not hits:
            raise NotAvailable(f"no {bare(blocks[0])} within reach to take")
        cell = (hits[0]["x"], hits[0]["y"], hits[0]["z"])
        if not nav.go_to(cell, ctx.policy, range_=3, attempts=2):
            ctx.ban(cell)
            continue
        mine_cell(ctx.policy, cell, wanted=[token], require_drops=False, wait=60)
        api.run({"type": "collect", "radius": 4}, wait=20)
        yield None
        got += 1
        # The block is gone from the world whether or not the drop reached the bag: the map has to stop sending us
        # back to it, or the next round walks to the same empty square and calls that progress.
        ctx.mem.note_resource(bare(hits[0]["block"]), cell, ctx.dimension, depleted=True)
        log(f"took {bare(token)} at {cell} ({got}/{want})")
    return got


def enclosed():
    """True when the body has no way out: every side blocked at feet or head height, and covered overhead."""
    s = api.get("/state")
    x, y, z = s["blockX"], s["blockY"], s["blockZ"]
    return is_enclosed(Region((x - 1, y - 1, z - 1), (x + 1, y + 2, z + 1)), (x, y, z))


@skill(done=lambda c: enclosed(), budget=120, stall=40, per_unit=10)
def pod(ctx):
    """Night fallback where digging in is unsafe (water/caves below): wall in the body with blocks — four sides at
    feet and head height plus a roof. Mobs can't reach us; in the morning the navigator digs out."""
    s = api.get("/state")
    x, y, z = s["blockX"], s["blockY"], s["blockZ"]
    region = Region((x - 1, y - 1, z - 1), (x + 1, y + 2, z + 1))
    cells = [(x + dx, y + dy, z + dz) for dy in (0, 1) for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]]
    cells.append((x, y + 2, z))
    todo = [c for c in cells if not region.solid(c)]
    inv = Inventory()
    blocks = [b for b in GROUPS["building"] + GROUPS["planks"] if inv.count(b)]
    if sum(inv.count(b) for b in blocks) < len(todo):
        raise NotAvailable(f"need {len(todo)} blocks to wall in, not enough carried")
    # Feet level first: head-level blocks and the roof need a neighbour to be placed against.
    todo.sort(key=lambda c: c[1])
    pool = [[b, inv.count(b)] for b in blocks]

    def next_block():
        while pool and pool[0][1] == 0:
            pool.pop(0)
        if not pool:
            raise NotAvailable("ran out of blocks while walling in")
        pool[0][1] -= 1
        return pool[0][0]

    def has_support(cell, reg):
        return any(reg.solid(add(cell, d)) for d in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)])

    for c in todo:
        region = Region((x - 2, y - 3, z - 2), (x + 2, y + 2, z + 2))
        if region.solid(c):
            continue
        name = region.name(c)
        if not name.endswith(("air", "water")) and not region.hazard(c):
            # Torches, flowers, grass: something non-solid occupies the cell. Break it first.
            mine_cell(ctx.policy, c)
        if not has_support(c, region) and c == (x, y + 2, z):
            # The roof has nothing to click against: cap one of the side walls first (a block on top of a wall
            # beside the head), then the roof goes against that cap — how players close a 1×1 hole.
            for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                wall, cap = (x + dx, y + 1, z + dz), (x + dx, y + 2, z + dz)
                if region.solid(wall) and not region.solid(cap):
                    try:
                        place(next_block(), cap)
                        region = Region((x - 2, y - 3, z - 2), (x + 2, y + 2, z + 2))
                        break
                    except McError as e:
                        log(f"pod: roof cap at {cap} failed: {e}")
        if not has_support(c, region):
            # Nothing to click against (water or air all around): build a support column up from below first.
            below = add(c, (0, -1, 0))
            stack = []
            while not region.solid(below) and below[1] > y - 3:
                stack.append(below)
                below = add(below, (0, -1, 0))
            for s_cell in reversed(stack):
                try:
                    place(next_block(), s_cell)
                except McError as e:
                    log(f"pod: support at {s_cell} failed: {e}")
        try:
            place(next_block(), c)
        except McError as e:
            log(f"pod: could not place at {c}: {e}")
    region = Region((x - 1, y - 1, z - 1), (x + 1, y + 2, z + 1))
    open_cells = [c for c in cells if not region.solid(c)]
    # Either way the walls that went up are still there: record them, so finishing this pod is cheaper than
    # starting another one two blocks away.
    ctx.mem.note_progress("pod", (x, y, z), ctx.dimension, done=len(cells) - len(open_cells), of=len(cells))
    if open_cells:
        raise McError(f"pod left {len(open_cells)} openings")
    ctx.mem.clear_progress("pod", (x, y, z))
    log("walled in for the night")


# ---------------------------------------------------------------- machines (blueprints.py)


def _has_torches_to_spare(c):
    if Inventory().usable("minecraft:torch") <= 2:
        raise NotAvailable("no torches to spare")


@skill(pre=[_has_torches_to_spare], needs={"minecraft:torch": 3}, budget=180, stall=60)
def light_area(ctx, radius=10, limit=6):
    """Spawn-proof the surroundings: torches on the darkest reachable spots (block light 0) nearby, keeping 2.

    The torch check is a declared precondition, not a line in the body: the pool asks it (skill.can_run) before it
    prices this work. As a line it could only be discovered by failing, and the idle rule kept thawing the
    candidate, so "no torches to spare" was logged ninety times in four minutes. The `enclosed()` check stays in
    the body — it reads the world, and an estimate that reads the world cannot be replayed.
    """
    if enclosed():
        raise NotAvailable("sealed in: nothing outside to light")
    spots = [p for p in dark_spots(radius=radius, max_light=0, limit=40) if not ctx.blocked((p["x"], p["y"], p["z"]))]
    if not spots:
        raise NotAvailable("nothing dark nearby")
    lit, misses = 0, 0
    for p in spots[: limit * 2]:
        if lit >= limit or Inventory().usable("minecraft:torch") <= 2 or misses >= 3:
            break   # three unreachable spots in a row: the rest are behind walls too
        pos = (p["x"], p["y"], p["z"])
        r = api.run({"type": "place", "item": "minecraft:torch", "x": pos[0], "y": pos[1], "z": pos[2]}, wait=40)
        if r["status"] == "succeeded":
            lit, misses = lit + 1, 0
        else:
            misses += 1
            ctx.ban(pos, 900)
        yield lit
    if not lit:
        raise NotAvailable("no dark spot could be lit")
    log(f"lit {lit} dark spots")


@skill(start=lambda c: Inventory().count(c.args[2]), verify=lambda c: Inventory().count(c.args[2]) < c.base,
       budget=300, stall=60)
def load_smelter(ctx, machine, input_token, count, fuel, output):
    """Put up to a stack of input into an auto smelter's input chest and matching fuel into its fuel chest, and note
    the expected output as pending so the planner treats it as on its way."""
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


def pending_ready(machine):
    return any(p["ready_at"] <= time.time() for p in machine.get("pending", []))


@skill(start=lambda c: sum(p["count"] for p in c.args[1].get("pending", [])),
       verify=lambda c: sum(p["count"] for p in c.args[1].get("pending", [])) < c.base or c.base == 0,
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
    got = {item: Inventory().count(item) - n for item, n in before.items()}
    ctx.mem.settle_pending(machine["name"], got)
    log(f"collected from {machine['name']}: {got}")


# ---------------------------------------------------------------- base life


@skill(start=lambda c: Inventory().used_slots(), verify=lambda c: Inventory().used_slots() < c.base,
       budget=60, stall=30, per_unit=6)
def tidy_inventory(ctx):
    """Free slots anywhere, no chest needed: drop the least valuable stacks (free_slots_plan) until
    FREE_SLOTS_TARGET slots are free, then step away so they aren't picked straight back up."""
    close_screen()
    x, y, z = feet()
    region = Region((x - 3, y - 1, z - 3), (x + 3, y + 2, z + 3))
    direction = throw_direction(region, (x, y, z))
    if direction is None:
        # A sealed shaft: dropped items land at our feet and the next step picks them all back up.
        raise NotAvailable("no open side to throw items into (shaft)")
    inv = Inventory()
    throw = free_slots_plan(inv.slots, need=max(0, inv.used_slots() - (36 - FREE_SLOTS_TARGET)))
    if not throw:
        raise NotAvailable("nothing to throw away")
    # Face the open side (in a tunnel: back the way we came) so the drops fly where we won't walk next.
    yaw = {(1, 0): -90.0, (-1, 0): 90.0, (0, 1): 0.0, (0, -1): 180.0}[direction]
    api.run({"type": "look", "yaw": yaw, "pitch": 0}, wait=5)
    for s in throw:
        api.post("/click", {"slot": 36 + s["slot"] if s["slot"] < 9 else s["slot"], "button": 1, "action": "THROW"})
        yield s["slot"]
    log(f"threw away {len(throw)} stacks toward {direction}: {sorted({bare(s['id']) for s in throw})}")
    # Step away from the drops, never onto them, before the pickup delay ends.
    spots = [p for p in free_spots(reach=4, limit=12)
             if (p[0] - x) * direction[0] + (p[2] - z) * direction[1] < 0]
    if spots:
        far = max(spots, key=lambda p: math.dist(p, (x, y, z)))
        api.run({"type": "goto", "x": far[0], "y": far[1], "z": far[2], "range": 0.8, "partial": True}, wait=15)


def can_store_here(ctx, local_only=False):
    """A chest is possible without a long trip: a site within 96 blocks (unless local_only), a chest or the planks
    for one in the bag."""
    inv = Inventory()
    if inv.usable("minecraft:chest") or inv.usable("planks") >= 8 or inv.usable("log") >= 2:
        return True
    if local_only:
        return bool(find(["chest", "barrel"], radius=6, limit=1))
    here = feet()
    return any(math.dist(s["pos"], here) <= 96 and site_trek_ok(s) for s in ctx.mem.sites(ctx.dimension))


_TREK_FAILED = {}      # site name -> when a storage trek there failed
TREK_RETRY = 600       # seconds before trying that trek again (a failed trek costs a minute of travel replans)


def site_trek_ok(site, now=None):
    import time
    return (now or time.time()) - _TREK_FAILED.get(site.get("name"), 0) >= TREK_RETRY


def _place_cache_chest(ctx):
    """A chest right here for storage when no site with a chest is reachable; becomes a 'cache' site."""
    near = [c for c in find(["chest", "barrel"], radius=6, limit=6) if openable_container((c["x"], c["y"], c["z"]))]
    if near:
        return near[0]["x"], near[0]["y"], near[0]["z"]
    here = feet()
    if Inventory().used_slots() < 36:
        # The carried chest is a last resort for a completely full bag, not a storage habit (it was placed and
        # re-crafted over and over: 11 caches).
        raise NotAvailable("bag not completely full: no new cache chest")
    if any(math.dist(s["pos"], here) <= 24 for s in ctx.mem.sites(ctx.dimension, kinds=["cache"])):
        # Eight cache chests within 30 blocks once: one per area; beyond it, throwing frees slots instead.
        raise NotAvailable("a cache chest already exists within 24 blocks")
    if Inventory().usable("minecraft:chest") == 0:
        # Crafting needs somewhere to put the result: with a full bag, drop the two least valuable stacks first.
        inv = Inventory()
        if inv.used_slots() >= 35:
            close_screen()
            for s in free_slots_plan(inv.slots, need=2)[:2]:
                api.post("/click", {"slot": 36 + s["slot"] if s["slot"] < 9 else s["slot"], "button": 1,
                                    "action": "THROW"})
        if Inventory().usable("planks") < 8:
            if Inventory().usable("log") >= 2:
                craft(ctx, "planks", 2)
            else:
                raise NotAvailable("no chest and not enough planks for a cache chest")
        craft(ctx, "minecraft:chest", 1)
    x, y, z = feet()
    around = Region((x - 5, y - 4, z - 5), (x + 5, y + 5, z + 5))
    spots = [p for p in free_spots(limit=8) if chest_spot_ok(around, p)][:3] or make_room(ctx)
    for spot in spots:
        try:
            place("minecraft:chest", spot)
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


@skill(pre=[_has_something_to_store], start=lambda c: Inventory().used_slots(), verify=lambda c: Inventory().used_slots() < c.base,
       budget=600, stall=90, per_unit=60)
def deposit(ctx, local_only=False):
    """Store everything beyond the keep list: in a chest at the nearest reachable site within 96 blocks (skipped
    with local_only, e.g. at night), else in a cache chest placed right here (crafted if needed, recorded as a
    site for later trips)."""
    before = Inventory().used_slots()
    moving = store_plan(Inventory().slots)
    if not moving:
        raise NotAvailable("nothing worth storing")
    here, c = feet(), None
    for site in ([] if local_only else sorted(ctx.mem.sites(ctx.dimension), key=lambda s: math.dist(s["pos"], here))):
        if math.dist(site["pos"], here) > 96:
            break
        if not site_trek_ok(site):
            continue
        if not nav.go_to(tuple(site["pos"]), ctx.policy, range_=4, attempts=1):
            import time
            _TREK_FAILED[site.get("name")] = time.time()
            # A cache chest that stays unreachable is dead memory: forget it after 3 failed treks.
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
    r = api.run({"type": "use", "x": c[0], "y": c[1], "z": c[2]}, wait=60)
    if r["result"].get("screen") in (None, "none"):
        raise McError("could not open the home chest")
    try:
        # Container view slot numbers differ from inventory indices: match by owner + inventory index.
        view = {s["index"]: s for s in world.container()["slots"] if s["owner"] == "player"}
        for s in moving:
            v = view.get(s["slot"])
            if v and v["id"] == s["id"]:
                api.post("/click", {"slot": v["slot"], "button": 0, "action": "QUICK_MOVE"})
    finally:
        api.post("/close")
    after = Inventory().used_slots()
    log(f"stored {before - after} stacks in the home chest")
    if after >= before:
        raise NotAvailable("home chest full or nothing moved")


@skill(budget=600, stall=90)
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
        if not nav.go_to(tuple(site["pos"]), ctx.policy, range_=3, attempts=2):
            raise NotAvailable(f"{site['name']} not reachable")
        log(f"repairing {site['name']}: {len(blocks)} blocks")
        api.run({"type": "build", "blocks": blocks}, wait=600)
    region = Region(tuple(snap["lo"]), tuple(snap["hi"]))
    remaining = sum(1 for key in snap["blocks"] if not region.solid(tuple(int(v) for v in key.split(","))))
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

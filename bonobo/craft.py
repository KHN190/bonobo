"""Stations, crafting and smelting, machines fed and collected, tools and armor worn."""

import math
import time
from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import bag as _bag
from . import knowledge as K
from . import api, nav, world
from .api import McError, NotAvailable, log
from .skill import skill
from .data import HAND_MINEABLE_SUFFIX, ARMOR_RANK, ARMOR_SLOTS, GROUPS, LOG_TO_PLANKS, RECIPES, bare, mid
from .knowledge import GROUP_RECIPES, members
from .world import BAG_SLOTS, Inventory, Region, screen_slot, add, find
from .bag import free_slots_plan
from .skillcore import StationMissing, feet, close_screen, free_spots_here, place, mine_cell, gained
from .building import _open_container, _empty_container_slot, _machine_roles, _go_to_machine

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
    return [_bag.throw(s["slot"])
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
            api.post("/click", _bag.quick_move(2))

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
        assert station.pos is not None, "Station.__enter__ raises when it places nothing"
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
            api.post("/click", _bag.quick_move(slot))
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
            api.post("/click", _bag.quick_move(screen_slot(s["slot"])))
            log(f"equipped {bare(s['id'])}")
            changed = True
    return changed

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
                api.post("/click", _bag.quick_move(s["slot"]))
    finally:
        api.post("/close")
    got = {item: gained(lambda item=item: Inventory().count(item), n) - n for item, n in before.items()}
    ctx.mem.settle_pending(machine["name"], got)
    log(f"collected from {machine['name']}: {got}")

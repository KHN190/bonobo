"""Storing: the bag tidied, a cache chest, deposits and withdrawals, sites repaired, the base found."""

import math
from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import bag as _bag
from . import api, nav, world
from .api import McError, NotAvailable, log
from .skill import skill
from .data import BASE_MARKERS, GROUPS, MARKER_WEIGHT, PLACEABLE_AS, bare, mid
from .world import BAG_SLOTS, Inventory, Region, add, find
from .bag import let_go, FREE_SLOTS_TARGET, throw_direction, store_plan
from .terrain import chest_spot_ok
from .skillcore import feet, close_screen, free_spots_here, opened, place, lost
from .craft import craft, make_bag_room, make_room

def openable_container(pos):
    """A chest opens only with no solid block right above it (barrels always open)."""
    above = add(pos, (0, 1, 0))
    r = Region(pos, above)
    return r.name(pos) == "barrel" or not r.solid(above)

def stored_left(st, c):
    """`remaining` of deposit: nothing beyond the keep list left in the bag (bag.store_plan)."""
    rest_ = store_plan(st["inv"].slots)
    return {} if not rest_ else {"to store": len(rest_)}

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
        api.post("/click", _bag.throw(s["slot"]))
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
        chest = ctx.mem.home_part("chests", ctx.dimension, here, anywhere=True) if site.get("kind") == "home" else None
        if not nav.arrived(chest or tuple(site["pos"]), ctx.policy, range_=4, attempts=1):
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
        chests = [c for c in ([{"x": chest[0], "y": chest[1], "z": chest[2]}] if chest else [])
                  + find(["chest", "barrel"], radius=8, limit=6) if openable_container((c["x"], c["y"], c["z"]))]
        if chests:
            c = (chests[0]["x"], chests[0]["y"], chests[0]["z"])
            break
        yield site["name"]
    if c is None:
        c = _place_cache_chest(ctx)
        moving = store_plan(Inventory().slots)
    r = api.run({"type": "use", "x": c[0], "y": c[1], "z": c[2]}, wait=60, awaits="the chest's slots read on its screen")
    if not opened(r):
        raise McError("could not open the home chest")
    try:
        # container view slot numbers differ from inventory indices: match by owner and index
        view = {s["index"]: s for s in world.container()["slots"] if s["owner"] == "player"}
        for s in moving:
            v = view.get(s["slot"])
            if v and v["id"] == s["id"]:
                api.post("/click", _bag.quick_move(v["slot"]))
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
    if not opened(r):
        ctx.mem.forget_container(pos)
        raise NotAvailable(f"the container at {pos} did not open")
    left = int(count)
    try:
        for s in world.container()["slots"]:
            if left <= 0:
                break
            if s["owner"] != "player" and s["id"] == item:
                api.post("/click", _bag.quick_move(s["slot"]))
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

"""Fluids and the Nether portal: fill a water bucket, cast obsidian on a lava pool, light a portal frame.

Route (community knowledge): no diamonds needed for obsidian placement, but with a diamond pickaxe the simplest
reliable way is to pour water over a lava pool's surface (every lava source it flows onto turns to obsidian), take
the water back, mine the obsidian, and build a 4×5 frame (corners any block) lit with flint and steel.
Pure planners (`fill_spot`, `pour_plan`, `portal_light_aim`) are offline-tested; the skills only execute them."""
import math

from . import api, blueprints, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .world import Inventory, Region, add, find

REACH = 4.0


def _eye(cell):
    return cell[0] + 0.5, cell[1] + 1.62, cell[2] + 0.5


def is_source(region, p, fluid):
    """A still source block of `fluid` (level 0). Regions without block properties count every fluid cell."""
    if region.name(p) != fluid:
        return False
    prop = getattr(region, "prop", None)
    level = prop(p, "level") if prop else None
    return level is None or str(level) == "0"


def standable(region, p):
    below, head = add(p, (0, -1, 0)), add(p, (0, 1, 0))
    return (region.inside(below) and region.solid(below) and not region.hazard(below)
            and not region.solid(p) and not region.hazard(p) and not region.solid(head) and not region.hazard(head))


def clear_line(region, eye, target_cell, target_point, margin=0.2):
    """Pure: nothing solid between the eye and a point in `target_cell` (sampled every 0.1 block, with a margin for
    aim error). A bucket clicks whatever the crosshair meets first: water behind a stone wall was 'filled' into the
    stone, and a pour grazing a cache chest opened the chest instead of placing water."""
    steps = max(1, int(math.dist(eye, target_point) / 0.1))
    target = tuple(target_cell)
    offsets = [(dx, dz) for dx in (-margin, 0, margin) for dz in (-margin, 0, margin)]
    for i in range(1, steps):
        t = i / steps
        q = [eye[k] + (target_point[k] - eye[k]) * t for k in range(3)]
        for dx, dz in offsets:
            p = (int(math.floor(q[0] + dx)), int(math.floor(q[1])), int(math.floor(q[2] + dz)))
            if p != target and region.solid(p):
                return False
    return True


def lava_within(region, p, r):
    """Lava in the box of half-size r around p (feet, head and the floor layers)."""
    for dx in range(-r, r + 1):
        for dy in range(-1, 2 + 1):
            for dz in range(-r, r + 1):
                if region.name((p[0] + dx, p[1] + dy, p[2] + dz)) == "lava":
                    return True
    return False


def surface_aim(cell):
    """Pure: where to point a bucket at a source block — just under its top face, so the ray comes down onto the
    surface instead of crossing neighbouring (flowing) water on the way in."""
    return cell[0] + 0.5, cell[1] + 0.95, cell[2] + 0.5


def fill_spot(region, here, fluid="water", reach=REACH):
    """Pure: (stand, source) to fill a bucket from — a dry standable cell whose eye is within reach of a source
    block's top face, never below its surface (and, for lava, never within 2 blocks of lava itself). Nearest to `here` first."""
    best = None
    sources = [p for p in region.blocks if is_source(region, p, fluid)]
    for w in sources:
        for dx in range(-3, 4):
            for dz in range(-3, 4):
                # Never below the surface: a level view entered the pool sideways through flowing water, which a
                # bucket's ray ignores — "clicked water but the bucket stayed empty" twice on real lakes.
                for dy in (0, 1):
                    s = (w[0] + dx, w[1] + dy, w[2] + dz)
                    if s == w or not standable(region, s) or region.name(s) == "water":
                        continue
                    if fluid == "lava" and lava_within(region, s, 2):
                        continue
                    top = surface_aim(w)
                    if math.dist(_eye(s), top) > reach or not clear_line(region, _eye(s), w, top):
                        continue
                    d = math.dist(s, here)
                    if best is None or d < best[0]:
                        best = (d, s, w)
    return None if best is None else (best[1], best[2])


def pour_plan(region, here, reach=REACH):
    """Pure: (stand, bank block, lava sources reached) to cast obsidian. The bank block sits at the lava surface
    level next to a lava source, with air above (the water lands there and flows over the pool); the stand spot
    reaches the bank's top face and keeps 3+ blocks from any lava. Most lava covered wins, then nearest."""
    best = None
    lava = [p for p in region.blocks if is_source(region, p, "lava")
            and not region.solid(add(p, (0, 1, 0))) and not region.hazard(add(p, (0, 1, 0)))]
    lava_set = set(lava)
    banks = set()
    for p in lava:
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            b = (p[0] + dx, p[1], p[2] + dz)
            up = add(b, (0, 1, 0))
            if region.solid(b) and not region.hazard(b) and not region.solid(up) and not region.hazard(up):
                banks.add(b)
    for b in banks:
        covered = sum(1 for p in lava_set if p[1] == b[1] and math.dist(p, b) <= 4)
        top = (b[0] + 0.5, b[1] + 1.0, b[2] + 0.5)
        for dx in range(-4, 5):
            for dz in range(-4, 5):
                for dy in (1, 2):
                    s = (b[0] + dx, b[1] + dy, b[2] + dz)
                    if s == add(b, (0, 1, 0)) or not standable(region, s) or lava_within(region, s, 2):
                        continue
                    if math.dist(_eye(s), top) > reach or not clear_line(region, _eye(s), b, top):
                        continue
                    key = (-covered, math.dist(s, here))
                    if best is None or key < best[0]:
                        best = (key, s, b, covered)
    return None if best is None else (best[1], best[2], best[3])


def portal_light_aim(origin, turns):
    """Pure: the point to click with flint and steel — the top face of the frame's inner bottom obsidian."""
    d = blueprints.rotate_offset((1, 0, 0), turns)
    x, y, z = origin[0] + d[0], origin[1] + d[1], origin[2] + d[2]
    return x + 0.5, y + 1.0, z + 0.5


def _use(item, aim, on_block):
    r = api.run({"type": "use_item", "item": item, "x": aim[0], "y": aim[1], "z": aim[2], "onBlock": on_block},
                wait=30)
    if r["status"] != "succeeded":
        raise McError(f"using {item} failed: {r['message']}")
    res = r.get("result") or {}
    if "hitX" in res:
        log(f"   {item.split(':')[1]} clicked {(res['hitX'], res['hitY'], res['hitZ'])} face {res.get('face')} "
            f"(aimed at {tuple(round(v, 2) for v in aim)})")
    return res


@skill(done=lambda c: Inventory().count("minecraft:water_bucket") > 0,
       budget=300, stall=120, per_unit=60)
def fill_water_bucket(ctx):
    """Fill an empty bucket at the nearest reachable still water."""
    if not Inventory().count("minecraft:bucket"):
        raise NotAvailable("no empty bucket to fill")
    here = nav.feet_now()
    hits = sorted((h for h in find(["water"], radius=48, limit=60) if not ctx.blocked((h["x"], h["y"], h["z"]))),
                  key=lambda h: h["distance"])
    for h in hits[:6]:
        c = (h["x"], h["y"], h["z"])
        region = Region(add(c, (-5, -3, -5)), add(c, (5, 3, 5)), props=True)
        spot = fill_spot(region, here)
        if spot is None:
            ctx.ban(c)
            continue   # planning only (no game action): trying the next water cell is not a retry
        stand, source = spot
        if not nav.go_to(stand, ctx.policy, range_=0.6, attempts=1):
            ctx.ban(c)
            raise api.NavFailed(f"stand spot {stand} for water at {source} not reachable")
        _use("minecraft:bucket", surface_aim(source), False)     # the same point fill_spot checked
        yield Inventory().count("minecraft:water_bucket")
        if Inventory().count("minecraft:water_bucket"):
            return
        # "used" isn't "filled": one real attempt per call, then the retry policy decides.
        ctx.ban(c)
        ctx.ban(source)
        raise NotAvailable(f"clicked water at {source} but the bucket stayed empty")
    raise NotAvailable("no still water with a clear line of sight within 48 blocks")


def _obsidian_near(pos, radius=8):
    """Obsidian blocks in a box around the pour spot (not around the player, and not capped by a find() limit):
    a pour that turned 47 lava blocks into obsidian was once reported as "no obsidian formed"."""
    if not pos:
        return 0
    region = Region(add(pos, (-radius, -2, -radius)), add(pos, (radius, 2, radius)))
    return sum(1 for n in region.blocks.values() if n == "obsidian")


@skill(budget=900, stall=240, per_unit=120)
def cast_obsidian(ctx):
    """Turn a lava pool's surface into obsidian: pour water from a safe bank, wait, take the water back.
    Returns the number of new obsidian blocks near the pool (the mine skill collects them)."""
    if not Inventory().count("minecraft:water_bucket"):
        raise NotAvailable("need a water bucket to cast obsidian")
    here = nav.feet_now()
    pools = [h for h in find(["lava"], radius=48, limit=80) if not ctx.blocked((h["x"], h["y"], h["z"]))]
    if pools:
        ctx.mem.add_lava(pools[0], ctx.dimension)
        made = yield from _cast_pools(ctx, pools, here)
        if made:
            return made
    # No castable lava here (each reason is logged): go to another remembered pool instead of retrying these.
    known = sorted((p for p in ctx.mem.lava_pools(ctx.dimension)
                    if not ctx.blocked(tuple(p)) and math.dist(p, here) > 24), key=lambda p: math.dist(p, here))
    if not known:
        raise NotAvailable("no castable lava here and no other remembered pool")
    target = tuple(known[0])
    log(f"   no castable lava here; heading to the remembered pool at {target}")
    if not nav.go_to((target[0], target[1] + 2, target[2]), ctx.policy, range_=8, attempts=1):
        ctx.ban(target, 900)
        raise api.NavFailed(f"remembered lava pool at {target} not reachable")
    yield None
    here = nav.feet_now()
    pools = [h for h in find(["lava"], radius=48, limit=80) if not ctx.blocked((h["x"], h["y"], h["z"]))]
    if not pools:
        ctx.mem.forget_lava(target)
        raise NotAvailable(f"the remembered lava pool at {target} is gone")
    made = yield from _cast_pools(ctx, pools, here)
    if made:
        return made
    raise NotAvailable("no lava pool could be cast into obsidian (reasons logged above)")


def _cast_pools(ctx, pools, here):
    """Try up to 5 distinct pools; returns the obsidian made (0 when none worked). Every failure logs its reason
    and is banned for 10 min only — bans live in the agent's memory, so a restart after a mod fix clears them."""
    tried = set()
    for h in pools:
        c = (h["x"], h["y"], h["z"])
        if any(math.dist(c, t) < 6 for t in tried):
            continue
        tried.add(c)
        if len(tried) > 5:
            break
        region = Region(add(c, (-7, -3, -7)), add(c, (7, 4, 7)), props=True)
        plan = pour_plan(region, here)
        if plan is None:
            log(f"   lava at {c}: no bank next to still lava with a safe stand spot and a clear line of sight")
            ctx.ban(c, 600)
            continue
        stand, bank, covered = plan
        log(f"   casting obsidian: pouring water on {bank} from {stand} (~{covered} lava sources)")
        if not nav.go_to(stand, ctx.policy, range_=0.6, attempts=1):
            log(f"   lava at {c}: stand spot {stand} not reachable")
            ctx.ban(c, 600)
            continue
        before = _obsidian_near(bank)
        # Use the item, not use-on-block: the on-block path's interactItem fallback could fire a second use with the
        # now-empty bucket and scoop the water straight back (bench 03:57: water for one poll, lava bucket after).
        _use("minecraft:water_bucket", (bank[0] + 0.5, bank[1] + 1.0, bank[2] + 0.5), False)
        if not Inventory().count("minecraft:bucket"):
            # "used" isn't "poured": the click didn't place water. Logged with the hit block by _use.
            log(f"   lava at {c}: the pour on {bank} placed no water (bucket still full)")
            ctx.ban(c, 600)
            continue
        api.run({"type": "wait", "ticks": 80}, wait=15)   # water spreads ~1 block per 5 ticks: let it cover the pool
        made = _obsidian_near(bank) - before
        try:
            _use("minecraft:bucket", (bank[0] + 0.5, bank[1] + 1.5, bank[2] + 0.5), False)
        except McError as e:
            log(f"   could not take the water back: {e}")
        yield made
        if made > 0:
            log(f"cast {made} obsidian at {bank}")
            return made
        log(f"   lava at {c}: water poured but no obsidian formed")
        ctx.ban(c, 600)
    return 0


def light_portal(ctx, origin, turns):
    """Flint and steel on the inner bottom obsidian; verified by a nether_portal block inside the frame."""
    for attempt in range(2):
        aim = portal_light_aim(origin, turns)
        if attempt:
            d = blueprints.rotate_offset((2, 0, 0), turns)
            aim = (origin[0] + d[0] + 0.5, origin[1] + 1.0, origin[2] + d[2] + 0.5)
        _use("minecraft:flint_and_steel", aim, True)
        api.run({"type": "wait", "ticks": 10}, wait=5)
        lo = add(origin, (-3, 0, -3))
        hi = add(origin, (3, 4, 3))
        if any(n == "nether_portal" for n in Region(lo, hi).blocks.values()):
            log(f"nether portal lit at {origin}")
            return
    raise McError("the portal frame did not light")

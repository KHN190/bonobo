"""Fluids and the Nether portal: fill a water bucket, cast a portal frame in place, light it. Route (the speedrun way): no diamond pickaxe and no mining of obsidian — the frame is cast where it stands. Each frame cell is walled in by a mould of throwaway blocks, filled with lava from a bucket and turned to obsidian with water, bottom-up; the water is taken back, the mould inside the frame broken, and the frame lit with flint and steel. Pure planners (`fill_spot`, `cast_frame_plan`, `portal_light_aim`) are offline-tested; the skills only execute them."""

import math

from . import knowledge as K
from . import knowledge as _k
from . import api, blueprints, nav, skillcore
from .api import McError, NotAvailable, log
from .data import EYE_HEIGHT, GROUPS, bare
from .skill import skill
from .skillcore import body_state, feet, gained
from .world import Inventory, Region, add, find

REACH = nav.WORK_REACH        # the reach holds uses: the jar's range less its margin

def _eye(cell):
    return cell[0] + 0.5, cell[1] + EYE_HEIGHT, cell[2] + 0.5

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
    """Pure: nothing solid between the eye and a point in `target_cell` (sampled every 0.1 block, with an aim margin)."""

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
    """Pure: aim just under a source's top face, so the ray comes down onto it rather than through flowing water."""

    return cell[0] + 0.5, cell[1] + 0.95, cell[2] + 0.5

def fill_spot(region, here, fluid="water", reach=REACH):
    """Pure: (stand, source) to fill a bucket from: a dry cell whose eye reaches the source's top, never below its surface (lava: 2 away)."""

    best = None
    sources = [p for p in region.blocks if is_source(region, p, fluid)]
    for w in sources:
        for dx in range(-3, 4):
            for dz in range(-3, 4):
                # never below the surface: a level view entered sideways through flowing water, which the bucket's ray ignores
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

def portal_light_cell(origin, turns, attempt=0):
    """Pure: the frame's inner bottom obsidian clicked with flint and steel (the second attempt one block further in)."""
    d = blueprints.rotate_offset((2 if attempt else 1, 0, 0), turns)
    return origin[0] + d[0], origin[1] + d[1], origin[2] + d[2]

def portal_light_aim(origin, turns):
    """Pure: the point to click with flint and steel — the top face of the frame's inner bottom obsidian."""
    t = nav.use_on_top("minecraft:flint_and_steel", portal_light_cell(origin, turns))
    return t["x"], t["y"], t["z"]

def use_task(item, aim, on_block):
    return {"type": "use_item", "item": item, "x": aim[0], "y": aim[1], "z": aim[2], "onBlock": on_block}

def light_commands(state, args):
    """Pure: flint and steel on the inner bottom obsidian (then one block further in), then half a second for the portal."""

    origin, turns, attempt = args
    if not state["inv"].count("minecraft:flint_and_steel"):
        raise NotAvailable("no flint and steel to light the portal")
    return [nav.use_on_top("minecraft:flint_and_steel", portal_light_cell(origin, turns, attempt)),
            {"type": "wait", "ticks": 10}]

def _use(item, aim, on_block):
    r = api.run(use_task(item, aim, on_block), wait=30, awaits="callers read where the click landed (the hit face) before the next use")
    if r["status"] != "succeeded":
        raise McError(f"using {item} failed: {r['message']}")
    res = r.get("result") or {}
    if "hitX" in res:
        log(f"   {item.split(':')[1]} clicked {(res['hitX'], res['hitY'], res['hitZ'])} face {res.get('face')} "
            f"(aimed at {tuple(round(v, 2) for v in aim)})")
    return res

@skill(gives=K.GIVES_FILL, needs={"minecraft:bucket": 1}, speed={}, done=lambda c: Inventory().count("minecraft:water_bucket") > 0,
       budget=300, stall=120, provides={"fill": lambda ctx, s: ()})
def fill_water_bucket(ctx):
    """Fill an empty bucket at the nearest reachable still water."""
    here = skillcore.feet()
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
        if not nav.arrived(stand, ctx.policy, range_=0.6, attempts=1):
            ctx.ban(c)
            raise api.NavFailed(f"stand spot {stand} for water at {source} not reachable")
        _use("minecraft:bucket", surface_aim(source), False)     # the same point fill_spot checked
        yield Inventory().count("minecraft:water_bucket")
        if gained(lambda: Inventory().count("minecraft:water_bucket"), 0):
            return
        # "used" isn't "filled": one attempt per call, then the retry policy decides
        ctx.ban(c)
        ctx.ban(source)
        raise NotAvailable(f"clicked water at {source} but the bucket stayed empty")
    raise NotAvailable("no still water with a clear line of sight within 48 blocks")

def cast_frame_plan(bp, origin, turns, solid):
    """Pure: [(cell, mould)] bottom-up: each obsidian cell with the open neighbours a lava source would run into."""

    obs = [pos for pos, part, *_ in blueprints.placed(bp, origin, turns) if part.item == "minecraft:obsidian"]
    frame = set(pos for pos, *_ in blueprints.placed(bp, origin, turns))
    out, done = [], set()
    for c in sorted(obs, key=lambda p: (p[1], p[0], p[2])):
        mould = []
        for d in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, -1, 0)):
            n = add(c, d)
            if n in done or (n in frame and n not in obs):
                continue           # obsidian already cast, or a corner block of the frame itself
            if not solid(n):
                mould.append(n)
        out.append((c, mould))
        done.add(c)
    return out

def mould_to_break(bp, origin, turns, placed_mould):
    """Pure: mould blocks to break again: inside the frame, or on a frame cell still to be cast."""

    inside = set(blueprints.clear_cells(bp, origin, turns))
    frame = set(pos for pos, *_ in blueprints.placed(bp, origin, turns))
    return [m for m in placed_mould if m in inside or m in frame]

def _lava_bucket(ctx, here):
    """A lava bucket in hand: carried, or filled from the nearest lava source a stand spot reaches."""
    if Inventory().count("minecraft:lava_bucket"):
        return
    if not Inventory().count("minecraft:bucket"):
        raise NotAvailable("no bucket for lava")
    for h in sorted(find(["lava"], radius=32, limit=40), key=lambda h: h["distance"])[:6]:
        c = (h["x"], h["y"], h["z"])
        spot = fill_spot(Region(add(c, (-5, -3, -5)), add(c, (5, 3, 5)), props=True), here, fluid="lava")
        if spot is None or ctx.blocked(c):
            continue
        stand, source = spot
        nav.arrive(stand, ctx.policy, range_=0.6)
        _use("minecraft:bucket", surface_aim(source), False)
        if gained(lambda: Inventory().count("minecraft:lava_bucket"), 0):
            ctx.mem.note_seen("lava", source, ctx.dimension)
            return
        ctx.ban(c)
    raise NotAvailable("no lava source within reach to fill a bucket from")

def floor_aim(cell):
    """The top face of the block under `cell`: a bucket clicked there empties into `cell`."""
    return cell[0] + 0.5, cell[1] + 0.02, cell[2] + 0.5

def portal_lit(origin):
    """A nether_portal block inside the frame at `origin`: the one proof a portal stands."""
    return any(n == "nether_portal" for n in Region(add(origin, (-3, 0, -3)), add(origin, (3, 4, 3))).blocks.values())

def light_portal(ctx, origin, turns):
    """Flint and steel on the inner bottom obsidian; verified by a nether_portal block inside the frame."""
    for attempt in range(2):
        # the click and its settle as one chain; closed loop between attempts
        done = api.run_chain(light_commands({"inv": Inventory()}, (origin, turns, attempt)), stop_on_failure=True)
        if done and done[0]["status"] != "succeeded":
            raise McError(f"using flint_and_steel failed: {done[0]['message']}")
        if portal_lit(origin):
            log(f"nether portal lit at {origin}")
            return
    raise McError("the portal frame did not light")

def _open_lava(region, here):
    """Pure: lava cells within reach of `here` with air beside them, nearest first."""
    x, y, z = here
    return sorted((p for p in region.blocks if region.name(p) == "lava"
                   and math.dist(p, (x, y + 1, z)) <= nav.REACH
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

def swimming(state):
    """The one "in the water" test: in water and not standing, or standing with the head under (breath below full)."""

    return bool(state.get("inWater")) and (not state.get("onGround", False)
                                            or float(state.get("air", AIR_FULL) or 0) < AIR_FULL)

AIR_FULL = 300          # the air meter's top, in ticks

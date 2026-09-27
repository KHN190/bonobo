"""Fluids and the Nether portal: fill a water bucket, cast a portal frame in place, light it.

Route (the speedrun way): no diamond pickaxe and no mining of obsidian — the frame is cast where it stands. Each
frame cell is walled in by a mould of throwaway blocks, filled with lava from a bucket and turned to obsidian with
water, bottom-up; the water is taken back, the mould inside the frame broken, and the frame lit with flint and steel.
Pure planners (`fill_spot`, `cast_frame_plan`, `portal_light_aim`) are offline-tested; the skills only execute them."""
import math

from . import knowledge as K
from . import api, blueprints, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .skillcore import gained
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


def portal_light_aim(origin, turns):
    """Pure: the point to click with flint and steel — the top face of the frame's inner bottom obsidian."""
    d = blueprints.rotate_offset((1, 0, 0), turns)
    x, y, z = origin[0] + d[0], origin[1] + d[1], origin[2] + d[2]
    return x + 0.5, y + 1.0, z + 0.5


def use_task(item, aim, on_block):
    return {"type": "use_item", "item": item, "x": aim[0], "y": aim[1], "z": aim[2], "onBlock": on_block}


def light_commands(state, args):
    """Pure: the chain that lights the frame at `origin` — flint and steel on the inner bottom obsidian (the second
    attempt one block further in), then half a second for the portal blocks to appear."""
    origin, turns, attempt = args
    if not state["inv"].count("minecraft:flint_and_steel"):
        raise NotAvailable("no flint and steel to light the portal")
    aim = portal_light_aim(origin, turns)
    if attempt:
        d = blueprints.rotate_offset((2, 0, 0), turns)
        aim = (origin[0] + d[0] + 0.5, origin[1] + 1.0, origin[2] + d[2] + 0.5)
    return [use_task("minecraft:flint_and_steel", aim, True), {"type": "wait", "ticks": 10}]


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
       budget=300, stall=120, per_unit=60, provides={"fill": lambda ctx, s: ()})
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
        if not nav.arrived(stand, ctx.policy, range_=0.6, attempts=1):
            ctx.ban(c)
            raise api.NavFailed(f"stand spot {stand} for water at {source} not reachable")
        _use("minecraft:bucket", surface_aim(source), False)     # the same point fill_spot checked
        yield Inventory().count("minecraft:water_bucket")
        if gained(lambda: Inventory().count("minecraft:water_bucket"), 0):
            return
        # "used" isn't "filled": one real attempt per call, then the retry policy decides.
        ctx.ban(c)
        ctx.ban(source)
        raise NotAvailable(f"clicked water at {source} but the bucket stayed empty")
    raise NotAvailable("no still water with a clear line of sight within 48 blocks")


def cast_frame_plan(bp, origin, turns, solid):
    """Pure: [(cell, mould)] — the frame's obsidian cells bottom-up, each with the mould cells to fill first: every
    neighbour a lava source would run into (the four sides and below) that `solid(cell)` says is open. Mould on a
    future frame cell or inside the frame is broken again later (`mould_to_break`)."""
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
    """Pure: the mould blocks that must go again — inside the frame (the portal needs the air) or on a frame cell
    still to be cast (the lava goes there)."""
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
        # The click and its settle as one chain; closed loop between attempts: the portal lit (portal_lit)
        done = api.run_chain(light_commands({"inv": Inventory()}, (origin, turns, attempt)), stop_on_failure=True)
        if done and done[0]["status"] != "succeeded":
            raise McError(f"using flint_and_steel failed: {done[0]['message']}")
        if portal_lit(origin):
            log(f"nether portal lit at {origin}")
            return
    raise McError("the portal frame did not light")

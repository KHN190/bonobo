"""The stronghold and the End: fill the end portal frame with eyes of ender and go through; the dragon fight is in
dragon.py. Pure `frames_missing_eye` / `portal_centre` are offline-tested."""
import math
import time

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import api, nav, skillcore
from .api import McError, NotAvailable, log
from .skill import skill
from .world import Inventory, Region, cells_with, find

def frames_missing_eye(region):
    """Pure: end portal frame blocks without an eye (block state eye=false)."""
    return cells_with(region, "end_portal_frame", "eye", "true", want=False)

def portal_centre(frames):
    """Pure: the centre of the 3×3 opening surrounded by the 12 frame blocks (x, y, z)."""
    if not frames:
        return None
    xs, zs = [p[0] for p in frames], [p[2] for p in frames]
    return (min(xs) + max(xs)) // 2, frames[0][1], (min(zs) + max(zs)) // 2

def eye_plan(missing, centre, floor_solid, lit, have_block):
    """Pure: (floor cell to fill or None, where to stand, frames to fill): stand over the ring's middle and fill every eye from there (each frame within 2.3 blocks)."""
    if lit or not missing:
        return None, None, []
    floor = (centre[0], centre[1] - 1, centre[2])
    if not floor_solid and not have_block:
        raise NotAvailable("no block to put over the ring's middle to stand on")
    return (None if floor_solid else floor), centre, sorted(missing)

def outside_spot(frame, centre):
    """Pure: the floor cell just outside the ring next to a frame block (away from the 3×3 opening)."""
    dx, dz = frame[0] - centre[0], frame[2] - centre[2]
    if abs(dx) >= abs(dz):
        return frame[0] + (1 if dx > 0 else -1), frame[1], frame[2]
    return frame[0], frame[1], frame[2] + (1 if dz > 0 else -1)

@skill(gives=["state:end_portal_open"], remaining=_k.blocks_there("end_portal"), needs={"minecraft:ender_eye": 1}, done=lambda c: not frames_missing_eye(_frame_region()) if find(["end_portal_frame"], 32, 1) else False,
       budget=600, stall=180, provides={"activate:end_portal": lambda ctx, s: ()})
def activate_end_portal(ctx):
    """Fill every missing eye from a block over the middle's lava, in one chain; the portal opens under the feet."""
    from .skillcore import place
    hits = find(["end_portal_frame"], radius=32, limit=12)
    if not hits:
        raise NotAvailable("no end portal frame within 32 blocks (dig down at the stronghold estimate first)")
    region = _frame_region()
    missing = frames_missing_eye(region)
    if Inventory().count("minecraft:ender_eye") < len(missing):
        raise NotAvailable(f"need {len(missing)} eyes of ender, have {Inventory().count('minecraft:ender_eye')}")
    centre = portal_centre([(h["x"], h["y"], h["z"]) for h in hits])
    assert centre is not None, "hits is not empty"
    lit = any(n == "end_portal" for n in region.blocks.values())
    below = (centre[0], centre[1] - 1, centre[2])
    block = nav.building_item()
    floor, stand, frames = eye_plan(missing, centre, Region(below, below).solid(below), lit, bool(block))
    if stand is None:
        return
    if floor is not None:
        nav.arrived(outside_spot(min(frames, key=lambda f: math.dist(f, skillcore.feet())), centre), ctx.policy,
                    range_=1.0, attempts=1)
        place(block, floor)
    if not nav.arrived(stand, ctx.policy, range_=0.5, attempts=1):
        raise api.NavFailed(f"the ring's middle at {stand} is not reachable")
    # every eye in one chain: the last opens the portal under the feet
    done = api.run_chain([{"type": "use_item", "item": "minecraft:ender_eye", "x": f[0] + 0.5, "y": f[1] + 0.8125,
                           "z": f[2] + 0.5, "onBlock": True} for f in frames], stop_on_failure=True, wait=20)
    bad = [t for t in done if t["status"] != "succeeded"]
    if bad or len(done) < len(frames):
        raise McError(f"placing eyes from the middle failed: {bad[0]['message'] if bad else 'chain cut short'}")
    yield stand
    if api.get("/state")["dimension"] == "minecraft:the_end":
        log("end portal activated (and fallen through)")
        return
    if frames_missing_eye(_frame_region()):
        raise McError("some frames still have no eye")
    log("end portal activated")

BRICKS = ["minecraft:stone_bricks", "minecraft:mossy_stone_bricks", "minecraft:cracked_stone_bricks"]
ROOM_Y = 30  # strongholds sit around y 0–50

SCAN = 48  # the mod's /find maximum
# 40 apart: each point costs a 40–60 s tunnel (24 apart with a 32 scan found nothing in 3 min)

def search_points(centre, y=ROOM_Y, rings=3, step=40):
    """Pure: where to look for the portal room — the estimate first, then square rings `step` apart at depth y."""
    cx, cz = centre
    pts = [(cx, y, cz)]
    for r in range(1, rings + 1):
        ring = [(dx, dz) for dx in range(-r, r + 1) for dz in (-r, r)] + \
               [(dx, dz) for dz in range(-r + 1, r) for dx in (-r, r)]
        pts += [(cx + dx * step, y, cz + dz * step) for dx, dz in sorted(ring, key=lambda d: (abs(d[0]) + abs(d[1]), d))]
    return pts

def next_brick(bricks, visited, radius=12):
    """Pure: the nearest stronghold brick not near a place already visited (corridors lead to the room)."""
    fresh = [b for b in bricks if all(math.dist(b[:3], v[:3]) > radius for v in visited)]
    return min(fresh, key=lambda b: b[3]) if fresh else None

ROOM_REACH = 12   # one number for "we are at the portal room": the contract, the walk and the bench check share it

@skill(gives=["state:portal_room_found"], remaining=_k.blocks_there("end_portal_frame"), needs={"tool:pickaxe:0": 1}, done=lambda c: bool(find(["end_portal_frame"], ROOM_REACH, 1)), budget=900, stall=240,
       provides={"seek:portal_room": lambda ctx, s: ()})
def find_portal_room(ctx):
    """Dig to stronghold depth near the estimate, follow bricks, else search rings, until an end portal frame is within 32 blocks."""
    sites = ctx.mem.sites("minecraft:overworld", kinds=["stronghold"])
    if not sites:
        raise NotAvailable("no stronghold estimate yet (locate_stronghold first)")
    # the nearest estimate, not the oldest: a stale one walked thousands of blocks back
    _here = skillcore.feet()
    sx, _, sz = min(sites, key=lambda s: math.dist(s["pos"], _here))["pos"]
    visited = []
    points = search_points((sx, sz))
    for _ in range(len(points) + 12):
        hit = find(["end_portal_frame"], SCAN, 1)
        if hit:
            pos = (hit[0]["x"], hit[0]["y"], hit[0]["z"])
            ctx.mem.add_site("portal_room", pos, "minecraft:overworld", name="portal_room")
            log(f"end portal room found at {pos}")
            # seeing it is not reaching it: the scan sees 48 blocks, often from the surface, so dig down until within ROOM_REACH
            for _ in range(4):
                if math.dist(skillcore.feet(), pos) <= ROOM_REACH:
                    return True
                nav.arrived(pos, ctx.policy, range_=ROOM_REACH - 4, attempts=1)
                yield skillcore.feet()
            if math.dist(skillcore.feet(), pos) > ROOM_REACH:
                raise api.NavFailed(f"portal room at {pos} spotted but not reached", pos=pos)
            return True
        bricks = [(b["x"], b["y"], b["z"], b["distance"]) for b in find(BRICKS, SCAN, 40)]
        b = next_brick(bricks, visited)
        if b is not None:
            target = b[:3]
        elif points:
            target = points.pop(0)
        else:
            break
        visited.append(target)
        log(f"   portal room search: heading to {target}")
        nav.arrived(target, ctx.policy, range_=3, attempts=1)
        yield target
    raise NotAvailable("no end portal frame around the stronghold estimate")

def _frame_region():
    hits = find(["end_portal_frame"], radius=32, limit=12)
    if not hits:
        return Region((0, 0, 0), (0, 0, 0), props=True)
    xs, ys, zs = [h["x"] for h in hits], [h["y"] for h in hits], [h["z"] for h in hits]
    return Region((min(xs) - 2, min(ys), min(zs) - 2), (max(xs) + 2, max(ys), max(zs) + 2), props=True)

PORTAL_ARRIVE_S = 10.0     # standing in the portal this long without a dimension change: it did not take us

@skill(gives=["state:in_the_end"], remaining=_k.in_dimension(lambda c: "minecraft:the_end"), needs={}, done=lambda c: api.get("/state")["dimension"] == "minecraft:the_end", budget=120, stall=60)
def enter_end(ctx):
    """Jump into the activated end portal (the centre of the frame ring)."""
    centre = portal_centre([(h["x"], h["y"], h["z"]) for h in find(["end_portal_frame"], radius=32, limit=12)])
    if centre is None:
        raise NotAvailable("no end portal nearby")
    if not find(["end_portal"], radius=32, limit=1):
        raise NotAvailable("the end portal isn't active yet")
    nav.arrived(centre, ctx.policy, range_=0.6, attempts=1)
    end = time.time() + PORTAL_ARRIVE_S
    while time.time() < end:
        api.run({"type": "wait", "ticks": 20}, wait=5, awaits="the dimension change after stepping in")
        yield None
        if api.get("/state")["dimension"] == "minecraft:the_end":
            return True
    raise McError("stood in the end portal but didn't arrive in the End")


def throw_ender_pearl(ctx, target):
    """Shell, never planned: throw a pearl at `target`, land there."""
    raise NotImplementedError("throw_ender_pearl: a shell")

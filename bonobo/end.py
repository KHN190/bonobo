"""The stronghold and the End: fill the end portal frame with eyes of ender and go through; the dragon fight is in
combat.py. Pure `frames_missing_eye` / `portal_centre` are offline-tested."""
import math
import time

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import api, nav, skillcore
from .api import McError, NotAvailable, log
from .skill import skill
from .data import bare
from .world import Inventory, Region, away_from, cells_with, entities, find

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

@skill(gives=["state:end_portal_open"], remaining=_k.blocks_there("end_portal"), needs={"minecraft:ender_eye": 1}, speed={}, done=lambda c: not frames_missing_eye(_frame_region()) if find(["end_portal_frame"], 32, 1) else False,
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

@skill(gives=["state:portal_room_found"], remaining=_k.blocks_there("end_portal_frame"), needs={"tool:pickaxe:0": 1}, speed={}, done=lambda c: bool(find(["end_portal_frame"], ROOM_REACH, 1)), budget=900, stall=240,
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
                raise api.NavFailed(f"portal room at {pos} spotted but not reached")
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

PERCH_PHASES = {5, 6, 7}  # 3 is landing: running in then meets the head
# 5 is sitting while flaming (breath pours over the fountain); 6 and 7 are the safe halves of the perch
BOMB_PHASES = {6, 7}
combat_ENDERMAN = "minecraft:enderman"

def bombable(dragon):
    """Pure: the window is open — it sits, and it is not breathing right now."""
    return dragon is not None and dragon.get("phase") in BOMB_PHASES
# geometry lives in fight.toml only
_GEO = __import__("bonobo.fight_plan", fromlist=["fight_plan"]).CONFIG["geometry"]
EYE = 1.62

def choose_side(here, centre=(0, 0)):
    """Pure: the axis side of the portal the player is on (the head turns toward the player)."""
    dx, dz = here[0] - centre[0], here[2] - centre[1]
    if abs(dx) >= abs(dz):
        return (1 if dx >= 0 else -1, 0)
    return (0, 1 if dz >= 0 else -1)

def dragon_entry(near):
    """Pure: the dragon itself from /entities — body parts share its type but have no health and cannot be attacked."""
    return next((e for e in near if e["type"] == "minecraft:ender_dragon" and e.get("health") is not None), None)

def pillar_top(bedrock):
    """Pure: the standing height on the exit-portal pillar — one above the highest bedrock near the island centre."""
    tops = [b[1] for b in bedrock if abs(b[0]) <= 3 and abs(b[2]) <= 3]
    return max(tops) + 1 if tops else None

def find_pillar_top():
    hits = find(["bedrock"], radius=48, limit=80)
    return pillar_top([(h["x"], h["y"], h["z"]) for h in hits])

def perched(dragon, top=None, centre=(0, 0)):
    """Pure: perched = near the centre AND down at the pillar top (a hovering dragon is not perched)."""
    if dragon.get("phase") is not None:
        # landing (3) or sitting (5 flaming, 6 scanning, 7 attacking)
        return dragon["phase"] in PERCH_PHASES
    # the ~16-long body puts its position several blocks off the pillar while perched
    near = math.hypot(dragon["x"] - centre[0], dragon["z"] - centre[1]) <= 8
    return near and (top is None or dragon["y"] <= top + 6)

def _bed_item():
    from .data import GROUPS
    inv = Inventory()
    return next((b for b in GROUPS["bed"] if inv.count(b)), None)

PIT_R = _GEO["mouth_r"]
PIT_DEPTH = _GEO["pit_depth"]
WAIT_BAND = (16, 22)
PREP_MIN_R = 16      # never build anything closer than this while the dragon is down

def bed_cell(side, top, centre=(0, 0)):
    """Pure: the bed cell on the fountain bedrock, 2 out on `side`, right under the perched head (a bed further out misses the head)."""
    # one block above the bedrock: lifts the blast into the perched head's hitbox
    return (centre[0] + side[0] * 2, top + 1, centre[1] + side[1] * 2)

def breath_near(near, here, radius=8.0):
    """Pure: a breath cloud within `radius`: any one close means the floor around us burns."""
    return any(e["type"] == "minecraft:area_effect_cloud"
               and math.dist((e["x"], e["y"], e["z"]), here) <= radius for e in near)

def breath_escape(here, near, centre=(0, 0), run=10):
    """Pure: run straight away from the cloud along our axis, never toward "the most open cell" (that walks along the cloud)."""
    clouds = [(e["x"], e["y"], e["z"]) for e in near if e["type"] == "minecraft:area_effect_cloud"]
    if not clouds:
        return None
    cx = sum(c[0] for c in clouds) / len(clouds)
    cz = sum(c[2] for c in clouds) / len(clouds)
    x, _y, z = away_from(here, (cx, here[1], cz), run)
    return (round(x), here[1], round(z))

def in_pit(feet, pit_feet, floor_y=None):
    """Pure: same column, head under the floor; tolerant of standing one block off."""
    if (feet[0], feet[2]) != (pit_feet[0], pit_feet[2]):
        return False
    if floor_y is None:
        return abs(feet[1] - pit_feet[1]) <= 1
    return feet[1] + EYE < floor_y

def prep_safe(dragon, here, centre=(0, 0), floor_y=None):
    """Pure: may we stand in the open now? Only while the dragon is not perched, or we are far outside its breath."""
    if dragon is None:
        return True
    r = math.hypot(here[0] - centre[0], here[2] - centre[1])
    return not perched(dragon, floor_y, centre) or r >= PREP_MIN_R

def exit_portal_open(centre=(0, 0)):
    """The exit portal's blocks appear only when the dragon dies: the one truth for "it's over" (out of range is not dead)."""
    return bool(find(["end_portal"], radius=32, limit=1))

def dragon_dead(near=None, centre=(0, 0)):
    """Pure-ish: the dragon is gone AND the exit portal is open (or its health reached 0)."""
    d = dragon_entry(near if near is not None else entities(128))
    if d is not None and d["health"] > 0:          # 0 is dead (`or 1` read a dead dragon's 0 as alive)
        return False
    return exit_portal_open(centre)

# needs: none — end stone breaks by hand
@skill(gives=["state:in_pit"], remaining=_k.walled_sides, needs={}, speed={}, budget=240, stall=90, soft=True)
def build_bed_pit(ctx):
    """Dig the 1×2 pit beside the exit portal and stand in it before the dragon lands."""
    from .skillcore import place
    from .world import Region
    if api.get("/state")["dimension"] != "minecraft:the_end":
        raise NotAvailable("not in the End")
    top = find_pillar_top()
    if top is None:
        raise NotAvailable("no exit portal pillar in range")
    s = api.get("/state")
    # only while it flies: the breath covers ~6 blocks around
    from .combat import station
    for _ in range(40):
        d = dragon_entry(entities(128))
        if prep_safe(d, (s["x"], s["y"], s["z"]), floor_y=top):
            break
        log("   dragon is perched: waiting out of its reach before digging the pit")
        for _ in station.__wrapped__(ctx, (0, 0), band=WAIT_BAND, rounds=8):
            pass
        s = api.get("/state")
        yield ("wait", round(s["health"]))
    side = choose_side((s["x"], s["y"], s["z"]))
    # one height for everything: the bombing hole on the bed's plane (y = top) or the bed is out of reach; imported here, a top import is circular
    from . import bunker
    fy = top
    _m = bunker.mouth(side, top)
    column = _floor_under(_m[0], _m[2], top)
    if column is not None and abs(column - top) <= 2:
        fy = column          # the bedrock apron right beside the fountain, when it sits a block or two off
    # the bunker: its firing cell is 4.11 blocks from the bed's top against a 4.5 reach, so every bomb is clicked from cover
    pit_feet = bunker.mouth(side, fy)
    fire_cell = bunker.fire(side, fy)
    retreat_cell = bunker.retreat(side, fy)
    bed = bed_cell(side, top)
    PIT[:] = [pit_feet, fire_cell, retreat_cell, bed, fy]
    log(f"   bunker: mouth {pit_feet}, fire {fire_cell}, retreat {retreat_cell}, bed {bed}, floor {fy}")
    # stand on the rim first: nothing stands next to a hole not dug yet
    if not nav.arrived((pit_feet[0], fy, pit_feet[2]), ctx.policy, range_=0.8, attempts=2, min_hp=15):
        raise api.NavFailed(f"the hole's rim at {(pit_feet[0], fy, pit_feet[2])} is not reachable")
    # place the bed from the rim: from inside the pit it is out of reach
    try:
        item = _bed_item()
        if item and not Region(bed, bed).solid(bed):
            # the bed sits on a block over the bedrock: obsidian if carried (blast-proof), else any block, else the bedrock itself
            under = (bed[0], bed[1] - 1, bed[2])
            if not Region(under, under).solid(under):
                filler = "minecraft:obsidian" if Inventory().count("minecraft:obsidian") else _building_block()
                try:
                    place(filler, under)
                    log(f"   {bare(filler)} under the bed at {under}")
                except McError as e:
                    log(f"   nothing under the bed ({e}): bedding the bedrock cell instead")
                    bed = under
                    PIT[2] = bed
            place(item, bed)
            log(f"   bed placed at {bed} ahead of the fight")
    except (McError, api.NavFailed) as e:
        log(f"   bed not pre-placed ({e}): the window will place it itself")
    # not while it perches over us, not hurt
    s = api.get("/state")
    if _soft_interrupt() or not prep_safe(dragon_entry(entities(128)), (s["x"], s["y"], s["z"]), floor_y=fy) \
            or s["health"] < 15:
        log("   not safe to start digging: backing off")
        dx, dz = side
        nav.arrived((dx * PREP_MIN_R, fy, dz * PREP_MIN_R), ctx.policy, range_=2, attempts=1)
        raise NotAvailable("not safe to dig the pit now")
    # the whole bunker as one submission, each cell within reach of the last; the world's result is judged below
    cells = [(pit_feet[0], y, pit_feet[2]) for y in range(pit_feet[1], fy)] + bunker.dig_plan(side, fy)
    lo = tuple(min(c[i] for c in cells) - 1 for i in range(3))
    hi = tuple(max(c[i] for c in cells) + 1 for i in range(3))
    results = api.run_chain(bunker.dig_batch(side, fy, Region(lo, hi).solid), stop_on_failure=True, wait=60)
    failed = next((r for r in results if r.get("status") != "succeeded"), None)
    if failed is not None:
        log(f"   the bunker stops at {failed.get('type')}: {failed.get('message')}")
    yield ("dug", len(results))
    # obsidian around the mouth: the blast eats end stone and the roof must survive every window
    if Inventory().count("minecraft:obsidian"):
        for cell in bunker.reinforce_cells(side, fy):
            if Region(cell, cell).solid(cell):
                continue
            try:
                place("minecraft:obsidian", cell)
            except McError as e:
                log(f"   mouth not reinforced at {cell}: {e}")
                break
    # dig the hole and set the bed while it still flies; end inside the hole, not on the rim
    s = api.get("/state")
    if not in_pit((s["blockX"], s["blockY"], s["blockZ"]), pit_feet, fy):
        nav.arrived(pit_feet, ctx.policy, range_=0.6, attempts=2, min_hp=15)
    return True

PIT = []      # [mouth, firing cell, retreat cell, bed, floor y] from the last build_bed_pit

def _soft_interrupt():
    """Take perception's interrupt without raising: a fight answers danger by retreating to the pit and trying again."""
    return api.consume_interrupt()     # one reader/clearer for the message channel, next to its one writer

def _recover(ctx, reason):
    """Carry out the recovery table's answer for an interrupt: one lookup, one action, always an answer."""
    from . import arbiter, recovery
    act, why = recovery.explain(reason)
    log(f"   {reason} → {act} ({why})")
    # a recovery is the safety layer: preempt so it owns the body and drops anything slower
    arbiter.BODY.preempt("safety", lambda: _recover_body(ctx, act), f"{act}: {reason}")
    return act

def _recover_body(ctx, act):
    if act == "shake_enderman":
        shake_enderman(ctx)
    elif act == "water_clutch":
        api.run({"type": "wait", "ticks": 5}, wait=5, awaits="the fall's landing (onGround) before the next recovery act")     # mid-air: the mod pours water under us
    elif act == "retreat_and_eat":
        _retreat(ctx)
        from . import skills
        try:
            skills.eat(raw_ok=True)
        except McError:
            pass
    else:                                                 # retreat_to_cover, and every unrecognised danger
        _retreat(ctx)

@skill(gives=["state:dragon_perched"], remaining=_k.dragon_phase(PERCH_PHASES), needs={}, speed={}, budget=180, stall=120, soft=True)
def await_perch(ctx):
    """Sit in the pit until the dragon is perched (DragonPhase) and health can take the bed's own blast."""
    if not PIT:
        raise NotAvailable("no bed pit built yet")
    pit_feet, fire_cell, retreat_cell, bed, floor_y = PIT
    from .combat import angry_endermen, station
    from .combat_tape import EventStream
    last_hp, last_feet, bleeding = None, None, 0
    # wait on the event stream: a quiet circle costs nothing and a landing is known the tick it happens
    stream = EventStream()
    waiting = False

    def _pit_is_clear(near_):
        """Nothing hostile is standing in the hole we are about to jump into."""
        return not [e for e in near_ if e.get("hostile") or e["type"] == combat_ENDERMAN
                    if math.dist((e["x"], e["y"], e["z"]), pit_feet) <= 2.5]

    for _ in range(400):
        if waiting:
            # parked on the stream: a phase change or a hit wakes it; a timeout re-checks
            stream.poll(timeout_ms=1500)
            waiting = False
        s = api.get("/state")
        feet = (s["blockX"], s["blockY"], s["blockZ"])
        here_p = (s["x"], s["y"], s["z"])
        near = entities(128)
        # the hole is no safe room: an enderman is 2.9 tall and teleports — never climb back in with one
        if angry_endermen(near, here_p, 4.0):
            shake_enderman(ctx)
            yield ("enderman", round(s["health"]))
            continue
        why = _soft_interrupt()
        if why:
            # the table answers every danger; only an occupied corridor is ours to notice
            if not _pit_is_clear(near):
                log(f"   {why}: the corridor is occupied, keeping clear instead")
                for _ in station.__wrapped__(ctx, (0, 0), band=WAIT_BAND, rounds=4):
                    pass
            else:
                _recover(ctx, why)      # await_perch has no arbiter of its own; it is already a waiting loop
            yield (why, round(s["health"]))
            continue
        if not s.get("onGround") and s["y"] > floor_y + 3:
            # flung: pathing mid-air does nothing; hold still for the mod's WaterClutch
            api.run({"type": "wait", "ticks": 5}, wait=5, awaits="on the ground again after the fling (the mod's water clutch)")
            yield ("airborne", round(s["y"]))
            continue
        if not in_pit(feet, pit_feet, floor_y):
            d = dragon_entry(entities(128))
            if not prep_safe(d, (s["x"], s["y"], s["z"]), floor_y=floor_y):
                # walking back in during a perch killed a run: wait it out far away
                for _ in station.__wrapped__(ctx, (0, 0), band=WAIT_BAND, rounds=8):
                    pass
                yield ("out of reach", round(s["health"]))
                continue
            nav.arrived(pit_feet, ctx.policy, range_=0.6, attempts=1, min_hp=15)
            yield ("to pit", round(s["health"]))
            continue
        near = entities(128)
        if dragon_dead(near):
            return True
        d = dragon_entry(near)
        # perched (6/7), full enough for the blast, no breath where we stand
        if (bombable(d) and s["health"] >= 19
                and not breath_near(near, fire_cell, 8.0)):
            return True
        if breath_near(near, (s["x"], s["y"], s["z"]), 6.0):
            away = breath_escape((s["x"], s["y"], s["z"]), near)
            if away is not None:
                log("   breath on us: running straight out of it")
                nav.arrived(away, ctx.policy, range_=1.5, attempts=1)
                yield ("breath", round(s["health"]))
                continue
        # losing health standing still: whatever hurts us reaches here — leave
        if last_hp is not None and s["health"] < last_hp and feet == last_feet:
            bleeding += 1
        else:
            bleeding = 0
        last_hp, last_feet = s["health"], feet
        if bleeding >= 2:
            log(f"   losing health at {feet} without moving: getting out")
            bleeding = 0
            for _ in station.__wrapped__(ctx, (0, 0), band=WAIT_BAND, rounds=6):
                pass
            yield ("bleeding", round(s["health"]))
            continue
        if s["health"] < 19 and s.get("food", 20) < 20:
            # only when a bite helps: at a full bar eat fails its own verify
            from . import skills
            try:
                skills.eat(raw_ok=True)
            except McError as e:
                log(f"   no bite ({e})")
        # wait on the stream, not a timer: a timed wait sleeps through a landing
        waiting = True
        yield (d.get("phase") if d else None, round(s["health"]))
    raise McError("the dragon never perched")

@skill(gives=["state:window_used"], remaining=_k.window_over(BOMB_PHASES), needs={"bed": 1}, speed={}, budget=120, stall=60, soft=True)
def bed_bomb_window(ctx):
    """One bomb, then straight back into the pit."""
    if not PIT:
        raise NotAvailable("no bed pit built yet")
    pit_feet, stand, retreat_cell, bed, floor_y = PIT
    item = _bed_item()
    if item is None:
        raise NotAvailable("out of beds (melee is fight_loop.dragon_answer)")
    # check the window before stepping into reach: flung far away, the pillar is out of range
    if find_pillar_top() is None:
        raise NotAvailable("no exit portal pillar in range")
    near = entities(128)
    d = dragon_entry(near)
    if not bombable(d):
        raise NotAvailable(f"no window: phase {(d or {}).get('phase')} (5 is sitting and breathing)")
    if breath_near(near, stand, 6.0):
        raise NotAvailable("breath on the bombing spot")
    if api.get("/state")["health"] < 19:
        raise NotAvailable("too hurt to take the blast")
    # the window is no time to find the pit unfinished
    from .world import Region
    dug = Region((pit_feet[0], pit_feet[1], pit_feet[2]), (pit_feet[0], floor_y - 1, pit_feet[2]))
    if any(dug.solid((pit_feet[0], y, pit_feet[2])) for y in range(pit_feet[1], floor_y)):
        raise NotAvailable(f"the pit at {pit_feet} isn't dug out")
    # into the hole for this one window only, and back to the pit whatever happens
    if not nav.arrived(stand, ctx.policy, range_=0.8, attempts=1, min_hp=15):
        nav.arrived(pit_feet, ctx.policy, range_=0.6, attempts=1)
        raise api.NavFailed(f"bombing hole {stand} not reached")
    before = dragon_entry(entities(128))
    from .world import Region as _R
    # the window as one submission: a round trip (~98 ms) is a quarter of the shortest window (0.4 s), so it runs open-loop
    from . import fight_loop
    window = fight_loop.batch(fight_loop.Answer("bed_bomb", (tuple(bed), item, tuple(stand), tuple(retreat_cell),
                                                             _R(bed, bed).solid(bed))), None)
    try:
        # one detonation and a three-block walk: longer means gone wrong, and more time outside
        results = api.run_chain(window, wait=6)
        r = results[1] if len(results) > 1 else {"status": "failed", "message": "no result"}
    except api.Interrupted as e:
        # soft: the window is off, the fight isn't
        _recover(ctx, _soft_interrupt() or str(e))
        raise NotAvailable(f"window cut short ({e})")
    if r["status"] != "succeeded":
        if "unknown task" in (r.get("message") or "").lower():
            raise NotAvailable("the mod has no bed_bomb task (needs ≥0.1.31)")
        log(f"   bed bomb failed: {r['message']}")
    api.run({"type": "wait", "ticks": 10}, wait=5, awaits="the dragon's health and phase after the bomb decide a follow-up")
    # place, pop, place, pop: each blast lifts the dragon and the next bed catches it (4–5 per landing)
    for _ in range(2):
        d_now = dragon_entry(entities(128))
        if not bombable(d_now) or api.get("/state")["health"] < 19 or not _bed_item():
            break
        again = api.run({"type": "bed_bomb", "x": bed[0], "y": bed[1], "z": bed[2], "item": _bed_item()}, wait=8, awaits="each follow-up bomb waits on the dragon still perched and bombable")
        log(f"   follow-up bomb: {again['status']} ({(d_now or {}).get('health')} hp before)")
        api.run({"type": "wait", "ticks": 10}, wait=5, awaits="the dragon's phase after this bomb")
    after = dragon_entry(entities(128))
    lost = round((before or {}).get("health", 0) - (after or {}).get("health", 0))
    log(f"   bed bomb: dragon {before.get('health') if before else None} → "
        f"{after.get('health') if after else None} hp (-{lost}), player hp {api.get('/state')['health']}")
    if lost < 20:
        # a weak blast means the bed's cell is wrong, a missed window the timing
        log(f"   ?? window took only {lost} hp — the bed sits wrong, not the timing")
    yield round((after or {}).get("health", 0))
    return True

CAGE_STAND_R = 2      # melee reach is ~3 blocks and the bars eat one: at 3 the attack task got 0 hits in 10 s

def cage_plan(crystal, here, floor_y):
    """Pure: (tower base, stand cell, bars) for a caged crystal: tower up on our side, break the bars, hit it from a water source (the water takes the blast)."""
    dx, dz = here[0] - crystal[0], here[2] - crystal[2]
    n = math.hypot(dx, dz) or 1.0
    ux, uz = dx / n, dz / n
    cy = math.floor(crystal[1])
    base = (math.floor(crystal[0] + ux * CAGE_STAND_R), floor_y, math.floor(crystal[2] + uz * CAGE_STAND_R))
    stand = (base[0], cy, base[2])
    # every bar around it: the cage is a ring and a corner post can block the hit
    bars = [(math.floor(crystal[0]) + bx, cy + dy, math.floor(crystal[2]) + bz)
            for bx, bz in ((1, 0), (-1, 0), (0, 1), (0, -1)) for dy in (0, 1)]
    return base, stand, bars

def crystal_commands(state, args):
    """`commands` for break_caged_crystal from the tower base: pillar up, break every bar still standing, in one chain."""
    from .data import GROUPS
    crystal = args[0]
    pos = (crystal["x"], crystal["y"], crystal["z"])
    feet = tuple(state["feet"])
    _base, stand, bars = cage_plan(pos, feet, feet[1])
    block = next((b for b in GROUPS["building"] if state["inv"].count(b)), None)
    rise = max(0, stand[1] - feet[1])
    if rise and block is None:
        raise NotAvailable("no blocks to tower up with")
    region = state.get("region")
    banned = state.get("banned", ())
    standing = [c for c in bars if (region is None or region.solid(c)) and c not in banned]
    return [{"type": "pillar", "item": block} for _ in range(rise)] + \
        [{"type": "mine", "x": c[0], "y": c[1], "z": c[2], "collect": False} for c in standing]

def unreachable(results):
    """Pure: the cells a chain's results name as "cannot reach x, y, z" — the only ones a partial failure bans."""
    from .data import cannot_reach
    return {c for t in results if t["status"] != "succeeded" for c in cannot_reach(t.get("message"))}

def caged(crystal, here):
    """Pure: the crystal sits on a tall caged pillar (the open ones are at island level and can be hit from there)."""
    return crystal[1] - here[1] > 8

@skill(gives=["state:enderman_off"], remaining=_k.none_of("minecraft:enderman", within=8.0), needs={}, speed={}, budget=90, stall=45, soft=True)
def shake_enderman(ctx):
    """Shake an angry enderman without fighting: the pit (they can't follow), else water, else walk away — never look at it (looking provokes)."""
    from .skillcore import place
    from .combat import angry_endermen
    from .world import entities as _entities
    s = api.get("/state")
    here = (s["x"], s["y"], s["z"])
    angry = angry_endermen(_entities(32), here, 16.0)
    if not angry:
        return True
    # water first: one click, works where we stand and teleports them away; walking to a half-dug hole puts us under the dragon
    if Inventory().count("minecraft:water_bucket"):
        feet = skillcore.feet()
        try:
            place("minecraft:water_bucket", feet)
            api.run({"type": "wait", "ticks": 20}, wait=5, awaits="whether the enderman lets go while we stand in water")
            yield "water"
            return True
        except McError as e:
            log(f"   no water placed ({e}): trying the hole instead")
    # the hole helps only when at our feet: running to it through endermen killed this twice
    if PIT and math.dist(here, PIT[0]) <= 6:
        pit_feet, fire_cell, retreat_cell, bed, floor_y = PIT
        from .world import Region as _R
        dug = not any(_R((pit_feet[0], y, pit_feet[2]), (pit_feet[0], y, pit_feet[2])).solid((pit_feet[0], y, pit_feet[2]))
                      for y in range(pit_feet[1], floor_y))
        if dug and nav.arrived(pit_feet, ctx.policy, range_=0.6, attempts=1):
            api.run({"type": "wait", "ticks": 20}, wait=5, awaits="whether the enderman lets go while we sit in the pit")
            yield "pit"
            return True
    # Away from the enderman AND away from the island centre: never shake one off by walking under the dragon.
    e = angry[0]
    dx, dz = here[0] - e["x"], here[2] - e["z"]
    n = math.hypot(dx, dz) or 1.0
    cx, cz = here[0], here[2]
    c = math.hypot(cx, cz) or 1.0
    ux, uz = dx / n + cx / c, dz / n + cz / c
    m = math.hypot(ux, uz) or 1.0
    away = (round(here[0] + ux / m * 12), round(here[1]), round(here[2] + uz / m * 12))
    # a short plain goto, not travel: chased, 10 s of "no progress" is far too slow
    api.run({"type": "goto", "x": away[0], "y": away[1], "z": away[2], "range": 2, "partial": True}, wait=3, awaits="one short leg, then the chase is read again (a lone task, nothing to chain)")
    yield "away"
    return True

@skill(gives=["state:crystal_broken"], remaining=_k.entity_gone(lambda c: c.args[1]["id"]), needs={"building": 1}, speed={}, budget=300, stall=120, soft=True,
       commands=lambda state, args: crystal_commands(state, args))
def break_caged_crystal(ctx, crystal):
    """Tower up to a caged crystal, break the bars, stand in water and hit it. `crystal` is an /entities row."""
    from .skillcore import place
    from .world import Region
    inv = Inventory()
    block = _building_block()
    if not inv.count(block):
        raise NotAvailable("no blocks to tower up with")
    here = skillcore.feet()
    pos = (crystal["x"], crystal["y"], crystal["z"])
    # the tower's own column, not the crystal's (that lands in the pillar or mid-air)
    base, stand, bars = cage_plan(pos, here, round(here[1]))
    floor = _floor_under(base[0], base[2], round(here[1]))
    if floor is None:
        raise NotAvailable(f"no ground under the tower base {base}")
    base, stand, bars = cage_plan(pos, here, floor)
    log(f"   caged crystal at {tuple(round(c) for c in pos)}: tower at {base} up to {stand}")
    if not nav.arrived(base, ctx.policy, range_=1.0, attempts=2):
        raise api.NavFailed(f"tower base {base} not reachable")
    if not api.get("/state").get("onGround"):
        # pillaring needs something under the feet
        api.run({"type": "wait", "ticks": 10}, wait=5, awaits="the dragon's next reading decides the next move")
    # Up and through the cage in one chain (crystal_commands): the tower's height and the bars are known here.
    from .skillcore import body_state
    lo = tuple(min(c[i] for c in bars) for i in range(3))
    hi = tuple(max(c[i] for c in bars) for i in range(3))
    banned = {c for c, until in ctx.blacklist.items() if until > time.time()}
    tasks = crystal_commands(body_state(ctx, Region(lo, hi), banned=banned), (crystal,))
    done = api.run_chain(tasks, stop_on_failure=True)
    # only the bars the mod could not reach are banned; an interrupt raises before this and bans nothing
    for cell in unreachable(done):
        ctx.ban(cell)
    # judged by the world, not the chain's word
    if skillcore.feet()[1] < stand[1]:
        raise McError(f"towering up failed: at y {skillcore.feet()[1]}, the crystal's height is {stand[1]}")
    yield skillcore.feet()[1]
    if inv.count("minecraft:water_bucket"):
        # stand in water when it goes off: the blast is power 6 and we wear no armor
        try:
            place("minecraft:water_bucket", skillcore.feet())
        except McError as e:
            log(f"   no water under our feet ({e}): hitting the crystal anyway")
    for _ in range(3):
        try:
            hit = {"type": "attack", "entity": crystal["id"]}
            hit = api.ARM([hit], inv=inv, read_blocks=False)[0] if api.ARM else hit      # the bag this fight holds
            api.run(hit, wait=6, awaits="the crystal gone (entity list) after each hit")
        except api.TaskStuck:
            pass                     # out of reach for a moment: step closer and try again
        api.run({"type": "wait", "ticks": 10}, wait=5, awaits="the dragon's next reading decides the next move")
        if not [e for e in entities(128) if e["id"] == crystal["id"]]:
            return True
        nav.arrived((stand[0], stand[1], stand[2]), ctx.policy, range_=0.8, attempts=1)
        yield "hitting"
    raise McError("the crystal survived the hits")

def fight_state(near, s, ctx):
    """The planner's view of one perception round — never summarised: threat rows go through whole."""
    from . import fight_plan
    d = dragon_entry(near)
    inv = Inventory()
    here = (s["x"], s["y"], s["z"])
    phase = (d or {}).get("phase")
    elapsed = time.time() - PHASE_SINCE[1] if PHASE_SINCE[0] == phase else 0.0
    # an unstarted clock reports decades; believing it refuses every action
    if not 0.0 <= elapsed <= 300.0:
        elapsed = 0.0
    return fight_plan.fight_state(
        self_={"pos": here, "hp": s["health"], "cover": PIT[2] if PIT else None,
               "in_cover": bool(PIT) and math.dist(here, PIT[2]) <= 1.5},
        boss={"phase": phase if phase is not None else 0, "phase_elapsed_s": round(elapsed, 2),
              "hp": (d or {}).get("health") or 0.0},
        threats=_threats(near, d),
        resources={"beds": inv.count("bed"), "obsidian": inv.count("minecraft:obsidian"),
                   "water": bool(inv.count("minecraft:water_bucket")),
                   "bow": inv.count("minecraft:bow"), "arrows": inv.count("minecraft:arrow")},
        terrain={"tunnel_ready": bool(PIT), "bed_placed": bool(PIT) and _solid(PIT[3]),
                 "reinforced": _reinforced(PIT),
                 "crystals_open": len([e for e in near if e["type"] == "minecraft:end_crystal"
                                       and not caged((e["x"], e["y"], e["z"]), here)])},
    )

def _reinforced(pit):
    """Pure-ish: is the bunker's mouth already blast-proofed?"""
    if not pit:
        return False
    from . import bunker
    side = (1 if pit[0][0] > 0 else (-1 if pit[0][0] < 0 else 0), 0)
    if side == (0, 0):
        side = (0, 1 if pit[0][2] > 0 else -1)
    cells = bunker.reinforce_cells(side, pit[4])
    return all(_solid(c) for c in cells)

_LAST_SEEN = {}          # entity id -> (position, when), for differencing velocity between rounds

def _threats(near, dragon, now=None):
    """The round as (centre, radius, velocity, kind) hazards; a new enemy needs only a radius in combat_model.HAZARD_R."""
    from . import combat_model, threat
    now = time.time() if now is None else now
    live = [e for e in near or [] if not (e.get("type") == combat_ENDERMAN and not e.get("angry"))]
    return threat.rows(live, _LAST_SEEN, now, combat_model.HAZARD_R)

def _solid(cell):
    from .world import Region
    return Region(cell, cell).solid(cell)

def dragon_view(near, s):
    """What fight_loop.dragon_answer reads off one perception round: dragon, crystals, bed, a takeable bomb window, cells to proof, breath escape, cover."""
    from . import bunker
    from .combat import crystal_order
    d = dragon_entry(near)
    here = (s["x"], s["y"], s["z"])
    item = _bed_item()
    bomb, reinforce = None, []
    if PIT:
        pit_feet, stand, retreat_cell, bed, floor_y = PIT
        if item and bombable(d) and not breath_near(near, stand, 6.0) and s["health"] >= 19:
            bomb = (tuple(bed), item, tuple(stand), tuple(retreat_cell), _solid(bed))
        side = choose_side((s["x"], 0, s["z"]))
        reinforce = [c for c in bunker.reinforce_cells(side, floor_y) if not _solid(c)]
    return {"dead": dragon_dead(near), "dragon": d, "here": here, "bed": item,
            "crystals": crystal_order([e for e in near if e["type"] == "minecraft:end_crystal"
                                       and not caged((e["x"], e["y"], e["z"]), here)], here),
            "bed_cell": tuple(PIT[3]) if PIT else None, "bomb": bomb, "reinforce": reinforce,
            "escape": breath_escape(here, near) if breath_near(near, here) else None,
            "cover": tuple(PIT[2]) if PIT else None}

def _retreat(ctx, near=None):
    """Get away from whatever hurts us; always does something (a no-op default turns every upstream bug into a silent death)."""
    if PIT:
        nav.arrived(PIT[2], ctx.policy, range_=0.6, attempts=1)
        return "corridor"
    s = api.get("/state")
    here = (s["x"], s["y"], s["z"])
    near = entities(128) if near is None else near
    # no cover: put distance between us and the nearest hazard
    from . import combat_model
    hazards = _threats(near, dragon_entry(near))
    if hazards:
        # the same rule the safety veto applies, so a retreat never goes where the veto refuses
        away, slack = combat_model.best_step(here, hazards, cover=PIT[2] if PIT else None)
        if away is not None:
            log(f"   no cover to retreat into: backing off toward "
                f"{tuple(round(c, 1) for c in away)} (slack {slack}s)")
            nav.go_to(away, ctx.policy, range_=2, attempts=1, min_hp=0)
            return "away"
    api.run({"type": "wait", "ticks": 10}, wait=5, awaits="the next threat reading decides the next move")         # nothing nearby: waiting really is the answer
    return "clear"

_ASSUMPTIONS_LOGGED = []      # say it once per fight, not every round
LAST_ROUND = [None, None]     # (state, intent) of the latest planning round, for incident capture
PHASE_SINCE = [None, time.time()]  # (phase, start) — never 0.0: the epoch's elapsed time vetoes every action

def _track_phase(near):
    d = dragon_entry(near)
    phase = (d or {}).get("phase")
    if phase != PHASE_SINCE[0]:
        PHASE_SINCE[:] = [phase, time.time()]
    return phase

DRAGON_PASSES = 2400  # ≈0.5 s each while waiting

# needs: none — beds do the damage; fists hit when no sword
@skill(gives=["state:dragon_dead"], remaining=_k.none_of("minecraft:ender_dragon", within=512.0), needs={}, speed={}, budget=1800, stall=300, soft=True)
def slay_dragon(ctx):
    """Hold the body while fight_loop carries the dragon fight; runs the two preparations and says when it is over."""
    if api.get("/state")["dimension"] != "minecraft:the_end":
        raise NotAvailable("not in the End")
    from . import arbiter, fight_loop, fight_plan
    motion = arbiter.BODY  # perception preempts on it from its own thread
    motion.engage(log=log)
    fight = fight_plan.Fight()
    held = {"done": None, "task_id": None}
    passes = [0]
    prep = {"dig_tunnel": build_bed_pit, "water_bucket": shake_enderman}

    def want():
        passes[0] += 1
        near = entities(128)
        _track_phase(near)
        s = api.get("/state")
        try:
            state = fight_state(near, s, ctx)
            intent = fight.plan(state)
        except Exception as e:                            # a planner fault must never leave us standing in the open
            log(f"   planner failed ({e}): retreating")
            state, intent = {}, {"intent": "retreat", "benefit_s": 0.0, "deadline_s": 0.0}
        LAST_ROUND[:] = [state, intent]                   # what the bench dumps as an incident when this run dies
        _say(intent, state)
        return fight_loop.dragon_answer(intent, dragon_view(near, s))

    def answer(a):
        if a.kind == "prep":
            prep[a.target](ctx)                           # a preparation runs whole, then the loop reads again
            return None
        return fight_loop.engage(a, api.get("/state"), ctx)

    try:
        for kind in fight_loop.carry(want, answer, lambda: passes[0] < DRAGON_PASSES, held, again=True):
            yield kind
        if passes[0] >= DRAGON_PASSES:
            raise McError("the dragon fight ran out of rounds")
        ctx.mem.data["dragon_defeated"] = True
        ctx.mem.save()
        log("the exit portal is open — the dragon is dead")
        return True
    finally:
        if held["task_id"] is not None:
            api.post("/stop")
        motion.disengage()

def _say(intent, state):
    """The round in the log: the plan, and loudly a fault or the unmeasured numbers it rests on."""
    if intent.get("assumptions") and not _ASSUMPTIONS_LOGGED:
        log(f"   planning on unmeasured parameters: {', '.join(intent['assumptions'])} "
            f"(run `mc.py report --fit` after a recorded fight)")
        _ASSUMPTIONS_LOGGED.append(True)
    if intent.get("fault"):
        # loud: a malformed state, or every productive action refused
        log(f"?? planner fault: {intent['fault']}")
        log(f"   refused: {intent.get('rejected')}")
    boss = state.get("boss", {})
    if intent.get("intent") != LAST_SAID[0]:
        LAST_SAID[0] = intent.get("intent")
        log(f"   plan: {intent['intent']} (worth {intent.get('benefit_s')}s, phase {boss.get('phase')}, "
            f"{boss.get('hp', 0):.0f} hp left)")

LAST_SAID = [None]

def _floor_under(x, z, y_hint):
    """The standing height at column (x, z): one above the highest solid block from y_hint+3 down 12 blocks."""
    from .world import Region
    r = Region((x, y_hint - 12, z), (x, y_hint + 3, z))
    for y in range(y_hint + 3, y_hint - 13, -1):
        if r.solid((x, y, z)):
            return y + 1
    return None

def _building_block():
    from .data import GROUPS
    inv = Inventory()
    return next((b for b in GROUPS["building"] if inv.count(b)), "minecraft:cobblestone")

def _frame_region():
    hits = find(["end_portal_frame"], radius=32, limit=12)
    if not hits:
        return Region((0, 0, 0), (0, 0, 0), props=True)
    xs, ys, zs = [h["x"] for h in hits], [h["y"] for h in hits], [h["z"] for h in hits]
    return Region((min(xs) - 2, min(ys), min(zs) - 2), (max(xs) + 2, max(ys), max(zs) + 2), props=True)

@skill(gives=["state:in_the_end"], remaining=_k.in_dimension(lambda c: "minecraft:the_end"), needs={}, speed={}, done=lambda c: api.get("/state")["dimension"] == "minecraft:the_end", budget=120, stall=60)
def enter_end(ctx):
    """Jump into the activated end portal (the centre of the frame ring)."""
    centre = portal_centre([(h["x"], h["y"], h["z"]) for h in find(["end_portal_frame"], radius=32, limit=12)])
    if centre is None:
        raise NotAvailable("no end portal nearby")
    if not find(["end_portal"], radius=32, limit=1):
        raise NotAvailable("the end portal isn't active yet")
    nav.arrived(centre, ctx.policy, range_=0.6, attempts=1)
    for _ in range(10):
        api.run({"type": "wait", "ticks": 20}, wait=5, awaits="the dimension change after stepping in")
        yield None
        if api.get("/state")["dimension"] == "minecraft:the_end":
            return True
    raise McError("stood in the end portal but didn't arrive in the End")

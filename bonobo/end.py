"""The stronghold and the End: fill the end portal frame with eyes of ender and go through; the dragon fight is in
combat.py. Pure `frames_missing_eye` / `portal_centre` are offline-tested."""
import math
import time

from . import api, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .data import bare
from .world import Inventory, Region, add, entities, find


def frames_missing_eye(region):
    """Pure: end portal frame blocks without an eye (block state eye=false)."""
    prop = getattr(region, "prop", None)
    return [p for p, n in region.blocks.items()
            if n == "end_portal_frame" and prop and str(prop(p, "eye")) != "true"]


def portal_centre(frames):
    """Pure: the centre of the 3×3 opening surrounded by the 12 frame blocks (x, y, z)."""
    if not frames:
        return None
    xs, zs = [p[0] for p in frames], [p[2] for p in frames]
    return (min(xs) + max(xs)) // 2, frames[0][1], (min(zs) + max(zs)) // 2


def ring_stops(frames, centre, here):
    """Pure: [(stand spot, [frames])] — one stop per side of the ring (outside its middle), sides in walking order
    around the ring starting with the one nearest `here`."""
    import math as _m
    sides = {}
    for f in frames:
        dx, dz = f[0] - centre[0], f[2] - centre[2]
        key = ("x", 1 if dx > 0 else -1) if abs(dx) >= abs(dz) else ("z", 1 if dz > 0 else -1)
        sides.setdefault(key, []).append(f)
    stops = []
    for (axis, sign), fs in sides.items():
        mid = (centre[0] + 2 * sign + sign, fs[0][1], centre[2]) if axis == "x" else \
              (centre[0], fs[0][1], centre[2] + 2 * sign + sign)
        stops.append((mid, sorted(fs)))
    if not stops:
        return []
    start = min(range(len(stops)), key=lambda i: _m.dist(stops[i][0], here))
    by_angle = sorted(stops, key=lambda s: _m.atan2(s[0][2] - centre[2], s[0][0] - centre[0]))
    i0 = by_angle.index(stops[start])
    return by_angle[i0:] + by_angle[:i0]


def outside_spot(frame, centre):
    """Pure: the floor cell just outside the ring next to a frame block (away from the 3×3 opening)."""
    dx, dz = frame[0] - centre[0], frame[2] - centre[2]
    if abs(dx) >= abs(dz):
        return frame[0] + (1 if dx > 0 else -1), frame[1], frame[2]
    return frame[0], frame[1], frame[2] + (1 if dz > 0 else -1)


@skill(done=lambda c: not frames_missing_eye(_frame_region()) if find(["end_portal_frame"], 32, 1) else False,
       budget=600, stall=180, per_unit=60)
def activate_end_portal(ctx):
    """At the stronghold's portal room: put an eye of ender into every empty frame block (click its top)."""
    hits = find(["end_portal_frame"], radius=32, limit=12)
    if not hits:
        raise NotAvailable("no end portal frame within 32 blocks (dig down at the stronghold estimate first)")
    region = _frame_region()
    missing = frames_missing_eye(region)
    if Inventory().count("minecraft:ender_eye") < len(missing):
        raise NotAvailable(f"need {len(missing)} eyes of ender, have {Inventory().count('minecraft:ender_eye')}")
    centre = portal_centre([(h["x"], h["y"], h["z"]) for h in hits])
    here = nav.feet_now()
    # One walk around the ring: a stop outside the middle of each side reaches that side's 3 frames. Placing them in
    # search order crossed the ring for every eye (bench 05:20, 79 s for 12 eyes).
    for stop, side in ring_stops(missing, centre, here):
        # Outside the ring, not on top of it: standing on a frame slid the player into the opening's lava once.
        if not nav.go_to(stop, ctx.policy, range_=0.8, attempts=1):
            raise api.NavFailed(f"the ring side at {stop} is not reachable")
        for f in side:
            r = api.run({"type": "use_item", "item": "minecraft:ender_eye", "x": f[0] + 0.5, "y": f[1] + 0.8125,
                         "z": f[2] + 0.5, "onBlock": True}, wait=20)
            if r["status"] != "succeeded":
                raise McError(f"placing an eye on {f} failed: {r['message']}")
            yield f
    if frames_missing_eye(_frame_region()):
        raise McError("some frames still have no eye")
    log("end portal activated")


BRICKS = ["minecraft:stone_bricks", "minecraft:mossy_stone_bricks", "minecraft:cracked_stone_bricks"]
ROOM_Y = 30          # strongholds sit around y 0–50; the portal room is usually within 30 blocks of the estimate


SCAN = 48            # the mod's /find maximum: every search point scans the widest box it can
# Points 40 apart: each one costs a 40–60 s tunnel at depth (bench: 24 apart with a 32 scan found nothing in 3 min).


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


@skill(done=lambda c: bool(find(["end_portal_frame"], ROOM_REACH, 1)), budget=900, stall=240, per_unit=300)
def find_portal_room(ctx):
    """From the triangulated estimate: dig down to stronghold depth, follow stronghold bricks toward unexplored parts,
    otherwise search rings around the estimate, until an end portal frame is within 32 blocks."""
    sites = ctx.mem.sites("minecraft:overworld", kinds=["stronghold"])
    if not sites:
        raise NotAvailable("no stronghold estimate yet (locate_stronghold first)")
    # The NEAREST estimate, not the first one ever remembered: with an old test stronghold still in memory the search
    # walked thousands of blocks back to it while the fresh one stood right here.
    _here = nav.feet_now()
    sx, _, sz = min(sites, key=lambda s: math.dist(s["pos"], _here))["pos"]
    visited = []
    points = search_points((sx, sz))
    for _ in range(len(points) + 12):
        hit = find(["end_portal_frame"], SCAN, 1)
        if hit:
            pos = (hit[0]["x"], hit[0]["y"], hit[0]["z"])
            ctx.mem.add_site("portal_room", pos, "minecraft:overworld", name="portal_room")
            log(f"end portal room found at {pos}")
            # Seeing it is not reaching it. The scan sees 48 blocks away, often 40 blocks *up* from the room, so
            # returning here meant "finished without reaching its goal" while standing on the surface. Dig down to
            # it (the policy allows digging) until the frames are within ROOM_REACH.
            for _ in range(4):
                if math.dist(nav.feet_now(), pos) <= ROOM_REACH:
                    return True
                nav.go_to(pos, ctx.policy, range_=ROOM_REACH - 4, attempts=1)
                yield nav.feet_now()
            if math.dist(nav.feet_now(), pos) > ROOM_REACH:
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
        nav.go_to(target, ctx.policy, range_=3, attempts=1)
        yield target
    raise NotAvailable("no end portal frame around the stronghold estimate")


PERCH_PHASES = {5, 6, 7}      # 3 is "landing": running in while it still comes down met the head on the way
# The attack window is narrower than "sitting": 5 is sitting *flaming*, i.e. the breath is pouring out over the
# fountain, so stepping in then means eating it. 6 (scanning) and 7 (attacking) are the safe halves of the perch.
BOMB_PHASES = {6, 7}
combat_ENDERMAN = "minecraft:enderman"


def bombable(dragon):
    """Pure: the window is open — it sits, and it is not breathing right now."""
    return dragon is not None and dragon.get("phase") in BOMB_PHASES
BED_R = 3     # blocks from the portal centre along the chosen side, for the bed
EYE = 1.62
REACH = 4.5
BED_TOP = 0.5625


def choose_side(here, centre=(0, 0)):
    """Pure: the axis side of the portal the player is on (the dragon's head turns toward the player)."""
    dx, dz = here[0] - centre[0], here[2] - centre[1]
    if abs(dx) >= abs(dz):
        return (1 if dx >= 0 else -1, 0)
    return (0, 1 if dz >= 0 else -1)


def dragon_entry(near):
    """Pure: the ender dragon itself from an /entities list. Its body parts share the type but carry no health, and
    their ids aren't in the client's entity table: attacking one returned "target not found" 100 times."""
    return next((e for e in near if e["type"] == "minecraft:ender_dragon" and e.get("health") is not None), None)


def pillar_top(bedrock):
    """Pure: the standing height on the exit-portal pillar — one above the highest bedrock near the island centre."""
    tops = [b[1] for b in bedrock if abs(b[0]) <= 3 and abs(b[2]) <= 3]
    return max(tops) + 1 if tops else None


def find_pillar_top():
    hits = find(["bedrock"], radius=48, limit=80)
    return pillar_top([(h["x"], h["y"], h["z"]) for h in hits])


def perched(dragon, top=None, centre=(0, 0)):
    """Pure: the dragon sits on the exit portal — near the island centre AND down at the pillar top (a dragon
    hovering above the centre counted as perched and the bed went into the air)."""
    if dragon.get("phase") is not None:
        # mod ≥0.1.31 reports the synced phase: landing (3) or sitting (5 flaming, 6 scanning, 7 attacking).
        return dragon["phase"] in PERCH_PHASES
    # The dragon's body is ~16 long: its entity position sits several blocks off the pillar centre while perched
    # (5 never matched on the bench: 43 s of "not perched" with the dragon 14 blocks from the player).
    near = math.hypot(dragon["x"] - centre[0], dragon["z"] - centre[1]) <= 8
    return near and (top is None or dragon["y"] <= top + 6)


BED_IDS = None


def _bed_item():
    from .data import GROUPS
    inv = Inventory()
    return next((b for b in GROUPS["bed"] if inv.count(b)), None)


PIT_R = 5
PIT_DEPTH = 2
BOMB_R = PIT_R
BOMB_DEPTH = PIT_DEPTH
WAIT_BAND = (16, 22)
PREP_MIN_R = 16      # never build anything closer than this while the dragon is down


def bed_cell(side, top, centre=(0, 0)):
    """Pure: where the bed goes — on the bedrock of the fountain, 2 from the centre on the chosen side, at the pillar
    top. The perched head hangs directly above it (mcmod 2197: 引爆时龙头在床的正上方; 4 beds suffice when every blast
    lands under the head). A bed on the island floor 3 blocks out left the head outside the blast and every window
    reported the dragon still at 200 hp."""
    # One block ABOVE the bedrock: human runners put an obsidian block on the highest bedrock of the fountain and
    # bed on top of it, which lifts the blast into the perched head's hitbox instead of under it.
    return (centre[0] + side[0] * 2, top + 1, centre[1] + side[1] * 2)


def breath_near(near, here, radius=8.0):
    """Pure: a dragon breath cloud within `radius`. The clouds spread along the floor as a string of entities, so any
    one of them close by means the floor around us is burning."""
    return any(e["type"] == "minecraft:area_effect_cloud"
               and math.dist((e["x"], e["y"], e["z"]), here) <= radius for e in near)


def breath_escape(here, near, centre=(0, 0), run=10):
    """Pure: where to run when the breath lands on us — straight away from the cloud along the axis we are on, never
    a search for "the most open cell" (that walked along the cloud)."""
    clouds = [(e["x"], e["y"], e["z"]) for e in near if e["type"] == "minecraft:area_effect_cloud"]
    if not clouds:
        return None
    cx = sum(c[0] for c in clouds) / len(clouds)
    cz = sum(c[2] for c in clouds) / len(clouds)
    dx, dz = here[0] - cx, here[2] - cz
    n = math.hypot(dx, dz) or 1.0
    return (round(here[0] + dx / n * run), here[1], round(here[2] + dz / n * run))


def in_pit(feet, pit_feet, floor_y=None):
    """Pure, with tolerance: the same column and low enough that the head is under the floor. Exact equality made the
    skill walk back into the open every time the player stood one block off."""
    if (feet[0], feet[2]) != (pit_feet[0], pit_feet[2]):
        return False
    if floor_y is None:
        return abs(feet[1] - pit_feet[1]) <= 1
    return feet[1] + EYE < floor_y


def prep_safe(dragon, here, centre=(0, 0), floor_y=None):
    """Pure: may we spend seconds standing in the open (digging the pit, placing the lip)? Only while the dragon is
    not perched, or while we are still far outside its breath. Walking to the pit during a perch took 20 hp to 0."""
    if dragon is None:
        return True
    r = math.hypot(here[0] - centre[0], here[2] - centre[1])
    return not perched(dragon, floor_y, centre) or r >= PREP_MIN_R


def exit_portal_open(centre=(0, 0)):
    """The exit portal's end_portal blocks only appear when the dragon dies: the one truth for "it's over". A dragon
    that flew out of entity range was declared dead once after 5 s of not being seen."""
    return bool(find(["end_portal"], radius=32, limit=1))


def dragon_dead(near=None, centre=(0, 0)):
    """Pure-ish: the dragon is gone AND the exit portal is open (or its health reached 0)."""
    d = dragon_entry(near if near is not None else entities(128))
    if d is not None and (d.get("health") or 1) > 0:
        return False
    return exit_portal_open(centre)


@skill(budget=240, stall=90, soft=True)
def build_bed_pit(ctx):
    """Prepare the fight before the dragon lands: dig the 1×2 pit beside the exit portal and stand in it. Speedruns
    do this while the dragon still circles; the old code placed its cover mid-fight and died waiting."""
    from .skillcore import place
    from .world import Region
    if api.get("/state")["dimension"] != "minecraft:the_end":
        raise NotAvailable("not in the End")
    top = find_pillar_top()
    if top is None:
        raise NotAvailable("no exit portal pillar in range")
    s = api.get("/state")
    # Only while it flies: digging takes seconds in the open and the breath covers everything within ~6 blocks.
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
    # ONE height for everything. The bed has to sit on the fountain's bedrock (y = top), so the hole we bomb from
    # must be on that same plane: deriving the hole from `_floor_under` put it on the island floor at y 59 while the
    # bed stayed at y 68 — nine blocks apart, out of reach, and the hole's rim wasn't even standable
    # ("no path found (1 positions explored)").
    # Imported here, not at the top: bunker reads this module's geometry constants, so a module-level import would
    # be circular.
    from . import bunker
    fy = top
    _m = bunker.mouth(side, top)
    column = _floor_under(_m[0], _m[2], top)
    if column is not None and abs(column - top) <= 2:
        fy = column          # the bedrock apron right beside the fountain, when it sits a block or two off
    # The bunker, not the old two-block pit: mouth at the rim, a firing cell one block in that still reaches the bed,
    # and a retreat cell deep enough that nothing the dragon does arrives there. Measured, not chosen — the firing
    # cell sits 4.11 blocks from the bed's top against a 4.5 reach, which is what lets every bomb be clicked from
    # inside cover. Sight is irrelevant: entity data is read directly, so reach is the only reason to be near.
    pit_feet = bunker.mouth(side, fy)
    fire_cell = bunker.fire(side, fy)
    retreat_cell = bunker.retreat(side, fy)
    bed = bed_cell(side, top)
    PIT[:] = [pit_feet, fire_cell, retreat_cell, bed, fy]
    log(f"   bunker: mouth {pit_feet}, fire {fire_cell}, retreat {retreat_cell}, bed {bed}, floor {fy}")
    # Stand on the rim first. Travelling straight to a cell two blocks underground answered "cannot reach
    # (16, 57, 0): no path found" — nothing stands next to a hole that doesn't exist yet.
    if not nav.go_to((pit_feet[0], fy, pit_feet[2]), ctx.policy, range_=0.8, attempts=2, min_hp=15):
        raise api.NavFailed(f"the hole's rim at {(pit_feet[0], fy, pit_feet[2])} is not reachable")
    # Place the bed while still standing on the rim: it sits on the fountain's bedrock, which is out of arm's reach
    # from two blocks under the floor. Do it before digging, not after.
    try:
        item = _bed_item()
        if item and not Region(bed, bed).solid(bed):
            # The bed sits one block above the bedrock now, so that block must exist first: obsidian when we carry
            # any (blast-proof, what human runners use), else any building block. If nothing can be placed, fall
            # back to the bedrock cell itself rather than dropping a bed into thin air.
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
    for _ in range(PIT_DEPTH * 4):
        s = api.get("/state")
        if s["blockY"] <= pit_feet[1]:
            break
        started = s["blockY"] < fy          # already one block down: the hole itself is now the best cover
        why = _soft_interrupt()
        if started:
            if why:
                log(f"   {why} mid-dig: finishing the hole, it is the cover")
        elif why or not prep_safe(dragon_entry(entities(128)), (s["x"], s["y"], s["z"]), floor_y=fy) \
                or s["health"] < 15:
            # Still on the surface with nowhere to hide: walk straight away from the portal and wait it out. Going
            # back and forth between "away" and "the rim" is what drained the health bar.
            log("   not safe to start digging: backing off")
            dx, dz = side
            nav.go_to((dx * PREP_MIN_R, fy, dz * PREP_MIN_R), ctx.policy, range_=2, attempts=1)
            api.run({"type": "wait", "ticks": 20}, wait=5)
            yield ("backed off", round(api.get("/state")["health"]))
            continue
        # Mine the block under our own feet, then step into it: always reachable, one block at a time. "Our own feet"
        # must mean the pit column, not wherever we happen to stand: arriving within 0.8 of the rim can leave us on
        # the neighbouring block, where the cell below is already air, so the mine is skipped, the step "arrives" in
        # 0.0 s without moving, and every round is spent at the same height (bench: 8 × "arrived (0.0s)", then
        # "hole not dug out").
        if (s["blockX"], s["blockZ"]) != (pit_feet[0], pit_feet[2]):
            nav.go_to((pit_feet[0], s["blockY"], pit_feet[2]), ctx.policy, range_=0.3, attempts=1)
            s = api.get("/state")
        cell = (pit_feet[0], s["blockY"] - 1, pit_feet[2])
        if Region(cell, cell).solid(cell):
            r = api.run({"type": "mine", "x": cell[0], "y": cell[1], "z": cell[2], "collect": True}, wait=30)
            if r["status"] != "succeeded":
                raise McError(f"digging the hole at {cell} failed: {r['message']}")
        nav.go_to(cell, ctx.policy, range_=0.5, attempts=1)
        after = api.get("/state")["blockY"]
        if after >= s["blockY"]:
            # Neither the mine nor the step took us down: report that, with the reason, instead of spinning out the
            # remaining rounds and reporting the symptom four rounds later.
            raise api.NavFailed(f"cannot get down into the hole at {cell}: still standing at y {after}")
        yield ("digging", after)
    if api.get("/state")["blockY"] > pit_feet[1]:
        raise api.NavFailed(f"hole at {pit_feet} not dug out")
    # Now the corridor. The shaft alone is a hole: the head reaches into it, breath falls into it, and every bomb has
    # to be thrown from its rim. Two more cells outward turn it into cover with a firing cell that still reaches the
    # bed (4.11 blocks against a 4.5 reach) and a retreat cell that nothing reaches at all.
    #
    # Dug from inside, one cell at a time: each is within arm's reach of the last, so building the corridor never
    # puts us back on the surface.
    for cell in bunker.dig_plan(side, fy)[2:]:
        if not Region(cell, cell).solid(cell):
            continue
        r = api.run({"type": "mine", "x": cell[0], "y": cell[1], "z": cell[2], "collect": True}, wait=20)
        if r["status"] != "succeeded":
            # A corridor one cell short is still cover; the window checks its own geometry before firing.
            log(f"   corridor stops at {cell}: {r['message']}")
            break
        yield ("corridor", cell[1])
    # Obsidian around the mouth when we carry any: a bed blast eats end stone, and a bunker that loses its roof on
    # the second window stops being cover exactly when the fight is longest.
    if Inventory().count("minecraft:obsidian"):
        for cell in bunker.reinforce_cells(side, fy):
            if Region(cell, cell).solid(cell):
                continue
            try:
                place("minecraft:obsidian", cell)
            except McError as e:
                log(f"   mouth not reinforced at {cell}: {e}")
                break
    # Dig the bombing hole by the portal and put the bed on its rim NOW, while the dragon still flies. The window then
    # costs one click from inside a hole instead of standing in the open placing a bed under the head.
    # End inside the hole: finishing on the rim means the next perch starts in the open ("skill returned True without
    # the outcome").
    s = api.get("/state")
    if not in_pit((s["blockX"], s["blockY"], s["blockZ"]), pit_feet, fy):
        nav.go_to(pit_feet, ctx.policy, range_=0.6, attempts=2, min_hp=15)
    return True


PIT = []      # [mouth, firing cell, retreat cell, bed, floor y] from the last build_bed_pit


def _soft_interrupt():
    """Take perception's interrupt without raising: a fight skill answers danger by retreating into the pit and
    trying again, not by failing. Four "dragon breath close" interrupts in a row killed a whole bench run."""
    reason, api.INTERRUPT = api.INTERRUPT, None
    return reason


def _recover(ctx, reason):
    """Carry out the recovery table's answer for an interrupt. One lookup, one fixed action, always an answer.

    Every fight skill used to classify interrupts itself with its own if/elif chain. The chains disagreed with each
    other about the same danger, and anything none of them enumerated fell through to "carry on" — which is standing
    still while being hit. The table decides; this function only executes.
    """
    from . import recovery
    act, why = recovery.explain(reason)
    log(f"   {reason} → {act} ({why})")
    if act == "shake_enderman":
        shake_enderman(ctx)
    elif act == "water_clutch":
        api.run({"type": "wait", "ticks": 5}, wait=5)     # mid-air: the mod pours water under us
    elif act == "retreat_and_eat":
        if PIT:
            nav.go_to(PIT[2], ctx.policy, range_=0.6, attempts=1)
        from . import skills
        try:
            skills.eat(raw_ok=True)
        except McError:
            pass
    else:                                                 # retreat_to_cover, and every unrecognised danger
        if PIT:
            nav.go_to(PIT[2], ctx.policy, range_=0.6, attempts=1)
    return act


@skill(budget=180, stall=120, soft=True)
def await_perch(ctx):
    """Sit in the pit until the dragon is really perched (mod ≥0.1.31 reports DragonPhase) and health is full enough
    to survive the bed's own blast. Never judged by height and distance again."""
    if not PIT:
        raise NotAvailable("no bed pit built yet")
    pit_feet, fire_cell, retreat_cell, bed, floor_y = PIT
    from .combat import angry_endermen, station
    from .combat_tape import EventStream
    last_hp, last_feet, bleeding = None, None, 0
    # Wait on the change instead of asking for it. Each round of this loop costs two round trips (~200 ms) and most
    # rounds learn nothing: the dragon circles for a median 15 s and the landing announces itself 4.5 s ahead. The
    # stream blocks until the game reports a phase change or a hit, so a quiet minute costs nothing and a landing is
    # known the tick it happens rather than up to 200 ms later.
    stream = EventStream()
    waiting = False

    def _pit_is_clear(near_):
        """Nothing hostile is standing in the hole we are about to jump into."""
        return not [e for e in near_ if e.get("hostile") or e["type"] == combat_ENDERMAN
                    if math.dist((e["x"], e["y"], e["z"]), pit_feet) <= 2.5]

    for _ in range(400):
        if waiting:
            # Settled in the hole with nothing to do: park on the event stream. Anything that matters — the dragon
            # changing phase, us taking a hit — wakes this immediately; a timeout just re-checks.
            stream.poll(timeout_ms=1500)
            waiting = False
        s = api.get("/state")
        feet = (s["blockX"], s["blockY"], s["blockZ"])
        here_p = (s["x"], s["y"], s["z"])
        near = entities(128)
        # The hole is NOT a safe room: an enderman is 2.9 blocks tall and teleports, so it reaches us in there.
        # Check that before anything else, and never answer an enderman by climbing back into the hole with it.
        if angry_endermen(near, here_p, 4.0):
            shake_enderman(ctx)
            yield ("enderman", round(s["health"]))
            continue
        why = _soft_interrupt()
        if why:
            # One table decides what every danger is answered with (recovery.TABLE); this only has to notice the one
            # thing the table cannot know — that the corridor is occupied, so retreating into it is not available.
            if not _pit_is_clear(near):
                log(f"   {why}: the corridor is occupied, keeping clear instead")
                for _ in station.__wrapped__(ctx, (0, 0), band=WAIT_BAND, rounds=4):
                    pass
            else:
                _recover(ctx, why)
            yield (why, round(s["health"]))
            continue
        if not s.get("onGround") and s["y"] > floor_y + 3:
            # Flung by a take-off: pathing in mid-air does nothing. Hold still and let the mod's WaterClutch pour
            # under us (one run rode the knockback to y 144 and died on landing).
            api.run({"type": "wait", "ticks": 5}, wait=5)
            yield ("airborne", round(s["y"]))
            continue
        if not in_pit(feet, pit_feet, floor_y):
            d = dragon_entry(entities(128))
            if not prep_safe(d, (s["x"], s["y"], s["z"]), floor_y=floor_y):
                # Walking back in during a perch is what killed the run: wait it out far away instead.
                for _ in station.__wrapped__(ctx, (0, 0), band=WAIT_BAND, rounds=8):
                    pass
                yield ("out of reach", round(s["health"]))
                continue
            nav.go_to(pit_feet, ctx.policy, range_=0.6, attempts=1, min_hp=15)
            yield ("to pit", round(s["health"]))
            continue
        near = entities(128)
        if dragon_dead(near):
            return True
        d = dragon_entry(near)
        # Perched (phase 5/6/7 only), full enough to take the blast, and no breath burning where we have to stand.
        if (bombable(d) and s["health"] >= 19
                and not breath_near(near, fire_cell, 8.0)):
            return True
        if breath_near(near, (s["x"], s["y"], s["z"]), 6.0):
            away = breath_escape((s["x"], s["y"], s["z"]), near)
            if away is not None:
                log("   breath on us: running straight out of it")
                nav.go_to(away, ctx.policy, range_=1.5, attempts=1)
                yield ("breath", round(s["health"]))
                continue
        # Bleeding watchdog: losing health while standing still means whatever hurts us reaches this spot. Waiting
        # another round is not an answer — leave, and let the next round decide again.
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
            # Only when a bite can actually do something: with a full food bar `eat` fails its own verify and the log
            # filled with "no bite" while regeneration was already running.
            from . import skills
            try:
                skills.eat(raw_ok=True)
            except McError as e:
                log(f"   no bite ({e})")
        # Nothing to do this round: wait on the event stream rather than on a fixed number of ticks. A timed wait
        # sleeps through a landing it could have answered 200 ms sooner, and wakes up for nothing when the dragon is
        # still circling with 40 s to go.
        waiting = True
        yield (d.get("phase") if d else None, round(s["health"]))
    raise McError("the dragon never perched")


@skill(budget=120, stall=60, soft=True)
def bed_bomb_window(ctx):
    """One bomb, then straight back into the pit. An attack window does exactly one thing: the old loop kept standing
    outside between bombs."""
    if not PIT:
        raise NotAvailable("no bed pit built yet")
    pit_feet, stand, retreat_cell, bed, floor_y = PIT
    item = _bed_item()
    if item is None:
        raise NotAvailable("out of beds (melee is combat.fight_dragon)")
    # Everything the window needs, checked before stepping into the dragon's reach: the pillar still in range, the
    # dragon really perched, full health, no breath on the bombing spot. One run kept going after being flung 80
    # blocks up and reported "no exit portal pillar in range" from wherever it landed.
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
    # The window is not the time to discover the pit was never finished: one run mined end stone for 10 s here and
    # died. Check the hole and our place in it first.
    s = api.get("/state")
    from .world import Region
    dug = Region((pit_feet[0], pit_feet[1], pit_feet[2]), (pit_feet[0], floor_y - 1, pit_feet[2]))
    if any(dug.solid((pit_feet[0], y, pit_feet[2])) for y in range(pit_feet[1], floor_y)):
        raise NotAvailable(f"the pit at {pit_feet} isn't dug out")
    # Into the hole by the portal for this one window only — and back to the pit whatever happens.
    if not nav.go_to(stand, ctx.policy, range_=0.8, attempts=1, min_hp=15):
        nav.go_to(pit_feet, ctx.policy, range_=0.6, attempts=1)
        raise api.NavFailed(f"bombing hole {stand} not reached")
    before = dragon_entry(entities(128))
    from .world import Region as _R
    # The window as ONE submission: detonate, then walk back to the retreat cell, queued together and executed by the
    # client without coming back to Python in between.
    #
    # This is the open-loop part of the design, and it is open-loop because the numbers say closing the loop is
    # impossible: an HTTP round trip measures ~98 ms and the shortest window the tapes recorded is 0.4 s, so a
    # mid-window decision would spend a quarter of the window asking what to do. Everything that could be decided was
    # decided by the planner before this ran; what is left is a fixed sequence.
    #
    # The sequence lives here, in Python, not in the mod: composing tasks is strategy, and the mod stays a thing that
    # executes tasks and reports what it sees.
    bomb = ({"type": "use", "x": bed[0], "y": bed[1], "z": bed[2]} if _R(bed, bed).solid(bed)
            else {"type": "bed_bomb", "x": bed[0], "y": bed[1], "z": bed[2], "item": item})
    try:
        # Six seconds, not the default half hour: this is one detonation and a three-block walk. A window that has
        # not finished in six seconds has gone wrong, and waiting longer only means being outside for longer.
        results = api.run_chain([bomb,
                                 {"type": "travel", "x": retreat_cell[0], "y": retreat_cell[1],
                                  "z": retreat_cell[2], "range": 0.6}],
                                wait=6)
        r = results[0] if results else {"status": "failed", "message": "no result"}
    except api.Interrupted as e:
        # Soft: the window is off, the fight isn't. The recovery table decides where to go; the driver calls us again.
        _recover(ctx, _soft_interrupt() or str(e))
        raise NotAvailable(f"window cut short ({e})")
    if r["status"] != "succeeded":
        if "unknown task" in (r.get("message") or "").lower():
            raise NotAvailable("the mod has no bed_bomb task (needs ≥0.1.31)")
        log(f"   bed bomb failed: {r['message']}")
    api.run({"type": "wait", "ticks": 10}, wait=5)
    # "Place, pop, place, pop": every blast shoves the dragon up, and the next bed catches it on the way. One bomb
    # per visit throws away the rest of a perch, which is why runners land 4–5 blasts in a single landing.
    for _ in range(2):
        d_now = dragon_entry(entities(128))
        if not bombable(d_now) or api.get("/state")["health"] < 19 or not _bed_item():
            break
        again = api.run({"type": "bed_bomb", "x": bed[0], "y": bed[1], "z": bed[2], "item": _bed_item()}, wait=8)
        log(f"   follow-up bomb: {again['status']} ({(d_now or {}).get('health')} hp before)")
        api.run({"type": "wait", "ticks": 10}, wait=5)
    after = dragon_entry(entities(128))
    lost = round((before or {}).get("health", 0) - (after or {}).get("health", 0))
    log(f"   bed bomb: dragon {before.get('health') if before else None} → "
        f"{after.get('health') if after else None} hp (-{lost}), player hp {api.get('/state')['health']}")
    if lost < 20:
        # The metric that tells the two failure modes apart: a weak blast means the bed is in the wrong cell, a
        # missed window means the timing was wrong. 4–5 clean windows should kill.
        log(f"   ?? window took only {lost} hp — the bed sits wrong, not the timing")
    yield round((after or {}).get("health", 0))
    return True


CAGE_STAND_R = 2      # melee reach is ~3 blocks and the bars eat one: at 3 the attack task got 0 hits in 10 s


def cage_plan(crystal, here, floor_y):
    """Pure: (tower base, stand cell, bars between us and the crystal) for breaking a caged end crystal. Without a bow
    the speedrun way is to tower up beside the pillar on the side we come from, break the iron bars, stand in a water
    source and hit the crystal — the blast is what kills an unarmored player, water takes most of it."""
    dx, dz = here[0] - crystal[0], here[2] - crystal[2]
    n = math.hypot(dx, dz) or 1.0
    ux, uz = dx / n, dz / n
    cy = math.floor(crystal[1])
    base = (math.floor(crystal[0] + ux * CAGE_STAND_R), floor_y, math.floor(crystal[2] + uz * CAGE_STAND_R))
    stand = (base[0], cy, base[2])
    # Every bar cell around the crystal, not only the ones on our line: the cage is a ring and the hit can be blocked
    # by a corner post.
    bars = [(math.floor(crystal[0]) + bx, cy + dy, math.floor(crystal[2]) + bz)
            for bx, bz in ((1, 0), (-1, 0), (0, 1), (0, -1)) for dy in (0, 1)]
    return base, stand, bars


def caged(crystal, here):
    """Pure: the crystal sits on a tall caged pillar (the open ones are at island level and can be hit from there)."""
    return crystal[1] - here[1] > 8


@skill(budget=90, stall=45, soft=True)
def shake_enderman(ctx):
    """Get an angry enderman off us without fighting it, in this order: drop into the wait pit (3 blocks tall, they
    can't follow), else stand in a water source (endermen teleport away from water), else walk away without turning
    to look at it. Looking is what provokes them, so nothing here aims at the enderman."""
    from .skillcore import place
    from .combat import angry_endermen
    from .world import entities as _entities
    s = api.get("/state")
    here = (s["x"], s["y"], s["z"])
    angry = angry_endermen(_entities(32), here, 16.0)
    if not angry:
        return True
    # The hole only helps when it is actually dug — walking toward the portal to reach a half-built one puts us under
    # the dragon, which is far worse than an enderman (one run died doing exactly that).
    # Water first (zh wiki: 听到直升机般的声音时立即在脚下放水驱赶): it costs one click, works where we stand and
    # teleports them away. Walking to the hole is slower and can drag us toward the dragon.
    if Inventory().count("minecraft:water_bucket"):
        feet = nav.feet_now()
        try:
            place("minecraft:water_bucket", feet)
            api.run({"type": "wait", "ticks": 20}, wait=5)
            yield "water"
            return True
        except McError as e:
            log(f"   no water placed ({e}): trying the hole instead")
    # The hole only helps if it is already at our feet. Running 10–20 blocks to it through two angry endermen is how
    # this skill died twice; distance matters more than cover here.
    if PIT and math.dist(here, PIT[0]) <= 6:
        pit_feet, fire_cell, retreat_cell, bed, floor_y = PIT
        from .world import Region as _R
        dug = not any(_R((pit_feet[0], y, pit_feet[2]), (pit_feet[0], y, pit_feet[2])).solid((pit_feet[0], y, pit_feet[2]))
                      for y in range(pit_feet[1], floor_y))
        if dug and nav.go_to(pit_feet, ctx.policy, range_=0.6, attempts=1):
            api.run({"type": "wait", "ticks": 20}, wait=5)
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
    # A plain goto with a short budget, not travel: being chased, 10 s of "no progress" before anything changes is
    # far too slow. Three seconds and we look at the world again.
    api.run({"type": "goto", "x": away[0], "y": away[1], "z": away[2], "range": 2, "partial": True}, wait=3)
    yield "away"
    return True


@skill(budget=300, stall=120, soft=True)
def break_caged_crystal(ctx, crystal):
    """Tower up to a caged crystal, break the bars, stand in water and hit it. `crystal` is an /entities row."""
    from .skillcore import place
    from .world import Region
    inv = Inventory()
    block = _building_block()
    if not inv.count(block):
        raise NotAvailable("no blocks to tower up with")
    here = nav.feet_now()
    pos = (crystal["x"], crystal["y"], crystal["z"])
    # The tower's own column, not the crystal's: the base was landing inside the obsidian pillar or in mid-air
    # ("tower base (-4, 63, 4) not reachable").
    base, stand, bars = cage_plan(pos, here, round(here[1]))
    floor = _floor_under(base[0], base[2], round(here[1]))
    if floor is None:
        raise NotAvailable(f"no ground under the tower base {base}")
    base, stand, bars = cage_plan(pos, here, floor)
    log(f"   caged crystal at {tuple(round(c) for c in pos)}: tower at {base} up to {stand}")
    if not nav.go_to(base, ctx.policy, range_=1.0, attempts=2):
        raise api.NavFailed(f"tower base {base} not reachable")
    if not api.get("/state").get("onGround"):
        # Pillaring needs something under the feet ("towering up failed: nothing solid to stand on").
        api.run({"type": "wait", "ticks": 10}, wait=5)
    while nav.feet_now()[1] < stand[1]:
        r = api.run({"type": "pillar", "item": block}, wait=30)
        if r["status"] != "succeeded":
            raise McError(f"towering up failed: {r['message']}")
        yield nav.feet_now()[1]
    for cell in bars:
        if Region(cell, cell).solid(cell):
            api.run({"type": "mine", "x": cell[0], "y": cell[1], "z": cell[2], "collect": False}, wait=30)
            yield cell
    if inv.count("minecraft:water_bucket"):
        # Standing in water when it goes off: an end crystal's blast is power 6 and we wear no armor.
        try:
            place("minecraft:water_bucket", nav.feet_now())
        except McError as e:
            log(f"   no water under our feet ({e}): hitting the crystal anyway")
    for _ in range(3):
        try:
            api.run({"type": "attack", "entity": crystal["id"]}, wait=6)
        except api.TaskStuck:
            pass                     # out of reach for a moment: step closer and try again
        api.run({"type": "wait", "ticks": 10}, wait=5)
        if not [e for e in entities(128) if e["id"] == crystal["id"]]:
            return True
        nav.go_to((stand[0], stand[1], stand[2]), ctx.policy, range_=0.8, attempts=1)
        yield "hitting"
    raise McError("the crystal survived the hits")


def fight_state(near, s, ctx):
    """Pure-ish: the planner's view of the fight, assembled from one perception round.

    The planner is a pure function of this dict, which is what makes it testable without a game: the same dict can be
    constructed by hand in a test or replayed from a tape.
    """
    from . import bunker, fight_plan
    d = dragon_entry(near)
    inv = Inventory()
    here = (s["x"], s["y"], s["z"])
    phase = (d or {}).get("phase")
    elapsed = time.time() - PHASE_SINCE[1] if PHASE_SINCE[0] == phase else 0.0
    in_cover = bool(PIT) and math.dist(here, PIT[2]) <= 1.5
    return {
        "phase": phase if phase is not None else 0,
        "phase_elapsed_s": round(elapsed, 2),
        "hp": s["health"],
        "hp_floor": fight_plan.CONFIG["combat"]["hp_floor"],
        "incoming_dps": 6.0 if breath_near(near, here, 6.0) else 0.0,
        "dragon_hp": (d or {}).get("health") or 0.0,
        "beds": inv.count("bed"),
        "obsidian": inv.count("minecraft:obsidian"),
        "crystals_open": len([e for e in near if e["type"] == "minecraft:end_crystal"
                              and not caged((e["x"], e["y"], e["z"]), here)]),
        "water": bool(inv.count("minecraft:water_bucket")),
        "angry_endermen": len([e for e in near if e["type"] == combat_ENDERMAN and e.get("angry")]),
        "tunnel_ready": bool(PIT),
        "bed_placed": bool(PIT) and _solid(PIT[3]),
        "reinforced": False,
        "in_cover": in_cover,
        "exposure_s": 1.0 if in_cover else 3.0,
    }


def _solid(cell):
    from .world import Region
    return Region(cell, cell).solid(cell)


def _carry_out(ctx, intent, state, near):
    """Execute one planned intent. Dispatch only — every choice was already made by fight_plan.plan().

    Split this way on purpose: choosing is a pure function that a test can exercise a thousand times a second, and
    doing is a thin layer with no judgement in it. The old loop mixed the two, which is why its order could only be
    changed by editing the fight itself.
    """
    from . import combat
    from .skillcore import place as _place
    name = intent["intent"]
    if name == "dig_tunnel":
        build_bed_pit(ctx)
    elif name == "place_bed":
        if PIT and not _solid(PIT[3]) and _bed_item():
            if nav.go_to(PIT[1], ctx.policy, range_=0.8, attempts=1, min_hp=15):
                try:
                    _place(_bed_item(), PIT[3])
                    log(f"   bed on {PIT[3]} while it flies")
                except (McError, api.NavFailed) as e:
                    log(f"   bed not placed ({e})")
    elif name == "reinforce":
        from . import bunker
        side = choose_side((api.get("/state")["x"], 0, api.get("/state")["z"]))
        for cell in bunker.reinforce_cells(side, PIT[4] if PIT else 0):
            if not _solid(cell):
                try:
                    _place("minecraft:obsidian", cell)
                except McError:
                    break
    elif name == "shoot_crystal":
        here = (api.get("/state")["x"], api.get("/state")["y"], api.get("/state")["z"])
        open_ones = [e for e in near if e["type"] == "minecraft:end_crystal"
                     and not caged((e["x"], e["y"], e["z"]), here)]
        if open_ones:
            try:
                combat.shoot(combat.crystal_order(open_ones, here)[0], near=near)
            except NotAvailable as e:
                log(f"   holding the shot: {e}")
    elif name == "water_bucket":
        shake_enderman(ctx)
    elif name == "fire_window":
        await_perch(ctx)
        if _bed_item():
            bed_bomb_window(ctx)
    else:                                                  # retreat: the default, and the answer to every fault
        if PIT:
            nav.go_to(PIT[2], ctx.policy, range_=0.6, attempts=1)
        else:
            api.run({"type": "wait", "ticks": 10}, wait=5)
    return True


PHASE_SINCE = [None, 0.0]     # (phase, when it started) — the planner needs elapsed time, not just the phase


def _track_phase(near):
    d = dragon_entry(near)
    phase = (d or {}).get("phase")
    if phase != PHASE_SINCE[0]:
        PHASE_SINCE[:] = [phase, time.time()]
    return phase


@skill(budget=1800, stall=300, soft=True)
def slay_dragon(ctx):
    """The fight, one planned intent at a time: perceive, plan, dispatch, repeat.

    There is no fixed order here any more. What to do next comes from fight_plan.plan(), which vetoes anything that
    cannot be finished and returned from inside the remaining phase time, then picks whatever saves the most seconds.
    The order that used to be written out here (crystals, pit, perch, bomb) was unchangeable without editing the
    fight, and it could not answer "is a tunnel worth it with one window left" at all.
    """
    if api.get("/state")["dimension"] != "minecraft:the_end":
        raise NotAvailable("not in the End")
    from . import fight_plan
    for _ in range(120):
        near = entities(128)
        _track_phase(near)
        if dragon_dead(near):
            ctx.mem.data["dragon_defeated"] = True
            ctx.mem.save()
            log("the exit portal is open — the dragon is dead")
            return True
        s = api.get("/state")
        # The planner decides. Not a hard-coded order any more: it reads the state, vetoes what cannot be finished
        # safely, and returns one intent with a deadline. Everything below is dispatch — the choosing happens in
        # fight_plan, which is a pure function and is tested without a game.
        try:
            state = fight_state(near, s, ctx)
            intent = fight_plan.plan(state)
        except Exception as e:                            # a planner fault must never leave us standing in the open
            log(f"   planner failed ({e}): retreating")
            intent = {"intent": "retreat", "benefit_s": 0.0, "deadline_s": 0.0}
            state = {}
        log(f"   plan: {intent['intent']} (worth {intent['benefit_s']}s, phase "
            f"{state.get('phase')}, {state.get('dragon_hp', 0):.0f} hp left)")
        done = _carry_out(ctx, intent, state, near)
        yield (intent["intent"], round(s["health"]))
        if done:
            continue
        d = dragon_entry(entities(128))
        yield ("hp", round((d or {}).get("health", 0)))
    raise McError("the dragon fight ran out of rounds")


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


@skill(done=lambda c: api.get("/state")["dimension"] == "minecraft:the_end", budget=120, stall=60)
def enter_end(ctx):
    """Jump into the activated end portal (the centre of the frame ring)."""
    centre = portal_centre([(h["x"], h["y"], h["z"]) for h in find(["end_portal_frame"], radius=32, limit=12)])
    if centre is None:
        raise NotAvailable("no end portal nearby")
    if not find(["end_portal"], radius=32, limit=1):
        raise NotAvailable("the end portal isn't active yet")
    nav.go_to(centre, ctx.policy, range_=0.6, attempts=1)
    for _ in range(10):
        api.run({"type": "wait", "ticks": 20}, wait=5)
        yield None
        if api.get("/state")["dimension"] == "minecraft:the_end":
            return True
    raise McError("stood in the end portal but didn't arrive in the End")

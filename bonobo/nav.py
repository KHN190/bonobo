"""Getting from A to B: ASKING THE GAME to, and pricing what it answers.

There is no pathfinder here any more. There were two — the mod's, which moves the body (walking, digging,
bridging, pillaring, ladders), and one in this file, which planned routes the body then did not take. Every
disagreement between them became a bug: water in the floor priced as flat ground, ore two blocks inside rock
"unreachable", a village room behind a door the walker would have opened. Physics is the world's, so:

    route_s(cell, policy)   /plan — is there a way, and how many seconds
    go_to(pos, policy)      travel — the mod walks, digs and bridges its own way there
    way_to(ctx, cells)      the answer to "could not get to it": walk with digging allowed, then check

What stays on this side is the decision: what is worth walking to, what a walk is worth, and when to give up."""
import heapq
import math
import re
import time
from dataclasses import dataclass, field

from . import api, tape
from .api import McError, NotAvailable, log
from .data import GROUPS, HAND_MINEABLE_SUFFIX, bare
from .world import NEIGHBOURS6, Inventory, Region, add, region_around

DIRS4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]


@dataclass
class Policy:
    """What movement may do. Night and base contexts tighten it; daytime exploration loosens it."""
    allow_dig: bool = True
    allow_surface: bool = True          # escaping up to open sky is allowed
    protected: set = field(default_factory=set)  # structure cells of sites: never dig
    before_segment: object = None       # reflex hook passed to run_chain (tools, light, site bookkeeping)
    lava_ok: bool = False               # the current goal wants lava (bucket, portal casting): don't avoid or cover it
    allow_build: bool = True            # routes may place blocks (bridges, pillars) and ladders
    hand_only: bool = False             # no usable pickaxe: dig only what hands break quickly (dirt, sand, logs…)


def mine_task(c, collect=False):
    return {"type": "mine", "x": c[0], "y": c[1], "z": c[2], "collect": collect, "requireDrops": False}


_features = None
WALK_EAT_BELOW = 18        # hunger points: the jar eats on the way below this (regen stops at 18), `autoeat_policy`


def autoeat_policy():
    """Pure: what the jar eats on the way, and when (POST /autoeat, jar ≥ 0.1.46): below WALK_EAT_BELOW, the
    best food first (data.FOOD's order). Standing still and very hungry is the MAINTAIN row's (EAT_BELOW)."""
    from .data import FOOD
    return {"below": WALK_EAT_BELOW, "foods": [f"minecraft:{f}" for f in FOOD]}


def mod_features():
    """Movement features of the running mod: 'pillar' task and ladders placed in the body's own cell (≥ 0.1.15)."""
    global _features
    if _features is None:
        try:
            v = tuple(int(x) for x in re.findall(r"\d+", str(api.status().get("version", "0")))[:3])
        except McError:
            return set()
        # "ladder_in_cell" stays off: a ladder plate sits 0.1875 from the cell edge and the body reaches 0.2 from it
        # only when perfectly centred — 0.02 off-centre and the server rejects the placement every time. Climb by
        # pillaring instead until the mod centres exactly before placing.
        _features = {"pillar"} if v >= (0, 1, 15) else set()
        if v >= (0, 1, 17):
            _features.add("travel")   # the mod plans and executes walk/dig/bridge/pillar routes itself
        if v >= (0, 1, 40):
            _features.add("approach_dig")   # mine/place/use dig their own way when walking finds none (ApproachTask)
        if v >= (0, 1, 50):
            _features.add("input")          # keys held until the body stands (the "input" task): climb_out
        if v >= (0, 1, 46):
            _features.add("autoeat")        # the jar eats while only walking, on the policy /autoeat sets
            # Set here, at the session's first contact, once: whoever drives the jar (the brain, a bench row running
            # a skill directly) walks with it — sent from the brain's round only, a bench row never had it.
            try:
                api.post("/autoeat", autoeat_policy())
            except McError as e:
                log(f"autoeat policy not set: {e}")
    return _features


def building_item():
    inv = Inventory()
    options = [b for b in GROUPS["building"] if inv.usable(b)]
    return max(options, key=inv.usable) if options else None


def feet_now():
    s = api.get("/state")
    return s["blockX"], s["blockY"], s["blockZ"]


def ground_in_column(solid, x, z, y_hint, span=32):
    """Pure: standing height (one above the highest solid block with 2 free cells above it) in column (x, z),
    searching y_hint ± span from the top down; None when the column has no such spot."""
    for y in range(y_hint + span, y_hint - span - 1, -1):
        if solid((x, y, z)) and not solid((x, y + 1, z)) and not solid((x, y + 2, z)):
            return y + 1
    return None


LEG = 48   # blocks per travel leg on long trips
ROAD_MEM = None   # the brain's memory: travelled legs are kept as a road network (roads.py) and reused


BLOCK_RESERVE = 16   # blocks kept back from travel: shelter walls, a pillar out of a hole, the next bridge


def place_budget(stock):
    """Pure: how many blocks a trip may spend on bridges and pillars. A 200-block trek spent all 64 cobblestone
    bridging small dips and then failed with "no building blocks to bridge with", so keep a reserve — but never
    strand a small kit: with little stock, half of it may still go into getting out."""
    return max(0, stock - BLOCK_RESERVE) if stock > 2 * BLOCK_RESERVE else stock // 2


# Below this, walking on is how runs end: no sprinting, no regeneration, and the next hit is the last one.
# A default, not an option — see the comment on go_to.
MIN_WALK_HP = 6.0


def safe_destination(pos, hazards=None, clear=1.0):
    """Pure: `pos`, or a nearby spot clear of every hazard when `pos` sits inside one. None when nothing is clear.

    Consulted by every walk rather than by the call sites that remember — the alternative produced exactly one
    caller that did. The choice comes from combat_model, the same closed-form rule the safety veto and the retreat
    use, so a destination this accepts is never one the veto would refuse.

    `hazards` are (centre, radius, velocity) rows; the two-element form from combat_model.hazard_points is accepted too.
    """
    from . import combat_model
    hazards = [h if len(h) > 2 else (h[0], h[1], (0.0, 0.0, 0.0)) for h in (hazards or [])]
    if not hazards or combat_model.min_tti(pos, hazards) == float("inf"):
        return pos
    spot, slack = combat_model.best_step(pos, hazards)
    return spot if slack is not None and slack > 0 else None


PLAYER_SPEED = 4.3


def _arrived(start, target, began, ok, closer=False):
    """Feed one walk back into the terrain estimate, and say what the leg achieved.

    Returns True when we got there (`there`), `Walked` when we only got nearer — falsy, so a caller that asks "did
    the walk get there" reads no, and `moved` reads the progress. Without this a deep
    target was a failure every round, cooled for two minutes, and never finished, though every attempt dug
    another ten blocks toward it.
    """
    try:
        from . import field
        straight = math.dist(start, target) / PLAYER_SPEED
        if ok and straight > 0.5:
            state = api.get("/state")
            field.TERRAIN.observed(field.bucket_of(state), straight, time.time() - began)
    except Exception:
        pass
    if ok:
        return True
    return Walked(math.dist(start, target) - math.dist(feet_now(), target)) if closer else False


class Walked(float):
    """A leg that got nearer without arriving: never arrival, so falsy (a travel stopped 1.7 below the platform read
    as True and the scenario was over). Carries how many blocks it gained; `moved` reads it as progress."""

    def __bool__(self):
        return False

    def __repr__(self):
        return f"Walked({float(self):.0f} blocks nearer)"


# How much closer a walk has to leave us before it counts as progress rather than a failed errand. A journey is
# made of legs: the mod walks until the ground runs out, digs or bridges what it can, and stops at the closest
# point it could reach. That is not "unreachable" — the next round starts from there and goes further. Treating
# it as a failure is what put a 120 s cooldown on every deep target and let a 135 s errand take the round back.
PROGRESS_BLOCKS = 2.0
# How many legs one call may walk before it hands the round back. Enough that a deep or far target is reached in
# one errand; few enough that the body comes up for air and the planner can change its mind.
LEGS = 6


AVOID_RADIUS = 64        # protected cells this near a walk's ends go with it: the jar may not dig or build in them
AVOID_MAX = 4000
# Task types whose approach may dig and build (jar ApproachTask → TravelTask): each carries the "avoid" list.
APPROACHING = ("mine", "place", "use", "build", "mine_many")


def avoid_cells(protected, *near):
    """Pure: the "avoid" list a walk that may dig carries — the protected cells (memory.protected_cells: our own
    builds, sites, machines) within AVOID_RADIUS of any of `near`, as the jar reads them. One list for travel and
    for every approach."""
    return [{"x": c[0], "y": c[1], "z": c[2]} for c in sorted(protected)
            if any(math.dist(c, n) <= AVOID_RADIUS for n in near)][:AVOID_MAX]


def with_avoid(task, protected):
    """Pure: `task` with its "avoid" when its type approaches by digging (APPROACHING) and it names none; else the
    task as it was. The cells near its own targets (x/y/z, or each of "blocks")."""
    if task.get("type") not in APPROACHING or "avoid" in task:
        return task
    targets = [(b["x"], b["y"], b["z"]) for b in task.get("blocks", ())]
    if "x" in task:
        targets.append((task["x"], task["y"], task["z"]))
    return {**task, "avoid": avoid_cells(protected, *targets)}


ARRIVE_SLACK = 0.5       # the walker's own margin past `range` (the mod counts arrived within range + 0.5)


def there(state, pos, range_):
    """Pure: the body stands within range_ + ARRIVE_SLACK of `pos`, in 3-D — the feet's block to a block target
    (int coordinates: the walker's own test, TravelTask.arrived, with the height within range_) or the feet to a
    point. The one arrival test: what
    the walker answered is not read, only where it left the body (a travel "succeeded" one step below the target;
    a leg that stopped short is not there)."""
    if not at_rest(state):
        return False                   # mid-jump over the target cell (y 200.18, off the ground) is not there
    body = (state["x"], state["y"], state["z"])
    if all(isinstance(p, int) for p in pos):
        body = tuple(math.floor(v) for v in body)
        if abs(body[1] - pos[1]) > range_:
            return False               # a step below the target: range + slack in 3-D let one block of height pass
    return math.dist(body, pos) <= range_ + ARRIVE_SLACK


ASHORE_RANGE = 0.3      # a land target is the cell itself: 1.5 counted a body still in the water 1.36 from the bank


def ashore(state, land, range_=ASHORE_RANGE):
    """Pure: the body stands on `land` (its feet cell), dry — on the ground and out of the water. `there` alone lets
    the water hold the body up (the jar's GotoTask counts touching water as arrived): reach_land_swim "arrived"
    1.36 blocks off the bank and was still swimming."""
    return bool(state.get("onGround")) and not state.get("inWater") and there(state, land, range_)


CLIMB_REACH = 2.0      # horizontal blocks from the bank's cell within which a swimmer presses into it
CLIMB_TICKS = 40


def climb_out_tasks(state, land):
    """Pure: the batch that lifts a swimmer beside the bank onto `land` (its feet cell): face the bank, then
    forward+jump held until standing (jar "input", ≥ 0.1.50) — the move the walker never made, bobbing at the
    bank's edge (reach_land_swim). [] when already ashore or not beside it."""
    if ashore(state, land) or not state.get("inWater"):
        return []
    if math.dist((state["x"], state["z"]), (land[0] + 0.5, land[2] + 0.5)) > CLIMB_REACH:
        return []
    return [{"type": "look", "x": land[0] + 0.5, "y": land[1] + 0.5, "z": land[2] + 0.5},
            {"type": "input", "keys": ["forward", "jump"], "until": "onGround", "ticks": CLIMB_TICKS}]


def climb_out(land):
    """Out of the water onto `land` when the body is beside it (`climb_out_tasks`). True when ashore after."""
    if "input" not in mod_features():
        return False
    tasks = climb_out_tasks(api.get("/state"), land)
    if tasks:
        try:
            api.run_chain(tasks, stop_on_failure=True, wait=10)
        except McError as err:
            log(f"   climb out onto {land}: {err}")
    return ashore(api.get("/state"), land)


def at_rest(state):
    """Pure: the body is held up — on the ground, in water or on a ladder — not in the air. The jar's own arrival
    condition (GotoTask: onGround or touching water; climbing from 0.1.41)."""
    return bool(state.get("onGround") or state.get("inWater") or state.get("climbing"))


def moved(got):
    """Did a `go_to` answer move us: there (True), or a leg that gained ground (`Walked`)? Never read as a bare
    truth value — the three answers are spelled out here, once."""
    return got is True or isinstance(got, Walked)


def walked_closer(start, here, target):
    """Did this leg actually bring us nearer the target? In blocks, against where it began."""
    return math.dist(start, target) - math.dist(here, target) >= PROGRESS_BLOCKS


# What a walk may do to the world, by why we walk: (break, place, bridge out over the void). Every walk may dig
# into a hill or bridge a ditch — the pathfinder prices those against walking round. Only a walk with a known
# far side (work: a vein, a trunk, a site) may lay a floor where nothing lies within a survivable drop below: an
# explore leg bridged off the sky platform and a knockback threw the body 125 blocks down. Protected cells (our
# builds) are avoided by every walk (`avoid_cells`). The one table for every walk.
MOVES = {"work": (True, True, True), "explore": (True, True, False), "evade": (True, True, False)}
SAFE_DROP = 3        # blocks a walk may drop onto dry ground unhurt; deeper only into water (`terrain.landing`)


def landing(region, here, spot, max_drop=None, least=2):
    """Pure: the farthest cell walking straight from `here` toward `spot` reaches on connected ground — every
    column on the way has a floor at most `max_drop` (SAFE_DROP) below the last one and at most one above, or
    water under it (a drop into water is survivable); the walk stops at the first column that fails, or at the
    region's edge. None when that is under `least` blocks away. evade picked a spot 16 blocks off a sky platform
    by geometry alone and the body fell 136 blocks (ban_needs_a_failure, resume_after_combat)."""
    max_drop = SAFE_DROP if max_drop is None else max_drop
    x0, y, z0 = (int(math.floor(v)) for v in here)
    dx, dz = spot[0] - here[0], spot[2] - here[2]
    steps = int(max(abs(dx), abs(dz)))
    best, seen = None, set()

    def far_enough(cell):
        return cell if cell is not None and math.dist((cell[0], cell[2]), (x0, z0)) >= least else None
    for k in range(1, steps + 1):
        cx, cz = int(math.floor(here[0] + dx * k / steps)), int(math.floor(here[2] + dz * k / steps))
        if (cx, cz) in seen:
            continue
        seen.add((cx, cz))
        floor = None
        for fy in range(y + 1, y - max_drop - 1, -1):          # feet cells, a step up first
            below, feet_c, head_c = (cx, fy - 1, cz), (cx, fy, cz), (cx, fy + 1, cz)
            if not all(region.inside(c) for c in (below, feet_c, head_c)):
                return far_enough(best)
            if region.solid(feet_c) or region.solid(head_c):
                continue
            if region.solid(below) or region.name(below).endswith("water"):
                floor = fy
                break
        if floor is None:
            # Nothing within a safe drop: only water further down makes the step survivable.
            for fy in range(y - max_drop - 1, region.lo[1], -1):
                c = (cx, fy - 1, cz)
                if not region.inside(c) or region.solid(c):
                    break
                if region.name(c).endswith("water"):
                    floor = fy
                    break
        if floor is None:
            return far_enough(best)
        y = floor
        best = (cx, y, cz)
    return far_enough(best)


def may_alter(purpose, policy):
    """Pure: (may break, may place, may bridge over the void) for a walk made for `purpose`, within the round's
    policy."""
    brk, plc, void = MOVES[purpose]
    return brk and bool(getattr(policy, "allow_dig", True)), plc and bool(getattr(policy, "allow_build", True)), void


def go_to(pos, policy, range_=1.5, attempts=3, min_hp=MIN_WALK_HP, avoid_hazards=True, purpose="work"):
    """Walk; when the walker can't get there, build/dig a route toward the target.

    `min_hp` aborts the walk when health drops below it. It defaults to a real value because it used to
    default to None: every call site had to remember the guard, and almost none did. Pass min_hp=0 where
    walking while nearly dead is the point — fleeing, or recovering a body.
    """
    pos = tuple(pos)
    from . import arbiter
    if not arbiter.BODY.owns("nav.go_to"):
        # A fight holds the body: an interruption, not "no way there" — read as False it banned the vein the walk
        # was heading for (ban_needs_a_failure).
        raise api.FightHolds(f"nav.go_to {pos}: a fight holds the body")
    _began, _from = time.time(), feet_now()
    if avoid_hazards:
        from . import combat_model
        hz = combat_model.hazards()
        if hz:
            safe = safe_destination(pos, hz)
            if safe is None:
                log(f"   every spot near {pos} is inside something dangerous: not walking there")
                return False
            if safe != pos:
                from . import combat_model
                log(f"   {pos} sits inside a hazard: walking to {safe} instead "
                    f"(slack {combat_model.slack_at(safe, hz, pos)}s)")
                pos = tuple(safe)
    if "travel" in mod_features():
        # One world model: the mod plans and executes the whole route (walk, swim, climb, dig, bridge, pillar), so
        # Python never plans moves the walker can't make.
        here = feet_now()
        if ROAD_MEM is not None and math.hypot(pos[0] - here[0], pos[2] - here[2]) > LEG:
            # Known roads first: legs really travelled before (roads.py) are chained where they beat the direct way.
            from . import roads
            known = ROAD_MEM.data.setdefault("roads", {}).setdefault(api.get("/state")["dimension"], [])
            for wp in roads.route(known, here, pos)[:-1]:
                t0 = time.time()
                if not moved(go_to(wp, policy, range_=6, attempts=1, purpose=purpose)):
                    break
                roads.add_leg(known, here, feet_now(), time.time() - t0, time.time())
                here = feet_now()
        if math.hypot(pos[0] - here[0], pos[2] - here[2]) > LEG:
            # Long trips in legs: one plan over 100+ blocks exhausts the search and ends "target unreachable"
            # (≈1 150 s of failed travel in 2.5 h). Each leg is a short, reliable plan.
            start, t_start = here, time.time()
            dx, dz = pos[0] - here[0], pos[2] - here[2]
            n = math.ceil(math.hypot(dx, dz) / LEG)
            for k in range(1, n):
                hop = (round(here[0] + dx * k / n), round(here[1] + (pos[1] - here[1]) * k / n),
                       round(here[2] + dz * k / n))
                if min_hp is not None and api.get("/state")["health"] < min_hp:
                    log(f"   travel stopped at {min_hp} hp: falling back instead of walking on")
                    return False
                if not moved(go_to(hop, policy, range_=6, attempts=1, min_hp=min_hp, purpose=purpose)):
                    # This hop got nowhere. The trip is not over unless we are no nearer than when it started:
                    # the caller asked for the far end, and the next round carries on from wherever we stand.
                    return _arrived(_from, pos, _began, False,
                                    closer=walked_closer(_from, feet_now(), pos))
            here = feet_now()
            # Per-leg accounting: a Nether trek took 3× the Overworld one with no failures and no blocks spent, so
            # the cost is inside the walking itself. Without this line there is no way to tell replanning from
            # slow movement.
            _walked = math.dist(start, here)
            _took = time.time() - t_start
            log(f"   leg: {_walked:.0f} blocks in {_took:.1f}s ({_took / max(_walked, 1) * 100:.0f} s/100)")
            if ROAD_MEM is not None:
                # This trip is now a known road (timed), for the way back and for later trips.
                from . import roads
                roads.add_leg(ROAD_MEM.data.setdefault("roads", {}).setdefault(api.get("/state")["dimension"], []),
                              start, here, time.time() - t_start, time.time())
        budget = place_budget(Inventory().count("building"))
        avoid = avoid_cells(policy.protected, here, pos)
        grounded = False
        brk, plc, void = may_alter(purpose, policy)
        # A journey is made of LEGS. The mod walks until the ground, the pickaxe or its own search budget runs
        # out, then stops at the closest point it could reach and says "target unreachable". Read as a failure,
        # that put a two-minute cooldown on every far or deep target and none of them ever finished — though
        # every attempt had dug another ten blocks toward it. So: keep going while each leg brings us nearer,
        # and only give up when one does not.
        for _ in range(max(attempts, LEGS)):
            was = feet_now()
            r = api.run({"type": "travel", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_,
                         "break": brk, "place": plc, "voidBridge": void, "placeBudget": budget,
                         "avoid": avoid}, wait=900, awaits="where the leg left the body decides the next leg (walked_closer, the retry on the ground)")
            if there(api.get("/state"), pos, range_):
                return _arrived(_from, pos, _began, True)
            if walked_closer(was, feet_now(), pos):
                budget = place_budget(Inventory().count("building"))
                continue                     # that leg gained ground: the next one starts from here
            here = feet_now()
            budget = place_budget(Inventory().count("building"))     # the last leg spent some
            if not grounded and "no route" in (r.get("message") or "") and \
                    math.hypot(pos[0] - here[0], pos[2] - here[2]) <= 64:
                # The target's y was a guess (a trip kept the start's y 87 over ground at 71–79: "no route" in 0 s).
                # Its column is loaded now: stand on the real ground there and try again, once.
                grounded = True
                col = Region((pos[0], pos[1] - 32, pos[2]), (pos[0], pos[1] + 32, pos[2]))
                fy = ground_in_column(col.solid, pos[0], pos[2], pos[1])
                if fy is not None and fy != pos[1]:
                    log(f"   travel target {pos} had no route; retrying on the ground at y {fy}")
                    pos = (pos[0], fy, pos[2])
                    api.run({"type": "travel", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_,
                             "break": brk, "place": plc, "voidBridge": void, "placeBudget": budget,
                             "avoid": avoid}, wait=900, awaits="the retry's arrival is read before anything else is asked")
                    if there(api.get("/state"), pos, range_):
                        return _arrived(_from, pos, _began, True)
        # The walker says it could not get all the way. Whether that is a failure depends on where it left us:
        # a leg that ended thirty blocks nearer is progress, and the next round continues from there.
        return _arrived(_from, pos, _began, False, closer=walked_closer(_from, feet_now(), pos))
    for _ in range(attempts):
        # A jar without `travel`: one step at a time, and the same rule — the mod says whether it got there.
        api.run({"type": "goto", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_, "partial": True}, awaits="whether the step arrived (`there`) decides the next attempt")
        if there(api.get("/state"), pos, range_):
            return _arrived(_from, pos, _began, True)
    return _arrived(_from, pos, _began, False)


ARRIVE_CALLS = 8          # go_to calls one `arrive` may chain while each keeps gaining ground


def arrive(pos, policy, range_=1.5, **kw):
    """Get there, or raise `api.NavFailed`. The call a skill makes when it needs to BE somewhere.

    `go_to` answers in three ways — True (there), `Walked` (nearer, not there; falsy) and False (no nearer); a skill
    that read a leg stopped thirty blocks short as arrival began working on thin air. Here a leg that gained ground
    is followed by the next one, arrival returns True, and a leg that gained nothing is the failure it is. A pending interrupt ends the walk between legs.
    """
    began = time.time()
    got = False
    for _ in range(ARRIVE_CALLS):
        got = go_to(pos, policy, range_=range_, **kw)
        if got is True:
            return True
        if not moved(got):
            raise api.NavFailed(f"could not get to {tuple(pos)} (no nearer after walking)", pos=pos)
        api.check_interrupt(began, api.SOFT)
    raise api.NavFailed(f"still {math.dist(feet_now(), pos):.0f} blocks from {tuple(pos)} after {ARRIVE_CALLS} walks",
                        pos=pos)


def arrived(pos, policy, range_=1.5, **kw):
    """`arrive` for a caller that handles not getting there itself (ban the spot, try the next one): True or False,
    never a `Walked`."""
    try:
        return arrive(pos, policy, range_=range_, **kw)
    except api.NavFailed:
        return False


def dig_down_region(feet, depth):
    """The box `dig_down_tasks` reads: the shaft, its walls and what is under it."""
    x, y, z = feet
    return Region((x - 1, y - depth - 2, z - 1), (x + 1, y + 2, z + 1))


def dig_down_tasks(region, feet, depth, protected=(), use_ladders=False):
    """Pure: (tasks, depth that is safe) for digging straight down from `feet`, stopping above caves, lava and
    water. With ladders, one hangs on the shaft wall above the head after each step so the way back is a climb.
    Raises NotAvailable when not even one block down is safe."""
    x, y, z = feet
    safe = 0
    for i in range(1, depth + 1):
        cell, below = (x, y - i, z), (x, y - i - 1, z)
        if (region.unbreakable(cell) or region.hazard(cell) or not region.solid(below) or (x, y - i, z) in protected
                or any(region.hazard(add(cell, d)) for d in NEIGHBOURS6)):
            break
        safe = i
    if safe == 0:
        raise NotAvailable("unsafe to dig down here")
    tasks = []
    for i in range(1, safe + 1):
        cell = (x, y - i, z)
        if region.solid(cell):
            tasks.append(mine_task(cell))
        tasks.append({"type": "wait", "ticks": 6})
        if use_ladders:
            wall = next(((x + dx, y - i + 2, z + dz) for dx, dz in DIRS4 if region.solid((x + dx, y - i + 2, z + dz))),
                        None)
            if wall:
                tasks.append({"type": "place", "item": "minecraft:ladder", "x": x, "y": y - i + 2, "z": z,
                              "against": {"x": wall[0], "y": wall[1], "z": wall[2]}})
    return tasks, safe


def dig_down(depth, policy, use_ladders):
    """Straight down under the feet (`dig_down_tasks`), run as one chain. Returns how deep it went."""
    here = feet_now()
    tasks, safe = dig_down_tasks(dig_down_region(here, depth), here, depth, policy.protected, use_ladders)
    # Stop at the first failure: a mine that couldn't happen leaves stone where the next ladder would go, and the
    # rest of the chain then fails block by block ("position is occupied").
    results = api.run_chain(tasks, stop_on_failure=True, before_segment=policy.before_segment)
    if any(r["status"] != "succeeded" for r in results):
        raise NotAvailable("digging down stopped: a block couldn't be reached")
    return safe


def sweep(ctx, radius=6, only=(), wait=30, tries=2):
    """Pick up what is lying around; when something lies where nothing can stand, make a way and sweep again.

    Every collector in the package used to fire one `collect` and read the result as the whole truth, so "1 items
    unreachable" — beef up a tree, ore down a hole, a drop across a fence — meant the work had produced nothing.
    """
    for attempt in range(max(1, tries)):
        try:
            return api.run({"type": "collect", "radius": radius, **({'only': list(only)} if only else {})}, wait=wait, awaits="an Unreachable answer names the cells a way is made to before the next sweep")
        except api.Unreachable as out:
            if attempt + 1 >= tries or not way_to(ctx, out.cells or [feet_now()], range_=1.5):
                raise
    return None


# One round asks about dozens of targets and the answer cannot change while the body stands still, so the
# game is asked once per (target, policy, nodes) and the answer is kept for the round. Cleared by `forget_routes`.
from .world import ROUTES as _ROUTES  # noqa: E402  (the round's route answers, read by the cost model too)
# How many route questions one round may put to the game. A `/plan` is a real pathfinding search on the client
# thread — tens of milliseconds when it succeeds, seconds when it has to give up — so asking it once per candidate
# priced a round in minutes and the body stood still through all of it. The budget is what makes "ask the world"
# affordable: the few questions that decide something get the truth, the rest fall back to the straight line and
# say so (unknown, never "no way").
_ROUTE_BUDGET = [0]
ROUTES_PER_ROUND = 6


def route_s(cell, policy, range_=1.5, nodes=6000):
    """(can we get there, seconds it would take) — THE GAME'S answer, not one assembled here.

    Physics belongs to the world: what counts as a standing spot, what can be stepped up, broken, bridged or
    pillared, and how long the walk takes. The mod plans with the very pathfinder it moves with (`/plan`), so
    asking it is both cheaper to maintain and impossible to disagree with. What stays on this side is what to do
    with the answer — pricing, ranking, giving up.

    Three answers, not two: (True, seconds) there is a way, (False, seconds-or-None) there is none, and
    (None, None) NOBODY COULD SAY — no game, or a recorded round whose tape never asked this. Unknown is not "no":
    a replayed round must not conclude that a place is unreachable because the recording did not happen to ask,
    and pricing falls back to the straight line rather than to a refusal.
    """
    key = (tuple(cell), bool(policy.allow_dig), bool(policy.allow_build), float(range_), int(nodes))
    hit = _ROUTES.get(key)
    if hit is not None:
        return hit
    if _ROUTE_BUDGET[0] >= ROUTES_PER_ROUND:
        return (None, None)          # this round has asked enough: unknown, and the caller estimates instead
    _ROUTE_BUDGET[0] += 1
    try:
        r = api.get(f"/plan?to={cell[0]},{cell[1]},{cell[2]}&range={range_}"
                    f"&break={'true' if policy.allow_dig else 'false'}"
                    f"&place={'true' if policy.allow_build else 'false'}&nodes={nodes}")
        out = (bool(r.get("found")), r.get("seconds"))
    except tape.ReplayMiss:
        return (None, None)                 # a recorded round: not an answer, and never cached as one
    except McError as err:
        api.swallowed("nav.route_s", err)
        out = (None, None)
    _ROUTES[key] = out
    return out


def forget_routes():
    """New round, new body position: the routes priced from the old one say nothing about this one."""
    _ROUTES.clear()
    _ROUTE_BUDGET[0] = 0


def plainly_below(feet, cell, max_drop=None):
    """Pure: `cell` is further below the feet than a safe drop and nearer sideways than it is deep — a cliff or a
    sky platform's floor, not a slope a walk goes down. A tree 100 blocks under the platform, 20 across."""
    max_drop = SAFE_DROP if max_drop is None else max_drop
    deep = feet[1] - cell[1]
    return deep > max_drop and math.dist((feet[0], feet[2]), (cell[0], cell[2])) < deep


def reachable(cell, policy, range_=1.5, nodes=6000, feet=None):
    """Is there a way to `cell` at all? The first half of `route_s`. The one judgement a skill picking where to
    walk asks (wood.chop's trunk, explore.seek_blocks's hits).

    Unknown counts as "maybe": only a definite No from the game may stand behind a ban, because the alternative
    is banning a place because nobody asked — unless the cell is plainly below a drop from `feet`
    (`plainly_below`): that unknown is a no (chop walked off a sky platform to a tree below, the budget spent).
    """
    found, seconds = route_s(cell, policy, range_=range_, nodes=nodes)
    if found is None:
        found = not (feet is not None and plainly_below(feet, cell))
    return found, seconds


def way_to(ctx, cells, range_=2.0):
    """Get these cells within working range, and say whether they now ARE. Walk if there is a way, make one if not.

    The one answer to "could not get to it" (`api.Unreachable`), wherever it is raised — ore inside rock, beef up
    a tree, a chest behind a wall. What it returns is not "something happened" but the only thing a caller can act
    on: can the work be done now? Walking three blocks nearer a stone sealed in earth changes nothing, and
    reporting that as a way made is how a retry loop spins — the caller asks again, the mod refuses again, and
    after three refusals sixty perfectly good stones are banned and the goal says there is no stone for 48 blocks.

    Whether a cell is within reach is the GAME'S question (`/plan`), not one to answer from a block region here.
    """
    cells = {tuple(int(round(v)) for v in c) for c in cells}
    if not cells:
        return False
    near = min(cells, key=lambda p: math.dist(p, feet_now()))
    if arrived(near, ctx.policy, range_=range_, attempts=1) and reachable(near, ctx.policy, range_)[0]:
        return True                                    # the walk was enough
    if not (ctx.policy.allow_dig or ctx.policy.allow_build):
        return False
    # Making a way IS travel with a pickaxe: the mod breaks and places as it goes. There is nothing for this side
    # to plan — asking the body to walk there, with digging allowed, is the whole of it.
    for cell in sorted(cells)[:4]:
        if arrived(cell, ctx.policy, range_=range_, attempts=1) and reachable(cell, ctx.policy, range_)[0]:
            return True
    return False

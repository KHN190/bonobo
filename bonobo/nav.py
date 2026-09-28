"""Getting from A to B: ASKING THE GAME to, and pricing what it answers. There is no pathfinder here any more. There were two — the mod's, which moves the body (walking, digging, bridging, pillaring, ladders), and one in this file, which planned routes the body then did not take. Every disagreement between them became a bug: water in the floor priced as flat ground, ore two blocks inside rock "unreachable", a village room behind a door the walker would have opened. Physics is the world's, so: route_s(cell, policy)   /plan — is there a way, and how many seconds go_to(pos, policy)      travel — the mod walks, digs and bridges its own way there way_to(ctx, cells)      the answer to "could not get to it": walk with digging allowed, then check What stays on this side is the decision: what is worth walking to, what a walk is worth, and when to give up."""

import math
import re
import time
from dataclasses import dataclass, field

from . import api, tape, arbiter, combat_model, roads
from .api import McError, NotAvailable, log
from .data import GROUPS, FOOD, EYE_HEIGHT, HOLD_MARGIN, NAV_NODES, REACH, TASK_WAIT_S, WORK_REACH  # noqa: F401  (WORK_REACH: nav.WORK_REACH)
from .world import NEIGHBOURS6, Inventory, Region, add, feet

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

def waypoints(here, target, leg=40):
    """Pure: points every `leg` blocks (horizontal) from here to target, height interpolated, ending at target."""
    dx, dz = target[0] - here[0], target[2] - here[2]
    dist = math.hypot(dx, dz)
    n = max(1, math.ceil(dist / leg))
    return [(round(here[0] + dx * k / n), round(here[1] + (target[1] - here[1]) * k / n), round(here[2] + dz * k / n))
            for k in range(1, n)] + [tuple(target)]

FARMLAND_TOP = 0.9375      # farmland is 15/16 of a block: its top face is below the cell's top

def use_on_top(item, cell, top=1.0):
    """Pure: the task that uses `item` on the top face of `cell` (till, sow, pour, light a portal); `top`: that face's
    height in the cell — aimed inside it, so a block lower than a full cube (farmland) is hit, not the air above."""
    # under a lower face, aimed well inside it: 0.02 under the farmland's top left the look's own error sailing over
    # the cell (the jar raycasts along the look, not to the point): "no block under the crosshair" on 7 of 8 sows
    y = cell[1] + (1.0 if top >= 1.0 else top - 0.15)
    return {"type": "use_item", "item": item, "x": cell[0] + 0.5, "y": y, "z": cell[2] + 0.5, "onBlock": True}

def use_on_face(item, cell, face):
    """Pure: the task that uses `item` on the side face of `cell` that points along `face` ((dx, dz)): aimed at that
    face's centre — a bucket used there pours into the cell beside it."""
    return {"type": "use_item", "item": item, "x": cell[0] + 0.5 + 0.5 * face[0], "y": cell[1] + 0.5,
            "z": cell[2] + 0.5 + 0.5 * face[1], "onBlock": True}

def first_solid(region, eye, point, past=0.3):
    """Pure: the first solid cell a straight look from `eye` through `point` meets (a little past the point: the
    point sits on a face), or None — what the jar's click raycast would hit (ray_first)."""
    return ray_first(region, eye, point, past)

def mine_task(c, collect=False, down=False):
    """A mine task; `down`: the block is under the feet on purpose (a dig down) — the jar (≥ 0.1.58) then may stand
    on its own column, which ordinary mining never does."""
    return {"type": "mine", "x": c[0], "y": c[1], "z": c[2], "collect": collect, "requireDrops": False,
            **({"down": True} if down else {})}

# -- batches: many cells as one chain of single tasks (the jar's SequenceTask, now planned here) ------------------
def mine_order(cells, start=None):
    """Pure: the order a batch mines `cells` — the columns nearest `start` (else the first cell given) first, each
    top down (never a cell while another of the batch sits above it). A sort, not a walk: what is left after any
    step keeps its order, so a batch rebuilt from the world after an interruption is the rest of the first."""
    cells = [tuple(c) for c in cells]
    here = tuple(start) if start is not None else (cells[0] if cells else None)
    return sorted(cells, key=lambda c: ((c[0] - here[0]) ** 2 + (c[2] - here[2]) ** 2, -c[1], c[0], c[2]))


def build_order(cells, start=None):
    """Pure: the order a batch places `cells` — the lowest layer first (every block has support), in it the nearest
    to `start` (else the first cell given). A sort: what is left after any step keeps its order."""
    cells = [tuple(c) for c in cells]
    here = tuple(start) if start is not None else (cells[0] if cells else None)
    return sorted(cells, key=lambda c: (c[1], sum((c[i] - here[i]) ** 2 for i in range(3)), c[0], c[2]))


SWEEP_IDLE = 10        # ticks a batch's closing sweep waits for a drop before it ends (the jar's mine_many used 10)


def batch_sweep(cells, only=None):
    """Pure: a batch's closing pickup — one collect centred on the cells (their rounded mean), reaching the farthest
    of them + 5: what fell near the batch, not a walk per item."""
    cells = [tuple(c) for c in cells]
    if not cells:
        return {"type": "collect", "radius": 5, "idle": SWEEP_IDLE, **({"only": list(only)} if only else {})}
    centre = tuple(int(round(sum(c[i] for c in cells) / len(cells))) for i in range(3))
    reach = 5 + max(math.dist(c, centre) for c in cells)
    return {"type": "collect", "x": centre[0], "y": centre[1], "z": centre[2], "radius": round(reach, 2),
            "idle": SWEEP_IDLE, **({"only": list(only)} if only else {})}


def mine_batch(cells, start=None, require_drops=False, collect=True, only=None):
    """Pure: many cells to break as one chain — single mines in mine_order, the closing sweep (batch_sweep) last when `collect`."""
    order = mine_order(cells, start)
    tasks = [{"type": "mine", "x": c[0], "y": c[1], "z": c[2], "collect": False, "requireDrops": require_drops}
             for c in order]
    return tasks + ([batch_sweep(order, only)] if collect else [])


def build_batch(blocks, start=None):
    """Pure: blocks to place ([{x, y, z, item, (against, facing)}]) as one chain of single places in build_order."""
    by = {(b["x"], b["y"], b["z"]): b for b in blocks}
    return [dict(by[c], type="place") for c in build_order(list(by), start)]


# the jar's answers for "the body cannot get at this block from here" (fail fast after two in a row)
STEP_UNREACHABLE = ("cannot reach", "no line of sight", "no path found", "cannot hold a stand spot")
NO_STAND = "cannot hold a stand spot"         # the same spot again is the same flip: never retried


def run_cells(kind, tasks, then=None, wait=TASK_WAIT_S):
    """One batch as chains of single tasks (the jar's mine_many / build, planned here): sent in order; a failed
    step retried once at the end (never a no-stand: the same spot is the same flip); two unreachable in a row give
    the rest back as failures; `then` (the closing sweep) after. One result in the shape a batch answered —
    {status, type, message 'N of M steps failed: …', result: {succeeded, total, failures: [{x, y, z, reason}]}} —
    and the reach refusal raised as api.Unreachable, as api.run does."""
    began = time.time()
    pending, retry, failures = list(tasks), [], []
    succeeded, in_a_row, retrying = 0, 0, False

    def failed(t, reason):
        failures.append({"x": t.get("x"), "y": t.get("y"), "z": t.get("z"), "reason": reason})
    while pending or (retry and not retrying):
        if not pending:
            pending, retry, retrying = retry, [], True
        results = api.run_chain(pending, stop_on_failure=True, wait=wait)
        k = next((i for i, r in enumerate(results) if r.get("status") != "succeeded"), None)
        if k is None:
            succeeded += len(pending)
            pending, in_a_row = [], 0
            continue
        succeeded += k
        bad, msg = pending[k], results[k].get("message") or ""
        pending = pending[k + 1:]
        in_a_row = in_a_row + 1 if any(w in msg for w in STEP_UNREACHABLE) else 0
        if in_a_row >= 2:
            for t in pending + retry:
                failed(t, f"skipped after repeated unreachable blocks: {msg}")
            pending, retry, retrying = [], [], True
        if not retrying and not msg.startswith(NO_STAND):
            retry.append(bad)
        else:
            failed(bad, msg)
    if then is not None:
        api.run_chain([then], wait=60)
    total = len(tasks)
    r = {"type": kind, "status": "failed" if failures else "succeeded",
         "message": (f"{len(failures)} of {total} steps failed: {failures[0]['reason']}" if failures
                     else f"{kind} finished"),
         "seconds": round(time.time() - began, 2),
         "result": {"succeeded": succeeded, "total": total, "failures": failures}}
    api.detail(f"  {kind:<9} {r['status']:<9} {r['message']} ({r['seconds']}s)")
    api.out_of_reach(r)
    return r


_features = None
WALK_EAT_BELOW = 18        # hunger points: the jar eats on the way below this (regen stops at 18), `autoeat_policy`

def autoeat_policy():
    """Pure: what the jar eats on the way and when (jar ≥ 0.1.46): below WALK_EAT_BELOW, the best food first."""

    return {"below": WALK_EAT_BELOW, "foods": [f"minecraft:{f}" for f in FOOD]}

def mod_features():
    """Movement features of the running mod: 'pillar' task and ladders placed in the body's own cell (≥ 0.1.15)."""
    global _features
    if _features is None:
        try:
            v = tuple(int(x) for x in re.findall(r"\d+", str(api.status().get("version", "0")))[:3])
        except McError:
            return set()
        # "ladder_in_cell" off: the server rejects the plate 0.02 off-centre; climb by pillaring
        _features = {"pillar"} if v >= (0, 1, 15) else set()
        if v >= (0, 1, 17):
            _features.add("travel")   # the mod plans and executes walk/dig/bridge/pillar routes itself
        if v >= (0, 1, 40):
            _features.add("approach_dig")   # mine/place/use dig their own way when walking finds none (ApproachTask)
        if v >= (0, 1, 50):
            _features.add("input")          # keys held until the body stands (the "input" task): climb_out
        if v >= (0, 1, 63):
            _features.add("hold_use")       # the input task holds "use" (a shield raised from the offhand)
        if v >= (0, 1, 46):
            _features.add("autoeat")        # the jar eats while only walking, on the policy /autoeat sets
            # set once at first contact, so whoever drives the jar walks with it
            try:
                api.post("/autoeat", autoeat_policy())
            except McError as e:
                log(f"autoeat policy not set: {e}")
    return _features

def building_item():
    inv = Inventory()
    options = [b for b in GROUPS["building"] if inv.usable(b)]
    return max(options, key=inv.usable) if options else None

def ground_in_column(solid, x, z, y_hint, span=32):
    """Pure: standing height in column (x, z) within y_hint ± span, or None."""

    for y in range(y_hint + span, y_hint - span - 1, -1):
        if solid((x, y, z)) and not solid((x, y + 1, z)) and not solid((x, y + 2, z)):
            return y + 1
    return None

LEG = 48   # blocks per travel leg on long trips
ROAD_MEM = None   # the brain's memory: travelled legs are kept as a road network (roads.py) and reused

BLOCK_RESERVE = 16   # blocks kept back from travel: shelter walls, a pillar out of a hole, the next bridge

def place_budget(stock):
    """Pure: how many blocks a trip may spend on bridges and pillars."""

    return max(0, stock - BLOCK_RESERVE) if stock > 2 * BLOCK_RESERVE else stock // 2

# below this, walking on ends runs: no regeneration, and the next hit is the last
MIN_WALK_HP = 6.0

def safe_destination(pos, hazards=None, clear=1.0):
    """Pure: `pos`, or a nearby spot clear of every hazard when `pos` sits inside one."""

    hazards = [h if len(h) > 2 else (h[0], h[1], (0.0, 0.0, 0.0)) for h in (hazards or [])]
    if not hazards or combat_model.min_tti(pos, hazards) == float("inf"):
        return pos
    spot, slack = combat_model.best_step(pos, hazards)
    return spot if slack is not None and slack > 0 else None

PLAYER_SPEED = 4.3

def _arrived(start, target, began, ok, closer=False):
    """Feed one walk back into the terrain estimate, and say what the leg achieved."""

    try:
        from . import field
        straight = math.dist(start, target) / PLAYER_SPEED
        if ok and straight > 0.5:
            state = api.get("/state")
            field.TERRAIN.observed(field.bucket_of(state), straight, time.time() - began)
    except api.McError:
        pass                # the one read failed (the game away): the estimate waits for the next walk
    if ok:
        return True
    return Walked(math.dist(start, target) - math.dist(feet(), target)) if closer else False

class Walked(float):
    """A leg that got nearer without arriving: falsy, never arrival."""

    def __bool__(self):
        return False

    def __repr__(self):
        return f"Walked({float(self):.0f} blocks nearer)"

# a walk counts as progress once this much nearer: a leg that stops closer is not "unreachable" (that cooled every deep target)
PROGRESS_BLOCKS = 2.0
# legs one call may walk: far targets in one errand, yet the planner gets the body back
LEGS = 6

AVOID_RADIUS = 64        # protected cells this near a walk's ends go with it: the jar may not dig or build in them
AVOID_MAX = 4000
# task types whose approach may dig and build: each carries the "avoid" list
APPROACHING = ("mine", "place", "use")

def avoid_cells(protected, *near):
    """Pure: the protected cells within AVOID_RADIUS of `near`: the "avoid" list a digging walk carries."""

    return [{"x": c[0], "y": c[1], "z": c[2]} for c in sorted(protected)
            if any(math.dist(c, n) <= AVOID_RADIUS for n in near)][:AVOID_MAX]

def with_avoid(task, protected):
    """Pure: `task` with its "avoid" when its type approaches by digging (APPROACHING) and it names none; else the task as it was."""

    if task.get("type") not in APPROACHING or "avoid" in task:
        return task
    return {**task, "avoid": avoid_cells(protected, (task["x"], task["y"], task["z"]))}

ARRIVE_SLACK = 0.5       # the walker's own margin past `range` (the mod counts arrived within range + 0.5)

def there(state, pos, range_):
    """Pure: within range_ + ARRIVE_SLACK of `pos` in 3-D (block targets by the walker's own test)."""

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
    """Pure: the body stands on `land` (its feet cell), dry — on the ground and out of the water."""

    return bool(state.get("onGround")) and not state.get("inWater") and there(state, land, range_)

_OFFS = ((0, 0), (0.3, 0), (-0.3, 0), (0, 0.3), (0, -0.3))      # where the body may settle in its cell
_FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def ray_first(region, eye, point, past=0.1, through=(), target=None):
    """Pure: the jar's raycast from `eye` toward `point` (`past` beyond it) — the first cell met that is solid (or is
    `target`), None when the ray ends in air. Cells in `through` (broken in the same batch) do not block. The one ray
    walk: holds (a mine's sight) and first_solid (a click's aim) both read it."""
    d = [point[i] - eye[i] for i in range(3)]
    n = math.sqrt(sum(v * v for v in d)) or 1.0
    end = [point[i] + d[i] / n * past for i in range(3)]
    d = [end[i] - eye[i] for i in range(3)]
    c = [math.floor(v) for v in eye]
    step = [1 if v > 0 else -1 for v in d]
    t_max, t_delta = [], []
    for i in range(3):
        if d[i] == 0:
            t_max.append(math.inf)
            t_delta.append(math.inf)
        else:
            edge = c[i] + (1 if d[i] > 0 else 0)
            t_max.append((edge - eye[i]) / d[i])
            t_delta.append(abs(1 / d[i]))
    while True:
        here = tuple(c)
        if (target is not None and here == tuple(target)) or (here not in through and region.solid(here)):
            return here
        i = min(range(3), key=lambda k: t_max[k])
        if t_max[i] > 1.0:
            return None
        c[i] += step[i]
        t_max[i] += t_delta[i]

def _ray_hits(region, eye, point, cell, reach, through=()):
    """Pure: the jar's rayTo — from `eye` toward `point` (0.1 past it), is `cell` the first solid block met, within
    `reach`?"""
    return math.dist(eye, point) <= reach and ray_first(region, eye, point, 0.1, through, target=cell) == tuple(cell)


def holds(region, feet_at, cell, down=False, reach=REACH, through=()):
    """Pure: the jar's MineTask.holds — can the body standing at `feet_at` break `cell`? Never from the block's own
    column above it (unless `down`: digging down on purpose), and the block in sight — its centre or a face centre,
    the first solid thing hit — from the eye at the cell's centre and 0.3 off it each way, within the reach less
    0.5. Distance alone was not it: a pit 2 down is 'within 4.5' of an ore at its rim it cannot see."""
    fx, fy, fz = feet_at
    cx, cy, cz = cell
    if not down and (fx, fz) == (cx, cz) and fy > cy:
        return False
    centre = (cx + 0.5, cy + 0.5, cz + 0.5)
    points = [centre] + [(centre[0] + d[0] * 0.45, centre[1] + d[1] * 0.45, centre[2] + d[2] * 0.45) for d in _FACES]
    for ox, oz in _OFFS:
        eye = (fx + 0.5 + ox, fy + EYE_HEIGHT, fz + 0.5 + oz)
        if not any(_ray_hits(region, eye, p, cell, reach - HOLD_MARGIN, through) for p in points):
            return False
    return True


CLIMB_REACH = 2.0      # horizontal blocks from the bank's cell within which a swimmer presses into it
CLIMB_TICKS = 40

def climb_out_tasks(state, land):
    """Pure: face the bank, then hold forward+jump until standing (jar ≥ 0.1.50): the walker never climbs out; [] when ashore."""

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
    """Pure: the body is held up — on the ground, in water or on a ladder — not in the air."""

    return bool(state.get("onGround") or state.get("inWater") or state.get("climbing"))

def moved(got):
    """Did a `go_to` answer move us: there (True), or a leg that gained ground (`Walked`)?"""

    return got is True or isinstance(got, Walked)

def walked_closer(start, here, target):
    """Did this leg actually bring us nearer the target? In blocks, against where it began."""
    return math.dist(start, target) - math.dist(here, target) >= PROGRESS_BLOCKS

# what a walk may do by its purpose (break, place, bridge over the void): only a walk with a known far side lays floor over a drop
MOVES = {"work": (True, True, True), "explore": (True, True, False), "evade": (True, True, False)}
SAFE_DROP = 3        # blocks a walk may drop onto dry ground unhurt; deeper only into water (`terrain.landing`)

def landing(region, here, spot, max_drop=None, least=2):
    """Pure: the farthest cell a straight walk toward `spot` reaches on connected ground (drops ≤ max_drop, or into water)."""

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
    """Pure: (may break, may place, may bridge over the void) for a walk made for `purpose`, within the round's policy."""

    brk, plc, void = MOVES[purpose]
    return brk and bool(getattr(policy, "allow_dig", True)), plc and bool(getattr(policy, "allow_build", True)), void

def go_to(pos, policy, range_=1.5, attempts=3, min_hp=MIN_WALK_HP, avoid_hazards=True, purpose="work"):
    """Walk; when the walker can't get there, build/dig a route toward the target."""

    pos = tuple(pos)
    if not arbiter.BODY.owns("nav.go_to"):
        # a fight holds the body: an interruption, not "no way there" (read as False it banned the vein)
        raise api.FightHolds(f"nav.go_to {pos}: a fight holds the body")
    _began, _from = time.time(), feet()
    if avoid_hazards:
        pos = _clear_of_hazards(pos)
        if pos is None:
            return False
    if "travel" in mod_features():
        return _travel(pos, policy, range_, attempts, min_hp, purpose, _from, _began)
    for _ in range(attempts):
        # a jar without `travel`: one step at a time, the same rule
        api.run({"type": "goto", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_, "partial": True}, awaits="whether the step arrived (`there`) decides the next attempt")
        if there(api.get("/state"), pos, range_):
            return _arrived(_from, pos, _began, True)
    return _arrived(_from, pos, _began, False)

def _clear_of_hazards(pos):
    """The target, or the nearest spot outside every hazard (combat_model.hazards); None when there is none."""
    hz = combat_model.hazards()
    if not hz:
        return pos
    safe = safe_destination(pos, hz)
    if safe is None:
        log(f"   every spot near {pos} is inside something dangerous: not walking there")
        return None
    if safe != pos:
        log(f"   {pos} sits inside a hazard: walking to {safe} instead "
            f"(slack {combat_model.slack_at(safe, hz, pos)}s)")
        return tuple(safe)
    return pos

def _known_roads(here, pos, policy, purpose):
    """Walk the known roads toward `pos` where they beat the direct way; where the body ends up."""
    known = ROAD_MEM.data.setdefault("roads", {}).setdefault(api.get("/state")["dimension"], [])
    for wp in roads.route(known, here, pos)[:-1]:
        t0 = time.time()
        if not moved(go_to(wp, policy, range_=6, attempts=1, purpose=purpose)):
            break
        roads.add_leg(known, here, feet(), time.time() - t0, time.time())
        here = feet()
    return here

def _long_trip(here, pos, policy, min_hp, purpose, _from, _began):
    """A long trip in legs of LEG blocks: (where it ended, None), or (None, the walk's answer) when it stopped."""
    start, t_start = here, time.time()
    for hop in waypoints(here, pos, LEG)[:-1]:
        if min_hp is not None and api.get("/state")["health"] < min_hp:
            log(f"   travel stopped at {min_hp} hp: falling back instead of walking on")
            return None, False
        if not moved(go_to(hop, policy, range_=6, attempts=1, min_hp=min_hp, purpose=purpose)):
            # this hop got nowhere; the trip is over only if we are no nearer than at its start
            return None, _arrived(_from, pos, _began, False, closer=walked_closer(_from, feet(), pos))
    here = feet()
    # per-leg accounting tells replanning from slow movement
    _walked = math.dist(start, here)
    _took = time.time() - t_start
    log(f"   leg: {_walked:.0f} blocks in {_took:.1f}s ({_took / max(_walked, 1) * 100:.0f} s/100)")
    if ROAD_MEM is not None:
        # This trip is now a known road (timed), for the way back and for later trips.
        roads.add_leg(ROAD_MEM.data.setdefault("roads", {}).setdefault(api.get("/state")["dimension"], []),
                      start, here, time.time() - t_start, time.time())
    return here, None

def _travel(pos, policy, range_, attempts, min_hp, purpose, _from, _began):
    """The mod plans and runs the whole route, so Python never plans moves the walker can't make."""
    here = feet()
    if ROAD_MEM is not None and math.hypot(pos[0] - here[0], pos[2] - here[2]) > LEG:
        here = _known_roads(here, pos, policy, purpose)
    if math.hypot(pos[0] - here[0], pos[2] - here[2]) > LEG:
        here, stopped = _long_trip(here, pos, policy, min_hp, purpose, _from, _began)
        if here is None:
            return stopped
    budget = place_budget(Inventory().count("building"))
    avoid = avoid_cells(policy.protected, here, pos)
    grounded = False
    brk, plc, void = may_alter(purpose, policy)
    # keep walking while each leg brings us nearer; "target unreachable" at the leg's end is not failure
    for _ in range(max(attempts, LEGS)):
        api.at_boundary()                # nightfall between legs: never inside a walk
        was = feet()
        r = api.run({"type": "travel", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_,
                     "break": brk, "place": plc, "voidBridge": void, "placeBudget": budget,
                     "avoid": avoid}, wait=TASK_WAIT_S, awaits="where the leg left the body decides the next leg (walked_closer, the retry on the ground)")
        if there(api.get("/state"), pos, range_):
            return _arrived(_from, pos, _began, True)
        if walked_closer(was, feet(), pos):
            budget = place_budget(Inventory().count("building"))
            continue                     # that leg gained ground: the next one starts from here
        here = feet()
        budget = place_budget(Inventory().count("building"))     # the last leg spent some
        if grounded or "no route" not in (r.get("message") or "") or \
                math.hypot(pos[0] - here[0], pos[2] - here[2]) > 64:
            continue
        # the target's y was a guess: stand on the column's real ground and try once more
        grounded = True
        col = Region((pos[0], pos[1] - 32, pos[2]), (pos[0], pos[1] + 32, pos[2]))
        fy = ground_in_column(col.solid, pos[0], pos[2], pos[1])
        if fy is None or fy == pos[1]:
            continue
        log(f"   travel target {pos} had no route; retrying on the ground at y {fy}")
        pos = (pos[0], fy, pos[2])
        api.run({"type": "travel", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_,
                 "break": brk, "place": plc, "voidBridge": void, "placeBudget": budget,
                 "avoid": avoid}, wait=TASK_WAIT_S, awaits="the retry's arrival is read before anything else is asked")
        if there(api.get("/state"), pos, range_):
            return _arrived(_from, pos, _began, True)
    # a leg that ended nearer is progress; the next round continues from there
    return _arrived(_from, pos, _began, False, closer=walked_closer(_from, feet(), pos))

ARRIVE_CALLS = 8          # go_to calls one `arrive` may chain while each keeps gaining ground

def arrive(pos, policy, range_=1.5, **kw):
    """Get there, or raise `api.NavFailed`."""

    began = time.time()
    got = False
    for _ in range(ARRIVE_CALLS):
        got = go_to(pos, policy, range_=range_, **kw)
        if got is True:
            return True
        if not moved(got):
            raise api.NavFailed(f"could not get to {tuple(pos)} (no nearer after walking)", pos=pos)
        api.check_interrupt(began, api.SOFT)
    raise api.NavFailed(f"still {math.dist(feet(), pos):.0f} blocks from {tuple(pos)} after {ARRIVE_CALLS} walks",
                        pos=pos)

def arrived(pos, policy, range_=1.5, **kw):
    """`arrive` for a caller that handles failure itself: True or False, never a `Walked`."""

    try:
        return arrive(pos, policy, range_=range_, **kw)
    except api.NavFailed:
        return False

def dig_down_region(feet, depth):
    """The box `dig_down_tasks` reads: the shaft, its walls and what is under it."""
    x, y, z = feet
    return Region((x - 1, y - depth - 2, z - 1), (x + 1, y + 2, z + 1))

def dig_down_tasks(region, feet, depth, protected=(), use_ladders=False, dug_to=None):
    """Pure: (tasks, depth that is safe) for digging straight down from `feet`, stopping above caves, lava and water."""

    x, y, z = feet
    safe = 0
    for i in range(1, depth + 1):
        cell, below = (x, y - i, z), (x, y - i - 1, z)
        # down to where the body stands (`dug_to`, a resumed dig), the open cell under is our own shaft, not a cave
        shaft = dug_to is not None and i < depth and below[1] >= dug_to
        if (region.unbreakable(cell) or region.hazard(cell) or not (region.solid(below) or shaft)
                or (x, y - i, z) in protected
                or any(region.hazard(add(cell, d)) for d in NEIGHBOURS6)):
            break
        safe = i
    if safe == 0:
        raise NotAvailable("unsafe to dig down here")
    tasks = []
    for i in range(1, safe + 1):
        cell = (x, y - i, z)
        if region.solid(cell):
            tasks.append(mine_task(cell, down=True))
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
    here = feet()
    tasks, safe = dig_down_tasks(dig_down_region(here, depth), here, depth, policy.protected, use_ladders)
    # stop at the first failure: a mine that failed leaves stone where the next ladder goes
    results = api.run_chain(tasks, stop_on_failure=True, before_segment=policy.before_segment)
    if any(r["status"] != "succeeded" for r in results):
        raise NotAvailable("digging down stopped: a block couldn't be reached")
    return safe

def sweep(ctx, radius=6, only=(), wait=30, tries=2):
    """Pick up what is lying around; when something lies where nothing can stand, make a way and sweep again."""

    for attempt in range(max(1, tries)):
        try:
            return api.run({"type": "collect", "radius": radius, **({'only': list(only)} if only else {})}, wait=wait, awaits="an Unreachable answer names the cells a way is made to before the next sweep")
        except api.Unreachable as out:
            if attempt + 1 >= tries or not way_to(ctx, out.cells or [feet()], range_=1.5):
                raise
    return None

# the game is asked once per (target, policy, nodes) per round; cleared by `forget_routes`
from .world import ROUTES as _ROUTES  # noqa: E402  (the round's route answers, read by the cost model too)
# route questions one round may ask the game: a failing /plan costs seconds, so the rest fall back to "unknown"
_ROUTE_BUDGET = [0]
ROUTES_PER_ROUND = 6

def route_s(cell, policy, range_=1.5, nodes=NAV_NODES):
    """(can we get there, seconds it would take) — THE GAME'S answer, not one assembled here."""

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
    """Pure: `cell` is deeper below the feet than a safe drop and nearer sideways than deep — a cliff, not a slope."""

    max_drop = SAFE_DROP if max_drop is None else max_drop
    deep = feet[1] - cell[1]
    return deep > max_drop and math.dist((feet[0], feet[2]), (cell[0], cell[2])) < deep

def reachable(cell, policy, range_=1.5, nodes=NAV_NODES, feet=None):
    """Is there a way to `cell` at all?"""

    found, seconds = route_s(cell, policy, range_=range_, nodes=nodes)
    if found is None:
        found = not (feet is not None and plainly_below(feet, cell))
    return found, seconds

def way_to(ctx, cells, range_=2.0):
    """Get these cells within working range, and say whether they now ARE."""

    cells = {tuple(int(round(v)) for v in c) for c in cells}
    if not cells:
        return False
    near = min(cells, key=lambda p: math.dist(p, feet()))
    if arrived(near, ctx.policy, range_=range_, attempts=1) and reachable(near, ctx.policy, range_)[0]:
        return True                                    # the walk was enough
    if not (ctx.policy.allow_dig or ctx.policy.allow_build):
        return False
    # making a way is travel with digging allowed: nothing to plan here
    for cell in sorted(cells)[:4]:
        if arrived(cell, ctx.policy, range_=range_, attempts=1) and reachable(cell, ctx.policy, range_)[0]:
            return True
    return False

# -- stairs and pits: a body goes down by a staircase it can walk back up, never a 1-wide shaft
STAIR_STEPS = 8          # steps one staircase segment digs before the ground is read again

def stair_dir(feet, target):
    """Pure: the horizontal step toward `target` along its longer axis ((1, 0) when straight below)."""
    dx, dz = target[0] - feet[0], target[2] - feet[2]
    if dx == 0 and dz == 0:
        return (1, 0)
    return ((1 if dx > 0 else -1), 0) if abs(dx) >= abs(dz) else (0, (1 if dz > 0 else -1))

def stair_down_tasks(region, feet, target, protected=(), max_steps=STAIR_STEPS):
    """Pure: a 1-wide staircase from `feet` down toward `target` — each step one over and one down, its three cells
    (feet, head, the head room the walk down passes) cleared, a solid tread under it — until the feet are at the
    target's level or `max_steps`; stops before a fluid, an unbreakable or protected cell, or a tread that is not
    there. [] when not below or nothing can be dug."""
    x, y, z = feet
    if target[1] >= y - 1:
        return []
    d = stair_dir(feet, target)
    tasks = []
    for k in range(1, max_steps + 1):
        fx, fy, fz = x + d[0] * k, y - k, z + d[1] * k
        cells = [(fx, fy, fz), (fx, fy + 1, fz), (fx, fy + 2, fz)]
        tread = (fx, fy - 1, fz)
        if not all(region.inside(c) for c in cells + [tread]):
            break
        if any(region.hazard(c) or region.hazard(add(c, n)) for c in cells for n in NEIGHBOURS6) \
                or any(region.unbreakable(c) or c in protected for c in cells) or not region.solid(tread):
            break
        tasks += [mine_task(c) for c in cells if region.solid(c)]
        tasks.append({"type": "goto", "x": fx, "y": fy, "z": fz, "range": 0.5})
        if fy <= target[1] + 1:
            break
    return tasks

def in_pit(region, feet):
    """Pure: the body stands in a hole it cannot jump out of — on every side the cell at head height is solid (a 1-deep
    dip has air there: a jump clears it). False when the sides are not read."""
    x, y, z = feet
    sides = [(x + dx, y + 1, z + dz) for dx, dz in DIRS4]
    return all(region.inside(c) for c in sides) and all(region.solid(c) for c in sides)

def pit_exit_tasks(region, feet, block=None):
    """Pure: one level up out of a pit — a pillar with a carried block when the column above is clear, else a step dug
    into the side with the least to break (its head and the head room above the body), then a walk onto it."""
    x, y, z = feet
    if block is not None and not region.solid((x, y + 2, z)):
        return [{"type": "pillar", "item": block}]
    best = None
    for dx, dz in DIRS4:
        step = (x + dx, y + 1, z + dz)          # stand here next: its floor is the side block at our feet level
        cells = [step, (x + dx, y + 2, z + dz), (x, y + 2, z)]
        if not region.solid((x + dx, y, z + dz)) or any(region.unbreakable(c) or region.hazard(c) for c in cells):
            continue
        cost = sum(region.solid(c) for c in cells)
        if best is None or cost < best[0]:
            best = (cost, step, cells)
    if best is None:
        return []
    _cost, step, cells = best
    return [mine_task(c) for c in cells if region.solid(c)] + \
        [{"type": "goto", "x": step[0], "y": step[1], "z": step[2], "range": 0.5}]

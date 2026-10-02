"""Getting from A to B: ASKING THE GAME to, and pricing what it answers. There is no pathfinder here any more. There were two — the mod's, which moves the body (walking, digging, bridging, pillaring, ladders), and one in this file, which planned routes the body then did not take. Every disagreement between them became a bug: water in the floor priced as flat ground, ore two blocks inside rock "unreachable", a village room behind a door the walker would have opened. Physics is the world's, so: route_s(cell, policy)   /plan — is there a walk, and how many seconds go_to(pos, policy)      travel — the mod walks, digs and bridges its own way there way_to(ctx, cells)      the answer to "could not get to it": a planned way (plan_way's named steps), then check What stays on this side is the decision: what is worth walking to, what a walk is worth, and when to give up."""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

from . import api, tape, arbiter, combat_model, lifecycle, roads
from .api import McError, NotAvailable, log
from .data import STAIR_BELOW, STAIR_CELLS, is_falling, GROUPS, FOOD, EYE_HEIGHT, home_box_of, is_door, HOLD_MARGIN, NAV_NODES, REACH, TASK_WAIT_S, WALK_BLOCKS_PER_TICK, WORK_REACH  # noqa: F401  (WORK_REACH: nav.WORK_REACH)
from .world import NEIGHBOURS6, Inventory, Region, add, bag, box, feet, route_key, to_segment
from .knowledge import dig_ticks
from .beliefs import TICKS_PER_S
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .memory import Memory
    from .shapes import Cell

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

def use_on_top(item, cell: Cell, top=1.0):
    """Pure: the task that uses `item` on the top face of `cell` (till, sow, pour, light a portal); `top`: that face's
    height in the cell — aimed inside it, so a block lower than a full cube (farmland) is hit, not the air above."""
    # under a lower face, aimed well inside it: 0.02 under the farmland's top left the look's own error sailing over
    # the cell (the jar raycasts along the look, not to the point): "no block under the crosshair" on 7 of 8 sows
    y = cell[1] + (1.0 if top >= 1.0 else top - 0.15)
    return {"type": "use_item", "item": item, "x": cell[0] + 0.5, "y": y, "z": cell[2] + 0.5, "onBlock": True}

def use_on_face(item, cell: Cell, face):
    """Pure: the task that uses `item` on the side face of `cell` that points along `face` ((dx, dz)): aimed at that
    face's centre — a bucket used there pours into the cell beside it."""
    return {"type": "use_item", "item": item, "x": cell[0] + 0.5 + 0.5 * face[0], "y": cell[1] + 0.5,
            "z": cell[2] + 0.5 + 0.5 * face[1], "onBlock": True}

def first_solid(region, eye, point, past=0.3):
    """Pure: the first solid cell a straight look from `eye` through `point` meets (a little past the point: the
    point sits on a face), or None — what the jar's click raycast would hit (ray_first)."""
    return ray_first(region, eye, point, past)

def mine_task(c: Cell, collect=False, down=False):
    """A mine task; `down`: the block is under the feet on purpose (a dig down) — the jar (≥ 0.1.58) then may stand
    on its own column, which ordinary mining never does."""
    return {"type": "mine", "x": c[0], "y": c[1], "z": c[2], "collect": collect, "requireDrops": False,
            **({"down": True} if down else {})}

# -- batches: many cells as one chain of single tasks (the jar's SequenceTask, now planned here) ------------------
def mine_order(cells, start=None) -> list[Cell]:
    """Pure: the order a batch mines `cells` — the columns nearest `start` (else the first cell given) first, each
    top down (never a cell while another of the batch sits above it). A sort, not a walk: what is left after any
    step keeps its order, so a batch rebuilt from the world after an interruption is the rest of the first."""
    cells = [tuple(c) for c in cells]
    if not cells:
        return []
    here = tuple(start) if start is not None else cells[0]
    return sorted(cells, key=lambda c: ((c[0] - here[0]) ** 2 + (c[2] - here[2]) ** 2, -c[1], c[0], c[2]))


def build_order(cells, start=None):
    """Pure: the order a batch places `cells` — the lowest layer first (every block has support), in it the nearest
    to `start` (else the first cell given). A sort: what is left after any step keeps its order."""
    cells = [tuple(c) for c in cells]
    if not cells:
        return []
    here = tuple(start) if start is not None else cells[0]
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


def mine_batch(cells: list[Cell], start=None, require_drops=False, collect=True, only=None):
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
        if v >= (0, 1, 50):
            _features.add("input")          # keys held until the body stands (the "input" task): climb_out
        if v >= (0, 1, 46):
            _features.add("autoeat")        # the jar eats while only walking, on the policy /autoeat sets
            # set once at first contact, so whoever drives the jar walks with it
            try:
                api.post("/autoeat", autoeat_policy())
            except McError as e:
                log(f"autoeat policy not set: {e}")
    return _features

def building_of(inv):
    """Pure: the building block the bag holds most of, or None."""
    options = [b for b in GROUPS["building"] if inv.usable(b)]
    return max(options, key=inv.usable) if options else None

def building_item():
    return building_of(Inventory())

def ground_in_column(solid, x, z, y_hint, span=32):
    """Pure: standing height in column (x, z) within y_hint ± span, or None."""

    for y in range(y_hint + span, y_hint - span - 1, -1):
        if solid((x, y, z)) and not solid((x, y + 1, z)) and not solid((x, y + 2, z)):
            return y + 1
    return None

LEG = 48   # blocks per travel leg on long trips
DOORS = None      # mechanisms.doors_on_way, wired by the brain: (here, there, policy) → a door's cells, opened first
DOOR_ROUTE = None  # mechanisms.route_s, wired by the brain: (here, there, walk_s) → seconds through a door, or None
ROAD_MEM: "Memory | None" = None   # the brain's memory: travelled legs are kept as a road network (roads.py) and reused

BLOCK_RESERVE = 16   # blocks kept back from travel: shelter walls, a pillar out of a hole, the next bridge

def place_budget(stock):
    """Pure: how many blocks a trip may spend on bridges and pillars."""

    return max(0, stock - BLOCK_RESERVE) if stock > 2 * BLOCK_RESERVE else stock // 2

# below this, walking on ends runs: no regeneration, and the next hit is the last
MIN_WALK_HP = 6.0

def safe_destination(pos, hazards=None, clear=1.0, standable=None):
    """Pure: `pos`, or a nearby standable spot clear of every hazard when `pos` sits inside one."""

    hazards = [h if len(h) > 2 else (h[0], h[1], (0.0, 0.0, 0.0)) for h in (hazards or [])]
    if not hazards or combat_model.min_tti(pos, hazards) == float("inf"):
        return pos
    spot, slack = combat_model.best_step(pos, hazards, standable=standable)
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

# task types sent only from a stand the jar's own check holds (I4: gate); the jar's approach then has nothing to do
APPROACHING = ("mine", "place", "use")
WAY_TRIES = 3            # ways plan_way is asked for one stand (a staircase comes a segment at a time)
WAY_FACES = 3            # stand candidates a way's walks are asked for (/plan, break off)

def use_holds(region, feet_at, cell, reach=REACH):
    """Pure: the jar's UseBlockTask sight — the block's centre or a face centre the first hit from the eye, in reach."""
    eye = (feet_at[0] + 0.5, feet_at[1] + EYE_HEIGHT, feet_at[2] + 0.5)
    centre = tuple(c + 0.5 for c in cell)
    points = [centre] + [tuple(centre[i] + d[i] * 0.45 for i in range(3)) for d in _FACES]
    return any(_ray_hits(region, eye, p, cell, reach) for p in points)

def place_holds(region, feet_at, cell, reach=REACH):
    """Pure: the jar's PlaceTask stand (WorldUtil.findPlacement) — the cell not the body's own, and a solid
    neighbour whose shared face centre is the first hit from the eye, in reach: the click that fills `cell`."""
    if tuple(cell) in (tuple(feet_at), add(feet_at, (0, 1, 0))):
        return False
    eye = (feet_at[0] + 0.5, feet_at[1] + EYE_HEIGHT, feet_at[2] + 0.5)
    for d in _FACES:
        n = add(cell, d)
        if region.solid(n) and _ray_hits(region, eye, tuple(cell[i] + 0.5 + d[i] * 0.5 for i in range(3)), n, reach):
            return True
    return False

def stands_for(kind, region, feet_at, target, down=False):
    """Pure: does the body at `feet_at` pass the jar's own check for a `kind` task on `target` — mine: MineTask.holds
    (holds), place: findPlacement (place_holds), use: sight (use_holds), stand: standing on it."""
    feet_at, target = tuple(feet_at), tuple(target)
    if kind == "mine":
        return holds(region, feet_at, target, down=down)
    if kind == "place":
        return place_holds(region, feet_at, target)
    if kind == "use":
        return use_holds(region, feet_at, target)
    return feet_at == target

def _cell_of(task):
    return (int(task["x"]), int(task["y"]), int(task["z"]))

def task_stands(tasks, feet_at):
    """Pure: [(task, the stand it is sent from)] for the approaching ones — the body's feet, or the last goto's cell
    before it in the same chain."""
    out, at = [], tuple(feet_at)
    for t in tasks:
        if t.get("type") == "goto":
            at = _cell_of(t)
        elif t.get("type") in APPROACHING and "x" in t:
            out.append((t, at))
    return out

def _read_box(cells, pad=REACH):
    lo = tuple(math.floor(min(c[i] for c in cells) - pad) for i in range(3))
    hi = tuple(math.ceil(max(c[i] for c in cells) + pad) for i in range(3))
    return box(lo, hi)

_IN_WAY = [0]            # how deep a way's own steps are being sent (their gate never asks for another way)
lifecycle.in_place(__name__, "_IN_WAY")

def gate(tasks, policy):
    """api.GATE (I4): each mine/place/use goes out only from a stand where the jar's own check holds (stands_for);
    one that fails gets its way first (reach_stand) and the chain is checked again. Returns the after-check (R4)."""
    pairs = task_stands(tasks, feet())
    if not pairs:
        return None
    region = None
    for _ in range(WAY_TRIES):
        pairs = task_stands(tasks, feet())
        region = _read_box([_cell_of(t) for t, _s in pairs] + [s for _t, s in pairs])
        bad = next(((t, s) for t, s in pairs if not stands_for(t["type"], region, s, _cell_of(t), t.get("down", False))),
                   None)
        if bad is None:
            break
        if _IN_WAY[0]:
            api.detail(f"!! a way's own {bad[0]['type']} at {_cell_of(bad[0])} not standable from {bad[1]}")
            raise api.NavFailed(f"planned step not standable: {bad[0]['type']} at {_cell_of(bad[0])}")
        reach_stand(bad[0], policy)
    else:
        raise api.NavFailed(f"no stand reached for {[(t['type'], _cell_of(t)) for t, _s in pairs][:3]}")
    before = region
    return lambda done: unplanned(before, tasks, done)

# blocks that change on their own: falling, flowing, decaying (R4 never counts them as unplanned)
CHANGE_ON_THEIR_OWN = ("sand", "gravel", "concrete_powder", "leaves", "water", "lava", "fire", "snow")

def unplanned_cells(before, after, tasks, feet_at):
    """Pure: the cells that turned air (broken) or solid (placed) between two reads with no task naming them — the
    mine and place cells named, a pillar's column under the body, and blocks that change on their own aside."""
    named = {_cell_of(t) for t in tasks if t.get("type") in ("mine", "place") and "x" in t}
    pillar = any(t.get("type") == "pillar" for t in tasks)
    out = []
    for c in before.blocks.keys() | after.blocks.keys():
        was, now = before.name(c), after.name(c)
        if was == now or c in named or any(was.endswith(k) or now.endswith(k) for k in CHANGE_ON_THEIR_OWN):
            continue
        if pillar and (c[0], c[2]) == (feet_at[0], feet_at[2]):
            continue
        if before.solid(c) != after.solid(c):
            out.append((c, was, now))
    return sorted(out)

def unplanned(before, tasks, done):
    """R4: the after-read of a segment's box against the before-read; any unnamed change said (detail + anomaly)."""
    try:
        after = box(before.lo, before.hi)         # a send came between: a fresh read, the next segment's before
    except McError as e:
        api.detail(f"   after-check unread: {e}")
        return
    for c, was, now in unplanned_cells(before, after, tasks, feet()):
        api.detail(f"!! unplanned change at {c}: {was} → {now} (no step named it)")
        if api.ANOMALY is not None:
            api.ANOMALY("unplanned change", f"{c}: {was} → {now}")

def stand_candidates(region, target, kind, most=WAY_FACES):
    """Pure: floored cells round `target` where a `kind` task on it holds, nearest first."""
    r = int(REACH)
    cells = [(target[0] + dx, target[1] + dy, target[2] + dz) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
             for dz in range(-r, r + 1)]
    ok = [c for c in cells if region.inside(c) and region.solid(add(c, (0, -1, 0))) and not region.solid(c)
          and not region.solid(add(c, (0, 1, 0))) and stands_for(kind, region, c, target)]
    return sorted(ok, key=lambda c: math.dist(c, target))[:most]

def reach_stand(task, policy, faces=None):
    """The way to a stand for `task` (I4): read the box, ask the game's walk (break off) to the stand candidates
    (`faces`, else stand_candidates), plan_way (a88's: explicit mine/place/goto steps), send them through the door,
    and again until the stand holds; plan_way's None → NavFailed with its why."""
    kind = "stand" if task.get("type") == "goto" else task["type"]
    target = _cell_of(task)
    for _ in range(WAY_TRIES):
        here = feet()
        region = _read_box([here, target])
        if kind != "stand" and stands_for(kind, region, here, target, task.get("down", False)):
            return
        cands = faces if faces is not None else stand_candidates(region, target, kind)
        walks = {c: _plan_reply(c, False, False, 0.5) for c in cands}
        steps, why, seconds = plan_way(region, here, target, kind, bag(), policy.protected, walks)
        if steps is None:
            raise api.NavFailed(f"no way to {kind} {target}: {why}")
        if not steps:
            return
        api.detail(f"   way to {kind} {target}: {len(steps)} steps, ~{seconds:.0f}s")
        _IN_WAY[0] += 1
        try:
            api.run_chain(steps, stop_on_failure=True)
        finally:
            _IN_WAY[0] -= 1
    raise api.NavFailed(f"no stand for {kind} {target} after {WAY_TRIES} ways")

ARRIVE_RANGE = 1.5       # a walk arrives this near its target (go_to's own margin): what "came to us" means
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

def go_to(pos, policy, range_=ARRIVE_RANGE, attempts=3, min_hp: float | None = MIN_WALK_HP, avoid_hazards=True, purpose="work",
          y_guess=False):
    """Walk; when the walker can't get there, build/dig a route toward the target. `y_guess`: the target's y is not
    known (a waypoint) — only then is a "no route" retried on the column's ground."""

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
        return _travel(pos, policy, range_, attempts, min_hp, purpose, _from, _began, y_guess)
    for _ in range(attempts):
        # a jar without `travel`: one step at a time, the same rule
        api.run({"type": "goto", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_, "partial": True}, awaits="whether the step arrived (`there`) decides the next attempt")
        if there(api.get("/state"), pos, range_):
            return _arrived(_from, pos, _began, True)
    return _arrived(_from, pos, _began, False)

def standable_in(pos, r=6):
    """spot → can a body stand there (floor under, feet and head clear), over the blocks read around `pos`."""
    x, y, z = (int(math.floor(v)) for v in pos)
    try:
        region = Region((x - r, y - 1, z - r), (x + r, y + 2, z + r))
    except McError:
        return None
    return lambda p: standable_at(region.solid, p)

def standable_at(solid, p):
    """Pure: floor under `p`, feet and head clear."""
    x, y, z = (int(math.floor(v)) for v in p)
    return solid((x, y - 1, z)) and not solid((x, y, z)) and not solid((x, y + 1, z))

def _clear_of_hazards(pos):
    """The target, or the nearest spot outside every hazard (combat_model.hazards); None when there is none."""
    hz = combat_model.hazards()
    if not hz:
        return pos
    safe = safe_destination(pos, hz, standable=standable_in(pos))
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
    assert ROAD_MEM is not None, "called only when the brain has wired its memory"
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
        if not moved(go_to(hop, policy, range_=6, attempts=1, min_hp=min_hp, purpose=purpose, y_guess=True)):
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

# -- doors a walk meets: never dug. A block with an `open` state opens and shuts; a hand opens all but these two
# (vanilla: redstone only) — the jar's /blocks gives names and states, no tags
LOCKED = ("iron_door", "iron_trapdoor")
DOOR_NEAR = 2.0          # a door this near the straight way here → there is on the way
DOOR_PAD = 2             # the bounded read round the way: its box, this much wider


def doorways(region):
    """Pure: {cell: (by hand, open)} of every door, trapdoor and gate in `region` (read with states) — a barrel's
    `open` is no door (09:35: 'door on the way opened by hand' was the barrel just used)."""
    return {c: (region.name(c) not in LOCKED, st.get("open") == "true")
            for c, st in getattr(region, "props", {}).items() if "open" in st and is_door(region.name(c))}


def door_steps(ways, here, there, near=DOOR_NEAR):
    """Pure: (shut doors on the way a hand opens — one cell a door, its lower half; shut ones it cannot), nearest
    first. An open door is walked through, never used (a use would shut it)."""
    on = sorted((c for c in ways if to_segment(c, here, there) <= near and (c[0], c[1] - 1, c[2]) not in ways),
                key=lambda c: math.dist(c, here))
    return ([c for c in on if ways[c] == (True, False)], [c for c in on if ways[c] == (False, False)])


def _doorways_between(here, there):
    """One bounded read with states over the box from here to there: {} when it cannot be read."""
    lo = tuple(min(here[i], there[i]) - (DOOR_PAD if i != 1 else 1) for i in range(3))
    hi = tuple(max(here[i], there[i]) + (DOOR_PAD if i != 1 else 2) for i in range(3))
    try:
        return doorways(Region(lo, hi, props=True))
    except McError as e:
        return api.swallowed("nav.doorways", e) or {}


HOME_DOOR = None      # mechanisms.home_exit, wired by the brain: (here, there, policy) → through a home's door


def way_kind(walk, boxes, here, pos):
    """Pure: how a walk that found no route goes on — "door" (an end in a home: by its taught door), "dig" (a way of
    explicit steps: reach_stand), or None (the walk found one: legs as they are)."""
    if walk is None or walk.get("found") is not False:
        return None
    if home_box_of(boxes, here) is not None or home_box_of(boxes, pos) is not None:
        return "door"
    return "dig"


def _leg(task, awaits):
    """One travel leg: its answer, a refusal ("target unreachable") read as a leg that got no further — the walk
    judges it (no nearer → not there), never raised past the caller's own no-way handling."""
    try:
        return api.run(task, wait=TASK_WAIT_S, awaits=awaits)
    except api.Unreachable as e:
        return {"status": "failed", "message": str(e)}


def _travel(pos, policy, range_, attempts, min_hp, purpose, _from, _began, y_guess=False):
    """The mod plans and runs the whole route, so Python never plans moves the walker can't make."""
    here = feet()
    asked = pos
    if ROAD_MEM is not None and math.hypot(pos[0] - here[0], pos[2] - here[2]) > LEG:
        here = _known_roads(here, pos, policy, purpose)
    if math.hypot(pos[0] - here[0], pos[2] - here[2]) > LEG:
        here, stopped = _long_trip(here, pos, policy, min_hp, purpose, _from, _began)
        if here is None:
            return stopped
    budget = place_budget(Inventory().count("building"))
    # taught doors (mechanisms): pressed open first when on the way, and never dug; the home's cells too
    doors = DOORS(here, pos, policy) if DOORS is not None else []
    # every other door on the way: a wooden one opened by hand when shut, an iron one a wall — none ever dug
    ways = _doorways_between(here, pos)
    by_hand, locked = door_steps(ways, here, pos)
    for cell in by_hand:
        r = api.run({"type": "use", "x": cell[0], "y": cell[1], "z": cell[2]}, wait=30,
                    awaits="the door opened: the walk goes through it next")
        api.detail(f"   door {cell} on the way opened by hand: {r.get('status')}")
    grounded = False
    boxes = getattr(policy.protected, "boxes", ())
    # keep walking while each leg brings us nearer; "target unreachable" at the leg's end is not failure
    for _ in range(max(attempts, LEGS)):
        api.at_boundary()                # nightfall between legs: never inside a walk
        was = feet()
        how = None if locked else way_kind(_plan_reply(pos, False, False, range_), boxes, was, pos)
        if how == "door":
            # out (or in) by the home's taught door (NavFailed: none); the walk goes on from its far side
            api.detail(f"   no walk to {tuple(pos)}: by the home's door")
            if HOME_DOOR is not None:
                HOME_DOOR(was, pos, policy)
            continue
        if how == "dig":
            # no route: one way (plan_way's steps; NavFailed with its why when there is none), then judged at the
            # asked cell — never LEGS more asks
            api.detail(f"   no walk to {tuple(pos)}: a way dug to it")
            reach_stand({"type": "goto", "x": pos[0], "y": pos[1], "z": pos[2]}, policy)
            ok = there(api.get("/state"), asked, range_)
            return _arrived(_from, asked, _began, ok, closer=True)
        try:
            r = _leg({"type": "travel", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_,
                      "placeBudget": budget},
                     "where the leg left the body decides the next leg (walked_closer, the retry on the ground)")
        except api.TaskStuck as e:
            # stuck: decide again from where we stand (the target may sit by a hazard that moved), never stand still
            log(f"   travel stuck ({e}): deciding again from {feet()}")
            pos = _clear_of_hazards(asked) or pos
            continue
        if there(api.get("/state"), pos, range_):
            return _arrived(_from, pos, _began, True)
        if walked_closer(was, feet(), pos):
            budget = place_budget(Inventory().count("building"))
            continue                     # that leg gained ground: the next one starts from here
        here = feet()
        budget = place_budget(Inventory().count("building"))     # the last leg spent some
        if not y_guess or grounded or "no route" not in (r.get("message") or "") or \
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
        _leg({"type": "travel", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_, "placeBudget": budget},
             "the retry's arrival is read before anything else is asked")
        if there(api.get("/state"), asked, range_):          # judged at the asked cell, never the rewritten y
            return _arrived(_from, asked, _began, True)
    if locked:
        raise api.NavFailed(f"blocked by a door at {locked[0]}: shut, no hand opens it, nothing taught")
    # a leg that ended nearer is progress; the next round continues from there
    return _arrived(_from, pos, _began, False, closer=walked_closer(_from, feet(), pos))

ARRIVE_CALLS = 8          # go_to calls one `arrive` may chain while each keeps gaining ground

def arrive(pos, policy, range_=ARRIVE_RANGE, **kw):
    """Get there, or raise `api.NavFailed`."""

    began = time.time()
    got = False
    for _ in range(ARRIVE_CALLS):
        got = go_to(pos, policy, range_=range_, **kw)
        if got is True:
            return True
        if not moved(got):
            raise api.NavFailed(f"could not get to {tuple(pos)} (no nearer after walking)", pos=pos)
        api.check_interrupt(began, api.soft())
    raise api.NavFailed(f"still {math.dist(feet(), pos):.0f} blocks from {tuple(pos)} after {ARRIVE_CALLS} walks",
                        pos=pos)

def arrived(pos, policy, range_=ARRIVE_RANGE, **kw):
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
            if attempt + 1 >= tries or not way_to(ctx, out.cells or [feet()], kind="stand"):
                raise
    return None

# the game is asked once per (target, policy, nodes) per round; cleared by `forget_routes`
from .world import ROUTES as _ROUTES  # noqa: E402  (the round's route answers, read by the cost model too)
# route questions one round may ask the game: a failing /plan costs seconds, so the rest fall back to "unknown"
_ROUTE_BUDGET = [0]
lifecycle.in_place(__name__, "_ROUTE_BUDGET")
ROUTES_PER_ROUND = 6

def route_s(cell, policy, range_=1.5, nodes=NAV_NODES):
    """(can we get there, seconds it would take) — THE GAME'S answer, not one assembled here."""

    key = route_key(cell, range_, nodes)
    hit = _ROUTES.get(key)
    if hit is not None:
        return hit
    if _ROUTE_BUDGET[0] >= ROUTES_PER_ROUND:
        return (None, None)          # this round has asked enough: unknown, and the caller estimates instead
    _ROUTE_BUDGET[0] += 1
    try:
        out = _plan(cell, False, False, range_, nodes)       # the walk's price: walks never dig nor build (E3, D6)
    except tape.ReplayMiss:
        return (None, None)                 # a recorded round: not an answer, and never cached as one
    _ROUTES[key] = out
    return out

def _plan(cell, dig, build, range_, nodes=NAV_NODES):
    """The game's /plan to `cell`: (found, seconds), (None, None) when it cannot be asked."""
    r = _plan_reply(cell, dig, build, range_, nodes)
    if r is None:
        return (None, None)
    found = r.get("found")
    return (None if found is None else bool(found)), r.get("seconds")

def _plan_reply(cell, dig, build, range_, nodes=NAV_NODES):
    try:
        return api.get(f"/plan?to={cell[0]},{cell[1]},{cell[2]}&range={range_}"
                       f"&break={'true' if dig else 'false'}&place={'true' if build else 'false'}&nodes={nodes}")
    except McError as err:
        api.swallowed("nav.plan", err)
        return None

MOB_STEP_UP = 1          # blocks a walking mob climbs in one step: a drop deeper than this is one way down

def climbs_back(start_y, steps, step_up=MOB_STEP_UP):
    """Pure: can a walker come back along this path (steps: [{x, y, z}] from `start_y`) — no drop in it deeper than
    `step_up`. Our pathfinder drops up to 3; a zombie climbs none of those."""
    ys = [start_y] + [s["y"] for s in steps]
    return all(a - b <= step_up for a, b in zip(ys, ys[1:]))

def plan_walks(cells, range_):
    """{cell: the game's walk (/plan, nothing dug or built)} for each of `cells` — plan_way's `walks`."""
    return {tuple(c): _plan_reply(c, False, False, range_) for c in cells}

def walks_to(cell, range_=None, climber=False, start_y=None):
    """Can a walking mob at `cell` come to the body: the game's walk from our side (nothing dug, nothing built),
    taken back — so no drop on it a walker cannot climb (a climber climbs any). None when it cannot be asked."""
    try:
        r = _plan_reply(cell, False, False, ARRIVE_RANGE if range_ is None else range_)
    except tape.ReplayMiss:
        return None
    if r is None or r.get("found") is None:
        return None
    if not r["found"]:
        return False
    start_y = start_y if start_y is not None else int(math.floor(api.get("/state")["y"]))
    return climber or climbs_back(start_y, r.get("steps") or [])

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

    if DOOR_ROUTE is not None and feet is not None:
        # a taught door on the way: the game sees it shut and solid; we know a press opens it
        through = DOOR_ROUTE(tuple(feet), tuple(cell), lambda d: d / (WALK_BLOCKS_PER_TICK * 20))
        if through is not None:
            return True, through
    found, seconds = route_s(cell, policy, range_=range_, nodes=nodes)
    if found is None:
        found = not (feet is not None and plainly_below(feet, cell))
    return found, seconds

def way_to(ctx, cells, kind="mine"):
    """A stand from which `kind` holds on the nearest of `cells`, by a planned way (reach_stand: plan_way's named
    steps through the door, I3/I4): True there, False when there is no way (said by the NavFailed it raised)."""

    cells = sorted({tuple(int(round(v)) for v in c) for c in cells}, key=lambda p: math.dist(p, feet()))
    if not cells:
        return False
    x, y, z = cells[0]
    try:
        reach_stand({"type": "goto" if kind == "stand" else kind, "x": x, "y": y, "z": z}, ctx.policy)
    except api.NavFailed as e:
        api.detail(f"   no way to {kind} {cells[0]}: {e}")
        return False
    return True

# -- stairs and pits: a body goes down by a staircase it can walk back up, never a 1-wide shaft
STAIR_STEPS = 8          # steps one staircase segment digs before the ground is read again

def stair_dir(feet, target):
    """Pure: the horizontal step toward `target` along its longer axis ((1, 0) when straight below)."""
    dx, dz = target[0] - feet[0], target[2] - feet[2]
    if dx == 0 and dz == 0:
        return (1, 0)
    return ((1 if dx > 0 else -1), 0) if abs(dx) >= abs(dz) else (0, (1 if dz > 0 else -1))

def falling_above(region, cell):
    """Pure: the falling blocks stacked on `cell`, lowest first — they drop into it once it is dug."""
    out, c = [], add(cell, (0, 1, 0))
    while region.inside(c) and is_falling(region.name(c)):
        out.append(c)
        c = add(c, (0, 1, 0))
    return out

def dig_cells(region, cells, start):
    """Pure: what to break so `cells` stand open — each with the falling blocks over it, top down per column
    (mine_order): a cell dug under sand drops it into the next one (hello2 11:11 "nothing to mine (air)")."""
    want = set(cells)
    for c in cells:
        want.update(falling_above(region, c))
    return [c for c in mine_order(want, start) if region.solid(c)]

def _blocked(region, cells, protected, placed=()):
    """Pure: why these cells may not be opened — a fluid in or beside one (but a floor the step places: a bridge
    fills it), a protected or unbreakable one — or None."""
    for c in cells:
        if region.hazard(c) or any(region.hazard(add(c, n)) and add(c, n) not in placed for n in NEIGHBOURS6):
            return f"fluid at {c}"
        if c in protected:
            return f"home at {c}"
        if region.unbreakable(c):
            return f"unbreakable at {c}"
    return None

def open_tasks(region, cells, floors, start, protected, places):
    """Pure: (tasks, why) that leave `cells` open with a floor under each of `floors`: the cells opened top down with
    what falls on them (dig_cells), a missing floor placed from `places` (popped); None and why when a fluid, the home,
    an unbreakable block or a floor with nothing to place stands in the way."""
    opened = dig_cells(region, cells, start)
    missing = [f for f in floors if not region.solid(f)]       # a gap or a fluid: bridged by a placed block
    why = _blocked(region, set(cells) | set(opened), protected, placed=set(missing))
    if why:
        return None, why
    tasks = [mine_task(c) for c in opened]
    for f in missing:
        if f in protected:
            return None, f"home at {f}"
        if not places:
            return None, f"no tread at {f}"
        tasks.append({"type": "place", "item": places.pop(), "x": f[0], "y": f[1], "z": f[2]})
    return tasks, None

def _step_tasks(region, step_cells, tread, stand, start, protected, places):
    """Pure: (tasks, why) for one step of a dug way: its cells opened (open_tasks), then the walk onto `stand`."""
    tasks, why = open_tasks(region, step_cells, [tread], start, protected, places)
    if tasks is None:
        return None, why
    return tasks + [{"type": "goto", "x": stand[0], "y": stand[1], "z": stand[2], "range": 0.5}], None

def stair_steps(region, feet, target, protected=(), places=(), max_steps=STAIR_STEPS, stop_y=None):
    """Pure: (tasks, why, end) of a 1-wide staircase from `feet` toward `stop_y` (default one above the target) —
    down, or up when `stop_y` is above the feet (the climb out) — each step one over and one down/up, its cells
    opened top down with the falling blocks over them (down: feet, head and the head room the walk down passes; up:
    feet, head and the head room over the step it leaves, for the jump), a tread under it placed from `places` when
    missing, then walked onto — until the feet stand at `stop_y` or `max_steps` (the region is read again for the next
    segment). `end`: where the feet stand after it; `why`: what stopped it short."""
    x, y, z = feet
    stop_y = target[1] + 1 if stop_y is None else stop_y
    if stop_y == y:
        return [], None, tuple(feet)
    dy = 1 if stop_y > y else -1
    d, left, tasks, end = stair_dir(feet, target), list(places), [], tuple(feet)
    for k in range(1, max_steps + 1):
        stand = (x + d[0] * k, y + dy * k, z + d[1] * k)
        cells = ([(stand[0], stand[1] + i, stand[2]) for i in range(STAIR_CELLS)] if dy < 0 else
                 [stand, add(stand, (0, 1, 0)), add(end, (0, 2, 0))])
        tread = add(stand, (0, -1, 0))
        if not all(region.inside(c) for c in cells + [tread]):
            return tasks, None, end
        step, why = _step_tasks(region, cells, tread, stand, feet, protected, left)
        if step is None:
            return tasks, why, end
        tasks, end = tasks + step, stand
        if stand[1] * dy >= stop_y * dy:
            break
    return tasks, None, end

def tunnel_steps(region, feet, target, protected=(), places=(), done=None):
    """Pure: (tasks, why) of a 2-high level way from `feet` toward `target`, step by step until `done(feet)` (default:
    a stand that holds it — reach and sight), each step opened top down, its floor placed when missing."""
    done = done or (lambda here: holds(region, here, target))
    y = feet[1]
    left, tasks, here, been = list(places), [], tuple(feet), {tuple(feet)}
    while not done(here):
        d = stair_dir(here, target)
        stand = (here[0] + d[0], y, here[2] + d[1])
        cells = [stand, add(stand, (0, 1, 0))]
        tread = add(stand, (0, -1, 0))
        if not all(region.inside(c) for c in cells + [tread]) or stand in been:
            # off the read, or back where it was (over the target: no level stand holds it)
            return tasks, f"no stand reaches {tuple(target)} within the read"
        been.add(stand)
        step, why = _step_tasks(region, cells, tread, stand, feet, protected, left)
        if step is None:
            return tasks, why
        tasks += step
        here = stand
    return tasks, None

def stands_at(kind, region, feet, target):
    """Pure: the way's end for `kind`: standing on the cell (stand), else a stand that holds it (reach and sight)."""
    return tuple(feet) == tuple(target) if kind == "stand" else holds(region, feet, target)

PLACE_S = 0.25           # one place task (detail.log: "placed minecraft:torch (0.25s)")

def way_s(region, feet, steps, inv):
    """Pure: seconds a planned way takes — its breaks (knowledge.dig_ticks, the held tool per block), its places,
    its walks at PLAYER_SPEED."""
    mined = [region.name((t["x"], t["y"], t["z"])) for t in steps if t["type"] == "mine"]
    walk, at = 0.0, tuple(feet)
    for t in steps:
        if t["type"] == "goto":
            walk += math.dist(at, (t["x"], t["y"], t["z"]))
            at = (t["x"], t["y"], t["z"])
    places = sum(t["type"] == "place" for t in steps)
    return dig_ticks(mined, inv) / TICKS_PER_S + places * PLACE_S + walk / PLAYER_SPEED

def plan_way(region, feet, target, kind, inv, protected, walks=None):
    """Pure: (steps | None, why, seconds) — the cheapest way to where `kind` (mine|place|use|stand) of `target` can be
    done: a walk the game found (`walks`: {cell: its /plan reply, break off}, asked by the caller), else the dug
    way of named steps: a staircase down when deeper than STAIR_BELOW, a staircase up when above the feet (the climb
    out), then a level way (dug through, its missing treads placed: a bridge over a gap or a fluid). Steps name cells,
    never items (the door's ARM/HOLD do); a protected cell refuses the way (why "home at …")."""
    ways = [([{"type": "goto", "x": c[0], "y": c[1], "z": c[2], "range": 0.5}], None,
             float(r["seconds"]) if r.get("seconds") is not None else math.dist(feet, c) / PLAYER_SPEED)
            for c, r in (walks or {}).items() if r and r.get("found")]
    places = [building_of(inv)] * place_budget(inv.count("building")) if building_of(inv) else []
    done = lambda here: stands_at(kind, region, here, target)     # noqa: E731
    if done(tuple(feet)):
        return [], None, 0.0                       # standing where it can be done: nothing to plan
    steps, why, end = [], None, tuple(feet)
    if target[1] < feet[1] - STAIR_BELOW or target[1] > feet[1]:
        # down (or up) to the target's own level (a buried target is held from beside it), then along it
        steps, why, end = stair_steps(region, feet, target, protected, places, stop_y=target[1])
    if why is None and (not steps or end[1] == target[1]):
        more, why = tunnel_steps(region, end, target, protected, places, done)
        steps = steps + more
    if steps:
        ways.append((steps, None, way_s(region, feet, steps, inv)))
    if not ways:
        return None, why or f"no way to {tuple(target)}", None
    return min(ways, key=lambda w: w[2])

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


def ride_boat(ctx, target):
    """Shell, never planned: boat on water, row to `target`, boat taken back."""
    raise NotImplementedError("ride_boat: a shell")

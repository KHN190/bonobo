"""Getting from A to B: ASKING THE GAME to, and pricing what it answers.

There is no pathfinder here any more. There were two — the mod's, which moves the body (walking, digging,
bridging, pillaring, ladders), and one in this file, which planned routes the body then did not take. Every
disagreement between them became a bug: water in the floor priced as flat ground, ore two blocks inside rock
"unreachable", a village room behind a door the walker would have opened. Physics is the world's, so:

    route_s(cell, policy)   /plan — is there a way, and how many seconds (the one door for both questions)
    go_to(pos, policy)      travel — the mod walks, digs and bridges its own way there
    way_to(ctx, cells)      the answer to "could not get to it": walk with digging allowed, then check

What stays on this side is the decision: what is worth walking to, what a walk is worth, and when to give up."""
import heapq
import math
import re
import time
from dataclasses import dataclass, field

from . import api, tape
from .beliefs import CONFIG as _PLAY
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


# What a placement is allowed to spend, kept because `travel` is told how many blocks it may lay (`placeBudget`).
HAND_STONE = 16   # breaking a pickaxe block by hand
TOO_HARD_BY_HAND = {"obsidian", "crying_obsidian", "ancient_debris", "reinforced_deepslate", "iron_block",
                    "netherite_block", "respawn_anchor", "ender_chest", "anvil", "basalt", "blackstone"}


_features = None


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
    return _features


# What one cell of each kind costs to get through, in "walked block" units — the capability table. A body that
# can fly pays nothing for any of them; one that can teleport does not consult this at all. Same estimate, one
# table per kind of body, instead of a formula per kind of body.


def estimate_price_s(region, start, target, policy, sample=None):
    """Seconds to get there, as the GAME says. An ENGINE of `gates.takes_s(s, Go(...))`.
    """
    import math as _m
    found, seconds = route_s(target, policy, range_=1.5)
    if seconds is not None:
        return float(seconds) if found else float(seconds) * UNREACHABLE_FACTOR
    # Nobody could say. Not zero, not infinity: how long the walk would take if the ground were flat and empty.
    return max(1.0, _m.dist(tuple(start), tuple(target)) / float(_PLAY["player"]["speed"]))


# What "there is no way" costs, as a multiple of the search's own estimate: a price, never a wall — the same
# rule the seek column follows. Nothing is unreachable for ever; it is expensive until the world changes.
UNREACHABLE_FACTOR = 8.0


def _is_here(start):
    """Is `start` where the body actually stands? Only then can the game be asked about the route from it."""
    try:
        return tuple(int(v) for v in start) == tuple(int(v) for v in feet_now())
    except (McError, TypeError, ValueError):
        return False


def building_item():
    inv = Inventory()
    options = [b for b in GROUPS["building"] if inv.usable(b)]
    return max(options, key=inv.usable) if options else None


def trapped(message):
    m = re.search(r"\((\d+) positions explored\)", message or "")
    return m is not None and int(m.group(1)) < 5000


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

    Returns True when we got there, `Walked` when we only got nearer — truthy, so every caller that asks "did the
    walk work" still reads it as yes, while a caller that cares can tell the difference. Without this a deep
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
    """A leg that got nearer without arriving. Truthy, and carries how many blocks it gained."""

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


def walked_closer(start, here, target):
    """Did this leg actually bring us nearer the target? In blocks, against where it began."""
    return math.dist(start, target) - math.dist(here, target) >= PROGRESS_BLOCKS


def go_to(pos, policy, range_=1.5, attempts=3, min_hp=MIN_WALK_HP, avoid_hazards=True):
    """Walk; when the walker can't get there, build/dig a route toward the target.

    `min_hp` aborts the walk when health drops below it. It defaults to a real value because it used to
    default to None: every call site had to remember the guard, and almost none did. Pass min_hp=0 where
    walking while nearly dead is the point — fleeing, or recovering a body.
    """
    pos = tuple(pos)
    from . import arbiter
    if not arbiter.BODY.owns("nav.go_to"):
        return False
    _began, _from = time.time(), feet_now()
    if avoid_hazards:
        from . import perception
        hz = perception.hazards()
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
                if not go_to(wp, policy, range_=6, attempts=1):
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
                if not go_to(hop, policy, range_=6, attempts=1, min_hp=min_hp):
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
        avoid = [{"x": c[0], "y": c[1], "z": c[2]} for c in policy.protected
                 if math.dist(c, here) <= 64 or math.dist(c, pos) <= 64][:4000]
        grounded = False
        # A journey is made of LEGS. The mod walks until the ground, the pickaxe or its own search budget runs
        # out, then stops at the closest point it could reach and says "target unreachable". Read as a failure,
        # that put a two-minute cooldown on every far or deep target and none of them ever finished — though
        # every attempt had dug another ten blocks toward it. So: keep going while each leg brings us nearer,
        # and only give up when one does not.
        for _ in range(max(attempts, LEGS)):
            was = feet_now()
            r = api.run({"type": "travel", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_,
                         "break": policy.allow_dig, "place": policy.allow_build, "placeBudget": budget,
                         "avoid": avoid}, wait=900)
            if r["status"] == "succeeded" or math.dist(feet_now(), pos) <= range_ + 1:
                return True
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
                    r = api.run({"type": "travel", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_,
                                 "break": policy.allow_dig, "place": policy.allow_build, "placeBudget": budget,
                                 "avoid": avoid}, wait=900)
                    if r["status"] == "succeeded" or math.dist(feet_now(), pos) <= range_ + 1:
                        return True
        # The walker says it could not get all the way. Whether that is a failure depends on where it left us:
        # a leg that ended thirty blocks nearer is progress, and the next round continues from there.
        return _arrived(_from, pos, _began, False, closer=walked_closer(_from, feet_now(), pos))
    for _ in range(attempts):
        # A jar without `travel`: one step at a time, and the same rule — the mod says whether it got there.
        r = api.run({"type": "goto", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_, "partial": True})
        here = feet_now()
        if r["status"] == "succeeded" or math.dist(here, pos) <= range_ + 1:
            return True
    return _arrived(_from, pos, _began, False)


def dig_down(depth, policy, use_ladders):
    """Straight down under the feet, stopping above caves/lava/water. With ladders, hangs one on the shaft wall
    above the head after each step so the way back is a climb."""
    x, y, z = feet_now()
    region = Region((x - 1, y - depth - 2, z - 1), (x + 1, y + 2, z + 1))
    safe = 0
    for i in range(1, depth + 1):
        cell, below = (x, y - i, z), (x, y - i - 1, z)
        if (region.unbreakable(cell) or region.hazard(cell) or not region.solid(below) or (x, y - i, z) in policy.protected
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
            return api.run({"type": "collect", "radius": radius, **({'only': list(only)} if only else {})}, wait=wait)
        except api.Unreachable as out:
            if attempt + 1 >= tries or not way_to(ctx, out.cells or [feet_now()], range_=1.5):
                raise
    return None


# One round asks about dozens of targets and the answer cannot change while the body stands still, so the
# game is asked once per (target, policy, nodes) and the answer is kept for the round. Cleared by `forget_routes`.
_ROUTES = {}
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


def reachable(cell, policy, range_=1.5, nodes=6000):
    """Is there a way to `cell` at all? The first half of `route_s`.

    Unknown counts as "maybe": only a definite No from the game may stand behind a ban, because the alternative
    is banning a place because nobody asked.
    """
    found, seconds = route_s(cell, policy, range_=range_, nodes=nodes)
    return (True if found is None else found), seconds


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
    if go_to(near, ctx.policy, range_=range_, attempts=1) and reachable(near, ctx.policy, range_)[0]:
        return True                                    # the walk was enough
    if not (ctx.policy.allow_dig or ctx.policy.allow_build):
        return False
    # Making a way IS travel with a pickaxe: the mod breaks and places as it goes. There is nothing for this side
    # to plan — asking the body to walk there, with digging allowed, is the whole of it.
    for cell in sorted(cells)[:4]:
        if go_to(cell, ctx.policy, range_=range_, attempts=1) and reachable(cell, ctx.policy, range_)[0]:
            return True
    return False

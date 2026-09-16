"""Getting from A to B under a safety policy, Baritone-style: one cost-based route search whose moves may walk,
dig, bridge, pillar, climb placed ladders or dig down, compiled into a chain of mod tasks."""
import heapq
import math
import re
import time
from dataclasses import dataclass, field

from . import api
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


# Move costs (≈ seconds). Placing costs more than walking; blocks are a resource, so they get dearer when scarce.
WALK, STEP, DIG_DOWN, BRIDGE, PILLAR, LADDER = 1, 2, 3, 4, 4, 2.5
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


def plan_tunnel(region, start, targets, policy, max_expand=60000, blocks=None, ladders=None, features=None):
    """Cheapest route from `start` (feet) until a target block touches the body, as a list of mod tasks (empty when
    already there, None when impossible). Moves: walk / step up / step down (digging through if needed), bridge a
    gap by placing a floor, pillar up, climb ladders hung on a wall, dig straight down. Never digs next to lava or
    water, never bridges over or beside lava (unless policy.lava_ok), never touches protected or player-made blocks,
    never plans falls."""
    targets = set(targets)
    if blocks is None or ladders is None:
        inv = Inventory()
        blocks = sum(inv.usable(b) for b in GROUPS["building"]) if blocks is None else blocks
        ladders = inv.usable("minecraft:ladder") if ladders is None else ladders
    features = mod_features() if features is None else features
    can_place = policy.allow_build and blocks > 0
    can_pillar = can_place and "pillar" in features
    can_ladder = policy.allow_build and ladders > 0 and "ladder_in_cell" in features
    scarcity = 1 if blocks >= 32 else 3   # keep the last blocks for a night shelter

    def lava_near(c):
        return not policy.lava_ok and any(region.name(add(c, d)) == "lava" for d in NEIGHBOURS6)

    def dig_cost(cells):
        cost = 0
        for c in cells:
            if not region.inside(c) or region.hazard(c) or region.unbreakable(c):
                return None
            if region.solid(c):
                if not policy.allow_dig or c in policy.protected or region.player_made(c):
                    return None
                if policy.hand_only and not bare(region.name(c)).endswith(HAND_MINEABLE_SUFFIX):
                    name = bare(region.name(c))
                    if "deepslate" in name or name.endswith("_ore") or name in TOO_HARD_BY_HAND:
                        return None   # 15 s+ per block by hand: the task watchdog cancels it, so it's a wall
                    # Vanilla lets hands break stone too (~7 s, no drop): far dearer than dirt, but a way out
                    # when nothing else is.
                    cost += HAND_STONE
                    continue
                if any(region.hazard(add(c, d)) for d in NEIGHBOURS6):
                    return None
                cost += 6 if region.falling(c) else 2
        return cost

    def digs(cells):
        return [("dig", c) for c in cells if region.solid(c)]

    def goal(f):
        head = add(f, (0, 1, 0))
        return any(add(f, d) in targets or add(head, d) in targets for d in NEIGHBOURS6)

    frontier, best, came, expanded = [(0, start)], {start: 0}, {start: (None, ())}, 0
    # What the route itself put under a node ("floor", "pillar", "ladder"): the region snapshot doesn't know blocks
    # we plan to place, so a bridge or pillar could never continue past its first block without this.
    made = {start: None}

    def push(to, nc, actions, placed=None):
        if nc < best.get(to, math.inf):
            best[to] = nc
            came[to] = (f, tuple(actions))
            made[to] = placed
            heapq.heappush(frontier, (nc, to))

    def standing_support(node):
        below = (node[0], node[1] - 1, node[2])
        return (region.solid(below) and not region.hazard(below)) or made.get(node) in ("floor", "pillar")

    while frontier and expanded < max_expand:
        cost, f = heapq.heappop(frontier)
        if cost > best.get(f, math.inf):
            continue
        expanded += 1
        if goal(f):
            actions, node = [], f
            while node is not None:
                prev, acts = came[node]
                actions[:0] = list(acts)
                node = prev
            return compile_route(actions, targets)
        x, y, z = f
        floor_here = (x, y - 1, z)
        head2 = (x, y + 2, z)
        for dx, dz in DIRS4:
            n = (x + dx, y, z + dz)
            n_up, n_down = add(n, (0, 1, 0)), add(n, (0, -1, 0))
            for to, cells, floor, base in (
                (n, [n_up, n], n_down, WALK),
                (n_up, [head2, add(n, (0, 2, 0)), n_up], n, STEP),
                (n_down, [n_up, n, n_down], add(n, (0, -2, 0)), STEP),
            ):
                if not region.inside(floor) or not region.solid(floor) or region.hazard(floor):
                    continue
                if floor in targets:
                    below = add(floor, (0, -1, 0))
                    if not region.solid(below) or any(region.hazard(add(floor, d)) for d in NEIGHBOURS6):
                        continue
                dc = dig_cost(cells)
                if dc is not None:
                    push(to, cost + base + dc, digs(cells) + [("goto", to)])
            # Bridge: nothing to stand on ahead → place a floor against the block under us.
            # The floor cell must really be empty (air or water): a torch or flower there isn't solid but a block
            # can't be placed into it ("position is occupied"), which voided whole routes.
            if (can_place and standing_support(f) and region.inside(n_down)
                    and region.name(n_down) in ("air", "cave_air", "water") and not lava_near(n_down)):
                dc = dig_cost([n_up, n])
                if dc is not None:
                    push(n, cost + BRIDGE * scarcity + dc,
                         digs([n_up, n]) + [("floor", n_down, floor_here), ("goto", n)], placed="floor")
        up = (x, y + 1, z)
        on_ladder = made.get(f) == "ladder"
        if standing_support(f) or on_ladder:
            dc = dig_cost([head2])
            if dc is not None:
                # Ladder: hang a ladder on a wall beside the feet cell (and the head cell on the first rung), climb.
                if can_ladder:
                    wall = next(((x + dx, y + 1, z + dz) for dx, dz in DIRS4
                                 if region.solid((x + dx, y + 1, z + dz))
                                 and (on_ladder or region.solid((x + dx, y, z + dz)))), None)
                    if wall is not None:
                        rungs = [] if on_ladder else [("ladder", f, add(wall, (0, -1, 0)))]
                        push(up, cost + LADDER + dc,
                             digs([head2]) + rungs + [("ladder", up, wall), ("goto", up)], placed="ladder")
                # Pillar: jump and place a block into the cell we leave.
                # Pillaring places a block into our own cell: impossible when a ladder, torch or vine hangs there
                # (the block isn't replaceable), so a pillar step from such a cell just times out.
                if can_pillar and standing_support(f) and region.name(f) in ("air", "cave_air") \
                        and made.get(f) != "ladder":
                    push(up, cost + PILLAR * scarcity + dc, digs([head2]) + [("pillar", f)], placed="pillar")
        down, below_down = (x, y - 1, z), (x, y - 2, z)
        if (policy.allow_dig and region.inside(below_down) and region.solid(below_down)
                and not region.hazard(below_down)):
            dc = dig_cost([down])
            if dc is not None and region.solid(down):
                push(down, cost + DIG_DOWN + dc, digs([down]) + [("goto", down)])
    return None


def building_item():
    inv = Inventory()
    options = [b for b in GROUPS["building"] if inv.usable(b)]
    return max(options, key=inv.usable) if options else None


def compile_route(actions, targets=()):
    """Route actions → mod tasks. Digs and placements walk into reach by themselves; explicit gotos are only kept
    where standing on the exact cell matters (before a pillar or ladder) and at the end."""
    tasks, dug, pending_goto = [], set(), None
    block = building_item()

    def flush_goto(range_=0.6):
        nonlocal pending_goto
        if pending_goto is not None:
            g = pending_goto
            tasks.append({"type": "goto", "x": g[0], "y": g[1], "z": g[2], "range": range_, "partial": False})
            pending_goto = None

    for act in actions:
        kind = act[0]
        if kind == "goto":
            pending_goto = act[1]
        elif kind == "dig":
            if act[1] in dug or act[1] in targets:
                continue
            dug.add(act[1])
            tasks.append(mine_task(act[1]))
        elif kind == "floor":
            flush_goto()
            cell, against = act[1], act[2]
            tasks.append({"type": "place", "item": block, "x": cell[0], "y": cell[1], "z": cell[2],
                          "against": {"x": against[0], "y": against[1], "z": against[2]}})
        elif kind == "pillar":
            pending_goto = act[1]          # stand on the exact cell first; the task centres itself
            flush_goto(0.5)
            tasks.append({"type": "pillar", "item": block})
        elif kind == "ladder":
            flush_goto(0.6)                # rungs are hung from where the previous step ended
            cell, wall = act[1], act[2]
            tasks.append({"type": "place", "item": "minecraft:ladder", "x": cell[0], "y": cell[1], "z": cell[2],
                          "against": {"x": wall[0], "y": wall[1], "z": wall[2]}})
    if tasks and pending_goto is not None:
        flush_goto(1.0)
    return tasks


def trapped(message):
    m = re.search(r"\((\d+) positions explored\)", message or "")
    return m is not None and int(m.group(1)) < 5000


def feet_now():
    s = api.get("/state")
    return s["blockX"], s["blockY"], s["blockZ"]


def dig_route(tasks, policy):
    """Runs a compiled route (tasks from plan_tunnel). Old callers passing plain cells get mine tasks."""
    tasks = [t if isinstance(t, dict) else mine_task(t) for t in tasks]
    return api.run_chain(tasks, stop_on_failure=True, before_segment=policy.before_segment)


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


def _arrived(start, target, began, ok):
    """Feed one walk back into the terrain estimate: what it really cost against the straight line."""
    try:
        from . import field
        straight = math.dist(start, target) / PLAYER_SPEED
        if ok and straight > 0.5:
            state = api.get("/state")
            field.TERRAIN.observed(field.bucket_of(state), straight, time.time() - began)
    except Exception:
        pass
    return ok


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
                    return False
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
        for _ in range(attempts):
            r = api.run({"type": "travel", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_,
                         "break": policy.allow_dig, "place": policy.allow_build, "placeBudget": budget,
                         "avoid": avoid}, wait=900)
            if r["status"] == "succeeded" or math.dist(feet_now(), pos) <= range_ + 1:
                return True
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
        return False
    for _ in range(attempts):
        r = api.run({"type": "goto", "x": pos[0], "y": pos[1], "z": pos[2], "range": range_, "partial": True})
        here = feet_now()
        if r["status"] == "succeeded" or math.dist(here, pos) <= range_ + 1:
            return True
        if not (policy.allow_dig or policy.allow_build):
            return False
        if trapped(r["message"]) and policy.allow_surface and policy.allow_dig:
            try:
                if escape_to_surface(policy):
                    continue
            except NotAvailable:
                pass  # deep underground: no sky in range, so work toward the target instead
        if dig_toward(pos, policy, range_):
            return _arrived(_from, pos, _began, True)
    return _arrived(_from, pos, _began, False)


def dig_toward(target, policy, range_=2.0, hop=10, max_hops=20):
    """Hop by hop toward a target, each hop a planned route (walk, dig, bridge, pillar, ladder)."""
    for _ in range(max_hops):
        here = feet_now()
        dist = math.dist(here, target)
        if dist <= range_ + 1:
            return True
        step = min(hop, dist)
        waypoint = tuple(round(here[i] + (target[i] - here[i]) * step / dist) for i in range(3))
        region = region_around([here, waypoint], pad=3)
        route = plan_tunnel(region, here, {waypoint}, policy) if region else None
        if route is None:
            log(f"no safe route toward {waypoint}")
            return False
        kinds = {t["type"] for t in route}
        if kinds - {"mine"}:
            log(f"route toward {waypoint}: {len(route)} tasks ({', '.join(sorted(kinds))})")
        dig_route(route, policy)
        api.run({"type": "goto", "x": waypoint[0], "y": waypoint[1], "z": waypoint[2], "range": 2, "partial": True})
        if math.dist(feet_now(), here) < 1.5:
            return False
    return math.dist(feet_now(), target) <= range_ + 1


def escape_to_surface(policy, max_rise=64):
    """Staircase / pillar / ladder up to a column open to the sky. Returns True if it did something."""
    fx, fy, fz = feet_now()
    top_y = min(fy + max_rise, 319)
    region = Region((fx - 4, fy - 1, fz - 4), (fx + 4, top_y, fz + 4))
    tops = {}
    for (x, y, z) in region.blocks:
        if region.solid((x, y, z)):
            tops[(x, z)] = max(tops.get((x, z), y), y)
    targets = {(fx + dx, tops.get((fx + dx, fz + dz), fy - 1) + 1, fz + dz)
               for dx in range(-4, 5) for dz in range(-4, 5)
               if max(abs(dx), abs(dz)) >= 2
               and tops.get((fx + dx, fz + dz), fy - 1) + 3 <= top_y
               and tops.get((fx + dx, fz + dz), fy - 1) + 1 >= fy}
    if not targets:
        raise NotAvailable(f"no sky within {max_rise} blocks above")
    route = plan_tunnel(region, (fx, fy, fz), targets, policy)
    if not route:
        return False
    log(f"boxed in → {len(route)} tasks up to the surface")
    dig_route(route, policy)
    return True


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

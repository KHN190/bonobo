"""Gathering: mining a vein, strip mining a tunnel, hunting, taking a block."""

import math
from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import knowledge as K
from . import api, beliefs, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .data import cannot_reach, bare, mid
from .knowledge import DIG_SHOVEL_S, HUNT_SWORD_S, members
from .bag import mineable, opener, pickup_whitelist, refused
from .world import Inventory, Region, add, connected, entities, find, region_around, ripe_near
from .skillcore import ToolMissing, feet, mine_cell, gained
from .explore import surface_first, explore_for, approach_policy
from .fluids import CAVE_AIR, fluid_faces, seal_plan, swimming

def require_pickaxe(tier, min_left=3):
    if tier is None:
        return
    if not any(t >= tier and d >= min_left for t, d, _ in Inventory().tools("pickaxe")):
        raise ToolMissing("pickaxe", tier)

# -- gathering

MINE_BATCH = 32  # blocks one mine_many takes: smaller batches re-planned too often (1.4 s each)

BESIDE = 0.5            # travel range that ends face to face with a block (the walker's arrival: range + 0.5)

REACH_BUDGET = 3         # ways of not getting there, per call, before the place itself is the problem

def _reach_budget(spent, blocks, why=None):
    """Raise once the budget is gone."""

    if spent >= REACH_BUDGET:
        raise api.NavFailed(why or f"{blocks[0]}: {spent} unreachable in a row — not from this spot")

SEEK_RADII = (24, 48)     # a mining pass looks near first, then once wider

def seek_hits(blocks, found, radius, blocked, protected):
    """Pure: what a mining pass may go for, from what `/find` saw — sealed or exposed alike, minus bans and our own builds."""

    at = [(h, (h["x"], h["y"], h["z"])) for h in found]
    hits = [h for h, p in at if not blocked(p) and p not in protected]
    if hits:
        return hits
    if radius < SEEK_RADII[-1]:
        return None
    banned = sum(1 for _, p in at if blocked(p))
    why = f": {len(found)} in range but {banned} banned, {len(found) - banned} protected" if found else ""
    raise NotAvailable(f"no {blocks[0]} within {SEEK_RADII[-1]} blocks{why}")

def noted_hits(notes, blocks, blocked, protected):
    """Pure: remembered cells of `blocks` a mining pass may go straight to, shaped like /find's answer, minus bans and builds."""

    names = {bare(b) for b in blocks}
    out = []
    for n in notes:
        p = tuple(n["pos"])
        if bare(n["kind"]) in names and not blocked(p) and p not in protected:
            out.append({"x": p[0], "y": p[1], "z": p[2], "noted": True})
    return out

def mine_segment_commands(state, args):
    """Pure: one open-loop mining segment — one `mine_many` over `cells`, with the pickup filter a nearly full bag needs."""

    cells, drop, tier = args
    only = pickup_whitelist(state["inv"].used_slots(), [drop])
    return [{"type": "mine_many", "collect": True, "requireDrops": tier is not None, **({"only": only} if only else {}),
             "blocks": [{"x": p[0], "y": p[1], "z": p[2]} for p in cells]}]

def spent_cells(sent, name_at, blocks):
    """Pure: the sent cells whose block is gone in a fresh read (`name_at(cell)`) — their notes are spent; a cell
    still standing (sent, not broken) keeps its note. No read (`name_at` None): nothing is judged spent."""
    if name_at is None:
        return []
    kinds = {bare(b) for b in blocks}
    return sorted(c for c in sent if bare(name_at(c) or "air") not in kinds)


@skill(gives=K.GIVES_MINE, needs=lambda a: {} if a[4] is None else {f"tool:pickaxe:{a[4]}": 1}, speed={"shovel": DIG_SHOVEL_S},
       start=lambda c: Inventory().count(c.args[1]),
       done=lambda c: Inventory().count(c.args[1]) >= c.base + c.args[2], budget=900, stall=90,
       units=lambda c: c.args[2], key=lambda c: f"mine:{c.args[1]}",
       provides={"mine": lambda ctx, s: (s.token, s.count, s.detail["blocks"], s.detail["tier"],
                                          s.detail.get("breaks"))}, fills_bag=lambda c: members(c.args[1]))
def mine(ctx, token, count, blocks, tier, breaks=None):
    """Tunnel to the nearest reachable vein of `blocks` and mine it until `count` more `token` are held."""
    drop = token
    target = Inventory().count(drop) + count
    radius = SEEK_RADII[0]
    # unreachable is about where we stand, not the block: one budget for every way of not getting there, then fail as nav (the retry waits for a change of place)
    unreachable = 0
    empty_batches = 0    # batches the mod could not break at all; a few in a row means the seam really is dead
    tried = set()        # cells a batch already broke none of: a second refusal drops them (bag.refused)
    no_cell = set()      # seeds whose vein has no mineable cell from here: the next pass takes the next seed
    opened = set()       # cells the jar could not hold a stand at, given a side face once (then banned if refused again)
    sent = set()         # cells sent to the jar: their notes are retired once the count is met
    dug_out = False      # a batch that broke its cells but brought nothing in gets one dig-out and sweep
    for _ in range(10):
        have = Inventory().count(drop)
        if have >= target:
            # the count met: the notes of what was mined are spent (only a whole pass's end retired them — a count met
            # at the top of the next pass kept a mined diamond's note: seen_store__noted 10:16 "the note retired" False)
            if ctx.mem is not None and sent:
                seen_now = region_around(sorted(sent), pad=0)
                for p in spent_cells(sent, (lambda c: seen_now.name(c)) if seen_now is not None else None, blocks):
                    for b in blocks:
                        ctx.mem.forget_seen(b, p, ctx.dimension, radius=0.5)
            return
        yield None
        require_pickaxe(tier)
        # sealed or exposed alike (the jar's approach digs to buried blocks); remembered first, /find only when no noted cell is left
        notes = [n for b in blocks for n in ctx.mem.seen(b, ctx.dimension)] if ctx.mem is not None else []
        noted = [h for h in noted_hits(notes, blocks, ctx.blocked, ctx.policy.protected)
                 if (h["x"], h["y"], h["z"]) not in no_cell]
        raw = noted or find(blocks, radius=radius, limit=60)
        digs = "approach_dig" in nav.mod_features()
        exposed_cells = None if digs else {(h["x"], h["y"], h["z"])
                                           for h in find(blocks, radius=radius, limit=60, exposed=True)}
        fresh = [h for h in raw if (h["x"], h["y"], h["z"]) not in no_cell]
        if raw and not fresh:
            if radius < SEEK_RADII[-1]:
                radius = SEEK_RADII[-1]
                continue
            raise NotAvailable(f"no {blocks[0]} vein mineable from here ({len(no_cell)} seen, none with a cell to break)")
        hits = seek_hits(blocks, fresh, radius, ctx.blocked, ctx.policy.protected)
        if hits is None:
            api.detail(f"  mine {bare(drop)}: none within {radius}, looking wider")
            radius = SEEK_RADII[-1]
            continue
        start = feet()
        if swimming(api.get("/state")):
            raise NotAvailable("in the water: no digging until back on land")
        # which blocks have an open face is the world's answer (/find exposed), not a second model here
        seed = (hits[0]["x"], hits[0]["y"], hits[0]["z"])
        region = region_around([start, seed], pad=nav.SAFE_DROP + 2)   # deep enough to see a drop (bag.floored)
        if region is None and seed[1] < start[1] - 2 and ctx.policy.allow_dig:
            # too far to read at once and far below: the ground a staircase segment crosses is read, and the segment
            # dug — the seed read again from the foot of it (no blind shaft)
            d = nav.stair_dir(start, seed)
            leg_end = (start[0] + d[0] * nav.STAIR_STEPS, start[1] - nav.STAIR_STEPS, start[2] + d[1] * nav.STAIR_STEPS)
            leg = region_around([start, leg_end], pad=2)
            stairs = nav.stair_down_tasks(leg, start, seed, ctx.policy.protected) if leg is not None else []
            if stairs:
                api.detail(f"  mine {bare(drop)}: {seed} too far to read from {start}: a staircase segment down")
                api.run_chain(stairs, stop_on_failure=True, wait=120)
                continue
        if region is None:
            # too far to read the ground between: walk closer or make a way; only when neither works is the place the problem
            if not nav.arrived(seed, ctx.policy, range_=12) and not nav.way_to(ctx, {seed}):
                ctx.ban(seed)
                raise api.NavFailed(f"{blocks[0]} at {seed}: no way there and no tunnel")
            api.detail(f"  mine {bare(drop)}: {seed} too far to read from {start}, walked closer")
            continue
        if hits[0].get("noted") and bare(region.name(seed)) not in {bare(b) for b in blocks}:
            for b in blocks:
                ctx.mem.forget_seen(b, seed, ctx.dimension, radius=0.5)     # gone from where it was noted
            api.detail(f"  mine {bare(drop)}: noted {seed} is {region.name(seed)} now, note forgotten")
            continue
        vein = set(mineable((p for p in connected(region, seed, blocks) if not ctx.blocked(p)), start,
                            region, nav.SAFE_DROP))
        if not vein:
            # the whole connected vein is already proven unreachable: next seed
            api.detail(f"  mine {bare(drop)}: vein at {seed} ({region.name(seed)}) has no mineable cell from {start}")
            no_cell.add(seed)
            continue
        # never open a block touching lava or water unless the goal wants the fluid; surface blocks keep 2 from any fluid
        want = breaks or max(1, target - have)
        vein = set(sorted(vein, key=lambda p: math.dist(p, start))[: max(want, len(vein) if tier else want)])
        # reach a vein by walking if there is a way, else by digging one: buried ore has no path, and banning it left coal inside a wall forever
        near = min(vein, key=lambda p: math.dist(p, start))
        if near[1] < start[1] - 2 and ctx.policy.allow_dig:
            # far below: a staircase the body can walk back up, never travel's 1-wide shaft (brain__base: 9 deep,
            # no way out and no sight of the ore 2 blocks off)
            stairs = nav.stair_down_tasks(region, start, near, ctx.policy.protected)
            if stairs:
                api.run_chain(stairs, stop_on_failure=True, wait=120)
                continue
        walked = nav.arrived(near, ctx.policy, range_=3.5, attempts=1) if "travel" in nav.mod_features() else False
        if not walked and not nav.way_to(ctx, vein):
            for p in vein:
                ctx.ban(p)
            # the next vein is another target, not a retry: failing the step over one vein cooled the goal
            unreachable += 1
            _reach_budget(unreachable, blocks, f"no way and no tunnel to the {blocks[0]} vein at {seed}")
            continue
        # only blocks within reach of where travel left us, a dozen at a time
        here_now = feet()

        def workable(at):
            """The vein cells within reach of the stand at `at`, nearest first: the ones to work from here — sent
            to the jar when it can hold them (`holdable`), else opened / walked beside by the buried path below."""
            return sorted((p for p in vein if math.dist(p, at) <= nav.REACH), key=lambda p: math.dist(p, at))

        def holdable(at, cells):
            """The jar's own rule (nav.holds: sight, not distance — a pit 2 down cannot see an ore at its rim,
            search_night_resume 09:46:50 NO_STAND ×3; a buried ore has no face to see), the vein's other cells not in
            the way (they break in the same batch). Distance alone as the reach dropped buried ore before the opener
            (a2d37ae: the diamond rows never sent theirs)."""
            x0, y0, z0 = at
            sight = Region((x0 - 5, y0 - 4, z0 - 5), (x0 + 5, y0 + 6, z0 + 5))
            return {p for p in cells if nav.holds(sight, at, p, through=set(vein))}
        in_reach = workable(here_now)
        if not in_reach:
            near_cell = min(vein, key=lambda p: math.dist(p, here_now))
            if not nav.arrived(near_cell, ctx.policy, range_=2.0, attempts=1) and not nav.way_to(ctx, {near_cell}):
                for p in vein:
                    ctx.ban(p)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"{blocks[0]} at {near_cell}: no way there and no tunnel")
                continue
            here_now = feet()
            in_reach = workable(here_now)
            if not in_reach and not nav.way_to(ctx, vein):
                for p in vein:
                    ctx.ban(p)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"got near {near_cell} but no way in to the {blocks[0]}")
                continue
        # distance is not reachability: only open-faced blocks go to mine_many; travel digs a way to the nearest buried one
        held = holdable(here_now, in_reach)
        open_faced = [p for p in mineable(in_reach, here_now, region, nav.SAFE_DROP)
                      if (exposed_cells is None or p in exposed_cells) and p in held]
        if not open_faced:
            buried = in_reach[0]
            if not nav.arrived(buried, ctx.policy, range_=BESIDE, attempts=1):
                ctx.ban(buried)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"{blocks[0]} at {buried}: buried, and no way dug to it")
            continue
        vein = set(open_faced[:MINE_BATCH])
        if not ctx.policy.lava_ok:
            # seal every fluid face of what is about to break (seal_plan); with nothing to seal with, ban those cells (unless the goal wants the fluid)
            try:
                seal = seal_plan(region, sorted(vein), Inventory())
            except NotAvailable as e:
                wet = {c for c in vein if fluid_faces(region, c, vein)}
                log(f"   {len(wet)} {blocks[0]} cells not mined: {e}")
                for p in wet:
                    ctx.ban(p)
                vein -= wet
                if not vein:
                    continue
                seal = []
            if seal:
                api.run_chain(seal, stop_on_failure=True, wait=60)
        # a cell open only into pockets no body can stand in: the jar's mine never digs for a line of sight, so the
        # block on the body's side goes first (bag.opener; down-flagged when it lies below the feet)
        openers = sorted({op for c in vein if (op := opener(region, c, here_now, nav.SAFE_DROP)) is not None
                          and op not in ctx.policy.protected})
        if openers:
            api.run_chain([nav.mine_task(op, down=op[1] < here_now[1]) for op in openers], stop_on_failure=True, wait=60)
        before = Inventory().count(drop)
        sent |= set(vein)
        try:
            r = api.run(mine_segment_commands({"inv": Inventory()}, (vein, drop, tier))[0], wait=900, awaits="the batch's drops counted before the next vein is chosen")
        except api.Unreachable as out:
            around = {c: region.name(c) for c in out.cells or ()}
            api.detail(f"  mine {bare(drop)} refused by the jar ({out}): feet {feet()}, cells "
                       + "; ".join(f"{c} {n} faces {[region.name(add(c, d)) for d in nav.NEIGHBOURS6]}" for c, n in around.items()))
            # "cannot hold a stand spot": the jar found no spot that keeps the cell in sight (a top face seen only
            # from a pit's rim, search_night_resume 09:17:19) — open a side face once, then ask again; a ban only
            # when the opened cell is refused too
            held = [c for c in (out.cells or ()) if c not in opened and "cannot hold a stand spot" in str(out)]
            sides = [(c, op) for c in held if (op := opener(region, c, feet(), nav.SAFE_DROP, forced=True)) is not None
                     and op not in ctx.policy.protected]
            if sides:
                opened.update(c for c, _op in sides)
                api.run_chain([nav.mine_task(op, down=op[1] < feet()[1]) for _c, op in sides], stop_on_failure=True,
                              wait=60)
                continue
            # "cannot reach": make a standing spot and ask again; a way that changed nothing counts against the budget (from jar 0.1.40 its approach already dug)
            if "approach_dig" not in nav.mod_features() and nav.way_to(ctx, out.cells or vein):
                unreachable += 1
                _reach_budget(unreachable + 1, blocks, str(out))    # one more try than a plain refusal gets
                continue
            for p in (out.cells or vein):
                ctx.ban(p)
            unreachable += 1
            _reach_budget(unreachable, blocks, str(out))
            continue
        except api.TaskStuck as out:
            # the jar circled this batch (approach ↔ mine): refuse it, charge the budget, try the next vein
            for p in vein:
                ctx.ban(p)
            unreachable += 1
            _reach_budget(unreachable, blocks, str(out))
            continue
        if gained(lambda: Inventory().count(drop), before) <= before:
            from .knowledge import MINE_YIELD
            if MINE_YIELD.get(mid(drop), 1) < 1 and "failed" not in r["message"]:
                continue   # a chance drop (grass → seeds ~1 in 8): an empty break is expected, keep breaking
            # partial success is progress: some blocks broke, keep the batch (banning all over one block stalled the kit)
            import re as _re
            part = _re.search(r"(\d+) of (\d+) steps failed", r.get("message") or "")
            if part and int(part.group(1)) < int(part.group(2)):
                bad = cannot_reach(r.get("message"))
                # "no path" to a block inside rock: dig one face open and it is ordinary
                again, _ = refused(bad, tried, "approach_dig" in nav.mod_features())
                tried |= bad
                if again and ctx.policy.allow_dig and nav.way_to(ctx, again):
                    continue           # a face is open now: the same blocks, asked again — once
                for p in bad:
                    ctx.ban(p)      # only the blocks the mod named as unreachable, and only with no way in
                if bad:
                    # each mod refusal costs a full path search: charged to the same budget
                    unreachable += 1
                    _reach_budget(unreachable, blocks)
                continue
            if not dug_out and "failed" not in (r.get("message") or ""):
                # the batch broke its cells ("succeeded") and the bag gained nothing: the drop lies where the pickup
                # never went (a sealed cavity: brain__base "collecting items (0)") — open the cavity toward the body
                # once and sweep, not a silent success nor a ban of cells that are air now
                now = Region(tuple(min(c[i] for c in vein) - 1 for i in range(3)),
                             tuple(max(c[i] for c in vein) + 1 for i in range(3)))
                broken = [c for c in vein if not now.solid(c)]
                if broken:
                    dug_out = True
                    here = feet()
                    ops = sorted({op for c in broken if (op := opener(now, c, here, nav.SAFE_DROP, forced=True))
                                  is not None and op not in ctx.policy.protected})
                    api.detail(f"  mine {bare(drop)}: {len(broken)} broken, nothing in the bag: dig-out {ops}, then a sweep")
                    api.run_chain([nav.mine_task(op, down=op[1] < here[1]) for op in ops]
                                  + [{"type": "collect", "radius": 6}], wait=60)
                    continue
            again, _ = refused(vein, tried, "approach_dig" in nav.mod_features())
            tried |= vein
            if again and ctx.policy.allow_dig and nav.way_to(ctx, again):
                continue               # nothing broke because nothing could be stood next to: now it can, once
            for p in vein:
                ctx.ban(p)             # refused: dropped, never the same batch again
            # ban this batch and try the next: one unmineable batch is not a dead seam
            empty_batches += 1
            if empty_batches >= 3:
                raise NotAvailable(f"{blocks[0]} vein yielded nothing: {r['message']}")
            continue
        else:
            for b in blocks:                  # this vein is mined: its notes are spent
                ctx.mem.forget_seen(b, seed, ctx.dimension, radius=4)
    raise McError(f"could not mine enough {bare(drop)}")

STRIP_ORES = [("minecraft:diamond", ["diamond_ore", "deepslate_diamond_ore"], 2),
              ("minecraft:raw_iron", ["iron_ore", "deepslate_iron_ore"], 1),
              ("minecraft:raw_gold", ["gold_ore", "deepslate_gold_ore"], 2),
              ("minecraft:redstone", ["redstone_ore", "deepslate_redstone_ore"], 2),
              ("minecraft:lapis_lazuli", ["lapis_ore", "deepslate_lapis_ore"], 1),
              ("coal", ["coal_ore", "deepslate_coal_ore"], 0)]

def _stone_held():
    """What a tunnel yields whatever else it finds: stone of any kind in the bag."""
    return Inventory().count("stone") + Inventory().count("minecraft:cobbled_deepslate")

def _not_in_water(c):
    if api.get("/state")["inWater"]:
        raise NotAvailable("standing in water: no strip mining here")

@skill(gives=["state:tunnelled"], remaining=_k.tunnelled(lambda c: c.args[1] if len(c.args) > 1 else 16), speed={}, pre=[_not_in_water], needs={"tool:pickaxe:0": 1},
       start=lambda c: (feet()[1], _stone_held()),
       verify=lambda c: feet()[1] != c.base[0] or _stone_held() > c.base[1], budget=300, stall=60)
def strip_mine_step(ctx, length=16):
    """Always-available work: descend toward iron (diamond once an iron pickaxe exists) and tunnel, taking the ore revealed."""

    s = api.get("/state")
    fx, fy, fz = s["blockX"], s["blockY"], s["blockZ"]
    tools = Inventory().tools("pickaxe")
    best_tier = max((t for t, d, _ in tools if d >= 3), default=0)
    # diamonds peak near -58, bedrock starts at -60..-64: tunnel at -54
    depth = -54 if any(t >= 2 and d >= 20 for t, d, _ in tools) else 16
    if fy < depth - 3:
        # drifted below the band: staircase back up rather than tunnel into bedrock
        if not nav.arrived((fx, depth, fz), ctx.policy, range_=2, attempts=1):
            raise api.NavFailed(f"can't climb back up to mining depth {depth}")
        return "climbed"
    if fy > depth + 4:
        try:
            nav.dig_down(min(12, fy - depth), ctx.policy, use_ladders=Inventory().count("minecraft:ladder") >= 12)
        except NotAvailable:
            # unsafe here (water/lava/cave below): find dry solid ground nearby
            stone = [h for h in find(["stone", "deepslate", "dirt", "grass_block"], radius=24, limit=40)
                     if h["y"] <= fy and math.dist((h["x"], h["z"]), (fx, fz)) >= 6]
            if not stone:
                raise NotAvailable("no safe ground to dig down nearby")
            t = stone[0]
            if not nav.arrived((t["x"], t["y"] + 1, t["z"]), ctx.policy, range_=2, attempts=1):
                raise NotAvailable("can't reach safe ground to dig down")
        return
    facing = TUNNEL_DIRS[int(((s["yaw"] % 360) + 45) // 90) % 4]
    region = Region((fx - length - 1, fy - 1, fz - length - 1), (fx + length + 1, fy + 2, fz + length + 1))
    # mobs and caves are read through the walls: the tunnel goes where neither is
    hostiles = [(e["x"], e["y"], e["z"]) for e in entities(24) if beliefs.fights_back([e["type"]])]
    (dx, dz), end, cave = plan_tunnel(region, (fx, fy, fz), length, ctx.policy.protected, hostiles, facing)
    tasks = []
    if cave:
        # every way opens onto a cave at once: its faces sealed first (the one sealing rule), then the step dug
        own = {(fx, fy, fz), (fx, fy + 1, fz)}
        tasks = seal_plan(region, cave, Inventory(), own=own, cave=True) + [nav.mine_task(c) for c in cave
                                                                         if region.solid(c)]
        end = 1
    for i in range(1 if not cave else 2, end + 1):
        tasks += [nav.mine_task(c) for c in [(fx + dx * i, fy + 1, fz + dz * i), (fx + dx * i, fy, fz + dz * i)]
                  if region.solid(c)]
    if end == 0:
        # turning in place is not progress: say so and let the retry policy decide
        api.run({"type": "look", "yaw": (s["yaw"] + 90) % 360, "pitch": 0}, awaits="the next sight line read")
        raise NotAvailable("tunnel blocked ahead (turned to try another direction)")
    tasks.append({"type": "goto", "x": fx + dx * end, "y": fy, "z": fz + dz * end, "range": 1, "partial": True})
    log(f"strip mining {end} blocks at y={fy}")
    api.run_chain(tasks, stop_on_failure=True, before_segment=ctx.policy.before_segment)
    for token, blocks, tier in STRIP_ORES:
        if tier <= best_tier and find(blocks, radius=5, limit=1):
            try:
                mine(ctx, token, 1, blocks, tier)
            except NotAvailable:
                pass

def _hunt_progress(token, types):
    near = entities(64, types)
    # closing in (4-block bins) or collecting drops is progress; circling is not
    return Inventory().count(token), (int(near[0]["distance"] // 4) if near else None)

@skill(gives=K.GIVES_HUNT, needs=lambda a: {"tool:sword:1": 1} if beliefs.fights_back(a[3]) else {},
       speed={"sword": HUNT_SWORD_S}, start=lambda c: Inventory().count(c.args[1]), done=lambda c: Inventory().count(c.args[1]) >= c.base + c.args[2],
       budget=480, stall=60, units=lambda c: c.args[2], key=lambda c: f"hunt:{c.args[1]}",
       provides={"hunt": lambda ctx, s: (s.token, s.count, s.detail["types"], getattr(ctx, "night", False))},
       fills_bag=lambda c: members(c.args[1]))
def hunt(ctx, token, count, types, night):
    """Kill animals of `types` (reach them with the navigator first) until `count` more `token` drops are held."""
    target = Inventory().count(token) + count
    for _ in range(count * 3 + 3):
        if Inventory().count(token) >= target:
            return
        yield _hunt_progress(token, types)
        prey = [e for e in entities(64, types) if not ctx.blocked((e["id"], 0, 0))]
        if not prey:
            if night:
                raise NotAvailable(f"no {types[0]} nearby at night")
            prey = explore_for(ctx, types)
            if not prey:
                raise NotAvailable(f"no {bare(types[0])} found")
        before = Inventory().count(token)
        e = prey[0]
        # reach it with the navigator first: a blind chase from underground never lands a hit
        if e["distance"] > 4 and _k.under_rock(api.get("/state").get("skyLight", 15)) and e["y"] > feet()[1] + 3:
            # in a cave below the animal: climb out (digging allowed) first
            surface_first(ctx)
            continue
        if e["distance"] > 4:
            # walk and bridge to animals, never tunnel
            nav.arrived((math.floor(e["x"]), math.floor(e["y"]), math.floor(e["z"])), approach_policy(ctx.policy),
                      range_=3, attempts=2)
            e = next((n for n in entities(64, types) if n["id"] == e["id"]), None)
            if e is None or e["distance"] > 6:
                ctx.ban((prey[0]["id"], 0, 0), 300)
                raise api.NavFailed(f"could not get to the {bare(types[0])}")
        try:
            api.run({"type": "attack", "entity": e["id"]}, wait=30, awaits="the mob dead")
            # the drop can land where nothing stands: `sweep` makes a way to it before a kill is written off
            nav.sweep(ctx, radius=6, only=[token], wait=60)
        except api.TaskStuck:
            ctx.ban((prey[0]["id"], 0, 0), 600)  # unreachable (across water, on a ledge)
            raise api.NavFailed(f"the {bare(types[0])} is out of reach for attacks")
        if gained(lambda: Inventory().count(token), before) <= before:
            ctx.ban((prey[0]["id"], 0, 0), 120)
            raise NotAvailable(f"killing the {bare(types[0])} dropped no {bare(token)}")
    raise McError(f"could not hunt enough {bare(token)}")

TUNNEL_DIRS = [(0, 1), (-1, 0), (0, -1), (1, 0)]

def tunnel_run(region, feet_at, d, length, protected=()):
    """Pure: (end, cave_cells) — how many steps a 2-high tunnel goes from `feet_at` toward `d` before it stops
    (no floor, unbreakable, a hazard beside, ours) or would break into a cave: `cave_cells`, the step's two cells
    that open onto air not the tunnel's own (read through the walls, before a block is broken), else None."""

    fx, fy, fz = feet_at
    dx, dz = d
    own = {(fx, fy, fz), (fx, fy + 1, fz)}
    end = 0
    for i in range(1, length + 1):
        cells = [(fx + dx * i, fy + 1, fz + dz * i), (fx + dx * i, fy, fz + dz * i)]
        floor = (fx + dx * i, fy - 1, fz + dz * i)
        if (not region.solid(floor) or any(region.unbreakable(c) for c in cells)
                or any(region.hazard(c) or region.hazard(add(c, n))
                       for c in cells for n in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, -1)])
                or any(c in protected for c in cells)):
            break
        own |= set(cells)
        if any(fluid_faces(region, c, own, CAVE_AIR) for c in cells):
            return end, cells
        end = i
    return end, None

def plan_tunnel(region, feet_at, length, protected=(), hostiles=(), facing=None):
    """Pure: (direction, end, cave_cells) of the tunnel to dig — the direction running longest before a cave, ties
    to the one ending farthest from the hostiles seen through the walls, then the facing. A tunnel never breaks into
    a cave it could have avoided; when every way opens onto one at once, its cells come back to be sealed first."""

    order = sorted(TUNNEL_DIRS, key=lambda d: d != facing)
    best = None
    for d in order:
        end, cave = tunnel_run(region, feet_at, d, length, protected)
        tip = (feet_at[0] + d[0] * end, feet_at[1], feet_at[2] + d[1] * end)
        away = min((math.dist(tip, h) for h in hostiles), default=math.inf)
        score = (end, away)
        if best is None or score > best[0]:
            best = (score, d, end, cave)
    _score, d, end, cave = best
    return d, end, (cave if end == 0 else None)

def _take_needs(token):
    """The tool a take of `token` needs, from the one table (knowledge.TAKEABLE's `tool`, as MINE's tier is mine's)."""
    tool = K.TAKEABLE.get(token, {}).get("tool")
    return {} if tool is None else {f"tool:{tool[0]}:{tool[1]}": 1}

@skill(gives=K.GIVES_TAKE, needs=lambda a: _take_needs(a[1]), speed={}, start=lambda c: Inventory().count(c.args[1]), verify=lambda c: Inventory().count(c.args[1]) > c.base,
       budget=180, stall=45, provides={"take": lambda ctx, s: (s.token, s.count, s.detail["blocks"])},
       fills_bag=lambda c: members(c.args[1]))
def take(ctx, token, count, blocks):
    """Break blocks that ARE the thing and pick them up: a village's bed, furnace, table, hay, crops."""

    want = int(count)
    got = 0
    for _ in range(want * 2):
        if got >= want:
            return got
        hits = [h for h in (find(blocks, radius=48, limit=20) or ())
                if not ctx.blocked((h["x"], h["y"], h["z"]))
                and (h["x"], h["y"], h["z"]) not in ctx.policy.protected]
        if bare(token) == "wheat":
            # taken ripe or not at all: green wheat breaks into seeds
            ripe = set(ripe_near(feet(), 48))
            hits = [h for h in hits if (h["x"], h["y"], h["z"]) in ripe]
        if not hits:
            raise NotAvailable(f"no {bare(blocks[0])} within reach to take")
        cell = (hits[0]["x"], hits[0]["y"], hits[0]["z"])
        if not nav.arrived(cell, ctx.policy, range_=3, attempts=2):
            ctx.ban(cell)
            continue
        mine_cell(ctx.policy, cell, wanted=[token], require_drops=False, wait=60)
        api.run({"type": "collect", "radius": 4}, wait=20, awaits="the drops the break left, counted after")
        yield None
        got += 1
        # the block is gone whether or not the drop reached the bag: forget it, or we walk back to an empty square
        ctx.mem.forget_seen(bare(hits[0]["block"]), cell, ctx.dimension, radius=1)
        log(f"took {bare(token)} at {cell} ({got}/{want})")
    return got

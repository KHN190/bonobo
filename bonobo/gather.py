"""Gathering: mining a vein, strip mining a tunnel, hunting, taking a block."""
from __future__ import annotations

import math
import re
from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import knowledge as K
from . import api, beliefs, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .data import LEVEL_SIGHT_DEPTH, BAN_MAX_S, TASK_WAIT_S, WORK_REACH, cannot_reach, bare, mid
from .knowledge import FIND_AT, members
from .data import MINE_YIELD
from .bag import mineable, opener, pickup_whitelist, refused
from .world import Inventory, Region, cell_add, connected, entities, find, region_around, ripe_near
from .skillcore import ToolMissing, mine_cell, gained
from .world import feet
from .explore import surface_first, explore_for, approach_policy
from .fluids import CAVE_AIR, fluid_faces, seal_plan
from .knowledge import swimming
from typing import TYPE_CHECKING
from .data import MINE_YIELD, TAKEABLE

if TYPE_CHECKING:
    from .shapes import Cell

def require_pickaxe(tier, min_left=K.TOOL_WORKING):
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
    """Pure: one open-loop mining segment — the cells as single mines in the batch's order (nav.mine_batch: top of a
    column first, the nearest next), then one sweep with the pickup filter a nearly full bag needs."""

    cells, drop, tier = args
    only = pickup_whitelist(state["inv"].used_slots(), [drop])
    return nav.mine_batch(cells, state.get("feet"), require_drops=tier is not None, collect=True, only=only or None)

def spent_cells(sent, name_at, blocks) -> list[Cell]:
    """Pure: the sent cells whose block is gone in a fresh read (`name_at(cell)`) — their notes are spent; a cell
    still standing (sent, not broken) keeps its note. No read (`name_at` None): nothing is judged spent."""
    if name_at is None:
        return []
    kinds = {bare(b) for b in blocks}
    return sorted(c for c in sent if bare(name_at(c) or "air") not in kinds)


def deep_below(cell: Cell, feet_at: Cell) -> bool:
    """Pure: `cell` lies deeper than a level stand sees (LEVEL_SIGHT_DEPTH): reached by a way down, not a walk."""
    return cell[1] < feet_at[1] - LEVEL_SIGHT_DEPTH

def shaft_plan(region, feet_at: Cell, target: Cell, carried: int, protected=()):
    """Pure: (tasks, why not) for a straight shaft from the feet down to a buried `target`'s level: dug only as deep as
    nav.dig_down_tasks finds safe (lava, water, a cave stop it) and the blocks carried can pillar back out of."""
    depth = feet_at[1] - target[1]
    try:
        tasks, safe = nav.dig_down_tasks(region, feet_at, depth, protected)
    except NotAvailable as e:
        return None, str(e)
    if safe < depth:
        return None, f"lava, water or a cave {safe + 1} down"
    if carried < depth:
        return None, f"{carried} blocks carried, {depth} to pillar back out"
    return tasks, None

def stair_leg_end(start: Cell, target: Cell) -> Cell:
    """Pure: where one staircase segment from `start` toward `target` ends (nav.stair_dir, nav.STAIR_STEPS down)."""
    d = nav.stair_dir(start, target)
    return (start[0] + d[0] * nav.STAIR_STEPS, start[1] - nav.STAIR_STEPS, start[2] + d[1] * nav.STAIR_STEPS)

def reach_cells(vein, at: Cell) -> list[Cell]:
    """Pure: the vein cells within nav.REACH of the stand at `at`, nearest first."""
    return sorted((p for p in vein if math.dist(p, at) <= nav.REACH), key=lambda p: math.dist(p, at))

def sight_box(at: Cell) -> tuple[Cell, Cell]:
    """Pure: the corners of the read nav.holds needs around the stand at `at`."""
    x0, y0, z0 = at
    return (x0 - 5, y0 - 4, z0 - 5), (x0 + 5, y0 + 6, z0 + 5)

def held_cells(sight, at: Cell, cells, vein) -> set[Cell]:
    """Pure: the cells the jar can break from `at` (nav.holds: sight, not distance), the vein's other cells not in the way."""
    return {p for p in cells if nav.holds(sight, at, p, through=set(vein))}

def open_faced_cells(cells, at, region, held):
    """Pure: the cells sent to the jar: mineable from `at`, and held (a buried one's way is the gate's: plan_way)."""
    return [p for p in mineable(cells, at, region, nav.SAFE_DROP) if p in held]

def opener_pairs(region, cells, at, protected, forced=False):
    """Pure: (cell, the block to break first so the jar sees it: bag.opener) for each cell needing one, never a protected block."""
    return [(c, op) for c in cells if (op := opener(region, c, at, nav.SAFE_DROP, forced=forced)) is not None
            and op not in protected]

def seal_or_wet(region, cells, inv):
    """Pure: (seal_plan's tasks, set(), None); with nothing to seal with, ([], the cells with a fluid face, the reason)."""
    try:
        return seal_plan(region, sorted(cells), inv), set(), None
    except NotAvailable as e:
        return [], {c for c in cells if fluid_faces(region, c, cells)}, e

def stand_refused(cells, opened, why):
    """Pure: the refused cells the jar could hold no stand for ("cannot hold a stand spot"), not opened before."""
    return [c for c in cells if c not in opened and "cannot hold a stand spot" in why]

def partial_refusal(message):
    """Pure: the cells a partly failed batch ("k of n steps failed", k < n) names unreachable; None when not partial."""
    part = re.search(r"(\d+) of (\d+) steps failed", message or "")
    if part and int(part.group(1)) < int(part.group(2)):
        return cannot_reach(message)
    return None


def pick_seed(priced):
    """Pure: the candidate whose way is cheapest — [(cell, seconds | None, the least its way can take)]: an unpriced
    one by that least (a straight walk), never ranked after the priced; None when there are none."""
    best = min(priced, key=lambda c: (c[1] if c[1] is not None else c[2], c[1] is None), default=None)
    return best[0] if best else None

def _cheapest_seed(ctx, hits, start, open_set):
    """The vein to go for: every candidate, by the way plan_way prices for it (walked to when its cell is open, else
    dug) — asked in the order of the least each could take, none asked once that least cannot beat the best
    priced (a buried vein near beat an exposed one a little farther)."""
    cells = sorted(((h["x"], h["y"], h["z"]) for h in hits), key=lambda c: nav.least_way_s(c, start))
    inv, priced = Inventory(), []
    for c in cells:
        lb = nav.least_way_s(c, start)
        if any(p[1] is not None and p[1] <= lb for p in priced):
            break                      # no farther one can be cheaper than one already priced
        region = region_around([start, c], pad=nav.SAFE_DROP + 2)
        seconds = None
        if region is not None:
            walks = nav.plan_walks([c] if c in open_set else [], WORK_REACH)
            seconds = nav.plan_way(region, start, c, "mine", inv, ctx.policy.protected, walks)[2]
        priced.append((c, seconds, lb))
    return pick_seed(priced) or cells[0]

def _go_way(ctx, region, start, target, faces, drop):
    """Walk to an open face or dig the planned way toward `target` (nav.plan_way, said); False when there is none."""
    walks = nav.plan_walks(faces, WORK_REACH)
    steps, why, seconds = nav.plan_way(region, start, target, "mine", Inventory(), ctx.policy.protected, walks)
    if steps is None:
        api.detail(f"  mine {bare(drop)}: no way to {target}: {why}")
        return False
    api.detail(f"  mine {bare(drop)}: way to {target}: {len(steps)} steps, ~{seconds:.0f}s")
    walk = steps[0] if len(steps) == 1 and steps[0]["type"] == "goto" else None
    if walk is not None:
        return nav.arrived_near((walk["x"], walk["y"], walk["z"]), approach_policy(ctx.policy), range_=walk["range"],
                           attempts=1)
    api.run_chain(steps, stop_on_failure=True, wait=120)
    return True

@skill(gives=K.GIVES_MINE, needs=lambda a: {} if a[4] is None else {f"tool:pickaxe:{a[4]}": 1}, start=lambda c: Inventory().count(c.args[1]),
       done=lambda c: Inventory().count(c.args[1]) >= c.base + c.args[2], budget=900, stall=90, when=K.body_when(),
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
        # remembered first, /find only when no noted cell is left; a buried cell's way is planned (nav.plan_way)
        notes = [n for b in blocks for n in ctx.mem.seen(b, ctx.dimension)] if ctx.mem is not None else []
        noted = [h for h in noted_hits(notes, blocks, ctx.blocked, ctx.policy.protected)
                 if (h["x"], h["y"], h["z"]) not in no_cell]
        # every candidate, open or buried, remembered or seen: the way each takes is priced (_cheapest_seed)
        exposed_hits = find(blocks, radius=radius, limit=60, exposed=True)
        raw = list({(h["x"], h["y"], h["z"]): h for h in noted + exposed_hits
                    + find(blocks, radius=radius, limit=60)}.values())
        open_set = {(h["x"], h["y"], h["z"]) for h in exposed_hits}
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
        seed = _cheapest_seed(ctx, hits, start, open_set)
        region = region_around([start, seed], pad=nav.SAFE_DROP + 2)   # deep enough to see a drop (bag.floored)
        if region is None and deep_below(seed, start) and ctx.policy.allow_dig:
            # too far to read at once and far below: the ground a staircase segment crosses is read, and the segment
            # dug — the seed read again from the foot of it (no blind shaft)
            leg = region_around([start, stair_leg_end(start, seed)], pad=2)
            if leg is not None:
                api.detail(f"  mine {bare(drop)}: {seed} too far to read from {start}: a staircase segment down")
                if _go_way(ctx, leg, start, seed, [], drop):
                    continue
        if region is None:
            # too far to read the ground between: walk closer or make a way; only when neither works is the place the problem
            if not nav.arrived_near(seed, ctx.policy, range_=12) and not nav.way_to(ctx, {seed}):
                raise api.NavFailed(f"{blocks[0]} at {seed}: no way there and no tunnel", pos=seed)   # banned by brain.failed
            api.detail(f"  mine {bare(drop)}: {seed} too far to read from {start}, walked closer")
            continue
        hit = next(h for h in hits if (h["x"], h["y"], h["z"]) == seed)
        if ctx.mem is not None and hit.get("noted") and bare(region.name(seed)) not in {bare(b) for b in blocks}:
            for b in blocks:
                ctx.mem.forget_seen(b, seed, ctx.dimension, radius=0.5)     # gone from where it was noted
            api.detail(f"  mine {bare(drop)}: noted {seed} is {region.name(seed)} now, note forgotten")
            continue
        whole = set(connected(region, seed, blocks))      # no way to a vein bans all of it, never cell by cell
        vein = set(mineable((p for p in whole if not ctx.blocked(p)), start, region, nav.SAFE_DROP))
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
        if (FIND_AT.get(mid(drop)) is None and near not in open_set and near[1] < start[1]
                and ctx.policy.allow_dig):
            # a buried surface kind (stone under the soil): straight down, with a way back out and no lava below
            shaft, why = shaft_plan(nav.dig_down_region(start, start[1] - near[1]), start, near,
                                    Inventory().count("building"), ctx.policy.protected)
            if shaft is None:
                for p in whole:
                    ctx.ban(p)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"no shaft down to the {blocks[0]} at {near}: {why}")
                continue
            api.run_chain(shaft, stop_on_failure=True, wait=120)
            continue
        if deep_below(near, start) and ctx.policy.allow_dig:
            # far below: an open face walked to, else a staircase the body can walk back up (nav.plan_way)
            faces = sorted((p for p in vein if p in open_set), key=lambda p: nav.least_way_s(p, start))
            if _go_way(ctx, region, start, near, faces, drop):
                continue
            for p in whole:
                ctx.ban(p)
            unreachable += 1
            _reach_budget(unreachable, blocks, f"no way to the {blocks[0]} vein at {seed}")
            continue
        walked = nav.arrived_near(near, ctx.policy, range_=3.5, attempts=1) if "travel" in nav.mod_features() else False
        if not walked and not nav.way_to(ctx, vein):
            for p in whole:
                ctx.ban(p)
            # the next vein is another target, not a retry: failing the step over one vein cooled the goal
            unreachable += 1
            _reach_budget(unreachable, blocks, f"no way and no tunnel to the {blocks[0]} vein at {seed}")
            continue
        # only blocks within reach of where travel left us, a dozen at a time
        here_now = feet()
        in_reach = reach_cells(vein, here_now)
        if not in_reach:
            near_cell = min(vein, key=lambda p: math.dist(p, here_now))
            if not nav.arrived_near(near_cell, ctx.policy, range_=2.0, attempts=1) and not nav.way_to(ctx, {near_cell}):
                for p in whole:
                    ctx.ban(p)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"{blocks[0]} at {near_cell}: no way there and no tunnel")
                continue
            here_now = feet()
            in_reach = reach_cells(vein, here_now)
            if not in_reach and not nav.way_to(ctx, vein):
                for p in whole:
                    ctx.ban(p)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"got near {near_cell} but no way in to the {blocks[0]}")
                continue
        # distance is not reachability: only open-faced blocks go to mine_many; travel digs a way to the nearest buried one
        held = held_cells(Region(*sight_box(here_now)), here_now, in_reach, vein)
        open_faced = open_faced_cells(in_reach, here_now, region, held)
        if not open_faced:
            buried = in_reach[0]
            if not nav.arrived_near(buried, ctx.policy, range_=BESIDE, attempts=1):
                ctx.ban(buried)
                unreachable += 1
                _reach_budget(unreachable, blocks, f"{blocks[0]} at {buried}: buried, and no way dug to it")
            continue
        vein = set(open_faced[:MINE_BATCH])
        if not ctx.policy.lava_ok:
            # seal every fluid face of what is about to break (seal_plan); with nothing to seal with, ban those cells (unless the goal wants the fluid)
            seal, wet, e = seal_or_wet(region, vein, Inventory())
            if e is not None:
                log(f"   {len(wet)} {blocks[0]} cells not mined: {e}")
                for p in wet:
                    ctx.ban(p)
                vein -= wet
                if not vein:
                    continue
            if seal:
                api.run_chain(seal, stop_on_failure=True, wait=60)
        # a cell open only into pockets no body can stand in: the jar's mine never digs for a line of sight, so the
        # block on the body's side goes first (bag.opener; down-flagged when it lies below the feet)
        openers = sorted({op for _c, op in opener_pairs(region, vein, here_now, ctx.policy.protected)})
        if openers:
            api.run_chain([nav.mine_task(op, down=op[1] < here_now[1]) for op in openers], stop_on_failure=True, wait=60)
        before = Inventory().count(drop)
        sent |= set(vein)
        try:
            batch = mine_segment_commands({"inv": Inventory(), "feet": here_now}, (vein, drop, tier))
            r = nav.run_cells("mine_many", batch[:-1], then=batch[-1], wait=TASK_WAIT_S)
        except api.Unreachable as out:
            around = {c: region.name(c) for c in out.cells or ()}
            api.detail(f"  mine {bare(drop)} refused by the jar ({out}): feet {feet()}, cells "
                       + "; ".join(f"{c} {n} faces {[region.name(cell_add(c, d)) for d in nav.NEIGHBOURS6]}" for c, n in around.items()))
            # "cannot hold a stand spot": the jar found no spot that keeps the cell in sight (a top face seen only
            # from a pit's rim, search_night_resume 09:17:19) — open a side face once, then ask again; a ban only
            # when the opened cell is refused too
            held = stand_refused(out.cells or (), opened, str(out))
            sides = opener_pairs(region, held, feet(), ctx.policy.protected, forced=True)
            if sides:
                opened.update(c for c, _op in sides)
                api.run_chain([nav.mine_task(op, down=op[1] < feet()[1]) for _c, op in sides], stop_on_failure=True,
                              wait=60)
                continue
            # "cannot reach": a planned way to a standing spot (nav.way_to), then ask again; a way that changed
            # nothing counts against the budget
            if nav.way_to(ctx, out.cells or vein):
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
            if MINE_YIELD.get(mid(drop), 1) < 1 and "failed" not in r["message"]:
                continue   # a chance drop (grass → seeds ~1 in 8): an empty break is expected, keep breaking
            # partial success is progress: some blocks broke, keep the batch (banning all over one block stalled the kit)
            bad = partial_refusal(r.get("message"))
            if bad is not None:
                # "no path" to a block inside rock: dig one face open and it is ordinary
                again, _ = refused(bad, tried)
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
                    ops = sorted({op for _c, op in opener_pairs(now, broken, here, ctx.policy.protected, forced=True)})
                    api.detail(f"  mine {bare(drop)}: {len(broken)} broken, nothing in the bag: dig-out {ops}, then a sweep")
                    api.run_chain([nav.mine_task(op, down=op[1] < here[1]) for op in ops]
                                  + [{"type": "collect", "radius": 6}], wait=60)
                    continue
            again, _ = refused(vein, tried)
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
        elif ctx.mem is not None:
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

@skill(gives=["state:tunnelled"], remaining=_k.tunnelled(lambda c: c.args[1] if len(c.args) > 1 else 16), pre=[_not_in_water], needs={"tool:pickaxe:0": 1},
       start=lambda c: (feet()[1], _stone_held()),
       verify=lambda c: feet()[1] != c.base[0] or _stone_held() > c.base[1], budget=300, stall=60)
def strip_mine_step(ctx, length=16):
    """Always-available work: descend toward iron (diamond once an iron pickaxe exists) and tunnel, taking the ore revealed."""

    s = api.get("/state")
    fx, fy, fz = s["blockX"], s["blockY"], s["blockZ"]
    tools = Inventory().tools("pickaxe")
    best_tier = max((t for t, d, _ in tools if K.working(d)), default=0)
    # diamonds peak near -58, bedrock starts at -60..-64: tunnel at -54
    depth = -54 if any(t >= 2 and d >= 20 for t, d, _ in tools) else 16
    if fy < depth - 3:
        # drifted below the band: staircase back up rather than tunnel into bedrock
        if not nav.arrived_near((fx, depth, fz), ctx.policy, range_=2, attempts=1):
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
            if not nav.arrived_near((t["x"], t["y"] + 1, t["z"]), ctx.policy, range_=2, attempts=1):
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
            except NotAvailable as e:
                log(f"   strip mining: {token} left: {e}")

def _hunt_seen(types):
    """The prey in sight as a detail line: id and distance each."""
    return ", ".join(f"{n['id']}@{n['distance']:.1f}" for n in entities(64, types)) or "none"

def _hunt_progress(token, types):
    near = entities(64, types)
    # closing in (4-block bins) or collecting drops is progress; circling is not
    return Inventory().count(token), (int(near[0]["distance"] // 4) if near else None)

@skill(gives=K.GIVES_HUNT, needs=lambda a: {"tool:sword:1": 1} if beliefs.fights_back(a[3]) else {},
       fights=lambda c: c.args[3] if beliefs.fights_back(c.args[3]) else (),
       when=lambda s, f: K.body_when(footing=False, surface=True)(s, f) + K.lives_in(s.detail.get("types") or ()),
       start=lambda c: Inventory().count(c.args[1]), done=lambda c: Inventory().count(c.args[1]) >= c.base + c.args[2],
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
            nav.arrived_near((math.floor(e["x"]), math.floor(e["y"]), math.floor(e["z"])), approach_policy(ctx.policy),
                      range_=3, attempts=2)
            e = next((n for n in entities(64, types) if n["id"] == e["id"]), None)
            if e is None or e["distance"] > 6:
                ctx.ban((prey[0]["id"], 0, 0), 300)
                raise api.NavFailed(f"could not get to the {bare(types[0])}", pos=(prey[0]["id"], 0, 0))
        api.detail(f"   hunt: prey {e['id']} at {(round(e['x'], 1), round(e['y'], 1), round(e['z'], 1))} "
                   f"{e['distance']:.1f} off; {_hunt_seen(types)}")
        try:
            got = api.run({"type": "attack", "entity": e["id"]}, wait=30, awaits="the mob dead")
            api.detail(f"   hunt: attack {got.get('status')} {got.get('message', '')}; after: {_hunt_seen(types)}")
            bagged = Inventory().count(token)
            # the drop can land where nothing stands: `sweep` makes a way to it before a kill is written off
            nav.walk_sweep(ctx, radius=6, only=[token], wait=60)
            api.detail(f"   hunt: {bare(token)} {before} before, {bagged} after the attack, "
                       f"{Inventory().count(token)} after the sweep")
        except api.TaskStuck:
            ctx.ban((prey[0]["id"], 0, 0), BAN_MAX_S)  # unreachable (across water, on a ledge)
            raise api.NavFailed(f"the {bare(types[0])} is out of reach for attacks", pos=(prey[0]["id"], 0, 0))
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
                or any(region.hazard(c) or region.hazard(cell_add(c, n))
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
    assert best is not None, "TUNNEL_DIRS is not empty"
    _score, d, end, cave = best
    return d, end, (cave if end == 0 else None)

def _take_needs(token):
    """The tool a take of `token` needs, from the one table (knowledge.TAKEABLE's `tool`, as MINE's tier is mine's)."""
    tool = TAKEABLE.get(token, {}).get("tool")
    return {} if tool is None else {f"tool:{tool[0]}:{tool[1]}": 1}

@skill(gives=K.GIVES_TAKE, needs=lambda a: _take_needs(a[1]), start=lambda c: Inventory().count(c.args[1]), verify=lambda c: Inventory().count(c.args[1]) > c.base,
       budget=180, stall=45, provides={"take": lambda ctx, s: (s.token, s.count, s.detail["blocks"])}, when=K.body_when(surface=True),
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
        if not nav.arrived_near(cell, ctx.policy, range_=3, attempts=2):
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

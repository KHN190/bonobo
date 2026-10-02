"""Renewable food and wood: replant saplings after chopping, a 3×3 wheat plot around a water source, harvest when ripe, breed animals with wheat. Everything that grows is a job (jobs.py) collected later by upkeep. Pure planners (`farm_plot`, `ripe_cells`, `breeding_pair`) are offline-tested; skills only execute them."""
from __future__ import annotations

import dataclasses
import math
import time

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import knowledge as K
from . import api, jobs, nav, skillcore
from . import world
from .api import McError, NotAvailable, log, swallowed
from .data import BAN_MAX_S, EYE_HEIGHT, bare
from .skill import skill
from .skillcore import body_state, gained
from .knowledge import BREED_FOOD
from .world import Inventory, Region, add, entities, find, job_ready, ripe_cells, ripe_near  # noqa: F401  (ripe_*: world facts)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .shapes import Task

SOIL = ("grass_block", "dirt", "coarse_dirt", "rooted_dirt")
SAPLINGS = ("oak_sapling", "spruce_sapling", "birch_sapling", "jungle_sapling", "acacia_sapling",
            "dark_oak_sapling", "cherry_sapling")
RING = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dz) != (0, 0)]

# ---------------------------------------------------------------- pure planners

def farm_plot(region, here, protected=(), radius=8):
    """Pure: a 3×3 plot centre: nine soil cells at one height, two air above each, none protected."""

    best = None
    for (x, y, z), name in region.blocks.items():
        if name not in SOIL or math.dist((x, y, z), here) > radius:
            continue
        cells = [(x, y, z)] + [(x + dx, y, z + dz) for dx, dz in RING]
        ok = all(region.name(c) in SOIL and c not in protected and region.name(add(c, (0, 1, 0))) in ("air", "short_grass")
                 and region.name(add(c, (0, 2, 0))) == "air" for c in cells)
        if ok:
            d = math.dist((x, y, z), here)
            if best is None or d < best[0]:
                best = (d, (x, y, z))
    return None if best is None else best[1]

def breeding_pair(animals, kind, max_gap=8):
    """Pure: two adult animals of `kind` close to each other (ids), or None. `animals` are /entities entries."""
    same = [e for e in animals if e["type"] == kind and not e.get("baby")]
    for i, a in enumerate(same):
        for b in same[i + 1:]:
            if math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"])) <= max_gap:
                return a["id"], b["id"]
    return None

# ---------------------------------------------------------------- skills

def sow_commands(cells, seeds="minecraft:wheat_seeds"):
    """Pure: one sowing per soil cell, back to back (a harvest's resow)."""
    return [nav.use_on_top(seeds, c, top=nav.FARMLAND_TOP) for c in cells]      # seeds go on farmland: 15/16 high

def ring_commands(centre, hoe, region=None) -> "list[Task]":
    """Pure: the ring worked from ON the centre block, turning 45° a cell (every cell within 1.5, nothing between the
    eye and a cell's top): each cell tilled, then sown at once — what the world already shows is skipped."""
    name = (lambda c: region.name(c)) if region is not None else (lambda c: None)
    out = []
    for dx, dz in RING:
        cell = (centre[0] + dx, centre[1], centre[2] + dz)
        if name(cell) != "farmland":
            out.append(nav.use_on_top(hoe, cell))
        if name(add(cell, (0, 1, 0))) != "wheat":
            out.append(nav.use_on_top("minecraft:wheat_seeds", cell, top=nav.FARMLAND_TOP))   # tilled: 15/16 high
    return out

def finish_commands(centre, stand, region=None):
    """Pure: from the stand beside the plot, one send — the centre dug (the jar picks the best tool: a shovel takes
    dirt and grass at once) unless it is open already, then the water aimed at the far inner wall."""
    name = (lambda c: region.name(c)) if region is not None else (lambda c: None)
    out = [] if name(centre) in ("air", "water") else [nav.mine_task(centre)]
    if name(centre) != "water":
        out.append(water_task(centre, stand, region))
    return out

def plot_commands(centre, hoe, region=None, stand=None):
    """Pure: the whole plot as the tasks it takes, in order — the ring from the centre block (ring_commands), then
    the centre dug and watered from the stand (finish_commands); a resume computes the rest from the world."""
    return ring_commands(centre, hoe, region) + finish_commands(centre, stand, region)


def water_task(centre, stand=None, region=None):
    """Pure: the pour into the centre hole. From a stand beside the plot the rim hides the hole's floor (the ray met
    the ring's top: water on the ring, bread_from_a_farm 13268), so the aim is the far inner wall — the ring block past
    the centre on the side away from the stand, its face toward the hole: a bucket used there pours into the hole.
    With the blocks read, the aim must be the first solid thing the stand's eye meets; else the floor's top."""
    below = add(centre, (0, -1, 0))
    if stand is None:
        return nav.use_on_top("minecraft:water_bucket", below)
    dx, dz = centre[0] - stand[0], centre[2] - stand[2]
    step = ((1 if dx > 0 else -1), 0) if abs(dx) >= abs(dz) else (0, (1 if dz > 0 else -1))
    wall = (centre[0] + step[0], centre[1], centre[2] + step[1])
    task = nav.use_on_face("minecraft:water_bucket", wall, (-step[0], -step[1]))
    if region is not None:
        eye = (stand[0] + 0.5, stand[1] + EYE_HEIGHT, stand[2] + 0.5)
        if nav.first_solid(region, eye, (task["x"], task["y"], task["z"])) != wall:
            floor = nav.use_on_top("minecraft:water_bucket", below)
            if nav.first_solid(region, eye, (floor["x"], floor["y"], floor["z"])) == below:
                return floor
    return task

def plot_cells(centre):
    """Pure: the cells a walk must neither dig nor build in while the plot is made: the centre hole and the cell under
    it, the ring and the crop layer over it."""
    x, y, z = centre
    ring = {(x + dx, y, z + dz) for dx, dz in RING}
    return {(x, y, z), (x, y - 1, z)} | ring | {(c[0], c[1] + 1, c[2]) for c in ring}

def water_contained(region, cell):
    """Pure: water at `cell` stays a source there — solid under it and on its four sides at its own level, so it
    flows nowhere (the plot's centre hole: the ring's blocks hold it). Water poured onto the ground, not into a hole,
    is not contained: it runs over the ring."""
    x, y, z = cell
    return region.solid((x, y - 1, z)) and all(region.solid((x + dx, y, z + dz)) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))

def started_plot(region, here, radius=8):
    """Pure: the centre of a plot begun and unfinished here, nearest first, or None."""

    best = None
    farmland = {c for c, n in region.blocks.items() if n == "farmland"}
    centres = {c for c, n in region.blocks.items() if n == "water"} | \
        {(r[0] - dx, r[1], r[2] - dz) for r, n in region.blocks.items() if n in SOIL + ("farmland",)
         for dx, dz in RING if region.name((r[0] - dx, r[1], r[2] - dz)) in ("air", None)
         and region.solid((r[0] - dx, r[1] - 1, r[2] - dz))} | \
        {c for c in {(r[0] - dx, r[1], r[2] - dz) for r in farmland for dx, dz in RING}      # ring begun, centre not dug
         if region.name(c) in SOIL and all(max(abs(f[0] - c[0]), abs(f[2] - c[2])) <= 1 for f in farmland
                                          if f[1] == c[1] and max(abs(f[0] - c[0]), abs(f[2] - c[2])) <= 2)}
    for c in sorted(centres):
        if math.dist(c, here) > radius:
            continue
        ring = [(c[0] + dx, c[1], c[2] + dz) for dx, dz in RING]
        if all(region.name(r) in SOIL + ("farmland",) for r in ring) and \
                (any(region.name(add(r, (0, 1, 0))) != "wheat" for r in ring) or region.name(c) != "water"):
            d = math.dist(c, here)
            if best is None or d < best[0]:
                best = (d, c)
    return None if best is None else best[1]

def click_line(task, reply):
    """Pure: one use_item's detail line from what the jar reports anyway — the item, the aim sent, the status and
    message, the block and face hit, the task's own seconds."""
    res = reply.get("result") or {}
    return (f"click {task['item'].split(':')[-1]} aim ({task['x']:.2f}, {task['y']:.2f}, {task['z']:.2f}) → "
            f"{reply.get('status')} {reply.get('message') or ''} | hit {res.get('hitX', '-')},{res.get('hitY', '-')},"
            f"{res.get('hitZ', '-')} {res.get('face', '')} {res.get('blockResult', '')} in {reply.get('seconds', '-')} s")

def unreachable_cells(tasks, results):
    """Pure: the cells the jar refused as out of reach — the only ones a partial chain bans."""

    return sorted({(t["x"] - 0.5, float(math.floor(t["y"] - 0.05)), t["z"] - 0.5) if isinstance(t["x"], float)
                   else (t["x"], t["y"], t["z"])
                   for t, r in zip(tasks, results)
                   if r.get("status") != "succeeded" and "reach" in str(r.get("message", "")).lower()})

def plant_farm_commands(state, args, stand=None):
    """`commands` for plant_farm: the plot chain at the nearest flat 3×3 soil with the best hoe; NotAvailable names what is missing."""

    inv = state["inv"]
    hoe = next((h for h in HOES if inv.count(h)), None)
    if hoe is None:
        raise NotAvailable("no hoe")
    if inv.count("minecraft:wheat_seeds") < 8:
        raise NotAvailable("need 8 wheat seeds")
    region = state["region"]
    centre = started_plot(region, state["feet"]) or farm_plot(region, state["feet"], state.get("protected", ()))
    if centre is None:
        raise NotAvailable("no flat 3×3 soil nearby for a farm")
    if region.name(centre) != "water" and not inv.count("minecraft:water_bucket"):
        raise NotAvailable("need a water bucket for the plot")          # poured already: the bucket is not asked again
    return plot_commands(centre, hoe, region, stand)

def feed_commands(pair, food):
    """Pure: feed both animals of a breeding pair, back to back (the second needs nothing from the first)."""
    return [{"type": "interact", "entity": eid, "item": food} for eid in pair]

def breed_commands(state, args):
    """`commands` for breed: the first kind with food carried and an adult pair away from a cooling breeding; [] when none."""

    for kind, food in BREED_FOOD.items():
        if state["inv"].count(food) < 2:
            continue
        pair = breeding_pair(state["entities"], kind)
        if pair is None:
            continue
        where = next(e for e in state["entities"] if e["id"] == pair[0])
        pos = (round(where["x"]), round(where["y"]), round(where["z"]))
        if any(math.dist(c, pos) <= 12 for c in state.get("cooling", ())):
            continue
        return feed_commands(pair, food)
    return []

def sapling_in_bag():
    inv = Inventory()
    return next((f"minecraft:{s}" for s in SAPLINGS if inv.count(f"minecraft:{s}")), None)

def replant(ctx, base):
    """After felling a trunk: put a sapling on the soil it grew from and start a sapling job (a tree in ~20 min)."""

    sapling = sapling_in_bag()
    if sapling is None:
        return False
    soil = (base[0], base[1] - 1, base[2])
    region = Region(add(soil, (0, 0, 0)), add(soil, (0, 2, 0)))
    if region.name(soil) not in SOIL or region.name(base) != "air":
        return False
    # one task, nothing to chain; its result decides the sapling job
    r = api.run({"type": "place", "item": sapling, "x": base[0], "y": base[1], "z": base[2]}, wait=30, awaits="the sapling placed or not decides the next spot")
    if r["status"] != "succeeded":
        return False
    jobs.start(ctx.mem, "sapling", base, ctx.dimension, item="log", count=4)
    log(f"replanted {sapling.split(':')[1]} at {base}")
    return True

def check_sapling(ctx, job):
    """A sapling job is due: a grown tree becomes a tree resource point; still a sapling → check again later."""
    pos = tuple(job["pos"])
    names = Region(pos, add(pos, (0, 1, 0))).blocks
    if any(n.endswith("_log") for n in names.values()):
        ctx.mem.note_seen("tree", pos, ctx.dimension)
        ctx.mem.finish_job(job["id"])
        log(f"sapling at {pos} grew into a tree")
    elif any(n.endswith("_sapling") for n in names.values()):
        ctx.mem.postpone_job(job["id"], 5 * 60)
    else:
        ctx.mem.finish_job(job["id"])   # eaten, broken or never took

def _plot_growing(centre):
    """Verify farmland and wheat really exist around the centre, not just that the clicks succeeded."""
    if centre is None:
        return False
    names = Region(add(centre, (-1, 0, -1)), add(centre, (1, 1, 1))).blocks.values()
    return sum(n == "farmland" for n in names) >= 1 and sum(n == "wheat" for n in names) >= 1

def _crop_held():
    """What of the crop the bag holds now: (wheat, seeds) -- a reap must raise one of them."""
    bag = Inventory()
    return bag.count("minecraft:wheat"), bag.count("minecraft:wheat_seeds")

def farm_done(result, base, held, growing):
    """Did plant_farm change the world? A reap: the bag gained wheat or seeds since the call's start
    (`base`, from `held()` then). A plot: `growing(centre)` sees farmland and wheat. The skill's own
    return alone proves nothing."""
    if result == REAPED:
        return base is not None and any(h > b for h, b in zip(held(), base))
    return bool(result) and growing(result)

@skill(gives=K.GIVES_FARM, needs={"minecraft:wheat_seeds": 1, "minecraft:water_bucket": 1, "tool:hoe:0": 1}, commands=lambda state, args: plant_farm_commands(state, args), start=lambda c: _crop_held(), verify=lambda c: farm_done(c.result, c.base, _crop_held, _plot_growing), budget=300, stall=90,
       provides={"farm": lambda ctx, s: ()})
def plant_farm(ctx):
    """Wheat: reap a grown crop nearby first, else make a 3×3 plot (dig, water, till, sow) and start a crop job."""

    ripe = ripe_near(world.feet(), RIPE_LOOK)
    if ripe and _reap(ripe) > 0:
        return REAPED
    here = world.feet()
    state = body_state(ctx, Region(add(here, (-9, -3, -9)), add(here, (9, 3, 9))))
    region = state["region"]
    centre = started_plot(region, here) or farm_plot(region, here, ctx.policy.protected)
    if centre is None:
        raise NotAvailable("no 3×3 of soil with room above within 8 blocks for a plot")
    stand = (centre[0] - 2, centre[1] + 1, centre[2])
    hoe = next((h for h in HOES if state["inv"].count(h)), None)
    if hoe is None:
        raise NotAvailable("no hoe")
    # every walk here keeps off the plot's cells (no dig, no floor block: a walk once filled the dug centre)
    policy = dataclasses.replace(ctx.policy, protected=ctx.policy.protected | plot_cells(centre))    # home boxes kept
    below = add(centre, (0, -1, 0))

    def column():
        r = Region(below, centre)
        return f"centre {centre} {r.name(centre)}, under {below} {r.name(below)}"

    # 1. the ring from ON the centre block, turning: every cell within 1.5, no rim between the eye and a top
    on_centre = (centre[0], centre[1] + 1, centre[2])
    if not nav.arrived(on_centre, policy, range_=0.3, attempts=1):
        raise api.NavFailed(f"the plot's centre {centre} not reachable to stand on")
    ring = ring_commands(centre, hoe, Region(add(centre, (-1, 0, -1)), add(centre, (1, 1, 1))))
    # one send for the whole ring (a segment is a round trip and an idle queue between): its time logged
    t0 = time.time()
    done = api.run_chain(ring, stop_on_failure=False, segment=max(1, len(ring))) if ring else []
    wall = time.time() - t0
    inside = sum(float(r.get("seconds") or 0) for r in done)
    # each task's own seconds (turn, sneak, click) against the chain's wall: the rest is between tasks (queue, polls)
    api.detail(f"  plot ring: {len(ring)} clicks, wall {wall:.2f} s, in the tasks {inside:.2f} s, between "
               f"{wall - inside:.2f} s; per click " + " ".join(f"{float(r.get('seconds') or 0):.2f}" for r in done))
    for t, r in zip(ring, done):
        res = r.get("result") or {}
        hit = (res.get("hitX"), res.get("hitY"), res.get("hitZ"))
        api.detail("  " + click_line(t, r) + f" | hit block now: {Region(hit, hit).name(hit) if None not in hit else '-'}")
    top = Region(add(centre, (-1, 0, -1)), add(centre, (1, 1, 1)))
    api.detail("  plot ring after: " + ", ".join(
        f"{dx:+d}{dz:+d} {top.name((centre[0] + dx, centre[1], centre[2] + dz))}/"
        f"{top.name((centre[0] + dx, centre[1] + 1, centre[2] + dz))}" for dx, dz in RING))
    for cell in unreachable_cells(ring, done):
        ctx.ban(tuple(int(round(v)) for v in cell), BAN_MAX_S)

    # 2. off the plot to the stand, walking; then dig the centre and pour in one send
    if not nav.arrived(stand, policy, range_=0.5, attempts=1):
        raise api.NavFailed(f"the plot's stand {stand} not reachable")
    seen = Region(add(centre, (-3, -1, -2)), add(centre, (2, 3, 2)))
    finish = finish_commands(centre, stand, seen)
    api.detail(f"  plot finish: stand {stand}, feet {world.feet()}, {column()}")
    done += api.run_chain(finish, stop_on_failure=False) if finish else []
    tasks = ring + finish

    # 3. one read-back: water held (the server's view when the bench asks it, else a client read that holds)
    if not centre_holds(centre, "water"):
        raise McError(f"could not pour the plot's water: {column()}{server_view(centre)}")
    after = Region(add(centre, (-1, -1, -1)), add(centre, (1, 1, 1)))
    if not water_contained(after, centre):
        raise McError(f"the plot's water at {centre} is not held by the ring: it runs over the plot")
    # read again until the world shows what the clicks did (a read right after the chain lagged: "a plot of 3")
    clicks = sum(1 for t, r in zip(tasks, done) if t.get("item") == "minecraft:wheat_seeds" and r.get("status") == "succeeded")

    def sown_now():
        top = Region(add(centre, (-1, 1, -1)), add(centre, (1, 1, 1)))
        return sum(1 for dx, dz in RING if top.name((centre[0] + dx, centre[1] + 1, centre[2] + dz)) == "wheat")
    sown = skillcore.settle(sown_now, lambda n: n >= clicks, timeout=2.0, stable_s=0)
    yield sown
    if not sown:
        raise McError("no farm cell could be sown")
    ctx.mem.add_site("farm", centre, ctx.dimension, name=f"farm-{centre[0]}_{centre[2]}")
    jobs.start(ctx.mem, "crop", centre, ctx.dimension, item="minecraft:wheat", count=sown)
    log(f"planted a wheat plot of {sown} at {centre}")
    return centre

HOLD_S = 0.25            # a block read the same for 5 ticks is the server's, not the client's prediction

def centre_holds(pos, block):
    """The server holds `block` at `pos`: the bench's probe asks it (an `execute if block`); else the client's read
    held for HOLD_S (a broken block reads air on the client before the server agrees, and may come back)."""
    probe = PROBE.get("server_block")
    if probe is not None:
        try:
            said = probe(pos, block)
            if said is not None:               # no answer from the server: the client's held read decides
                return said
        except McError as e:
            swallowed("farming.centre_holds", e)
    got = skillcore.settle(lambda: Region(pos, pos).name(pos), lambda n: n == bare(block), timeout=2.0, stable_s=HOLD_S)
    return got == bare(block)

def server_view(pos):
    """'; server: <block?>' for the log when the bench's probe is wired, else ''."""
    probe = PROBE.get("server_block")
    if probe is None:
        return ""
    try:
        return "; server: " + ", ".join(f"{b} {probe(pos, b)}" for b in ("air", "water"))
    except McError as e:
        return f"; server probe failed: {e}"

# hooks the bench wires (production leaves them empty): {"server_block": fn(pos, block) → bool, the server's answer to
# an `execute if block`; "tick_speed": fn() → the random_tick_speed in effect}
PROBE = {}

def crop_ages(centre):
    """The ring's crop layer, read with block states: [(cell, block, age)] (the await's evidence)."""
    x, y, z = centre
    region = Region((x - 1, y + 1, z - 1), (x + 1, y + 1, z + 1), props=True)
    return [((x + dx, y + 1, z + dz), region.name((x + dx, y + 1, z + dz)), region.prop((x + dx, y + 1, z + dz), "age"))
            for dx, dz in RING]

AWAIT_MAX_S = 45        # longest an await step waits in place for a job; longer, it steps aside (NotAvailable)

def job_due(job, tick=None):
    """A job's output can be taken now: a crop when ripe wheat stands on its plot (the world, not the clock: crops
    ripen by random ticks), anything else by its clock."""
    if job.get("kind") == "crop":
        c = tuple(job["pos"])
        return bool(ripe_cells(Region(add(c, (-1, 1, -1)), add(c, (1, 1, 1)), props=True)))
    return job_ready(job, tick)

@skill(gives=["state:job_collected"], remaining=_k.more_than_at_start(lambda c: c.args[1], lambda c: c.args[2]),
       needs={}, start=lambda c: Inventory().count(c.args[1]),
       verify=lambda c: Inventory().count(c.args[1]) > c.base, budget=120, stall=60,
       provides={"await": lambda ctx, s: (s.token, s.count)})
def await_job(ctx, item, count):
    """What the plan takes from a running job (a sown crop, a furnace): waited for in place while it is near — then
    collected (jobs.collect) — or stepped aside from (NotAvailable) when it is not."""
    began = time.time()
    while True:     # bound: AWAIT_MAX_S below
        mine = [j for j in ctx.mem.jobs(ctx.dimension) if j.get("item") == item]
        if not mine:
            raise NotAvailable(f"no job is making {bare(item)}")
        tick = api.get("/state").get("gameTime")
        due = next((j for j in mine if job_due(j, tick)), None)
        if due is not None:
            jobs.collect(ctx, due)
            return
        if time.time() - began > AWAIT_MAX_S:
            raise NotAvailable(f"{bare(item)} not ready yet")
        for j in mine:                     # instrumented: each wait's crop ages, the tick speed once
            if j.get("kind") == "crop":
                speed = ""
                if "tick_speed" in PROBE and not getattr(await_job, "_speed_logged", False):
                    try:
                        speed = f"; random_tick_speed in effect: {PROBE['tick_speed']()}"
                    except McError as e:
                        speed = f"; tick speed probe failed: {e}"
                    await_job._speed_logged = True
                api.detail(f"  await {bare(item)} at {tuple(j['pos'])}: tick {tick}, ages "
                           + ", ".join(f"{c[0] - j['pos'][0]:+d}{c[2] - j['pos'][2]:+d} {bare(n or '-')} {a}"
                                       for c, n, a in crop_ages(tuple(j["pos"]))) + speed)
        api.run({"type": "wait", "ticks": 20}, wait=5, awaits="one second more for the job's output")
        yield round(time.time() - began)      # waiting on a clock is the progress here

HOES = tuple(f"minecraft:{m}_hoe" for m in ("netherite", "diamond", "iron", "golden", "stone", "wooden"))
RIPE_LOOK = 16          # how far a farm step looks for a crop already grown before it sows
REAPED = "reaped"       # plant_farm's answer when it took a grown crop instead of sowing

def _reap(cells):
    """Break these ripe wheat cells and collect wheat and seeds; the wheat gained."""
    before = Inventory().count("minecraft:wheat")
    # one batch: every ripe cell, then one pickup (read before the farmland is replanted)
    batch = nav.mine_batch(cells, nav.feet(), collect=True, only=["minecraft:wheat", "minecraft:wheat_seeds"])
    nav.run_cells("mine_many", batch[:-1], then=batch[-1], wait=120)
    got = gained(lambda: Inventory().count("minecraft:wheat"), before) - before
    log(f"reaped {got} wheat from {len(cells)} ripe cells")
    return got

def harvest(ctx, job):
    """A crop job is due: break ripe wheat, collect wheat + seeds, resow, schedule the next harvest."""
    centre = tuple(job["pos"])
    region = Region(add(centre, (-1, 1, -1)), add(centre, (1, 1, 1)), props=True)
    ripe = ripe_cells(region)
    if not ripe:
        ctx.mem.postpone_job(job["id"], 5 * 60)
        raise NotAvailable(f"wheat at {centre} not ripe yet")
    if not nav.arrived((centre[0] - 2, centre[1] + 1, centre[2]), ctx.policy, range_=2.0, attempts=1):
        raise api.NavFailed(f"farm at {centre} not reachable", pos=centre)
    got = _reap(ripe)
    seeds = Inventory().count("minecraft:wheat_seeds")
    if seeds:
        # the resow in one send; a cell the jar cannot sow stays bare
        api.run_chain(sow_commands([add(p, (0, -1, 0)) for p in ripe][:seeds]), stop_on_failure=False)
    ctx.mem.finish_job(job["id"])
    jobs.start(ctx.mem, "crop", centre, ctx.dimension, item="minecraft:wheat", count=len(ripe))
    log(f"harvested {got} wheat at {centre}")
    if got <= 0:
        raise NotAvailable("harvest yielded no wheat")
    return got

def _babies():
    """Young animals of the kinds we breed within 24 blocks: what a breeding makes (the jar reports `baby`)."""
    return sum(1 for e in entities(24, list(BREED_FOOD)) if e.get("baby"))

@skill(gives=["state:bred"], remaining=_k.babies, needs={}, start=lambda c: _babies(), verify=lambda c: _babies() > c.base, budget=180, stall=60,
       commands=lambda state, args: breed_commands(state, args),
       provides={"breed": lambda ctx, s: ()})
def breed(ctx):
    """Feed two adults of one kind the food they breed on; a breed job marks the 5-minute cooldown there."""
    animals = entities(24)
    state = body_state(ctx, entities=animals,
                       cooling=[j["pos"] for j in ctx.mem.jobs(ctx.dimension) if j["kind"] == "breed"])
    tasks = breed_commands(state, ())
    if not tasks:
        raise NotAvailable("no pair of animals with the food to breed them")
    food = tasks[0]["item"]
    kind = next(k for k, f in BREED_FOOD.items() if f == food and any(
        e["id"] == tasks[0]["entity"] and e["type"] == k for e in animals))
    where = next(e for e in animals if e["id"] == tasks[0]["entity"])
    pos = (round(where["x"]), round(where["y"]), round(where["z"]))
    for t in api.run_chain(tasks, stop_on_failure=True):
        if t["status"] != "succeeded":
            raise McError(f"feeding {kind.split(':')[1]} failed: {t['message']}")
    yield kind
    jobs.start(ctx.mem, "breed", pos, ctx.dimension, item=kind)
    ctx.mem.note_seen(kind, pos, ctx.dimension)
    log(f"bred two {kind.split(':')[1]} at {pos}")
    return kind

jobs.COLLECT.update(crop=harvest, sapling=check_sapling)


def fish(ctx, seconds):
    """Shell, never planned: cast into water near, reel in bites for `seconds`."""
    raise NotImplementedError("fish: a shell")

"""Renewable food and wood: replant saplings after chopping, a 3×3 wheat plot around a water source, harvest when
ripe, breed animals with wheat. Everything that grows is a job (jobs.py) collected later by upkeep.
Pure planners (`farm_plot`, `ripe_cells`, `breeding_pair`) are offline-tested; skills only execute them."""
import math

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import knowledge as K
from . import api, jobs, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .skillcore import body_state, gained
from .knowledge import BREED_FOOD
from .world import Inventory, Region, add, entities, find, ripe_cells, ripe_near  # noqa: F401  (ripe_*: world facts)

SOIL = ("grass_block", "dirt", "coarse_dirt", "rooted_dirt")
SAPLINGS = ("oak_sapling", "spruce_sapling", "birch_sapling", "jungle_sapling", "acacia_sapling",
            "dark_oak_sapling", "cherry_sapling")
RING = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dz) != (0, 0)]


# ---------------------------------------------------------------- pure planners

def farm_plot(region, here, protected=(), radius=8):
    """Pure: a centre cell for a 3×3 plot — the centre and its 8 neighbours are soil at one height with two air
    cells above (nothing to clear), none protected. The centre becomes the water source. Nearest first."""
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

def use_on_top(item, cell):
    """Pure: the task that uses `item` on the top face of `cell` (till, sow, pour)."""
    return {"type": "use_item", "item": item, "x": cell[0] + 0.5, "y": cell[1] + 1.0, "z": cell[2] + 0.5,
            "onBlock": True}


def sow_commands(cells, seeds="minecraft:wheat_seeds"):
    """Pure: one sowing per soil cell, back to back (a harvest's resow)."""
    return [use_on_top(seeds, c) for c in cells]


def plot_commands(centre, hoe, region=None):
    """Pure: the 3×3 plot as one chain — dig the centre, pour the water in, then till and sow each of the 8
    neighbours (ring order). With `region` (the world as it is), only what is still to do: the water already in,
    a cell already farmland, a cell already sown are skipped — an interrupted plot resumes by what is left, never
    by where the last chain stopped."""
    name = (lambda c: region.name(c)) if region is not None else (lambda c: None)
    below = add(centre, (0, -1, 0))
    out = []
    if name(centre) not in ("water", "air"):
        out.append({"type": "mine", "x": centre[0], "y": centre[1], "z": centre[2], "collect": False,
                    "requireDrops": False})
    if name(centre) != "water":
        out.append(use_on_top("minecraft:water_bucket", below))
    for dx, dz in RING:
        cell = (centre[0] + dx, centre[1], centre[2] + dz)
        if name(cell) != "farmland":
            out.append(use_on_top(hoe, cell))
        if name(add(cell, (0, 1, 0))) != "wheat":
            out.append(use_on_top("minecraft:wheat_seeds", cell))
    return out


def started_plot(region, here, radius=8):
    """Pure: the centre of a plot begun and not finished here — the centre dug (water in, or not yet: air on solid
    ground), its ring soil or farmland, some cell not yet sown — nearest first; None when there is none (a fresh
    plot is chosen by `farm_plot`)."""
    best = None
    centres = {c for c, n in region.blocks.items() if n == "water"} | \
        {(r[0] - dx, r[1], r[2] - dz) for r, n in region.blocks.items() if n in SOIL + ("farmland",)
         for dx, dz in RING if region.name((r[0] - dx, r[1], r[2] - dz)) in ("air", None)
         and region.solid((r[0] - dx, r[1] - 1, r[2] - dz))}
    for c in sorted(centres):
        if math.dist(c, here) > radius:
            continue
        ring = [(c[0] + dx, c[1], c[2] + dz) for dx, dz in RING]
        if all(region.name(r) in SOIL + ("farmland",) for r in ring) and \
                any(region.name(add(r, (0, 1, 0))) != "wheat" for r in ring):
            d = math.dist(c, here)
            if best is None or d < best[0]:
                best = (d, c)
    return None if best is None else best[1]


def unreachable_cells(tasks, results):
    """Pure: the cells of the tasks the jar refused as out of reach — the only ones a partial chain bans; the rest
    stand (done) or are asked again (`plot_commands` recomputes them from the world)."""
    return sorted({(t["x"] - 0.5, t["y"] - 1.0, t["z"] - 0.5) if isinstance(t["x"], float) else (t["x"], t["y"], t["z"])
                   for t, r in zip(tasks, results)
                   if r.get("status") != "succeeded" and "reach" in str(r.get("message", "")).lower()})


def plant_farm_commands(state, args):
    """`commands` for plant_farm: the plot chain at the nearest flat 3×3 soil (`farm_plot`), with the best hoe
    carried; NotAvailable naming what is missing."""
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
    return plot_commands(centre, hoe, region)


def feed_commands(pair, food):
    """Pure: feed both animals of a breeding pair, back to back (the second needs nothing from the first)."""
    return [{"type": "interact", "entity": eid, "item": food} for eid in pair]


def breed_commands(state, args):
    """`commands` for breed: the first kind with its food carried (2) and an adult pair (`breeding_pair`) away from
    a breeding still cooling (`state["cooling"]`: positions); [] when there is none."""
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
    """After felling a trunk: put a sapling on the soil it grew from and start a sapling job (a tree in ~20 min).
    Best effort — no sapling or no soil just skips."""
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
    """Verification: farmland and wheat really exist around the centre (not just "the clicks succeeded")."""
    if centre is None:
        return False
    names = Region(add(centre, (-1, 0, -1)), add(centre, (1, 1, 1))).blocks.values()
    return sum(n == "farmland" for n in names) >= 1 and sum(n == "wheat" for n in names) >= 1


@skill(gives=K.GIVES_FARM, needs={"minecraft:wheat_seeds": 1, "minecraft:water_bucket": 1, "tool:hoe:0": 1}, speed={},
       commands=lambda state, args: plant_farm_commands(state, args), verify=lambda c: c.result == REAPED or (bool(c.result) and _plot_growing(c.result)), budget=300, stall=90, per_unit=120,
       provides={"farm": lambda ctx, s: ()})
def plant_farm(ctx):
    """Wheat for the plan: a crop already grown nearby is reaped first (the world read here, at execution — the
    estimate only knows memory); else make a 3×3 plot here: dig the centre, pour the water bucket in (and take
    nothing back — it stays as the plot's source), till the 8 neighbours with a hoe, sow seeds, start a crop job."""
    ripe = ripe_near(nav.feet_now(), RIPE_LOOK)
    if ripe and _reap(ripe) > 0:
        return REAPED
    here = nav.feet_now()
    state = body_state(ctx, Region(add(here, (-9, -3, -9)), add(here, (9, 3, 9))))
    region = state["region"]
    centre = started_plot(region, here) or farm_plot(region, here, ctx.policy.protected)
    tasks = plant_farm_commands(state, ())
    stand = (centre[0] - 2, centre[1] + 1, centre[2])
    if not nav.arrived(stand, ctx.policy, range_=1.0, attempts=1):
        raise api.NavFailed(f"farm spot {centre} not reachable")
    # The plot in one send (an interrupt stops it between segments; the next call recomputes what is left from the
    # world). Judged by the world, never by the chain's "succeeded": the water in, the cells sown.
    done = api.run_chain(tasks, stop_on_failure=False)
    for cell in unreachable_cells(tasks, done):
        ctx.ban(tuple(int(round(v)) for v in cell), 600)
    after = Region(add(centre, (-1, 0, -1)), add(centre, (1, 1, 1)))
    if after.name(centre) != "water":
        raise McError(f"could not pour the plot's water at {centre}")
    sown = sum(1 for dx, dz in RING if after.name((centre[0] + dx, centre[1] + 1, centre[2] + dz)) == "wheat")
    yield sown
    if not sown:
        raise McError("no farm cell could be sown")
    ctx.mem.add_site("farm", centre, ctx.dimension, name=f"farm-{centre[0]}_{centre[2]}")
    jobs.start(ctx.mem, "crop", centre, ctx.dimension, item="minecraft:wheat", count=sown)
    log(f"planted a wheat plot of {sown} at {centre}")
    return centre


HOES = tuple(f"minecraft:{m}_hoe" for m in ("netherite", "diamond", "iron", "golden", "stone", "wooden"))
RIPE_LOOK = 16          # how far a farm step looks for a crop already grown before it sows
REAPED = "reaped"       # plant_farm's answer when it took a grown crop instead of sowing


def _reap(cells):
    """Break these ripe wheat cells and collect wheat and seeds; the wheat gained."""
    before = Inventory().count("minecraft:wheat")
    # one mine_many task: already a batch (every ripe cell, one pickup)
    api.run({"type": "mine_many", "collect": True, "requireDrops": False,
             "only": ["minecraft:wheat", "minecraft:wheat_seeds"],
             "blocks": [{"x": p[0], "y": p[1], "z": p[2]} for p in cells]}, wait=120, awaits="the crops broken before the farmland is read to replant")
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
        raise api.NavFailed(f"farm at {centre} not reachable")
    got = _reap(ripe)
    seeds = Inventory().count("minecraft:wheat_seeds")
    if seeds:
        # the resow in one send; a cell the jar cannot sow stays bare (the next harvest counts only what grew)
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


@skill(gives=["state:bred"], remaining=_k.babies, needs={}, speed={}, start=lambda c: _babies(), verify=lambda c: _babies() > c.base, budget=180, stall=60, per_unit=60,
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

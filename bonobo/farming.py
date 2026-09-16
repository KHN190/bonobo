"""Renewable food and wood: replant saplings after chopping, a 3×3 wheat plot around a water source, harvest when
ripe, breed animals with wheat. Everything that grows is a job (jobs.py) collected later by the priority pool.
Pure planners (`farm_plot`, `ripe_cells`, `breeding_pair`) are offline-tested; skills only execute them."""
import math

from . import api, jobs, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .world import Inventory, Region, add, entities, find

SOIL = ("grass_block", "dirt", "coarse_dirt", "rooted_dirt")
SAPLINGS = ("oak_sapling", "spruce_sapling", "birch_sapling", "jungle_sapling", "acacia_sapling",
            "dark_oak_sapling", "cherry_sapling")
BREED_FOOD = {"minecraft:cow": "minecraft:wheat", "minecraft:sheep": "minecraft:wheat",
              "minecraft:pig": "minecraft:carrot", "minecraft:chicken": "minecraft:wheat_seeds"}
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


def ripe_cells(region):
    """Pure: wheat blocks at full growth (age 7)."""
    prop = getattr(region, "prop", None)
    return [p for p, n in region.blocks.items() if n == "wheat" and prop and str(prop(p, "age")) == "7"]


def breeding_pair(animals, kind, max_gap=8):
    """Pure: two adult animals of `kind` close to each other (ids), or None. `animals` are /entities entries."""
    same = [e for e in animals if e["type"] == kind and not e.get("baby")]
    for i, a in enumerate(same):
        for b in same[i + 1:]:
            if math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"])) <= max_gap:
                return a["id"], b["id"]
    return None


# ---------------------------------------------------------------- skills

def _use_on_top(item, cell):
    r = api.run({"type": "use_item", "item": item, "x": cell[0] + 0.5, "y": cell[1] + 1.0, "z": cell[2] + 0.5,
                 "onBlock": True}, wait=30)
    if r["status"] != "succeeded":
        raise McError(f"using {item} on {cell} failed: {r['message']}")


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
    r = api.run({"type": "place", "item": sapling, "x": base[0], "y": base[1], "z": base[2]}, wait=30)
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
        ctx.mem.note_resource("tree", pos, ctx.dimension)
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


@skill(verify=lambda c: bool(c.result) and _plot_growing(c.result), budget=300, stall=90, per_unit=120)
def plant_farm(ctx):
    """Make a 3×3 wheat plot here: dig the centre, pour the water bucket in (and take nothing back — it stays as
    the plot's source), till the 8 neighbours with a hoe, sow seeds, start a crop job."""
    inv = Inventory()
    hoe = next((h for h in ("minecraft:iron_hoe", "minecraft:stone_hoe", "minecraft:wooden_hoe") if inv.count(h)), None)
    if hoe is None:
        raise NotAvailable("no hoe")
    if inv.count("minecraft:wheat_seeds") < 8:
        raise NotAvailable("need 8 wheat seeds")
    if not inv.count("minecraft:water_bucket"):
        raise NotAvailable("need a water bucket for the plot")
    here = nav.feet_now()
    region = Region(add(here, (-9, -3, -9)), add(here, (9, 3, 9)))
    centre = farm_plot(region, here, ctx.policy.protected)
    if centre is None:
        raise NotAvailable("no flat 3×3 soil nearby for a farm")
    stand = (centre[0] - 2, centre[1] + 1, centre[2])
    if not nav.go_to(stand, ctx.policy, range_=1.0, attempts=1):
        raise api.NavFailed(f"farm spot {centre} not reachable")
    api.run({"type": "mine", "x": centre[0], "y": centre[1], "z": centre[2], "collect": False,
             "requireDrops": False}, wait=30)
    below = add(centre, (0, -1, 0))
    r = api.run({"type": "use_item", "item": "minecraft:water_bucket", "x": below[0] + 0.5, "y": below[1] + 1.0,
                 "z": below[2] + 0.5, "onBlock": True}, wait=30)
    if r["status"] != "succeeded":
        raise McError(f"could not pour the plot's water: {r['message']}")
    yield 1
    sown = 0
    for dx, dz in RING:
        cell = (centre[0] + dx, centre[1], centre[2] + dz)
        try:
            _use_on_top(hoe, cell)
            _use_on_top("minecraft:wheat_seeds", cell)
            sown += 1
        except McError as e:
            log(f"   farm cell {cell}: {e}")
        yield sown
    if not sown:
        raise McError("no farm cell could be sown")
    ctx.mem.add_site("farm", centre, ctx.dimension, name=f"farm-{centre[0]}_{centre[2]}")
    jobs.start(ctx.mem, "crop", centre, ctx.dimension, item="minecraft:wheat", count=sown)
    log(f"planted a wheat plot of {sown} at {centre}")
    return centre


def harvest(ctx, job):
    """A crop job is due: break ripe wheat, collect wheat + seeds, resow, schedule the next harvest."""
    centre = tuple(job["pos"])
    region = Region(add(centre, (-1, 1, -1)), add(centre, (1, 1, 1)), props=True)
    ripe = ripe_cells(region)
    if not ripe:
        ctx.mem.postpone_job(job["id"], 5 * 60)
        raise NotAvailable(f"wheat at {centre} not ripe yet")
    if not nav.go_to((centre[0] - 2, centre[1] + 1, centre[2]), ctx.policy, range_=2.0, attempts=1):
        raise api.NavFailed(f"farm at {centre} not reachable")
    before = Inventory().count("minecraft:wheat")
    api.run({"type": "mine_many", "collect": True, "requireDrops": False,
             "only": ["minecraft:wheat", "minecraft:wheat_seeds"],
             "blocks": [{"x": p[0], "y": p[1], "z": p[2]} for p in ripe]}, wait=120)
    got = Inventory().count("minecraft:wheat") - before
    for p in ripe:
        if Inventory().count("minecraft:wheat_seeds"):
            try:
                _use_on_top("minecraft:wheat_seeds", add(p, (0, -1, 0)))
            except McError:
                pass
    ctx.mem.finish_job(job["id"])
    jobs.start(ctx.mem, "crop", centre, ctx.dimension, item="minecraft:wheat", count=len(ripe))
    log(f"harvested {got} wheat at {centre}")
    if got <= 0:
        raise NotAvailable("harvest yielded no wheat")
    return got


@skill(budget=180, stall=60, per_unit=60)
def breed(ctx):
    """Feed two adults of one kind the food they breed on; a breed job marks the 5-minute cooldown there."""
    inv = Inventory()
    animals = entities(24)
    for kind, food in BREED_FOOD.items():
        if inv.count(food) < 2:
            continue
        pair = breeding_pair(animals, kind)
        if pair is None:
            continue
        where = next(e for e in animals if e["id"] == pair[0])
        pos = (round(where["x"]), round(where["y"]), round(where["z"]))
        if any(math.dist(j["pos"], pos) <= 12 for j in ctx.mem.jobs(ctx.dimension) if j["kind"] == "breed"):
            continue   # cooling down
        for entity_id in pair:
            r = api.run({"type": "interact", "entity": entity_id, "item": food}, wait=45)
            if r["status"] != "succeeded":
                raise McError(f"feeding {kind.split(':')[1]} failed: {r['message']}")
            yield entity_id
        jobs.start(ctx.mem, "breed", pos, ctx.dimension, item=kind)
        ctx.mem.note_resource("herd", pos, ctx.dimension)
        log(f"bred two {kind.split(':')[1]} at {pos}")
        return kind
    raise NotAvailable("no pair of animals with the food to breed them")

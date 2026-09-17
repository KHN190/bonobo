"""The route past the portal: travel between dimensions, find a Nether fortress, locate the stronghold with eyes of
ender. Blaze rods and ender pearls come from the ordinary hunt skill (knowledge.HUNT) once the agent is in the
right place; this module gets it there. Pure helpers (`portal_cell`, `triangulate`) are offline-tested."""
import math
import time

from . import api, blueprints, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .world import Inventory, entities, find

OVERWORLD = "minecraft:overworld"
NETHER = "minecraft:the_nether"


def portal_cell(origin, turns):
    """Pure: an interior cell of a NETHER_PORTAL frame to walk into (bottom inner cell)."""
    d = blueprints.rotate_offset((1, 1, 0), turns)
    return origin[0] + d[0], origin[1] + d[1], origin[2] + d[2]


def waypoints(here, target, leg=40):
    """Pure: points every `leg` blocks (horizontal) from here to target, height interpolated, ending at target."""
    dx, dz = target[0] - here[0], target[2] - here[2]
    dist = math.hypot(dx, dz)
    n = max(1, math.ceil(dist / leg))
    return [(round(here[0] + dx * k / n), round(here[1] + (target[1] - here[1]) * k / n), round(here[2] + dz * k / n))
            for k in range(1, n)] + [tuple(target)]


def triangulate(p1, d1, p2, d2):
    """Pure: intersection (x, z) of two eye-of-ender throws — rays from p1 along d1 and p2 along d2 (x, z vectors).
    None when the rays are (nearly) parallel or meet behind a thrower."""
    (x1, z1), (a1, b1) = p1, d1
    (x2, z2), (a2, b2) = p2, d2
    det = a1 * b2 - b1 * a2
    if abs(det) < 1e-3:
        return None
    t = ((x2 - x1) * b2 - (z2 - z1) * a2) / det
    s = ((x2 - x1) * b1 - (z2 - z1) * a1) / det
    if t < 0 or s < 0:
        return None
    return round(x1 + a1 * t), round(z1 + b1 * t)


@skill(done=lambda c: api.get("/state")["dimension"] == c.args[1], budget=180, stall=90, per_unit=60)
def use_portal(ctx, to_dimension):
    """Walk into the nearest known lit portal and stand in it until the dimension changes. In the Overworld the
    portal is the remembered machine; in the Nether it's the arrival portal (remembered on arrival) or any in sight."""
    s = api.get("/state")
    here = (s["blockX"], s["blockY"], s["blockZ"])
    # Real portal blocks first (an arrival site is where we stood, not necessarily inside the portal).
    cells = [(h["x"], h["y"], h["z"]) for h in find(["nether_portal"], radius=48, limit=8)]
    if not cells:
        for m in ctx.mem.machines(s["dimension"], "portal"):
            cells.append(portal_cell(tuple(m["origin"]), m["turns"]))
        cells += [tuple(p["pos"]) for p in ctx.mem.sites(s["dimension"], kinds=["portal"])]
    if not cells:
        raise NotAvailable(f"no known portal in {s['dimension']}")
    cell = min(cells, key=lambda c: (math.dist(c, here), c[1]))
    log(f"   heading into the portal at {cell} → {to_dimension}")
    # Long trips in legs: one travel plan over 110 blocks and a 55-block climb ran out of search nodes four times.
    for hop in waypoints(here, cell)[:-1]:
        if not nav.go_to(hop, ctx.policy, range_=6, attempts=1):
            raise api.NavFailed(f"stuck on the way to the portal near {hop}")
        yield hop
    if not nav.go_to(cell, ctx.policy, range_=0.5, attempts=1):
        raise api.NavFailed(f"portal at {cell} not reachable")
    # travel counts "within 1.5 blocks" as arrived — it once stopped one block beside the portal and waited there.
    # Step onto the exact cell.
    api.run({"type": "goto", "x": cell[0], "y": cell[1], "z": cell[2], "range": 0.25, "partial": False,
             "sprint": False}, wait=15)
    from .world import Region
    if Region(cell, cell).name(cell) != "nether_portal" and Inventory().count("minecraft:flint_and_steel"):
        # A ghast fireball (or anything) put the portal out: relight it on the frame block under the opening.
        below = (cell[0], cell[1] - 1, cell[2])
        log(f"   portal at {cell} is out → relighting")
        api.run({"type": "use_item", "item": "minecraft:flint_and_steel", "x": below[0] + 0.5, "y": below[1] + 1.0,
                 "z": below[2] + 0.5, "onBlock": True}, wait=20)
    deadline = time.time() + 15
    while time.time() < deadline:
        api.run({"type": "wait", "ticks": 20}, wait=5)
        yield time.time()
        st = api.get("/state")
        if st["dimension"] == to_dimension:
            arrived = (st["blockX"], st["blockY"], st["blockZ"])
            ctx.mem.add_site("portal", arrived, to_dimension, name=f"portal-{to_dimension.split(':')[1]}")
            log(f"arrived in {to_dimension} at {arrived}")
            # Step out of the portal: vanilla only teleports again after leaving it, and standing inside kept the
            # agent "buried" in portal blocks and blocked the next round's actions.
            for dx, dz in ((2, 0), (-2, 0), (0, 2), (0, -2)):
                api.run({"type": "goto", "x": arrived[0] + dx, "y": arrived[1], "z": arrived[2] + dz, "range": 1.0,
                         "partial": True, "sprint": False}, wait=10)
                if not in_portal(api.get("/state")):
                    break
            return arrived
    raise McError("stood in the portal but the dimension didn't change")


@skill(budget=900, stall=180, per_unit=600)
def find_fortress(ctx, legs=8, leg=48):
    """Nether: look for nether bricks, exploring outward along straight legs (travel avoids lava). Remembers the
    fortress as a site so blaze hunting starts there."""
    if api.get("/state")["dimension"] != NETHER:
        raise NotAvailable("not in the Nether")
    for kind in ("fortress",):
        known = ctx.mem.sites(NETHER, kinds=[kind])
        if known:
            return tuple(known[0]["pos"])
    x, y, z = nav.feet_now()
    for i in range(legs):
        hits = find(["nether_bricks", "nether_brick_fence"], radius=64, limit=3)
        if hits:
            pos = (hits[0]["x"], hits[0]["y"], hits[0]["z"])
            ctx.mem.add_site("fortress", pos, NETHER, name="fortress")
            log(f"found a nether fortress at {pos}")
            return pos
        if must_leave():
            break
        dx, dz = [(1, 0), (0, 1), (-1, 0), (0, -1)][i % 4]
        length = leg * (i // 2 + 1)
        # Legs at y≈70: above the lava sea (y 31) and below most ceilings; travel bridges and tunnels as needed.
        nav.go_to((x + dx * length, EXPLORE_Y, z + dz * length), ctx.policy, range_=8, attempts=1)
        x, y, z = nav.feet_now()
        yield (x, z)
    # Out of legs (or supplies): go back to the arrival portal instead of wandering further from home.
    home = ctx.mem.sites(NETHER, kinds=["portal"])
    if home:
        nav.go_to(tuple(home[0]["pos"]), ctx.policy, range_=4, attempts=1)
    raise NotAvailable("no fortress found within the explored legs; back at the portal")


EXPLORE_Y = 70


def in_portal(state):
    """True when the feet or head stand in a portal block (mod ≥0.1.28 reports it in /state; older: a block read)."""
    if "inPortal" in state:
        return bool(state["inPortal"])
    from .world import Region
    x, y, z = state["blockX"], state["blockY"], state["blockZ"]
    r = Region((x, y, z), (x, y + 1, z))
    return any(n in ("nether_portal", "end_portal") for n in r.blocks.values())


def must_leave():
    """Stop exploring when food or health run low (survival then retreats through the portal)."""
    from .world import Inventory
    from .route import food_count
    s = api.get("/state")
    food = food_count(Inventory())
    return food < 6 or s.get("health", 20) <= 10


GOLD_ARMOR = ("minecraft:golden_helmet", "minecraft:golden_chestplate", "minecraft:golden_leggings",
              "minecraft:golden_boots")


HEAD_SLOT = 5   # armor head slot in the player inventory screen


def wear_gold_helmet():
    """Swap the carried gold helmet onto the head (the iron one goes back into its slot)."""
    from .world import container
    api.post("/close")
    src = next((s for s in container()["slots"] if s["owner"] == "player" and s["id"] == "minecraft:golden_helmet"
                and s["slot"] != HEAD_SLOT), None)
    if src is None:
        return False
    api.post("/click", {"slot": src["slot"], "button": 0, "action": "PICKUP"})
    api.post("/click", {"slot": HEAD_SLOT, "button": 0, "action": "PICKUP"})   # helmet on, old one on the cursor
    api.post("/click", {"slot": src["slot"], "button": 0, "action": "PICKUP"})  # old helmet into the freed slot
    log("wearing the gold helmet for piglins")
    return True


def barter_ready(inv, worn):
    """Pure: why bartering can't start, or None. Piglins attack a player without a piece of gold armor worn."""
    if not any(w in GOLD_ARMOR for w in worn):
        return "wear a piece of gold armor first"
    if inv.count("minecraft:gold_ingot") < 1:
        return "no gold ingots to barter"
    return None


@skill(start=lambda c: Inventory().count("minecraft:ender_pearl"), budget=600, stall=180, per_unit=15)
def barter_piglin(ctx, ingots=8):
    """Nether: toss gold ingots next to a (non-zombified) piglin, wait for it to inspect and toss its trade, collect.
    Pearls, obsidian, string and fire resistance potions all come this way."""
    if api.get("/state")["dimension"] != NETHER:
        raise NotAvailable("piglins live in the Nether")
    inv = Inventory()
    if inv.count("minecraft:golden_helmet") and (inv.equipment.get("head") or {}).get("id") != "minecraft:golden_helmet":
        wear_gold_helmet()
        inv = Inventory()
    worn = [(inv.equipment.get(k) or {}).get("id") for k in ("head", "chest", "legs", "feet")]
    why = barter_ready(inv, worn)
    if why:
        raise NotAvailable(why)
    thrown = 0
    while thrown < ingots:
        piglins = sorted((e for e in entities(24) if e["type"] == "minecraft:piglin"
                          and not ctx.blocked((e["id"], 0, 0))), key=lambda e: e["distance"])
        if not piglins:
            raise NotAvailable("no piglin nearby")
        if piglins[0]["distance"] > 3:
            p = piglins[0]
            if not nav.go_to((round(p["x"]), round(p["y"]), round(p["z"])), ctx.policy, range_=2.5, attempts=1):
                ctx.ban((p["id"], 0, 0), 300)
                raise api.NavFailed("could not get next to a piglin")
        # One ingot toward every piglin within reach, then one shared wait: each piglin inspects its own ingot in
        # parallel (one at a time took 78 s for 8 ingots on the bench).
        for p in [e for e in piglins if e["distance"] <= 6][:max(1, ingots - thrown)]:
            slot = next((s["slot"] for s in Inventory().slots if s["id"] == "minecraft:gold_ingot"), None)
            if slot is None:
                break
            api.run({"type": "look", "x": p["x"], "y": p["y"] + 0.5, "z": p["z"]}, wait=5)
            api.post("/click", {"slot": 36 + slot if slot < 9 else slot, "button": 0, "action": "THROW"})
            thrown += 1
        if not Inventory().count("minecraft:gold_ingot") and thrown < ingots:
            thrown = ingots
        api.run({"type": "wait", "ticks": 140}, wait=15)      # a piglin inspects gold for ~6 s
        nav.sweep(ctx, radius=8, wait=30)
        yield Inventory().count("minecraft:ender_pearl")
    log(f"bartered with piglins: {Inventory().count('minecraft:ender_pearl')} pearls now")
    return True


def _eye_direction(timeout=3.0):
    """Follow the thrown eye entity for a moment: its horizontal displacement is the stronghold direction."""
    first, last, t0 = None, None, time.time()
    while time.time() - t0 < timeout:
        eyes = [e for e in entities(12) if e["type"] == "minecraft:eye_of_ender"]
        if eyes:
            p = (eyes[0]["x"], eyes[0]["z"])
            first = first or p
            last = p
        time.sleep(0.2)
    if not first or not last or math.dist(first, last) < 0.5:
        return None
    n = math.dist(first, last)
    return (last[0] - first[0]) / n, (last[1] - first[1]) / n


@skill(budget=900, stall=240, per_unit=600)
def locate_stronghold(ctx):
    """Throw an eye here, walk ~200 blocks sideways, throw again, triangulate; the result is a 'stronghold' site."""
    known = ctx.mem.sites(OVERWORLD, kinds=["stronghold"])
    if known:
        # The nearest estimate, not the first one ever remembered: an old stronghold still in memory sent the
        # portal-room search thousands of blocks away from the one under our feet.
        here = nav.feet_now()
        return tuple(min(known, key=lambda s: math.dist(s["pos"], here))["pos"])
    if api.get("/state")["dimension"] != OVERWORLD:
        raise NotAvailable("strongholds are located from the Overworld")
    throws = []
    for leg in range(2):
        if Inventory().count("minecraft:ender_eye") < 1:
            raise NotAvailable("no eyes of ender left to throw")
        here = nav.feet_now()
        api.run({"type": "use_item", "item": "minecraft:ender_eye", "yaw": 0, "pitch": -20}, wait=10)
        direction = _eye_direction()
        if direction is None:
            raise McError("couldn't follow the eye of ender")
        throws.append(((here[0], here[2]), direction))
        log(f"   eye of ender from {here} flew toward {direction}")
        yield leg
        if leg == 0:
            side = (-direction[1], direction[0])       # perpendicular leg for a good triangulation angle
            target = (round(here[0] + side[0] * 200), here[1], round(here[2] + side[1] * 200))
            nav.go_to(target, ctx.policy, range_=12, attempts=1)
    spot = triangulate(throws[0][0], throws[0][1], throws[1][0], throws[1][1])
    if spot is None:
        raise McError("the two throws don't intersect (too parallel)")
    ctx.mem.add_site("stronghold", (spot[0], 30, spot[1]), OVERWORLD, name="stronghold")
    log(f"stronghold estimated near {spot}")
    return spot

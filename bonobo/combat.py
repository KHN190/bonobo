"""Ranged and defensive combat: shoot a bow with drop compensation, raise the shield, fight blazes from cover, and the dragon fight (crystals first, then the head). Pure helpers (`bow_aim`, `crystal_order`, `blaze_cover`) are offline-tested; skills execute with the mod's use_item (hold to draw) and attack tasks."""

import math
import time

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import api, nav
from . import combat_model
from .api import McError, NotAvailable, log
from .skill import skill
from .world import Inventory, add, entities

ARROW_SPEED = 3.0          # blocks per tick at full draw
GRAVITY = 0.05             # blocks per tick² on arrows

def bow_aim(eye, target, height=1.0):
    """Pure: the point to aim at so a fully drawn arrow from `eye` hits `target` (x, y, z of the entity's feet): aim at its body, raised by the arrow's drop over the flight time."""

    tx, ty, tz = target[0], target[1] + height, target[2]
    d = math.dist((eye[0], eye[2]), (tx, tz))
    ticks = d / ARROW_SPEED
    drop = 0.5 * GRAVITY * ticks * ticks
    return tx, ty + drop, tz

def crystal_order(crystals, here):
    """Pure: end crystals nearest first (entity dicts with x, y, z)."""

    return sorted(crystals, key=lambda e: (e["y"] - here[1] > 30, math.dist((e["x"], e["y"], e["z"]), here)))

def blaze_cover(region, here, blaze, radius=4):
    """Pure: a standable cell within `radius` where a solid block sits between the cell and the blaze at head height (the blaze's fireballs need a line of sight) but the blaze is still within 5 blocks to hit when it comes around."""

    best = None
    bx, by, bz = blaze
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            c = (here[0] + dx, here[1], here[2] + dz)
            below, head = add(c, (0, -1, 0)), add(c, (0, 1, 0))
            if not (region.solid(below) and not region.solid(c) and not region.solid(head)):
                continue
            mid = (round((c[0] + bx) / 2), c[1] + 1, round((c[2] + bz) / 2))
            if not region.solid(mid) or math.dist(c, blaze) > 7:
                continue
            d = math.dist(c, here)
            if best is None or d < best[0]:
                best = (d, c)
    return None if best is None else best[1]

# How far each kind of danger reaches, in blocks. One flat distance treated a breath cloud like a creeper and a
# dragon's head like a fireball; the head sweep and the take-off knockback are what killed the bench runs.

def clearance(spot, hazards):
    """Pure: the smallest margin between `spot` and any hazard's reach (negative = inside it, inf when none)."""
    return min((math.dist(spot, p) - r for p, r in hazards), default=float("inf"))

def safe_stand(region, here, hazards, anchor, band=(8, 14), clear=1.0):
    """Pure: where to wait out an attack — a standable cell whose horizontal distance to `anchor` is inside `band` and which is at least `clear` blocks from every hazard, nearest to `here`."""

    lo, hi = band
    ax, az = anchor[0], anchor[-1]      # (x, z) or (x, y, z)
    best, fallback = None, None
    for (x, y, z), name in region.blocks.items():
        cell = (x, y, z)
        below, head = add(cell, (0, -1, 0)), add(cell, (0, 1, 0))
        if not (region.solid(below) and not region.solid(cell) and not region.solid(head)):
            continue
        if region.hazard(cell) or region.hazard(below):
            continue
        r = math.hypot(cell[0] + 0.5 - ax, cell[2] + 0.5 - az)
        if not lo <= r <= hi:
            continue
        c = clearance(cell, hazards)
        d = math.dist(cell, here)
        if c >= clear and (best is None or d < best[1]):
            best = (cell, d)
        if fallback is None or c > fallback[1]:
            fallback = (cell, c)
    if best:
        return best[0]
    return fallback[0] if fallback else None

ENDERMAN = "minecraft:enderman"
EYE_HEIGHT = 1.62

def angry_endermen(near, here, radius=16.0):
    """Pure: endermen that are actually after us (mod ≥0.1.33 reports `angry`), nearest first."""

    out = [e for e in near if e["type"] == ENDERMAN and e.get("angry")
           and math.dist((e["x"], e["y"], e["z"]), here) <= radius]
    return sorted(out, key=lambda e: math.dist((e["x"], e["y"], e["z"]), here))

ENDERMAN_HEAD = 2.55      # eye/head height of a 2.9-block enderman
HEAD_BAND = 1.0           # how close to that height the aim may pass before it counts as "looking at it"

def endermen_near(near, here, radius=6.0):
    """Pure: endermen within `radius`, nearest first."""

    out = [e for e in near if e["type"] == ENDERMAN
           and math.dist((e["x"], e["y"], e["z"]), here) <= radius]
    return sorted(out, key=lambda e: math.dist((e["x"], e["y"], e["z"]), here))

@skill(gives=["state:fight_over"], remaining=_k.none_of("minecraft:ender_dragon", within=512.0), needs={}, speed={}, budget=180, stall=90, soft=True)
def station(ctx, anchor, band=(8, 14), clear=1.0, rounds=200, until=None):
    """Generic: wait out a fight at a safe distance from `anchor` — out of the breath/head/fireball, inside the band so the target stays reachable, eating when hurt."""

    from .world import Region
    from . import skills
    for _ in range(rounds):
        if until is not None and until():
            return True
        s = api.get("/state")
        here = (s["blockX"], s["blockY"], s["blockZ"])
        near = entities(128)
        angry = angry_endermen(near, here, 12.0)
        if angry:
            # Never trade hits with an enderman: shake it off (water, cover, distance). Provoking more of them by
            # swinging around is how a dragon fight turns into an enderman fight.
            from .end import shake_enderman
            shake_enderman(ctx)
            yield (len(angry), round(s["health"]))
            continue
        hz = combat_model.hazard_points(near)
        pad = int(band[1]) + 2
        ax, az = anchor[0], anchor[-1]      # (x, z) or (x, y, z): callers pass the perch as a flat pair
        region = Region((round(ax) - pad, here[1] - 4, round(az) - pad),
                        (round(ax) + pad, here[1] + 4, round(az) + pad))
        spot = safe_stand(region, here, hz, anchor, band, clear)
        safe = clearance(here, hz) >= clear and not endermen_near(near, here, 8.0)
        if not safe and spot is not None and math.dist(here, spot) > 1.0:
            # Get out first. Eating while still in the breath was a death loop: every bite was cancelled by the next
            # task, the log filled with "no bite" and health went 20 → 0 without a step taken.
            nav.arrived(spot, ctx.policy, range_=1.0, attempts=1)
        elif safe and s["health"] <= 14 and s.get("food", 20) < 20:
            try:
                skills.eat(raw_ok=True)       # a full food bar can't be eaten: it only spammed "no bite"
            except McError as e:
                log(f"   station: no bite ({e})")
        elif spot is not None and math.dist(here, spot) > 1.5:
            nav.arrived(spot, ctx.policy, range_=1.0, attempts=1)
        else:
            api.run({"type": "wait", "ticks": 5}, wait=5, awaits="the fight's next reading (reflex latency: a poll, never a batch)")
        margin = clearance(here, hz)
        # No hazards at all means an infinite margin, and round(inf) raises OverflowError — it killed the skill
        # mid-fight once ("died ... cannot convert float infinity to integer").
        yield (99 if margin == float("inf") else round(margin), round(s["health"]))
    return True

def shoot_batch(entity, eye, hold_ticks=22):
    """Pure: the one task that draws fully and looses at `entity` from `eye` (arrow drop allowed for)."""
    aim = bow_aim(eye, (entity["x"], entity["y"], entity["z"]), height=entity.get("height", 1.0) * 0.6)
    return [{"type": "use_item", "item": "minecraft:bow", "x": aim[0], "y": aim[1], "z": aim[2],
             "holdTicks": hold_ticks}]

def shoot(entity, hold_ticks=22, near=None):
    """Draw fully and release at an entity (entity dict from /entities)."""

    s = api.get("/state")
    if near is not None and combat_model.aim_hits_enderman((entity["x"], entity["y"], entity["z"]),
                                              (s["x"], s["y"], s["z"]), near):
        raise NotAvailable("an enderman stands in the line of aim")
    r = api.run(shoot_batch(entity, (s["x"], s["y"] + 1.62, s["z"]), hold_ticks)[0], wait=10, awaits="a moving target: one shot, then aim again from the new reading")
    if r["status"] != "succeeded":
        raise McError(f"shooting failed: {r['message']}")

BLAZE_QUIET_S = 30      # no blaze and no rod in sight for this long: this is not a spawner

def _rods_on_floor():
    return [e for e in entities(16, ["minecraft:item"]) if (e.get("item") or {}).get("id") == "minecraft:blaze_rod"]

@skill(gives=["state:rods_held"], remaining=_k.more_than_at_start(lambda c: "minecraft:blaze_rod", lambda c: c.args[1]), needs={"tool:sword:1": 1}, speed={}, start=lambda c: Inventory().count("minecraft:blaze_rod"),
       done=lambda c: Inventory().count("minecraft:blaze_rod") >= c.base + c.args[1],
       budget=900, stall=180, units=lambda c: c.args[1], key=lambda c: "collect_blaze_rods",
       provides={"hunt:minecraft:blaze_rod": lambda ctx, s: (s.count,)})
def collect_blaze_rods(ctx, rods):
    """The rod-collecting step of "have blaze_rod" (L2 puts the fortress first: decompose)."""

    quiet_since = None
    for _ in range(rods * 40):
        blazes = [e for e in entities(24, ["minecraft:blaze"]) if not ctx.blocked((e["id"], 0, 0))]
        floor = _rods_on_floor()
        if not blazes and not floor:
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since >= BLAZE_QUIET_S:
                raise NotAvailable("no blazes here: not a spawner")
        else:
            quiet_since = None
        if floor:
            try:
                api.run({"type": "collect", "radius": 6, "only": ["minecraft:blaze_rod"]}, wait=20, awaits="rods drop only when a blaze dies; collected per death")
            except api.Unreachable:
                e = floor[0]
                nav.arrived((round(e["x"]), round(e["y"]), round(e["z"])), ctx.policy, range_=1.0, attempts=1)
                api.run({"type": "collect", "radius": 3, "only": ["minecraft:blaze_rod"]}, wait=10, awaits="rods drop only when a blaze dies: collected per death")
        else:
            api.run({"type": "wait", "ticks": 20}, wait=5, awaits="the next blaze death (rods on the floor)")
        yield Inventory().count("minecraft:blaze_rod"), len(blazes)
    raise McError("no rods collected")


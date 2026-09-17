"""Ranged and defensive combat: shoot a bow with drop compensation, raise the shield, fight blazes from cover, and
the dragon fight (crystals first, then the head). Pure helpers (`bow_aim`, `crystal_order`, `blaze_cover`) are
offline-tested; skills execute with the mod's use_item (hold to draw) and attack tasks."""
import math
import time

from . import api, nav
from . import combat_model
from .api import McError, NotAvailable, log
from .skill import skill
from .world import Inventory, add, entities

ARROW_SPEED = 3.0          # blocks per tick at full draw
GRAVITY = 0.05             # blocks per tick² on arrows


def bow_aim(eye, target, height=1.0):
    """Pure: the point to aim at so a fully drawn arrow from `eye` hits `target` (x, y, z of the entity's feet):
    aim at its body, raised by the arrow's drop over the flight time. Beyond ~60 blocks the drop is too uncertain."""
    tx, ty, tz = target[0], target[1] + height, target[2]
    d = math.dist((eye[0], eye[2]), (tx, tz))
    ticks = d / ARROW_SPEED
    drop = 0.5 * GRAVITY * ticks * ticks
    return tx, ty + drop, tz


def crystal_order(crystals, here):
    """Pure: end crystals nearest first (entity dicts with x, y, z). Caged ones (y high on tall pillars) come last:
    they need pillaring up, the open ones can be shot from the ground."""
    return sorted(crystals, key=lambda e: (e["y"] - here[1] > 30, math.dist((e["x"], e["y"], e["z"]), here)))


def blaze_cover(region, here, blaze, radius=4):
    """Pure: a standable cell within `radius` where a solid block sits between the cell and the blaze at head height
    (the blaze's fireballs need a line of sight) but the blaze is still within 5 blocks to hit when it comes around."""
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
    """Pure: where to wait out an attack — a standable cell whose horizontal distance to `anchor` is inside `band` and
    which is at least `clear` blocks from every hazard, nearest to `here`. With nothing that clear, the cell in the
    band with the biggest clearance wins (still better than standing in the breath). Generic: the anchor is the
    dragon's perch, a blaze spawner or a fortress doorway."""
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
    """Pure: endermen that are actually after us (mod ≥0.1.33 reports `angry`), nearest first. Neutral ones are not a
    threat at all — attacking them on sight was the worst possible answer."""
    out = [e for e in near if e["type"] == ENDERMAN and e.get("angry")
           and math.dist((e["x"], e["y"], e["z"]), here) <= radius]
    return sorted(out, key=lambda e: math.dist((e["x"], e["y"], e["z"]), here))


ENDERMAN_HEAD = 2.55      # eye/head height of a 2.9-block enderman
HEAD_BAND = 1.0           # how close to that height the aim may pass before it counts as "looking at it"


def looking_at_enderman(state, near):
    """Pure: the crosshair is on an enderman right now (mod's /state lookingAt)."""
    look = state.get("lookingAt") or {}
    if look.get("kind") != "entity":
        return False
    return any(e["id"] == look.get("entity") and e["type"] == ENDERMAN for e in near)


def endermen_near(near, here, radius=6.0):
    """Pure: endermen within `radius`, nearest first. In the End they are everywhere and one angry enderman does 7 hp
    a hit — the dragon benches ignored them completely."""
    out = [e for e in near if e["type"] == ENDERMAN
           and math.dist((e["x"], e["y"], e["z"]), here) <= radius]
    return sorted(out, key=lambda e: math.dist((e["x"], e["y"], e["z"]), here))


def engage(player_hp, window_open, min_hp=19.0, retreat_hp=12.0):
    """Pure: "attack" while the window is open and health allows, "retreat" when hurt, else "hold". Keeps every fight
    skill on the same rule instead of each one inventing a health check."""
    if player_hp <= retreat_hp:
        return "retreat"
    if window_open and player_hp >= min_hp:
        return "attack"
    return "hold"


@skill(budget=180, stall=90, soft=True)
def station(ctx, anchor, band=(8, 14), clear=1.0, rounds=200, until=None):
    """Generic: wait out a fight at a safe distance from `anchor` — out of the breath/head/fireball, inside the band
    so the target stays reachable, eating when hurt. Ends when `until()` says the window is open (the caller then
    attacks) or the rounds run out. The dragon bench died standing 9 blocks in front of the head, unmoving."""
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
            nav.go_to(spot, ctx.policy, range_=1.0, attempts=1)
        elif safe and s["health"] <= 14 and s.get("food", 20) < 20:
            try:
                skills.eat(raw_ok=True)       # a full food bar can't be eaten: it only spammed "no bite"
            except McError as e:
                log(f"   station: no bite ({e})")
        elif spot is not None and math.dist(here, spot) > 1.5:
            nav.go_to(spot, ctx.policy, range_=1.0, attempts=1)
        else:
            api.run({"type": "wait", "ticks": 5}, wait=5)
        margin = clearance(here, hz)
        # No hazards at all means an infinite margin, and round(inf) raises OverflowError — it killed the skill
        # mid-fight once ("died ... cannot convert float infinity to integer").
        yield (99 if margin == float("inf") else round(margin), round(s["health"]))
    return True


def shoot(entity, hold_ticks=22, near=None):
    """Draw fully and release at an entity (entity dict from /entities). With `near` given, an aim that would sweep
    the crosshair across an enderman is refused — looking at one provokes it, and a crystal is not worth an enderman
    fight; the caller waits or picks another target."""
    s = api.get("/state")
    if near is not None and combat_model.aim_hits_enderman((entity["x"], entity["y"], entity["z"]),
                                              (s["x"], s["y"], s["z"]), near):
        raise NotAvailable("an enderman stands in the line of aim")
    eye = (s["x"], s["y"] + 1.62, s["z"])
    aim = bow_aim(eye, (entity["x"], entity["y"], entity["z"]), height=entity.get("height", 1.0) * 0.6)
    r = api.run({"type": "use_item", "item": "minecraft:bow", "x": aim[0], "y": aim[1], "z": aim[2],
                 "holdTicks": hold_ticks}, wait=10)
    if r["status"] != "succeeded":
        raise McError(f"shooting failed: {r['message']}")


def guard(ticks=30):
    """Raise the shield for a moment: hold 'use' with the weapon in hand (swords have no use action in 1.21, so the
    offhand shield blocks)."""
    inv = Inventory()
    if inv.offhand() != "minecraft:shield":
        return False
    weapon = next((w for w in ("minecraft:diamond_sword", "minecraft:iron_sword", "minecraft:stone_sword")
                   if inv.count(w)), None)
    if weapon is None:
        return False
    api.run({"type": "use_item", "item": weapon, "yaw": api.get("/state")["yaw"], "pitch": 0,
             "holdTicks": ticks}, wait=5)
    return True


def _strike(entity, seconds=8):
    """A short melee burst: the attack task is stopped after `seconds` (a blaze rising out of reach isn't a failure,
    the fight loop looks again)."""
    try:
        api.run({"type": "attack", "entity": entity["id"]}, wait=seconds)
    except api.TaskStuck:
        pass


@skill(start=lambda c: Inventory().count("minecraft:blaze_rod"),
       done=lambda c: Inventory().count("minecraft:blaze_rod") >= c.base + c.args[1],
       budget=900, stall=180, per_unit=90, units=lambda c: c.args[1], key=lambda c: "fight_blaze")
def fight_blaze(ctx, rods):
    """Blazes near a fortress: shoot them when a bow and arrows are carried, otherwise wait behind cover (shield up)
    until one comes within reach and hit it; collect the rods."""
    waited = 0
    none_since = None
    for _ in range(rods * 12):     # most rounds are short shield-up waits for a blaze to come down
        blazes = [e for e in entities(24) if e["type"] == "minecraft:blaze" and not ctx.blocked((e["id"], 0, 0))]
        if not blazes:
            # A moment without one in the list isn't "none here" (sync, one behind a pillar): 3 s before giving up.
            none_since = none_since or time.time()
            if time.time() - none_since >= 3:
                raise NotAvailable("no blazes nearby")
            api.run({"type": "wait", "ticks": 10}, wait=5)
            yield Inventory().count("minecraft:blaze_rod")
            continue
        none_since = None
        s = api.get("/state")
        here = (s["blockX"], s["blockY"], s["blockZ"])
        b = min(blazes, key=lambda e: e["distance"])
        if Inventory().count("minecraft:bow") and Inventory().count("minecraft:arrow") and b["distance"] > 5:
            shoot(b)
        elif b["distance"] <= 4 and b["y"] - here[1] <= 3:
            waited = 0
            _strike(b)
        elif (b["distance"] <= 12 and b["y"] - here[1] <= 3) or waited >= 3:
            # No bow (the speedrun kit): close in and strike — a sword reaches 3 up with a jump. A 1.5-block height
            # limit left 10 of 12 rounds shield-up while blazes hovered 2–4 up in a 5-high hall (bench 05:49); after
            # 3 waits, go for the nearest one anyway. Chasing one far up in the open ate fireballs (bench 04:19).
            waited = 0
            nav.go_to((round(b["x"]), here[1], round(b["z"])), ctx.policy, range_=2.5, attempts=1)
            _strike(b)
        else:
            waited += 1
            guard(30)   # shield up until it drops to our height (blazes descend to shoot)
        try:
            api.run({"type": "collect", "radius": 6, "only": ["minecraft:blaze_rod"]}, wait=20)
            unreachable = False
        except api.Unreachable:
            unreachable = True
        if unreachable:
            # A rod fell where the pickup walk can't go (bench 06:20): walk to it with the navigator, then collect.
            rods_on_floor = [e for e in entities(16, ["minecraft:item"])
                             if (e.get("item") or {}).get("id") == "minecraft:blaze_rod"]
            if rods_on_floor:
                e = rods_on_floor[0]
                nav.go_to((round(e["x"]), round(e["y"]), round(e["z"])), ctx.policy, range_=1.0, attempts=1)
                api.run({"type": "collect", "radius": 3, "only": ["minecraft:blaze_rod"]}, wait=10)
        yield Inventory().count("minecraft:blaze_rod")
    raise McError("blaze fight made no progress")


@skill(budget=1800, stall=300, per_unit=900)
def fight_dragon(ctx):
    """The End: shoot the end crystals (nearest, open ones first), then hit the dragon whenever it perches low."""
    if api.get("/state")["dimension"] != "minecraft:the_end":
        raise NotAvailable("not in the End")
    unseen_since = None
    for _ in range(400):
        near = entities(128)
        crystals = [e for e in near if e["type"] == "minecraft:end_crystal"]
        from .end import dragon_entry
        dragon = dragon_entry(near)    # the dragon itself, not a body part of the same type
        if dragon is None:
            # Not seen ≠ dead: a fresh dragon wasn't synced yet and one circling far out is beyond the entity scan
            # (bench 05:29 declared victory in 1 s). Gone only after 5 s unseen, waiting near the portal meanwhile.
            unseen_since = unseen_since or time.time()
            if time.time() - unseen_since >= 5:
                log("the ender dragon is gone")
                ctx.mem.data["dragon_defeated"] = True
                ctx.mem.save()
                return True
            api.run({"type": "wait", "ticks": 10}, wait=5)
            yield None
            continue
        unseen_since = None
        s = api.get("/state")
        here = (s["x"], s["y"], s["z"])
        s = api.get("/state")
        low = dragon["y"] - s["y"] <= 4
        move = engage(s["health"], low and dragon["distance"] <= 12)
        if crystals and Inventory().count("minecraft:arrow"):
            shoot(crystal_order(crystals, here)[0])
        elif move == "attack":
            # Down within reach of a jump: chase it and hit the nearest body part (mod ≥0.1.29 aims at parts).
            _strike(dragon, seconds=6)
        else:
            # Flying, or we are hurt: keep out of the head and the breath instead of circling blindly (two bench
            # fights died at 9 blocks in front of a perched dragon, health 20 → 0, without landing a hit).
            band = (14, 20) if move == "retreat" else (8, 14)
            for _ in station.__wrapped__(ctx, (0, 0), band=band, clear=1.0, rounds=6):
                pass
        yield (len(crystals), round(dragon.get("health", 0)))
    raise McError("dragon fight ran out of rounds")

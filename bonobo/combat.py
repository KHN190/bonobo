"""Ranged and defensive combat: shoot a bow with drop compensation, raise the shield, fight blazes from cover. Pure helpers (`bow_aim`, `blaze_cover`) are offline-tested; skills execute with the mod's use_item (hold to draw) and attack tasks."""

import math
import time

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import api, nav, skillcore
from . import combat_model
from .game import EYE_HEIGHT
from .data import NETHER
from .api import McError, NotAvailable
from .skill import budget_end, skill
from .world import Inventory, cell_add, entities, find

ARROW_SPEED = 3.0          # blocks per tick at full draw
GRAVITY = 0.05             # blocks per tick² on arrows

def bow_aim(eye, target, height=1.0):
    """Pure: where to aim so a fully drawn arrow from `eye` hits `target`: its body, raised by the arrow's drop."""

    tx, ty, tz = target[0], target[1] + height, target[2]
    d = math.dist((eye[0], eye[2]), (tx, tz))
    ticks = d / ARROW_SPEED
    drop = 0.5 * GRAVITY * ticks * ticks
    return tx, ty + drop, tz


# how far each danger reaches: one flat distance mistook the head sweep and take-off knockback


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
    r = api.run(shoot_batch(entity, (s["x"], s["y"] + EYE_HEIGHT, s["z"]), hold_ticks)[0], wait=10, awaits="a moving target: one shot, then aim again from the new reading")
    if r["status"] != "succeeded":
        raise McError(f"shooting failed: {r['message']}")

BLAZE_QUIET_S = 30      # no blaze and no rod in sight for this long: this is not a spawner
SPAWNER_RANGE = 16      # the game's spawner activation range: no spawner this near, no blaze comes back

def blaze_step(blazes, rods, spawners, quiet_s):
    """Pure: "collect" (rods on the floor), "wait" (a blaze alive, or a spawner near not yet quiet for BLAZE_QUIET_S),
    "end" (none alive, none lying, and no spawner near: nothing will come)."""
    if rods:
        return "collect"
    if blazes:
        return "wait"
    return "wait" if spawners and quiet_s < BLAZE_QUIET_S else "end"

def _rods_on_floor():
    return [e for e in entities(16, ["minecraft:item"]) if (e.get("item") or {}).get("id") == "minecraft:blaze_rod"]

@skill(gives=["state:rods_held"], remaining=_k.more_than_at_start(lambda c: "minecraft:blaze_rod", lambda c: c.args[1]), needs={"tool:sword:1": 1}, pre=[skillcore.in_dimension(NETHER)], fights=lambda c: ["minecraft:blaze"], abandon="cover", start=lambda c: Inventory().count("minecraft:blaze_rod"),
       done=lambda c: Inventory().count("minecraft:blaze_rod") >= c.base + c.args[1],
       budget=900, stall=180, units=lambda c: c.args[1], key=lambda c: "collect_blaze_rods",
       provides={"hunt:minecraft:blaze_rod": lambda ctx, s: (s.count,)}, when=lambda s, f: _k.lives_in(["minecraft:blaze"]))
def collect_blaze_rods(ctx, rods):
    """The rod-collecting step of "have blaze_rod" (L2 puts the fortress first: decompose)."""

    quiet_since, seen = None, None
    end = budget_end()
    while time.time() < end:     # waits on blaze deaths: bounded by time
        blazes = [e for e in entities(24, ["minecraft:blaze"]) if not ctx.blocked((e["id"], 0, 0))]
        floor = _rods_on_floor()
        now_seen = (len(blazes), len(floor), round(min((e["distance"] for e in blazes), default=0)))
        if now_seen != seen:     # a readout: what the step waits on (it never attacks: a fight must)
            api.detail(f"collect_blaze_rods: {now_seen[0]} blazes (nearest {now_seen[2]}), {now_seen[1]} rods on the floor")
            seen = now_seen
        if not blazes and not floor:
            quiet_since = quiet_since or time.time()
            spawners = find(["minecraft:spawner"], radius=SPAWNER_RANGE, limit=1)
            if blaze_step(blazes, floor, spawners, time.time() - quiet_since) == "end":
                raise NotAvailable("no blaze within 24, no rod on the floor" + (
                    f", quiet {BLAZE_QUIET_S} s by the spawner" if spawners else
                    f", no spawner within {SPAWNER_RANGE}: none will come here"))
        else:
            quiet_since = None
        if floor:
            try:
                api.run({"type": "collect", "radius": 6, "only": ["minecraft:blaze_rod"]}, wait=20, awaits="rods drop only when a blaze dies; collected per death")
            except api.Unreachable:
                e = floor[0]
                nav.arrived_near((round(e["x"]), round(e["y"]), round(e["z"])), ctx.policy, range_=1.0, attempts=1)
                api.run({"type": "collect", "radius": 3, "only": ["minecraft:blaze_rod"]}, wait=10, awaits="rods drop only when a blaze dies: collected per death")
        else:
            api.run({"type": "wait", "ticks": 20}, wait=5, awaits="the next blaze death (rods on the floor)")
        yield Inventory().count("minecraft:blaze_rod"), len(blazes)
    raise McError("no rods collected")


def shoot_bow(ctx, entity_id):
    """Shell, never planned: draw full, loose at `entity_id`, leading it."""
    raise NotImplementedError("shoot_bow: a shell")

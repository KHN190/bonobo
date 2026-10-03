"""Loot chests the agent didn't place (ruined portals, villages, temples, shipwrecks, dungeons): obsidian, flint and steel, gold, iron, diamonds, food, pearls, arrows. Looted chests are remembered so they aren't opened twice. Pure `loot_plan` is offline-tested."""

import math

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import bag as _bag
from . import api, nav, skillcore
from . import world
from .api import McError, NotAvailable, log
from .beliefs import slot_cost_s
from .skill import skill
from .skillcore import carried_total, opened
from .world import Inventory, find

def loot_plan(slots, prices, bag_free, stack=64):
    """Pure: container slot numbers worth taking, dearest first."""

    take = []
    free = float(bag_free)
    for s in slots:
        if s.get("owner") == "player" or s.get("id") in (None, "minecraft:air"):
            continue
        per = prices.get(s["id"])
        if per is None or per == float("inf"):
            continue
        worth = float(per) * float(s.get("count", 1))
        if worth > slot_cost_s(max(1.0, free)):
            take.append((-worth, s["slot"]))
            free -= 1.0
    return [slot for _worth, slot in sorted(take)]

def unlooted_chests(ctx, radius=32):
    here = world.feet()
    ours = [tuple(s["pos"]) for s in ctx.mem.sites(ctx.dimension)]
    looted = {tuple(p) for p in ctx.mem.data.get("looted", [])}
    out = []
    for c in find(["chest", "barrel", "trapped_chest"], radius=radius, limit=20):
        pos = (c["x"], c["y"], c["z"])
        if pos in looted or any(math.dist(pos, o) <= 3 for o in ours) or ctx.blocked(pos):
            continue
        out.append(pos)
    return sorted(out, key=lambda p: math.dist(p, here))

@skill(gives=["state:looted"], remaining=_k.gained_any, needs={}, start=lambda c: carried_total(), verify=lambda c: carried_total() > c.base,
       budget=240, stall=90, provides={"loot": lambda ctx, s: ()}, fills_bag=True)
def loot_chest(ctx):
    """Open the nearest chest that isn't ours and hasn't been looted, take the valuable stacks, remember it."""
    chests = unlooted_chests(ctx)
    if not chests:
        raise NotAvailable("no unlooted chest within 32 blocks")
    pos = chests[0]
    if not nav.arrived_near(pos, ctx.policy, range_=3, attempts=1):
        ctx.ban(pos, 1800)
        raise api.NavFailed(f"chest at {pos} not reachable", pos=pos)
    r = api.run({"type": "use", "x": pos[0], "y": pos[1], "z": pos[2]}, wait=30, awaits="the chest's slots (loot_plan) are only readable once its screen is open")
    if not opened(r):
        ctx.ban(pos, 1800)
        raise McError(f"could not open the chest at {pos}", pos=pos)
    prices = ctx.prices()
    if not prices:
        # no price table, no decision: say so rather than write a chest off as looted
        raise NotAvailable("no price table: cannot say what is worth taking")
    taken = 0
    try:
        from .world import container
        ctx.mem.saw_container(pos, container()["slots"])
        for slot in loot_plan(container()["slots"], prices, Inventory().free_slots()):
            api.post("/click", _bag.quick_move(slot))
            taken += 1
            yield taken
        ctx.mem.note_container(pos, ctx.dimension, container()["slots"])
    finally:
        api.post("/close")
    ctx.mem.data.setdefault("looted", []).append(list(pos))
    ctx.mem.save()
    log(f"looted {taken} stacks from the chest at {pos}")
    return taken

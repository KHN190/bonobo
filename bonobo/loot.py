"""Loot chests the agent didn't place (ruined portals, villages, temples, shipwrecks, dungeons): obsidian, flint and
steel, gold, iron, diamonds, food, pearls, arrows. Looted chests are remembered so they aren't opened twice.
Pure `loot_plan` is offline-tested."""
import math

from . import api, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .world import Inventory, find

WANTED_SUFFIX = ("_ingot", "diamond", "obsidian", "flint_and_steel", "ender_pearl", "arrow", "golden_carrot",
                 "golden_apple", "bread", "cooked_", "emerald", "saddle", "bucket", "bow", "_nugget", "blaze_rod",
                 "string", "gunpowder", "book", "enchanted_book", "name_tag", "fire_charge", "raw_iron", "raw_gold")


def loot_plan(slots):
    """Pure: container slot numbers worth taking (chest-owned, a wanted item), most valuable kinds first."""
    rank = {s: i for i, s in enumerate(WANTED_SUFFIX)}
    take = []
    for s in slots:
        if s.get("owner") == "player" or s.get("id") in (None, "minecraft:air"):
            continue
        item = s["id"].split(":")[-1]
        hits = [rank[w] for w in WANTED_SUFFIX if w in item]
        if hits:
            take.append((min(hits), s["slot"]))
    return [slot for _, slot in sorted(take)]


def unlooted_chests(ctx, radius=32):
    here = nav.feet_now()
    ours = [tuple(s["pos"]) for s in ctx.mem.sites(ctx.dimension)]
    looted = {tuple(p) for p in ctx.mem.data.get("looted", [])}
    out = []
    for c in find(["chest", "barrel", "trapped_chest"], radius=radius, limit=20):
        pos = (c["x"], c["y"], c["z"])
        if pos in looted or any(math.dist(pos, o) <= 3 for o in ours) or ctx.blocked(pos):
            continue
        out.append(pos)
    return sorted(out, key=lambda p: math.dist(p, here))


_CACHE = {"t": 0, "pos": None, "hits": []}


def unlooted_chests_cached(mem, snap, ttl=60):
    """For goal feasibility (checked every round): the chest scan at most once a minute per 16-block area."""
    import time
    area = tuple(int(v) // 16 for v in snap.feet)
    if time.time() - _CACHE["t"] < ttl and _CACHE["pos"] == area:
        return _CACHE["hits"]

    class _Ctx:
        def __init__(self):
            self.mem, self.dimension, self.blacklist = mem, snap.dimension, {}

        def blocked(self, pos):
            return False

    _CACHE.update(t=time.time(), pos=area, hits=unlooted_chests(_Ctx()))
    return _CACHE["hits"]


@skill(start=lambda c: Inventory().used_slots(), budget=240, stall=90, per_unit=60)
def loot_chest(ctx):
    """Open the nearest chest that isn't ours and hasn't been looted, take the valuable stacks, remember it."""
    chests = unlooted_chests(ctx)
    if not chests:
        raise NotAvailable("no unlooted chest within 32 blocks")
    pos = chests[0]
    if not nav.go_to(pos, ctx.policy, range_=3, attempts=1):
        ctx.ban(pos, 1800)
        raise api.NavFailed(f"chest at {pos} not reachable")
    r = api.run({"type": "use", "x": pos[0], "y": pos[1], "z": pos[2]}, wait=30)
    if r["status"] != "succeeded" or r["result"].get("screen") in (None, "none"):
        ctx.ban(pos, 1800)
        raise McError(f"could not open the chest at {pos}")
    taken = 0
    try:
        from .world import container
        for slot in loot_plan(container()["slots"]):
            api.post("/click", {"slot": slot, "button": 0, "action": "QUICK_MOVE"})
            taken += 1
            yield taken
    finally:
        api.post("/close")
    ctx.mem.data.setdefault("looted", []).append(list(pos))
    ctx.mem.save()
    log(f"looted {taken} stacks from the chest at {pos}")
    return taken

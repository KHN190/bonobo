"""Loot chests the agent didn't place (ruined portals, villages, temples, shipwrecks, dungeons): obsidian, flint and
steel, gold, iron, diamonds, food, pearls, arrows. Looted chests are remembered so they aren't opened twice.
Pure `loot_plan` is offline-tested."""
import math

from . import api, nav, tape
from .api import McError, NotAvailable, log
from .beliefs import slot_cost_s
from .skill import skill
from .world import Inventory, find

def loot_plan(slots, prices, bag_free, stack=64):
    """Pure: container slot numbers worth taking, dearest first.

    There used to be a hand-written list of interesting suffixes here. It had no wheat in it, no potatoes and no
    planks, so the agent walked to a village chest, opened it, took nothing, and wrote the chest down as looted.
    A list of names is a price nobody can check.

    What a stack is worth is what getting it another way would cost (`prices`, the round's shadow prices), and what
    taking it costs is the slot it eats (`actions.slot_cost_s`, which grows as the bag fills). Take it while the
    first is bigger than the second — so wheat is loot in a world where bread is dear, sticks are loot only when
    the bag is empty, and something nobody has a price for is left where it is.
    """
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

    try:
        hits = unlooted_chests(_Ctx())
    except (McError, tape.ReplayMiss) as err:
        # A feasibility check is asked every round, including while a recorded round is being replayed offline,
        # where this scan was never made. "Nobody could look" is not "there is a chest": it answers no, and says
        # so rather than throwing the whole round away.
        api.swallowed("loot.unlooted_chests_cached", err)
        return []
    _CACHE.update(t=time.time(), pos=area, hits=hits)
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
    prices = ctx.prices()
    if not prices:
        # No price table, no decision: taking nothing and calling the chest looted is how a chest of iron got
        # written off. Say so instead — whoever built this context owes the skill its prices.
        raise NotAvailable("no price table: cannot say what is worth taking")
    taken = 0
    try:
        from .world import container
        for slot in loot_plan(container()["slots"], prices, Inventory().free_slots()):
            api.post("/click", {"slot": slot, "button": 0, "action": "QUICK_MOVE"})
            taken += 1
            yield taken
    finally:
        api.post("/close")
    ctx.mem.data.setdefault("looted", []).append(list(pos))
    ctx.mem.save()
    log(f"looted {taken} stacks from the chest at {pos}")
    return taken

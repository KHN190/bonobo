"""Upkeep skills: recover dropped items after a death, repair a worn tool by combining two in the crafting grid.
Pure `repair_pair` is offline-tested."""
import math

from . import api, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .world import Inventory


def repair_pair(slots, kind):
    """Pure: two damaged tools of the same item (e.g. two stone pickaxes) whose combined durability beats the best
    one — crafting them together repairs (vanilla grid repair, +5 %). Returns (item id, slot a, slot b) or None."""
    tools = [s for s in slots if s["id"].endswith("_" + kind) and s.get("maxDamage")]
    by_item = {}
    for s in tools:
        by_item.setdefault(s["id"], []).append(s)
    best = None
    for item, stacks in by_item.items():
        if len(stacks) < 2:
            continue
        a, b = sorted(stacks, key=lambda s: s["maxDamage"] - s.get("damage", 0))[:2]
        left = lambda s: s["maxDamage"] - s.get("damage", 0)   # noqa: E731
        combined = min(a["maxDamage"], left(a) + left(b) + a["maxDamage"] // 20)
        if combined > max(left(s) for s in stacks) and (best is None or combined > best[0]):
            best = (combined, item, a["slot"], b["slot"])
    return None if best is None else best[1:]


@skill(budget=60, stall=30, per_unit=5)
def repair_tool(ctx, kind="pickaxe"):
    """Combine the two most worn tools of one kind in the 2×2 grid into one repaired tool."""
    pair = repair_pair(Inventory().slots, kind)
    if pair is None:
        raise NotAvailable(f"no two {kind}s of the same kind worth combining")
    item = pair[0]
    before = sum(1 for s in Inventory().slots if s["id"] == item)
    r = api.run({"type": "craft", "pattern": [item, item, None, None], "count": 1}, wait=30)
    yield None
    if sum(1 for s in Inventory().slots if s["id"] == item) >= before:
        raise McError(f"repairing {item.split(':')[1]} failed: {r['message']}")
    log(f"repaired a {item.split(':')[1]} by combining two")
    return item


@skill(budget=300, stall=90, per_unit=120)
def recover_items(ctx):
    """Go back to the last death spot within 5 minutes and pick up what dropped there."""
    s = api.get("/state")
    death = ctx.mem.recent_death(s["dimension"])
    if death is None:
        raise NotAvailable("no recent death to recover from")
    pos = tuple(death["pos"])
    log(f"   recovering items at the death spot {pos}")
    if not nav.go_to(pos, ctx.policy, range_=2, attempts=1):
        raise api.NavFailed(f"death spot {pos} not reachable")
    before = Inventory().used_slots()
    api.run({"type": "collect", "radius": 10}, wait=60)
    yield Inventory().used_slots()
    got = Inventory().used_slots() - before
    # Either way the note is spent: what is here is now carried, and what is not here is not coming back. A record
    # the world has already answered must be retired on arrival rather than left to expire on a timer, or the same
    # sixty-block walk is worth the same seconds again five minutes later.
    ctx.mem.forget_death(pos)
    log(f"recovered {got} stacks at {pos}" if got else f"nothing left at {pos}: the drops are gone")
    return True

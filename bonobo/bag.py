"""The bag: pure decisions about what to carry, throw and store. No game access here — skills.py executes them (tidy_inventory throws, deposit stores). Offline-testable with plain slot dicts."""

from .world import add

PICKUP_FILTER_AT = 28

# item ids the committed plan will consume: never thrown or stored (tidy once threw a plan's fresh planks every 8 s)
RESERVED = set()

def reserved_stacks(slots):
    """Pure: the biggest stack of each reserved item id (reserving every stack would make the bag untidyable)."""

    best = {}
    for s in slots:
        if s["id"] in RESERVED and (s["id"] not in best or s.get("count", 1) > best[s["id"]].get("count", 1)):
            best[s["id"]] = s
    return list(best.values())

def reserved_ids(plan, needs=()):
    """Pure: every item id a plan passes through plus the goal's needs — the one reservation list."""

    from .knowledge import members
    tokens = set()
    for step in plan:
        tokens.add(step.token)
        tokens.update((step.detail or {}).get("inputs", {}).keys())
    for need in needs:
        if need and need[0] != "tool":
            tokens.add(need[0])
    ids = set()
    for t in tokens:
        ids.update(members(t))
    return ids

# what a nearly full bag still picks up: the floor and what the route to the dragon is made of
PICKUP_ALWAYS = ("minecraft:raw_iron", "minecraft:raw_gold", "minecraft:iron_ingot", "minecraft:gold_ingot",
                 "minecraft:diamond", "minecraft:ender_pearl", "minecraft:blaze_rod", "minecraft:obsidian",
                 "minecraft:string", "minecraft:flint")

def pickup_whitelist(used_slots, wanted=()):
    """Pure: None (collect all) below PICKUP_FILTER_AT slots; above, only the keep list, supplies and the task's own items."""

    if used_slots < PICKUP_FILTER_AT:
        return None
    from .knowledge import members
    ids = {i for token in FLOOR if token != "building" for i in _floor_ids(token)} | set(PICKUP_ALWAYS)
    for token in ("food", "coal", "log", "planks", "wool"):
        ids |= set(members(token))
    for token in wanted:
        ids |= set(members(token))
    return sorted(ids)

# what the bag never goes below; working tools and armour are kept whole; everything else is priced (`let_go`)
FLOOR = {"food": 8, "building": 64, "minecraft:torch": 16, "minecraft:bucket": 1, "minecraft:water_bucket": 1,
         "minecraft:lava_bucket": 1, "bed": 1, "minecraft:crafting_table": 1, "minecraft:furnace": 1,
         "minecraft:flint_and_steel": 1, "minecraft:shield": 1, "coal": 16}
UNPRICED_S = 1.0          # seconds to get again when nothing prices an item: it goes first (junk)

def _floor_ids(token):
    from .knowledge import ALL_FOOD, RAW_MEAT, members
    if token == "food":
        return list(ALL_FOOD) + list(RAW_MEAT)     # raw meat is the next meal while cooked food is short
    return list(members(token))

def dead(s):
    """A tool or armour at <= 1 durability: the mod refuses it, it only takes a slot."""
    return bool(s.get("maxDamage")) and s["maxDamage"] - s.get("damage", 0) <= 1

def kept(slots):
    """Pure: stacks kept whatever — one of each RESERVED id, working tools and armour, the biggest up to FLOOR."""

    keep = list(reserved_stacks(slots)) + [s for s in slots if s.get("maxDamage") and not dead(s)]
    for token, n in FLOOR.items():
        ids, have = set(_floor_ids(token)), 0
        for st in sorted((x for x in slots if x["id"] in ids), key=lambda x: -x.get("count", 1)):
            if have >= n:
                break
            if st not in keep:
                keep.append(st)
            have += st.get("count", 1)
    return keep

def reget_seconds(s, price=None):
    """Seconds to get this stack again: the planner's price of one (cost.Prices, `price(item)`) × its count."""
    one = price(s["id"]) if price else None
    return (UNPRICED_S if one is None else float(one)) * s.get("count", 1)

def let_go(slots, need, price=None, chest_s=None, lava_near=False):
    """Pure: [(stack, "drop" | "deposit")] freeing `need` slots."""

    from .api import NotAvailable
    keep = kept(slots)
    order = [s for s in slots if dead(s)] + sorted(
        (s for s in slots if s not in keep and not dead(s)), key=lambda s: (reget_seconds(s, price), s.get("count", 1)))
    from .data import VALUABLES
    out = []
    for s in order:
        if len(out) >= need:
            break
        worth = reget_seconds(s, price)
        valuable = s["id"] in VALUABLES
        if chest_s is not None and (valuable or worth > chest_s or lava_near):
            out.append((s, "deposit"))
        elif not lava_near and not valuable:        # a valuable is never dropped: deposited, or kept
            out.append((s, "drop"))
    if need > 0 and not out:
        raise NotAvailable("bag: every stack is needed (plans, working tools, the upkeep floor)"
                           + (" and lava is near: nothing is dropped" if lava_near else ""))
    return out

def empty_how(slots, need, price=None, chest_s=None, lava_near=False):
    """Pure: "deposit" when let_go puts a stack in an existing chest, else "drop" (the cheapest stacks thrown)."""

    plan = let_go(slots, need, price, chest_s, lava_near)
    return "deposit" if any(how == "deposit" for _s, how in plan) else "drop"

def free_slots_plan(slots, need=0, price=None):
    """Pure: the stacks to drop to free `need` slots (let_go without a chest); dead tools always go."""
    try:
        plan = [s for s, how in let_go(slots, need, price) if how == "drop"]
    except Exception:
        plan = []
    return plan + [s for s in slots if dead(s) and s not in plan]

FREE_SLOTS_TARGET = 5     # keep this many slots free: crafting, pickups and loot need room

def throw_direction(region, inside):
    """Pure: the side open at feet and head with the most room beyond to throw into, or None in a sealed shaft."""

    x, y, z = inside
    best, best_room = None, 0
    for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        room = 0
        for k in (1, 2, 3):
            c = (x + dx * k, y, z + dz * k)
            if region.solid(c) or region.solid(add(c, (0, 1, 0))):
                break
            room += 1
        if room > best_room:
            best, best_room = (dx, dz), room
    # thrown items fly ~2 blocks: into a smaller niche they land back at our feet
    return best if best_room >= 3 else None

def store_plan(slots):
    """Pure: player stacks to move into a chest — everything not kept, dead tools too."""
    keep = kept(slots)
    return [s for s in slots if s not in keep]

STACK = 64

def has_room(slots, free, ids):
    """Pure: can one more of these (`ids`) go in the bag — a free slot, or a stack of one of them not yet full."""

    return free > 0 or any(s["id"] in ids and int(s.get("count", 1)) < STACK for s in slots)

def supports(feet):
    """Pure: the cells the body stands on, never mined unless digging down on purpose (a full-bag miner fell through its floor)."""

    x, y, z = feet
    return {(x + dx, y - 1, z + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)}

def under(feet, cell):
    """Pure: `cell` is in the body's own column below the feet, at any depth."""

    return cell[0] == feet[0] and cell[2] == feet[2] and cell[1] < feet[1]

FACES = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0))

def floored(region, cell, drop):
    """Pure: a body standing in `cell` has ground within `drop` blocks under it."""

    x, y, z = cell
    for k in range(1, drop + 2):
        c = (x, y - k, z)
        if not region.inside(c):
            return False
        if region.solid(c) or region.name(c).endswith("water"):
            return True
    return False

def buried(region, cell):
    """Pure: every face of `cell` is solid (read): no way at it but digging."""
    return all(region.inside(f) and region.solid(f) for f in (add(cell, d) for d in FACES))

def stand_spot(region, cell, drop):
    """Pure: `cell` can be worked at — some open face has a standing place beside it (ground within `drop`), or its
    open faces are only pockets too small to stand in (a neighbour mined out): the approach digs to it as to a
    buried cell. Refused only when an open face gives onto air over a fall deeper than `drop`."""

    if buried(region, cell):
        return True
    over_a_fall = False
    for face in (add(cell, d) for d in FACES):
        if region.inside(face) and region.solid(face):
            continue
        for s in (face, add(face, (0, -1, 0))):
            head = add(s, (0, 1, 0))
            if not all(region.inside(c) for c in (s, head)):
                return True                     # beyond what was read: not a drop the blocks show
            if not region.solid(s) and not region.solid(head):
                if floored(region, s, drop):
                    return True
                over_a_fall = True              # room to stand, nothing under it
    return not over_a_fall

def mineable(cells, feet, region=None, drop=None):
    """Pure: the cells breakable from `feet`, in order — never the floor, our own column below, or a face only over a deep drop."""

    feet = tuple(feet)
    floor = supports(feet)
    ok = [tuple(c) for c in cells if tuple(c) not in floor and not under(feet, tuple(c))
          and (region is None or stand_spot(region, tuple(c), drop))]
    if region is None:
        return ok
    # open-faced first, buried only when nothing open is left (else a batch undermined its own floor)
    open_ = [c for c in ok if not buried(region, c)]
    return open_ or ok

def refused(cells, refused_before, jar_digs):
    """Pure: of the cells a mine_many broke none of, (asked again after making a way, dropped)."""

    cells = {tuple(c) for c in cells}
    if jar_digs:
        return set(), cells
    drop = cells & set(refused_before)
    return cells - drop, drop


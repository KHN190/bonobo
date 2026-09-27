"""The bag: pure decisions about what to carry, throw and store. No game access here — skills.py executes them (tidy_inventory throws, deposit stores). Offline-testable with plain slot dicts."""

from .world import add

PICKUP_FILTER_AT = 28

# Item ids the committed plan will consume (set by the brain every round). Throwing, storing and slot freeing never
# touch them: tidy once threw the planks a wheat-farm plan had just crafted, every 8 s, and the plan re-crafted them.
RESERVED = set()

def reserved_stacks(slots):
    """Pure: the stacks kept for open goals' plans — the biggest stack of each reserved item id, not all of them (reserving every cobblestone stack would make the bag impossible to tidy)."""

    best = {}
    for s in slots:
        if s["id"] in RESERVED and (s["id"] not in best or s.get("count", 1) > best[s["id"]].get("count", 1)):
            best[s["id"]] = s
    return list(best.values())

def reserved_ids(plan, needs=()):
    """Pure: every item id a plan consumes or produces on the way (step inputs, intermediate outputs) plus the goal's own needs — the one reservation list bag, deposit and free_slots all read."""

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

# What a nearly full bag still walks over to pick up: the floor (blocks aside: a tunnel is made of them) and what
# the route to the dragon is made of.
PICKUP_ALWAYS = ("minecraft:raw_iron", "minecraft:raw_gold", "minecraft:iron_ingot", "minecraft:gold_ingot",
                 "minecraft:diamond", "minecraft:ender_pearl", "minecraft:blaze_rod", "minecraft:obsidian",
                 "minecraft:string", "minecraft:flint")

def pickup_whitelist(used_slots, wanted=()):
    """Pure: None (collect everything) below PICKUP_FILTER_AT slots; from there only the keep list, basic supplies and what the task is for."""

    if used_slots < PICKUP_FILTER_AT:
        return None
    from .knowledge import members
    ids = {i for token in FLOOR if token != "building" for i in _floor_ids(token)} | set(PICKUP_ALWAYS)
    for token in ("food", "coal", "log", "planks", "wool"):
        ids |= set(members(token))
    for token in wanted:
        ids |= set(members(token))
    return sorted(ids)

# Upkeep's floor: what the bag never goes below, by kind — the next meals, blocks to bridge and wall with, light,
# water, a bed, the two stations and fire. Tools and armour that still work are kept whole (spares included).
# Everything else is priced: what it costs to get again (`let_go`). One table, instead of seven that disagreed.
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
    """Pure: the stacks the bag keeps whatever — one stack of each item a held plan uses (RESERVED), working tools and armour, and the biggest stacks that fill the FLOOR."""

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
    """Pure: how the bag is emptied this time — "deposit" when `let_go` puts any stack in a chest that already exists (`chest_s`: seconds to reach it, None when there is none), else "drop" (the cheapest stacks thrown)."""

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
    """Pure: a horizontal side open at feet and head height to throw items into — the one with the most open room beyond (a tunnel's way back rather than a 1-block niche), or None in a sealed shaft."""

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
    # Thrown items fly ~2 blocks: into a 1–2 block niche they land back at our feet and get picked up again.
    return best if best_room >= 3 else None

def store_plan(slots):
    """Pure: the player stacks to move into a chest — everything the bag does not keep (`kept`), dead tools too."""
    keep = kept(slots)
    return [s for s in slots if s not in keep]

STACK = 64

def has_room(slots, free, ids):
    """Pure: can one more of these (`ids`) go in the bag — a free slot, or a stack of one of them not yet full."""

    return free > 0 or any(s["id"] in ids and int(s.get("count", 1)) < STACK for s in slots)

def supports(feet):
    """Pure: the cells the body stands on — the one under the feet and the ring around it the body's edge can rest on — never mined by a skill that is not digging down on purpose (a full-bag miner broke its own floor and fell through the platform; a stone batch dug the floor ring at its feet)."""

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
    """Pure: some open face of `cell` has a standing place beside it — feet in the face's cell or one below it (head in the face), room for the body, ground within `drop`."""

    if buried(region, cell):
        return True
    for face in (add(cell, d) for d in FACES):
        if region.inside(face) and region.solid(face):
            continue
        for s in (face, add(face, (0, -1, 0))):
            head = add(s, (0, 1, 0))
            if not all(region.inside(c) for c in (s, head)):
                return True                     # beyond what was read: not a drop the blocks show
            if not region.solid(s) and not region.solid(head) and floored(region, s, drop):
                return True
    return False

def mineable(cells, feet, region=None, drop=None):
    """Pure: the cells a skill may break standing at `feet`, in the order given — never the floor under or around the feet (`supports`), nor anything in the body's own column below it (`under`), nor (with `region`) a cell whose every open face is over a drop deeper than `drop` (`stand_spot`)."""

    feet = tuple(feet)
    floor = supports(feet)
    ok = [tuple(c) for c in cells if tuple(c) not in floor and not under(feet, tuple(c))
          and (region is None or stand_spot(region, tuple(c), drop))]
    if region is None:
        return ok
    # Open-faced first; a buried cell only when nothing open is left — else a stone batch dug the buried cells under
    # its own floor ring as readily as the open ones beside it (mine_stone__buried_by_sand: sand fell, a loop).
    open_ = [c for c in ok if not buried(region, c)]
    return open_ or ok

def refused(cells, refused_before, jar_digs):
    """Pure: of the cells a mine_many broke none of, (asked again after making a way, dropped)."""

    cells = {tuple(c) for c in cells}
    if jar_digs:
        return set(), cells
    drop = cells & set(refused_before)
    return cells - drop, drop


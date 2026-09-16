"""The bag: pure decisions about what to carry, throw and store. No game access here — skills.py executes them
(tidy_inventory throws, deposit stores). Offline-testable with plain slot dicts."""
from .data import GROUPS, KEEP_BUILDING_BLOCKS
from .world import add

PICKUP_FILTER_AT = 28

# Item ids the committed plan will consume (set by the brain every round). Throwing, storing and slot freeing never
# touch them: tidy once threw the planks a wheat-farm plan had just crafted, every 8 s, and the plan re-crafted them.
RESERVED = set()


def reserved_stacks(slots):
    """Pure: the stacks kept for open goals' plans — the biggest stack of each reserved item id, not all of them
    (reserving every cobblestone stack would make the bag impossible to tidy)."""
    best = {}
    for s in slots:
        if s["id"] in RESERVED and (s["id"] not in best or s.get("count", 1) > best[s["id"]].get("count", 1)):
            best[s["id"]] = s
    return list(best.values())


def reserved_ids(plan, needs=()):
    """Pure: every item id a plan consumes or produces on the way (step inputs, intermediate outputs) plus the goal's
    own needs — the one reservation list bag, deposit and free_slots all read."""
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


def pickup_whitelist(used_slots, wanted=()):
    """Pure: None (collect everything) below PICKUP_FILTER_AT slots; from there only the keep list, basic supplies
    and what the task is for. Vanilla pickup itself can't be filtered, but the collect sweep can stop walking onto
    cobblestone and dirt — the source of the bag filling up in tunnels and shafts."""
    if used_slots < PICKUP_FILTER_AT:
        return None
    from .knowledge import members
    ids = set(KEEP_ITEMS)
    for token in ("food", "coal", "log", "planks", "wool"):
        ids |= set(members(token))
    for token in wanted:
        ids |= set(members(token))
    return sorted(ids)


# What travels with us; everything else goes into the home chest. Amounts are caps per item group.
KEEP_ALWAYS_SUFFIX = ("_pickaxe", "_axe", "_shovel", "_sword", "_helmet", "_chestplate", "_leggings", "_boots",
                      "_bed", "_boat")


KEEP_ITEMS = {"minecraft:torch": 64, "minecraft:ladder": 32, "minecraft:crafting_table": 1, "minecraft:furnace": 1,
              "minecraft:bucket": 2, "minecraft:water_bucket": 1, "minecraft:lava_bucket": 1, "minecraft:shield": 1,
              "minecraft:flint_and_steel": 1, "minecraft:iron_ingot": 64, "minecraft:raw_iron": 64,
              "minecraft:raw_gold": 64, "minecraft:gold_ingot": 64, "minecraft:diamond": 64, "minecraft:flint": 8,
              "minecraft:stick": 32, "minecraft:ender_pearl": 16, "minecraft:blaze_rod": 16,
              "minecraft:ender_eye": 16, "minecraft:obsidian": 16, "minecraft:bow": 1, "minecraft:arrow": 64,
              "minecraft:string": 16, "minecraft:feather": 16, "minecraft:beef": 32, "minecraft:porkchop": 32,
              "minecraft:mutton": 32, "minecraft:chicken": 32, "minecraft:rabbit": 32,
              # The carried chest is kept (deposit used to store it, then the goal crafted another, over and over);
              # farming supplies travel with us.
              "minecraft:chest": 1, "minecraft:wheat_seeds": 32, "minecraft:wheat": 32, "minecraft:carrot": 16,
              "minecraft:stone_hoe": 1, "minecraft:iron_hoe": 1, "minecraft:wooden_hoe": 1,
              "minecraft:oak_sapling": 8, "minecraft:spruce_sapling": 8, "minecraft:birch_sapling": 8}


KEEP_GROUPS = {"food": 64, "coal": 64, "log": 32, "planks": 32, "wool": 3, "door": 3, "building": KEEP_BUILDING_BLOCKS}


def tidy_plan(slots):
    """Pure: the slots to throw away on the spot — DISCARD items and stacks beyond EXCESS_CAP (smaller stacks go
    first). Worn-out tools are not thrown: a player standing still picks them straight back up; deposit stores them."""
    from .data import DISCARD, EXCESS_CAP, KEEP_BUILDING_BLOCKS
    slots = [s for s in slots if s not in reserved_stacks(slots)]
    throw, kept = [], {}
    # Building blocks beyond the keep cap (plain cobble/deepslate kept first, then the biggest stacks). A full
    # inventory makes crafting fail, so this cap is enforced on the spot, not only at a chest.
    plain = ("minecraft:cobblestone", "minecraft:cobbled_deepslate")
    building = sorted((s for s in slots if s["id"] in GROUPS["building"]),
                      key=lambda s: (s["id"] not in plain, -s.get("count", 1)))
    held = 0
    for s in building:
        if held + s["count"] <= KEEP_BUILDING_BLOCKS:
            held += s["count"]
        else:
            throw.append(s)
    for s in sorted(slots, key=lambda s: -s.get("count", 1)):
        if s in throw:
            continue
        item = s["id"]
        if item in DISCARD:
            throw.append(s)
        elif item in EXCESS_CAP:
            # Keep stacks until the cap is reached, then drop the rest: a cap never empties an item completely
            # just because its single stack is bigger than the cap.
            if kept.get(item, 0) >= EXCESS_CAP[item]:
                throw.append(s)
            else:
                kept[item] = kept.get(item, 0) + s["count"]
    # A tool at <= 1 durability is dead weight: the mod refuses to use it (tidy_inventory steps away after throwing).
    throw += [s for s in slots if s not in throw and s.get("maxDamage") and s["maxDamage"] - s.get("damage", 0) <= 1]
    return throw


# Materials with little use on the route to the dragon, kept only up to a cap when slots are needed.
LOW_VALUE_CAPS = {"minecraft:redstone": 64, "minecraft:lapis_lazuli": 0, "minecraft:rabbit_hide": 0,
                  "minecraft:leather": 0, "_sapling": 0, "minecraft:crafting_table": 1, "door": 1, "wool": 3,
                  "minecraft:raw_copper": 0, "minecraft:copper_ingot": 0, "minecraft:bone_meal": 0,
                  "minecraft:string": 16, "minecraft:feather": 16}


# Rough value for the last-resort ordering (default 1). Higher = dropped later.
STACK_VALUE = {"minecraft:redstone": 2, "minecraft:lapis_lazuli": 2, "minecraft:coal": 6, "minecraft:dirt": 1,
               "minecraft:tuff": 1, "minecraft:cobbled_deepslate": 2, "minecraft:cobblestone": 2,
               "minecraft:beef": 3, "minecraft:porkchop": 3, "minecraft:mutton": 3, "minecraft:chicken": 3,
               "minecraft:rabbit": 3, "minecraft:flint": 3, "minecraft:stick": 3, "log": 4}


def _stack_value(s):
    """Last-resort drop order: explicit item value, else the value of its group (logs), else 1."""
    item = s["id"]
    if item in STACK_VALUE:
        return STACK_VALUE[item]
    for token, value in STACK_VALUE.items():
        if token in GROUPS and item in GROUPS[token]:
            return value
    return 1


PROTECTED_IDS = {"minecraft:bucket", "minecraft:water_bucket", "minecraft:lava_bucket", "minecraft:flint_and_steel",
                 "minecraft:furnace", "minecraft:torch", "minecraft:shield", "minecraft:bow", "minecraft:arrow",
                 "minecraft:ender_pearl", "minecraft:ender_eye", "minecraft:blaze_rod", "minecraft:blaze_powder",
                 "minecraft:obsidian", "minecraft:chest", "minecraft:wheat_seeds",
                 # fuel: every smelt (gold helmet, cooked food) and torch needs it — it was thrown while both waited
                 "minecraft:coal", "minecraft:charcoal"}


PROTECTED_SUFFIX = ("_ingot", "diamond", "emerald", "_bed", "raw_iron", "raw_gold", "_helmet", "_chestplate",
                    "_leggings", "_boots",
                    # things goals make or need (dropping them makes the goal craft them again, forever)
                    "_wool", "_door", "crafting_table", "ladder")


def _protected_stack(s):
    item = s["id"]
    if s.get("maxDamage"):
        return s["maxDamage"] - s.get("damage", 0) > 1          # working tools and armor
    if item in PROTECTED_IDS or item.endswith(PROTECTED_SUFFIX):
        return True
    return item.startswith("minecraft:cooked_") or item in ("minecraft:bread", "minecraft:golden_carrot")


RAW_MEAT = ("minecraft:beef", "minecraft:porkchop", "minecraft:mutton", "minecraft:chicken", "minecraft:rabbit")


SURPLUS_CAP = {"log": 16, "minecraft:stick": 16, "minecraft:flint": 4, "minecraft:sand": 0, "minecraft:gravel": 0}


def free_slots_plan(slots, need=0):
    """Pure: which stacks to drop to free `need` slots, least valuable first. Tiers 1–3 (tidy_plan: junk, stacks over
    caps, building blocks over 128) always go; then only as many as needed, smallest stacks first within a tier:
      4. building blocks beyond 64 (plain cobble/deepslate kept longest)
      5. raw meat, when 8+ other food is carried
      6. surplus materials: logs / sticks beyond 16, flint beyond 4, sand, leftover gravel
    Never: tools (spares included), armor, ingots / gems, torches, beds, doors, ladders, buckets, stations, cooked food."""
    from .data import GROUPS as G, KEEP_BUILDING_BLOCKS
    from .knowledge import ALL_FOOD, members as mem_of
    throw = tidy_plan(slots)
    missing = need - len(throw)
    if missing <= 0:
        return throw
    kept_for_plans = reserved_stacks(slots)
    left = [s for s in slots if s not in throw and s not in kept_for_plans]
    cooked = sum(s["count"] for s in slots if s["id"] in ALL_FOOD)
    if cooked < 8:   # same threshold as the raw-meat tier below: with 8+ other food raw meat may go
        # Raw meat is the next meal while cooked food is short: never a slot-freeing candidate (it was thrown ten
        # times while the food stock was being hunted).
        left = [s for s in left if s["id"] not in RAW_MEAT]
        throw = [s for s in throw if s["id"] not in RAW_MEAT]
    tiers = []
    plain = ("minecraft:cobblestone", "minecraft:cobbled_deepslate")
    building = sorted((s for s in left if s["id"] in G["building"]), key=lambda s: (s["id"] not in plain, -s["count"]))
    held, t4 = 0, []
    for s in building:
        if held + s["count"] <= min(64, KEEP_BUILDING_BLOCKS):
            held += s["count"]
        else:
            t4.append(s)
    tiers.append(t4)
    other_food = sum(s["count"] for s in left if s["id"] in ALL_FOOD)
    tiers.append([s for s in left if s["id"] in RAW_MEAT] if other_food >= 8 else [])
    t6 = []
    for token, cap in SURPLUS_CAP.items():
        stacks = sorted((s for s in left if s["id"] in mem_of(token)), key=lambda s: -s["count"])
        kept = 0
        for s in stacks:
            if kept >= cap:
                t6.append(s)
            else:
                kept += s["count"]
    tiers.append(t6)
    # Tools are never dropped while they still work: a "worn duplicate" is the spare that saves the day when the
    # main one breaks deep underground. A tool at <= 1 durability is useless (the mod refuses it) and may go.
    t7 = []
    for token, cap in LOW_VALUE_CAPS.items():
        stacks = sorted((s for s in left if (s["id"] in mem_of(token) or (token.startswith("_") and s["id"].endswith(token)))
                         and not _protected_stack(s)), key=lambda s: -s["count"])
        kept = 0
        for s in stacks:
            if kept >= cap:
                t7.append(s)
            else:
                kept += s["count"]
    t7 += [s for s in left if s.get("maxDamage") and s["maxDamage"] - s.get("damage", 0) <= 1]
    tiers.append(t7)
    # Last resort, so a full bag can ALWAYS be emptied: anything not protected, cheapest first — except the
    # building blocks goals keep (<= 64): dropping those just sends a goal off to mine them again.
    building_total = sum(s["count"] for s in left if s["id"] in G["building"])
    tiers.append(sorted((s for s in left if not _protected_stack(s)
                         and not (s["id"] in G["building"] and building_total <= 64)),
                        key=lambda s: (_stack_value(s), s["count"])))
    for tier in tiers:
        # Cheapest first (value), then smaller stacks: re-sorting by count alone threw logs before cobblestone.
        for s in sorted(tier, key=lambda s: (_stack_value(s), s["count"])):
            if missing <= 0:
                return throw
            if s not in throw:
                throw.append(s)
                missing -= 1
    return throw


FREE_SLOTS_TARGET = 5     # keep this many slots free: crafting, pickups and loot need room


def throw_direction(region, inside):
    """Pure: a horizontal side open at feet and head height to throw items into — the one with the most open room
    beyond (a tunnel's way back rather than a 1-block niche), or None in a sealed shaft."""
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
    """Returns the player slots to move into storage: anything not on the keep list, or beyond its cap."""
    from .knowledge import members
    budget = dict(KEEP_ITEMS)
    group_budget = dict(KEEP_GROUPS)
    group_of = {m: g for g in KEEP_GROUPS for m in members(g)}
    move = []
    kept_for_plans = reserved_stacks(slots)
    for s in slots:
        item = s["id"]
        if s in kept_for_plans:
            continue   # an open goal's plan needs it
        if item.endswith(KEEP_ALWAYS_SUFFIX):
            # Worn-out tools (the mod refuses to use them at <= 1) only take up slots.
            if "maxDamage" in s and s["maxDamage"] - s["damage"] <= 2:
                move.append(s)
            continue
        # A stack moves only when the allowance is already used up: shift-click moves whole stacks, and keeping a
        # little extra beats storing the one furnace we carry.
        if item in budget:
            if budget[item] <= 0:
                move.append(s)
            budget[item] -= s["count"]
        elif item in group_of:
            g = group_of[item]
            if group_budget[g] <= 0:
                move.append(s)
            group_budget[g] -= s["count"]
        else:
            move.append(s)
    return move

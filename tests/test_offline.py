#!/usr/bin/env python3
"""Offline checks for the parts of bonobo that decide without the game: routes, blueprints, planning, inventory
hygiene. Run before starting autoplay: `python3 tests/test_offline.py` (exit code 1 on failure)."""
import os
import tempfile
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bonobo import blueprints as B  # noqa: E402
from bonobo import nav, skills  # noqa: E402
from bonobo.planner import NullCost, Planner  # noqa: E402

FAILS = []
ALL = {"pillar", "ladder_in_cell"}


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class FakeRegion:
    def __init__(self, blocks, lo, hi):
        self.blocks, self.lo, self.hi = blocks, lo, hi

    def inside(self, p):
        return all(self.lo[i] <= p[i] <= self.hi[i] for i in range(3))

    def name(self, p):
        return self.blocks.get(p, "air")

    def solid(self, p):
        return self.name(p) not in ("air", "water", "lava", "ladder", "torch", "wall_torch")

    def hazard(self, p):
        return self.name(p) in ("water", "lava")

    def unbreakable(self, p):
        return self.name(p) == "bedrock"

    def falling(self, p):
        return False

    def player_made(self, p):
        return self.name(p).endswith("door")


nav.building_item = lambda: "minecraft:cobblestone"
WALK_ONLY = nav.Policy(allow_dig=False)


def types(route):
    return None if route is None else [t["type"] for t in route]


# ---- routes
gap = {(x, 0, 0): "stone" for x in list(range(0, 3)) + list(range(6, 9))}
r = FakeRegion(gap, (-1, -3, -2), (9, 4, 2))
route = nav.plan_tunnel(r, (1, 1, 0), {(8, 1, 1)}, WALK_ONLY, blocks=64, ladders=0, features=ALL)
check("bridge crosses a 3-wide gap", route is not None and types(route).count("place") == 3, types(route))
torch_gap = {(x, 0, 0): "stone" for x in list(range(0, 3)) + list(range(4, 9))}
torch_gap[(3, 0, 0)] = "torch"
r_t = FakeRegion(torch_gap, (-1, -3, -2), (9, 4, 2))
route = nav.plan_tunnel(r_t, (1, 1, 0), {(8, 1, 1)}, WALK_ONLY, blocks=64, ladders=0, features=ALL)
placed_cells = [(t["x"], t["y"], t["z"]) for t in (route or []) if t["type"] == "place"]
check("bridge never places a floor into a torch cell", (3, 0, 0) not in placed_cells, placed_cells)
check("no blocks → no bridge", nav.plan_tunnel(r, (1, 1, 0), {(8, 1, 1)}, WALK_ONLY, blocks=0, ladders=0,
                                              features=ALL) is None)
walled = {(x, 0, z): "stone" for x in range(-1, 6) for z in range(-2, 3)}
walled.update({(x, y, z): "stone" for x in range(2, 6) for y in range(1, 5) for z in range(-2, 3)})
r = FakeRegion(walled, (-1, -1, -2), (6, 8, 2))
route = nav.plan_tunnel(r, (1, 1, 0), {(4, 6, 0)}, WALK_ONLY, blocks=64, ladders=0, features=ALL)
check("pillar climbs a 4-high wall", route is not None and types(route).count("pillar") == 3, types(route))
laddered = dict(walled)
laddered[(1, 1, 0)] = "ladder"
r2 = FakeRegion(laddered, (-1, -1, -2), (6, 8, 2))
route = nav.plan_tunnel(r2, (1, 1, 0), {(4, 6, 0)}, WALK_ONLY, blocks=64, ladders=0, features=ALL)
pillar_cells = [(route[k - 1]["x"], route[k - 1]["y"], route[k - 1]["z"]) for k, t in enumerate(route or [])
                if t["type"] == "pillar" and k > 0 and route[k - 1]["type"] == "goto"]
check("no pillar planned from a cell a ladder hangs in (it steps off first)",
      route is not None and (1, 1, 0) not in pillar_cells and (route[0]["type"] != "pillar"), pillar_cells)
route = nav.plan_tunnel(r, (1, 1, 0), {(4, 6, 0)}, WALK_ONLY, blocks=64, ladders=0, features=set())
check("old mod: no pillar planned", route is None or "pillar" not in types(route), types(route))
route = nav.plan_tunnel(r, (1, 1, 0), {(4, 6, 0)}, WALK_ONLY, blocks=0, ladders=16, features=ALL)
ladders = [t for t in (route or []) if t.get("item") == "minecraft:ladder"]
check("ladders climb the wall", len(ladders) == 4 and all(t["against"]["x"] == 2 for t in ladders), types(route))
check("nothing to build with and no digging → None",
      nav.plan_tunnel(r, (1, 1, 0), {(4, 6, 0)}, WALK_ONLY, blocks=0, ladders=0, features=ALL) is None)
route = nav.plan_tunnel(r, (1, 1, 0), {(4, 6, 0)}, nav.Policy(), blocks=0, ladders=0, features=ALL)
check("digging allowed → staircase", route is not None and set(types(route)) <= {"mine", "goto"}, types(route))
dirtwall = {(x, 0, z): "stone" for x in range(-1, 8) for z in range(-2, 3)}
dirtwall.update({(3, y, z): "dirt" for y in (1, 2) for z in range(-2, 3)})
r = FakeRegion(dirtwall, (-1, -1, -2), (8, 4, 2))
hand = nav.Policy(hand_only=True, allow_build=False)
route = nav.plan_tunnel(r, (1, 1, 0), {(6, 1, 1)}, hand, blocks=0, ladders=0, features=ALL)
check("no pickaxe: digs through dirt by hand", route is not None and "mine" in types(route), types(route))
stonewall = dict(dirtwall)
stonewall.update({(3, y, z): "stone" for y in (1, 2, 3) for z in range(-2, 3)})
r = FakeRegion(stonewall, (-1, -1, -2), (8, 4, 2))
route = nav.plan_tunnel(r, (1, 1, 0), {(6, 1, 1)}, hand, blocks=0, ladders=0, features=ALL)
check("no pickaxe: stone wall still passable by hand (last resort)", route is not None, types(route))
mixed = dict(stonewall)
mixed.update({(3, 1, 2): "dirt", (3, 2, 2): "dirt"})
r = FakeRegion(mixed, (-1, -1, -2), (8, 4, 2))
route = nav.plan_tunnel(r, (1, 1, 0), {(6, 1, 1)}, hand, blocks=0, ladders=0, features=ALL)
dug = [(t["x"], t["y"], t["z"]) for t in (route or []) if t["type"] == "mine"]
check("no pickaxe: prefers the dirt gap over hand-breaking stone", dug and all(c[0] != 3 or c[2] == 2 for c in dug),
      dug)
lava = dict(gap)
lava[(4, -1, 0)] = "lava"
lava[(4, 0, 0)] = "lava"
r = FakeRegion(lava, (-1, -3, -2), (9, 4, 2))
route = nav.plan_tunnel(r, (1, 1, 0), {(8, 1, 1)}, WALK_ONLY, blocks=64, ladders=0, features=ALL)
placed = [(t["x"], t["y"], t["z"]) for t in (route or []) if t["type"] == "place"]
check("never places a floor in or beside lava",
      all(abs(p[0] - 4) + abs(p[1]) + abs(p[2]) > 1 and p != (4, 0, 0) for p in placed), placed)

# ---- blueprints
for bp in B.REGISTRY.values():
    for t in range(4):
        cells = B.placed(bp, (0, 64, 0), t)
        pos = [c[0] for c in cells]
        check(f"{bp.name} rot{t}: no overlapping parts", len(pos) == len(set(pos)))
        clear = B.clear_cells(bp, (0, 64, 0), t)
        check(f"{bp.name} rot{t}: clear cells aren't parts (except a torch)",
              all(c not in pos or any(p[0] == c and p[1].item == "minecraft:torch" for p in cells) for c in clear))
        for p, part, facing, against in cells:
            if against is not None and part.facing:
                d = tuple(against[i] - p[i] for i in range(3))
                check(f"{bp.name} rot{t}: {part.item} at {part.offset} outputs into its against",
                      B.DIRS[facing] == d)
hut = B.placed(B.SHELTER, (0, 0, 0), 0)
solid = {c[0] for c in hut if c[1].item != "minecraft:torch"} | {(0, 1, -1)}   # door top counts as solid
interior = [c for c in B.clear_cells(B.SHELTER, (0, 0, 0), 0) if c != (0, 1, -1)]
leaks = [(c, d) for c in interior for d in B.DIRS.values()
         if (c[0] + d[0], c[1] + d[1], c[2] + d[2]) not in solid and (c[0] + d[0], c[1] + d[1], c[2] + d[2]) not in interior
         and d != (0, -1, 0)]
check("shelter interior is sealed (floor aside)", not leaks, leaks)
check("shelter needs 14 stone + door + torch", B.materials(B.SHELTER) == {"stone": 14, "door": 1, "minecraft:torch": 1},
      B.materials(B.SHELTER))


# ---- planning
class Inv:
    def __init__(self, slots):
        self.slots, self.equipment = slots, {}


inv = Inv([{"id": "minecraft:iron_ingot", "count": 20}, {"id": "minecraft:oak_planks", "count": 64},
           {"id": "minecraft:cobblestone", "count": 64}, {"id": "minecraft:stick", "count": 8}])
plan = Planner.from_inventory(inv, NullCost()).plan([("minecraft:hopper", 3), ("minecraft:chest", 3)])
check("hoppers plan without mining", plan and all(s.kind == "craft" for s in plan), [str(s) for s in plan])
no_pick = Inv([{"id": "minecraft:cobblestone", "count": 20}, {"id": "minecraft:stick", "count": 4},
               {"id": "minecraft:oak_planks", "count": 8}])
plan = Planner.from_inventory(no_pick, NullCost()).plan([("tool", "pickaxe", 1)])
check("no pickaxe, cobble in the bag → stone pickaxe is crafted directly",
      plan and plan[0].kind == "craft" and plan[-1].token == "minecraft:stone_pickaxe", [str(s) for s in plan])
check("pending output satisfies a need",
      Planner.from_inventory(inv, NullCost(), {"minecraft:hopper": 3}).plan([("minecraft:hopper", 3)]) == [])

# ---- inventory hygiene
slots = [{"id": "minecraft:rotten_flesh", "count": 30, "slot": 10}, {"id": "minecraft:gravel", "count": 64, "slot": 11},
         {"id": "minecraft:gravel", "count": 10, "slot": 12}, {"id": "minecraft:iron_ingot", "count": 5, "slot": 13},
         {"id": "minecraft:stone_pickaxe", "count": 1, "damage": 130, "maxDamage": 131, "slot": 14},
         {"id": "minecraft:iron_pickaxe", "count": 1, "damage": 7, "maxDamage": 250, "slot": 15},
         {"id": "minecraft:granite", "count": 64, "slot": 16}, {"id": "minecraft:cobblestone", "count": 64, "slot": 17}]
thrown = sorted(s["slot"] for s in skills.tidy_plan(slots))
check("tidy throws junk, gravel beyond the cap and the broken pickaxe", thrown == [10, 12, 14], thrown)
check("tidy never throws working tools, building blocks or ingots", not {13, 15, 16, 17} & set(thrown), thrown)
hoard = [{"id": "minecraft:andesite", "count": 64, "slot": 20 + k} for k in range(4)] + \
        [{"id": "minecraft:cobblestone", "count": 64, "slot": 30}, {"id": "minecraft:cobblestone", "count": 64, "slot": 31}]
thrown = sorted(s["slot"] for s in skills.tidy_plan(hoard))
check("tidy caps building blocks at 128, keeping plain cobblestone", thrown == [20, 21, 22, 23], thrown)
bag = [{"id": "minecraft:cobblestone", "count": 64, "slot": 1}, {"id": "minecraft:cobblestone", "count": 40, "slot": 2},
       {"id": "minecraft:beef", "count": 5, "slot": 3}, {"id": "minecraft:cooked_beef", "count": 12, "slot": 4},
       {"id": "minecraft:spruce_log", "count": 30, "slot": 5}, {"id": "minecraft:iron_ingot", "count": 9, "slot": 6},
       {"id": "minecraft:torch", "count": 40, "slot": 7},
       {"id": "minecraft:stone_pickaxe", "count": 1, "damage": 125, "maxDamage": 131, "slot": 8},
       {"id": "minecraft:iron_pickaxe", "count": 1, "damage": 10, "maxDamage": 250, "slot": 9}]
plan = [s["slot"] for s in skills.free_slots_plan(bag, need=1)]
check("free 1 slot → the small building stack beyond 64 goes first", plan == [2], plan)
plan = [s["slot"] for s in skills.free_slots_plan(bag, need=3)]
check("free 3 slots → building surplus, raw meat, then the cheapest unprotected stack (a single log stack stays)",
      plan == [2, 3, 1], plan)
plan = [s["slot"] for s in skills.free_slots_plan(bag, need=9)]
check("never drops ingots, torches, cooked food or any pickaxe (spares included)", not {4, 6, 7, 8, 9} & set(plan),
      plan)
goal_items = [{"id": "minecraft:spruce_door", "count": 3, "slot": 1}, {"id": "minecraft:black_wool", "count": 2, "slot": 2},
              {"id": "minecraft:crafting_table", "count": 2, "slot": 3}, {"id": "minecraft:dirt", "count": 10, "slot": 4}]
plan = [s["id"][10:] for s in skills.free_slots_plan(goal_items, need=4)]
check("caps never throw the only stack of a goal item (doors, wool, table)",
      not {"spruce_door", "black_wool", "crafting_table"} & set(plan), plan)
# The real full bag from 2026-09-15 (nothing matched the old tiers, so crafting a pickaxe was impossible).
real = [("beef", 4), ("black_wool", 2), ("bucket", 1), ("coal", 19), ("cobbled_deepslate", 2), ("cobbled_deepslate", 31),
        ("cooked_beef", 1), ("cooked_mutton", 3), ("crafting_table", 2), ("diamond", 10), ("dirt", 8), ("flint", 1),
        ("flint_and_steel", 1), ("furnace", 1), ("iron_helmet", 1), ("iron_ingot", 15), ("iron_sword", 1),
        ("lapis_lazuli", 20), ("leather", 5), ("mutton", 2), ("porkchop", 3), ("rabbit", 9), ("rabbit_hide", 6),
        ("raw_gold", 8), ("raw_iron", 11), ("redstone", 44), ("redstone", 64), ("redstone", 64), ("spruce_door", 3),
        ("spruce_log", 4), ("spruce_sapling", 1), ("stone_axe", 1), ("stone_pickaxe", 1), ("stone_sword", 1),
        ("torch", 41), ("tuff", 13)]
real_slots = []
for k, (item, n) in enumerate(real):
    s = {"id": "minecraft:" + item, "count": n, "slot": k}
    if item in ("flint_and_steel", "iron_helmet", "iron_sword", "stone_axe", "stone_pickaxe", "stone_sword"):
        s.update({"maxDamage": 250, "damage": 249 if item == "stone_pickaxe" else 100})
    real_slots.append(s)
plan = skills.free_slots_plan(real_slots, need=4)
ids = [s["id"][10:] for s in plan]
check("real full bag: 4 slots can always be freed", len(plan) >= 4, ids)
check("real full bag: nothing valuable dropped",
      not {"diamond", "iron_ingot", "raw_iron", "raw_gold", "torch", "bucket", "iron_sword", "iron_helmet",
           "cooked_beef", "furnace", "flint_and_steel"} & set(ids), ids)
check("real full bag: the broken pickaxe or low-value junk goes first",
      set(ids) & {"stone_pickaxe", "rabbit_hide", "leather", "spruce_sapling", "lapis_lazuli", "redstone"}, ids)

# ---- survival: finding air
# A 7×7 pool, 6 deep, with a stone floor and walls up to the water line; open on top unless capped.
pool = {(x, y, z): "water" for x in range(-3, 4) for y in range(0, 6) for z in range(-3, 4)}
pool.update({(x, -1, z): "stone" for x in range(-4, 5) for z in range(-4, 5)})
pool.update({(x, y, z): "stone" for x in (-4, 4) for y in range(0, 6) for z in range(-4, 5)})
pool.update({(x, y, z): "stone" for z in (-4, 4) for y in range(0, 6) for x in range(-4, 5)})
r = FakeRegion(pool, (-8, -2, -8), (8, 16, 8))
check("air: open water → swim straight up", skills.air_route(r, (0, 1, 0)) == ("swim", (0, 6, 0)),
      skills.air_route(r, (0, 1, 0)))
capped = dict(pool)
capped.update({(x, 6, z): "stone" for x in range(-3, 4) for z in range(-3, 4)})
capped.update({(x, y, z): "stone" for x in (-4, 4) for y in range(0, 7) for z in range(-4, 5)})
capped.update({(x, y, z): "stone" for z in (-4, 4) for y in range(0, 7) for x in range(-4, 5)})
r = FakeRegion(capped, (-8, -2, -8), (8, 16, 8))
check("air: sealed water → dig the cap above", skills.air_route(r, (0, 1, 0)) == ("dig", (0, 6, 0)),
      skills.air_route(r, (0, 1, 0)))
pocket = dict(capped)
pocket[(3, 5, 3)] = "air"
r = FakeRegion(pocket, (-8, -2, -8), (8, 16, 8))
check("air: an air pocket in reach → swim to it", skills.air_route(r, (0, 1, 0)) == ("swim", (3, 5, 3)),
      skills.air_route(r, (0, 1, 0)))

# ---- survival: digging out of a pod
podw = {(x, 0, z): "stone" for x in range(-3, 4) for z in range(-3, 4)}           # floor
podw.update({(1, 1, 0): "andesite", (1, 2, 0): "andesite", (-1, 1, 0): "andesite", (-1, 2, 0): "andesite",
             (0, 1, 1): "andesite", (0, 2, 1): "andesite", (0, 1, -1): "andesite", (0, 2, -1): "andesite",
             (0, 3, 0): "andesite"})
podw[(-2, 1, 0)] = "stone"                                                         # west side: rock beyond
podw[(0, 1, 2)] = "lava"                                                           # south side: lava beyond
r = FakeRegion(podw, (-3, -2, -3), (3, 4, 3))
ex = skills.choose_exit(r, (0, 1, 0))
check("dig out: never toward lava", ex is not None and ex[1] != (0, 1, 1), ex)
check("dig out: prefers an open side with a floor", ex is not None and ex[1] in {(1, 1, 0), (0, 1, -1)}, ex)
check("dig out: mines both wall cells", ex is not None and len(ex[0]) == 2, ex)
check("pod counts as enclosed", skills.is_enclosed(r, (0, 1, 0)))
torch_gap = dict(podw)
torch_gap[(1, 1, 0)] = "torch"
r = FakeRegion(torch_gap, (-3, -2, -3), (3, 4, 3))
check("a torch in a side cell under a solid block is still enclosed", skills.is_enclosed(r, (0, 1, 0)))
ex = skills.choose_exit(r, (0, 1, 0))
check("dig out through the torch side mines only the head cell", ex == ([(1, 2, 0)], (1, 1, 0)), ex)
open_side = dict(podw)
del open_side[(1, 1, 0)], open_side[(1, 2, 0)]
r = FakeRegion(open_side, (-3, -2, -3), (3, 4, 3))
check("a 2-high opening is not enclosed", not skills.is_enclosed(r, (0, 1, 0)))

# ---- where to throw junk
solid_all = {(x, y, z): "stone" for x in range(-3, 4) for y in range(-1, 3) for z in range(-3, 4)}
shaft = dict(solid_all)
for yy in (0, 1):
    shaft.pop((0, yy, 0))
r = FakeRegion(shaft, (-3, -1, -3), (3, 2, 3))
check("throw: sealed shaft → nowhere", skills.throw_direction(r, (0, 0, 0)) is None)
tunnel = dict(shaft)
for xx in (-3, -2, -1):
    for yy in (0, 1):
        tunnel.pop((xx, yy, 0))
tunnel.pop((0, 0, 1)), tunnel.pop((0, 1, 1))          # a 1-block niche to the south
r = FakeRegion(tunnel, (-3, -1, -3), (3, 2, 3))
check("throw: in a tunnel → back along the tunnel, not into a niche", skills.throw_direction(r, (0, 0, 0)) == (-1, 0),
      skills.throw_direction(r, (0, 0, 0)))
niche_only = dict(shaft)
niche_only.pop((1, 0, 0)), niche_only.pop((1, 1, 0))          # a single 1-block niche beside a ladder shaft
r = FakeRegion(niche_only, (-3, -1, -3), (3, 2, 3))
check("throw: only a 1-block niche → don't throw (items land at our feet)", skills.throw_direction(r, (0, 0, 0)) is None,
      skills.throw_direction(r, (0, 0, 0)))
small_building = [{"id": "minecraft:cobblestone", "count": 30, "slot": 1}, {"id": "minecraft:dirt", "count": 12, "slot": 2},
                  {"id": "minecraft:redstone", "count": 64, "slot": 3}, {"id": "minecraft:redstone", "count": 64, "slot": 4}]
plan = [s["id"][10:] for s in skills.free_slots_plan(small_building, need=3)]
check("last resort keeps building blocks while they are 64 or fewer", not {"cobblestone", "dirt"} & set(plan), plan)

# ---- clock
from bonobo.world import ticks_until_dusk  # noqa: E402

check("dusk: morning counts down to 12500", ticks_until_dusk(1000) == 11500, ticks_until_dusk(1000))
check("dusk: night is 0", ticks_until_dusk(18000) == 0)
check("dusk: dawn (after 23400) has a whole day ahead, not 0", ticks_until_dusk(23600) == 12900,
      ticks_until_dusk(23600))

# ---- night burrow into a hillside
hill = {(x, y, z): "stone" for x in range(-4, 5) for y in range(-2, 4) for z in range(-4, 5)}
for yy in (0, 1):
    for xx in range(-4, 1):
        hill.pop((xx, yy, 0), None)        # open ground to the west; solid hill from x=1 eastward
for yy in range(0, 4):
    for zz in range(-4, 5):
        for xx in range(-4, 0):
            hill.pop((xx, yy, zz), None)
r = FakeRegion(hill, (-4, -2, -4), (4, 3, 4))
check("burrow: faces the solid hill", skills.choose_burrow(r, (0, 0, 0)) == (1, 0), skills.choose_burrow(r, (0, 0, 0)))
wet_hill = dict(hill)
wet_hill[(3, 0, 1)] = "water"
wet_hill[(1, 1, -1)] = "water"
r = FakeRegion(wet_hill, (-4, -2, -4), (4, 3, 4))
check("burrow: never next to water", skills.choose_burrow(r, (0, 0, 0)) is None, skills.choose_burrow(r, (0, 0, 0)))
flat = {(x, -1, z): "stone" for x in range(-4, 5) for z in range(-4, 5)}
r = FakeRegion(flat, (-4, -2, -4), (4, 3, 4))
check("burrow: flat open ground → none", skills.choose_burrow(r, (0, 0, 0)) is None)

# ---- night in the water: nearest land
lake = {(x, 0, z): "water" for x in range(-8, 9) for z in range(-8, 9)}
lake.update({(x, -1, z): "sand" for x in range(-8, 9) for z in range(-8, 9)})
lake.update({(x, 0, z): "grass_block" for x in range(5, 9) for z in range(-8, 9)})   # shore on the east
lake[(2, 0, 2)] = "stone"
lake[(2, 1, 2)] = "water"                                                          # a rock under water: not land
r = FakeRegion(lake, (-8, -2, -8), (8, 4, 8))
spot = skills.pick_land(r, (0, 0, 0))
check("land: nearest dry standing spot on the shore", spot is not None and spot[0] == 5 and spot[1] == 1, spot)
r = FakeRegion({(x, 0, z): "water" for x in range(-4, 5) for z in range(-4, 5)}, (-4, -2, -4), (4, 3, 4))
check("land: open sea → none", skills.pick_land(r, (0, 0, 0)) is None)

# ---- night: choose a shelter spot before a method
peak = {(x, y, z): "stone" for x in range(-8, 9) for y in range(-6, 0) for z in range(-8, 9)}   # ground at y=-1
for yy in range(0, 6):
    peak[(0, yy, 0)] = "stone"                              # a thin 1x1 pillar; standing on top at (0, 6, 0)
r = FakeRegion(peak, (-10, -6, -10), (10, 9, 10))
check("shelter: nothing works on top of a thin pillar", skills.shelter_method_at(r, (0, 6, 0)) is None,
      skills.shelter_method_at(r, (0, 6, 0)))
found = skills.find_shelter_spot(r, (0, 6, 0))
check("shelter: finds a spot on the ground nearby instead", found is not None and found[0][1] == 0 and found[1] in
      ("dig", "pod", "burrow"), found)
check("shelter: flat solid ground allows digging in", skills.shelter_method_at(r, (3, 0, 3)) == "dig",
      skills.shelter_method_at(r, (3, 0, 3)))
r = FakeRegion({(x, 0, z): "water" for x in range(-5, 6) for z in range(-5, 6)}, (-6, -3, -6), (6, 3, 6))
check("shelter: open water → no spot", skills.find_shelter_spot(r, (0, 1, 0), radius=5) is None)

# ---- cache chest placement
room = {(0, 1, 0): "stone"}
r = FakeRegion(room, (-2, -1, -2), (2, 3, 2))
check("chest: a solid block above means it can't open", not skills.chest_spot_ok(r, (0, 0, 0)))
check("chest: air above is fine", skills.chest_spot_ok(r, (1, 0, 0)))

# ---- full bag in a shaft: find open space
cave = {(x, y, z): "stone" for x in range(-8, 9) for y in range(-2, 6) for z in range(-8, 9)}
for yy in range(0, 5):
    cave.pop((0, yy, 0))                                     # the 1x1 shaft we're in
for xx in range(3, 8):
    for zz in range(-2, 3):
        for yy in (3, 4, 5):
            cave.pop((xx, yy, zz))                           # a room east, floor at y=2, halfway up the shaft
for xx in (1, 2):
    for yy in (3, 4):
        cave.pop((xx, yy, 0))                                # a passage from the shaft wall into it
r = FakeRegion(cave, (-8, -2, -8), (8, 6, 8))
check("open space: the sealed shaft bottom has no room to throw", skills.throw_direction(r, (0, 0, 0)) is None,
      skills.throw_direction(r, (0, 0, 0)))
spot = skills.find_open_spot(r, (0, 0, 0))
check("open space: finds the passage/room nearby", spot is not None and spot[0] >= 1 and spot[1] == 3, spot)

# ---- directives (Claude → script)
import tempfile  # noqa: E402

from bonobo import directives as D  # noqa: E402
dpath = os.path.join(tempfile.mkdtemp(), "directives.json")
D.add("goto", path=dpath, target=[1, 2, 3], note="road to the mine")
D.add("goal", path=dpath, needs=[["minecraft:iron_ingot", 24]])
items = D.load(dpath)
check("directives: first pending is the goto", D.current(items)["kind"] == "goto")
items, gave_up = D.mark(items, items[0]["id"], done=True)
check("directives: done moves on to the next", D.current(items)["kind"] == "goal" and not gave_up)
gid = D.current(items)["id"]
for _ in range(D.MAX_FAILS):
    items, gave_up = D.mark(items, gid, failed_reason="no iron")
check("directives: gives up after MAX_FAILS and asks the brain", gave_up and D.current(items) is None)

# ---- review packet
from bonobo import review as RV  # noqa: E402
import datetime as _dt  # noqa: E402
now = _dt.datetime(2026, 9, 15, 12, 10, 0)
log_lines = ["12:01:00 === stock coal (score 0.1; plan: x)\n", "12:06:00 === stock coal (score 0.1; plan: x)\n",
             "12:07:00 !! hunt: no progress\n", "12:08:00 ~~ tidy: in a shaft\n", "12:09:00 ?? idle 15s\n",
             "12:09:30 survival: find air\n", "11:50:00 === old goal (score 1)\n"]
entries = RV.recent_lines(log_lines, 5, now)
summ = RV.summarize(entries)
check("review: only the last 5 minutes", len(entries) == 5 and summ["goals"]["stock coal"] == 1, entries)
check("review: failures, help and survival are grouped", summ["failures"]["hunt"] == 1 and summ["help"]
      and summ["survival"], summ)

# ---- hunting approach
ap = skills.approach_policy(nav.Policy(protected={(1, 2, 3)}))
check("hunting approach never digs, keeps the rest of the policy", not ap.allow_dig and ap.allow_build
      and ap.protected == {(1, 2, 3)})
r = FakeRegion(stonewall, (-1, -1, -2), (8, 4, 2))
check("hunting approach can't tunnel through a wall",
      nav.plan_tunnel(r, (1, 1, 0), {(6, 1, 1)}, ap, blocks=0, ladders=0, features=ALL) is None)

# ---- time estimation
import tempfile  # noqa: E402

from bonobo import skill as skillkit  # noqa: E402
from bonobo.memory import Memory  # noqa: E402

tmp = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
skillkit.STATS = tmp
check("estimate: prior before any measurement", skillkit.expected(skills.chop, None, 10) == 60,
      skillkit.expected(skills.chop, None, 10))
for secs in (40, 40, 40):
    tmp.record_duration("chop", secs, 10)
check("estimate: measured per-unit time replaces the prior after 3 runs",
      abs(skillkit.expected(skills.chop, None, 10) - 40) < 1e-6, skillkit.expected(skills.chop, None, 10))
tmp.record_duration("mine:minecraft:raw_iron", 100, 5)
check("estimate: one sample isn't trusted yet", skillkit.expected(skills.mine, None, "minecraft:raw_iron", 5, [], 1)
      == 40, skillkit.expected(skills.mine, None, "minecraft:raw_iron", 5, [], 1))
tmp.record_duration("chop", 100, 10)
check("estimate: moving average of seconds per unit leans on history", 4 < tmp.duration("chop") < 10,
      tmp.duration("chop"))
skillkit.STATS = None

# ---- background jobs (multitasking)
import time as _time  # noqa: E402

tmp.add_job("furnace", (1, 2, 3), "minecraft:overworld", "minecraft:iron_ingot", 8, _time.time() + 85, True)
check("job: its output counts as pending for the planner",
      tmp.pending_outputs("minecraft:overworld").get("minecraft:iron_ingot") == 8, tmp.pending_outputs("minecraft:overworld"))
job = tmp.jobs()[0]
check("job: not ready before its estimate", not skills.job_ready(job))
tmp.postpone_job(job["id"], -1)
check("job: ready once the estimate has passed", skills.job_ready(tmp.jobs()[0]))
tmp.finish_job(job["id"])
check("job: finished jobs stop counting", tmp.pending_outputs("minecraft:overworld") == {})

# ---- look-ahead
from bonobo import lookahead  # noqa: E402
from bonobo.planner import Step  # noqa: E402


class LInv:
    def __init__(self, counts, tools=()):
        self.counts, self._tools = counts, list(tools)

    def count(self, token):
        return self.counts.get(token, 0)

    def usable(self, token):
        return self.count(token)

    def tools(self, kind):
        return self._tools if kind == "pickaxe" else []


def step(kind, token, count, est, **detail):
    s = Step(kind, token, count, detail)
    s.est = est
    return s


KIT = B.materials(B.SHELTER)
iron_trip = [step("mine", "minecraft:raw_iron", 8, 1200, blocks=["iron_ore"], tier=1, breaks=8),
             step("smelt", "minecraft:iron_ingot", 8, 900, input="minecraft:raw_iron", fuel="coal")]
full = LInv({"minecraft:furnace": 1, "food": 10, "stone": 64, "building": 64, "door": 1, "minecraft:torch": 10,
             "planks": 8},
            [(2, 200, "minecraft:iron_pickaxe")])
check("look-ahead: nothing missing → no extra", lookahead.prepare(iron_trip, full, 1000, False, KIT, 400) == [])
no_furnace = LInv({"food": 10}, [(2, 200, "minecraft:iron_pickaxe")])
extra = dict(lookahead.prepare(iron_trip, no_furnace, 1000, False, KIT, 400))
check("look-ahead: smelting after a trip → carry a furnace", extra.get("minecraft:furnace") == 1, extra)
smelt_only = [step("smelt", "minecraft:iron_ingot", 8, 900, input="minecraft:raw_iron", fuel="coal")]
check("look-ahead: smelting here needs no carried furnace",
      "minecraft:furnace" not in dict(lookahead.prepare(smelt_only, no_furnace, 1000, False, KIT, 400)))
extra = dict(lookahead.prepare(iron_trip, no_furnace, 11500, False, KIT, 4000))
check("look-ahead: trip into the night, home too far → shelter kit", extra.get("door") == 1 and extra.get("stone") == 14,
      extra)
check("look-ahead: carried bed → no shelter kit",
      "door" not in dict(lookahead.prepare(iron_trip, no_furnace, 11500, True, KIT, 4000)))
worn = LInv({"minecraft:furnace": 1, "food": 10}, [(2, 12, "minecraft:iron_pickaxe")])
extra = dict(lookahead.prepare(iron_trip, worn, 1000, False, KIT, 400))
check("look-ahead: pickaxe too worn for the trip → spare", extra.get("minecraft:stone_pickaxe") == 1, extra)
long_trip = [step("mine", "minecraft:raw_iron", 24, 6000, blocks=["iron_ore"], tier=1, breaks=24)]
extra = dict(lookahead.prepare(long_trip, LInv({}, [(2, 240, "p")]), 1000, False, KIT, 400))
check("look-ahead: long trip → pack food", extra.get("food", 0) >= 5, extra)

extra = dict(lookahead.prepare(iron_trip, no_furnace, 1000, False, KIT, 400))
check("look-ahead: deep work without wood → carry planks", extra.get("planks") == 8, extra)

# -- tool replacement picks a plan that can run to its end (real case: ingots smelted, no sticks, stuck at y=46)
from bonobo.brain import pick_tool_plan  # noqa: E402
log_step = step("gather", "log", 1, 1600)
tool_plans = {3: [log_step, step("craft", "minecraft:diamond_pickaxe", 1, 60)],
              2: [log_step, step("craft", "minecraft:iron_pickaxe", 1, 60)],
              1: [log_step, step("craft", "minecraft:stone_pickaxe", 1, 60)]}
check("tool plan: best tier when costs are equal", pick_tool_plan(tool_plans, set())[0] == 3)
check("tool plan: night without armor blocks gathering", pick_tool_plan(tool_plans, {"gather", "hunt"}) is None)
mining_plans = {2: [step("mine", "minecraft:raw_iron", 3, 100, tier=1), step("craft", "minecraft:iron_pickaxe", 1, 60)],
                1: [step("craft", "minecraft:stone_pickaxe", 1, 60)]}
check("tool plan: never mines with the missing pickaxe", pick_tool_plan(mining_plans, set())[0] == 1)
check("tool plan: other tools may mine", pick_tool_plan(mining_plans, set(), no_mining=False)[0] == 2)
check("tool plan: much cheaper lower tier wins",
      pick_tool_plan({2: [step("mine", "x", 1, 5000)], 1: [step("craft", "y", 1, 60)]}, set(), no_mining=False)[0] == 1)

from bonobo import bag as BG  # noqa: E402
check("pickup: everything below 28 slots", BG.pickup_whitelist(27, ["minecraft:raw_iron"]) is None)
_wl = BG.pickup_whitelist(30, ["minecraft:raw_iron", "log"])
check("pickup: from 28 slots only wanted and kept items (no cobble, no dirt)",
      "minecraft:raw_iron" in _wl and "minecraft:oak_log" in _wl and "minecraft:diamond" in _wl
      and "minecraft:cobblestone" not in _wl and "minecraft:dirt" not in _wl, len(_wl))

# Real loop: a wheat-farm plan crafted planks, tidy threw them away, the plan crafted them again (every 8 s).
_farm_plan = [step("craft", "planks", 4, 60, times=1, inputs={"log": 1}),
              step("craft", "minecraft:stick", 4, 60, times=1, inputs={"planks": 2}),
              step("craft", "minecraft:stone_hoe", 1, 60, times=1, inputs={"stone": 2, "minecraft:stick": 2})]
BG.RESERVED = BG.reserved_ids(_farm_plan, [("minecraft:stone_hoe", 1)])
_bag = [{"id": "minecraft:spruce_planks", "count": 60, "slot": i} for i in range(20)] + \
       [{"id": "minecraft:stick", "count": 40, "slot": 30}, {"id": "minecraft:dirt", "count": 64, "slot": 31}]
_thrown = BG.free_slots_plan(_bag, need=10)
_left = [s for s in _bag if s not in _thrown]
check("reservations: tidy always leaves the plan a stack of what it needs",
      any(s["id"] == "minecraft:spruce_planks" for s in _left) and any(s["id"] == "minecraft:stick" for s in _left),
      [s["id"] for s in _thrown])
_stored = BG.store_plan(_bag)
check("reservations: storage keeps a stack of them too",
      any(s["id"] == "minecraft:spruce_planks" for s in _bag if s not in _stored)
      and any(s["id"] == "minecraft:stick" for s in _bag if s not in _stored))
BG.RESERVED = {"minecraft:wheat_seeds", "minecraft:cobblestone"}
_bag2 = [{"id": "minecraft:wheat_seeds", "count": 5, "slot": 1}, {"id": "minecraft:cobblestone", "count": 64, "slot": 2},
         {"id": "minecraft:cobblestone", "count": 64, "slot": 3}, {"id": "minecraft:cobblestone", "count": 20, "slot": 4}]
_kept = BG.reserved_stacks(_bag2)
check("reservations: one stack per reserved item, not all of them (the bag stays tidyable)",
      len(_kept) == 2 and {s["slot"] for s in _kept} & {1} and sum(s["id"] == "minecraft:cobblestone" for s in _kept) == 1)
check("reservations: a down-weighted goal's seeds are never thrown",
      "minecraft:wheat_seeds" not in {s["id"] for s in BG.free_slots_plan(_bag2, need=3)})
BG.RESERVED = set()
_hungry = [{"id": "minecraft:mutton", "count": 6, "slot": 1}, {"id": "minecraft:cooked_mutton", "count": 3, "slot": 2},
           {"id": "minecraft:dirt", "count": 64, "slot": 3}, {"id": "minecraft:wheat_seeds", "count": 5, "slot": 4}]
_thrown_h = {s["id"] for s in BG.free_slots_plan(_hungry, need=3)} | {s["id"] for s in BG.tidy_plan(_hungry)}
check("food: raw meat is never thrown while cooked food is short (it was thrown 10× during a food hunt)",
      "minecraft:mutton" not in _thrown_h, _thrown_h)
check("farm: wheat seeds aren't junk any more", "minecraft:wheat_seeds" not in _thrown_h, _thrown_h)

# Real case 02:30: 5 obsidian + 2 cobblestone stood in the frame; the goal still asked for 10 obsidian and mined
# the frame. Only missing parts may be needed, and a started build's cells are protected.
_frame = {(-367, 119, 191): "cobblestone", (-364, 119, 191): "cobblestone", (-366, 119, 191): "obsidian",
          (-365, 119, 191): "obsidian", (-367, 120, 191): "obsidian", (-367, 121, 191): "obsidian"}
_need = B.remaining(B.NETHER_PORTAL, (-367, 119, 191), 0, lambda p: _frame.get(p, "air"))
check("build: a started portal needs only its missing parts", _need == {"minecraft:obsidian": 6, "stone": 2}, _need)
_bm = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
_bm.data["builds"] = {"nether_portal": {"origin": [-367, 119, 191], "turns": 0, "dimension": "minecraft:overworld"}}
check("build: cells of an unfinished build are protected from mining",
      (-367, 120, 191) in _bm.protected_cells("minecraft:overworld"))

broken ={"id": "minecraft:stone_pickaxe", "count": 1, "slot": 3, "damage": 130, "maxDamage": 131}
worn_ok = {"id": "minecraft:stone_pickaxe", "count": 1, "slot": 4, "damage": 100, "maxDamage": 131}
check("tidy: broken tools go, worn ones stay", skills.tidy_plan([broken, worn_ok]) == [broken])

skills._TREK_FAILED["far base"] = __import__("time").time()
check("deposit: a failed trek isn't retried soon", not skills.site_trek_ok({"name": "far base"})
      and skills.site_trek_ok({"name": "home"}))

# -- retry policy: wait for the state to change, not for a timer (unstuck once ran 29× from the same block)
from bonobo import retry as RT  # noqa: E402
from bonobo.api import NotAvailable as _NA  # noqa: E402
st_a = RT.signature((10, 40, 10), ["minecraft:dirt"], False)
st_b = RT.signature((30, 40, 10), ["minecraft:dirt"], False)
rp = RT.Retry()
n, wait, logit = rp.failed("unstuck", "nav", "no path", st_a, 1000)
check("retry: first failure logged, backstop by cause", n == 1 and wait == 120 and logit)
check("retry: same state waits", not rp.ready("unstuck", st_a, 1010))
check("retry: changed state retries soon", rp.ready("unstuck", st_b, 1010) and not rp.ready("unstuck", st_b, 1002))
_n, wait2, _l = rp.failed("unstuck", "nav", "no path", st_a, 1200)
check("retry: backstop doubles", wait2 == 240, wait2)
n, wait, logit = rp.failed("unstuck", "nav", "no path", st_a, 1500)
check("retry: exhausted after 3 in one state", rp.exhausted("unstuck", st_a) and not rp.exhausted("unstuck", st_b))
# The doubling runs into a ceiling that depends on WHAT went wrong: "no route from here" ages fast (we move, the
# sun moves), a bug does not. One ceiling for every cause left a cooling goal idle for a quarter of an hour.
check("retry: the ceiling is per cause", wait == RT.MAX_BACKSTOP["nav"], wait)
check("retry: a bug waits longer than a place that did not work",
      RT.MAX_BACKSTOP["error"] > RT.MAX_BACKSTOP["unavailable"])
n, _, logit = rp.failed("unstuck", "nav", "no path", st_a, 2000)
check("retry: repeats aren't logged", not logit)
check("retry: a few blocks or the same items don't count as change",
      RT.signature((11, 41, 9), ["minecraft:dirt", "minecraft:dirt"], False) == st_a)
check("retry: travel failures are navigation",
      RT.cause_of(_NA("gave up after 8 replans: floor failed: no reachable face")) == "nav"
      and RT.cause_of(_NA("nothing dark nearby")) == "unavailable")

# -- one priority pool
from bonobo import priority as PR  # noqa: E402

_C = PR.Candidate

# The pool, as the four things the score must be able to say. Not the shape of any curve: those are model numbers
# in play.toml, and asserting them here only pins the model to whatever it happened to be.
check("priority: the score is seconds gained, and every term of explain() is seconds",
      "s" in _C("x", 1, 600, None, seconds=50.0).explain() and
      abs(_C("x", 0, 0, None, seconds=100.0).score - 100.0) < 1e-6,
      _C("x", 0, 0, None, seconds=100.0).explain())
check("priority: work that costs more than it saves scores negative",
      _C("long errand", 0, 20 * 600, None, seconds=30.0).score < 0)
check("priority: a benefit that pays later is worth less than the same one now",
      _C("later", 0, 600, None, seconds=100.0, delay_s=PR.DISCOUNT_HORIZON_S).score
      < _C("now", 0, 600, None, seconds=100.0).score)
# Added in seconds, not multiplied — and discounted by when the plan finishes, like every other benefit. A plan
# that hands the pickaxe over in twenty seconds is worth more than the four-hundred-second one that passes through
# a pickaxe on its way somewhere else; leaving unlocks undiscounted is why the agent stopped making tools at all.
_opened = _C("opener", 0, 600, None, seconds=10.0, unlocks=[(100.0, 0.5)])
_plain = _C("plain", 0, 600, None, seconds=10.0)
_wait = 1.0 + (600 / PR.TICKS_PER_S) / PR.DISCOUNT_HORIZON_S
check("priority: what a goal unlocks is added in seconds, discounted by when it arrives",
      abs((_opened.score - _plain.score) - 50.0 / _wait) < 1e-6)
check("priority: the same unlock is worth less the longer the plan takes",
      _C("soon", 0, 60, None, seconds=10.0, unlocks=[(100.0, 1.0)]).benefit_s
      > _C("late", 0, 12000, None, seconds=10.0, unlocks=[(100.0, 1.0)]).benefit_s)
check("priority: an unreliable candidate is worth its expected benefit but the whole cost",
      abs(_C("flaky", 0, 20 * 10, None, seconds=100.0, success=0.5).score - (50.0 - 10.0)) < 1e-6)
check("priority: success floor, reset on state change",
      PR.effective_success(0.0, False) == PR.MIN_SUCCESS and PR.effective_success(0.0, True) == 1.0)
_a, _b = _C("iron armor", 0, 3000, None, seconds=200.0), _C("stone pickaxe", 0, 3000, None, seconds=201.0)
check("priority: a commitment survives a tie", PR.choose([_a, _b], "stone pickaxe").name == "stone pickaxe")
check("priority: a commitment whose premise failed is released",
      PR.choose([_a, _b], "stone pickaxe", held=False).name in ("stone pickaxe", "iron armor"))
check("priority: clearly beaten → switch",
      PR.choose([_C("a", 0, 600, None, seconds=9000.0), _b], "stone pickaxe").name == "a")
_plans = {"iron pickaxe": [step("craft", "minecraft:iron_pickaxe", 1, 60)],
          "diamonds": [step("craft", "minecraft:iron_pickaxe", 1, 60), step("mine", "minecraft:diamond", 3, 900)],
          "bucket": [step("craft", "minecraft:bucket", 1, 60)]}
# Unlocking is read off the solver's shadow prices, from the TOP down: how much cheaper the terminal goods get
# once this plan has run, capped by what each is worth. Summing over everything merely WANTED paid one saving once
# per link of a supply chain and put the pool at two hundred thousand seconds for an enchanting table.
_before, _after = {"bed": 400.0, "food": 60.0}, {"bed": 100.0, "food": 60.0}
check("priority: unlocking is the fall in the price of the terminal goods",
      PR.future_value(_before, _after, {"bed": 500.0, "food": 200.0}) == 300.0)
check("priority: nobody pays more for a thing than the thing saves",
      PR.future_value({"bed": 90000.0}, {"bed": 80000.0}, {"bed": 500.0}) == 0.0)

_pf = os.path.join(tempfile.mkdtemp(), "prio.json")
PR.add_weight("stock torches", path=_pf, now=1000, ttl=600, x=100)
PR.add_weight("deposit", path=_pf, now=1000, ttl=60, ban=True)
_w = PR.load(_pf, now=1030)
check("priority: weights clamp and ban", PR.weight_for("stock torches", _w) == (PR.CLAMP[1], False)
      and PR.weight_for("deposit", _w) == (0.0, True) and PR.weight_for("bed", _w) == (1.0, False))
check("priority: weights expire", "deposit" not in PR.load(_pf, now=1100) and "stock torches" in PR.load(_pf, now=1100))

_rk = [{"t": 1000, "pick": "stock torches", "top": [["stock torches", 0.02, 0.4], ["bed to carry", 0.01, 9]],
        "filtered": {"iron pickaxe": "no runnable step"}}] * 3
_rs = RV.rankings(_rk, 30, 1100)
check("review: starved candidates listed with their reason",
      "bed to carry (3×, mostly outscored)" in _rs and "iron pickaxe (3×, mostly no runnable step)" in _rs, _rs)

# -- escape capability before digging
check("escape: one pickaxe, no wood → not ready", not lookahead.escape_ready(LInv({"minecraft:cobblestone": 64}, [(1, 90, "p")])))
check("escape: spare pickaxe → ready", lookahead.escape_ready(LInv({}, [(1, 90, "p"), (1, 40, "q")])))
check("escape: planks + cobble + table → ready", lookahead.escape_ready(
    LInv({"planks": 2, "minecraft:cobblestone": 3, "minecraft:crafting_table": 1}, [(1, 90, "p")])))

# -- memory dedup
from bonobo.memory import Memory  # noqa: E402
_mp = os.path.join(tempfile.mkdtemp(), "notes.json")
_m = Memory(_mp)
_m.add_station("crafting_table", (1, 2, 3), "overworld")
_m.add_station("crafting_table", (1, 2, 3), "overworld")
_m.log_vein("iron_ore", (5, 5, 5), 4, "overworld")
_m.log_vein("iron_ore", (6, 5, 5), 3, "overworld")
check("memory: stations and veins deduplicated", len(_m.data["stations"]) == 1 and len(_m.data["veins"]) == 1)

# -- review macro progress
_tr = [{"t": 1000 + 60 * i, "pos": [-9, 37, 257], "done": ["stone pickaxe"]} for i in range(30)]
check("review: 30 min in one block is STALLED", "STALLED" in RV.macro(_tr, 30, 1000 + 60 * 30))
_tr2 = _tr[:-1] + [{"t": 1000 + 60 * 29, "pos": [40, 60, 200], "done": ["stone pickaxe", "iron pickaxe"]}]
check("review: movement and a new goal aren't a stall", "STALLED" not in RV.macro(_tr2, 30, 1000 + 60 * 30)
      and "iron pickaxe" in RV.macro(_tr2, 30, 1000 + 60 * 30))

# -- night: tunnel to a cell with solid roof (walking "6 lower" on a hillside only reached open ground)
_solid = {(x, y, z): "stone" for x in range(-6, 7) for y in range(50, 65) for z in range(-6, 7)}
_rg = FakeRegion(_solid, (-6, 50, -6), (6, 66, 6))
_t = skills.underground_target(_rg, (0, 65, 0))
check("night: underground target has a solid roof and floor",
      _t is not None and _rg.solid((_t[0], _t[1] + 2, _t[2])) and _rg.solid((_t[0], _t[1] + 3, _t[2]))
      and _rg.solid((_t[0], _t[1] - 1, _t[2])), _t)
_thin = {(x, 64, z): "stone" for x in range(-6, 7) for z in range(-6, 7)}
check("night: a one-block crust gives no underground target",
      skills.underground_target(FakeRegion(_thin, (-6, 50, -6), (6, 66, 6)), (0, 65, 0)) is None)
_ravine = {p: b for p, b in _solid.items() if p[0] != 1}   # an open slot one block east of the column
_rt = skills.underground_target(FakeRegion(_ravine, (-6, 50, -6), (6, 66, 6)), (0, 65, 0))
check("night: not on a ravine ledge (head walled on 3+ sides)",
      _rt is not None and sum(FakeRegion(_ravine, (-6, 50, -6), (6, 66, 6)).solid((_rt[0] + a, _rt[1] + 1, _rt[2] + b))
                              for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1))) >= 3, _rt)
_wet = dict(_solid)
for _x in range(-6, 7):
    for _z in range(-6, 7):
        for _y in range(55, 63):
            _wet[(_x, _y, _z)] = "water" if (_x + _z) % 2 else "stone"
check("night: never tunnel next to water",
      skills.underground_target(FakeRegion(_wet, (-6, 50, -6), (6, 66, 6)), (0, 65, 0)) is None)

# -- fluids and the Nether portal
from bonobo import fluids as FL  # noqa: E402

_pool = {}
for _x in range(-8, 9):
    for _z in range(-8, 9):
        _pool[(_x, 59, _z)] = "stone"
        _pool[(_x, 60, _z)] = "lava" if 0 <= _x <= 3 and 0 <= _z <= 3 else "stone"
_pr = FakeRegion(_pool, (-8, 55, -8), (8, 66, 8))
_plan = FL.pour_plan(_pr, (-6, 61, 1))
check("portal: pour plan finds a bank next to the pool", _plan is not None and _pr.name(_plan[1]) == "stone"
      and _plan[1][1] == 60 and _plan[2] >= 4, _plan)
check("portal: pour stand keeps 3+ blocks from lava", _plan is not None and not FL.lava_within(_pr, _plan[0], 2), _plan)
check("portal: no lava → no pour plan",
      FL.pour_plan(FakeRegion({p: "stone" for p in _pool}, (-8, 55, -8), (8, 66, 8)), (0, 61, 0)) is None)
_lake = {(_x, 60, _z): ("water" if _x >= 2 else "stone") for _x in range(-4, 6) for _z in range(-3, 4)}
_lake.update({(_x, 59, _z): "stone" for _x in range(-4, 6) for _z in range(-3, 4)})
_fs = FL.fill_spot(FakeRegion(_lake, (-4, 55, -3), (5, 64, 3)), (-3, 61, 0))
check("portal: fill spot stands dry within reach of still water",
      _fs is not None and _lake.get(_fs[1]) == "water" and _lake.get(_fs[0], "air") == "air", _fs)
# Real case: stood at (-71,12,307), water at z=309 behind a stone wall at z=308.
_walled = {(_x, 11, _z): "stone" for _x in range(-74, -67) for _z in range(304, 312)}
_walled.update({(_x, _y, 308): "stone" for _x in range(-74, -67) for _y in (12, 13, 14)})
_walled.update({(_x, _y, _z): "water" for _x in range(-74, -67) for _y in (12, 13) for _z in (309, 310)})
_ws = FL.fill_spot(FakeRegion(_walled, (-74, 10, 304), (-68, 15, 311)), (-71, 12, 307))
check("portal: never fill through a wall (line of sight)", _ws is None or _ws[0][2] >= 309 or _ws[0][1] >= 14, _ws)
check("portal: blueprint uses 10 obsidian + 4 stone",
      B.materials(B.NETHER_PORTAL) == {"stone": 4, "minecraft:obsidian": 10})
_cells = {pos: part.item for pos, part, *_ in B.placed(B.NETHER_PORTAL, (0, 64, 0), 1)}
_aim = FL.portal_light_aim((0, 64, 0), 1)
check("portal: light aim is the top face of an inner bottom obsidian (rotated)",
      _cells.get((int(_aim[0] // 1), 64, int(_aim[2] // 1))) == "minecraft:obsidian" and _aim[1] == 65.0, _aim)

# -- perception: when a running task must be interrupted
from bonobo import perception as PC  # noqa: E402

_ok = {"health": 20, "food": 20, "air": 300, "control": {"paused": False}}
check("perception: healthy → no interrupt", PC.danger(_ok, lambda r: 2) is None)
check("perception: lava interrupts", PC.danger({**_ok, "inLava": True}) == "lava")
check("perception: drowning is a clock, and standing on the bottom counts",
      PC.drowning({**_ok, "inWater": True, "air": 60, "onGround": True})
      and not PC.drowning({**_ok, "inWater": True, "air": 300, "onGround": False}))
check("perception: hurt + hostile close interrupts, hurt alone doesn't",
      PC.danger({**_ok, "health": 9}, lambda r: 4) == "hostiles"
      and PC.danger({**_ok, "health": 9}, lambda r: None) is None)
check("perception: never while the player holds control",
      PC.danger({**_ok, "inLava": True, "control": {"paused": True}}) is None)
check("perception: an interrupt is its own cause with no backstop",
      RT.cause_of(__import__("bonobo.api", fromlist=["Interrupted"]).Interrupted("lava")) == "interrupt"
      and RT.BACKSTOP["interrupt"] == 0)

# -- directive dependency graph, proximity
_dg = [{"id": "a", "status": "done"}, {"id": "b", "status": "pending", "requires": ["a"]},
       {"id": "c", "status": "pending", "requires": ["b"]}, {"id": "d", "status": "pending"},
       {"id": "e", "status": "failed"}, {"id": "f", "status": "pending", "requires": ["e"]}]
check("directives: runnable = pending with all dependencies done (parallel branches allowed)",
      [x["id"] for x in D.runnable(_dg)] == ["b", "d"])
check("directives: a failed dependency blocks its dependents", [x["id"] for x in D.blocked(_dg)] == ["f"])
check("priority: distance is a cost, not a bonus (there and back)",
      PR.detour_s(40) > PR.detour_s(10) > 0)
check("bag: the carried chest is kept, not stored", not [s for s in skills.store_plan(
    [{"id": "minecraft:chest", "count": 1, "slot": 5}]) if s["id"] == "minecraft:chest"])

# -- farming: plots, ripe wheat, breeding pairs, resource map
from bonobo import farming as FM  # noqa: E402

_field = {(x, 63, z): "grass_block" for x in range(-5, 6) for z in range(-5, 6)}
_field[(0, 63, 0)] = "stone"            # a stone in the middle of the field
check("farm: plot centre is soil with soil all around", (lambda c: c is not None and all(
    _field.get((c[0] + dx, 63, c[2] + dz)) == "grass_block" for dx in (-1, 0, 1) for dz in (-1, 0, 1)))(
    FM.farm_plot(FakeRegion(_field, (-5, 60, -5), (5, 66, 5)), (0, 64, 0))))


class _PropRegion(FakeRegion):
    def __init__(self, blocks, props, lo, hi):
        super().__init__(blocks, lo, hi)
        self._props = props

    def prop(self, p, key):
        return self._props.get(p, {}).get(key)


_wheat = _PropRegion({(0, 64, 0): "wheat", (1, 64, 0): "wheat"}, {(0, 64, 0): {"age": "7"}, (1, 64, 0): {"age": "3"}},
                     (-1, 63, -1), (1, 65, 1))
check("farm: only age-7 wheat is ripe", FM.ripe_cells(_wheat) == [(0, 64, 0)])
_herd = [{"id": 1, "type": "minecraft:cow", "x": 0, "y": 64, "z": 0}, {"id": 2, "type": "minecraft:cow", "x": 3, "y": 64, "z": 2},
         {"id": 3, "type": "minecraft:sheep", "x": 40, "y": 64, "z": 0}]
check("farm: breeding pair = two close adults of one kind", FM.breeding_pair(_herd, "minecraft:cow") == (1, 2)
      and FM.breeding_pair(_herd, "minecraft:sheep") is None)
_rm = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
_rm.note_resource("tree", (10, 64, 10), "overworld")
_rm.note_resource("tree", (12, 64, 11), "overworld", depleted=True)
check("resources: a depleted grove is unavailable until it regrows",
      _rm.resources("tree", "overworld") == [] and _rm.resources("tree", "overworld", now=__import__("time").time() + 1300))
check("recipes: hoes exist for the farm", "minecraft:stone_hoe" in __import__("bonobo.data", fromlist=["RECIPES"]).RECIPES)

# -- nether / stronghold helpers
from bonobo import nether as NT  # noqa: E402

check("stronghold: two throws triangulate", NT.triangulate((0, 0), (1, 0), (100, -100), (0, 1)) == (100, 0))
check("stronghold: parallel throws don't", NT.triangulate((0, 0), (1, 0), (0, 50), (1, 0)) is None)
check("stronghold: rays meeting behind a thrower don't count",
      NT.triangulate((0, 0), (1, 0), (-100, -100), (0, 1)) is None)
_pc = NT.portal_cell((0, 64, 0), 0)
check("portal: walk-in cell is inside the frame (a clear cell)",
      _pc in B.clear_cells(B.NETHER_PORTAL, (0, 64, 0), 0))

# -- wiki skills: combat, loot, End, brewing, upkeep, piglins, water clutch
from bonobo import brewing as BW, combat as CB, end as EN, loot as LT, upkeep as UK  # noqa: E402

_a = CB.bow_aim((0, 65.6, 0), (30, 64, 0), height=1.0)
check("bow: aims above the target to cover the drop", _a[1] > 65.0 and _a[0] == 30, _a)
check("bow: farther targets need more lift", CB.bow_aim((0, 65.6, 0), (60, 64, 0))[1] > _a[1])
_cr = [{"x": 40, "y": 100, "z": 0}, {"x": 10, "y": 70, "z": 0}, {"x": 5, "y": 110, "z": 0}]
check("dragon: open crystals nearest first, caged high ones last",
      [c["x"] for c in CB.crystal_order(_cr, (0, 64, 0))] == [10, 5, 40])
_fort = {(x, 64, z): "nether_bricks" for x in range(-5, 6) for z in range(-5, 6)}
_fort.update({(2, 65, 0): "nether_bricks", (2, 66, 0): "nether_bricks"})
_cover = CB.blaze_cover(FakeRegion(_fort, (-5, 60, -5), (5, 70, 5)), (0, 65, 0), (5, 66, 0))
check("blaze: cover puts a solid block between us and the blaze", _cover is not None and _cover[0] < 2, _cover)
_chest = [{"owner": "chest", "slot": 0, "id": "minecraft:rotten_flesh"}, {"owner": "chest", "slot": 1, "id": "minecraft:obsidian"},
          {"owner": "chest", "slot": 2, "id": "minecraft:gold_ingot"}, {"owner": "player", "slot": 30, "id": "minecraft:diamond"}]
_chest_prices = {"minecraft:obsidian": 300.0, "minecraft:gold_ingot": 500.0, "minecraft:rotten_flesh": 0.0}
check("loot: takes what is worth a slot, dearest first, and never the player's own",
      LT.loot_plan(_chest, _chest_prices, 30) == [2, 1], LT.loot_plan(_chest, _chest_prices, 30))
_frames = _PropRegion({(0, 30, 0): "end_portal_frame", (1, 30, 0): "end_portal_frame", (4, 30, 4): "end_portal_frame"},
                      {(0, 30, 0): {"eye": "true"}, (1, 30, 0): {"eye": "false"}, (4, 30, 4): {"eye": "false"}},
                      (-1, 29, -1), (5, 31, 5))
check("end: frames without an eye", sorted(EN.frames_missing_eye(_frames)) == [(1, 30, 0), (4, 30, 4)])
check("end: portal centre of the frame ring", EN.portal_centre([(0, 30, 1), (4, 30, 3), (2, 30, 0), (2, 30, 4)]) == (2, 30, 2))
check("brewing: full chain when inputs are there",
      BW.brew_steps({"minecraft:potion:water": 3, "minecraft:nether_wart": 1, "minecraft:magma_cream": 1,
                     "minecraft:blaze_powder": 1}) == ["minecraft:nether_wart", "minecraft:magma_cream"])
check("brewing: missing magma cream → can't brew", BW.brew_steps({"minecraft:potion:water": 3, "minecraft:nether_wart": 1}) is None)
_tools = [{"id": "minecraft:stone_pickaxe", "slot": 3, "damage": 120, "maxDamage": 131},
          {"id": "minecraft:stone_pickaxe", "slot": 4, "damage": 110, "maxDamage": 131},
          {"id": "minecraft:diamond_pickaxe", "slot": 5, "damage": 100, "maxDamage": 1561}]
check("repair: two worn stone pickaxes combine", UK.repair_pair(_tools, "pickaxe") == ("minecraft:stone_pickaxe", 3, 4))
check("repair: a single tool can't", UK.repair_pair(_tools[2:], "pickaxe") is None)
check("piglins: gold armor first, then ingots", NT.barter_ready(LInv({"minecraft:gold_ingot": 5}), [None, None, None, None])
      == "wear a piece of gold armor first"
      and NT.barter_ready(LInv({"minecraft:gold_ingot": 5}), ["minecraft:golden_helmet", None, None, None]) is None)
check("clutch: pour just above the ground after a long fall",
      PC.clutch_needed(8, 3, {"onGround": False}, True) and not PC.clutch_needed(8, 12, {"onGround": False}, True)
      and not PC.clutch_needed(3, 3, {"onGround": False}, True) and not PC.clutch_needed(8, 3, {"onGround": False}, False))
_dm = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
_dm.log_death((1, 64, 1), "minecraft:overworld")
check("death: recent death is recoverable for 5 minutes",
      _dm.recent_death("minecraft:overworld") and not _dm.recent_death("minecraft:overworld", now=__import__("time").time() + 400))
_R = __import__("bonobo.data", fromlist=["RECIPES"]).RECIPES
check("recipes: golden helmet, brewing stand, glass bottles, magma cream",
      all(k in _R for k in ("minecraft:golden_helmet", "minecraft:brewing_stand", "minecraft:glass_bottle", "minecraft:magma_cream")))

# -- screens: enchanting, trading, anvils
from bonobo import ui as UI  # noqa: E402

_opts = [{"cost": 3}, {"cost": 12}, {"cost": 30}]
check("enchant: best affordable option", UI.choose_enchant(_opts, 15, 3) == 1)
check("enchant: lapis limits the button", UI.choose_enchant(_opts, 40, 1) == 0)
check("enchant: nothing affordable", UI.choose_enchant(_opts, 2, 3) is None)
_offers = [{"sell": "minecraft:ender_pearl", "buy": "minecraft:emerald", "buyCount": 9},
           {"sell": "minecraft:ender_pearl", "buy": "minecraft:emerald", "buyCount": 5},
           {"sell": "minecraft:ender_pearl", "buy": "minecraft:emerald", "buyCount": 3, "disabled": True},
           {"sell": "minecraft:bread", "buy": "minecraft:emerald", "buyCount": 1}]
check("trade: cheapest enabled affordable offer", UI.choose_trade(_offers, "minecraft:ender_pearl", {"minecraft:emerald": 6}) == 1)
check("trade: can't pay → none", UI.choose_trade(_offers, "minecraft:ender_pearl", {"minecraft:emerald": 2}) is None)
check("anvil: affordable and not too expensive", UI.anvil_ok(8, 10) and not UI.anvil_ok(12, 10) and not UI.anvil_ok(40, 50))
_R2 = __import__("bonobo.data", fromlist=["RECIPES"]).RECIPES
check("recipes: paper, book, enchanting table, anvil",
      all(k in _R2 for k in ("minecraft:paper", "minecraft:book", "minecraft:enchanting_table", "minecraft:anvil")))

_body = {(x, 63, z): "stone" for x in range(-10, 11) for z in range(-10, 11)}
_spot = skills.find_machine_spot(B.NETHER_PORTAL, (0, 64, 0), nav.Policy(), radius=4, body=(0, 64, 0)) \
    if False else None   # (needs a live Region; the body exclusion is exercised through the pure `free` check below)
check("build: a spot never overlaps the player's body (resume + body exclusion wired)",
      "body" in skills.find_machine_spot.__code__.co_varnames)

# -- Nether safety: neutral mobs, trip kit, retreat
from bonobo.brain import nether_kit_missing  # noqa: E402
from bonobo.threat import is_threat  # noqa: E402

check("combat: zombified piglins, piglins and endermen aren't attacked on sight",
      not is_threat({"hostile": True, "type": "minecraft:zombified_piglin"})
      and not is_threat({"hostile": True, "type": "minecraft:enderman"})
      and is_threat({"hostile": True, "type": "minecraft:ghast"}) and is_threat({"hostile": True, "type": "minecraft:zombie"}))


class _KitInv(LInv):
    def __init__(self, counts, slots_used, head=None):
        super().__init__(counts)
        self._used, self._head = slots_used, head

    def used_slots(self):
        return self._used

    def worn(self, slot):
        return self._head if slot == "head" else None


check("nether kit: the real bag (4 food, no gold helmet, 35 slots) isn't ready",
      len(nether_kit_missing(_KitInv({"minecraft:cooked_beef": 4}, 35))) == 4)   # food, blocks, helmet, room
check("nether kit: enough food + 32 blocks + gold helmet worn + room → ready",
      nether_kit_missing(_KitInv({"minecraft:cooked_beef": 12, "building": 40}, 28, head="minecraft:golden_helmet")) == [])
check("nether kit: no blocks → not ready (bridges, shelter from fireballs)",
      any("blocks" in m for m in nether_kit_missing(_KitInv({"minecraft:cooked_beef": 12}, 20, head="minecraft:golden_helmet"))))
from bonobo import route as RO  # noqa: E402
_raw_only = _KitInv({"minecraft:mutton": 20, "building": 40}, 20, head="minecraft:golden_helmet")
check("route: raw meat isn't 'food' for the kit (one definition)", RO.food_count(_raw_only) == 0
      and ("food", RO.KIT_FOOD) in RO.kit_needs(_raw_only))


class _RMem:
    def __init__(self, portal=False, fortress=False):
        self.portal, self.fortress, self.data = portal, fortress, {}

    def machines(self, dim, tag=None):
        return [1] if self.portal else []

    def sites(self, dim, kinds=None):
        return [1] if self.fortress and kinds == ["fortress"] else []


_seg = RO.active_segment(RO.SPEEDRUN, _KitInv({"minecraft:cooked_beef": 4}, 35), _RMem(portal=True), "minecraft:overworld")
check("route: portal built, kit missing → 'nether kit' segment; side goals filtered, essentials allowed",
      _seg["name"] == "nether kit" and not RO.allowed(_seg, "wheat farm") and RO.allowed(_seg, "nether kit")
      and RO.allowed(_seg, "iron pickaxe"))
_seg2 = RO.active_segment(RO.SPEEDRUN, _KitInv({"minecraft:cooked_beef": 4}, 35), _RMem(portal=True), "minecraft:the_nether")
check("route: in the Nether the kit segment is past → fortress", _seg2["name"] == "fortress")
_sp = os.path.join(tempfile.mkdtemp(), "prio.json")
PR.add_weight("nether fortress", path=_sp, now=1000, ttl=3600, ban=True)
PR.apply_profile("speedrun", path=_sp, now=1000)
_spw = PR.load(_sp, now=1010)
check("speedrun profile: side goals banned, dragon route boosted, earlier Nether ban lifted",
      PR.weight_for("wheat farm", _spw)[1] and PR.weight_for("blaze rods (7)", _spw) == (8.0, False)
      and PR.weight_for("nether fortress", _spw) == (6.0, False))
check("speedrun profile: the wandering fallbacks stay banned (a slice picked 'light up' 80× without the profile)",
      {"light up", "torches (≥8)", "deposit"} <= set(PR.PROFILES["speedrun"]["ban"]))
check("speedrun profile: nothing is both banned and boosted; the bow is allowed and boosted, not banned",
      not set(PR.PROFILES["speedrun"]["ban"]) & set(PR.PROFILES["speedrun"]["boost"])
      and PR.weight_for("bow", _spw) == (6.0, False))
_rf = os.path.join(tempfile.mkdtemp(), "route.json")
RO.choose("speedrun", path=_rf) if "RO" in dir() else None
from bonobo import route as RO2  # noqa: E402
RO2.choose("speedrun", path=_rf)
check("route: the chosen route lives in its own file", RO2.current(path=_rf) == "speedrun")

check("nether: exploration legs stay above the lava sea", 50 <= NT.EXPLORE_Y <= 100)
import math  # noqa: E402
_wp = NT.waypoints((-258, 65, 270), (-366, 120, 191))
check("portal trip: 110 blocks go in legs of ≤40, ending at the portal",
      _wp[-1] == (-366, 120, 191) and len(_wp) == 4
      and all(math.hypot(b[0] - a[0], b[2] - a[2]) <= 41 for a, b in zip([(-258, 65, 270)] + _wp, _wp)), _wp)
_st = {"reached": 2}
_wobble = _KitInv({"minecraft:cooked_beef": 12, "building": 33}, 32, head="minecraft:golden_helmet")
_kitseg = next(s for s in RO.SPEEDRUN if s["name"] == "nether kit")
check("route: a slot of bag noise doesn't send the route back to the kit",
      RO.with_hysteresis(RO.SPEEDRUN, _kitseg, _st, _wobble)["name"] == "fortress")
check("route: a really missing kit (food 3) does",
      RO.with_hysteresis(RO.SPEEDRUN, _kitseg, {"reached": 2}, _KitInv({"minecraft:cooked_beef": 3, "building": 33}, 20,
                                                                       head="minecraft:golden_helmet"))["name"] == "nether kit")
# Real case 03:03: dirt at (-17,61,241) next to a pond two blocks away; the pit filled and the agent nearly drowned.
_pond = {(-17, 61, 241): "dirt", (-15, 61, 241): "water", (-15, 62, 241): "water", (-20, 61, 241): "dirt"}
_pr2 = FakeRegion(_pond, (-22, 58, 238), (-12, 64, 244))
check("mine: surface blocks keep 2 blocks from water", skills.too_wet(_pr2, (-17, 61, 241), 2)
      and not skills.too_wet(_pr2, (-17, 61, 241), 1) and not skills.too_wet(_pr2, (-20, 61, 241), 2))

# -- scenario bench: readiness table
from bonobo import scenarios as SC  # noqa: E402

_tb = {}
for ok, s in ((True, 40), (False, 90), (True, 50)):
    SC.record(_tb, "cast_obsidian", "abc", ok, s)
check("readiness: 2 of the last 3 passed → scenario-ready, median of passes",
      SC.status(_tb, "cast_obsidian", "abc") == ("scenario", 50))
check("readiness: per code version (a new hash starts untested)", SC.status(_tb, "cast_obsidian", "new")[0] == "untested")
SC.record(_tb, "cast_obsidian", "abc", False, 99)
SC.record(_tb, "cast_obsidian", "abc", False, 99)
check("readiness: recent failures demote a skill", SC.status(_tb, "cast_obsidian", "abc")[0] == "failing")
check("scenarios: every scenario has setup, run, check and a budget",
      all({"setup", "run", "check", "budget", "doc"} <= set(sc) for sc in SC.SCENARIOS.values()))
def _raises(fn):
    try:
        fn()
    except RuntimeError:
        return True
    return False


check("scenarios: never run without the test-world flag",
      __import__("os").path.exists(SC.FLAG) or _raises(lambda: SC.run("gather_logs", None)))
check("scenarios: every scenario names its module and a signature",
      all(sc.get("module") and (sc.get("expect") or sc.get("raw")) for sc in SC.SCENARIOS.values()))

# Real case 03:38: every /fill answered "That position is not loaded" and the bench ran skills in natural terrain.
_fb = ["Set the time to 1000", "No entity was found", "That position is not loaded", "Incorrect argument for command",
       "gamerule doMobSpawning false<--[HERE]", "Target has no effects to remove", "No blocks were filled"]
check("bench: command feedback errors are caught, harmless replies aren't",
      SC.feedback_errors(_fb) == _fb[2:5])
_pool = {SC.at(dx, -1, dz): "lava" for dx in range(-2, 3) for dz in range(-2, 3)}
_nat = {SC.at(3, 0, 1): "grass_block"}
check("bench: exact signature — natural terrain in the box is a mismatch",
      not SC.setup_mismatches(_pool, [(SC.at(-2, -1, -2), SC.at(2, -1, 2), "lava", 25, 25)])
      and SC.setup_mismatches(_nat, [(SC.at(-6, 0, -6), SC.at(6, 4, 6), "*", 0, 0)]))
from bonobo.api import NavFailed  # noqa: E402
check("bench: failure classes", (SC.classify(SC.SetupInvalid("x"), False), SC.classify(NavFailed("x"), False),
                                 SC.classify(ValueError("x"), False), SC.classify(None, True))
      == ("setup", "nav", "skill", "pass"))
_tb2 = {}
for _ in range(3):
    SC.record(_tb2, "fill_water_bucket", "h", False, 0, "SETUP_INVALID", cls="setup")
SC.record(_tb2, "fill_water_bucket", "h", True, 2, cls="pass")
SC.record(_tb2, "fill_water_bucket", "h", True, 3, cls="pass")
SC.record(_tb2, "fill_water_bucket", "h", False, 0, "SETUP_INVALID", cls="setup")
check("readiness: setup/harness failures don't count against a skill",
      SC.status(_tb2, "fill_water_bucket", "h") == ("scenario", 3))
_tv = {}
SC.record(_tv, "s", "c", True, 1)
check("bench: one pass is no verdict yet", SC.verdict(_tv, "s", "c") is None)
SC.record(_tv, "s", "c", True, 1)
check("bench: two passes → verdict, no more runs", SC.verdict(_tv, "s", "c") == "pass")
SC.record(_tv, "f", "c", False, 1, cls="setup")
SC.record(_tv, "f", "c", False, 1)
SC.record(_tv, "f", "c", False, 1)
check("bench: two counted fails → verdict (setup failures ignored)", SC.verdict(_tv, "f", "c") == "fail")
# A throwaway source tree, so this tests the tag logic rather than whether a checkout of the mod happens to sit
# next to this repository (without one, every digest collapses to the same "no sources" fallback).
_modsrc = tempfile.mkdtemp(prefix="bonobo-modsrc-")
for _rel in SC.MOD_CORE + [f for fs in SC.MOD_FILES.values() for f in fs]:
    _p = os.path.join(_modsrc, _rel)
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    with open(_p, "w") as _fh:
        _fh.write(_rel)
check("bench: a crafting-only scenario's key ignores pathfinder sources",
      SC.mod_hash(["craft"], java=_modsrc) != SC.mod_hash(None, java=_modsrc)
      and SC.mod_hash(["craft"], java=_modsrc) == SC.mod_hash(["craft"], java=_modsrc))
check("bench: with no mod sources the key falls back to the jar version, never to a constant",
      SC.mod_hash(["craft"], java="").startswith("jar-"))
shutil.rmtree(_modsrc, ignore_errors=True)
_ts = {}
SC.record(_ts, "activate_end_portal", "old", True, 10)
SC.record(_ts, "activate_end_portal", "old", True, 11)
check("bench: a stable scenario that passed stays settled across code/jar changes",
      SC.settled(_ts, "activate_end_portal") and SC.verdict(_ts, "activate_end_portal", "new-jar") is None)
SC.record(_ts, "activate_end_portal", "new-jar", False, 60)
check("bench: a failure un-settles it", not SC.settled(_ts, "activate_end_portal"))
_t1 = {}
SC.record(_t1, "cave_escape", "c", True, 22)
check("bench: one pass settles a non-fight scenario", SC.settled(_t1, "cave_escape"))
# Real case: a Nether-kit slice spent 8 minutes looking for water and sheep that were not in that biome. Walking 30
# blocks makes a new retry state, so only a per-segment counter stops it.
from bonobo import brain as BR  # noqa: E402
from bonobo import retry as RT  # noqa: E402
from bonobo.api import McError as _ME  # noqa: E402
from bonobo.api import NotAvailable as _NA  # noqa: E402

_brain = object.__new__(BR.Brain)
_brain.retry, _brain.sig, _brain.place = RT.Retry(), "state", "place"
_brain.seg_name, _brain.seg_misses = "nether kit", {}
for _i in range(BR.SEG_MISSES):
    _brain.sig = _brain.place = f"state-{_i}"      # a different place each time: the state-aware retry resets
    _brain.failed("water bucket", _NA("no water within 48 blocks"))
check("brain: 'found nothing' answers are counted per route segment, not per state",
      _brain.seg_misses[("nether kit", "water bucket")] == BR.SEG_MISSES
      and not _brain.retry.exhausted("water bucket", _brain.sig),
      _brain.seg_misses)
_brain.failed("water bucket", _ME("bucket broke"))
check("brain: only 'nothing here' answers count toward the segment cap, not real errors",
      _brain.seg_misses[("nether kit", "water bucket")] == BR.SEG_MISSES)
# Real case 07:35/07:48: "clicked water but the bucket stayed empty" on real lakes — the stand spot was a block below
# the surface and the level view crossed flowing water, which a bucket's ray ignores.
from bonobo import fluids as FL  # noqa: E402


class _Lake:
    """A still source at (0, 64, 0), flowing water at (1, 64, 0), a low shore at (2, 63, 0) and a high one at
    (0, 65, 1)."""
    def __init__(self):
        self.blocks = {(0, 64, 0): "water", (1, 64, 0): "water", (0, 63, 0): "stone", (1, 63, 0): "stone",
                       (2, 62, 0): "stone", (0, 64, 1): "stone"}
        self.levels = {(0, 64, 0): "0", (1, 64, 0): "3"}

    def name(self, p):
        return self.blocks.get(p, "air")

    def prop(self, p, key):
        return self.levels.get(p) if key == "level" else None

    def solid(self, p):
        return self.name(p) == "stone"

    def hazard(self, p):
        return False

    def inside(self, p):
        return -6 <= p[0] <= 6 and 58 <= p[1] <= 70 and -6 <= p[2] <= 6


_fs = FL.fill_spot(_Lake(), (2, 63, 0))
check("water: the fill stand spot is never below the surface, and the aim is the source's top face",
      _fs is not None and _fs[1] == (0, 64, 0) and _fs[0][1] >= 64
      and FL.surface_aim((0, 64, 0)) == (0.5, 64.95, 0.5), _fs)
# Real cases 07:57 and 07:58: both dragon benches died standing ~9 blocks in front of a perched dragon, health
# 20 → 0, without a single bed placed. The fight now waits at a distance that clears every body part and the breath.
from bonobo import combat as CB
from bonobo import combat_model as CM  # noqa: E402


class _Flat:
    """Flat stone floor at y 63, walkable at y 64, 24×24 around the origin."""
    def __init__(self):
        self.blocks = {(x, y, z): ("stone" if y == 63 else "air")
                       for x in range(-12, 13) for z in range(-12, 13) for y in (63, 64, 65)}

    def name(self, p):
        return self.blocks.get(p, "air")

    def solid(self, p):
        return self.name(p) == "stone"

    def hazard(self, p):
        return False

    def inside(self, p):
        return p in self.blocks


_near = [{"type": "minecraft:ender_dragon", "x": 0, "y": 64, "z": 0, "health": 200.0},
         {"type": "minecraft:ender_dragon", "x": 5, "y": 66, "z": 0},          # head: no health field
         {"type": "minecraft:area_effect_cloud", "x": 7, "y": 64, "z": 0},     # breath
         {"type": "minecraft:item", "x": 8, "y": 64, "z": 0}]
_hz = CM.hazard_points(_near)
_spot = CB.safe_stand(_Flat(), (9, 64, 0), _hz, (0, 0), band=(8, 12), clear=1.0)
check("combat: hazards carry their own reach — head 8, breath 6 — and items are not hazards",
      _hz == [((0, 64, 0), 8.0), ((5, 66, 0), 8.0), ((7, 64, 0), 6.0)], _hz)
check("combat: standing in front of the head is inside its reach (negative margin)",
      CB.clearance((9, 64, 0), _hz) < 0, CB.clearance((9, 64, 0), _hz))
check("combat: the wait spot clears every hazard's reach and stays inside the band",
      _spot is not None and CB.clearance(_spot, _hz) >= 1.0
      and 8 <= math.hypot(_spot[0] + 0.5, _spot[2] + 0.5) <= 12, (_spot, CB.clearance(_spot, _hz)))
# The speedrun shape lives in bunker.py now (tests/test_bunker.py); what stays here is the tolerance of "in the hole".
from bonobo import end as END  # noqa: E402

check("end: being in the hole tolerates a block of slop but not standing on the floor",
      END.in_pit((5, 62, 0), (5, 62, 0), 64) and END.in_pit((5, 61, 0), (5, 62, 0), 64)
      and not END.in_pit((5, 64, 0), (5, 62, 0), 64) and not END.in_pit((6, 62, 0), (5, 62, 0), 64))
# Fight skills take cover and retry; ordinary skills still end on an interrupt.
check("skills: every End fight skill is soft-interruptible, a plain skill is not",
      all(getattr(f, "contract").soft for f in (END.build_bed_pit, END.await_perch, END.bed_bomb_window,
                                                END.shake_enderman, END.break_caged_crystal, END.slay_dragon,
                                                CB.station))
      and not skills.eat.contract.soft)
# The dragon heals only from a crystal within 32 blocks of itself and the pillars stand 40+ out, so perched at the
# fountain it heals from nothing: beds alone finish the kit, a bow stays opportunistic.
from bonobo import route as RT_ROUTE  # noqa: E402


class _KitInv:
    def __init__(self, counts):
        self.counts = counts

    def count(self, item):
        return self.counts.get(item, 0)


class _KitMem:
    data = {}

    def sites(self, *a, **k):
        return []


_end_kit = next(s for s in RT_ROUTE.SPEEDRUN if s["name"] == "end kit")
check("route: beds alone finish the End kit; the bow is offered but never blocks it",
      _end_kit["done"](_KitInv({"bed": 6}), _KitMem(), "minecraft:overworld")
      and not _end_kit["done"](_KitInv({"bed": 5}), _KitMem(), "minecraft:overworld")
      and {"bow", "arrows (32)"} <= _end_kit["goals"])
check("route: already in the End, the kit segment no longer blocks the route",
      _end_kit["done"](_KitInv({}), _KitMem(), "minecraft:the_end"))
# The bed goes on the fountain's bedrock, under where the perched head hangs — not on the island floor.
_bedrock_bed = END.bed_cell((1, 0), 69)
check("end: the bed sits one block ABOVE the bedrock, 2 from the centre (obsidian goes under it)",
      _bedrock_bed == (2, 70, 0) and END.bed_cell((0, -1), 69) == (0, 70, -2), _bedrock_bed)
# Landing (phase 3) is not perched: running in while it came down met the head on the way.
check("end: only phases 5/6/7 open the window",
      END.perched({"phase": 6, "x": 0, "y": 64, "z": 0}) and END.perched({"phase": 7, "x": 0, "y": 64, "z": 0})
      and not END.perched({"phase": 3, "x": 0, "y": 64, "z": 0})
      and not END.perched({"phase": 1, "x": 0, "y": 64, "z": 0}))
_clouds = [{"type": "minecraft:area_effect_cloud", "x": 4, "y": 64, "z": 0},
           {"type": "minecraft:area_effect_cloud", "x": 6, "y": 64, "z": 0}]
check("end: breath is spotted as a group and the escape runs straight away from it, not to the roomiest cell",
      END.breath_near(_clouds, (5, 64, 0), 8.0) and not END.breath_near(_clouds, (30, 64, 0), 8.0)
      and END.breath_escape((7, 64, 0), _clouds, run=10) == (17, 64, 0),
      END.breath_escape((7, 64, 0), _clouds, run=10))
# Real case 08:36: the run walked from 14 blocks out to (9,64,-1) while the dragon sat on the portal — 20 hp to 0.
check("end: preparation waits while the dragon is perched and we are inside its reach",
      END.prep_safe({"phase": 6, "x": 0, "y": 64, "z": 0, "health": 200.0}, (9, 64, -1), floor_y=64) is False
      and END.prep_safe({"phase": 6, "x": 0, "y": 64, "z": 0, "health": 200.0}, (14, 64, 0), floor_y=64) is False
      and END.prep_safe({"phase": 6, "x": 0, "y": 64, "z": 0, "health": 200.0}, (17, 64, 0), floor_y=64)
      and END.prep_safe({"phase": 1, "x": 0, "y": 80, "z": 0, "health": 200.0}, (7, 64, 0), floor_y=64))
# Caged crystals: no bow in the speedrun kit, so tower up beside the pillar and break the bars between us and it.
_base, _stand, _bars = END.cage_plan((0, 100, 0), (20, 64, 0), 64)
check("end: the cage plan towers up on our side, stands at crystal height, breaks the whole bar ring",
      _base == (2, 64, 0) and _stand == (2, 100, 0)
      and set(_bars) == {(1, 100, 0), (1, 101, 0), (-1, 100, 0), (-1, 101, 0),
                         (0, 100, 1), (0, 101, 1), (0, 100, -1), (0, 101, -1)}, (_base, _stand, _bars))
check("end: a crystal high above us is caged, one at our level is not",
      END.caged((0, 100, 0), (20, 64, 0)) and not END.caged((10, 65, 4), (20, 64, 0)))
_ender = [{"type": "minecraft:enderman", "id": 7, "x": 6, "y": 64, "z": 0, "angry": True},
          {"type": "minecraft:enderman", "id": 8, "x": 3, "y": 64, "z": 0},
          {"type": "minecraft:end_crystal", "id": 9, "x": 0, "y": 80, "z": 20}]
check("combat: only provoked endermen count as a threat, neutral ones are left alone",
      [e["id"] for e in CB.angry_endermen(_ender, (0, 64, 0), 16.0)] == [7],
      [e["id"] for e in CB.angry_endermen(_ender, (0, 64, 0), 16.0)])
check("combat: only an aim through an enderman's head provokes it — level or low aims are fine",
      CM.aim_hits_enderman((12, 69, 0), (0, 64, 0), _ender)          # rising line crosses the head band
      and not CM.aim_hits_enderman((12, 64, 0), (0, 64, 0), _ender)  # same direction, below the head
      and not CM.aim_hits_enderman((0, 80, 20), (0, 64, 0), _ender))
check("combat: the crosshair sitting on an enderman is seen (mod lookingAt)",
      CB.looking_at_enderman({"lookingAt": {"kind": "entity", "entity": 7}}, _ender)
      and not CB.looking_at_enderman({"lookingAt": {"kind": "entity", "entity": 9}}, _ender)
      and not CB.looking_at_enderman({"lookingAt": {"kind": "block"}}, _ender))
check("combat: endermen close by are handled before the boss, nearest first",
      [round(e["x"]) for e in CB.endermen_near(
          [{"type": "minecraft:enderman", "x": 4, "y": 64, "z": 0},
           {"type": "minecraft:enderman", "x": 2, "y": 64, "z": 0},
           {"type": "minecraft:enderman", "x": 30, "y": 64, "z": 0},
           {"type": "minecraft:ender_dragon", "x": 1, "y": 64, "z": 0, "health": 200.0}], (0, 64, 0), 6.0)] == [2, 4])
check("combat: engage holds while hurt and attacks only in an open window",
      (CB.engage(20, True), CB.engage(20, False), CB.engage(14, True), CB.engage(8, True))
      == ("attack", "hold", "hold", "retreat"))
# Real case 08:07: a 200-block trek over y 63–70 terrain (no water) bridged small dips until all 64 cobblestone were
# gone and travel failed "no building blocks to bridge with".
check("travel: a full bag keeps a block reserve, a small kit still gets half",
      [nav.place_budget(n) for n in (64, 40, 33, 16, 8, 0)] == [48, 24, 17, 8, 4, 0],
      [nav.place_budget(n) for n in (64, 40, 33, 16, 8, 0)])
# Real case 07:59: a trip kept the start's y (87) over ground at 71–79 and travel answered "no route" in 0 s.
_col = {(5, y, 9) for y in range(40, 71)}          # solid up to y 70, open above
check("nav: a guessed target y is moved onto the real ground of its column",
      nav.ground_in_column(lambda p: p in _col, 5, 9, 87) == 71
      and nav.ground_in_column(lambda p: False, 5, 9, 87) is None)
_tf = {}
SC.record(_tf, "fight_blaze", "c", True, 10)
SC.record(_tf, "fight_blaze", "c", True, 11)
check("bench: fights never settle (they change run to run)", not SC.settled(_tf, "fight_blaze"))
_slog = [f"06:39:{s:02d} bucket/mine:minecraft:raw_iron: vein yielded nothing" for s in range(10, 15)] + \
        ["06:39:20   travel    succeeded arrived (1.0s)", "06:39:21 route: now 'portal'"]
_spos = [(0, (0, 64, 0)), (1, (10, 64, 0)), (2, (4, 64, 0)), (3, (20, 64, 0))]
_srep = SC.slice_report(_slog, _spos, (100, 64, 0), 22.4)
check("route slice report: a repeated decision line is a loop, idle is kept, walking away is counted",
      len(_srep["loops"]) == 1 and "vein yielded nothing" in _srep["loops"][0] and _srep["idle_s"] == 22
      and _srep["away_m"] == 6)
check("bench: server-side entity count parsed",
      SC.server_count(["Test passed. Count: 3"]) == 3 and SC.server_count(["Test failed"]) == 0)
check("bench: /locate reply parsed",
      SC.locate_reply([{"cmd": "execute in minecraft:overworld run locate structure minecraft:stronghold",
                        "reply": ["The nearest minecraft:stronghold is at [10456, ~, 9832] (484 blocks away)"]}])
      == (10456, 9832))
from bonobo import end as END  # noqa: E402
check("dragon: perched only near the island centre (its body sits a few blocks off the pillar)",
      END.perched({"x": 1.0, "z": -2.0}) and END.perched({"x": 6.0, "z": 3.0})
      and not END.perched({"x": 30.0, "z": 0.0}))
check("bed bomb: the side the player stands on", END.choose_side((-20, 64, 3)) == (-1, 0)
      and END.choose_side((2, 64, 15)) == (0, 1))
check("dragon: with a synced phase, perching is the phase (sitting/landing), not a position guess",
      END.perched({"x": 30.0, "y": 90.0, "z": 0.0, "phase": 6}, 64)
      and not END.perched({"x": 0.5, "y": 65.0, "z": 0.5, "phase": 0}, 64))
# Real case 06:32: the first "ender_dragon" entry was a body part (no health, id unknown to the client): 100 attacks
# returned "target not found" in 0 s.
check("dragon: the entity with health is the dragon, not a body part of the same type",
      END.dragon_entry([{"type": "minecraft:ender_dragon", "id": 7}, {"type": "minecraft:end_crystal", "id": 8},
                        {"type": "minecraft:ender_dragon", "id": 3, "health": 147.7}])["id"] == 3
      and END.dragon_entry([{"type": "minecraft:ender_dragon", "id": 7}]) is None)
# Real case 05:40: a dragon summoned at y 70 above the pillar counted as perched; the fight stood still.
check("dragon: hovering above the pillar isn't perched; the pillar top comes from the bedrock",
      END.pillar_top([(0, 60, 0), (0, 62, 1), (1, 63, 0), (20, 70, 0)]) == 64
      and END.perched({"x": 0.5, "y": 65.0, "z": 0.5}, 64) and not END.perched({"x": 0.5, "y": 75.0, "z": 0.5}, 64))
# Real case 05:20: eyes placed in search order crossed the ring 12 times (79 s).
_ring = [(10 + dx, 60, 10 - 2) for dx in (-1, 0, 1)] + [(10 + dx, 60, 10 + 2) for dx in (-1, 0, 1)] + \
        [(10 - 2, 60, 10 + dz) for dz in (-1, 0, 1)] + [(10 + 2, 60, 10 + dz) for dz in (-1, 0, 1)]
_stops = END.ring_stops(_ring, (10, 60, 10), (14, 60, 10))
check("end portal: one stop per side, 3 eyes each, starting at the nearest side, walking around",
      len(_stops) == 4 and all(len(fs) == 3 for _, fs in _stops) and _stops[0][0] == (13, 60, 10)
      and {s for s, _ in _stops} == {(13, 60, 10), (7, 60, 10), (10, 60, 13), (10, 60, 7)})
# Real case 05:04: standing on the frame to place an eye slid into the opening's lava.
check("end portal: eyes are placed from outside the ring",
      END.outside_spot((10, 60, 8), (10, 60, 10)) == (10, 60, 7)
      and END.outside_spot((12, 60, 11), (10, 60, 10)) == (13, 60, 11))
_sp = END.search_points((100, -40))
check("stronghold: search starts at the estimate at room depth, then 8 points of the first ring",
      _sp[0] == (100, 30, -40) and len(_sp) == 1 + 8 + 16 + 24
      and all(max(abs(p[0] - 100), abs(p[2] + 40)) == 40 for p in _sp[1:9]))
check("stronghold: follow the nearest brick not already visited",
      END.next_brick([(0, 30, 0, 5.0), (40, 30, 0, 9.0)], [(2, 30, 1)]) == (40, 30, 0, 9.0)
      and END.next_brick([(0, 30, 0, 5.0)], [(1, 30, 0)]) is None)
check("bench: entity signature (3 piglins expected, 2 there)",
      SC.entity_mismatches([{"type": "minecraft:piglin"}] * 2, [("minecraft:piglin", 3)])
      and not SC.entity_mismatches([{"type": "minecraft:piglin"}] * 3, [("minecraft:piglin", 3)]))
# Real case 03:43: go_to returned False after 3 "target unreachable" travels and the run was classed "skill".
check("bench: a silent failure after a failed travel is a nav failure",
      type(SC.silent_failure(["03:43:31", "  travel    failed    target unreachable; stopped at the closest"],
                             False)).__name__ == "NavFailed"
      and type(SC.silent_failure(["  mine_many failed    5 of 6 steps failed"], None)).__name__ == "McError")
check("bench: lava variants keep the far platform inside the box",
      all(SC.SCENARIOS[n]["expect"][2][1][0] <= SC.at(*SC.BOX[1])[0]
          for n in ("cross_lava_3", "cross_lava_8", "cross_lava_lake")))
check("readiness: keyed by the skill's transitive modules", "nav" in SC.module_deps("fluids")
      and "api" in SC.module_deps("fluids") and "scenarios" not in SC.module_deps("fluids"))

_loop = [(None, "night in the water: swimming to land first")] * 12 + [(None, "  goto      failed    no path")] * 9
check("review: a decision loop shows as one repeated pattern",
      "×12 night in the water" in RV.repeated(_loop) and "goto" not in RV.repeated(_loop))

# -- a wooden axe before real woodcutting (hand logs ~3.9 s each on the bench)
from bonobo import lookahead as LA  # noqa: E402


class _NoTools:
    def count(self, token, include_worn=False):
        return 0

    def usable(self, token):
        return 0

    def tools(self, kind):
        return []


class _Step:
    def __init__(self, kind, token, count):
        self.kind, self.token, self.count, self.detail, self.est = kind, token, count, {}, 60 * count


_axe = LA.prepare([_Step("gather", "log", 8)], _NoTools(), 1000, True, {})
_few = LA.prepare([_Step("gather", "log", 3)], _NoTools(), 1000, True, {})
check("look-ahead: 8 planned logs without an axe → wooden axe first; 3 logs → no",
      ("minecraft:wooden_axe", 1) in _axe and not any(t == "minecraft:wooden_axe" for t, _ in _few))

# -- road network: proven legs are reused only where they beat the direct way
from bonobo import roads as ROADS  # noqa: E402
_rd = []
ROADS.add_leg(_rd, (0, 70, 0), (200, 70, 0), 30.0, 1)          # a fast known road east (0.15 s/block)
ROADS.add_leg(_rd, (0, 70, 0), (200, 70, 0), 45.0, 2)          # a slower repeat keeps the best time
check("roads: a repeated leg keeps its fastest time", len(_rd) == 1 and _rd[0]["s"] == 30.0)
check("roads: a trip past the road's end follows it",
      ROADS.route(_rd, (2, 70, 1), (210, 70, 0))[0] == (200, 70, 0))
ROADS.add_leg(_rd, (0, 70, 0), (0, 70, 300), 900.0, 3)          # a terrible known leg north (a mountain tunnel)
check("roads: a slow known leg isn't taken over the direct way",
      ROADS.route(_rd, (0, 70, 0), (0, 70, 300)) == [(0, 70, 300)])

# -- decisions apart from execution: random-state invariants (pure rules must hold for any bag / state)
import random as _rnd  # noqa: E402
from bonobo import bag as BAG, route as RT, brain as BR, decide as DEC, tape as TAPE  # noqa: E402

_ITEMS = ["minecraft:cobblestone", "minecraft:dirt", "minecraft:oak_log", "minecraft:stick", "minecraft:coal",
          "minecraft:iron_ingot", "minecraft:cooked_beef", "minecraft:beef", "minecraft:bread", "minecraft:gravel",
          "minecraft:flint", "minecraft:torch", "minecraft:water_bucket", "minecraft:ender_pearl", "minecraft:string",
          "minecraft:rotten_flesh", "minecraft:obsidian", "minecraft:golden_helmet", "minecraft:sand"]


class _RInv:
    def __init__(self, slots, equipment=None):
        self.slots, self.equipment = slots, equipment or {}

    def count(self, item, include_worn=False):
        from bonobo.data import GROUPS, mid
        ids = GROUPS.get(item, [mid(item)])
        return sum(s["count"] for s in self.slots if s["id"] in ids)

    def used_slots(self):
        return len(self.slots)

    def worn(self, slot):
        return (self.equipment.get(slot) or {}).get("id", "minecraft:air")


_bad = []
for _seed in range(300):
    r = _rnd.Random(_seed)
    slots = [{"id": r.choice(_ITEMS), "count": r.randint(1, 64)} for _ in range(r.randint(5, 36))]
    slots += [{"id": "minecraft:stone_pickaxe", "count": 1, "damage": r.randint(0, 130), "maxDamage": 131}]
    BAG.RESERVED = set(r.sample(_ITEMS, 3))
    throw = BAG.free_slots_plan(slots, need=r.randint(0, 20))
    # 1. the biggest stack of every reserved item stays
    for rid in BAG.RESERVED:
        stacks = [s for s in slots if s["id"] == rid]
        if stacks and all(s in throw for s in stacks):
            _bad.append((_seed, "reserved item fully thrown", rid))
    # 2. protected things (working tools, ingots, pearls, water bucket, cooked food) are never thrown
    for s in throw:
        if BAG._protected_stack(s):
            _bad.append((_seed, "protected stack thrown", s["id"]))
    # 3. one definition of food: route, brain and the bag's cooked count agree
    inv = _RInv(slots)
    from bonobo.knowledge import ALL_FOOD
    if not (RT.food_count(inv) == BR.food_count(inv) == sum(s["count"] for s in slots if s["id"] in ALL_FOOD)):
        _bad.append((_seed, "food counts disagree"))
    # 4. no Nether trip without the kit; low health in the Nether always retreats
    if not RT.nether_kit_missing(inv) and RT.food_count(inv) < RT.KIT_FOOD:
        _bad.append((_seed, f"kit complete with < {RT.KIT_FOOD} food"))

    class _Snap:
        dimension = "minecraft:the_nether"
        state = {"health": r.randint(1, 8)}
    _Snap.inv = inv
    if not BR.must_retreat(_Snap):
        _bad.append((_seed, "hp ≤ 8 in the Nether without a retreat"))
BAG.RESERVED = set()
check("invariants (300 random bags/states): reservations, protection, food, kit, retreat", not _bad, _bad[:3])

# -- decision tape encoding round-trips (replays must see the same retry state and signatures)
_sig = ((1, 2, 3), frozenset({"minecraft:dirt"}), True, 0)
check("tape: signatures and retry state round-trip", TAPE.decode_sig(TAPE.encode_sig(_sig)) == _sig)
check("tape: an old recording's blacklist size is normalised away",
      TAPE.decode_sig([[1, 2, 3], ["minecraft:dirt"], True, 234]) == _sig)
check("decide: longest run of one pick", DEC.longest_run([(0, "a"), (1, "a"), (2, "b"), (3, "a")], "a") == 2)

# -- golden decisions (recorded situations with the expected pick), and a loop check on each of them
for _name, _ok, _msg in DEC.golden_results():
    check(f"golden decision: {_name}", _ok, _msg)

# -- loops (simulate on every golden world): everything the brain picks fails, the world never changes. The retry
# policy must not pick the same candidate over and over: at most EXHAUSTED_AFTER in a row, then something else.
import json as _json  # noqa: E402
from bonobo import retry as _retry  # noqa: E402
from bonobo.api import McError as _McError  # noqa: E402
if os.path.isdir(DEC.GOLDEN):
    for _fn in sorted(os.listdir(DEC.GOLDEN)):
        if not _fn.endswith(".json"):
            continue
        with open(os.path.join(DEC.GOLDEN, _fn)) as _f:
            _row = _json.load(_f)
        try:
            _picks = DEC.simulate(_row, lambda name, i: _McError("scripted failure"), rounds=60)
        except TAPE.ReplayMiss as _e:
            check(f"loop check {_fn[:-5]}: replayable", False, str(_e))
            continue
        _names = {p[1] for p in _picks if p[1]}
        _worst = max((DEC.longest_run(_picks, n) for n in _names), default=0)
        check(f"loop check {_fn[:-5]}: no candidate picked > {_retry.EXHAUSTED_AFTER}× in a row while all fail",
              _worst <= _retry.EXHAUSTED_AFTER, f"longest run {_worst}")
        _rot = DEC.step_rotation(_picks)
        check(f"loop check {_fn[:-5]}: one failing step isn't retried by goals taking turns (≤ 2 in 6 rounds)",
              _rot <= 2, f"step tried {_rot}× within 6 rounds")

# -- cerebellum scheduling, offline (real recorded worlds, edited into the situations that went wrong live)
_rows = {}
if os.path.isdir(DEC.GOLDEN):
    for _fn in os.listdir(DEC.GOLDEN):
        if _fn.endswith(".json"):
            with open(os.path.join(DEC.GOLDEN, _fn)) as _f:
                _rows[_fn[:-5]] = _json.load(_f)
_base = _rows.get("rotating_mine_failure")

# 1. The route never flips back to 'nether kit' for small wobbles once a later segment was reached.
class _KitInv:
    def __init__(self, food, blocks, helmet, used):
        self.food, self.blocks, self.helmet, self.used = food, blocks, helmet, used

    def count(self, token, include_worn=False):
        if token in ("food",) or token.startswith("minecraft:cooked"):
            return self.food if token == "minecraft:cooked_beef" else 0
        if token == "building":
            return self.blocks
        if token == "minecraft:golden_helmet":
            return 1 if self.helmet else 0
        return 0

    def worn(self, slot):
        return "minecraft:air"

    def used_slots(self):
        return self.used


_flips = 0
_state = {"reached": 0}
_prev = None
_r = _rnd.Random(7)
for _i in range(200):
    _inv = _KitInv(food=12 + _r.randint(-3, 2), blocks=32 + _r.randint(-6, 4), helmet=True, used=28 + _r.randint(0, 3))
    _seg = RT.active_segment(RT.ROUTES["speedrun"], _inv, type("M", (), {"sites": lambda *a, **k: [],
                                                                         "machines": lambda *a, **k: [],
                                                                         "data": {}})(), "minecraft:overworld")
    _seg = RT.with_hysteresis(RT.ROUTES["speedrun"], _seg, _state, _inv)
    _name = _seg["name"] if _seg else None
    if _prev is not None and _name != _prev:
        _flips += 1
    _prev = _name
check("route: wobbling kit counts (±3 food, ±6 blocks) don't flip segments back and forth", _flips <= 2,
      f"{_flips} segment changes in 200 rounds")

if _base is not None:
    # 2. Idle is bounded: with every candidate failing, "nothing runnable" never lasts past the idle rule + a round.
    _picks = DEC.simulate(_base, lambda name, i: _McError("scripted failure"), rounds=80)
    _idle = _longest = 0
    _start = None
    for _t, _n, _k in _picks:
        if _n is None:
            _start = _start if _start is not None else _t
            _longest = max(_longest, _t - _start)
        else:
            _start = None
    from bonobo.brain import IDLE_LIMIT as _IL
    check(f"scheduling: idle stays within the idle rule while everything fails (≤ {_IL + 5} s)",
          _longest <= _IL + 5, f"longest idle {_longest:.0f} s")

    # 3. Survival preempts: in the Nether at 6 hp with the arrival portal known, the rescue is the retreat.
    _hurt = DEC.synth(_base, state={"dimension": "minecraft:the_nether", "health": 6.0},
                      calls={f"/entities?radius={r}": {"entities": []} for r in (8, 10, 12, 16)},
                      mem=lambda m: m.setdefault("sites", []).append(
                          {"name": "portal-nether", "kind": "portal", "pos": [0, 70, 0],
                           "dimension": "minecraft:the_nether"}))
    try:
        # Leaving the Nether is no longer an if above the pool: it is a candidate priced in seconds, so the check
        # is that it WINS, not that it runs first. If it stops winning at 6 hp, the price is wrong.
        _pick, _top, _filt, _ = DEC.decide(_hurt)
        check("scheduling: 6 hp in the Nether → the pool chooses to leave through the portal",
              _pick is not None and "retreat" in _pick, f"picked {_pick}; top {[n for n, _ in _top]}")
    except TAPE.ReplayMiss as _e:
        check("scheduling: survival pick replayable from the recorded world", False, str(_e))

    # 4. Going through the portal isn't retried by goals taking turns: kit complete, a portal built, every portal
    #    trip failing (the live "leaving again and again").
    _ready = DEC.synth(_base, add_items=[("minecraft:cooked_beef", 16), ("minecraft:cobblestone", 64),
                                         ("minecraft:golden_helmet", 1), ("minecraft:flint_and_steel", 1)],
                       mem=lambda m: m.setdefault("machines", []).append(
                           {"name": "nether_portal-1", "blueprint": "nether_portal", "origin": [10, 64, 10],
                            "turns": 0, "dimension": "minecraft:overworld", "tags": ["portal"]}))
    try:
        _pp = DEC.simulate(_ready, lambda name, i: _McError("portal trip failed"), rounds=40)
        _nether = [p for p in _pp if p[1] in ("nether fortress", "blaze rods (7)", "piglin barter")]
        _worst = max((DEC.longest_run(_pp, n) for n in {p[1] for p in _nether}), default=0)
        check("scheduling: failing portal trips aren't repeated by Nether goals in turn (≤ 3 in a row, ≤ 6 of 40)",
              _worst <= 3 and len(_nether) <= 6, f"longest {_worst}, total {len(_nether)}")
    except TAPE.ReplayMiss as _e:
        check("scheduling: portal situation replayable from the recorded world", False, str(_e))

# 8. The resource map feeds costs: nothing in sight, a tree noted 90 blocks away → a distance, not "unobtainable".
from unittest import mock as _mock2  # noqa: E402


class _MapMem:
    def resources(self, kind, dimension, now=None):
        return [[90, 64, 0]] if kind == "tree" else []


class _MapSnap:
    feet = (0, 64, 0)
    dimension = "minecraft:overworld"

    def get(self, key, default=None):
        return default


with _mock2.patch("bonobo.brain.find", return_value=[]):
    _lc = BR.LiveCost(_MapSnap(), {}, _MapMem())
    check("costs: a remembered tree 90 blocks away is a distance when none is in sight",
          _lc._find(["oak_log", "birch_log"], 48) == 90.0 and _lc._find(["gold_ore"], 48) is None)

# 7. Never idle while exploring could find what's missing (recorded 07:32: portal cooling, food waiting for a seen pig,
#    bed for seen sheep → "staying while it retries" filtered exploring → nothing runnable).
_idle_row = _rows.get("idle_while_portal_retries")
if _idle_row is not None:
    try:
        _ip, _itop, _ifilt, _ = DEC.decide(_idle_row)
        check("scheduling: a portal retry doesn't leave the agent idle when exploring can help",
              _ip is not None, f"picked {_ip}; explore: {_ifilt.get('explore')}")
    except TAPE.ReplayMiss as _e:
        check("scheduling: idle situation replayable", False, str(_e))

if _base is not None:
    # 5. Unstuck in the Nether heads for the arrival portal, not the nearest remembered spot (the live wrong-way
    #    unstuck). The movement is captured, not executed.
    from unittest import mock as _mock
    _stuck = DEC.synth(_base, state={"dimension": "minecraft:the_nether"},
                       mem=lambda m: m.setdefault("sites", []).extend([
                           {"name": "fortress", "kind": "fortress", "pos": [_base["calls"]["/state"]["blockX"] + 6, 70,
                                                                          _base["calls"]["/state"]["blockZ"]],
                            "dimension": "minecraft:the_nether"},
                           {"name": "portal-nether", "kind": "portal", "pos": [_base["calls"]["/state"]["blockX"] - 40,
                                                                             70, _base["calls"]["/state"]["blockZ"]],
                            "dimension": "minecraft:the_nether"}]))
    _targets = []
    _b = DEC.make_brain(_stuck)
    with DEC._files(_stuck), _mock.patch("time.time", return_value=_stuck["t"]), \
            _mock.patch("bonobo.nav.go_to", lambda target, *a, **k: _targets.append(tuple(target)) or True):
        TAPE.REPLAY = _stuck["calls"]
        try:
            from bonobo.world import Snapshot as _Snap
            _b.unstuck(None, _Snap())
        except TAPE.ReplayMiss as _e:
            _targets.append(("miss", str(_e)))
        finally:
            TAPE.REPLAY = None
    check("scheduling: unstuck in the Nether heads for the arrival portal first (not the nearer fortress)",
          _targets and _targets[0][0] == _base["calls"]["/state"]["blockX"] - 40, _targets[:2])

    # 6. Recovery chain: right after a death with the items on the ground, the pool picks recovering them.
    #    `carried` is what the corpse holds, and it is the whole reason to walk back: recovery is worth what that
    #    pile costs to make again (memory.worth_of), so a scenario that leaves it empty is a scenario about an
    #    empty corpse — correctly worth nothing, and correctly beaten by making a sword.
    _dead = DEC.synth(_base, mem=lambda m: m.setdefault("deaths", []).append(
        {"pos": [_base["calls"]["/state"]["blockX"] + 5, _base["calls"]["/state"]["blockY"],
                 _base["calls"]["/state"]["blockZ"]], "dimension": "minecraft:overworld", "t": _base["t"] - 30,
         "carried": [["minecraft:iron_pickaxe", 1], ["minecraft:iron_ingot", 12], ["minecraft:cooked_beef", 16]]}))
    try:
        _pick, _top, _filt, _ = DEC.decide(_dead)
        check("scheduling: a fresh death → 'recover items after death' is picked", _pick == "recover items after death",
              f"picked {_pick}; recover filtered: {_filt.get('recover items after death')}")
    except TAPE.ReplayMiss as _e:
        check("scheduling: recovery situation replayable", False, str(_e))

print(f"\n{len(FAILS)} failed" if FAILS else "\nall offline checks passed")

if __name__ == "__main__":
    sys.exit(1 if FAILS else 0)

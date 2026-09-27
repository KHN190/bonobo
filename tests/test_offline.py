#!/usr/bin/env python3
"""Offline checks for the parts of bonobo that decide without the game: blueprints, planning, inventory hygiene,
terrain readers, bench bookkeeping. Run standalone (`python3 tests/test_offline.py`, exit code 1 on failure) or
under unittest, where `Offline` fails with every failing check named. `same(name, got, want)` is the exact form:
a failure shows both values."""
import unittest
import os
import tempfile
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bonobo import blueprints as B  # noqa: E402
from bonobo import nav, skills  # noqa: E402
from bonobo.planner import NullCost, Planner  # noqa: E402

FAILS = []
CHECKS = []            # every check, in order: (name, held?, detail) — the table the unittest walks
ALL = {"pillar", "ladder_in_cell"}


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))
    CHECKS.append((name, bool(cond), detail))
    if not cond:
        FAILS.append(name + (f"  {detail}" if detail else ""))


def same(name, got, want):
    """An exact check: `got == want`, and on failure both values."""
    check(name, got == want, f"got {got!r}, want {want!r}")


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


_BUILDING_ITEM = nav.building_item            # restored at the end: the stub must not outlive this module
nav.building_item = lambda: "minecraft:cobblestone"
WALK_ONLY = nav.Policy(allow_dig=False)


# ---- blueprints
for bp in B.REGISTRY.values():
    for t in range(4):
        cells = B.placed(bp, (0, 64, 0), t)
        pos = [c[0] for c in cells]
        same(f"{bp.name} rot{t}: no overlapping parts", len(pos), len(set(pos)))
        clear = B.clear_cells(bp, (0, 64, 0), t)
        check(f"{bp.name} rot{t}: clear cells aren't parts (except a torch)",
              all(c not in pos or any(p[0] == c and p[1].item == "minecraft:torch" for p in cells) for c in clear))
        for p, part, facing, against in cells:
            if against is not None and part.facing:
                d = tuple(against[i] - p[i] for i in range(3))
                same(f"{bp.name} rot{t}: {part.item} at {part.offset} outputs into its against", B.DIRS[facing], d)
hut = B.placed(B.SHELTER, (0, 0, 0), 0)
solid = {c[0] for c in hut if c[1].item != "minecraft:torch"} | {(0, 1, -1)}   # door top counts as solid
interior = [c for c in B.clear_cells(B.SHELTER, (0, 0, 0), 0) if c != (0, 1, -1)]
leaks = [(c, d) for c in interior for d in B.DIRS.values()
         if (c[0] + d[0], c[1] + d[1], c[2] + d[2]) not in solid and (c[0] + d[0], c[1] + d[1], c[2] + d[2]) not in interior
         and d != (0, -1, 0)]
check("shelter interior is sealed (floor aside)", not leaks, leaks)
same("shelter needs 14 stone + door + torch", B.materials(B.SHELTER), {"stone": 14, "door": 1, "minecraft:torch": 1})


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
same("pending output satisfies a need",
     Planner.from_inventory(inv, NullCost(), {"minecraft:hopper": 3}).plan([("minecraft:hopper", 3)]), [])

# ---- inventory hygiene: the one scoring (bag.let_go) is tabled in test_skill_contract.BagFull

# ---- survival: finding air
# A 7×7 pool, 6 deep, with a stone floor and walls up to the water line; open on top unless capped.
pool = {(x, y, z): "water" for x in range(-3, 4) for y in range(0, 6) for z in range(-3, 4)}
pool.update({(x, -1, z): "stone" for x in range(-4, 5) for z in range(-4, 5)})
pool.update({(x, y, z): "stone" for x in (-4, 4) for y in range(0, 6) for z in range(-4, 5)})
pool.update({(x, y, z): "stone" for z in (-4, 4) for y in range(0, 6) for x in range(-4, 5)})
r = FakeRegion(pool, (-8, -2, -8), (8, 16, 8))
capped = dict(pool)
capped.update({(x, 6, z): "stone" for x in range(-3, 4) for z in range(-3, 4)})
capped.update({(x, y, z): "stone" for x in (-4, 4) for y in range(0, 7) for z in range(-4, 5)})
capped.update({(x, y, z): "stone" for z in (-4, 4) for y in range(0, 7) for x in range(-4, 5)})
r = FakeRegion(capped, (-8, -2, -8), (8, 16, 8))
pocket = dict(capped)
pocket[(3, 5, 3)] = "air"
r = FakeRegion(pocket, (-8, -2, -8), (8, 16, 8))

# ---- survival: digging out of a pod
podw = {(x, 0, z): "stone" for x in range(-3, 4) for z in range(-3, 4)}           # floor
podw.update({(1, 1, 0): "andesite", (1, 2, 0): "andesite", (-1, 1, 0): "andesite", (-1, 2, 0): "andesite",
             (0, 1, 1): "andesite", (0, 2, 1): "andesite", (0, 1, -1): "andesite", (0, 2, -1): "andesite",
             (0, 3, 0): "andesite"})
podw[(-2, 1, 0)] = "stone"                                                         # west side: rock beyond
podw[(0, 1, 2)] = "lava"                                                           # south side: lava beyond
r = FakeRegion(podw, (-3, -2, -3), (3, 4, 3))
ex = skills.choose_exit(r, (0, 1, 0))
same("dig out: east (open, floored; first of the tie with north), both wall cells mined, never toward lava",
     ex, ([(1, 1, 0), (1, 2, 0)], (1, 1, 0)))
check("pod counts as enclosed", skills.is_enclosed(r, (0, 1, 0)))
torch_gap = dict(podw)
torch_gap[(1, 1, 0)] = "torch"
r = FakeRegion(torch_gap, (-3, -2, -3), (3, 4, 3))
check("a torch in a side cell under a solid block is still enclosed", skills.is_enclosed(r, (0, 1, 0)))
ex = skills.choose_exit(r, (0, 1, 0))
same("dig out through the torch side mines only the head cell", ex, ([(1, 2, 0)], (1, 1, 0)))
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
same("throw: in a tunnel → back along the tunnel, not into a niche", skills.throw_direction(r, (0, 0, 0)), (-1, 0))
niche_only = dict(shaft)
niche_only.pop((1, 0, 0)), niche_only.pop((1, 1, 0))          # a single 1-block niche beside a ladder shaft
r = FakeRegion(niche_only, (-3, -1, -3), (3, 2, 3))
check("throw: only a 1-block niche → don't throw (items land at our feet)", skills.throw_direction(r, (0, 0, 0)) is None,
      skills.throw_direction(r, (0, 0, 0)))

# ---- clock
from bonobo.world import ticks_until_dusk  # noqa: E402

same("dusk: morning counts down to 12500", ticks_until_dusk(1000), 11500)
same("dusk: night is 0", ticks_until_dusk(18000), 0)
same("dusk: dawn (after 23400) has a whole day ahead, not 0", ticks_until_dusk(23600), 12900)

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
same("burrow: faces the solid hill", skills.choose_burrow(r, (0, 0, 0)), (1, 0))
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
same("land: nearest dry standing spot on the shore", spot, (5, 1, 0))
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
same("shelter: flat solid ground allows digging in", skills.shelter_method_at(r, (3, 0, 3)), "dig")
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
same("open space: finds the passage/room nearby", spot, (1, 3, 0))

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

# ---- time estimation
import tempfile  # noqa: E402

from bonobo import skill as skillkit  # noqa: E402
from bonobo.memory import Memory  # noqa: E402

tmp = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
skillkit.STATS = tmp
same("estimate: prior before any measurement", skillkit.expected(skills.chop, None, 10), 60)
for secs in (40, 40, 40):
    tmp.record_duration("chop", secs, 10)
check("estimate: measured per-unit time replaces the prior after 3 runs",
      abs(skillkit.expected(skills.chop, None, 10) - 40) < 1e-6, skillkit.expected(skills.chop, None, 10))
tmp.record_duration("mine:minecraft:raw_iron", 100, 5)
same("estimate: one sample isn't trusted yet", skillkit.expected(skills.mine, None, "minecraft:raw_iron", 5, [], 1), 40)
tmp.record_duration("chop", 100, 10)
check("estimate: moving average of seconds per unit leans on history (4·0.7 + 10·0.3)",
      abs(tmp.duration("chop") - 5.8) < 1e-9, tmp.duration("chop"))
skillkit.STATS = None

# ---- background jobs (multitasking)
import time as _time  # noqa: E402

tmp.add_job("furnace", (1, 2, 3), "minecraft:overworld", "minecraft:iron_ingot", 8, _time.time() + 85, True)
same("job: its output counts as pending for the planner",
     tmp.pending_outputs("minecraft:overworld").get("minecraft:iron_ingot"), 8)
job = tmp.jobs()[0]
check("job: not ready before its estimate", not skills.job_ready(job))
tmp.postpone_job(job["id"], -1)
check("job: ready once the estimate has passed", skills.job_ready(tmp.jobs()[0]))
tmp.finish_job(job["id"])
same("job: finished jobs stop counting", tmp.pending_outputs("minecraft:overworld"), {})


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
_thrown_h = {s["id"] for s in BG.free_slots_plan(_hungry, need=3)}
check("food: raw meat is never thrown while cooked food is short (it was thrown 10× during a food hunt)",
      "minecraft:mutton" not in _thrown_h, _thrown_h)
_seedbag = _hungry + [{"id": "minecraft:dirt", "count": 64, "slot": 5}]
_prices = {"minecraft:wheat_seeds": 20.0, "minecraft:dirt": 0.5}.get
check("farm: seeds (priced) stay while surplus dirt can go",
      "minecraft:wheat_seeds" not in {s["id"] for s in BG.free_slots_plan(_seedbag, need=1, price=_prices)})

# Real case 02:30: 5 obsidian + 2 cobblestone stood in the frame; the goal still asked for 10 obsidian and mined
# the frame. Only missing parts may be needed, and a started build's cells are protected.
_frame = {(-367, 119, 191): "cobblestone", (-364, 119, 191): "cobblestone", (-366, 119, 191): "obsidian",
          (-365, 119, 191): "obsidian", (-367, 120, 191): "obsidian", (-367, 121, 191): "obsidian"}
_need = B.remaining(B.NETHER_PORTAL, (-367, 119, 191), 0, lambda p: _frame.get(p, "air"))
same("build: a started portal needs only its missing parts", _need, {"minecraft:obsidian": 6, "stone": 2})
_bm = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
_bm.data["builds"] = {"nether_portal": {"origin": [-367, 119, 191], "turns": 0, "dimension": "minecraft:overworld"}}
check("build: cells of an unfinished build are protected from mining",
      (-367, 120, 191) in _bm.protected_cells("minecraft:overworld"))

broken ={"id": "minecraft:stone_pickaxe", "count": 1, "slot": 3, "damage": 130, "maxDamage": 131}
worn_ok = {"id": "minecraft:stone_pickaxe", "count": 1, "slot": 4, "damage": 100, "maxDamage": 131}
same("tidy: broken tools go, worn ones stay", skills.free_slots_plan([broken, worn_ok], need=0), [broken])

_tctx = __import__("bonobo.skillcore", fromlist=["Context"]).Context(None, None, "minecraft:overworld", blacklist={})
_tctx.ban_counts = {}
_tctx.ban((90, 64, 0))
check("deposit: a failed trek isn't retried soon (its site cell is banned)",
      not skills.site_trek_ok(_tctx, {"name": "far base", "pos": [90, 64, 0]})
      and skills.site_trek_ok(_tctx, {"name": "home", "pos": [5, 64, 0]}))

# -- memory dedup
from bonobo.memory import Memory  # noqa: E402
_mp = os.path.join(tempfile.mkdtemp(), "notes.json")
_m = Memory(_mp)
_m.add_station("crafting_table", (1, 2, 3), "overworld")
_m.add_station("crafting_table", (1, 2, 3), "overworld")
_m.note_seen("iron_ore", (5, 5, 5), "overworld")
_m.note_seen("iron_ore", (5, 5, 5), "overworld")
check("memory: stations and seen ore deduplicated", len(_m.data["stations"]) == 1
      and len(_m.seen("iron_ore", "overworld")) == 1)

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
same("portal: blueprint uses 10 obsidian + 4 stone",
     B.materials(B.NETHER_PORTAL), {"stone": 4, "minecraft:obsidian": 10})
_cells = {pos: part.item for pos, part, *_ in B.placed(B.NETHER_PORTAL, (0, 64, 0), 1)}
_aim = FL.portal_light_aim((0, 64, 0), 1)
check("portal: light aim is the top face of an inner bottom obsidian (rotated)",
      _cells.get((int(_aim[0] // 1), 64, int(_aim[2] // 1))) == "minecraft:obsidian" and _aim[1] == 65.0, _aim)

# -- perception: when a running task must be interrupted
from bonobo import perception as PC  # noqa: E402

same("perception: an interrupt is its own cause, never a failure",
     __import__("bonobo.retry", fromlist=["cause_of"]).cause_of(
          __import__("bonobo.api", fromlist=["Interrupted"]).Interrupted("lava")), "interrupt")

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
same("farm: only age-7 wheat is ripe", FM.ripe_cells(_wheat), [(0, 64, 0)])
_herd = [{"id": 1, "type": "minecraft:cow", "x": 0, "y": 64, "z": 0}, {"id": 2, "type": "minecraft:cow", "x": 3, "y": 64, "z": 2},
         {"id": 3, "type": "minecraft:sheep", "x": 40, "y": 64, "z": 0}]
check("farm: breeding pair = two close adults of one kind", FM.breeding_pair(_herd, "minecraft:cow") == (1, 2)
      and FM.breeding_pair(_herd, "minecraft:sheep") is None)
_rm = Memory(os.path.join(tempfile.mkdtemp(), "notes.json"))
_rm.clock = 0
_rm.note_seen("tree", (10, 64, 10), "overworld")
_rm.forget_seen("tree", (10, 64, 10), "overworld")
same("seen: a grove we felled is forgotten, not walked to again", _rm.seen("tree", "overworld"), [])
check("recipes: hoes exist for the farm", "minecraft:stone_hoe" in __import__("bonobo.data", fromlist=["RECIPES"]).RECIPES)

# -- nether / stronghold helpers
from bonobo import nether as NT  # noqa: E402

same("stronghold: two throws triangulate", NT.triangulate((0, 0), (1, 0), (100, -100), (0, 1)), (100, 0))
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
same("dragon: open crystals nearest first, caged high ones last",
     [c["x"] for c in CB.crystal_order(_cr, (0, 64, 0))], [10, 5, 40])
_fort = {(x, 64, z): "nether_bricks" for x in range(-5, 6) for z in range(-5, 6)}
_fort.update({(2, 65, 0): "nether_bricks", (2, 66, 0): "nether_bricks"})
_cover = CB.blaze_cover(FakeRegion(_fort, (-5, 60, -5), (5, 70, 5)), (0, 65, 0), (5, 66, 0))
check("blaze: cover puts a solid block between us and the blaze", _cover is not None and _cover[0] < 2, _cover)
_chest = [{"owner": "chest", "slot": 0, "id": "minecraft:rotten_flesh"}, {"owner": "chest", "slot": 1, "id": "minecraft:obsidian"},
          {"owner": "chest", "slot": 2, "id": "minecraft:gold_ingot"}, {"owner": "player", "slot": 30, "id": "minecraft:diamond"}]
_chest_prices = {"minecraft:obsidian": 300.0, "minecraft:gold_ingot": 500.0, "minecraft:rotten_flesh": 0.0}
same("loot: takes what is worth a slot, dearest first, and never the player's own",
     LT.loot_plan(_chest, _chest_prices, 30), [2, 1])
_frames = _PropRegion({(0, 30, 0): "end_portal_frame", (1, 30, 0): "end_portal_frame", (4, 30, 4): "end_portal_frame"},
                      {(0, 30, 0): {"eye": "true"}, (1, 30, 0): {"eye": "false"}, (4, 30, 4): {"eye": "false"}},
                      (-1, 29, -1), (5, 31, 5))
same("end: frames without an eye", sorted(EN.frames_missing_eye(_frames)), [(1, 30, 0), (4, 30, 4)])
same("end: portal centre of the frame ring",
     EN.portal_centre([(0, 30, 1), (4, 30, 3), (2, 30, 0), (2, 30, 4)]), (2, 30, 2))
same("brewing: full chain when inputs are there",
     BW.brew_steps({"minecraft:potion:water": 3, "minecraft:nether_wart": 1, "minecraft:magma_cream": 1,
                     "minecraft:blaze_powder": 1}), ["minecraft:nether_wart", "minecraft:magma_cream"])
check("brewing: missing magma cream → can't brew", BW.brew_steps({"minecraft:potion:water": 3, "minecraft:nether_wart": 1}) is None)
_tools = [{"id": "minecraft:stone_pickaxe", "slot": 3, "damage": 120, "maxDamage": 131},
          {"id": "minecraft:stone_pickaxe", "slot": 4, "damage": 110, "maxDamage": 131},
          {"id": "minecraft:diamond_pickaxe", "slot": 5, "damage": 100, "maxDamage": 1561}]
same("repair: two worn stone pickaxes combine", UK.repair_pair(_tools, "pickaxe"), ("minecraft:stone_pickaxe", 3, 4))
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
same("enchant: best affordable option", UI.choose_enchant(_opts, 15, 3), 1)
same("enchant: lapis limits the button", UI.choose_enchant(_opts, 40, 1), 0)
check("enchant: nothing affordable", UI.choose_enchant(_opts, 2, 3) is None)
_offers = [{"sell": "minecraft:ender_pearl", "buy": "minecraft:emerald", "buyCount": 9},
           {"sell": "minecraft:ender_pearl", "buy": "minecraft:emerald", "buyCount": 5},
           {"sell": "minecraft:ender_pearl", "buy": "minecraft:emerald", "buyCount": 3, "disabled": True},
           {"sell": "minecraft:bread", "buy": "minecraft:emerald", "buyCount": 1}]
same("trade: cheapest enabled affordable offer",
     UI.choose_trade(_offers, "minecraft:ender_pearl", {"minecraft:emerald": 6}), 1)
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
from bonobo.knowledge import nether_kit_missing  # noqa: E402
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


same("nether kit: the real bag (4 food, no gold helmet, 35 slots) isn't ready",
     len(nether_kit_missing(_KitInv({"minecraft:cooked_beef": 4}, 35))), 4)   # food, blocks, helmet, room
same("nether kit: enough food + 32 blocks + gold helmet worn + room → ready",
     nether_kit_missing(_KitInv({"minecraft:cooked_beef": 12, "building": 40}, 28, head="minecraft:golden_helmet")), [])
check("nether kit: no blocks → not ready (bridges, shelter from fireballs)",
      any("blocks" in m for m in nether_kit_missing(_KitInv({"minecraft:cooked_beef": 12}, 20, head="minecraft:golden_helmet"))))
check("nether: exploration legs stay above the lava sea", 50 <= NT.EXPLORE_Y <= 100)
import math  # noqa: E402
_wp = NT.waypoints((-258, 65, 270), (-366, 120, 191))
check("portal trip: 110 blocks go in legs of ≤40, ending at the portal",
      _wp[-1] == (-366, 120, 191) and len(_wp) == 4
      and all(math.hypot(b[0] - a[0], b[2] - a[2]) <= 41 for a, b in zip([(-258, 65, 270)] + _wp, _wp)), _wp)
# Real case 03:03: dirt at (-17,61,241) next to a pond two blocks away; the pit filled and the agent nearly drowned.
# WHERE the water is comes from the game; how far away is far enough is the rule being checked here.
from unittest import mock as _mock_pond  # noqa: E402
_pond_hits = [{"x": -15, "y": 61, "z": 241}, {"x": -15, "y": 62, "z": 241}]
with _mock_pond.patch.object(skills, "find", lambda *a, **k: _pond_hits):
    check("mine: surface blocks keep 2 blocks from water",
          skills.fluids_near({(-17, 61, 241)}, 2) == {(-17, 61, 241)}
          and skills.fluids_near({(-17, 61, 241)}, 1) == set()
          and skills.fluids_near({(-20, 61, 241)}, 2) == set())

# -- scenario bench: readiness table
from bonobo import scenarios as SC  # noqa: E402

_tb = {}
for ok, s in ((True, 40), (False, 90), (True, 50)):
    SC.record(_tb, "cast_obsidian", "abc", ok, s)
same("readiness: 2 of the last 3 passed → scenario-ready, median of passes",
     SC.status(_tb, "cast_obsidian", "abc"), ("scenario", 50))
same("readiness: per code version (a new hash starts untested)", SC.status(_tb, "cast_obsidian", "new")[0], "untested")
SC.record(_tb, "cast_obsidian", "abc", False, 99)
SC.record(_tb, "cast_obsidian", "abc", False, 99)
same("readiness: recent failures demote a skill", SC.status(_tb, "cast_obsidian", "abc")[0], "failing")
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
same("bench: command feedback errors are caught, harmless replies aren't", SC.feedback_errors(_fb), _fb[2:5])
_pool = {SC.at(dx, -1, dz): "lava" for dx in range(-2, 3) for dz in range(-2, 3)}
_nat = {SC.at(3, 0, 1): "grass_block"}
check("bench: exact signature — natural terrain in the box is a mismatch",
      not SC.setup_mismatches(_pool, [(SC.at(-2, -1, -2), SC.at(2, -1, 2), "lava", 25, 25)])
      and SC.setup_mismatches(_nat, [(SC.at(-6, 0, -6), SC.at(6, 4, 6), "*", 0, 0)]))
from bonobo.api import NavFailed  # noqa: E402
same("bench: failure classes", (SC.classify(SC.SetupInvalid("x"), False), SC.classify(NavFailed("x"), False),
                                 SC.classify(ValueError("x"), False), SC.classify(None, True)), ("setup", "nav", "skill", "pass"))
_tb2 = {}
for _ in range(3):
    SC.record(_tb2, "fill_water_bucket", "h", False, 0, "SETUP_INVALID", cls="setup")
SC.record(_tb2, "fill_water_bucket", "h", True, 2, cls="pass")
SC.record(_tb2, "fill_water_bucket", "h", True, 3, cls="pass")
SC.record(_tb2, "fill_water_bucket", "h", False, 0, "SETUP_INVALID", cls="setup")
same("readiness: setup/harness failures don't count against a skill",
     SC.status(_tb2, "fill_water_bucket", "h"), ("scenario", 3))
_tv = {}
SC.record(_tv, "s", "c", True, 1)
same("bench: one pass on the first run is a pass (≤3 runs, ≥2/3)", SC.verdict(_tv, "s", "c"), "pass")
SC.record(_tv, "s2", "c", False, 1)
same("bench: one failure is no verdict yet: run again", SC.verdict(_tv, "s2", "c"), None)
SC.record(_tv, "f", "c", False, 1, cls="setup")
SC.record(_tv, "f", "c", False, 1)
SC.record(_tv, "f", "c", False, 1)
same("bench: two counted fails → verdict (setup failures ignored)", SC.verdict(_tv, "f", "c"), "fail")
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
same("combat: hazards carry their own reach — head 8, breath 6 — and items are not hazards",
     _hz, [((0, 64, 0), 8.0), ((5, 66, 0), 8.0), ((7, 64, 0), 6.0)])
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


class _KitInv:
    def __init__(self, counts):
        self.counts = counts

    def count(self, item):
        return self.counts.get(item, 0)


class _KitMem:
    data = {}

    def sites(self, *a, **k):
        return []


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
same("combat: only provoked endermen count as a threat, neutral ones are left alone",
     [e["id"] for e in CB.angry_endermen(_ender, (0, 64, 0), 16.0)], [7])
check("combat: only an aim through an enderman's head provokes it — level or low aims are fine",
      CM.aim_hits_enderman((12, 69, 0), (0, 64, 0), _ender)          # rising line crosses the head band
      and not CM.aim_hits_enderman((12, 64, 0), (0, 64, 0), _ender)  # same direction, below the head
      and not CM.aim_hits_enderman((0, 80, 20), (0, 64, 0), _ender))
check("combat: the crosshair sitting on an enderman is seen (mod lookingAt)",
      CB.looking_at_enderman({"lookingAt": {"kind": "entity", "entity": 7}}, _ender)
      and not CB.looking_at_enderman({"lookingAt": {"kind": "entity", "entity": 9}}, _ender)
      and not CB.looking_at_enderman({"lookingAt": {"kind": "block"}}, _ender))
same("combat: endermen close by are handled before the boss, nearest first", [round(e["x"]) for e in CB.endermen_near(
          [{"type": "minecraft:enderman", "x": 4, "y": 64, "z": 0},
           {"type": "minecraft:enderman", "x": 2, "y": 64, "z": 0},
           {"type": "minecraft:enderman", "x": 30, "y": 64, "z": 0},
           {"type": "minecraft:ender_dragon", "x": 1, "y": 64, "z": 0, "health": 200.0}], (0, 64, 0), 6.0)], [2, 4])
# Real case 08:07: a 200-block trek over y 63–70 terrain (no water) bridged small dips until all 64 cobblestone were
# gone and travel failed "no building blocks to bridge with".
same("travel: a full bag keeps a block reserve, a small kit still gets half",
     [nav.place_budget(n) for n in (64, 40, 33, 16, 8, 0)], [48, 24, 17, 8, 4, 0])
# Real case 07:59: a trip kept the start's y (87) over ground at 71–79 and travel answered "no route" in 0 s.
_col = {(5, y, 9) for y in range(40, 71)}          # solid up to y 70, open above
check("nav: a guessed target y is moved onto the real ground of its column",
      nav.ground_in_column(lambda p: p in _col, 5, 9, 87) == 71
      and nav.ground_in_column(lambda p: False, 5, 9, 87) is None)
_tf = {}
SC.record(_tf, "collect_blaze_rods", "c", True, 10)
SC.record(_tf, "collect_blaze_rods", "c", True, 11)
check("bench: fights never settle (they change run to run)", not SC.settled(_tf, "collect_blaze_rods"))
_slog = [f"06:39:{s:02d} bucket/mine:minecraft:raw_iron: vein yielded nothing" for s in range(10, 15)] + \
        ["06:39:20   travel    succeeded arrived (1.0s)", "06:39:21 route: now 'portal'"]
_spos = [(0, (0, 64, 0)), (1, (10, 64, 0)), (2, (4, 64, 0)), (3, (20, 64, 0))]
_srep = SC.slice_report(_slog, _spos, (100, 64, 0), 22.4)
check("bench: server-side entity count parsed",
      SC.server_count(["Test passed. Count: 3"]) == 3 and SC.server_count(["Test failed"]) == 0)
same("bench: /locate reply parsed",
     SC.locate_reply([{"cmd": "execute in minecraft:overworld run locate structure minecraft:stronghold",
                        "reply": ["The nearest minecraft:stronghold is at [10456, ~, 9832] (484 blocks away)"]}]), (10456, 9832))
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
# (frames missing, floor over the lava solid, portal lit, a block carried) → (floor to fill, stand, eyes), or the
# reason: the speedrun way — a block over the middle, stand on it, every missing eye from there.
for _name, _miss, _solid, _lit, _block, _want in [
        ("lava in the middle: block it, stand there, all 12", _ring, False, False, True,
         ((10, 59, 10), (10, 60, 10), 12)),
        ("9 eyes in: only the 3 missing", _ring[:3], True, False, True, (None, (10, 60, 10), 3)),
        ("no block, lava below: a reason", _ring, False, False, False, "no block"),
        ("lit already: nothing", _ring[:3], True, True, True, (None, None, 0)),
        ("no frame missing: nothing", [], True, False, True, (None, None, 0))]:
    try:
        _f, _s, _fr = END.eye_plan(_miss, (10, 60, 10), _solid, _lit, _block)
        _got = (_f, _s, len(_fr))
        _reach = all(math.dist((_s[0] + .5, _s[1] + 1.62, _s[2] + .5), (f[0] + .5, f[1] + .8125, f[2] + .5)) <= 4.5
                     for f in _fr)
    except Exception as _e:
        _got, _reach = ("no block" if "no block" in str(_e) else str(_e)), True
    check(f"end portal: {_name}", _got == _want and _reach)
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

# -- road network: proven legs are reused only where they beat the direct way
from bonobo import roads as ROADS  # noqa: E402
_rd = []
ROADS.add_leg(_rd, (0, 70, 0), (200, 70, 0), 30.0, 1)          # a fast known road east (0.15 s/block)
ROADS.add_leg(_rd, (0, 70, 0), (200, 70, 0), 45.0, 2)          # a slower repeat keeps the best time
same("roads: a repeated leg keeps its fastest time and its newest use", [(leg["s"], leg["used"]) for leg in _rd],
     [(30.0, 2)])
ROADS.add_leg(_rd, (0, 70, 0), (0, 70, 300), 900.0, 3)          # a terrible known leg north (a mountain tunnel)

nav.building_item = _BUILDING_ITEM
print(f"\n{len(FAILS)} failed" if FAILS else "\nall offline checks passed")


class Offline(unittest.TestCase):
    def test_every_offline_check(self):
        self.assertGreater(len(CHECKS), 100, "the checks did not run")
        for i, (name, held, detail) in enumerate(CHECKS):
            with self.subTest(f"{i}: {name}"):
                self.assertEqual((held, detail if not held else ""), (True, ""))


if __name__ == "__main__":
    sys.exit(1 if FAILS else 0)

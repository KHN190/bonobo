#!/usr/bin/env python3
"""Offline tables for the parts of bonobo that decide without the game: blueprints, planning, the bag, terrain readers, the End, screens, bench bookkeeping. Every row is (situation, the call, what it must answer); a row named "must fail" is the answer that tells a wrong reading apart. Regions are block dicts handed to pure readers."""
import collections
import datetime
import math
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bonobo import arbiter, bag as BG, blueprints as B, brewing as BW, combat as CB, combat_model as CM  # noqa: E402
from bonobo import dragon as DR, end as END, farming as FM, fluids as FL, loot as LT, nav, needs as UK, nether as NT  # noqa: E402
from bonobo import explore as EX, review as RV, roads as ROADS, store as ST, survive as SV, terrain as TN, ui as UI, world as WD  # noqa: E402
from bonobo.bench import table as SC  # noqa: E402
from bonobo.bench import core  # noqa: E402
from bonobo.bench.words import runs as words_runs  # noqa: E402
from bonobo.api import NavFailed  # noqa: E402
from bonobo.bench import runner  # noqa: E402
from bonobo.data import RECIPES  # noqa: E402
from bonobo.knowledge import nether_kit_missing  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from bonobo.planner import NullCost, Planner, Step  # noqa: E402
from bonobo.skillcore import Context  # noqa: E402
from bonobo.world import ticks_until_dusk  # noqa: E402

DIRS = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0), "up": (0, 1, 0),
        "down": (0, -1, 0)}      # a facing → the step toward it


def table(tc, rows):
    """rows: [(situation, thunk, want)]; `want` is the exact answer, or a predicate over it (a function)."""
    for why, got_of, want in rows:
        with tc.subTest(why):
            got = got_of()
            if callable(want):
                tc.assertTrue(want(got), got)
            else:
                tc.assertEqual(got, want)


def memory():
    return Memory(os.path.join(tempfile.mkdtemp(prefix="offline"), "notes.json"))


class FakeRegion:
    def __init__(self, blocks, lo, hi, props=None):
        self.blocks, self.lo, self.hi, self._props = blocks, lo, hi, props or {}

    def inside(self, p):
        return all(self.lo[i] <= p[i] <= self.hi[i] for i in range(3))

    def name(self, p):
        return self.blocks.get(p, "air")

    def prop(self, p, key):
        return self._props.get(p, {}).get(key)

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


def region(blocks, lo, hi, set_=None, drop=()):
    """A FakeRegion over a copy of `blocks`, with `set_` ({pos: name}) laid on and `drop` taken away."""
    b = dict(blocks) | (set_ or {})
    for c in drop:
        b.pop(c, None)
    return FakeRegion(b, lo, hi)


def setUpModule():
    global _STUB
    _STUB = mock.patch.object(nav, "building_item", lambda: "minecraft:cobblestone")     # the bag, not the readers
    _STUB.start()


def tearDownModule():
    _STUB.stop()


# ---------------------------------------------------------------- blueprints
class Blueprints(unittest.TestCase):
    def test_every_rotation(self):
        # (blueprint, rotation) → parts never overlap, clear cells are never parts (a torch aside), a facing part outputs into the block it is against
        for bp in B.REGISTRY.values():
            for t in range(4):
                with self.subTest(bp=bp.name, rot=t):
                    cells = B.placed(bp, (0, 64, 0), t)
                    pos = [c[0] for c in cells]
                    self.assertEqual(len(pos), len(set(pos)))
                    torches = {p[0] for p in cells if p[1].item == "minecraft:torch"}
                    self.assertEqual([c for c in B.clear_cells(bp, (0, 64, 0), t) if c in pos and c not in torches],
                                     [])
                    for p, part, facing, against in cells:
                        if against is not None and part.facing:
                            self.assertEqual(DIRS[facing], tuple(against[i] - p[i] for i in range(3)))

    def test_table(self):
        hut = B.placed(B.SHELTER, (0, 0, 0), 0)
        solid = {c[0] for c in hut if c[1].item != "minecraft:torch"} | {(0, 1, -1)}     # door top counts as solid
        interior = [c for c in B.clear_cells(B.SHELTER, (0, 0, 0), 0) if c != (0, 1, -1)]
        frame = {(-367, 119, 191): "cobblestone", (-364, 119, 191): "cobblestone", (-366, 119, 191): "obsidian",
                 (-365, 119, 191): "obsidian", (-367, 120, 191): "obsidian", (-367, 121, 191): "obsidian"}
        cells = {pos: part.item for pos, part, *_ in B.placed(B.NETHER_PORTAL, (0, 64, 0), 1)}
        table(self, [
            ("the shelter's interior is sealed (floor aside)",
             lambda: [(c, d) for c in interior for d in DIRS.values() if d != (0, -1, 0)
                      and (c[0] + d[0], c[1] + d[1], c[2] + d[2]) not in solid | set(interior)], []),
            ("the shelter: 14 stone, a door, a torch", lambda: B.materials(B.SHELTER),
             {"stone": 14, "door": 1, "minecraft:torch": 1}),
            ("the portal: 10 obsidian, 4 stone", lambda: B.materials(B.NETHER_PORTAL),
             {"stone": 4, "minecraft:obsidian": 10}),
            # real case 02:30: the goal still asked 10 obsidian and mined the frame it had started
            ("must fail: a started portal needs only its missing parts",
             lambda: B.missing_materials(B.NETHER_PORTAL, (-367, 119, 191), 0, lambda p: frame.get(p, "air")),
             {"minecraft:obsidian": 6, "stone": 2}),
            ("the portal's walk-in cell is inside the frame", lambda: NT.portal_cell((0, 64, 0), 0),
             lambda c: c in B.clear_cells(B.NETHER_PORTAL, (0, 64, 0), 0)),
        ])


# ---------------------------------------------------------------- planning and the bag
class _Inv:
    def __init__(self, slots):
        self.slots, self.equipment = slots, {}

    def tools(self, kind):
        return []


class LInv:
    def __init__(self, counts, slots_used=0, head=None):
        self.counts, self._used, self._head = counts, slots_used, head

    def count(self, token):
        return self.counts.get(token, 0)

    def usable(self, token):
        return self.count(token)

    def tools(self, kind):
        return []

    def used_slots(self):
        return self._used

    def free_slots(self):
        from bonobo.world import BAG_SLOTS
        return max(0, BAG_SLOTS - self._used)

    def worn(self, slot):
        return self._head if slot == "head" else None


def _step(kind, token, count, **detail):
    s = Step(kind, token, count, detail)
    s.est = 60
    return s


def _stacks(*rows):
    return [{"id": f"minecraft:{i}", "count": n, "slot": s} for i, n, s in rows]


class PlanningAndBag(unittest.TestCase):
    def test_table(self):
        rich = _Inv([{"id": "minecraft:iron_ingot", "count": 20}, {"id": "minecraft:oak_planks", "count": 64},
                     {"id": "minecraft:cobblestone", "count": 64}, {"id": "minecraft:stick", "count": 8}])
        no_pick = _Inv([{"id": "minecraft:cobblestone", "count": 20}, {"id": "minecraft:stick", "count": 4},
                        {"id": "minecraft:oak_planks", "count": 8}])
        farm = [_step("craft", "planks", 4, times=1, inputs={"log": 1}),
                _step("craft", "minecraft:stick", 4, times=1, inputs={"planks": 2}),
                _step("craft", "minecraft:stone_hoe", 1, times=1, inputs={"stone": 2, "minecraft:stick": 2})]
        planks = [{"id": "minecraft:spruce_planks", "count": 60, "slot": i} for i in range(20)] + \
            _stacks(("stick", 40, 30), ("dirt", 64, 31))
        seeds = _stacks(("wheat_seeds", 5, 1), ("cobblestone", 64, 2), ("cobblestone", 64, 3), ("cobblestone", 20, 4))
        hungry = _stacks(("mutton", 6, 1), ("cooked_mutton", 3, 2), ("dirt", 64, 3), ("wheat_seeds", 5, 4))
        broken = {"id": "minecraft:stone_pickaxe", "count": 1, "slot": 3, "damage": 130, "maxDamage": 131}
        worn = {"id": "minecraft:stone_pickaxe", "count": 1, "slot": 4, "damage": 100, "maxDamage": 131}

        def reserved(for_the_farm, then):
            BG.RESERVED = BG.reserved_ids(farm, [("minecraft:stone_hoe", 1)]) if for_the_farm else \
                {"minecraft:wheat_seeds", "minecraft:cobblestone"}
            try:
                return then()
            finally:
                BG.RESERVED = set()

        def left(bag, gone):
            return {s["id"] for s in bag if s not in gone}

        table(self, [
            ("hoppers from the bag: crafts only",
             lambda: Planner.from_inventory(rich, NullCost()).plan([("minecraft:hopper", 3), ("minecraft:chest", 3)]),
             lambda p: p and all(s.kind == "craft" for s in p)),
            ("no pickaxe, cobble carried: the stone pickaxe crafted directly",
             lambda: Planner.from_inventory(no_pick, NullCost()).plan([("tool", "pickaxe", 1)]),
             lambda p: p and p[0].kind == "craft" and p[-1].token == "minecraft:stone_pickaxe"),
            ("boundary: an output on its way meets the need",
             lambda: Planner.from_inventory(rich, NullCost(), {"minecraft:hopper": 3}).plan([("minecraft:hopper", 3)]),
             []),
            ("pickup: below 28 slots everything", lambda: BG.pickup_whitelist(27, ["minecraft:raw_iron"]), None),
            ("must fail: from 28 slots only wanted and kept items (no cobble, no dirt)",
             lambda: BG.pickup_whitelist(30, ["minecraft:raw_iron", "log"]),
             lambda w: {"minecraft:raw_iron", "minecraft:oak_log", "minecraft:diamond"} <= set(w)
             and not {"minecraft:cobblestone", "minecraft:dirt"} & set(w)),
            # real loop: a farm plan crafted planks, tidy threw them away, the plan crafted them again
            ("must fail: tidy leaves the plan a stack of what it needs",
             lambda: reserved(True, lambda: left(planks, BG.free_slots_plan(planks, need=10))),
             lambda kept: {"minecraft:spruce_planks", "minecraft:stick"} <= kept),
            ("storage keeps a stack of them too", lambda: reserved(True, lambda: left(planks, BG.store_plan(planks))),
             lambda kept: {"minecraft:spruce_planks", "minecraft:stick"} <= kept),
            ("one stack per reserved item, not all of them", lambda: reserved(False, lambda: BG.reserved_stacks(seeds)),
             lambda k: len(k) == 2 and 1 in {s["slot"] for s in k}),
            ("must fail: a down-weighted goal's seeds are never thrown",
             lambda: reserved(False, lambda: {s["id"] for s in BG.free_slots_plan(seeds, need=3)}),
             lambda gone: "minecraft:wheat_seeds" not in gone),
            ("must fail: raw meat stays while cooked food is short",
             lambda: {s["id"] for s in BG.free_slots_plan(hungry, need=3)},
             lambda gone: "minecraft:mutton" not in gone),
            ("priced seeds stay while surplus dirt goes",
             lambda: {s["id"] for s in BG.free_slots_plan(hungry + _stacks(("dirt", 64, 5)), need=1,
                                                         price={"minecraft:wheat_seeds": 20.0,
                                                                "minecraft:dirt": 0.5}.get)},
             lambda gone: "minecraft:wheat_seeds" not in gone),
            ("broken tools go, worn ones stay", lambda: BG.free_slots_plan([broken, worn], need=0), [broken]),
        ])


# ---------------------------------------------------------------- terrain readers
POD = {(x, 0, z): "stone" for x in range(-3, 4) for z in range(-3, 4)} | {
    c: "andesite" for c in ((1, 1, 0), (1, 2, 0), (-1, 1, 0), (-1, 2, 0), (0, 1, 1), (0, 2, 1), (0, 1, -1), (0, 2, -1),
                            (0, 3, 0))} | {(-2, 1, 0): "stone", (0, 1, 2): "lava"}   # rock west, lava south
POD_BOX = ((-3, -2, -3), (3, 4, 3))
ROCK = {(x, y, z): "stone" for x in range(-3, 4) for y in range(-1, 3) for z in range(-3, 4)}
SHAFT = [(0, 0, 0), (0, 1, 0)]
ROCK_BOX = ((-3, -1, -3), (3, 2, 3))
HILL = {(x, y, z): "stone" for x in range(-4, 5) for y in range(-2, 4) for z in range(-4, 5)
        if not (x < 0 and y >= 0) and not (x == 0 and y in (0, 1) and z == 0)}   # open to the west, solid from x=1
HILL_BOX = ((-4, -2, -4), (4, 3, 4))
CAVE = {(x, y, z): "stone" for x in range(-8, 9) for y in range(-2, 6) for z in range(-8, 9)}
CAVE_OPEN = [(0, y, 0) for y in range(5)] + [(x, y, z) for x in range(3, 8) for z in range(-2, 3) for y in (3, 4, 5)] \
    + [(x, y, 0) for x in (1, 2) for y in (3, 4)]                    # a shaft, a room east, a passage into it
DEEP = {(x, y, z): "stone" for x in range(-6, 7) for y in range(50, 65) for z in range(-6, 7)}
DEEP_BOX = ((-6, 50, -6), (6, 66, 6))


def _sea(shores=(), walls=()):
    """Water at y 0 over sand, x/z -8..8; `shores`: x ranges of grass; `walls`: x columns of glass above the water."""
    b = {(x, 0, z): "water" for x in range(-8, 9) for z in range(-8, 9)}
    b.update({(x, -1, z): "sand" for x in range(-8, 9) for z in range(-8, 9)})
    for x0, x1 in shores:
        b.update({(x, 0, z): "grass_block" for x in range(x0, x1 + 1) for z in range(-8, 9)})
    for wx in walls:
        b.update({(wx, y, z): "glass" for y in range(0, 3) for z in range(-8, 9)})
    return FakeRegion(b, (-8, -2, -8), (8, 5, 8))


def _route(r):
    got = TN.air_route(r, (0, 0, 0))
    return got[:2] if got else None


class Terrain(unittest.TestCase):
    def test_digging_out_and_throwing(self):
        torch = region(POD, *POD_BOX, {(1, 1, 0): "torch"})
        cave = region(CAVE, (-8, -2, -8), (8, 6, 8), drop=CAVE_OPEN)
        table(self, [
            ("dig out: east (open, floored; first of the tie), both wall cells, never toward lava",
             lambda: TN.choose_exit(region(POD, *POD_BOX), (0, 1, 0)), ([(1, 1, 0), (1, 2, 0)], (1, 1, 0))),
            ("a torch in the side cell: only the head cell", lambda: TN.choose_exit(torch, (0, 1, 0)),
             ([(1, 2, 0)], (1, 1, 0))),
            ("the pod is enclosed", lambda: WD.is_enclosed(region(POD, *POD_BOX), (0, 1, 0)), True),
            ("boundary: a torch under a solid block is still enclosed", lambda: WD.is_enclosed(torch, (0, 1, 0)),
             True),
            ("must fail: a 2-high opening is not enclosed", lambda: WD.is_enclosed(
                region(POD, *POD_BOX, drop=[(1, 1, 0), (1, 2, 0)]), (0, 1, 0)), False),
            ("throw: a sealed shaft, nowhere", lambda: BG.throw_direction(region(ROCK, *ROCK_BOX, drop=SHAFT),
                                                                             (0, 0, 0)), None),
            ("throw: in a tunnel, back along it — not into the niche", lambda: BG.throw_direction(region(
                ROCK, *ROCK_BOX,
                drop=SHAFT + [(x, y, 0) for x in (-3, -2, -1) for y in (0, 1)] + [(0, 0, 1), (0, 1, 1)]),
                (0, 0, 0)), (-1, 0)),
            ("must fail: only a 1-block niche, don't throw (items land at the feet)", lambda: BG.throw_direction(
                region(ROCK, *ROCK_BOX, drop=SHAFT + [(1, 0, 0), (1, 1, 0)]), (0, 0, 0)), None),
            ("a cave's sealed shaft bottom: no room to throw", lambda: BG.throw_direction(cave, (0, 0, 0)), None),
            ("open space: the passage to the room nearby", lambda: TN.find_open_spot(cave, (0, 0, 0)), (1, 3, 0)),
        ])

    def test_night_spots(self):
        peak = FakeRegion({(x, y, z): "stone" for x in range(-8, 9) for y in range(-6, 0) for z in range(-8, 9)}
                          | {(0, y, 0): "stone" for y in range(6)}, (-10, -6, -10), (10, 9, 10))   # a thin pillar
        water = FakeRegion({(x, 0, z): "water" for x in range(-5, 6) for z in range(-5, 6)}, (-6, -3, -6), (6, 3, 6))
        flat = FakeRegion({(x, -1, z): "stone" for x in range(-4, 5) for z in range(-4, 5)}, *HILL_BOX)
        solid = FakeRegion(DEEP, *DEEP_BOX)
        ravine = FakeRegion({p: b for p, b in DEEP.items() if p[0] != 1}, *DEEP_BOX)   # an open slot one block east
        wet = region(DEEP, *DEEP_BOX, {(x, y, z): "water" if (x + z) % 2 else "stone" for x in range(-6, 7)
                                       for z in range(-6, 7) for y in range(55, 63)})
        chest_room = FakeRegion({(0, 1, 0): "stone"}, (-2, -1, -2), (2, 3, 2))
        table(self, [
            ("burrow: faces the solid hill", lambda: TN.choose_burrow(FakeRegion(HILL, *HILL_BOX), (0, 0, 0)),
             (1, 0)),
            ("must fail: never burrow next to water", lambda: TN.choose_burrow(
                region(HILL, *HILL_BOX, {(3, 0, 1): "water", (1, 1, -1): "water"}), (0, 0, 0)), None),
            ("burrow: flat open ground, none", lambda: TN.choose_burrow(flat, (0, 0, 0)), None),
            ("chest: air above opens", lambda: TN.chest_spot_ok(chest_room, (1, 0, 0)), True),
            ("must fail: a solid block above, it can't open", lambda: TN.chest_spot_ok(chest_room, (0, 0, 0)),
             False),
        ])

    def test_water(self):
        rock = _sea(shores=[(5, 8)])
        rock.blocks[(2, 0, 2)], rock.blocks[(2, 1, 2)] = "stone", "water"          # a rock under water: not land
        lake = {(x, 60, z): ("water" if x >= 2 else "stone") for x in range(-4, 6) for z in range(-3, 4)}
        lake.update({(x, 59, z): "stone" for x in range(-4, 6) for z in range(-3, 4)})
        # real case: stood at (-71,12,307), water at z=309 behind a stone wall at z=308
        walled = {(x, 11, z): "stone" for x in range(-74, -67) for z in range(304, 312)}
        walled.update({(x, y, 308): "stone" for x in range(-74, -67) for y in (12, 13, 14)})
        walled.update({(x, y, z): "water" for x in range(-74, -67) for y in (12, 13) for z in (309, 310)})
        # real cases 07:35/07:48: a still source at (0,64,0), flowing water at (1,64,0), a low shore at (2,63,0)
        still = FakeRegion({(0, 64, 0): "water", (1, 64, 0): "water", (0, 63, 0): "stone", (1, 63, 0): "stone",
                            (2, 62, 0): "stone", (0, 64, 1): "stone"}, (-6, 58, -6), (6, 70, 6),
                           {(0, 64, 0): {"level": "0"}, (1, 64, 0): {"level": "3"}})
        table(self, [
            ("a shore the swim reaches: its nearest standing cell", lambda: _route(_sea(shores=[(5, 8)])),
             ("land", (5, 1, 0))),
            ("must fail: a rock under water is not land", lambda: _route(rock), ("land", (5, 1, 0))),
            ("a shore behind a wall higher than the water: pillar", lambda: _route(_sea(shores=[(6, 8)], walls=[5])),
             ("pillar", (0, 1, 0))),
            ("open sea: pillar at the surface, saying why", lambda: TN.air_route(_sea(), (0, 0, 0)),
             lambda g: g[:2] == ("pillar", (0, 1, 0)) and "no land" in g[2]),
            ("two shores, the nearer walled off: the reachable one",
             lambda: _route(_sea(shores=[(-8, -3), (6, 8)], walls=[-2])), ("land", (6, 1, 0))),
            ("fill: dry stand within reach of still water",
             lambda: FL.fill_spot(FakeRegion(lake, (-4, 55, -3), (5, 64, 3)), (-3, 61, 0)),
             lambda f: f is not None and lake.get(f[1]) == "water" and lake.get(f[0], "air") == "air"),
            ("must fail: never fill through a wall",
             lambda: FL.fill_spot(FakeRegion(walled, (-74, 10, 304), (-68, 15, 311)), (-71, 12, 307)),
             lambda f: f is None or f[0][2] >= 309 or f[0][1] >= 14),
            ("fill: the stand never below the surface, the source's own cell", lambda: FL.fill_spot(still, (2, 63, 0)),
             lambda f: f is not None and f[1] == (0, 64, 0) and f[0][1] >= 64),
            ("the aim is the source's top face", lambda: FL.surface_aim((0, 64, 0)), (0.5, 64.95, 0.5)),
        ])


# ---------------------------------------------------------------- memory, estimates, jobs, reviews
class MemoryAndReview(unittest.TestCase):
    def test_durations(self):
        # measured seconds per unit (Memory.duration, what the cost model reads back once there are enough runs)
        m = memory()
        table(self, [
            ("no runs: nothing measured", lambda: m.duration("chop"), None),
            ("must fail: two runs are not trusted yet", lambda: [m.record_duration("chop", 40, 10) for _ in range(2)]
             and m.duration("chop"), None),
            ("the third run: seconds per unit", lambda: m.record_duration("chop", 40, 10) or m.duration("chop"),
             lambda d: abs(d - 4.0) < 1e-9),
            ("the moving average leans on history (4·0.7 + 10·0.3)",
             lambda: m.record_duration("chop", 100, 10) or m.duration("chop"), lambda d: abs(d - 5.8) < 1e-9),
            ("boundary: one run trusted when asked for one", lambda: m.record_duration("smelt", 21, 2) or m.duration(
                "smelt", min_samples=1), lambda d: abs(d - 10.5) < 1e-9)])

    def test_memory(self):
        m = memory()
        m.add_job("furnace", (1, 2, 3), "minecraft:overworld", "minecraft:iron_ingot", 8, time.time() + 85, True)
        job = m.jobs()[0]
        dup = memory()
        for _ in range(2):
            dup.add_station("crafting_table", (1, 2, 3), "overworld")
            dup.note_seen("iron_ore", (5, 5, 5), "overworld")
        felled = memory()
        felled.clock = 0
        felled.note_seen("tree", (10, 64, 10), "overworld")
        felled.forget_seen("tree", (10, 64, 10), "overworld")
        built = memory()
        built.data["builds"] = {"nether_portal": {"origin": [-367, 119, 191], "turns": 0,
                                                  "dimension": "minecraft:overworld"}}
        died = memory()
        died.log_death((1, 64, 1), "minecraft:overworld")
        ctx = Context(None, None, "minecraft:overworld", blacklist={})
        ctx.ban_counts = {}
        ctx.ban((90, 64, 0))
        table(self, [
            ("a job's output counts as pending", lambda: m.pending_outputs("minecraft:overworld"),
             {"minecraft:iron_ingot": 8}),
            ("must fail: a job is not ready before its estimate", lambda: WD.job_ready(job), False),
            ("a job is ready once the estimate passed",
             lambda: m.postpone_job(job["id"], -1) or WD.job_ready(m.jobs()[0]), True),
            ("boundary: a finished job stops counting",
             lambda: m.finish_job(job["id"]) or m.pending_outputs("minecraft:overworld"), {}),
            ("stations and sightings deduplicated",
             lambda: (len(dup.data["stations"]), len(dup.seen("iron_ore", "overworld"))), (1, 1)),
            ("a grove we felled is forgotten", lambda: felled.seen("tree", "overworld"), []),
            ("an unfinished build's cells are protected from mining",
             lambda: (-367, 120, 191) in built.protected_cells("minecraft:overworld"), True),
            ("a recent death is recoverable for 5 minutes", lambda: (
                bool(died.recent_death("minecraft:overworld")),
                bool(died.recent_death("minecraft:overworld", now=time.time() + 400))), (True, False)),
            ("must fail: a failed trek's site is banned, another is not",
             lambda: (ST.site_trek_ok(ctx, {"name": "far", "pos": [90, 64, 0]}),
                      ST.site_trek_ok(ctx, {"name": "home", "pos": [5, 64, 0]})), (False, True)),
        ])

    def test_review(self):
        lines = ["12:01:00 === stock coal (score 0.1; plan: x)\n", "12:06:00 === stock coal (score 0.1; plan: x)\n",
                 "12:07:00 !! hunt: no progress\n", "12:08:00 ~~ tidy: in a shaft\n", "12:09:00 ?? idle 15s\n",
                 "12:09:30 survival: find air\n", "11:50:00 === old goal (score 1)\n"]
        entries = RV.recent_lines(lines, 5, datetime.datetime(2026, 9, 15, 12, 10, 0))
        still = [{"t": 1000 + 60 * i, "pos": [-9, 37, 257], "done": ["stone pickaxe"]} for i in range(30)]
        moved = still[:-1] + [{"t": 1000 + 60 * 29, "pos": [40, 60, 200], "done": ["stone pickaxe", "iron pickaxe"]}]
        loop = [(None, "night in the water: swimming to land first")] * 12 + \
            [(None, "  goto      failed    no path")] * 9
        table(self, [
            ("only the last 5 minutes", lambda: (len(entries), RV.summarize(entries)["goals"]["stock coal"]), (5, 1)),
            ("failures, help and survival are grouped", lambda: RV.summarize(entries),
             lambda s: s["failures"]["hunt"] == 1 and s["help"] and s["survival"]),
            ("must fail: 30 minutes in one block is STALLED", lambda: RV.macro(still, 30, 2800),
             lambda t: "STALLED" in t),
            ("movement and a new goal aren't a stall", lambda: RV.macro(moved, 30, 2800),
             lambda t: "STALLED" not in t and "iron pickaxe" in t),
            ("a decision loop shows as one repeated pattern", lambda: RV.repeated(loop),
             lambda t: "×12 night in the water" in t and "goto" not in t),
        ])


# ---------------------------------------------------------------- nav, nether, roads, clock
class NavAndNether(unittest.TestCase):
    def test_table(self):
        col = {(5, y, 9) for y in range(40, 71)}
        roads = []
        ROADS.add_leg(roads, (0, 70, 0), (200, 70, 0), 30.0, 1)
        ROADS.add_leg(roads, (0, 70, 0), (200, 70, 0), 45.0, 2)
        table(self, [
            ("hunting never digs, keeps the rest of the policy",
             lambda: EX.approach_policy(nav.Policy(protected={(1, 2, 3)})),
             lambda p: not p.allow_dig and p.allow_build and p.protected == {(1, 2, 3)}),
            # real case 08:07: a 200-block trek bridged dips until all 64 cobblestone were gone
            ("a full bag keeps a block reserve, a small kit still gets half",
             lambda: [nav.place_budget(n) for n in (64, 40, 33, 16, 8, 0)], [48, 24, 17, 8, 4, 0]),
            ("a guessed target y moves onto its column's ground",
             lambda: nav.ground_in_column(lambda p: p in col, 5, 9, 87), 71),
            ("must fail: a column with no ground", lambda: nav.ground_in_column(lambda p: False, 5, 9, 87), None),
            ("a repeated road leg keeps its fastest time and newest use",
             lambda: [(leg["s"], leg["used"]) for leg in roads], [(30.0, 2)]),
            ("two throws triangulate", lambda: NT.triangulate((0, 0), (1, 0), (100, -100), (0, 1)), (100, 0)),
            ("must fail: parallel throws don't", lambda: NT.triangulate((0, 0), (1, 0), (0, 50), (1, 0)), None),
            ("must fail: rays meeting behind a thrower don't",
             lambda: NT.triangulate((0, 0), (1, 0), (-100, -100), (0, 1)), None),
            ("piglins: gold armor first", lambda: NT.barter_ready(LInv({"minecraft:gold_ingot": 5}), [None] * 4),
             "wear a piece of gold armor first"),
            ("piglins: helmet worn, ingots carried", lambda: NT.barter_ready(
                LInv({"minecraft:gold_ingot": 5}), ["minecraft:golden_helmet", None, None, None]), None),
            ("nether kit: the real bag (4 food, no helmet, 35 slots) lacks 4 things",
             lambda: len(nether_kit_missing(LInv({"minecraft:cooked_beef": 4}, 35))), 4),
            ("nether kit: food, 32 blocks, helmet worn, room: ready", lambda: nether_kit_missing(
                LInv({"minecraft:cooked_beef": 12, "building": 40}, 28, head="minecraft:golden_helmet")), []),
            ("must fail: no blocks, not ready", lambda: nether_kit_missing(
                LInv({"minecraft:cooked_beef": 12}, 20, head="minecraft:golden_helmet")),
             lambda m: any("blocks" in x for x in m)),
            ("exploration legs stay above the lava sea", lambda: 50 <= NT.EXPLORE_Y <= 100, True),
            ("dusk: the morning counts down to 12500", lambda: ticks_until_dusk(1000), 11500),
            ("boundary: dusk itself is 0", lambda: ticks_until_dusk(12500), 0),
            ("must fail: dawn (after 23400) has a whole day ahead, not 0", lambda: ticks_until_dusk(23600), 12900),
        ])


# ---------------------------------------------------------------- farming, loot, brewing, upkeep, screens
class WikiSkills(unittest.TestCase):
    def test_table(self):
        field = {(x, 63, z): "grass_block" for x in range(-5, 6) for z in range(-5, 6)} | {(0, 63, 0): "stone"}
        wheat = FakeRegion({(0, 64, 0): "wheat", (1, 64, 0): "wheat"}, (-1, 63, -1), (1, 65, 1),
                           {(0, 64, 0): {"age": "7"}, (1, 64, 0): {"age": "3"}})
        herd = [{"id": 1, "type": "minecraft:cow", "x": 0, "y": 64, "z": 0},
                {"id": 2, "type": "minecraft:cow", "x": 3, "y": 64, "z": 2},
                {"id": 3, "type": "minecraft:sheep", "x": 40, "y": 64, "z": 0}]
        chest = [{"owner": "chest", "slot": 0, "id": "minecraft:rotten_flesh"},
                 {"owner": "chest", "slot": 1, "id": "minecraft:obsidian"},
                 {"owner": "chest", "slot": 2, "id": "minecraft:gold_ingot"},
                 {"owner": "player", "slot": 30, "id": "minecraft:diamond"}]
        tools = [{"id": "minecraft:stone_pickaxe", "slot": 3, "damage": 120, "maxDamage": 131},
                 {"id": "minecraft:stone_pickaxe", "slot": 4, "damage": 110, "maxDamage": 131},
                 {"id": "minecraft:diamond_pickaxe", "slot": 5, "damage": 100, "maxDamage": 1561}]
        brew = {"minecraft:potion:water": 3, "minecraft:nether_wart": 1, "minecraft:magma_cream": 1,
                "minecraft:blaze_powder": 1}
        table(self, [
            ("the plot's centre is soil with soil all around",
             lambda: FM.farm_plot(FakeRegion(field, (-5, 60, -5), (5, 66, 5)), (0, 64, 0)),
             lambda c: c is not None and all(field.get((c[0] + dx, 63, c[2] + dz)) == "grass_block"
                                             for dx in (-1, 0, 1) for dz in (-1, 0, 1))),
            ("only age-7 wheat is ripe", lambda: FM.ripe_cells(wheat), [(0, 64, 0)]),
            ("a breeding pair: two close adults of one kind", lambda: FM.breeding_pair(herd, "minecraft:cow"), (1, 2)),
            ("must fail: one sheep is no pair", lambda: FM.breeding_pair(herd, "minecraft:sheep"), None),
            ("loot: worth a slot, dearest first, never the player's own", lambda: LT.loot_plan(
                chest, {"minecraft:obsidian": 300.0, "minecraft:gold_ingot": 500.0, "minecraft:rotten_flesh": 0.0}, 30),
             [2, 1]),
            ("repair: two worn stone pickaxes combine", lambda: UK.repair_pair(tools, "pickaxe"),
             ("minecraft:stone_pickaxe", 3, 4)),
            ("must fail: a single tool can't", lambda: UK.repair_pair(tools[2:], "pickaxe"), None),
            ("recipes the wiki skills craft exist", lambda: [k for k in (
                "minecraft:stone_hoe", "minecraft:golden_helmet", "minecraft:brewing_stand", "minecraft:glass_bottle",
                "minecraft:magma_cream", "minecraft:paper", "minecraft:book", "minecraft:enchanting_table",
                "minecraft:anvil") if k not in RECIPES], []),
        ])

    def test_screens(self):
        opts = [{"cost": 3}, {"cost": 12}, {"cost": 30}]
        pearls = [{"sell": "minecraft:ender_pearl", "buy": "minecraft:emerald", "buyCount": 9},
                  {"sell": "minecraft:ender_pearl", "buy": "minecraft:emerald", "buyCount": 5},
                  {"sell": "minecraft:ender_pearl", "buy": "minecraft:emerald", "buyCount": 3, "disabled": True},
                  {"sell": "minecraft:bread", "buy": "minecraft:emerald", "buyCount": 1}]
        emeralds = [{"sell": "minecraft:emerald", "buy": "minecraft:wheat", "buyCount": 20},
                    {"sell": "minecraft:emerald", "buy": "minecraft:stick", "buyCount": 32},
                    {"sell": "minecraft:emerald", "buy": "minecraft:paper", "buyCount": 24, "disabled": True},
                    {"sell": "minecraft:bread", "buy": "minecraft:emerald", "buyCount": 1}]

        def sell(bag):
            return UI.choose_trade(emeralds, "minecraft:emerald", bag)
        table(self, [
            ("enchant: the best affordable option", lambda: UI.choose_enchant(opts, 15, 3), 1),
            ("enchant: lapis limits the button", lambda: UI.choose_enchant(opts, 40, 1), 0),
            ("must fail: enchant, nothing affordable", lambda: UI.choose_enchant(opts, 2, 3), None),
            ("trade: the cheapest enabled affordable offer",
             lambda: UI.choose_trade(pearls, "minecraft:ender_pearl", {"minecraft:emerald": 6}), 1),
            ("must fail: trade, can't pay",
             lambda: UI.choose_trade(pearls, "minecraft:ender_pearl", {"minecraft:emerald": 2}), None),
            ("selling: 20 wheat, the wheat offer", lambda: sell({"minecraft:wheat": 20}), 0),
            ("must fail: selling, one wheat short", lambda: sell({"minecraft:wheat": 19}), None),
            ("selling: sticks only, the stick offer", lambda: sell({"minecraft:stick": 40}), 1),
            ("must fail: selling paper to a disabled offer", lambda: sell({"minecraft:paper": 64}), None),
            ("selling: wheat and sticks, the cheaper payment",
             lambda: sell({"minecraft:wheat": 20, "minecraft:stick": 40}), 0),
            ("anvil: affordable and not too expensive", lambda: UI.anvil_ok(8, 10), True),
            ("must fail: anvil, can't afford", lambda: UI.anvil_ok(12, 10), False),
            ("must fail: anvil, too expensive", lambda: UI.anvil_ok(40, 50), False),
        ])


class FarmVerify(unittest.TestCase):
    def test_table(self):
        """plant_farm's verdict reads recorded world state: bag counts at start vs now, the plot's blocks."""
        bag = lambda wheat, seeds: (lambda: (wheat, seeds))
        grows = {(0, 64, 0)}
        growing = lambda centre: centre in grows
        table(self, [
            ("reaped: wheat went up since the start", lambda: FM.farm_done(FM.REAPED, (0, 3), bag(2, 3), growing), True),
            ("reaped: only seeds went up still counts", lambda: FM.farm_done(FM.REAPED, (4, 1), bag(4, 5), growing), True),
            ("must fail: REAPED returned but the bag is unchanged", lambda: FM.farm_done(FM.REAPED, (4, 1), bag(4, 1), growing), False),
            ("must fail: REAPED with no start snapshot", lambda: FM.farm_done(FM.REAPED, None, bag(9, 9), growing), False),
            ("planted: the plot at the centre has farmland and wheat", lambda: FM.farm_done((0, 64, 0), (0, 0), bag(0, 0), growing), True),
            ("must fail: a centre whose plot holds no wheat", lambda: FM.farm_done((5, 64, 5), (0, 0), bag(0, 0), growing), False),
            ("must fail: no centre at all", lambda: FM.farm_done(None, (0, 0), bag(0, 0), growing), False),
        ])

# ---------------------------------------------------------------- combat


class Combat(unittest.TestCase):
    def test_table(self):
        fort = {(x, 64, z): "nether_bricks" for x in range(-5, 6) for z in range(-5, 6)}
        fort.update({(2, 65, 0): "nether_bricks", (2, 66, 0): "nether_bricks"})
        # real cases 07:57/07:58: both dragon benches died ~9 blocks in front of a perched dragon
        near = [{"type": "minecraft:ender_dragon", "x": 0, "y": 64, "z": 0, "health": 200.0},
                {"type": "minecraft:ender_dragon", "x": 5, "y": 66, "z": 0},
                {"type": "minecraft:area_effect_cloud", "x": 7, "y": 64, "z": 0},
                {"type": "minecraft:item", "x": 8, "y": 64, "z": 0}]
        hz = CM.hazard_points(near)
        ender = [{"type": "minecraft:enderman", "id": 7, "x": 6, "y": 64, "z": 0, "angry": True},
                 {"type": "minecraft:enderman", "id": 8, "x": 3, "y": 64, "z": 0},
                 {"type": "minecraft:end_crystal", "id": 9, "x": 0, "y": 80, "z": 20}]
        table(self, [
            ("bow: aims above the target to cover the drop", lambda: CB.bow_aim((0, 65.6, 0), (30, 64, 0), height=1.0),
             lambda a: a[1] > 65.0 and a[0] == 30),
            ("bow: farther targets need more lift", lambda: (CB.bow_aim((0, 65.6, 0), (60, 64, 0))[1],
                                                             CB.bow_aim((0, 65.6, 0), (30, 64, 0), height=1.0)[1]),
             lambda ys: ys[0] > ys[1]),
            ("hazards carry their own reach — head 8, breath 6 — items are none", lambda: hz,
             [((0, 64, 0), 8.0), ((5, 66, 0), 8.0), ((7, 64, 0), 6.0)]),
            ("an aim through an enderman's head provokes it",
             lambda: CM.aim_hits_enderman((12, 69, 0), (0, 64, 0), ender), True),
            ("must fail: the same direction below the head",
             lambda: CM.aim_hits_enderman((12, 64, 0), (0, 64, 0), ender), False),
            ("must fail: an aim elsewhere", lambda: CM.aim_hits_enderman((0, 80, 20), (0, 64, 0), ender), False),
        ])


# ---------------------------------------------------------------- the End
RING = [(10 + dx, 60, 8) for dx in (-1, 0, 1)] + [(10 + dx, 60, 12) for dx in (-1, 0, 1)] + \
       [(8, 60, 10 + dz) for dz in (-1, 0, 1)] + [(12, 60, 10 + dz) for dz in (-1, 0, 1)]


def _eyes(missing, solid, lit, block):
    """eye_plan's (floor to fill, stand, how many eyes) when every eye is in reach of the stand; or its refusal."""
    try:
        floor, stand, frames = END.eye_plan(missing, (10, 60, 10), solid, lit, block)
    except Exception as e:     # noqa: BLE001  (the refusal's words are the answer)
        return "no block" if "no block" in str(e) else str(e)
    reach = all(math.dist((stand[0] + .5, stand[1] + 1.62, stand[2] + .5), (f[0] + .5, f[1] + .8125, f[2] + .5))
                <= 4.5 for f in frames)
    return (floor, stand, len(frames)) if reach else "out of reach"


class End(unittest.TestCase):
    def test_table(self):
        frames = FakeRegion({(0, 30, 0): "end_portal_frame", (1, 30, 0): "end_portal_frame",
                             (4, 30, 4): "end_portal_frame"}, (-1, 29, -1), (5, 31, 5),
                            {(0, 30, 0): {"eye": "true"}, (1, 30, 0): {"eye": "false"}, (4, 30, 4): {"eye": "false"}})
        search = END.search_points((100, -40))
        table(self, [
            ("frames without an eye", lambda: sorted(END.frames_missing_eye(frames)), [(1, 30, 0), (4, 30, 4)]),
            ("the portal centre of the frame ring",
             lambda: END.portal_centre([(0, 30, 1), (4, 30, 3), (2, 30, 0), (2, 30, 4)]), (2, 30, 2)),
            ("lava in the middle: block it, stand there, all 12", lambda: _eyes(RING, False, False, True),
             ((10, 59, 10), (10, 60, 10), 12)),
            ("9 eyes in: only the 3 missing", lambda: _eyes(RING[:3], True, False, True), (None, (10, 60, 10), 3)),
            ("must fail: no block over the lava, a reason", lambda: _eyes(RING, False, False, False), "no block"),
            ("lit already: nothing", lambda: _eyes(RING[:3], True, True, True), (None, None, 0)),
            ("boundary: no frame missing, nothing", lambda: _eyes([], True, False, True), (None, None, 0)),
            # real case 05:04: standing on the frame to place an eye slid into the opening's lava
            ("eyes are placed from outside the ring",
             lambda: (END.outside_spot((10, 60, 8), (10, 60, 10)), END.outside_spot((12, 60, 11), (10, 60, 10))),
             ((10, 60, 7), (13, 60, 11))),
            ("the End fight skills take cover and retry", lambda: [f.__name__ for f in (
                DR.build_bed_pit, DR.await_perch, DR.bed_bomb_window, DR.shake_enderman, DR.slay_dragon)
                if not f.contract.soft], []),
            ("must fail: a plain skill ends on an interrupt", lambda: SV.eat.contract.soft, False),
            ("stronghold search: the estimate at room depth, then the first ring of 8",
             lambda: (search[0], len(search), all(max(abs(p[0] - 100), abs(p[2] + 40)) == 40 for p in search[1:9])),
             ((100, 30, -40), 49, True)),
            ("follow the nearest brick not visited",
             lambda: END.next_brick([(0, 30, 0, 5.0), (40, 30, 0, 9.0)], [(2, 30, 1)]), (40, 30, 0, 9.0)),
            ("must fail: every brick visited", lambda: END.next_brick([(0, 30, 0, 5.0)], [(1, 30, 0)]), None),
        ])


# ---------------------------------------------------------------- scenario bench bookkeeping
def _record(*runs, name="s", code="c"):
    """A readiness table after `runs`: (passed, seconds[, class]) each."""
    t = {}
    for ok, s, *cls in runs:
        if cls:
            runner.record(t, name, code, ok, s, "SETUP_INVALID" if cls[0] == "setup" else "", cls=cls[0])
        else:
            runner.record(t, name, code, ok, s)
    return t


def _mod_hashes():
    src = tempfile.mkdtemp(prefix="bonobo-modsrc-")
    try:
        for rel in runner.MOD_CORE + [f for fs in runner.MOD_FILES.values() for f in fs]:
            p = os.path.join(src, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(rel)
        return runner.mod_hash(["craft"], java=src), runner.mod_hash(["craft"], java=src), runner.mod_hash(None, java=src)
    finally:
        shutil.rmtree(src, ignore_errors=True)


def _waits(*chosen):
    picks = collections.Counter()
    for k in chosen:
        arbiter.note_pick(picks, arbiter.Intent("plan", lambda: None, k, at=0.0, kind=k, key=k) if k else None)
    return words_runs.slice_report([], [], None, 0, picks)["waits"]


def _refused_without_flag():
    with mock.patch.object(runner, "FLAG", os.path.join(tempfile.mkdtemp(prefix="noflag"), "test-world")):
        try:
            runner.run_named("gather_logs", None)
        except RuntimeError:
            return True
    return False


class Bench(unittest.TestCase):
    def test_readiness(self):
        cast = _record((True, 40), (False, 90), (True, 50), name="cast_obsidian", code="abc")
        demoted = _record((True, 40), (False, 90), (True, 50), (False, 99), (False, 99), name="cast_obsidian",
                          code="abc")
        setup = _record(*[(False, 0, "setup")] * 3, (True, 2, "pass"), (True, 3, "pass"), (False, 0, "setup"),
                        name="fill_water_bucket", code="h")
        stable = _record((True, 10), (True, 11), name="activate_end_portal", code="old")
        unsettled = _record((True, 10), (True, 11), name="activate_end_portal", code="old")
        runner.record(unsettled, "activate_end_portal", "new-jar", False, 60)
        table(self, [
            ("2 of the last 3 passed: ready, the median of passes", lambda: runner.status(cast, "cast_obsidian", "abc"),
             ("scenario", 50)),
            ("a new code hash starts untested", lambda: runner.status(cast, "cast_obsidian", "new")[0], "untested"),
            ("must fail: recent failures demote it", lambda: runner.status(demoted, "cast_obsidian", "abc")[0], "failing"),
            ("setup failures don't count against a skill", lambda: runner.status(setup, "fill_water_bucket", "h"),
             ("scenario", 3)),
            ("must fail: one pass of a chance row is no verdict yet (run again)",
             lambda: runner.verdict(_record((True, 1)), "s", "c"), None),
            ("two passes of a chance row: a pass", lambda: runner.verdict(_record((True, 1), (True, 1)), "s", "c"), "pass"),
            ("one failure is no verdict yet", lambda: runner.verdict(_record((False, 1)), "s", "c"), None),
            ("must fail: two counted fails, setup ignored",
             lambda: runner.verdict(_record((False, 1, "setup"), (False, 1), (False, 1)), "s", "c"), "fail"),
            ("a crafting-only row's key ignores pathfinder sources, and is stable",
             _mod_hashes, lambda h: h[0] == h[1] != h[2]),
            ("with no mod sources the key is the jar version, never a constant",
             lambda: runner.mod_hash(["craft"], java=""), lambda k: k.startswith("jar-")),
            ("keyed by the skill's transitive modules", lambda: runner.module_deps("fluids"),
             lambda d: "nav" in d and "api" in d and "scenarios" not in d),
        ])

    def test_rows_and_replies(self):
        feedback = ["Set the time to 1000", "No entity was found", "That position is not loaded",
                    "Incorrect argument for command", "gamerule doMobSpawning false<--[HERE]",
                    "Target has no effects to remove", "No blocks were filled"]
        lava = {core.at(dx, -1, dz): "lava" for dx in range(-2, 3) for dz in range(-2, 3)}
        stronghold = [{"cmd": "execute in minecraft:overworld run locate structure minecraft:stronghold",
                       "reply": ["The nearest minecraft:stronghold is at [10456, ~, 9832] (484 blocks away)"]}]
        table(self, [
            ("every row has setup, run, check, a budget, a doc, a module and a signature",
             lambda: [n for n, s in SC.SCENARIOS.items() if not ({"setup", "run", "check", "budget", "doc"} <= set(s)
                                                                  and s.get("module")
                                                                  and (s.get("expect") or s.get("raw")))], []),
            ("must fail: never run without the test-world flag", _refused_without_flag, True),
            # real case 03:38: every /fill answered "That position is not loaded"
            ("feedback errors caught, harmless replies not", lambda: runner.feedback_errors(feedback), feedback[2:5]),
            ("the exact signature matches the lava box", lambda: runner.setup_mismatches(
                lava, [(core.at(-2, -1, -2), core.at(2, -1, 2), "lava", 25, 25)]), lambda m: not m),
            ("must fail: natural terrain in the box is a mismatch", lambda: runner.setup_mismatches(
                {core.at(3, 0, 1): "grass_block"}, [(core.at(-6, 0, -6), core.at(6, 4, 6), "*", 0, 0)]), lambda m: bool(m)),
            ("failure classes", lambda: (runner.classify(core.SetupInvalid("x"), False), runner.classify(NavFailed("x"), False),
                                         runner.classify(ValueError("x"), False), runner.classify(None, True)),
             ("setup", "nav", "skill", "pass")),
            ("slice waits: a wait for day chosen", lambda: _waits("wait for day"), 1),
            ("slice waits: two waits beside idle stocking", lambda: _waits("wait for day", "idle", "wait for day"), 2),
            ("must fail: idle stocking and a task are work, not waits", lambda: _waits("idle", "queue", "idle"), 0),
            ("boundary: nothing chosen, nothing counted", lambda: (_waits(None), _waits()), (0, 0)),
            ("the server-side entity count",
             lambda: (core.server_count(["Test passed. Count: 3"]), core.server_count(["Test failed"])), (3, 0)),
            ("a /locate reply", lambda: words_runs.locate_reply(stronghold), (10456, 9832)),
            # real case 03:43: go_to returned False after 3 "target unreachable" travels, classed "skill"
            ("a silent failure after a failed travel is a nav failure", lambda: type(runner.silent_failure(
                ["03:43:31", "  travel    failed    target unreachable; stopped at the closest"], False)).__name__,
             "NavFailed"),
            ("any other silent failure is the skill's", lambda: type(runner.silent_failure(
                ["  mine_many failed    5 of 6 steps failed"], None)).__name__, "McError"),
            ("the lava variants keep the far platform inside the box",
             lambda: [n for n in ("cross_lava_3", "cross_lava_8", "cross_lava_lake")
                      if SC.SCENARIOS[n]["expect"][2][1][0] > core.at(*core.BOX[1])[0]], []),
        ])


if __name__ == "__main__":
    unittest.main()

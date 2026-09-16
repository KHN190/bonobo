"""Scenario bench for the 30-minute speedrun: set up a situation with commands (test world only), run one skill the
normal way, check the outcome, time it, and record it in the readiness table.

Rules
- Commands (/tp, /give, /setblock, /fill, /time, /effect) are used ONLY to build the scenario, and ONLY when the
  test-world flag file exists (`mc.py scenario enable` after the user confirms a cheats-on test world). The skill
  under test runs exactly as in a real run — no cheats inside it.
- A setup is only valid when every command's chat feedback (read from the client log) is free of errors and the
  scenario's signature blocks are exactly there. Otherwise the run is SETUP_INVALID and never enters the table.
- Failures are classed setup / harness / nav / mod / skill; only nav and skill runs count for readiness.
- Readiness is keyed by the hash of the skill's module and every bonobo module it imports (transitively), so an
  unrelated edit doesn't invalidate it. Ready = ≥ 2 of the last 3 counted runs passed within budget.
- Each failed run writes `bench/<scenario>/<time>/report.json` (feedback, mismatches, 5 Hz trace, log lines,
  inventory, region) so one read locates the cause without re-running.
"""
import ast
import hashlib
import io
import json
import os
import sys
import threading
import time
from . import paths

FLAG = paths.data("test-world")
TABLE = paths.data("readiness.json")
NOTES = paths.data("test-world-notes.json")
BENCH = paths.data("bench")
ORIGIN = (10000, 200, 10000)   # a sky platform: skills search 48 blocks, natural terrain (y ≤ ~120) stays out of it
PKG = os.path.dirname(__file__)
# Everything a scenario touches lies inside this box (cleared to air before each setup, force-loaded).
BOX = ((-10, -4, -10), (20, 9, 10))
UNCOUNTED = ("setup", "harness")


def at(dx, dy, dz, origin=ORIGIN):
    return origin[0] + dx, origin[1] + dy, origin[2] + dz


def _c(p):
    return f"{p[0]} {p[1]} {p[2]}"


# name → module (for the readiness hash), setup commands (relative to ORIGIN), expected signature blocks
# [(lo, hi, block or "*" for any non-air, min, max)], the skill call, the success check and a time budget (s).
SCENARIOS = {
    "cast_obsidian": {
        "doc": "A 5×5 still lava pool in a stone floor; water bucket in hand → obsidian appears.",
        "module": "fluids",
        "setup": [f"fill {_c(at(-6, -3, -6))} {_c(at(6, -1, 6))} stone",
                  f"fill {_c(at(-2, -1, -2))} {_c(at(2, -1, 2))} lava",
                  f"tp @p {_c(at(-5, 0, 0))}",
                  "clear @p", "give @p water_bucket", "give @p diamond_pickaxe"],
        "expect": [(at(-2, -1, -2), at(2, -1, 2), "lava", 25, 25),
                   (at(-6, -1, -6), at(6, -1, 6), "stone", 144, 144),
                   (at(-6, 0, -6), at(6, 4, 6), "*", 0, 0)],
        "run": lambda ctx: __import__("bonobo.fluids", fromlist=["cast_obsidian"]).cast_obsidian(ctx),
        "check": lambda api, inv: _count_blocks(api, at(-3, -2, -3), at(3, 0, 3), "obsidian") >= 10,
        "budget": 90,
    },
    "build_light_portal": {
        "doc": "Flat stone ground, 10 obsidian + 4 cobblestone + flint and steel → a lit nether portal.",
        "module": "building",
        "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} stone",
                  f"tp @p {_c(at(0, 0, 0))}",
                  "clear @p", "give @p obsidian 10", "give @p cobblestone 16", "give @p flint_and_steel"],
        "expect": [(at(-8, -1, -8), at(8, -1, 8), "stone", 289, 289),
                   (at(-8, 0, -8), at(8, 6, 8), "*", 0, 0)],
        "run": lambda ctx: __import__("bonobo.building", fromlist=["build_blueprint"]).build_blueprint(
            ctx, "nether_portal", at(0, 0, 0)),
        "check": lambda api, inv: _count_blocks(api, at(-8, 0, -8), at(8, 6, 8), "nether_portal") >= 6,
        "budget": 60,
    },
    "fill_water_bucket": {
        "doc": "A 3×3 pond next to the player, empty bucket → water bucket.",
        "module": "fluids",
        "setup": [f"fill {_c(at(-4, -2, -4))} {_c(at(4, -1, 4))} stone",
                  f"fill {_c(at(1, -1, -1))} {_c(at(3, -1, 1))} water",
                  f"tp @p {_c(at(-1, 0, 0))}", "clear @p", "give @p bucket"],
        "expect": [(at(1, -1, -1), at(3, -1, 1), "water", 9, 9),
                   (at(-4, -1, -4), at(4, -1, 4), "stone", 72, 72),
                   (at(-4, 0, -4), at(4, 3, 4), "*", 0, 0)],
        "run": lambda ctx: __import__("bonobo.fluids", fromlist=["fill_water_bucket"]).fill_water_bucket(ctx),
        "check": lambda api, inv: inv.count("minecraft:water_bucket") >= 1,
        "budget": 30,
    },
    "cross_lava_lake": {
        "doc": "A 12-block lava strip between two stone platforms; cobblestone in the bag → reach the far side alive.",
        "module": "nav",
        "setup": [f"fill {_c(at(-3, -3, -4))} {_c(at(18, -3, 4))} stone",
                  f"fill {_c(at(-2, -2, -3))} {_c(at(16, -1, 3))} lava",
                  f"fill {_c(at(-2, -1, -3))} {_c(at(1, -1, 3))} stone",
                  f"fill {_c(at(14, -1, -3))} {_c(at(17, -1, 3))} stone",
                  f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p cobblestone 64", "give @p diamond_pickaxe"],
        "expect": [(at(2, -1, -3), at(13, -1, 3), "lava", 84, 84),
                   (at(-2, -1, -3), at(1, -1, 3), "stone", 28, 28),
                   (at(-2, 0, -3), at(17, 4, 3), "*", 0, 0)],
        "run": lambda ctx: __import__("bonobo.nav", fromlist=["go_to"]).go_to(at(15, 0, 0), ctx.policy, range_=1.5),
        "check": lambda api, inv: _near(api, at(15, 0, 0), 2.5) and api.get("/state")["health"] > 10,
        "budget": 60,
    },
    "gather_logs": {
        "doc": "A small grove; empty bag → 4 logs (speed wood).",
        "module": "wood",
        "setup": [f"fill {_c(at(-8, -3, -8))} {_c(at(8, -2, 8))} dirt",
                  f"fill {_c(at(-8, -1, -8))} {_c(at(8, -1, 8))} grass_block",
                  f"place feature minecraft:oak {_c(at(4, 0, 0))}",
                  f"place feature minecraft:oak {_c(at(-4, 0, 3))}",
                  f"tp @p {_c(at(0, 0, 0))}", "clear @p"],
        "expect": [(at(-8, -1, -8), at(8, -1, 8), "grass_block", 285, 289),
                   (at(-8, 0, -8), at(8, 8, 8), "oak_log", 6, 20)],
        "run": lambda ctx: __import__("bonobo.wood", fromlist=["chop"]).chop(ctx, 4),
        "check": lambda api, inv: inv.count("log") >= 4,
        "budget": 45,
    },
    "enter_nether": {
        "doc": "A lit 4×5 portal on stone ground → stand in it and arrive in the Nether.",
        "module": "nether",
        "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone",
                  f"fill {_c(at(-1, 0, 2))} {_c(at(2, 4, 2))} obsidian",
                  f"fill {_c(at(0, 1, 2))} {_c(at(1, 3, 2))} nether_portal[axis=x]",
                  f"tp @p {_c(at(0, 0, -3))}", "clear @p", "give @p flint_and_steel"],
        "expect": [(at(0, 1, 2), at(1, 3, 2), "nether_portal", 6, 6),
                   (at(-1, 0, 2), at(2, 4, 2), "obsidian", 14, 14)],
        "run": lambda ctx: __import__("bonobo.nether", fromlist=["use_portal"]).use_portal(ctx, "minecraft:the_nether"),
        "check": lambda api, inv: api.get("/state")["dimension"] == "minecraft:the_nether",
        "budget": 20,
    },
    "barter_piglin": {
        "doc": "Nether platform, 3 piglins, 8 gold ingots and a carried gold helmet → wear it, barter, collect trades.",
        "module": "nether",
        "dimension": "minecraft:the_nether",
        "combat": True,
        "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} netherrack",
                  f"fill {_c(at(-9, 0, -9))} {_c(at(9, 4, 9))} glass hollow",
                  f"fill {_c(at(-8, 0, -8))} {_c(at(8, 3, 8))} air",
                  # Gold boots on during setup: with a bare body the summoned piglins attacked before the skill
                  # started (hp 15, setup invalid). The helmet stays in the bag: wearing it is part of the skill.
                  f"tp @p {_c(at(0, 0, 0))}", "clear @p", "item replace entity @p armor.feet with golden_boots",
                  "give @p gold_ingot 8", "give @p golden_helmet", "give @p iron_sword",
                  f"summon piglin {_c(at(4, 0, 0))} {{PersistenceRequired:1b}}",
                  f"summon piglin {_c(at(-4, 0, 2))} {{PersistenceRequired:1b}}",
                  f"summon piglin {_c(at(2, 0, -4))} {{PersistenceRequired:1b}}"],
        "expect": [(at(-8, -1, -8), at(8, -1, 8), "netherrack", 289, 289)],
        "expect_entities": [("minecraft:piglin", 3)],
        "run": lambda ctx: __import__("bonobo.nether", fromlist=["barter_piglin"]).barter_piglin(ctx, 8),
        "check": lambda api, inv: inv.count("minecraft:gold_ingot") <= 4 and _trades(inv) >= 3,
        "detail": lambda inv: "got " + ", ".join(f"{s['id'].split(':')[1]}×{s['count']}" for s in inv.slots
                                                 if s["id"] not in ("minecraft:gold_ingot", "minecraft:golden_helmet",
                                                                    "minecraft:iron_sword")),
        "budget": 120,
    },
    "fight_blaze": {
        "doc": "Nether platform, 3 blazes, sword + shield + iron armor → at least one blaze rod.",
        "module": "combat",
        "dimension": "minecraft:the_nether",
        "combat": True,
        # Walled like a fortress hall: on an open platform the chase walked off the edge (bench 04:19, fell to death).
        "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} nether_bricks",
                  f"fill {_c(at(-9, 0, -9))} {_c(at(9, 5, 9))} nether_bricks hollow",
                  f"fill {_c(at(-8, 0, -8))} {_c(at(8, 4, 8))} air",
                  f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p diamond_sword",
                  "item replace entity @p weapon.offhand with shield",
                  "item replace entity @p armor.chest with iron_chestplate",
                  "item replace entity @p armor.head with iron_helmet",
                  "give @p cooked_beef 16", "give @p cobblestone 32",
                  f"summon blaze {_c(at(6, 1, 0))} {{PersistenceRequired:1b}}",
                  f"summon blaze {_c(at(-6, 1, 3))} {{PersistenceRequired:1b}}",
                  f"summon blaze {_c(at(0, 1, -6))} {{PersistenceRequired:1b}}"],
        "expect": [(at(-8, -1, -8), at(8, -1, 8), "nether_bricks", 289, 289)],
        "expect_entities": [("minecraft:blaze", 3)],
        "run": lambda ctx: __import__("bonobo.combat", fromlist=["fight_blaze"]).fight_blaze(ctx, 1),
        "check": lambda api, inv: inv.count("minecraft:blaze_rod") >= 1 and api.get("/state")["health"] > 0,
        "budget": 90,
    },
    "activate_end_portal": {
        "doc": "A stronghold portal ring of 12 empty frames, 12 eyes of ender → an open end portal.",
        "module": "end",
        "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone_bricks",
                  f"fill {_c(at(-1, 0, -2))} {_c(at(1, 0, -2))} end_portal_frame[facing=south]",
                  f"fill {_c(at(-1, 0, 2))} {_c(at(1, 0, 2))} end_portal_frame[facing=north]",
                  f"fill {_c(at(-2, 0, -1))} {_c(at(-2, 0, 1))} end_portal_frame[facing=east]",
                  f"fill {_c(at(2, 0, -1))} {_c(at(2, 0, 1))} end_portal_frame[facing=west]",
                  f"fill {_c(at(-1, -1, -1))} {_c(at(1, -1, 1))} lava",
                  f"tp @p {_c(at(0, 0, -5))}", "clear @p", "give @p ender_eye 12"],
        "expect": [(at(-2, 0, -2), at(2, 0, 2), "end_portal_frame", 12, 12)],
        "run": lambda ctx: _drain(__import__("bonobo.end", fromlist=["activate_end_portal"]).activate_end_portal(ctx)),
        "check": lambda api, inv: _count_blocks(api, at(-1, 0, -1), at(1, 0, 1), "end_portal") == 9,
        "budget": 45,
    },
    "enter_end": {
        "doc": "An open end portal in a stronghold room → jump in and arrive in the End.",
        "module": "end",
        "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone_bricks",
                  f"fill {_c(at(-1, 0, -2))} {_c(at(1, 0, -2))} end_portal_frame[facing=south,eye=true]",
                  f"fill {_c(at(-1, 0, 2))} {_c(at(1, 0, 2))} end_portal_frame[facing=north,eye=true]",
                  f"fill {_c(at(-2, 0, -1))} {_c(at(-2, 0, 1))} end_portal_frame[facing=east,eye=true]",
                  f"fill {_c(at(2, 0, -1))} {_c(at(2, 0, 1))} end_portal_frame[facing=west,eye=true]",
                  f"fill {_c(at(-1, 0, -1))} {_c(at(1, 0, 1))} end_portal",
                  f"tp @p {_c(at(0, 0, -5))}", "clear @p"],
        "expect": [(at(-1, 0, -1), at(1, 0, 1), "end_portal", 9, 9)],
        "run": lambda ctx: __import__("bonobo.end", fromlist=["enter_end"]).enter_end(ctx),
        "check": lambda api, inv: api.get("/state")["dimension"] == "minecraft:the_end",
        "budget": 20,
    },
}


def _lava_lake(width):
    """Variant: a `width`-block lava strip between two stone platforms (one layout passing can be luck)."""
    far = 2 + width
    return {
        "doc": f"A {width}-block lava strip between two stone platforms; cobblestone → reach the far side alive.",
        "module": "nav",
        "setup": [f"fill {_c(at(-3, -3, -4))} {_c(at(far + 4, -3, 4))} stone",
                  f"fill {_c(at(-2, -2, -3))} {_c(at(far + 3, -1, 3))} lava",
                  f"fill {_c(at(-2, -1, -3))} {_c(at(1, -1, 3))} stone",
                  f"fill {_c(at(far, -1, -3))} {_c(at(far + 3, -1, 3))} stone",
                  f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p cobblestone 64", "give @p diamond_pickaxe"],
        "expect": [(at(2, -1, -3), at(far - 1, -1, 3), "lava", width * 7, width * 7),
                   (at(-2, -1, -3), at(1, -1, 3), "stone", 28, 28),
                   (at(-2, 0, -3), at(far + 3, 4, 3), "*", 0, 0)],
        "run": lambda ctx: __import__("bonobo.nav", fromlist=["go_to"]).go_to(at(far + 1, 0, 0), ctx.policy,
                                                                               range_=1.5),
        "check": lambda api, inv: _near(api, at(far + 1, 0, 0), 2.5) and api.get("/state")["health"] > 10,
        "budget": 10 + 4 * width,
    }


BRAIN = None     # set by `mc.py scenario`: plan-driven scenarios execute steps exactly as the brain does


def _achieve(ctx, needs, done, rounds=12):
    """Plan the needs from the current bag and execute the first step until `done()` — the brain's own path."""
    from .brain import LiveCost
    from .planner import Planner
    from .world import Inventory, Snapshot
    from . import jobs as _jobs
    for _ in range(rounds * 4):
        if done():
            return True
        snap = Snapshot()
        pending = BRAIN.mem.jobs(snap.dimension)
        ready = [j for j in pending if j["ready_at"] <= time.time()]
        if ready:
            _jobs.collect(ctx, ready[0])        # a background furnace finished: take its output (the brain's job)
            continue
        plan = Planner.from_inventory(Inventory(), LiveCost(snap), BRAIN.mem.pending_outputs(snap.dimension)) \
            .plan(needs)
        if not plan:
            if pending:
                time.sleep(1)                   # everything else is done; the furnace is still cooking
                continue
            break
        BRAIN.execute(ctx, plan[0], False)
    if not done():
        from .api import McError
        raise McError(f"needs {needs} not met after {rounds} plan steps")
    return True


def _inv_has(item, n):
    from .world import Inventory
    return lambda: Inventory().count(item) >= n


SCENARIOS["cross_lava_lake"] = _lava_lake(12)
SCENARIOS["craft_stone_tools"] = {
    "doc": "Stone floor, 6 oak logs → crafting table, wooden then stone pickaxe (plan-driven).",
    "module": "skills",
    "setup": [f"fill {_c(at(-8, -4, -8))} {_c(at(8, -1, 8))} stone",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p oak_log 6"],
    "expect": [(at(-8, -1, -8), at(8, -1, 8), "stone", 289, 289)],
    "run": lambda ctx: _achieve(ctx, [("tool", "pickaxe", 1)], _inv_has("minecraft:stone_pickaxe", 1)),
    "check": lambda api, inv: inv.count("minecraft:stone_pickaxe") >= 1,
    "budget": 60,
}
SCENARIOS["iron_ingots"] = {
    "doc": "Stone room with 6 iron ore + 4 coal ore, stone pickaxe + furnace materials → 3 iron ingots.",
    "module": "skills",
    "setup": [f"fill {_c(at(-8, -4, -8))} {_c(at(8, -1, 8))} stone",
              f"fill {_c(at(5, 0, -2))} {_c(at(7, 2, 2))} stone",
              f"fill {_c(at(5, 0, -1))} {_c(at(5, 1, 1))} iron_ore",
              f"fill {_c(at(5, 0, 2))} {_c(at(5, 1, 2))} coal_ore",
              f"fill {_c(at(-5, 0, 2))} {_c(at(-5, 1, 2))} coal_ore",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p stone_pickaxe", "give @p crafting_table",
              "give @p cobblestone 8"],
    "expect": [(at(5, 0, -1), at(5, 1, 1), "iron_ore", 6, 6), (at(-8, 0, -8), at(8, 3, 8), "coal_ore", 4, 4)],
    "run": lambda ctx: _achieve(ctx, [("minecraft:iron_ingot", 3)], _inv_has("minecraft:iron_ingot", 3), rounds=16),
    "check": lambda api, inv: inv.count("minecraft:iron_ingot") >= 3,
    "budget": 120,
}
SCENARIOS["hunt_food"] = {
    "doc": "Grass pen with 4 cows, iron sword → 3 raw beef.",
    "module": "skills",
    "combat": False,
    "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} grass_block",
              # Four fence walls (a hollow fill also covers the top and bottom faces: cows stood on fences).
              f"fill {_c(at(-9, 0, -9))} {_c(at(9, 0, -9))} oak_fence",
              f"fill {_c(at(-9, 0, 9))} {_c(at(9, 0, 9))} oak_fence",
              f"fill {_c(at(-9, 0, -8))} {_c(at(-9, 0, 8))} oak_fence",
              f"fill {_c(at(9, 0, -8))} {_c(at(9, 0, 8))} oak_fence",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p iron_sword",
              *[f"summon cow {_c(at(dx, 0, dz))}" for dx, dz in ((4, 3), (-4, 2), (3, -5), (-5, -4))]],
    "expect": [(at(-8, -1, -8), at(8, -1, 8), "grass_block", 289, 289)],
    "expect_entities": [("minecraft:cow", 4)],
    "run": lambda ctx: __import__("bonobo.skills", fromlist=["hunt"]).hunt(
        ctx, "minecraft:beef", 3, ["minecraft:cow"], False),
    "check": lambda api, inv: inv.count("minecraft:beef") >= 3,
    "budget": 45,
}
SCENARIOS["craft_eyes"] = {
    "doc": "6 blaze rods + 12 ender pearls → 12 eyes of ender (plan-driven, table placed on the way).",
    "module": "skills",
    "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p blaze_rod 6", "give @p ender_pearl 12",
              "give @p crafting_table"],
    "expect": [(at(-6, -1, -6), at(6, -1, 6), "stone", 169, 169)],
    "run": lambda ctx: _achieve(ctx, [("minecraft:ender_eye", 12)], _inv_has("minecraft:ender_eye", 12)),
    "check": lambda api, inv: inv.count("minecraft:ender_eye") >= 12,
    "budget": 30,
}
SCENARIOS["return_from_nether"] = {
    "doc": "Nether side: a lit portal 6 blocks away → walk in and arrive in the Overworld.",
    "module": "nether",
    "dimension": "minecraft:the_nether",
    "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} netherrack",
              f"fill {_c(at(-1, 0, 4))} {_c(at(2, 4, 4))} obsidian",
              f"fill {_c(at(0, 1, 4))} {_c(at(1, 3, 4))} nether_portal[axis=x]",
              f"tp @p {_c(at(0, 0, -2))}", "clear @p", "give @p flint_and_steel", "give @p cobblestone 16"],
    "expect": [(at(0, 1, 4), at(1, 3, 4), "nether_portal", 6, 6)],
    "run": lambda ctx: __import__("bonobo.nether", fromlist=["use_portal"]).use_portal(ctx, "minecraft:overworld"),
    "check": lambda api, inv: api.get("/state")["dimension"] == "minecraft:overworld",
    "budget": 20,
}
# Real structures in the test world (seed 1234): no box. /locate gives the truth to check against.
SCENARIOS["locate_stronghold"] = {
    "doc": "Surface near the origin, 12 eyes → two throws, triangulated estimate within 64 blocks of /locate.",
    "module": "nether",
    "raw": True,
    # Away from the bench's sky platform (spreadplayers picks the highest block: it put us on the platform at y 200).
    "setup": ["spreadplayers 10400 10400 0 4 false @p", "clear @p", "give @p ender_eye 12", "give @p cobblestone 64",
              "give @p stone_pickaxe", "give @p cooked_beef 16", "locate structure minecraft:stronghold"],
    "run": lambda ctx: __import__("bonobo.nether", fromlist=["locate_stronghold"]).locate_stronghold(ctx),
    "check": lambda api, inv: _stronghold_error() <= 64,
    "budget": 300,
}
def _fresh_stronghold(ctx):
    """A brand-new stronghold for every run: /place structure at the next free slot along x (the real one gets dug
    up by each search), an estimate 20 blocks off its start, the player on the surface above the estimate."""
    from . import api
    table = load_table()
    n = sum(len(v) for v in table.get("find_portal_room_fresh", {}).values())
    x, z = 20000 + 600 * n, 20000
    # The spot must be loaded first ("That position is not loaded" 4×): force-load the area and probe until it is.
    # A stronghold is ~80 blocks across: probing one column at the centre said "loaded" while the outskirts weren't,
    # and /place structure answered "That position is not loaded" four runs in a row. Force-load the whole footprint
    # and probe its corners, then give the placement a few tries while chunks finish loading.
    _command(f"execute in minecraft:overworld run forceload add {x - 96} {z - 96} {x + 96} {z + 96}", [])
    probes = [(x + dx, z + dz) for dx in (-80, 0, 80) for dz in (-80, 0, 80)]
    for _ in range(60):
        if not any("not loaded" in l for px, pz in probes for l in
                   _command(f"execute in minecraft:overworld run fill {px} 300 {pz} {px} 300 {pz} air", [])):
            break
        time.sleep(0.5)
    else:
        raise SetupInvalid("stronghold area never loaded")
    for attempt in range(5):
        lines = _command(f"execute in minecraft:overworld run place structure minecraft:stronghold {x} 30 {z}", [])
        if any("Generated" in l or "placed" in l.lower() for l in lines):
            break
        time.sleep(1.0)
    else:
        raise SetupInvalid(f"stronghold not placed: {lines[:1]}")
    est = (x + 20, 30, z - 12)
    ctx.mem.add_site("stronghold", est, "minecraft:overworld", name="stronghold")
    api.post("/chat", {"message": f"/spreadplayers {est[0]} {est[2]} 0 4 false @p"})
    time.sleep(4)


PORTAL_ROOM_OK = []


def _portal_room_run(ctx):
    """Run the search and remember whether the skill itself succeeded: a run that raised "finished without reaching
    its goal" was recorded as PASS because the check only asked whether any frame stood within 48 blocks."""
    from .end import find_portal_room
    PORTAL_ROOM_OK.clear()
    find_portal_room(ctx)
    PORTAL_ROOM_OK.append(True)
    return True


def _portal_room_found():
    from .end import ROOM_REACH
    from .world import find as _find
    # The same number the skill's contract uses: three different radii (scan 48, contract 32, check 12) was how a
    # run could log "room found", fail its own verify, and still be recorded as a pass.
    return bool(PORTAL_ROOM_OK) and bool(_find(["end_portal_frame"], radius=ROOM_REACH, limit=1))


SCENARIOS["find_portal_room_fresh"] = {
    "doc": "A freshly generated stronghold (new place every run), estimate 20 off, surface start → portal frame found.",
    "module": "end", "raw": True, "release": True,
    "setup": ["clear @p", "give @p diamond_pickaxe", "give @p cobblestone 64", "give @p cooked_beef 16",
              "give @p torch 32", "give @p water_bucket"],
    "before": _fresh_stronghold,
    # The skill has to succeed AND the frames have to be right here: a run that raised "finished without reaching its
    # goal" counted as PASS because any frame within 48 blocks satisfied the old check.
    "run": _portal_room_run,
    "check": lambda api, inv: _portal_room_found(),
    "budget": 300,
}
# find_portal_room (the real stronghold, dug up by every run) was replaced by find_portal_room_fresh.
SCENARIOS["fight_dragon"] = {
    "doc": "The End's main island with the dragon, diamond sword, shield, iron armor, food, blocks → dragon dead.",
    "module": "combat",
    "raw": True,
    "combat": True,
    "dimension": "minecraft:the_end",
    # Repeatable: a dragon only spawns once per world, so every run replaces it with a fresh one.
    "setup": ["kill @e[type=ender_dragon]", "summon ender_dragon 0 80 0 {DragonPhase:0}",
              "spreadplayers 0 0 4 10 false @p", "clear @p", "give @p diamond_sword",
              "item replace entity @p weapon.offhand with shield",
              "item replace entity @p armor.chest with iron_chestplate",
              "item replace entity @p armor.head with iron_helmet",
              "item replace entity @p armor.legs with iron_leggings",
              "item replace entity @p armor.feet with iron_boots",
              "give @p cooked_beef 32", "give @p cobblestone 64", "give @p water_bucket"],
    "run": lambda ctx: __import__("bonobo.combat", fromlist=["fight_dragon"]).fight_dragon(ctx),
    "check": lambda api, inv: not any(e["type"] == "minecraft:ender_dragon"
                                      for e in __import__("bonobo.world", fromlist=["entities"]).entities(200)),
    "budget": 900,
}

def _chat(cmd):
    """A command from a `before` hook (after setup, perception running): world changes the skill must react to."""
    from . import api
    api.post("/chat", {"message": "/" + cmd})
    time.sleep(0.3)


def _worn_head():
    from .world import Inventory
    return ((Inventory().equipment.get("head") or {}).get("id") or "")


def _wait_landed(ctx, seconds=10):
    from . import api
    t0 = time.time()
    time.sleep(1.0)
    while time.time() - t0 < seconds:
        s = api.get("/state")
        if s.get("onGround") or s.get("inWater") or s.get("dead"):
            time.sleep(1.0)
            return True
        time.sleep(0.2)
    return False


def _snap_survival(ctx):
    from .world import Snapshot
    return BRAIN.survival(Snapshot(), ctx)


SCENARIOS["loot_chest"] = {
    "doc": "A chest with 5 iron ingots and bread 6 blocks away → take the valuable stacks.",
    "module": "loot",
    "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone",
              f"setblock {_c(at(5, 0, 1))} chest",
              f"item replace block {_c(at(5, 0, 1))} container.0 with iron_ingot 5",
              f"item replace block {_c(at(5, 0, 1))} container.1 with bread 4",
              f"tp @p {_c(at(-1, 0, 0))}", "clear @p"],
    "expect": [(at(5, 0, 1), at(5, 0, 1), "chest", 1, 1)],
    "run": lambda ctx: __import__("bonobo.loot", fromlist=["loot_chest"]).loot_chest(ctx),
    "check": lambda api, inv: inv.count("minecraft:iron_ingot") >= 5,
    "budget": 20,
}
SCENARIOS["water_clutch"] = {
    "doc": "Dropped 30 blocks above stone with a water bucket → the perception thread pours water, no damage.",
    "module": "perception",
    "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p water_bucket"],
    "expect": [(at(-6, -1, -6), at(6, -1, 6), "stone", 169, 169)],
    # The fall starts after setup (perception is paused during setup).
    "before": lambda ctx: _chat(f"tp @p {_c(at(0.5, 30, 0.5))}"),
    "run": _wait_landed,
    "check": lambda api, inv: api.get("/state")["health"] >= 18 and not api.get("/state")["dead"],
    "budget": 15,
}
SCENARIOS["recover_items"] = {
    "doc": "Died 10 blocks away a minute ago, 3 diamonds lie there → walk back and pick them up.",
    "module": "upkeep",
    "setup": [f"fill {_c(at(-10, -2, -6))} {_c(at(12, -1, 6))} stone",
              f"tp @p {_c(at(-6, 0, 0))}", "clear @p"],
    "expect": [(at(-10, -1, -6), at(12, -1, 6), "stone", 299, 299)],
    "before": lambda ctx: (ctx.mem.log_death(at(6, 0, 0), "minecraft:overworld"),
                           _chat(f'summon item {_c(at(6, 0, 0))} {{Item:{{id:"minecraft:diamond",count:3}},Age:-32768}}')),
    "run": lambda ctx: __import__("bonobo.upkeep", fromlist=["recover_items"]).recover_items(ctx),
    "check": lambda api, inv: inv.count("minecraft:diamond") >= 3,
    "budget": 20,
}
SCENARIOS["relight_portal"] = {
    "doc": "A remembered portal frame whose fire went out, flint and steel → relight it and arrive in the Nether.",
    "module": "nether",
    "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone",
              f"fill {_c(at(-1, 0, 4))} {_c(at(2, 4, 4))} obsidian",
              f"fill {_c(at(0, 1, 4))} {_c(at(1, 3, 4))} air",
              f"tp @p {_c(at(0, 0, -2))}", "clear @p", "give @p flint_and_steel"],
    "expect": [(at(-1, 0, 4), at(2, 4, 4), "obsidian", 14, 14)],
    "before": lambda ctx: ctx.mem.add_site("portal", at(0, 1, 4), "minecraft:overworld", name="portal-test"),
    "run": lambda ctx: __import__("bonobo.nether", fromlist=["use_portal"]).use_portal(ctx, "minecraft:the_nether"),
    "check": lambda api, inv: api.get("/state")["dimension"] == "minecraft:the_nether",
    "budget": 25,
}
SCENARIOS["gold_helmet_swap"] = {
    "doc": "In the Nether wearing iron, a gold helmet in the bag → the reflex puts gold on (piglins stay neutral).",
    "module": "brain",
    "dimension": "minecraft:the_nether",
    "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} netherrack",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "item replace entity @p armor.head with iron_helmet",
              "give @p golden_helmet"],
    "expect": [(at(-6, -1, -6), at(6, -1, 6), "netherrack", 169, 169)],
    "run": lambda ctx: BRAIN.reflexes(),
    "check": lambda api, inv: _worn_head() == "minecraft:golden_helmet" and inv.count("minecraft:iron_helmet") >= 1,
    "budget": 10,
}
SCENARIOS["retreat_from_nether"] = {
    "doc": "Nether, hurt to 6 hp with one food, the arrival portal 8 blocks away → survival retreats through it.",
    "module": "brain",
    "dimension": "minecraft:the_nether",
    "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} netherrack",
              f"fill {_c(at(-1, 0, 6))} {_c(at(2, 4, 6))} obsidian",
              f"fill {_c(at(0, 1, 6))} {_c(at(1, 3, 6))} nether_portal[axis=x]",
              f"tp @p {_c(at(0, 0, -2))}", "clear @p", "give @p cooked_beef 1", "give @p cobblestone 16"],
    "expect": [(at(0, 1, 6), at(1, 3, 6), "nether_portal", 6, 6)],
    "before": lambda ctx: _chat("damage @p 14 minecraft:generic"),
    "run": _snap_survival,
    "check": lambda api, inv: api.get("/state")["dimension"] == "minecraft:overworld",
    "budget": 25,
}
SCENARIOS["find_fortress"] = {
    "doc": "Nether platform, a nether brick hall 18 blocks away → found and remembered as the fortress site.",
    "module": "nether",
    "dimension": "minecraft:the_nether",
    "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(20, -1, 6))} netherrack",
              f"fill {_c(at(16, 0, -3))} {_c(at(20, 4, 3))} nether_bricks hollow",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p cobblestone 32", "give @p cooked_beef 16"],
    "expect": [(at(16, 0, -3), at(20, 4, 3), "nether_bricks", 60, 200)],
    "run": lambda ctx: __import__("bonobo.nether", fromlist=["find_fortress"]).find_fortress(ctx),
    "check": lambda api, inv: bool(__import__("bonobo.memory", fromlist=["Memory"]).Memory(NOTES)
                                   .sites("minecraft:the_nether", kinds=["fortress"])),
    "budget": 15,
}

def _reflex_for(seconds):
    def run(ctx):
        t0 = time.time()
        while time.time() - t0 < seconds:
            BRAIN.reflexes()
            time.sleep(0.2)
        return True
    return run


SCENARIOS["ghast_fireball"] = {
    "doc": "Nether hall open to one side, a ghast 20 blocks out, sword + armor → 30 s of reflexes, still healthy.",
    "module": "brain",
    "dimension": "minecraft:the_nether",
    "combat": True,
    # A knee-high rim (a blast knocked the player off during setup: "doomed to fall by Ghast"); the ghast is summoned
    # only when the skill starts, so setup time isn't spent under fire.
    "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} netherrack",
              f"fill {_c(at(-6, 0, -6))} {_c(at(6, 0, 6))} netherrack hollow",
              f"fill {_c(at(-5, 0, -5))} {_c(at(5, 0, 5))} air",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p diamond_sword",
              "item replace entity @p armor.chest with iron_chestplate",
              "item replace entity @p armor.head with golden_helmet", "give @p cooked_beef 16"],
    "expect": [(at(-6, -1, -6), at(6, -1, 6), "netherrack", 169, 169)],
    "before": lambda ctx: _chat(f"execute in minecraft:the_nether run summon ghast {_c(at(18, 6, 0))} "
                                "{PersistenceRequired:1b}"),
    "run": _reflex_for(30),
    "check": lambda api, inv: api.get("/state")["health"] >= 10 and not api.get("/state")["dead"],
    "budget": 35,
}

FORTRESS_RUN = {}


def _far_from_fortress(ctx):
    """Stand ~120 blocks from the real fortress, below the roof (spreadplayers 'under 90' picks a floor there)."""
    real = locate_reply(LAST_FEEDBACK)
    if not real:
        raise SetupInvalid("no /locate answer for the fortress")
    _chat(f"execute in minecraft:the_nether run spreadplayers {real[0] + 120} {real[1]} 0 12 under 90 false @p")
    time.sleep(3)
    # What the memory already holds, and where we start: a fortress site written by an earlier scenario passed this
    # one in 3.5 s without a step taken.
    from . import api
    from .memory import Memory
    s = api.get("/state")
    FORTRESS_RUN.clear()
    FORTRESS_RUN["start"] = (s["x"], s["y"], s["z"])
    FORTRESS_RUN["before"] = {tuple(round(c) for c in site["pos"])
                              for site in Memory(NOTES).sites("minecraft:the_nether", kinds=["fortress"])}


def _found_fortress_now():
    """The run passed only if it wrote a fortress site this time, and one the player actually walked to."""
    from .memory import Memory
    sites = Memory(NOTES).sites("minecraft:the_nether", kinds=["fortress"])
    start = FORTRESS_RUN.get("start")
    for site in sites:
        pos = tuple(round(c) for c in site["pos"])
        if pos in FORTRESS_RUN.get("before", ()):
            continue
        if start and math.hypot(pos[0] - start[0], pos[2] - start[2]) >= 60:
            return True
    return False


SCENARIOS["find_fortress_far"] = {
    "doc": "The real Nether: ~120 blocks from a fortress (lava sea likely between), blocks + food → fortress found.",
    "module": "nether",
    "raw": True,
    "combat": True,
    "dimension": "minecraft:the_nether",
    "setup": ["spreadplayers 0 0 0 8 under 90 false @p", "locate structure minecraft:fortress", "clear @p",
              "give @p cobblestone 128",
              "give @p diamond_pickaxe", "give @p cooked_beef 32", "give @p flint_and_steel",
              "item replace entity @p armor.head with golden_helmet",
              "item replace entity @p armor.chest with iron_chestplate"],
    "before": _far_from_fortress,
    "run": lambda ctx: __import__("bonobo.nether", fromlist=["find_fortress"]).find_fortress(ctx),
    "check": lambda api, inv: _found_fortress_now(),
    "budget": 180,
}

def _trek(dx, dz, dimension="minecraft:overworld"):
    """Walk a straight-line distance over real terrain; the note gets seconds per 100 blocks (the main time sink:
    travel + goto were 47 % of 5.5 h of task time)."""
    def run(ctx):
        from . import api, nav
        s = api.get("/state")
        start = (s["blockX"], s["blockY"], s["blockZ"])
        target = (start[0] + dx, start[1], start[2] + dz)
        # In this process, not through the notes file: the check reloaded Memory from disk, found no trek and failed
        # a walk that had ended 8 blocks from the target (bench 08:07).
        TREK.clear()
        TREK.update(start=start, target=target, t0=time.time())
        ok = nav.go_to(target, ctx.policy, range_=12, attempts=1)
        end = api.get("/state")
        TREK["end"] = (end["x"], end["y"], end["z"])
        TREK["seconds"] = time.time() - TREK["t0"]
        if not ok and math.hypot(end["x"] - target[0], end["z"] - target[2]) > 14:
            raise api.NavFailed(f"trek to {target} stopped short")
        return True
    return run


TREK = {}


def _trek_detail(inv):
    if not TREK.get("end"):
        return ""
    dist = math.hypot(TREK["target"][0] - TREK["start"][0], TREK["target"][2] - TREK["start"][2])
    left = math.hypot(TREK["end"][0] - TREK["target"][0], TREK["end"][2] - TREK["target"][2])
    return f"{dist:.0f} blocks, {TREK['seconds'] / dist * 100:.1f} s/100 (wall), ended {left:.0f} from target"


def _trek_check(api):
    if not TREK.get("end"):
        return False
    left = math.hypot(TREK["end"][0] - TREK["target"][0], TREK["end"][2] - TREK["target"][2])
    return left <= 14 and not api.get("/state")["dead"]


import math  # noqa: E402  (used by the trek helpers above)

SCENARIOS["trek_overworld_200"] = {
    "doc": "Real Overworld terrain (hills, forest, water), 200 blocks east, basic kit → arrive; seconds per 100 blocks.",
    "module": "nav", "raw": True,
    "setup": ["spreadplayers 10600 10600 0 4 false @p", "clear @p", "give @p stone_pickaxe", "give @p stone_axe",
              "give @p cobblestone 64", "give @p cooked_beef 16", "give @p oak_boat"],
    "run": _trek(200, 0), "check": lambda api, inv: _trek_check(api), "detail": _trek_detail, "budget": 90,
}
SCENARIOS["trek_nether_150"] = {
    "doc": "Real Nether terrain below the roof, 150 blocks, kit with gold helmet → arrive; seconds per 100 blocks.",
    "module": "nav", "raw": True, "combat": True, "dimension": "minecraft:the_nether",
    "setup": ["spreadplayers 300 300 0 8 under 90 false @p", "clear @p", "give @p diamond_pickaxe",
              "give @p cobblestone 128", "give @p cooked_beef 16",
              "item replace entity @p armor.head with golden_helmet"],
    "run": _trek(150, 0, "minecraft:the_nether"), "check": lambda api, inv: _trek_check(api),
    "detail": _trek_detail, "budget": 90,
}
SCENARIOS["cave_escape"] = {
    "doc": "Sealed in a dark 1×2 pocket 8 blocks under the platform, pickaxe + blocks → back on the surface platform.",
    "module": "nav",
    "setup": [f"fill {_c(at(-6, -12, -6))} {_c(at(6, -1, 6))} stone",
              f"fill {_c(at(0, -9, 0))} {_c(at(0, -8, 0))} air",
              f"tp @p {_c(at(0.5, -9, 0.5))}", "clear @p", "give @p stone_pickaxe", "give @p cobblestone 32"],
    "expect": [(at(-6, -1, -6), at(6, -1, 6), "stone", 169, 169)],
    # range 0.6: travel counts 3-D distance, so 1.5 "arrived" one step below the surface (y 199.2, bench 05:28).
    # Target one above the platform: the mod counts "arrived" within range + 0.5, so a y 200 target accepted the stair
    # step at y 199.2 twice; from y 201 only standing on the platform is close enough.
    "run": lambda ctx: __import__("bonobo.nav", fromlist=["go_to"]).go_to(at(3, 1, 3), ctx.policy, range_=0.6),
    "check": lambda api, inv: api.get("/state")["y"] >= at(0, 0, 0)[1] - 0.5 and api.get("/state")["onGround"],
    "budget": 30,
}
SCENARIOS["return_to_portal"] = {
    "doc": "The arrival portal 18 blocks away behind a stone wall, remembered as a site → back into it (Nether).",
    "module": "nether",
    "dimension": "minecraft:the_nether",
    "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(18, -1, 8))} netherrack",
              f"fill {_c(at(8, 0, -8))} {_c(at(8, 4, 5))} netherrack",
              f"fill {_c(at(-2, 0, 0))} {_c(at(1, 4, 0))} obsidian",
              f"fill {_c(at(-1, 1, 0))} {_c(at(0, 3, 0))} nether_portal[axis=x]",
              f"tp @p {_c(at(16, 0, 0))}", "clear @p", "give @p diamond_pickaxe", "give @p cobblestone 32"],
    "expect": [(at(-1, 1, 0), at(0, 3, 0), "nether_portal", 6, 6)],
    "before": lambda ctx: ctx.mem.add_site("portal", at(-1, 1, 1), "minecraft:the_nether", name="portal-nether"),
    "run": lambda ctx: __import__("bonobo.nether", fromlist=["use_portal"]).use_portal(ctx, "minecraft:overworld"),
    "check": lambda api, inv: api.get("/state")["dimension"] == "minecraft:overworld",
    "budget": 25,
}
for _name in ("fight_dragon", "find_fortress_far", "locate_stronghold", "trek_overworld_200",
              "trek_nether_150"):
    SCENARIOS[_name]["release"] = True       # minutes each: run by name before a live run, not in every round
def _road_reuse(ctx):
    """There, back, and there again over the same 150 blocks: the third trip must follow the remembered legs
    (roads.py) and take no longer than the first. Times are kept for the check."""
    from . import api, nav
    s = api.get("/state")
    a = (s["blockX"], s["blockY"], s["blockZ"])
    b = (a[0] + 150, a[1], a[2])
    times = []
    for target in (b, a, b):
        t0 = time.time()
        if not nav.go_to(target, ctx.policy, range_=12, attempts=1):
            raise api.NavFailed(f"road trip to {target} stopped short")
        times.append(time.time() - t0)
    ROAD_TIMES[:] = times
    return True


ROAD_TIMES = []
SCENARIOS["road_reuse"] = {
    "doc": "Overworld, 150 blocks there, back, there again → the third trip reuses the road and is no slower.",
    "module": "nav", "raw": True, "release": True,
    "setup": ["spreadplayers 11200 11200 0 4 false @p", "clear @p", "give @p stone_pickaxe", "give @p cobblestone 64",
              "give @p cooked_beef 16"],
    "run": _road_reuse,
    "check": lambda api, inv: len(ROAD_TIMES) == 3 and ROAD_TIMES[2] <= ROAD_TIMES[0] * 1.05,
    "detail": lambda inv: "trips " + ", ".join(f"{t:.0f}s" for t in ROAD_TIMES),
    "budget": 240,
}

# -- route slices: the cerebellum itself (brain.round) over one route segment, not a single skill. Most live problems
# were scheduling and chaining (loops, idle holds, wrong-way unstucks, repeated exits), so each slice measures them.
SLICE = {}


def slice_report(lines, positions, target, idle_s):
    """Pure: loops (review.repeated over the brain's own log), longest idle, and how far the player moved away from
    `target` in total (walking the wrong way) — from the slice's log lines and (t, pos) samples."""
    from . import review
    import datetime
    entries = []
    for raw in lines:
        parts = raw.split(" ", 1)
        if len(parts) == 2 and len(parts[0]) == 8 and parts[0].count(":") == 2:
            entries.append((parts[0], parts[1]))
    # review.repeated returns review text ("- ×5 …" lines, or "- none"): as a string, "- none" read as a loop and
    # would have stopped every slice after its first round.
    loops = [l[2:] for l in review.repeated(entries, at_least=4).splitlines() if l.strip() and l != "- none"]
    away = 0.0
    if target is not None:
        d = [math.dist((p[0], p[2]), (target[0], target[2])) for _, p in positions]
        away = sum(max(0.0, b - a) for a, b in zip(d, d[1:]))
    return {"loops": loops, "idle_s": round(idle_s), "away_m": round(away)}


def _slice(done, minutes, target=None, route_name="speedrun", max_idle=15):
    """Run the whole cerebellum (brain.round) until done() or `minutes`, on a private route file. Stops at once on a
    loop (the same decision line 4×) or an idle hold longer than `max_idle` — a report, not a timeout."""
    def run(ctx):
        from . import api, priority, route
        from .world import Snapshot
        saved = route.FILE
        route.FILE = os.path.join(os.path.dirname(NOTES), "slice-route.json")
        route.choose(route_name)
        # The profile too, not just the route: a slice used to run with default weights, so speedrun bans never
        # applied and "light up" was picked 80× in one 8-minute Nether-kit slice ("no torches to spare").
        saved_prio = priority.FILE
        priority.FILE = os.path.join(os.path.dirname(NOTES), "slice-priorities.json")
        try:
            os.remove(priority.FILE)
        except OSError:
            pass
        priority.apply_profile(route_name)
        BRAIN.idle_since, BRAIN.committed = None, None
        t0, positions, idle = time.time(), [], 0.0
        start_line = len(sys.stdout.lines) if hasattr(sys.stdout, "lines") else 0
        stopped = None
        try:
            while time.time() - t0 < minutes * 60:
                if done():
                    break
                try:
                    BRAIN.round()
                except api.McError as e:
                    api.log(f"!! round: {e}")
                s = Snapshot()
                positions.append((time.time(), s.feet))
                if BRAIN.idle_since:
                    idle = max(idle, time.time() - BRAIN.idle_since)
                lines = sys.stdout.lines[start_line:] if hasattr(sys.stdout, "lines") else []
                rep = slice_report(lines, positions, target, idle)
                if rep["loops"] or rep["idle_s"] > max_idle:
                    stopped = f"loop: {rep['loops'][0]}" if rep["loops"] else f"idle {rep['idle_s']}s"
                    break
        finally:
            route.FILE = saved
            priority.FILE = saved_prio     # the slice's private weights must not leak into the next scenario
            SLICE.update(seconds=time.time() - t0, positions=positions, idle=idle, target=target)
        if stopped:
            raise api.McError(f"slice stopped early — {stopped}")
        SLICE.update(seconds=time.time() - t0, positions=positions, idle=idle, target=target)
        if not done():
            raise api.McError(f"slice not done after {minutes} min")
        return True
    return run


def _slice_detail(inv):
    if not SLICE:
        return ""
    rep = slice_report(LAST_LINES, SLICE["positions"], SLICE["target"], SLICE["idle"])
    return (f"{SLICE['seconds']:.0f}s, longest idle {rep['idle_s']}s, walked away {rep['away_m']} m, "
            f"loops: {'; '.join(rep['loops'][:3]) or 'none'}")


LAST_LINES = []


def _slice_check(done, max_idle=15, max_loops=0):
    def check(api, inv):
        if not SLICE or not done():
            return False
        rep = slice_report(LAST_LINES, SLICE["positions"], SLICE["target"], SLICE["idle"])
        return rep["idle_s"] <= max_idle and len(rep["loops"]) <= max_loops
    return check


def _has_tools_and_furnace():
    from .world import Inventory
    inv = Inventory()
    return inv.count("minecraft:stone_pickaxe") >= 1 and (inv.count("minecraft:furnace") >= 1
                                                          or bool(__import__("bonobo.world", fromlist=["find"])
                                                                  .find(["furnace"], 8, 1)))


def _nether_kit_ready():
    from .route import nether_kit_missing
    from .world import Inventory
    return not nether_kit_missing(Inventory())


def _in_overworld():
    from . import api
    return api.get("/state")["dimension"] == "minecraft:overworld"


SCENARIOS["slice_start_tools"] = {
    "doc": "Route slice: empty-handed on real Overworld terrain → stone pickaxe + furnace, no loops, idle ≤ 15 s.",
    "module": "brain", "raw": True, "release": True,
    "setup": ["spreadplayers 11600 11600 0 4 false @p", "clear @p", "time set day"],
    "run": _slice(_has_tools_and_furnace, 6),
    "check": _slice_check(_has_tools_and_furnace),
    "detail": _slice_detail,
    "budget": 360,
}
def _spread_to_located_biome():
    """Move the player to the biome the setup's `/locate biome` just found. The kit needs meat, so the slice has to
    start where animals spawn: at a fixed (12000, 12000) it landed in animal-free mountains and spent 8 minutes at
    food 0/6 with everything else ready."""
    spot = locate_reply(LAST_FEEDBACK)
    if spot is None:
        raise SetupInvalid("no /locate biome answer for the plains")
    # Say where we are putting the player: a silent setup left no way to tell "landed in plains, still no animals"
    # apart from "never moved at all" when the slice failed at food 0/6 again.
    _chat(f"execute in minecraft:overworld run spreadplayers {spot[0]} {spot[1]} 0 4 false @p")
    time.sleep(3)
    from . import api as _api
    s = _api.get("/state")
    print(f"   slice starts at {(s['blockX'], s['blockY'], s['blockZ'])} (plains located at {spot})", flush=True)


def _portal_beside_player(ctx):
    """A lit portal three blocks east of wherever the player landed, remembered as built — the slice starts in the
    'nether kit' segment. Without it the route sat in 'portal' and the slice hunted sheep for a bed for 5 minutes."""
    from . import api
    s = api.get("/state")
    x, y, z = s["blockX"] + 3, s["blockY"], s["blockZ"]
    for cmd in (f"fill {x} {y - 1} {z - 1} {x} {y + 3} {z + 2} obsidian",
                f"fill {x} {y} {z} {x} {y + 2} {z + 1} nether_portal[axis=z]"):
        _chat(f"execute in minecraft:overworld run {cmd}")
    ctx.mem.add_machine("nether_portal", (x, y - 1, z - 1), 1, "minecraft:overworld", ["portal"])
    ctx.mem.add_site("portal", (x, y, z), "minecraft:overworld", name="portal-overworld")


SCENARIOS["slice_nether_kit"] = {
    "doc": "Route slice: at a lit portal with tools but no kit → kit complete (food, blocks, gold helmet) without "
           "stepping into the Nether early, no loops, idle ≤ 15 s.",
    "module": "brain", "raw": True, "release": True,
    # Land in a plains biome, not wherever (12000, 12000) happens to be: that spot is animal-free mountains, and the
    # slice failed for 8 minutes at food 0/6 with everything else in the kit ready (blocks 30/32, no idling, no
    # loops). The kit needs meat, so the scenario must start where meat exists — this tests the route, not the luck
    # of a landing spot.
    # Three cooked steaks to start with, like the iron pickaxe and the bucket. This slice measures route scheduling
    # (kit assembled without stepping into the Nether early, no loops, no idling), not whether cows happen to have
    # spawned: in this world every landing spot tried, plains included, answered "no pig seen yet" for 8 minutes
    # while blocks went 0 → 30/32. The remaining half of the food target still has to be hunted, so the food path
    # is still exercised.
    "setup": ["locate biome minecraft:plains", "clear @p", "time set day", "give @p iron_pickaxe",
              "give @p iron_sword", "give @p bucket", "give @p flint_and_steel", "give @p gold_ingot 5",
              "give @p coal 8", "give @p crafting_table", "give @p furnace", "give @p cooked_beef 6"],
    "before": lambda ctx: (_spread_to_located_biome(), _portal_beside_player(ctx)),
    "run": _slice(lambda: _nether_kit_ready() or not _in_overworld(), 8),
    "check": _slice_check(lambda: _nether_kit_ready() and _in_overworld()),
    "detail": _slice_detail,
    "budget": 480,
}
SCENARIOS["slice_retreat"] = {
    "doc": "Route slice: in the Nether at 6 hp with one food, the arrival portal remembered 10 blocks away → back in "
           "the Overworld by the survival layer, no loops.",
    "module": "brain", "dimension": "minecraft:the_nether", "release": True,
    "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(12, -1, 8))} netherrack",
              f"fill {_c(at(8, 0, -1))} {_c(at(8, 4, 2))} obsidian",
              f"fill {_c(at(8, 1, 0))} {_c(at(8, 3, 1))} nether_portal[axis=z]",
              f"tp @p {_c(at(-2, 0, 0))}", "clear @p", "give @p cooked_beef 1", "give @p cobblestone 16",
              "give @p iron_pickaxe"],
    "expect": [(at(8, 1, 0), at(8, 3, 1), "nether_portal", 6, 6)],
    "before": lambda ctx: (ctx.mem.add_site("portal", at(8, 1, 0), "minecraft:the_nether", name="portal-nether"),
                           _chat("damage @p 14 minecraft:generic")),
    "run": _slice(_in_overworld, 2, target=at(8, 1, 0)),
    "check": _slice_check(_in_overworld),
    "detail": _slice_detail,
    "budget": 120,
}

for _name in ("trek_overworld_200", "trek_nether_150", "cave_escape", "return_to_portal"):
    SCENARIOS[_name]["mod"] = ["travel"] + (["use"] if _name == "return_to_portal" else [])
# Portal trips read /state's inPortal (mod ≥0.1.28): they depend on WorldInfo too.
for _name in ("enter_nether", "return_from_nether", "relight_portal", "return_to_portal", "retreat_from_nether"):
    SCENARIOS[_name]["mod_extra"] = ["state"]


def _dragon_health_after(seconds):
    """Run the dragon fight for `seconds`, then read the dragon's health from the server."""
    def run(ctx):
        from . import api
        from .combat import fight_dragon
        t0 = time.time()
        gen = fight_dragon.__wrapped__(ctx) if hasattr(fight_dragon, "__wrapped__") else fight_dragon(ctx)
        try:
            for _ in gen if hasattr(gen, "__next__") else []:
                if time.time() - t0 > seconds:
                    break
        finally:
            api.post("/stop")
        return True
    return run


def _dragon_health():
    import re
    lines = _command("execute in minecraft:the_end run data get entity @e[type=minecraft:ender_dragon,limit=1] Health",
                     [])
    for line in lines:
        m = re.search(r"([\d.]+)f", line)
        if m:
            return float(m.group(1))
    return None


SPEEDRUN_END_KIT = ["clear @p", "give @p stone_sword", "give @p stone_pickaxe", "give @p white_bed 6",
                    "give @p cobblestone 64", "give @p cooked_beef 16", "give @p water_bucket"]


def _summon_perched_dragon(phase=6):
    """After setup: a dragon standing ON the exit-portal pillar (its real top read from the world, not a guessed y —
    a dragon summoned in mid-air at y 70 never perched and the fight stood still)."""
    def before(ctx):
        from .end import find_pillar_top
        top = find_pillar_top()
        if top is None:
            raise SetupInvalid("no exit-portal bedrock found near the island centre")
        # The player on the island floor east of the pillar, never on an obsidian tower (spreadplayers picks the
        # highest block: it put the player at y 97–123 on a tower and nothing could be reached).
        _chat(f"execute in minecraft:the_end run spreadplayers 12 0 0 3 under {top + 8} false @p")
        # Never /kill the dragon: its death opens the exit portal and the player on the island fell into the end
        # poem ("passed" without a single bomb). Reuse a living dragon — full health, perched phase, on the
        # pillar — and summon only when there is none.
        count = lambda: server_count(_command("execute in minecraft:the_end as @p at @s if entity "
                                              "@e[type=minecraft:ender_dragon,distance=..300]", []))
        if count() < 1:
            _chat(f"execute in minecraft:the_end run summon ender_dragon 0 {top} 0 {{DragonPhase:{phase}}}")
            time.sleep(2)
        sel = "@e[type=minecraft:ender_dragon,limit=1]"
        for cmd in (f"data modify entity {sel} Health set value 200f",
                    f"data modify entity {sel} DragonPhase set value {phase}",
                    f"tp {sel} 0 {top} 0"):
            _chat(f"execute in minecraft:the_end run {cmd}")
        time.sleep(1.5)
        # A previous scenario can leave the End without a dragon, and a fresh summon needs a moment to sync: one
        # check right after the commands reported "no living dragon after setup" while one was on its way.
        for attempt in range(8):
            if count() >= 1:
                break
            if attempt % 3 == 2:
                _chat(f"execute in minecraft:the_end run summon ender_dragon 0 {top} 0 {{DragonPhase:{phase}}}")
            time.sleep(1.0)
        else:
            raise SetupInvalid("no living dragon after setup")
        # Full health before the skill starts: a dragon left perched by the previous scenario chews on the player
        # during this hook, and the run then failed as "player dead before the skill started".
        _chat("execute in minecraft:the_end run effect give @p minecraft:instant_health 2 10 true")
        _chat("execute in minecraft:the_end run effect clear @p minecraft:instant_health")
    return before


SCENARIOS["bed_bomb_once"] = {
    "doc": "Speedrun End kit (6 beds, stone sword, cobble, food, water; no armor), dragon perched → a bed bomb takes "
           "≥ 20 hp within 40 s and the player lives.",
    "module": "end", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    "setup": ["spreadplayers 0 0 8 10 false @p", *SPEEDRUN_END_KIT],
    "before": _summon_perched_dragon(6),
    # The planner drives, same as every other fight: there is only one dragon implementation now.
    "run": lambda ctx: __import__("bonobo.end", fromlist=["slay_dragon"]).slay_dragon(ctx),
    "check": lambda api, inv: (_dragon_health() or 200) <= 180 and not api.get("/state")["dead"],
    "detail": lambda inv: f"dragon hp {_dragon_health()}, beds left {inv.count('bed')}",
    "budget": 45,
}
SCENARIOS["bed_bomb_kill"] = {
    "doc": "Speedrun End kit, a fresh dragon → dead by bed bombs (release check: the whole fight).",
    "module": "end", "raw": True, "combat": True, "dimension": "minecraft:the_end", "release": True,
    "setup": ["spreadplayers 0 0 8 12 false @p", *SPEEDRUN_END_KIT],
    "before": _summon_perched_dragon(0),        # a free-flying dragon: the whole fight, perching included
    # The driver, not the old single skill: crystals → pit → perch → one bomb → pit.
    "run": lambda ctx: __import__("bonobo.end", fromlist=["slay_dragon"]).slay_dragon(ctx),
    "check": lambda api, inv: _dragon_health() is None and not api.get("/state")["dead"],
    "budget": 300,
}
def _fill_bunker(ctx):
    """Setup hook: build the bunker with /fill instead of digging it, and stand the player in it.

    Digging it costs 30–90 s of game time and tests the digging skill, which is not what a window or a hold test is
    about. Every scenario that starts from "the bunker exists" should start there directly — the dig is worth testing
    once, on its own.
    """
    from . import bunker
    from .end import PIT, bed_cell, choose_side, find_pillar_top
    top = find_pillar_top()
    if top is None:
        raise SetupInvalid("no exit-portal bedrock found near the island centre")
    side = (1, 0)
    cells = bunker.tunnel(side, top)
    heads = bunker.head_cells(cells)
    # Air for the corridor, end stone for its roof and floor: a trench would let the head and the breath straight in.
    for a, b in ((cells[0], cells[-1]), (heads[0], heads[-1])):
        _chat(f"execute in minecraft:the_end run fill {a[0]} {a[1]} {a[2]} {b[0]} {b[1]} {b[2]} air")
    roof = bunker.ceiling(cells)
    _chat(f"execute in minecraft:the_end run fill {roof[0][0]} {roof[0][1]} {roof[0][2]} "
          f"{roof[-1][0]} {roof[-1][1]} {roof[-1][2]} end_stone")
    # And a floor. Hollowing the corridor out of the island also removes whatever the corridor was standing on: the
    # first runs teleported the player into a tunnel with nothing under it, and the trace shows it falling 65 → 62
    # and dying in the open. A bunker without a floor is a shaft.
    floor = [(c[0], c[1] - 1, c[2]) for c in cells]
    _chat(f"execute in minecraft:the_end run fill {floor[0][0]} {floor[0][1]} {floor[0][2]} "
          f"{floor[-1][0]} {floor[-1][1]} {floor[-1][2]} end_stone")
    # Seal the far end too, so the corridor is a dead end rather than a passage an enderman can walk around into.
    far = (cells[-1][0] + 1, cells[-1][1], cells[-1][2])
    _chat(f"execute in minecraft:the_end run fill {far[0]} {far[1]} {far[2]} {far[0]} {far[1] + 1} {far[2]} end_stone")
    PIT[:] = [bunker.mouth(side, top), bunker.fire(side, top), bunker.retreat(side, top),
              bed_cell(side, top), top]
    stand = bunker.retreat(side, top)
    _chat(f"execute in minecraft:the_end run tp @p {stand[0] + 0.5} {stand[1]} {stand[2] + 0.5}")
    time.sleep(0.5)


def _hp_lost_over(seconds):
    """Run nothing, just hold position, and report the health lost. The measurement the bunker exists to improve."""
    def run(ctx):
        from . import api
        start = api.get("/state")["health"]
        t0 = time.time()
        while time.time() - t0 < seconds:
            time.sleep(1.0)
            if api.get("/state")["dead"]:
                break
        LAST_HOLD[:] = [start - api.get("/state")["health"]]
        return True
    return run


LAST_HOLD = [None]


def _station_for(seconds):
    """Hold station near a perched dragon for `seconds` and report the health left."""
    def run(ctx):
        from . import api
        from .combat import station
        t0 = time.time()
        # Decorated (heartbeat for perception); rounds ≈ one per half second of the scenario.
        try:
            station(ctx, (0, 0), band=(14, 20), clear=1.0, rounds=max(4, seconds * 2))
        finally:
            api.post("/stop")
        return True
    return run


SCENARIOS["dragon_station"] = {
    "doc": "Dragon perched on the portal, speedrun kit → 30 s of holding station costs at most 4 hp and never stands "
           "within 8 blocks of a body part (both bench fights died here at 9 blocks in front of the head).",
    "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    # 14–18 blocks out: the skill has to hold the distance itself. Spawning at 5–9 put the player inside the breath
    # and health was 10 before the skill's first tick.
    "setup": ["spreadplayers 0 0 14 18 false @p", *SPEEDRUN_END_KIT],
    "before": _summon_perched_dragon(6),
    "run": _station_for(30),
    "check": lambda api, inv: api.get("/state")["health"] >= 16 and not api.get("/state")["dead"],
    "detail": lambda inv: f"dragon hp {_dragon_health()}",
    "budget": 45,
}
def _perch_dragon_only(phase=6):
    """Perch the dragon without touching the player: the full hook runs spreadplayers, which teleported the player
    out of the pit that the setup had just dug ("not in the pit: no bomb from the open")."""
    def before(ctx):
        from .end import find_pillar_top
        top = find_pillar_top()
        if top is None:
            raise SetupInvalid("no exit-portal bedrock found near the island centre")
        count = lambda: server_count(_command("execute in minecraft:the_end as @p at @s if entity "
                                              "@e[type=minecraft:ender_dragon,distance=..300]", []))
        if count() < 1:
            _chat(f"execute in minecraft:the_end run summon ender_dragon 0 {top} 0 {{DragonPhase:{phase}}}")
            time.sleep(2)
        sel = "@e[type=minecraft:ender_dragon,limit=1]"
        for cmd in (f"data modify entity {sel} Health set value 200f",
                    f"data modify entity {sel} DragonPhase set value {phase}",
                    f"tp {sel} 0 {top} 0"):
            _chat(f"execute in minecraft:the_end run {cmd}")
        time.sleep(1.5)
        # A previous scenario can leave the End without a dragon, and a fresh summon needs a moment to sync: one
        # check right after the commands reported "no living dragon after setup" while one was on its way.
        for attempt in range(8):
            if count() >= 1:
                break
            if attempt % 3 == 2:
                _chat(f"execute in minecraft:the_end run summon ender_dragon 0 {top} 0 {{DragonPhase:{phase}}}")
            time.sleep(1.0)
        else:
            raise SetupInvalid("no living dragon after setup")
        # Full health before the skill starts: a dragon left perched by the previous scenario chews on the player
        # during this hook, and the run then failed as "player dead before the skill started".
        _chat("execute in minecraft:the_end run effect give @p minecraft:instant_health 2 10 true")
        _chat("execute in minecraft:the_end run effect clear @p minecraft:instant_health")
    return before


def _summon_endermen(n=2, angry=True):
    """After setup: `n` endermen right next to the player, angry at them (the End is full of endermen and one hit
    takes 7 hp; the dragon benches never handled them)."""
    def before(ctx):
        from . import api
        # Leftovers from the previous attempt are still angry and still hunting: they killed the next scenario during
        # its own setup. Clear them, then summon exactly the ones this run is about.
        _chat("execute in minecraft:the_end run kill @e[type=minecraft:enderman,distance=..64]")
        time.sleep(0.5)
        s = api.get("/state")
        for i in range(n):
            x, z = round(s["x"]) + (3 if i % 2 else -3), round(s["z"]) + (3 if i > 1 else 0)
            _chat(f"execute in minecraft:the_end run summon enderman {x} {round(s['y'])} {z}")
        if angry:
            time.sleep(1)
            # Anger them by hand: the vanilla way is looking at them, which the agent must not need to do.
            _chat("execute in minecraft:the_end run damage @e[type=minecraft:enderman,distance=..10] 1 "
                  "minecraft:player_attack by @p")
        time.sleep(1)
    return before


def _end_skill_for(name, seconds=None):
    """Run one End skill by name (the fight is a chain of small skills, each with its own scenario)."""
    def run(ctx):
        from . import api, end
        fn = getattr(end, name)
        try:
            # Through the decorator, not __wrapped__: the driver writes the skill heartbeat, and perception uses that
            # to know a fight skill is running. Without it every dig was interrupted by "enderman after us".
            fn(ctx)
        finally:
            api.post("/stop")
        return True
    return run


SCENARIOS["bunker_hold_60s"] = {
    "doc": "Bunker pre-built, dragon perched, player in the retreat cell → 60 s costs less than 4 hp.",
    "module": "end", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    "setup": ["spreadplayers 0 0 10 14 false @p", *SPEEDRUN_END_KIT],
    # Summon FIRST, build second. _summon_perched_dragon runs its own spreadplayers to put the player on the island
    # floor, which threw the player straight back out of a tunnel built before it: the trace of the first run shows
    # it wandering the open island at y 64 for the whole "bunker" test, and dying there.
    "before": lambda ctx: (_summon_perched_dragon(6)(ctx), _fill_bunker(ctx)),
    "run": _hp_lost_over(60),
    "check": lambda api, inv: (LAST_HOLD[0] or 0) < 4 and not api.get("/state")["dead"],
    "detail": lambda inv: f"hp lost {LAST_HOLD[0]}",
    "budget": 80,
}
SCENARIOS["bunker_window"] = {
    "doc": "Bunker pre-built, dragon perched → one window from inside cover takes >= 20 hp off the dragon.",
    "module": "end", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    "setup": ["spreadplayers 0 0 10 14 false @p", *SPEEDRUN_END_KIT],
    "before": lambda ctx: (_summon_perched_dragon(6)(ctx), _fill_bunker(ctx)),
    "run": _end_skill_for("bed_bomb_window"),
    "check": lambda api, inv: (_dragon_health() or 200) <= 180 and not api.get("/state")["dead"],
    "detail": lambda inv: f"dragon hp {_dragon_health()}, beds left {inv.count('bed')}",
    "budget": 40,
}



SCENARIOS["build_bed_pit"] = {
    "doc": "Dragon still flying, speedrun End kit → the 1×2 pit beside the exit portal is dug and the player stands "
           "in it with the head below floor level.",
    "module": "end", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    "setup": ["spreadplayers 0 0 10 14 false @p", *SPEEDRUN_END_KIT],
    "before": _summon_perched_dragon(0),        # circling, not perched: the pit is prepared before it lands
    "run": _end_skill_for("build_bed_pit"),
    "check": lambda api, inv: _in_built_pit(api),
    "detail": lambda inv: f"pit {_pit_info()}",
    "budget": 90,
}
SCENARIOS["await_perch"] = {
    "doc": "Pit built, dragon perched → await_perch returns within 30 s and the player is still ≥ 18 hp in the pit.",
    "module": "end", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    "setup": ["spreadplayers 0 0 10 14 false @p", *SPEEDRUN_END_KIT],
    # Pit first (no dragon down yet), then perch one: build_bed_pit refuses to dig while a dragon sits on the portal,
    # so the old order (perch, then dig) waited forever.
    "before": lambda ctx: (_build_pit_first(ctx), _perch_dragon_only(6)(ctx)),
    "run": _end_skill_for("await_perch"),
    "check": lambda api, inv: api.get("/state")["health"] >= 18 and not api.get("/state")["dead"],
    "detail": lambda inv: f"dragon hp {_dragon_health()}",
    "budget": 45,
}
SCENARIOS["bed_bomb_window"] = {
    "doc": "Pit built, dragon perched → one bomb takes ≥ 20 hp off the dragon and the player is back in the pit alive.",
    "module": "end", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    "setup": ["spreadplayers 0 0 10 14 false @p", *SPEEDRUN_END_KIT],
    "before": lambda ctx: (_build_pit_first(ctx), _perch_dragon_only(6)(ctx)),
    "run": _end_skill_for("bed_bomb_window"),
    "check": lambda api, inv: (_dragon_health() or 200) <= 180 and not api.get("/state")["dead"],
    "detail": lambda inv: f"dragon hp {_dragon_health()}, beds left {inv.count('bed')}",
    "budget": 45,
}


def _build_pit_first(ctx):
    """Setup hook: the pit skill itself, so the scenario under test starts from a built pit — then heal, because
    digging it next to an already perched dragon costs most of the health bar and the skill under test isn't to
    blame (a real run digs the pit while the dragon still circles)."""
    from .end import build_bed_pit
    # Send any dragon left perched by the previous scenario back into the air first, or the pit is dug under its
    # breath and the player is at 1 hp before the skill under test starts.
    _chat("execute in minecraft:the_end run data modify entity @e[type=minecraft:ender_dragon,limit=1] "
          "DragonPhase set value 0")
    time.sleep(1)
    build_bed_pit(ctx)       # decorated: writes the heartbeat perception checks
    _chat("execute in minecraft:the_end run effect give @p minecraft:instant_health 1 10 true")
    _chat("execute in minecraft:the_end run effect clear @p minecraft:instant_health")
    time.sleep(0.5)


def _in_built_pit(api):
    from .end import PIT, in_pit
    if not PIT:
        return False
    s = api.get("/state")
    return in_pit((s["blockX"], s["blockY"], s["blockZ"]), PIT[0])


def _pit_info():
    from .end import PIT
    return PIT[0] if PIT else None


def _caged_crystal_tower(ctx):
    """Setup hook: an obsidian pillar 16 blocks east of the player with a caged end crystal on top — the shape the
    speedrun has to break without a bow."""
    from . import api
    s = api.get("/state")
    x, y, z = round(s["x"]) + 16, round(s["y"]), round(s["z"])
    _chat(f"execute in minecraft:the_end run fill {x} {y} {z} {x} {y + 15} {z} obsidian")
    _chat(f"execute in minecraft:the_end run summon end_crystal {x}.5 {y + 16} {z}.5 {{ShowBottom:1b}}")
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        _chat(f"execute in minecraft:the_end run fill {x + dx} {y + 16} {z + dz} {x + dx} {y + 17} {z + dz} iron_bars")
    time.sleep(1)


def _break_nearest_crystal(ctx):
    from . import api, end
    from .world import entities
    crystals = [e for e in entities(64) if e["type"] == "minecraft:end_crystal"]
    if not crystals:
        raise SetupInvalid("no end crystal in range after setup")
    try:
        for _ in end.break_caged_crystal.__wrapped__(ctx, crystals[0]):
            pass
    finally:
        api.post("/stop")
    return True


SCENARIOS["break_caged_crystal"] = {
    "doc": "A caged end crystal on a 16-block obsidian pillar, speedrun kit (no bow) → towered up, bars broken, "
           "crystal destroyed and the player alive.",
    "module": "end", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    "setup": ["spreadplayers 0 0 20 30 false @p", *SPEEDRUN_END_KIT, "give @p cobblestone 64"],
    "before": _caged_crystal_tower,
    "run": _break_nearest_crystal,
    "check": lambda api, inv: not [e for e in __import__("bonobo.world", fromlist=["entities"]).entities(64)
                                   if e["type"] == "minecraft:end_crystal"] and not api.get("/state")["dead"],
    "budget": 180,
}
def _angry_endermen_within(radius=6):
    from .world import entities
    from . import api
    s = api.get("/state")
    here = (s["x"], s["y"], s["z"])
    return [e for e in entities(32) if e["type"] == "minecraft:enderman" and e.get("angry")
            and __import__("math").dist((e["x"], e["y"], e["z"]), here) <= radius]


SCENARIOS["endermen_at_the_dragon"] = {
    "doc": "Two angry endermen next to the player while the dragon perches → 30 s of station shakes them off "
           "(never trades hits) and health stays ≥ 14.",
    "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    "setup": ["spreadplayers 0 0 14 18 false @p", *SPEEDRUN_END_KIT],
    "before": lambda ctx: (_summon_perched_dragon(6)(ctx), _summon_endermen(2)(ctx)),
    "run": _station_for(30),
    "check": lambda api, inv: (api.get("/state")["health"] >= 14 and not api.get("/state")["dead"]
                               and not _angry_endermen_within(6)),
    "detail": lambda inv: f"angry endermen near {len(_angry_endermen_within(8))}, dragon hp {_dragon_health()}",
    "budget": 45,
}
SCENARIOS["shake_enderman"] = {
    "doc": "Two angry endermen on the player, pit dug → shake_enderman loses them (pit, water or distance) without "
           "attacking, player alive.",
    "module": "end", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    "setup": ["spreadplayers 0 0 14 18 false @p", *SPEEDRUN_END_KIT],
    # No pit here: shaking an enderman is water → walk away → cover only if it is already at our feet, and digging one
    # next to two angry endermen is what killed this scenario twice during its own setup.
    "before": _summon_endermen(2),
    "run": _end_skill_for("shake_enderman"),
    "check": lambda api, inv: not _angry_endermen_within(6) and not api.get("/state")["dead"],
    "detail": lambda inv: f"angry endermen near {len(_angry_endermen_within(8))}",
    "budget": 90,
}
SCENARIOS["strike_perched_dragon"] = {
    "doc": "End island, the dragon sitting on the portal, speedrun kit without beds → 40 s of melee takes ≥ 30 hp.",
    "module": "combat", "raw": True, "combat": True, "dimension": "minecraft:the_end",
    "setup": ["spreadplayers 0 0 5 8 false @p", "clear @p", "give @p stone_sword", "give @p cobblestone 64",
              "give @p cooked_beef 16"],
    "before": _summon_perched_dragon(6),
    "run": _dragon_health_after(40),
    "check": lambda api, inv: (_dragon_health() or 200) <= 170,
    "detail": lambda inv: f"dragon hp {_dragon_health()}",
    "budget": 45,
}


def _decision(name, expect_pick=None, expect_filtered=None):
    """A decision scenario: record what the brain would pick here (nothing is executed), freeze it as a golden case
    for offline regression, and pass when the pick matches."""
    def run(ctx):
        from . import decide
        row = decide.record_live(BRAIN)
        path = decide.save_golden(name, row, expect_pick, expect_filtered)
        LAST_DECISION[:] = [row, path]
        return row["pick"]
    return run


LAST_DECISION = []


def _decision_ok(expect_pick=None, expect_filtered=None):
    def check(api, inv):
        if not LAST_DECISION:
            return False
        row = LAST_DECISION[0]
        ok = expect_pick is None or row["pick"] == expect_pick
        for cand, part in (expect_filtered or {}).items():
            ok = ok and part in str(row["filtered"].get(cand, ""))
        return ok
    return check


SCENARIOS["decide_bag_full_shaft"] = {
    "doc": "Day, bottom of a 1×1 shaft 6 deep, bag full of cobblestone → the pick is moving to open space.",
    "module": "brain", "mod": [],
    "setup": [f"fill {_c(at(-4, -8, -4))} {_c(at(4, -1, 4))} stone",
              f"fill {_c(at(0, -7, 0))} {_c(at(0, -1, 0))} air",
              f"tp @p {_c(at(0.5, -7, 0.5))}", "clear @p", "give @p stone_pickaxe", "give @p cobblestone 2240"],
    "expect": [(at(0, -7, 0), at(0, -1, 0), "*", 0, 0)],
    "run": _decision("bag_full_in_shaft", "open space"),
    "check": _decision_ok("open space"),
    "detail": lambda inv: f"picked {LAST_DECISION[0]['pick']}; top {LAST_DECISION[0]['top'][:3]}"
    if LAST_DECISION else "",
    "budget": 10,
}
SCENARIOS["decide_no_kit_at_portal"] = {
    "doc": "Next to a lit portal with no food or gold → Nether goals are filtered ('Nether kit not ready').",
    "module": "brain", "mod": [],
    "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone",
              f"fill {_c(at(-1, 0, 3))} {_c(at(2, 4, 3))} obsidian",
              f"fill {_c(at(0, 1, 3))} {_c(at(1, 3, 3))} nether_portal[axis=x]",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p iron_pickaxe", "give @p cobblestone 16"],
    "expect": [(at(0, 1, 3), at(1, 3, 3), "nether_portal", 6, 6)],
    "before": lambda ctx: BRAIN.mem.add_machine("nether_portal", at(-1, 0, 3), 0, "minecraft:overworld", ["portal"]),
    "run": _decision("no_kit_at_portal", None, {"nether fortress": "Nether kit not ready"}),
    "check": _decision_ok(None, {"nether fortress": "Nether kit not ready"}),
    "detail": lambda inv: f"picked {LAST_DECISION[0]['pick']}; fortress: "
    f"{LAST_DECISION[0]['filtered'].get('nether fortress')}" if LAST_DECISION else "",
    "budget": 10,
}


# Which mod features each scenario really exercises (default: all). Narrow tags keep results valid across jar
# changes elsewhere.
for _name, _tags in {
    "craft_eyes": ["craft", "use", "travel"], "gold_helmet_swap": [], "loot_chest": ["travel", "use"],
    "water_clutch": ["nets", "use"], "enter_end": ["travel"], "activate_end_portal": ["travel", "use"],
}.items():
    SCENARIOS[_name]["mod"] = _tags
# Faster game ticks only help server-side waiting (furnaces, piglin inspection): the player's own actions run on
# client ticks (3 logs still took 10.6 s at rate 60), so only those scenarios speed up and times stay wall seconds.
for _name in ("iron_ingots", "barter_piglin"):
    SCENARIOS[_name]["tick_rate"] = 60

LAST_FEEDBACK = []


def locate_reply(feedback):
    """Pure: (x, z) from '/locate structure' feedback ('... is at [x, ~, z] (N blocks away)'), or None."""
    import re
    for f in feedback:
        for line in f.get("reply", []):
            m = re.search(r"at \[(-?\d+), [^,]+, (-?\d+)\]", line)
            if m and "locate" in f.get("cmd", ""):
                return int(m.group(1)), int(m.group(2))
    return None


def _stronghold_error():
    import math
    from .memory import Memory
    real = locate_reply(LAST_FEEDBACK)
    sites = Memory(NOTES).sites("minecraft:overworld", kinds=["stronghold"])
    if not real or not sites:
        return 1e9
    return math.dist(real, (sites[0]["pos"][0], sites[0]["pos"][2]))


def _near_real_stronghold(ctx):
    """Put a deliberately-off estimate in memory and stand on the surface above it."""
    from . import api
    real = locate_reply(LAST_FEEDBACK)
    if not real:
        raise SetupInvalid("no /locate answer for the stronghold")
    est = (real[0] + 20, 30, real[1] - 12)
    ctx.mem.add_site("stronghold", est, "minecraft:overworld", name="stronghold")
    api.post("/chat", {"message": f"/spreadplayers {est[0]} {est[2]} 0 4 false @p"})
    time.sleep(3)
SCENARIOS["cross_lava_3"] = _lava_lake(3)
SCENARIOS["cross_lava_8"] = _lava_lake(8)
SCENARIOS["gather_logs_birch"] = {
    **SCENARIOS["gather_logs"],
    "doc": "Two birches (taller, thin crowns); empty bag → 4 logs.",
    "setup": [c.replace("minecraft:oak", "minecraft:birch") for c in SCENARIOS["gather_logs"]["setup"]],
    "expect": [(at(-8, -1, -8), at(8, -1, 8), "grass_block", 285, 289),
               (at(-8, 0, -8), at(8, 8, 8), "birch_log", 8, 20)],
}


# Route goals → the scenarios that prove their skills. The review lists them; a route goal whose scenario is not
# ready is flagged, so a live-run failure there is expected rather than a surprise.
GOAL_SCENARIOS = {
    "water bucket": ["fill_water_bucket"], "nether portal": ["cast_obsidian", "build_light_portal"],
    "stock logs": ["gather_logs", "gather_logs_birch"], "nether fortress": ["enter_nether", "cross_lava_8"],
    "blaze rods (7)": ["fight_blaze"], "piglin barter": ["barter_piglin"],
    "activate end portal": ["activate_end_portal"], "enter the End": ["enter_end"],
}


_FAILING = {"t": 0, "names": set()}


def failing_goals(ttl=60):
    """Route goals whose bench scenario has a 'fail' verdict for the current code (cached: hashing sources each
    round would cost more than the round). The route skips them while another goal of the segment can run."""
    now = time.time()
    if now - _FAILING["t"] < ttl:
        return _FAILING["names"]
    table = load_table()
    names = set()
    for goal, scen in GOAL_SCENARIOS.items():
        try:
            if any(verdict(table, n, code_for(n)) == "fail" for n in scen if n in SCENARIOS):
                names.add(goal)
        except OSError:
            continue
    _FAILING.update(t=now, names=names)
    return names


def readiness_lines(table=None):
    """Lines for the review: every scenario's verdict for the current code, then route goals not yet proven."""
    table = load_table() if table is None else table
    out, verdict = [], {}
    for name in SCENARIOS:
        st, med = status(table, name, code_for(name))
        verdict[name] = st
        budget = SCENARIOS[name]["budget"]
        out.append(f"  {name:22} {st:9}" + ("" if med is None else f" median {med}s (budget {budget}s)"))
    unproven = [g for g, names in GOAL_SCENARIOS.items() if any(verdict.get(n) != "scenario" for n in names)]
    if unproven:
        out.append(f"  route goals not proven on the bench: {', '.join(unproven)}")
    return out


def _trades(inv):
    """Items a piglin trade can give (anything but what the scenario handed out)."""
    given = ("minecraft:gold_ingot", "minecraft:golden_helmet", "minecraft:iron_sword", "minecraft:iron_helmet")
    return sum(s["count"] for s in inv.slots if s["id"] not in given)


def _drain(result):
    """Run a skill generator to its end when it's called directly (the @skill wrapper normally drives it)."""
    if hasattr(result, "__next__"):
        for _ in result:
            pass
    return result


class SetupInvalid(Exception):
    """The scenario wasn't built as specified: the run says nothing about the skill."""


def _count_blocks(api, lo, hi, name):
    from .world import Region
    return sum(1 for n in Region(lo, hi).blocks.values() if n == name)


def _near(api, pos, r):
    import math
    s = api.get("/state")
    return math.dist((s["x"], s["y"], s["z"]), pos) <= r


# ---------------------------------------------------------------- pure helpers (tested offline)

ERROR_MARKS = ("not loaded", "Incorrect argument", "Unknown or incomplete", "<--[HERE]", "Unknown ", "Invalid ",
               "is not within", "Too many blocks", "Could not", "Failed to", "Expected ", "unexpected error",
               "out of the world")


def feedback_errors(lines):
    """Pure: the chat feedback lines that mean a command didn't do its job. 'No entity was found' (nothing to
    kill), 'has no effects to remove', 'No items were found' (nothing to clear) and 'No blocks were filled'
    (already that block) are fine."""
    return [l for l in lines if any(m in l for m in ERROR_MARKS)]


def setup_mismatches(blocks, expect):
    """Pure: expected signature counts that don't hold. `blocks` maps (x, y, z) → name (non-air only)."""
    bad = []
    for lo, hi, name, lo_n, hi_n in expect:
        n = sum(1 for p, b in blocks.items()
                if all(lo[i] <= p[i] <= hi[i] for i in range(3)) and (name == "*" or b == name))
        if not lo_n <= n <= hi_n:
            bad.append(f"{name} in {lo}..{hi}: {n}, expected {lo_n}..{hi_n}")
    return bad


def classify(exc, ok):
    """Pure: which layer failed. Only nav and skill failures say something about the skill's readiness."""
    if ok:
        return "pass"
    name = type(exc).__name__ if exc is not None else ""
    if name == "SetupInvalid":
        return "setup"
    if name in ("GameUnreachable", "PlayerTookControl", "HarnessError"):
        return "harness"
    if name in ("NavFailed", "TaskStuck"):
        return "nav"
    msg = str(exc or "")
    if "HTTP" in msg or "unknown task" in msg.lower():
        return "mod"
    return "skill"


def silent_failure(lines, result):
    """Pure: the exception a silent failure stands for — NavFailed when the last failed task was movement."""
    from .api import McError, NavFailed
    last = next((l.strip() for l in reversed(lines) if " failed " in l), "")
    if last.split(" ", 1)[0] in ("travel", "goto"):
        return NavFailed(last)
    return McError(last or f"skill returned {result!r} without the outcome")


def module_deps(module, pkg_dir=PKG):
    """Pure-ish (reads source files): the module and every bonobo module it imports, transitively."""
    seen, todo = set(), [module]
    while todo:
        m = todo.pop()
        path = os.path.join(pkg_dir, m + ".py")
        if m in seen or not os.path.exists(path):
            continue
        seen.add(m)
        with open(path) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module:
                    todo.append(node.module.split(".")[0])
                else:
                    todo.extend(a.name for a in node.names)
    return sorted(seen)


def dep_hash(module, pkg_dir=PKG):
    h = hashlib.sha1()
    for m in module_deps(module, pkg_dir):
        with open(os.path.join(pkg_dir, m + ".py"), "rb") as f:
            h.update(m.encode() + f.read())
    return h.hexdigest()[:10]


# The mod's Java sources, when they happen to be checked out next to this repository. They are a different
# project, so this is optional: with MC_MOD_SRC unset (the normal case for anyone who installed the mod from a
# release) readiness falls back to the jar version the mod reports over /status. Hashing the sources is the finer
# tool — it re-tests only the scenarios whose feature actually changed — but it cannot be a requirement.
JAVA = os.path.expanduser(os.environ.get("MC_MOD_SRC", ""))
# The mod features a scenario can depend on → their Java sources. A pathfinder change re-tests movement scenarios
# only, not a crafting scenario (a jar version bump used to reset every result).
# Core = what every task runs through. WorldInfo (/state fields) and HttpApi (routes like /plan) change often and
# rarely change behaviour: they are their own features, so adding a /state field doesn't reset every scenario.
MOD_CORE = ["task/Task.java", "task/TaskFactory.java", "util/WorldUtil.java", "util/InvUtil.java", "Agent.java"]
MOD_FILES = {
    "state": ["WorldInfo.java"],
    "http": ["HttpApi.java"],
    "travel": ["util/BuildPathfinder.java", "util/Pathfinder.java", "task/TravelTask.java", "task/GotoTask.java",
               "util/LavaGuard.java"],
    "use": ["task/UseItemTask.java", "task/PlaceTask.java", "task/UseBlockTask.java"],
    "mine": ["task/MineTask.java", "task/SequenceTask.java", "task/CollectTask.java"],
    "combat": ["task/AttackTask.java", "task/InteractEntityTask.java"],
    "craft": ["task/CraftTask.java"],
    "nets": ["Agent.java", "util/WaterClutch.java"],
}


def mod_hash(tags=None, java=JAVA):
    """Identity of the mod behind `tags`: a hash of its Java sources when they are available, else its jar version.

    Hashing the sources is the finer instrument — a pathfinder change re-tests movement scenarios and leaves crafting
    results standing — but it needs a checkout of a different repository, which most people running this will not
    have. Without one, fall back to the version the mod reports: coarser (any release resets every scenario) and
    still correct.

    What must never happen is falling back to a CONSTANT. An empty source directory would hash to the same digest
    forever, and every stale scenario result would look fresh.
    """
    if not java or not os.path.isdir(java):
        return "jar-" + _mod_version()
    files = list(MOD_CORE)
    # Default: every behaviour feature, not the /state and HTTP plumbing (tag those explicitly where they matter).
    for t in ([k for k in MOD_FILES if k not in ("state", "http")] if tags is None else tags):
        files += MOD_FILES[t]
    h = hashlib.sha1()
    found = 0
    for rel in sorted(set(files)):
        path = os.path.join(java, rel)
        if os.path.exists(path):
            found += 1
            with open(path, "rb") as f:
                h.update(rel.encode() + f.read())
    if not found:
        return "jar-" + _mod_version()
    return h.hexdigest()[:6]


def _mod_version():
    """The running mod's version, or "unknown" when the game is not up (readiness is then simply not trusted)."""
    from . import api
    try:
        return str(api.get("/status").get("version") or "unknown")
    except Exception:
        return "unknown"


def jar_matches_source():
    """The running jar must be the one built from these sources, or results would be credited to the wrong code."""
    from . import api
    running = api.status()["version"].split("+")[0]
    with open(os.path.join(JAVA, "..", "..", "..", "..", "..", "gradle.properties")) as f:
        built = next(l.split("=", 1)[1].strip() for l in f if l.startswith("mod_version="))
    return running == built, running, built


_CODE = {}


def code_for(name):
    """Readiness key: the skill's Python modules, the scenario's own layout (a broken setup — fences covering the
    pen — must not keep counting against the skill after it is fixed) and the Java sources it uses. Frozen per process:
    editing sources during a bench run changed the key mid-run and a finished scenario was run again 2× on old code."""
    if name not in _CODE:
        _CODE[name] = _code_for(name)
    return _CODE[name]


def _code_for(name):
    sc = SCENARIOS[name]
    layout = hashlib.sha1(repr((sc["setup"], sc.get("expect"), sc["budget"])).encode()).hexdigest()[:6]
    tags = sc.get("mod")
    if sc.get("mod_extra"):
        tags = sorted(set(tags if tags is not None else [k for k in MOD_FILES if k not in ("state", "http")])
                      | set(sc["mod_extra"]))
    return f"{dep_hash(sc['module'])}{layout}-{mod_hash(tags)}"


# Settled scenarios: obvious mechanics that passed and never failed are not re-run for code or jar changes (placing
# eyes, throwing gold at piglins). Re-test by name with --force, or drop the name here after a live-run problem.
STABLE = {"activate_end_portal", "barter_piglin", "enter_end", "craft_eyes", "gold_helmet_swap", "loot_chest",
          "fill_water_bucket", "enter_nether", "return_from_nether", "relight_portal", "cast_obsidian",
          "build_light_portal",
          # deterministic layouts with no opponent: once they pass, logic fixes don't need a game run to prove them
          "craft_stone_tools", "iron_ingots", "hunt_food", "gather_logs", "gather_logs_birch", "recover_items",
          "retreat_from_nether", "return_to_portal", "find_fortress", "water_clutch", "cross_lava_3", "cross_lava_8",
          "cross_lava_lake", "decide_bag_full_shaft", "decide_no_kit_at_portal"}


# Only fights change run to run (mob AI, knockback, fireballs). Everything else is settled once it passes.
FIGHTS = {"fight_blaze", "ghast_fireball", "bed_bomb_once", "bed_bomb_kill", "strike_perched_dragon", "fight_dragon"}


def settled(table, name):
    """Pure: a non-fight scenario whose latest counted run (under any code) passed. One pass settles it (user,
    2026-09-16): deterministic layouts don't need a second confirmation; a later failure re-opens it."""
    if name in FIGHTS:
        return False
    runs = sorted((r for c in table.get(name, {}).values() for r in c if r.get("cls", "skill") not in UNCOUNTED),
                  key=lambda r: r.get("t", 0))
    return bool(runs) and runs[-1]["ok"]


def verdict(table, name, code):
    """Pure: 'pass' / 'fail' once 2 counted runs agree (or 2 of 3), else None — a stable result isn't re-run."""
    counted = [r for r in table.get(name, {}).get(code, []) if r.get("cls", "skill") not in UNCOUNTED][-3:]
    passes = sum(r["ok"] for r in counted)
    if passes >= 2:
        return "pass"
    # Stable scenarios run once: a first pass is enough when the scenario never failed under earlier code.
    history = [r for c, runs in table.get(name, {}).items() if c != code for r in runs
               if r.get("cls", "skill") not in UNCOUNTED]
    if counted == [r for r in counted if r["ok"]] and len(counted) == 1 and history and all(r["ok"] for r in history):
        return "pass"
    if len(counted) - passes >= 2:
        return "fail"
    return None


# ---------------------------------------------------------------- readiness table (pure helpers are tested offline)

def load_table(path=None):
    try:
        with open(path or TABLE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def record(table, scenario, code, ok, seconds, note="", cls="skill"):
    """Pure: append one result (last 10 kept per scenario and code version)."""
    runs = table.setdefault(scenario, {}).setdefault(code, [])
    runs.append({"ok": bool(ok), "s": round(seconds, 1), "note": note[:120], "cls": cls, "t": int(time.time())})
    del runs[:-10]
    return table


def status(table, scenario, code):
    """Pure: 'untested' | 'failing' | 'scenario' (≥2 of the last 3 counted runs passed) and the median pass time.
    Setup and harness failures don't count: they say nothing about the skill."""
    runs = [r for r in table.get(scenario, {}).get(code, []) if r.get("cls", "skill") not in UNCOUNTED]
    if not runs:
        return "untested", None
    last = runs[-3:]
    passes = sorted(r["s"] for r in runs if r["ok"])
    median = passes[len(passes) // 2] if passes else None
    return ("scenario" if sum(r["ok"] for r in last) >= 2 else "failing"), median


def save_table(table, path=None):
    path = path or TABLE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(table, f, indent=1)


# ---------------------------------------------------------------- live bench

class _Console(io.TextIOBase):
    """Tee stdout so the report carries the skill's own log lines."""
    def __init__(self, real):
        self.real, self.lines = real, []

    def write(self, s):
        self.real.write(s)
        self.lines.extend(l for l in s.splitlines() if l.strip())
        return len(s)

    def flush(self):
        self.real.flush()


def _chat_log():
    from . import api
    return os.path.join(api.INSTANCE, "logs", "latest.log")


def _command(cmd, feedback, timeout=2.0):
    """Send one command and wait for its chat feedback in the client log; returns the new chat lines."""
    from . import api
    path = _chat_log()
    size = os.path.getsize(path)
    api.post("/chat", {"message": "/" + cmd})
    t0, lines = time.time(), []
    while time.time() - t0 < timeout:
        time.sleep(0.15)
        with open(path, "rb") as f:
            f.seek(size)
            new = f.read().decode(errors="replace")
        lines = [l.split("[CHAT] ", 1)[1] for l in new.splitlines() if "[CHAT] " in l]
        if lines:
            break
    feedback.append({"cmd": cmd, "reply": lines})
    return lines


def _batch(cmds, feedback, settle=0.6):
    """Send commands back to back, then read all their chat replies at once; any error line fails the setup."""
    from . import api
    path = _chat_log()
    size = os.path.getsize(path)
    for cmd in cmds:
        api.post("/chat", {"message": "/" + cmd})
    time.sleep(settle)
    with open(path, "rb") as f:
        f.seek(size)
        lines = [l.split("[CHAT] ", 1)[1] for l in f.read().decode(errors="replace").splitlines() if "[CHAT] " in l]
    feedback.append({"cmd": f"batch of {len(cmds)}", "cmds": cmds, "reply": lines})
    bad = feedback_errors(lines)
    if bad:
        raise SetupInvalid(f"setup batch → {bad[0]}")


def _checked(cmd, feedback):
    bad = feedback_errors(_command(cmd, feedback))
    if bad:
        raise SetupInvalid(f"/{cmd} → {bad[0]}")


def server_count(lines):
    """Pure: N from '/execute if entity' feedback ('Test passed, count: N'); 0 for 'Test failed'."""
    import re
    for line in lines:
        m = re.search(r"count: (\d+)", line, re.IGNORECASE)   # the server says "Test passed. Count: 1"
        if m:
            return int(m.group(1))
    return 0


def entity_mismatches(ents, expect):
    """Pure: expected entity counts [(type, min)] that don't hold (with what was seen, to tell a sync delay from a
    mob that died or wandered off)."""
    seen = sorted({e["type"].split(":")[-1] for e in ents})
    return [f"{t}: {sum(1 for e in ents if e['type'] == t)}, expected ≥ {n} (seen: {', '.join(seen) or 'nothing'})"
            for t, n in expect if sum(1 for e in ents if e["type"] == t) < n]


def _setup(name, sc, feedback):
    from . import api
    from .world import Region, entities
    if api.get("/state").get("dead"):
        api.post("/respawn")
        time.sleep(2)
    api.post("/resume")          # a pause menu freezes the integrated server: commands would do nothing
    time.sleep(0.5)
    lo, hi = at(*BOX[0]), at(*BOX[1])
    combat = sc.get("combat", False)
    dim = sc.get("dimension", "minecraft:overworld")
    moved = api.get("/state")["dimension"] != dim

    def ex(cmd):                 # every command runs in the scenario's dimension (tp included: it moves us there)
        return f"execute in {dim} run {cmd}"

    if sc.get("raw"):
        # Real-world scenarios (a real stronghold, the real dragon): no box, just the body reset and the commands.
        if moved:
            # Another dimension first. "@p" inside "execute in <dim>" only finds players already there (a raw
            # Overworld scenario after an End one failed setup 8×): park with @a on the waiting glass of that
            # dimension, chunks loaded, before any scenario command.
            _checked(ex(f"forceload add {lo[0]} {lo[2]} {hi[0]} {hi[2]}"), feedback)
            probe = _c(at(0, BOX[1][1], 0))
            for _ in range(60):
                if not any("not loaded" in l for l in _command(ex(f"fill {probe} {probe} air"), feedback)):
                    break
                time.sleep(0.5)
            glass = _c(at(0, BOX[1][1] + 2, 0))
            _checked(ex(f"fill {glass} {glass} glass"), feedback)
            _checked(ex(f"tp @a[limit=1] {_c(at(0, BOX[1][1] + 3, 0))}"), feedback)
            time.sleep(4)
        for cmd in ("gamemode survival @p", "effect clear @p", "time set day", "weather clear",
                    f"difficulty {'normal' if combat else 'peaceful'}"):
            _checked(ex(cmd), feedback)
        for cmd in sc["setup"]:
            _checked(ex(cmd), feedback)
        _command(ex("effect give @p minecraft:instant_health 1 10 true"), feedback)
        _command(ex("effect give @p minecraft:saturation 1 10 true"), feedback)
        time.sleep(4 if moved else 1.5)
        s = api.get("/state")
        if s.get("dimension") != dim:
            raise SetupInvalid(f"player in {s.get('dimension')}, scenario needs {dim}")
        # Real-world scenarios need their actors too (a dragon fight ran with no dragon: "target not found").
        for t, want in sc.get("expect_entities", []):
            n = server_count(_command(ex(f"execute as @p at @s if entity @e[type={t},distance=..160]"), feedback))
            if n < want:
                raise SetupInvalid(f"{t}: {n} on the server, expected ≥ {want}")
        return

    # Empty bag first: a water bucket left from the previous scenario made any setup drop a water clutch trigger.
    for cmd in ("clear @p", "gamemode survival @p", "effect clear @p", "time set day", "weather clear",
                "gamerule spawn_mobs false", f"difficulty {'normal' if combat else 'peaceful'}",
                f"forceload add {lo[0]} {lo[2]} {hi[0]} {hi[2]}"):
        _checked(ex(cmd), feedback)
    # Wait until the box's chunks are really loaded: a 1-block fill answers "not loaded" until then.
    probe = _c(at(0, BOX[1][1], 0))
    for _ in range(60):
        if not any("not loaded" in l for l in _command(ex(f"fill {probe} {probe} air"), feedback)):
            break
        time.sleep(0.5)
    else:
        raise SetupInvalid("scenario chunks never loaded")
    # Wait above the box on a glass block: no fall while the box is rebuilt (a fall fired the water clutch).
    glass = _c(at(0, BOX[1][1] + 2, 0))
    _checked(ex(f"fill {glass} {glass} glass"), feedback)   # setblock errors when it's already glass; fill doesn't
    _checked(ex(f"tp @p {_c(at(0, BOX[1][1] + 3, 0))}"), feedback)
    if moved:
        time.sleep(3)            # the client loads the new dimension
    # Mobs of the previous scenario ("No entity was found" is fine).
    _command(ex(f"kill @e[type=!player,x={lo[0]},y={lo[1]},z={lo[2]},dx={hi[0] - lo[0]},dy={hi[1] - lo[1] + 6},"
                f"dz={hi[2] - lo[2]}]"), feedback)
    # Leftovers of the previous scenario (lava!) go first — up to above the waiting glass: water poured on the glass
    # (y 211, outside the box) kept flowing back into every later setup (cross_lava: 48 water, 9 obsidian).
    # Two fills around the glass layer: removing the glass under the player dropped them for a moment.
    # The whole layout in one burst, feedback checked once at the end (waiting for every reply cost ~10 min a round).
    top = _c((hi[0], hi[1] + 6, hi[2]))
    _batch([ex(f"fill {_c(lo)} {_c((hi[0], hi[1] + 1, hi[2]))} air"),
            ex(f"fill {_c((lo[0], hi[1] + 3, lo[2]))} {top} air"),
            # Fluids anywhere in the volume, the glass layer included (water beside the glass survived both fills
            # above and kept flooding cast_obsidian's pool: 47 water, 0 lava).
            ex(f"fill {_c(lo)} {top} air replace water"), ex(f"fill {_c(lo)} {top} air replace lava")]
           + [ex(cmd) for cmd in sc["setup"]], feedback)
    # The waiting glass must go once we're down: a 30-block fall landed on it 18 blocks early (water_clutch, hp 5).
    _checked(ex(f"fill {glass} {glass} air"), feedback)
    _command(ex("kill @e[type=item]"), feedback)
    _checked(ex("effect give @p minecraft:instant_health 1 10 true"), feedback)
    _checked(ex("effect give @p minecraft:saturation 1 10 true"), feedback)
    time.sleep(1.0)
    blocks = Region(lo, hi).blocks
    bad = setup_mismatches(blocks, sc.get("expect", []))
    for _ in range(12):          # summoned mobs and the health effect land a few ticks later (a ghast took > 3 s)
        # Count on the server: the client's entity list missed a summoned ghast 18 blocks away ("seen: nothing").
        ents = [f"{t}: {n} on the server, expected ≥ {want}" for t, want in sc.get("expect_entities", [])
                for n in [server_count(_command(ex(f"execute as @p at @s if entity @e[type={t},distance=..40]"),
                                                feedback))] if n < want]
        s = api.get("/state")
        if not ents and s.get("health", 0) >= 18:
            break
        if s.get("health", 0) < 18:
            _command(ex("effect give @p minecraft:instant_health 1 10 true"), feedback)
        time.sleep(0.5)
    bad += ents
    if bad:
        raise SetupInvalid("; ".join(bad))
    if s.get("dimension") != dim:
        raise SetupInvalid(f"player in {s.get('dimension')}, scenario needs {dim}")
    if s.get("dead") or s.get("health", 0) < 18:
        raise SetupInvalid(f"player not healthy after setup (hp {s.get('health')})")


def _trace(stop, out):
    from . import api
    while not stop.is_set():
        try:
            s = api.get("/state")
            out.append({"t": round(time.time(), 1), **{k: s.get(k) for k in
                        ("x", "y", "z", "health", "dead", "inWater", "inLava", "onGround", "screen")},
                        "task": (s.get("control") or {}).get("task")})
        except Exception as e:
            out.append({"t": round(time.time(), 1), "error": str(e)})
        stop.wait(0.2)


def _report(name, data):
    from .world import Inventory, Region
    try:
        data["inventory"] = [(s["id"], s["count"]) for s in Inventory().slots]
        lo, hi = at(*BOX[0]), at(*BOX[1])
        data["region"] = [[*p, n] for p, n in Region(lo, hi).blocks.items()]
    except Exception as e:
        data["report_error"] = str(e)
    folder = os.path.join(BENCH, name, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "report.json"), "w") as f:
        json.dump(data, f, indent=1, default=str)
    return folder


def run(name, make_ctx):
    """Set up and run one scenario (test world only). Returns (ok, seconds, note, cls, code).
    `make_ctx()` is called after the setup: a context built before it carries the old place's policy and
    dimension."""
    from . import api
    from .api import McError
    from .world import Inventory
    if not os.path.exists(FLAG):
        raise RuntimeError("scenarios run only in a test world: `mc.py scenario enable` there first")
    sc = SCENARIOS[name]
    code = code_for(name)
    feedback, trace, stop = [], [], threading.Event()
    console = _Console(sys.stdout)
    exc, ok, seconds, note = None, False, 0.0, ""
    sys.stdout = console
    from . import perception
    rate = sc.get("tick_rate")
    try:
        if rate:
            # Waiting-heavy scenarios (smelting, piglin inspection) run the game faster; skills wait in ticks, so
            # their logic is unchanged — only wall time shrinks. Always reset below.
            _command(f"tick rate {rate}", feedback)
        perception.PAUSED = True
        try:
            _setup(name, sc, feedback)
        except SetupInvalid as e:
            exc, note = e, f"SETUP_INVALID: {e}"
        finally:
            perception.PAUSED = False
        if exc is None:
            threading.Thread(target=_trace, args=(stop, trace), daemon=True).start()
            t0 = time.time()
            result = None
            crashed = False
            LAST_FEEDBACK[:] = feedback
            try:
                ctx = make_ctx()
                if sc.get("before"):
                    sc["before"](ctx)
                    ctx = make_ctx()      # the hook may move the player: rebuild policy/dimension there
                if api.get("/state").get("dead"):
                    # Dead before the skill began (a dragon fight started at 0 hp): the setup is invalid, not the skill.
                    raise SetupInvalid("player dead before the skill started")
                result = sc["run"](ctx)
            except Exception as e:     # the skill's own failure is a result, not a crash of the bench
                exc, note = e, f"{type(e).__name__}: {e}"
                if not isinstance(e, (api.McError, api.NotAvailable, SetupInvalid)):
                    crashed = True      # a bug in our own code is never a pass, whatever the world looks like after
                    # An unexpected crash ("IndexError: tuple index out of range") says nothing without its frames.
                    import traceback as _tb
                    note += " @ " + " < ".join(f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}"
                                               for f in reversed(_tb.extract_tb(e.__traceback__)[-4:]))
            seconds = time.time() - t0
            LAST_LINES[:] = console.lines          # slices read the cerebellum's own log for loops
            try:
                inv_after = Inventory()
                reached = bool(sc["check"](api, inv_after))
                # A crash (IndexError from our own code) passed the check once and was recorded as PASS.
                ok = reached and seconds <= sc["budget"] * 1.5 and not crashed
                if reached and not ok:
                    # The outcome happened, just too slowly: say so (it read "returned None without the outcome").
                    exc = exc or McError(f"outcome reached but over budget: {seconds:.0f}s > {sc['budget'] * 1.5:.0f}s")
                    note = note or str(exc)
                if sc.get("detail"):
                    note = (note + " " if note else "") + sc["detail"](inv_after)   # what a pass really produced
            except Exception as e:
                exc = exc or type("HarnessError", (Exception,), {})(f"check failed: {e}")
                note = note or f"check failed: {e}"
            if not ok and api.get("/state").get("dead") and type(exc).__name__ != "SetupInvalid":
                # Died: that's the result, whatever the skill did afterwards (20 "no route" travels after death).
                exc = McError("died")
                note = "died" + (f" ({note})" if note else "")
            if not ok and exc is None:
                # A skill that returned False / nothing without raising: blame the layer of its last failed task.
                exc = silent_failure(console.lines, result)
                note = f"{type(exc).__name__}: {exc} (outcome not reached in {seconds:.0f}s, budget {sc['budget']}s)"
    finally:
        stop.set()
        sys.stdout = console.real
        if rate:
            _command("tick rate 20", feedback)
    cls = classify(exc, ok)
    if cls not in UNCOUNTED:
        save_table(record(load_table(), name, code, ok, seconds, note, cls))
    if not ok:
        folder = _report(name, {"scenario": name, "code": code, "cls": cls, "note": note, "seconds": seconds,
                                "feedback": feedback, "trace": trace, "log": console.lines[-200:]})
        note = f"{note} [{cls}] → {folder}"
    return ok, seconds, note, cls, code

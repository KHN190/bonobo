"""The scenario sheets: one situation per entry, built with commands in the test world, run the way the agent
plays, and judged by the outcome. The primitives live in `bench.core`, the runner and the readiness table in
`bench.runner`, the fighting benches in `bench.fight`; this module is the sheet of everything else, and the name
the rest of the codebase still imports.
"""
import json
import math
import os
import re
import time

from .bench import core, runner
from .data import POD_BLOCKS
from .bench.core import *          # noqa: F403  (the bench's primitives are this module's own vocabulary)
from .bench.core import (BOX, FLAG, NOTES, ORIGIN, PKG, SCENARIOS, SetupInvalid, _achieve, _batch, _c, _chat,
                         _checked, _command, _count_blocks, _drain, _inv_has, _near, _platform, _sweep,
                         _sweep_check, _by, at, server_count, set_brain)
from .bench.runner import *        # noqa: F403
from .bench.runner import (LAST_FEEDBACK, LAST_LINES, _report, _setup, _trace, classify, code_for, dep_hash, feedback_errors, load_table,
                           module_deps, record, run, save_table, setup_mismatches, silent_failure, status)

# name → module (for the readiness hash), setup commands (relative to ORIGIN), expected signature blocks
# [(lo, hi, block or "*" for any non-air, min, max)], the skill call, the success check and a time budget (s).
SCENARIOS.update({
    "cast_portal": {
        "doc": "A 3×3 lava pool beside the body; water bucket, bucket, 16 cobblestone, flint and steel → "
               "a portal frame cast in place and lit (no obsidian carried, no diamond pickaxe).",
        "module": "building",
        "setup": [f"fill {_c(at(-8, -3, -8))} {_c(at(8, -1, 8))} stone",
                  f"fill {_c(at(2, -1, -1))} {_c(at(4, -1, 1))} lava",
                  f"tp @p {_c(at(0, 0, 0))}",
                  "clear @p", "give @p water_bucket", "give @p bucket", "give @p cobblestone 16",
                  "give @p flint_and_steel"],
        "expect": [(at(2, -1, -1), at(4, -1, 1), "lava", 9, 9),
                   (at(-8, 0, -8), at(8, 4, 8), "*", 0, 0)],
        "run": lambda ctx: __import__("bonobo.building", fromlist=["cast_portal"]).cast_portal(ctx),
        "check": lambda api, inv: _count_blocks(api, at(-8, -1, -8), at(8, 6, 8), "nether_portal") >= 1,
        "budget": 60,
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
        "doc": "Nether platform, 3 piglins, 2 gold ingots and a carried gold helmet → wear it, barter, collect trades.",
        "module": "nether",
        "dimension": "minecraft:the_nether",
        "combat": True,
        "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} netherrack",
                  f"fill {_c(at(-9, 0, -9))} {_c(at(9, 4, 9))} glass hollow",
                  f"fill {_c(at(-8, 0, -8))} {_c(at(8, 3, 8))} air",
                  # Gold boots on during setup: with a bare body the summoned piglins attacked before the skill
                  # started (hp 15, setup invalid). The helmet stays in the bag: wearing it is part of the skill.
                  f"tp @p {_c(at(0, 0, 0))}", "clear @p", "item replace entity @p armor.feet with golden_boots",
                  "give @p gold_ingot 2", "give @p golden_helmet", "give @p iron_sword",
                  f"summon piglin {_c(at(4, 0, 0))} {{PersistenceRequired:1b}}",
                  f"summon piglin {_c(at(-4, 0, 2))} {{PersistenceRequired:1b}}",
                  f"summon piglin {_c(at(2, 0, -4))} {{PersistenceRequired:1b}}"],
        "expect": [(at(-8, -1, -8), at(8, -1, 8), "netherrack", 289, 289)],
        "expect_entities": [("minecraft:piglin", 3)],
        "run": lambda ctx: __import__("bonobo.nether", fromlist=["barter_piglin"]).barter_piglin(ctx, 2),
        "check": lambda api, inv: inv.count("minecraft:gold_ingot") <= 1 and _trades(inv) >= 1,
        "detail": lambda inv: "got " + ", ".join(f"{s['id'].split(':')[1]}×{s['count']}" for s in inv.slots
                                                 if s["id"] not in ("minecraft:gold_ingot", "minecraft:golden_helmet",
                                                                    "minecraft:iron_sword")),
        "budget": 60,
    },
    "collect_blaze_rods": {
        "doc": "Nether platform, 3 blazes, sword + shield + iron armor → fight_loop fights them, the step picks up at least one rod.",
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
                  # Close and worn (10 hp): the fight and the pickup are judged, not a long approach.
                  f"summon blaze {_c(at(3, 1, 0))} {{PersistenceRequired:1b,Health:4f}}",
                  f"summon blaze {_c(at(-3, 1, 2))} {{PersistenceRequired:1b,Health:4f}}",
                  f"summon blaze {_c(at(0, 1, -3))} {{PersistenceRequired:1b,Health:4f}}"],
        "expect": [(at(-8, -1, -8), at(8, -1, 8), "nether_bricks", 289, 289)],
        "expect_entities": [("minecraft:blaze", 3)],
        "run": lambda ctx: __import__("bonobo.combat", fromlist=["collect_blaze_rods"]).collect_blaze_rods(ctx, 1),
        "check": lambda api, inv: inv.count("minecraft:blaze_rod") >= 1 and api.get("/state")["health"] > 0,
        "budget": 60,
    },
    "activate_end_portal": {
        # 9 frames already hold their eye (a room found part-filled, as real ones are): the three on our side are
        # the job, so the row fits 30 s and still ends in the portal opening.
        "doc": "A stronghold portal ring, 9 frames with eyes and the 3 nearest empty, 3 eyes and a block → a block over the "
               "middle, stood on, the 3 eyes from there: an open end portal ≤ 3 s.",
        "module": "end",
        "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone_bricks",
                  f"fill {_c(at(-1, 0, -2))} {_c(at(1, 0, -2))} end_portal_frame[facing=south]",
                  f"fill {_c(at(-1, 0, 2))} {_c(at(1, 0, 2))} end_portal_frame[facing=north,eye=true]",
                  f"fill {_c(at(-2, 0, -1))} {_c(at(-2, 0, 1))} end_portal_frame[facing=east,eye=true]",
                  f"fill {_c(at(2, 0, -1))} {_c(at(2, 0, 1))} end_portal_frame[facing=west,eye=true]",
                  f"fill {_c(at(-1, -1, -1))} {_c(at(1, -1, 1))} lava",
                  # beside the ring, a block to put over the middle's lava (end.eye_plan)
                  f"tp @p {_c(at(0, 0, -3))}", "clear @p", "give @p ender_eye 3", "give @p cobblestone 1"],
        "expect": [(at(-2, 0, -2), at(2, 0, 2), "end_portal_frame", 12, 12)],
        "run": lambda ctx: _drain(__import__("bonobo.end", fromlist=["activate_end_portal"]).activate_end_portal(ctx)),
        # Open: the 9 portal blocks, or already fallen through them (standing in the middle is the speedrun way).
        "check": lambda api, inv: api.get("/state")["dimension"] == "minecraft:the_end"
        or _count_blocks(api, at(-1, 0, -1), at(1, 0, 1), "end_portal") == 9,
        "budget": 30,
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
})


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
    "doc": "Stone room, 3 furnaces placed side by side, 3 raw iron + 3 coal carried → 3 iron ingots, one per furnace "
           "in parallel (load, start, wait, collect).",
    "module": "skills",
    # Core tests the smelt chain only: mining and crafting the furnace are other rows' job. The furnace clock is
    # game time — the wait is sprinted (/tick sprint) twice, early and late, so the row fits the core's 30 s.
    "setup": [f"fill {_c(at(-8, -4, -8))} {_c(at(8, -1, 8))} stone",
              f"fill {_c(at(2, 0, -1))} {_c(at(2, 0, 1))} furnace",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p raw_iron 3", "give @p coal 3"],
    "expect": [(at(2, 0, -1), at(2, 0, 1), "furnace", 3, 3)],
    "run": lambda ctx: _achieve(ctx, [("minecraft:iron_ingot", 3)], _inv_has("minecraft:iron_ingot", 3), rounds=10),
    "check": lambda api, inv: inv.count("minecraft:iron_ingot") >= 3 and inv.count("minecraft:raw_iron") == 0,
    "budget": 30,
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
LEG_START = (10400, 200, 10400)
SCENARIOS["locate_stronghold"] = {
    "doc": "A flat sky plane under the skill's 200-block sideways leg, speed, 12 eyes → two throws, triangulated "
           "estimate within 64 blocks of /locate.",
    "module": "nether",
    "raw": True,
    # Away from the bench's sky platform; /locate answers from where we stand, so the tp comes first.
    "setup": [f"tp @p {LEG_START[0]} {LEG_START[1] + 1} {LEG_START[2]}", "clear @p", "give @p ender_eye 12",
              "give @p cobblestone 64", "give @p stone_pickaxe", "give @p cooked_beef 16",
              "locate structure minecraft:stronghold"],
    "before": lambda ctx: _stronghold_leg(ctx),
    "run": lambda ctx: __import__("bonobo.nether", fromlist=["locate_stronghold"]).locate_stronghold(ctx),
    "check": lambda api, inv: _stronghold_error() <= 64,
    "budget": 30,
}
LEG = 200        # nether.locate_stronghold's sideways leg between the two throws
LEG_PAD = 12     # the eye's reading is a few degrees off /locate's: the plane is wider than the line


def _leg_box(start, stronghold):
    """Pure: (x0, z0, x1, z1) around the leg the skill walks — perpendicular to the line to the stronghold,
    (-dz, dx) as the skill turns it — padded on every side."""
    x, z = start
    d = math.dist(stronghold, start) or 1.0
    ex, ez = round(x - (stronghold[1] - z) / d * LEG), round(z + (stronghold[0] - x) / d * LEG)
    return (min(x, ex) - LEG_PAD, min(z, ez) - LEG_PAD, max(x, ex) + LEG_PAD, max(z, ez) + LEG_PAD)


def _stronghold_leg(ctx):
    """The walk between the throws on a flat stone plane at sky height, with speed: 200 blocks of real hills were
    most of a 60 s row. The throws, the eye's flight and the triangulation are the skill's own."""
    real = locate_reply(LAST_FEEDBACK)
    if not real:
        raise SetupInvalid("no /locate answer for the stronghold")
    x, y, z = LEG_START
    box = _leg_box((x, z), real)
    _load_area(*box)
    _overworld(_flat(*box, y, "stone") + [f"tp @p {x} {y + 1} {z}", "effect give @p speed 60 3 true"])
    time.sleep(1)
def _load_area(x0, z0, x1, z1):
    """Force-load a footprint and wait until its corners answer ("That position is not loaded" otherwise)."""
    _command(f"execute in minecraft:overworld run forceload add {x0} {z0} {x1} {z1}", [])
    probes = [(px, pz) for px in (x0, x1) for pz in (z0, z1)]
    for _ in range(60):
        if not any("not loaded" in l for px, pz in probes for l in
                   _command(f"execute in minecraft:overworld run fill {px} 300 {pz} {px} 300 {pz} air", [])):
            return
        time.sleep(0.5)
    raise SetupInvalid(f"area {x0},{z0}..{x1},{z1} never loaded")


def _overworld(cmds):
    """Commands run in the Overworld from a hook; a refused one is a setup that did not happen."""
    for cmd in cmds:
        lines = _command(f"execute in minecraft:overworld run {cmd}", [])
        if any("not loaded" in l or "Unknown" in l or "Too many" in l for l in lines):
            raise SetupInvalid(f"{cmd}: {lines[:1]}")


def _flat(x0, z0, x1, z1, y, block):
    """Pure: fills covering a flat rectangle, each under the game's 32768-block limit."""
    step = max(1, 32768 // (z1 - z0 + 1))
    return [f"fill {a} {y} {z0} {min(a + step - 1, x1)} {y} {z1} {block}" for a in range(x0, x1 + 1, step)]


STRONGHOLD_AT = (20000, 150, 20000)     # a built stronghold piece, in a sealed stone block in the sky
ROOM_OFF = 64          # the ring's centre along +x: past the skill's 48-block scan, so the bricks are followed first


def _stronghold_piece(x, y, z):
    """Commands for a corridor of stone bricks (3×3 inside) running 58 blocks east from the start, ending in a
    portal room with a ring of 12 empty frames, all sealed in stone: the search, the brick-following and the walk,
    without a /place structure and a 40-block dig down (most of the old 60 s)."""
    f = lambda a, b, block: f"fill {a[0]} {a[1]} {a[2]} {b[0]} {b[1]} {b[2]} {block}"   # noqa: E731
    cx = x + ROOM_OFF
    return [f((x - 3, y - 2, z - 6), (cx + 6, y + 5, z + 6), "stone"),
            f((x - 1, y - 1, z - 2), (x + 58, y + 3, z + 2), "stone_bricks"),
            f((x, y, z - 1), (x + 58, y + 2, z + 1), "air"),
            f((cx - 5, y - 1, z - 5), (cx + 5, y + 4, z + 5), "stone_bricks"),
            f((cx - 4, y, z - 4), (cx + 4, y + 3, z + 4), "air"),
            f((x + 58, y, z - 1), (cx - 4, y + 2, z + 1), "air"),
            f((cx - 1, y, z - 2), (cx + 1, y, z - 2), "end_portal_frame[facing=south]"),
            f((cx - 1, y, z + 2), (cx + 1, y, z + 2), "end_portal_frame[facing=north]"),
            f((cx - 2, y, z - 1), (cx - 2, y, z + 1), "end_portal_frame[facing=east]"),
            f((cx + 2, y, z - 1), (cx + 2, y, z + 1), "end_portal_frame[facing=west]"),
            f"tp @p {x + 1} {y} {z}"]


def _built_stronghold(ctx):
    """The piece built fresh every run (a run digs it up), the estimate at the corridor's start where we stand."""
    x, y, z = STRONGHOLD_AT
    _load_area(x - 8, z - 8, x + ROOM_OFF + 8, z + 8)
    _overworld(_stronghold_piece(x, y, z))
    ctx.mem.add_site("stronghold", (x + 1, y, z), "minecraft:overworld", name="stronghold")
    time.sleep(1)


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
    "doc": "A stronghold piece built every run (a brick corridor, the room 64 blocks on, past the scan), standing on "
           "the estimate → bricks followed, portal frame found and reached.",
    "module": "end", "raw": True, "release": True,
    "setup": ["clear @p", "give @p diamond_pickaxe", "give @p cobblestone 64", "give @p cooked_beef 16",
              "give @p torch 32", "give @p water_bucket"],
    "before": _built_stronghold,
    # The skill has to succeed AND the frames have to be right here: a run that raised "finished without reaching its
    # goal" counted as PASS because any frame within 48 blocks satisfied the old check.
    "run": _portal_room_run,
    "check": lambda api, inv: _portal_room_found(),
    "budget": 30,
}
# find_portal_room (the real stronghold, dug up by every run) was replaced by find_portal_room_fresh.
# A dragon already worn down, its crystals gone: the fight's last phase (the approach, the perch, the finishing
# blows) is what a row can judge inside 60 s; the full fight from 200 hp is the acceptance run's.
WORN_DRAGON = ["kill @e[type=end_crystal]", "data merge entity @e[type=ender_dragon,limit=1] {Health:8f}"]


def _wear_dragon(ctx):
    for cmd in WORN_DRAGON:
        _chat(f"execute in minecraft:the_end run {cmd}")
    time.sleep(0.5)


SCENARIOS["fight_dragon"] = {
    "doc": "The End's main island with the dragon, diamond sword, shield, iron armor, food, blocks → dragon dead.",
    "module": "end",
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
    "before": _wear_dragon,
    "run": lambda ctx: __import__("bonobo.end", fromlist=["slay_dragon"]).slay_dragon(ctx),
    "check": lambda api, inv: not any(e["type"] == "minecraft:ender_dragon"
                                      for e in __import__("bonobo.world", fromlist=["entities"]).entities(200)),
    "budget": 60,
}

def _worn_head():
    from .world import Inventory
    return ((Inventory().equipment.get("head") or {}).get("id") or "")


def _wait_landed(ctx, seconds=10):
    from . import api
    from .skillcore import settle
    s = settle(lambda: api.get("/state"), lambda st: st.get("onGround") or st.get("inWater") or st.get("dead"),
               timeout=seconds, stable_s=1.0, soft=True)
    return bool(s.get("onGround") or s.get("inWater") or s.get("dead"))


def _snap_survival(ctx, seconds=20):
    """Brain rounds until the body is back in the Overworld: leaving the Nether is an upkeep row now (low food,
    health or room → `nether.use_portal`), not an L0 rescue."""
    from . import api
    t0 = time.time()
    while time.time() - t0 < seconds and api.get("/state")["dimension"] != "minecraft:overworld":
        core.BRAIN.round()
    return api.get("/state")["dimension"] == "minecraft:overworld"


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
# The bucket sits in the main bag, not the hotbar (nine stacks of dirt fill the hotbar first): the clutch must
# select it. After landing the water is scooped back up.
_FALL_FLOOR = [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone", f"tp @p {_c(at(0, 0, 0))}", "clear @p",
               "give @p dirt 576"]
SCENARIOS["water_clutch"] = {
    "doc": "Dropped 30 blocks above stone, a water bucket in the main bag (not the hotbar) → water poured in time "
           "(health ≥ 16) and scooped back (the bucket full again)",
    "module": "perception", "stochastic": False,
    "setup": _FALL_FLOOR + ["give @p water_bucket"],
    "expect": [(at(-6, -1, -6), at(6, -1, 6), "stone", 169, 169)],
    # The fall starts after setup (perception is paused during setup).
    "before": lambda ctx: _chat(f"tp @p {_c(at(0.5, 30, 0.5))}"),
    "run": _wait_landed,
    "check": lambda api, inv: api.get("/state")["health"] >= 16 and not api.get("/state")["dead"]
    and inv.count("minecraft:water_bucket") >= 1,
    "budget": 15,
}
SCENARIOS["fall_without_bucket"] = {
    "doc": "The same fall with no bucket (control): the fall hurts — dead, or health ≤ 10 — so water_clutch's pass "
           "is the bucket's doing",
    "module": "perception", "stochastic": False,
    "setup": list(_FALL_FLOOR),
    "expect": [(at(-6, -1, -6), at(6, -1, 6), "stone", 169, 169)],
    "before": lambda ctx: _chat(f"tp @p {_c(at(0.5, 30, 0.5))}"),
    "run": _wait_landed,
    "check": lambda api, inv: api.get("/state")["dead"] or api.get("/state")["health"] <= 10,
    "budget": 15,
}
SCENARIOS["recover_items"] = {
    "doc": "Died 10 blocks away a minute ago, 3 diamonds lie there → walk back and pick them up.",
    "module": "reflexes",
    "setup": [f"fill {_c(at(-10, -2, -6))} {_c(at(12, -1, 6))} stone",
              f"tp @p {_c(at(-6, 0, 0))}", "clear @p"],
    "expect": [(at(-10, -1, -6), at(12, -1, 6), "stone", 299, 299)],
    "before": lambda ctx: (ctx.mem.log_death(at(6, 0, 0), "minecraft:overworld"),
                           _chat(f'summon item {_c(at(6, 0, 0))} {{Item:{{id:"minecraft:diamond",count:3}},Age:-32768}}')),
    "run": lambda ctx: __import__("bonobo.reflexes", fromlist=["recover_items"]).recover_items(ctx),
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
    "run": lambda ctx: core.BRAIN.invariants(),
    "check": lambda api, inv: _worn_head() == "minecraft:golden_helmet" and inv.count("minecraft:iron_helmet") >= 1,
    "budget": 10,
}
SCENARIOS["retreat_from_nether"] = {
    "doc": "Nether, hurt to 6 hp with one food, the arrival portal 8 blocks away → upkeep retreats through it.",
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
            core.BRAIN.invariants()
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
    """Stand ~20 blocks from the real fortress, below the roof (spreadplayers 'under 90' picks a floor there)."""
    real = locate_reply(LAST_FEEDBACK)
    if not real:
        raise SetupInvalid("no /locate answer for the fortress")
    _chat(f"execute in minecraft:the_nether run spreadplayers {real[0] + 20} {real[1]} 0 6 under 90 false @p")
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
    "doc": "The real Nether: ~20 blocks from a fortress (lava sea likely between), blocks + food → fortress found.",
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
    "budget": 60,
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
        stop = api.get("/state")
        TREK["end"] = (stop["x"], stop["y"], stop["z"])
        TREK["seconds"] = time.time() - TREK["t0"]
        if not ok and math.hypot(stop["x"] - target[0], stop["z"] - target[2]) > 14:
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



SCENARIOS["trek_overworld_30"] = {
    "doc": "Real Overworld terrain (hills, forest, water), 30 blocks east (18 walked: arrival is 12 off), basic kit → arrive; seconds per 100 blocks.",
    "module": "nav", "raw": True,
    "setup": ["spreadplayers 10600 10600 0 4 false @p", "clear @p", "give @p stone_pickaxe", "give @p stone_axe",
              "give @p cobblestone 64", "give @p cooked_beef 16", "give @p oak_boat"],
    "run": _trek(30, 0), "check": lambda api, inv: _trek_check(api), "detail": _trek_detail, "budget": 30,
}
SCENARIOS["trek_nether_25"] = {
    "doc": "Real Nether terrain below the roof, 25 blocks (13 walked), kit with gold helmet → arrive; seconds per 100 blocks.",
    "module": "nav", "raw": True, "combat": True, "dimension": "minecraft:the_nether",
    "setup": ["spreadplayers 300 300 0 8 under 90 false @p", "clear @p", "give @p diamond_pickaxe",
              "give @p cobblestone 128", "give @p cooked_beef 16",
              "item replace entity @p armor.head with golden_helmet"],
    "run": _trek(25, 0, "minecraft:the_nether"), "check": lambda api, inv: _trek_check(api),
    "detail": _trek_detail, "budget": 30,
}
SCENARIOS["cave_escape"] = {
    "doc": "Sealed in a dark 1×2 pocket 8 blocks under the platform, pickaxe + blocks → back on the surface platform.",
    "module": "nav",
    "setup": [f"fill {_c(at(-6, -12, -6))} {_c(at(6, -1, 6))} stone",
              f"fill {_c(at(0, -9, 0))} {_c(at(0, -8, 0))} air",
              f"tp @p {_c(at(0.5, -9, 0.5))}", "clear @p", "give @p stone_pickaxe", "give @p cobblestone 32"],
    "expect": [(at(-6, -1, -6), at(6, -1, 6), "stone", 169, 169)],
    # The standing cell on the platform; range 0.6: `nav.there` measures the feet's block, so a step below is 1 off.
    "run": lambda ctx: __import__("bonobo.nav", fromlist=["go_to"]).go_to(at(3, 0, 3), ctx.policy, range_=0.6),
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
for _name in ("fight_dragon", "find_fortress_far", "locate_stronghold", "trek_overworld_30",
              "trek_nether_25"):
    SCENARIOS[_name]["release"] = True       # minutes each: run by name before a live run, not in every round
def _road_reuse(ctx):
    """There, back, and there again over the same 150 blocks: the third trip must follow the remembered legs
    (roads.py) and take no longer than the first. Times are kept for the check."""
    from . import api, nav
    s = api.get("/state")
    a = (s["blockX"], s["blockY"], s["blockZ"])
    b = (a[0] + ROAD_LEG, a[1], a[2])
    times = []
    for target in (b, a, b):
        t0 = time.time()
        if not nav.go_to(target, ctx.policy, range_=12, attempts=1):
            raise api.NavFailed(f"road trip to {target} stopped short")
        times.append(time.time() - t0)
    ROAD_TIMES[:] = times
    return True


ROAD_TIMES = []
ROAD_LEG = 10      # three legs of 10 blocks: the reuse is what is judged, not the distance
SCENARIOS["road_reuse"] = {
    "doc": "Overworld, 10 blocks there, back, there again → the third trip reuses the road and is no slower.",
    "module": "nav", "raw": True, "release": True,
    "setup": ["spreadplayers 11200 11200 0 4 false @p", "clear @p", "give @p stone_pickaxe", "give @p cobblestone 64",
              "give @p cooked_beef 16"],
    "run": _road_reuse,
    "check": lambda api, inv: len(ROAD_TIMES) == 3 and ROAD_TIMES[2] <= ROAD_TIMES[0] * 1.05,
    "detail": lambda inv: "trips " + ", ".join(f"{t:.0f}s" for t in ROAD_TIMES),
    "budget": 60,
}

# -- slices: the cerebellum itself (brain.round) over a private task queue, not a single skill. Most live problems
# were scheduling and chaining (loops, idle holds, wrong-way unstucks, repeated exits), so each slice measures them.
SLICE = {}


# Rounds the arbiter gave to a waiting kind (brain.picks, arbiter.waits). With work queued, any is a waste
# (arbiter.gate should have offered the work).
MAX_WAITS_WITH_QUEUE = 0


def slice_report(lines, positions, target, idle_s, picks=None):
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
    from .arbiter import waits
    return {"loops": loops, "idle_s": round(idle_s), "away_m": round(away), "waits": waits(picks or {})}


def queue_finished(items):
    """Pure: every task of a slice's queue has left the live states (done, failed or cancelled) — the slice's own
    work is over, whatever the brain would stock up on next. An empty queue is never finished (nothing was asked)."""
    from .tasks import LIVE
    return bool(items) and all(t["state"] not in LIVE for t in items)


def tier_rows(rows, tier, named):
    """Pure: the rows a tier selects for `--failed` / `--pending` — never acceptance (its own run); only `tier`'s
    rows when a tier was named on the command line (`named`) and it is not "all"."""
    return [n for n, r in rows.items() if r["tier"] != "acceptance"
            and (not named or tier == "all" or r["tier"] == tier)]


def _slice(done, minutes, target=None, queue=(), max_idle=15):
    """Run the whole cerebellum (brain.round) until done() or `minutes`, on a private task queue holding `queue`
    (goals, in order; empty = the brain prepares on its own). Stops at once on a
    loop (the same decision line 4×) or an idle hold longer than `max_idle` — a report, not a timeout.

    `done=None` means the window itself is the test: run the full `minutes` and let the scenario's own check say
    whether it went well. That is what the e2e markers need — surviving a night has no completion, only an end.
    """
    def run(ctx):
        from . import api, tasks
        from .world import Snapshot
        saved = tasks.FILE
        tasks.FILE = os.path.join(os.path.dirname(NOTES), "slice-tasks.json")
        tasks.save([])
        for goal in queue:
            tasks.add(goal, source="bench")
        core.BRAIN.idle_since, core.BRAIN.committed = None, None
        core.BRAIN.picks.clear()              # this slice's rounds only
        t0, positions, idle = time.time(), [], 0.0
        start_line = len(sys.stdout.lines) if hasattr(sys.stdout, "lines") else 0
        stopped = None
        try:
            while time.time() - t0 < minutes * 60:
                # The queue's goals all finished is the end of the slice: idle stocking after it is not the row's work.
                if (done is not None and done()) or (queue and queue_finished(tasks.load())):
                    break
                try:
                    core.BRAIN.round()
                except api.McError as e:
                    api.log(f"!! round: {e}")
                s = Snapshot()
                positions.append((time.time(), s.feet))
                if core.BRAIN.idle_since:
                    idle = max(idle, time.time() - core.BRAIN.idle_since)
                lines = sys.stdout.lines[start_line:] if hasattr(sys.stdout, "lines") else []
                rep = slice_report(lines, positions, target, idle, core.BRAIN.picks)
                if rep["loops"] or rep["idle_s"] > max_idle:
                    stopped = f"loop: {rep['loops'][0]}" if rep["loops"] else f"idle {rep['idle_s']}s"
                    break
        finally:
            tasks.FILE = saved             # the slice's private queue must not leak into the next scenario
            SLICE.update(seconds=time.time() - t0, positions=positions, idle=idle, target=target, queued=bool(queue),
                         picks=dict(core.BRAIN.picks))
        if stopped:
            raise api.McError(f"slice stopped early — {stopped}")
        SLICE.update(seconds=time.time() - t0, positions=positions, idle=idle, target=target)
        if done is not None and not done():
            last = [l.strip() for l in (sys.stdout.lines[start_line:] if hasattr(sys.stdout, "lines") else [])
                    if "→" in l or "!!" in l or "task" in l or "upkeep" in l]
            raise api.McError(f"slice not done after {minutes} min, last decision: {last[-1] if last else 'no decision logged'}")
        return True
    return run


def _slice_detail(inv):
    if not SLICE:
        return ""
    rep = slice_report(LAST_LINES, SLICE["positions"], SLICE["target"], SLICE["idle"], SLICE.get("picks"))
    return (f"loops: {'; '.join(rep['loops'][:3]) or '0'}, {SLICE['seconds']:.0f}s, longest idle {rep['idle_s']}s, "
            f"walked away {rep['away_m']} m, waits {rep['waits']}")




def slice_verdict(finished, rep, queued, max_idle, max_loops, picks=None):
    """Pure: (passed, the one line that says why) — every part named, so a failed slice carries its reason."""
    from . import arbiter
    waited = {k: n for k, n in (picks or {}).items() if k in arbiter.WAIT_KINDS}
    ok = finished and rep["idle_s"] <= max_idle and len(rep["loops"]) <= max_loops \
        and not (queued and rep["waits"] > MAX_WAITS_WITH_QUEUE)
    return ok, (f"slice check: done={finished} idle_s={rep['idle_s']}/{max_idle} loops={len(rep['loops'])}/{max_loops}"
                f" waits={rep['waits']}/{MAX_WAITS_WITH_QUEUE if queued else '-'} {waited}")


def _slice_check(done, max_idle=15, max_loops=0):
    said = []

    def check(api, inv):
        if not SLICE:
            return False
        finished = done is None or bool(done())
        rep = slice_report(LAST_LINES, SLICE["positions"], SLICE["target"], SLICE["idle"], SLICE.get("picks"))
        ok, why = slice_verdict(finished, rep, SLICE.get("queued"), max_idle, max_loops, SLICE.get("picks"))
        if not ok and why not in said:          # polled: the same reason once
            said.append(why)
            api_mod = __import__("bonobo.api", fromlist=["log"])
            api_mod.log(why)
        return ok
    return check


def _has_stone_pickaxe():
    from .world import Inventory
    return Inventory().count("minecraft:stone_pickaxe") >= 1


def _has_tools_and_furnace():
    from .world import Inventory
    inv = Inventory()
    return inv.count("minecraft:stone_pickaxe") >= 1 and (inv.count("minecraft:furnace") >= 1
                                                          or bool(__import__("bonobo.world", fromlist=["find"])
                                                                  .find(["furnace"], 8, 1)))


def _nether_kit_ready():
    from .knowledge import nether_kit_missing
    from .world import Inventory
    return not nether_kit_missing(Inventory())


def _in_overworld():
    from . import api
    return api.get("/state")["dimension"] == "minecraft:overworld"


SCENARIOS["slice_start_tools"] = {
    "doc": "Slice: the whole cerebellum, a crafting table beside it, planks, sticks and 3 cobblestone carried, a "
           "pickaxe asked → the wooden-then-stone chain by crafting alone, a stone pickaxe in the bag, no loops "
           "(core: what gathering costs is the other rows' job).",
    "module": "brain",
    "setup": [f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone", f"setblock {_c(at(1, 0, 1))} crafting_table",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "time set day", "give @p oak_planks 6", "give @p stick 4",
              "give @p cobblestone 3"],
    "expect": [(at(1, 0, 1), at(1, 0, 1), "crafting_table", 1, 1)],
    "run": _slice(_has_stone_pickaxe, 0.5, queue=[__import__("bonobo.goals", fromlist=["goals"]).have(
        ("tool", "pickaxe", 1))]),
    "check": _slice_check(_has_stone_pickaxe),
    "detail": _slice_detail,
    "budget": 30,
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
    'nether kit' milestone. Without it the slice hunted sheep for a bed for 5 minutes before the kit."""
    from . import api
    s = api.get("/state")
    x, y, z = s["blockX"] + 3, s["blockY"], s["blockZ"]
    for cmd in (f"fill {x} {y - 1} {z - 1} {x} {y + 3} {z + 2} obsidian",
                f"fill {x} {y} {z} {x} {y + 2} {z + 1} nether_portal[axis=z]"):
        _chat(f"execute in minecraft:overworld run {cmd}")
    ctx.mem.add_machine("nether_portal", (x, y - 1, z - 1), 1, "minecraft:overworld", ["portal"])
    ctx.mem.add_site("portal", (x, y, z), "minecraft:overworld", name="portal-overworld")


SCENARIOS["slice_nether_kit"] = {
    "doc": "Slice: at a lit portal, the kit two steps short (one block, the gold helmet) → kit complete (food, blocks, gold helmet) without "
           "stepping into the Nether early, no loops, idle ≤ 15 s.",
    "module": "brain",
    # Two steps left: one block to mine (stone at arm's length) and the helmet to craft (gold + table carried).
    # Food is over the kit's 6 with a margin (upkeep may eat one before the run starts); the cow stays 2 blocks off
    # in case it does not.
    "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} grass_block", f"fill {_c(at(1, 0, -1))} {_c(at(1, 1, 1))} stone",
              f"tp @p {_c(at(0, 0, 0))}", "clear @p", "time set day", "give @p iron_pickaxe",
              "give @p iron_sword", "give @p bucket", "give @p flint_and_steel", "give @p gold_ingot 5",
              "give @p crafting_table", "give @p cooked_beef 8", "give @p cobblestone 31",
              f"summon cow {_c(at(-2, 0, 1))}"],
    "expect": [(at(1, 0, -1), at(1, 1, 1), "stone", 6, 6)],
    "expect_entities": [("minecraft:cow", 1)],
    "before": lambda ctx: _portal_beside_player(ctx),
    "run": _slice(lambda: _nether_kit_ready() or not _in_overworld(), 0.4,
                  queue=[__import__("bonobo.goals", fromlist=["goals"]).make("milestone", name="nether kit")]),
    "check": _slice_check(lambda: _nether_kit_ready() and _in_overworld()),
    "detail": _slice_detail,
    "budget": 35,
}
SCENARIOS["slice_retreat"] = {
    "doc": "Slice: in the Nether at 6 hp with one food, the arrival portal remembered 4 blocks away → back in "
           "the Overworld by the upkeep table, no loops.",
    "module": "brain", "dimension": "minecraft:the_nether", "release": True,
    "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(12, -1, 8))} netherrack",
              f"fill {_c(at(2, 0, -1))} {_c(at(2, 4, 2))} obsidian",
              f"fill {_c(at(2, 1, 0))} {_c(at(2, 3, 1))} nether_portal[axis=z]",
              f"tp @p {_c(at(-2, 0, 0))}", "clear @p", "give @p cooked_beef 1", "give @p cobblestone 16",
              "give @p iron_pickaxe"],
    "expect": [(at(2, 1, 0), at(2, 3, 1), "nether_portal", 6, 6)],
    "before": lambda ctx: (ctx.mem.add_site("portal", at(2, 1, 0), "minecraft:the_nether", name="portal-nether"),
                           _chat("damage @p 14 minecraft:generic")),
    "run": _slice(_in_overworld, 0.4, target=at(2, 1, 0)),
    "check": _slice_check(_in_overworld),
    "detail": _slice_detail,
    "budget": 60,
}

for _name in ("trek_overworld_30", "trek_nether_25", "cave_escape", "return_to_portal"):
    SCENARIOS[_name]["mod"] = ["travel"] + (["use"] if _name == "return_to_portal" else [])
# Portal trips read /state's inPortal (mod ≥0.1.28): they depend on WorldInfo too.
for _name in ("enter_nether", "return_from_nether", "relight_portal", "return_to_portal", "retreat_from_nether"):
    SCENARIOS[_name]["mod_extra"] = ["state"]


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


def _worn_perched_dragon(ctx, _perch=_summon_perched_dragon(6)):
    """The fight's last phase, built: the dragon on the pillar, crystals gone, 8 hp (WORN_DRAGON). Waiting for a
    free-flying dragon to come down was most of a 60 s row; the crystals have their own row (break_caged_crystal).
    Plain chaining: `_hooks` is defined further down."""
    _perch(ctx)
    _wear_dragon(ctx)


SCENARIOS["fight_dragon"].update(
    doc="The End's main island, the dragon perched and worn (crystals gone, 8 hp), diamond sword, shield, iron "
        "armour, food, blocks → dragon dead.",
    setup=[c for c in SCENARIOS["fight_dragon"]["setup"] if not c.startswith(("kill ", "summon ", "spreadplayers "))],
    before=_worn_perched_dragon, budget=30)

SCENARIOS["bed_bomb_kill"] = {
    "doc": "Speedrun End kit, the dragon perched and worn (crystals gone, 8 hp) → dead by a bed bomb.",
    "module": "end", "raw": True, "combat": True, "dimension": "minecraft:the_end", "release": True,
    "setup": list(SPEEDRUN_END_KIT),
    "before": _worn_perched_dragon,
    # The driver, not the old single skill: crystals → pit → perch → one bomb → pit.
    "run": lambda ctx: __import__("bonobo.end", fromlist=["slay_dragon"]).slay_dragon(ctx),
    "check": lambda api, inv: _dragon_health() is None and not api.get("/state")["dead"],
    "budget": 30,
}

for _name, _tags in {
    "craft_eyes": ["craft", "use", "travel"], "gold_helmet_swap": [], "loot_chest": ["travel", "use"],
    "water_clutch": ["nets", "use"], "enter_end": ["travel"], "activate_end_portal": ["travel", "use"],
}.items():
    SCENARIOS[_name]["mod"] = _tags
# Faster game ticks only help server-side waiting (furnaces, piglin inspection): the player's own actions run on
# client ticks (3 logs still took 10.6 s at rate 60), so only those scenarios speed up and times stay wall seconds.
for _name in ("iron_ingots", "barter_piglin"):
    SCENARIOS[_name]["tick_rate"] = 60



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


# Milestones (goals.MILESTONES) → the scenarios that prove the skills they need. The review lists the milestones
# whose scenarios are not ready, so a live-run failure there is expected rather than a surprise.
MILESTONE_SCENARIOS = {
    "stone tools": ["craft_stone_tools", "slice_start_tools"], "station kit": ["slice_start_tools"],
    "food": ["hunt_food"], "iron pickaxe": ["iron_ingots"], "water bucket": ["fill_water_bucket"],
    "nether kit": ["slice_nether_kit"], "blaze rods": ["collect_blaze_rods"], "eyes of ender": ["craft_eyes"],
}


def readiness_lines(table=None):
    """Lines for the review: every scenario's verdict for the current code, then milestones not yet proven."""
    table = load_table() if table is None else table
    out, verdict = [], {}
    for name in SCENARIOS:
        st, med = status(table, name, code_for(name))
        verdict[name] = st
        budget = SCENARIOS[name]["budget"]
        out.append(f"  {name:22} {st:9}" + ("" if med is None else f" median {med}s (budget {budget}s)"))
    unproven = [g for g, names in MILESTONE_SCENARIOS.items() if any(verdict.get(n) != "scenario" for n in names)]
    if unproven:
        out.append(f"  milestones not proven on the bench: {', '.join(unproven)}")
    return out


def _trades(inv):
    """Items a piglin trade can give (anything but what the scenario handed out)."""
    given = ("minecraft:gold_ingot", "minecraft:golden_helmet", "minecraft:iron_sword", "minecraft:iron_helmet")
    return sum(s["count"] for s in inv.slots if s["id"] not in given)



from .bench import fight           # noqa: E402,F401  (the fight sheet registers itself)
from .bench.fight import *         # noqa: E402,F403
from .bench.fight import (_build, _cells, _combat_execute, _combat_intent, _fought, _hostiles,
                          _siege_cells, _summon)        # noqa: E402


# ================================================================================================================
# The generated sheet (test points A–D). Basics × the conditions that break them, as a cross-product: a BASE is one
# skill doing one job in a small arena, a CONDITION changes the arena, the clock, the bag or the world around the
# job, and says what must happen then — the same effect, or a failure with a specific reason. Every row is judged by
# the WORLD (a bag delta against the bag at the start, a block count, where the body stands), never by what the
# skill returned. Rows carry `skills` (what they prove), `point` (A–D) and `tags`; `tests/test_scenario_sheet.py`
# checks the sheet's shape and that every registered skill is proven somewhere. Nothing here runs until
# `mc.py scenario <name>` in a test world.
# ================================================================================================================
import itertools as _it
import threading as _threading

BASE = {}                 # the bag, the /state and the time at the start of the run (`_start`)
FAILED_AS_EXPECTED = {}   # scenario → the failure message that matched its `fails` pattern
INTERRUPTS = {}           # scenario → interruptions the run absorbed (injected or not)
SHEET = {}                # the generated rows, by name (also in SCENARIOS)
CHAIN_C = ("slice_start_tools", "iron_ingots", "slice_nether_kit")    # test point C, in this order
ACCEPTANCE_D = "accept_fresh_iron_pickaxe"


def _skill(name):
    """The registered runner of a skill (every skill module is imported by the brain)."""
    from . import brain  # noqa: F401
    from .skill import REGISTRY
    return REGISTRY[name].runner


def _inv_now():
    from .world import Inventory
    return Inventory()


def _start(name):
    """`before` hook head: forget the last run's verdicts and remember the bag and body this run starts from."""
    def hook(ctx):
        from . import api
        FAILED_AS_EXPECTED.pop(name, None)
        INTERRUPTS[name] = 0
        BASE.clear()
        BASE.update(name=name, inv=api.get("/inventory"), state=api.get("/state"), t=time.time())
    return hook


def _base_count(token):
    from .world import Inventory
    return Inventory(BASE["inv"]).count(token) if BASE.get("inv") else 0


# -- checks: what the world must show afterwards ----------------------------------------------------------------
def _gain(token, n, at_most=None):
    """The bag holds at least `n` more `token` than at the start (and, with `at_most`, not more than that)."""
    def check(api, inv):
        got = inv.count(token) - _base_count(token)
        return got >= n and (at_most is None or got <= at_most)
    return check


def _same_bag_and_place(r=1.5):
    """Goal already met: nothing taken, nothing spent, the body did not wander off."""
    def check(api, inv):
        from .world import Inventory
        before = sorted((s["id"], s["count"]) for s in Inventory(BASE["inv"]).slots)
        after = sorted((s["id"], s["count"]) for s in inv.slots)
        s0, s1 = BASE["state"], api.get("/state")
        return before == after and math.dist((s0["x"], s0["y"], s0["z"]), (s1["x"], s1["y"], s1["z"])) <= r
    return check


def _alive(min_hp=1.0):
    return lambda api, inv: not api.get("/state")["dead"] and api.get("/state")["health"] >= min_hp


def _at(pos, r):
    return lambda api, inv: _near(api, pos, r)


TORCHES = ("torch", "wall_torch")          # a torch on a wall is the same light, and reads as another block


def _blocks(lo, hi, name, least, most=None):
    """`name` blocks (or any of a tuple of names) in the box: at least `least`, at most `most`."""
    names = (name,) if isinstance(name, str) else tuple(name)

    def check(api, inv):
        n = sum(_count_blocks(api, lo, hi, nm) for nm in names)
        return n >= least and (most is None or n <= most)
    return check


def _same_bag():
    """Nothing taken, nothing spent: the bag reads as it did at the start."""
    def check(api, inv):
        from .world import Inventory
        before = sorted((s_["id"], s_["count"]) for s_ in Inventory(BASE["inv"]).slots)
        return before == sorted((s_["id"], s_["count"]) for s_ in inv.slots)
    return check


def _not(check):
    return lambda api, inv: not check(api, inv)


def _slot_has(item, *words):
    """A carried `item` whose slot mentions all of `words` (enchantments, potion contents), as the mod reports it."""
    def check(api, inv):
        return any(s_["id"] == item and all(w in json.dumps(s_) for w in words) for s_ in inv.slots)
    return check


def _mobs_near(kind, least, r=16):
    def check(api, inv):
        from .world import entities
        return len(entities(r, [kind])) >= least
    return check


def _under_feet(*names):
    def check(api, inv):
        from .world import Region
        s_ = api.get("/state")
        p = (s_["blockX"], s_["blockY"] - 1, s_["blockZ"])
        return Region(p, p).name(p) in names
    return check


def _room_to_work():
    def check(api, inv):
        from .skillcore import free_spots_here
        return bool(free_spots_here())
    return check


def _dropped_nothing():
    def check(api, inv):
        from .world import entities
        return not entities(8, ["minecraft:item"])
    return check


def _no_block_suffix(lo, hi, suffix):
    def check(api, inv):
        from .world import Region
        return not any(n.endswith(suffix) for n in Region(lo, hi).blocks.values())
    return check


def _food_up():
    """The food bar above where it stood when the eating began (after the row made the body hungry)."""
    return lambda api, inv: api.get("/state")["food"] > BASE.get("food_before", BASE["state"]["food"])


def _is_day():
    return lambda api, inv: int(api.get("/state")["timeOfDay"]) % 24000 < 12500


def _dimension(dim):
    return lambda api, inv: api.get("/state")["dimension"] == dim


def _free_slots(n):
    return lambda api, inv: inv.free_slots() >= n


def _interrupted(least=1):
    return lambda api, inv: INTERRUPTS.get(BASE.get("name"), 0) >= least


def _failed_as_expected():
    return lambda api, inv: BASE.get("name") in FAILED_AS_EXPECTED


def _all(*checks):
    return lambda api, inv: all(c(api, inv) for c in checks)


# -- run wrappers: timing and expected failures -----------------------------------------------------------------
def _expect_failure(name, run, pattern):
    """An expected-failure row: the run must END (the budget still applies) with a failure whose message names the
    reason — not succeed, not fail for some other reason."""
    def go(ctx):
        from . import api
        try:
            out = run(ctx)
        except api.McError as e:
            if re.search(pattern, str(e), re.I):
                FAILED_AS_EXPECTED[name] = str(e)
                return True
            raise
        raise api.McError(f"expected to fail ({pattern}), reported success instead: {out!r}")
    return go


def _resume(name, run, resume, tries=4):
    """Run; when an interruption stops it, count it and resume by what is still missing (`resume`, plan-driven)."""
    def go(ctx):
        from . import api
        fn = run
        try:
            for _ in range(tries):
                try:
                    return fn(ctx)
                except api.INTERRUPTIONS:
                    INTERRUPTS[name] = INTERRUPTS.get(name, 0) + 1
                    fn = resume
            raise api.McError(f"still interrupted after {tries} tries")
        finally:
            if api.INTERRUPT and str(api.INTERRUPT).startswith("bench:"):
                api.INTERRUPT = None       # an injected interrupt that landed after the end must not stop the next row
    return go


def _progress_of(base):
    """Pure: how this base's progress is counted — ("bag", token) by its products (`progress`, else `effect`), or
    ("walk", target) by the distance walked toward its target; None when neither is known."""
    token = (base.get("progress") or base.get("effect") or (None,))[0]
    if token:
        return "bag", token
    if base.get("target"):
        return "walk", base["target"]
    return None


def _on_progress(name, base, action, times=1):
    """`before` hook: `action()` the moment the run has made progress — the k-th product in the bag, or k/(times+1)
    of the walk to the target — for k = 1..times. Only while this row is the one running: a trigger left over from a
    row that already ended fires nothing (it used to interrupt the next row at 0 s)."""
    how = _progress_of(base)

    def hook(ctx):
        from . import api
        start = BASE["state"]
        here0 = (start["x"], start["y"], start["z"])

        def made(k):
            if how[0] == "bag":
                return _inv_now().count(how[1]) - _base_count(how[1]) >= k
            s_ = api.get("/state")
            whole = math.dist(here0, how[1])
            return whole - math.dist((s_["x"], s_["y"], s_["z"]), how[1]) >= whole * k / (times + 1)

        def watch():
            t0, k = time.time(), 1
            while k <= times and time.time() - t0 < 120 and BASE.get("name") == name:
                try:
                    if made(k):
                        action()
                        k += 1
                        continue
                except api.McError:
                    pass
                time.sleep(0.05)
        _threading.Thread(target=watch, daemon=True).start()
    return hook


def _inject_interrupt():
    __import__("bonobo.api", fromlist=["INTERRUPT"]).INTERRUPT = "bench: injected interrupt"


def _post_foreign_task():
    """Another commander posts a task straight to the mod (BodyContested for the skill)."""
    __import__("bonobo.api", fromlist=["api"]).api("POST", "/task?wait=0", {"type": "wait", "ticks": 40})


def _take_over(hold=3):
    """The player presses the toggle key (jar /control, as the key does), then hands back after `hold` s."""
    from . import api
    api.api("POST", "/control", {"paused": True})
    _threading.Timer(hold, lambda: api.api("POST", "/control", {"paused": False})).start()


def _sand_on_head():
    from . import api
    s_ = api.get("/state")
    x, y, z = s_["blockX"], s_["blockY"], s_["blockZ"]
    _chat(f"fill {x} {y + 1} {z} {x} {y + 3} {z} sand")


def _interrupt_when(token, n, message="bench: interrupt at the moment of success"):
    """`before` hook: the interrupt lands the moment the bag first shows the effect (n more `token`)."""
    def hook(ctx):
        def fire():
            from . import api
            t0 = time.time()
            while time.time() - t0 < 120:
                try:
                    if _inv_now().count(token) - _base_count(token) >= n:
                        api.INTERRUPT = message
                        return
                except api.McError:
                    pass
                time.sleep(0.05)
        _threading.Thread(target=fire, daemon=True).start()
    return hook


def _unless_done(check, run):
    """Resume only what is not done: an interruption that landed at the moment of success leaves nothing to redo."""
    def go(ctx):
        from . import api
        return True if check(api, _inv_now()) else run(ctx)
    return go


def _after_l0(name, run, resume, tries=4):
    """Run; when an interruption stops it (L0 preempted for a hazard), let the brain's own rounds rescue the body —
    L0 goes first in every round — then resume by what is still missing. The interruption is counted, never failed."""
    def go(ctx):
        from . import api, hazard
        fn = run
        for _ in range(tries):
            try:
                return fn(ctx)
            except api.INTERRUPTIONS:
                INTERRUPTS[name] = INTERRUPTS.get(name, 0) + 1
                t0 = time.time()
                while time.time() - t0 < 20 and hazard.due(api.get("/state")) is not None:
                    core.BRAIN.round()
                fn = resume
        raise api.McError(f"still interrupted after {tries} tries")
    return go


def _sprint_after(delay, ticks):
    """`before` hook: `delay` s into the run, the game sprints `ticks` ahead (/tick sprint): a furnace, a night, a
    crop is waited out in a second instead of in real time."""
    def hook(ctx):
        def fire():
            time.sleep(delay)
            _chat(f"tick sprint {ticks}")
        _threading.Thread(target=fire, daemon=True).start()
    return hook



def _hooks(*hooks):
    hooks = [h for h in hooks if h is not None]
    return lambda ctx: [h(ctx) for h in hooks] and None


# The furnace clock is game time: sprint it twice, once the load is in and once more late (see the row's doc).
SCENARIOS["iron_ingots"]["before"] = _hooks(_start("iron_ingots"), _sprint_after(4, 700), _sprint_after(10, 700))


def _achieve_needs(needs, rounds=12):
    """Resume by amount: plan the needs from the bag as it is now and run the plan (the brain's own path)."""
    def run(ctx):
        want = [(t, n + _base_count(t)) if t != "tool" else (t, n, *rest) for t, n, *rest in needs]
        done = lambda: all(_inv_now().count(t) >= n for t, n, *_ in want if t != "tool")  # noqa: E731
        return _achieve(ctx, want, done, rounds=rounds)
    return run


def _plan_is_empty(needs):
    """Goal already met: the planner, asked from the real bag, plans nothing — and nothing is run."""
    def run(ctx):
        from . import api, decompose, goals
        from .cost import Cost
        from .world import Snapshot
        snap = Snapshot()
        steps = decompose.decompose(snap.inv, goals.have(*needs), Cost(snap, ctx.mem))
        if steps:
            raise api.McError(f"goal already met, but planned {' → '.join(map(str, steps))}")
        return True
    return run


def _brain_rounds(seconds, until):
    """The whole cerebellum for up to `seconds` (L0 and upkeep included), until `until()`."""
    def run(ctx):
        t0 = time.time()
        while time.time() - t0 < seconds and not until():
            core.BRAIN.round()
        return until()
    return run


def _enclosed():
    """Walled in, feet and head, and covered: what a burrow, a pod or a dug-in hole must leave."""
    from . import skills
    return skills.enclosed()


def _breathing(least=280):
    """Out of the water's grip: the air bar back near full and the head out of the water."""
    def check(api, inv):
        from . import skills
        s = api.get("/state")
        return s["air"] >= least and not skills.head_underwater(s)
    return check


def _head_clear():
    from . import skills
    return not skills.head_buried()


# -- arena pieces (relative to ORIGIN) ----------------------------------------------------------------------------
def _floor(block="stone", half=8, depth=3):
    return [f"fill {_c(at(-half, -depth, -half))} {_c(at(half, -1, half))} {block}"]


def _tp(dx=0, dy=0, dz=0):
    return f"tp @p {_c(at(dx + 0.5, dy, dz + 0.5))}"


def _tree(x, z, wood="oak", height=5):
    """One tree built block by block: the same trunk and crown every run (a generated feature is a random shape,
    and its log count decided rows by chance). Leaves persistent: nothing decays under them."""
    return [f"fill {_c(at(x - 2, height - 2, z - 2))} {_c(at(x + 2, height - 1, z + 2))} {wood}_leaves[persistent=true]",
            f"fill {_c(at(x - 1, height, z - 1))} {_c(at(x + 1, height, z + 1))} {wood}_leaves[persistent=true]",
            f"fill {_c(at(x, 0, z))} {_c(at(x, height - 1, z))} {wood}_log"]


CHOP_TREE = (2, 0)       # the chop base's one oak (x, z): rows that must leave it standing read it here


def _grove(*spots, wood="oak"):
    return [f"fill {_c(at(-8, -1, -8))} {_c(at(8, -1, 8))} grass_block"] + [c for x, z in spots for c in _tree(x, z, wood)]


def _chest(pos, *items):
    return [f"setblock {_c(pos)} chest"] + \
        [f"item replace block {_c(pos)} container.{i} with {item}" for i, item in enumerate(items)]


def _pen(mob, n, half=7):
    walls = [f"fill {_c(at(a, 0, b))} {_c(at(c, 0, d))} oak_fence" for a, b, c, d in
             ((-half, -half, half, -half), (-half, half, half, half), (-half, -half + 1, -half, half - 1),
              (half, -half + 1, half, half - 1))]
    spots = [(3, 2), (-3, 2), (2, -4), (-4, -3), (4, -1), (-1, 4)][:n]
    return walls + [f"summon {mob} {_c(at(x, 0, z))}" for x, z in spots]


def _tank(x0, x1, z0, z1, top, water_top=None, floor_y=-4, wall="glass", open_side=None):
    """A glass tank inside the box: floor at `floor_y`, four walls up to `top`, open above, water up to `water_top`.
    `open_side` ("north", "south", "west", "east"): that wall stops at the water line, so a shore built beyond it
    can be swum to and climbed onto."""
    lo, hi = (x0 - 1, floor_y, z0 - 1), (x1 + 1, top, z1 + 1)
    out = [f"fill {_c(at(lo[0], floor_y, lo[2]))} {_c(at(hi[0], floor_y, hi[2]))} stone"]
    for side, a, b in (("north", (lo[0], lo[2]), (hi[0], lo[2])), ("south", (lo[0], hi[2]), (hi[0], hi[2])),
                       ("west", (lo[0], lo[2]), (lo[0], hi[2])), ("east", (hi[0], lo[2]), (hi[0], hi[2]))):
        height = water_top if side == open_side and water_top is not None else top
        out.append(f"fill {_c(at(a[0], floor_y + 1, a[1]))} {_c(at(b[0], height, b[1]))} {wall}")
    if water_top is not None:
        out.append(f"fill {_c(at(x0, floor_y + 1, z0))} {_c(at(x1, water_top, z1))} water")
    return out


# -- the bases: one skill, one job, one arena ---------------------------------------------------------------------
# `skills` names what a row proves: an effect a skill provides (`@skill(provides=...)`, e.g. "item:log", "sleep") —
# which survives skills being merged or renamed — or, for a skill that provides nothing, its registered name.
# name → dict(skills, doc, setup, run, check, budget, needs (the goal as planner needs, for resume / goal-met),
#             effect (token, n) for the at-success interrupt, progress: what counts as progress when it is not the effect)
# How long a skill's own work should take here (seconds, measured from its run starting, the setup's waits
# excluded); a row fails past TARGET_SLACK × this. From the speed-run targets: one tree, a small stone batch, one
# craft sitting, one bite, surfacing.
# A miss is its own named failure ("slow"), judged by the runner (`judge`, row["target_s"]), never folded into the
# outcome check: a pickaxe made in 11 s read "skill returned … without the outcome". The craft base carries its
# table, so its sitting includes placing it and taking it back (inferred from one trace: ~9-11 s).
TARGET_S = {"chop": 10.0, "mine_stone": 8.0, "craft": 8.0, "eat": 2.0, "find_air": 5.0}
TARGET_SLACK = 1.5


def _timed(run):
    """The run, its own seconds kept in BASE["run_s"] (the runner's `judge` reads them against row["target_s"])."""
    def go(ctx):
        t0 = time.time()
        try:
            return run(ctx)
        finally:
            BASE["run_s"] = time.time() - t0
    return go


def _skill_within(name, seconds):
    """The skill `name` itself (skill.LAST_S: from its own start, no planning, setup or walk to it) finished inside
    `seconds` — for rows whose run is the brain, where the run's own clock counts the plan too."""
    return lambda api, inv: __import__("bonobo.skill", fromlist=["LAST_S"]).LAST_S.get(name, 1e9) <= seconds


def _forget_skill_time(name):
    return lambda ctx: __import__("bonobo.skill", fromlist=["LAST_S"]).LAST_S.pop(name, None)




# The speedrun standard, from the moment the body stands where the job is done (the setup puts it there):
# 12 eyes' worth of ring filled from one spot ≤ 3 s; the last two frame cells cast and the portal lit ≤ 5 s.
SCENARIOS["activate_end_portal"].update(run=_timed(SCENARIOS["activate_end_portal"]["run"]), target_s=3.0)


BASES = {
    # Every base is one small job (≤ 15 s): the conditions and surprises add to it, and a row stays under 30 s.
    "nav": dict(skills=["goto"], doc="walk 8 blocks east over the arena", point="A",
                # The floor reaches the target: the arena is a sky platform, and a target over the void is unreachable.
                setup=_floor() + [f"fill {_c(at(8, -3, -3))} {_c(at(10, -1, 3))} stone", _tp()],
                run=lambda ctx: _skill("travel_to")(ctx, at(8, 0, 0), 2),
                check=_at(at(8, 0, 0), 3.5), budget=15, arena=16, target=at(8, 0, 0)),
    "chop": dict(skills=["item:log"], bound=("log", 2, 10), doc="one oak beside the body → 2 logs", point="A",
                 setup=_grove(CHOP_TREE) + [_tp()], run=lambda ctx: _skill("chop")(ctx, 2),
                 check=_gain("log", 2), needs=[("log", 2)], effect=("log", 1), budget=15),
    "mine_stone": dict(skills=["mine"], bound=("minecraft:cobblestone", 3, 5), doc="stone floor, a wooden pickaxe → 3 cobblestone", point="A",
                       setup=_floor() + [_tp(), "give @p wooden_pickaxe"],
                       run=lambda ctx: _skill("mine")(ctx, "minecraft:cobblestone", 3, ["stone"], 0),
                       check=_gain("minecraft:cobblestone", 3), needs=[("minecraft:cobblestone", 3)],
                       effect=("minecraft:cobblestone", 1), budget=15),
    "mine_iron": dict(skills=["mine"], doc="one iron ore in a stone wall at arm's length, a stone pickaxe → 1 raw iron", point="A",
                      setup=_floor() + [f"fill {_c(at(2, 0, -1))} {_c(at(3, 2, 1))} stone",
                                        f"setblock {_c(at(2, 0, 0))} iron_ore", _tp(),
                                        "give @p stone_pickaxe"],
                      run=lambda ctx: _skill("mine")(ctx, "minecraft:raw_iron", 1, ["iron_ore"], 1),
                      check=_gain("minecraft:raw_iron", 1), needs=[("minecraft:raw_iron", 1)],
                      effect=("minecraft:raw_iron", 1), budget=15),
    "craft": dict(skills=["craft"], bound=("minecraft:wooden_pickaxe", 1, 1), doc="planks, sticks, a table carried → a wooden pickaxe", point="A",
                  setup=_floor() + [_tp(), "give @p oak_planks 8", "give @p stick 4", "give @p crafting_table"],
                  run=lambda ctx: _skill("craft")(ctx, "minecraft:wooden_pickaxe", 1),
                  check=_gain("minecraft:wooden_pickaxe", 1, at_most=1), needs=[("minecraft:wooden_pickaxe", 1)],
                  effect=("minecraft:wooden_pickaxe", 1), progress=("planks", 1), budget=15),
    "smelt": dict(skills=["smelt"], bound=("minecraft:iron_ingot", 1, 1), doc="a furnace, 1 raw iron, coal → 1 iron ingot", point="A",
                  setup=_floor() + [_tp(), "give @p furnace", "give @p raw_iron 1", "give @p coal 1"],
                  run=lambda ctx: _skill("smelt")(ctx, "minecraft:iron_ingot", "minecraft:raw_iron", 1, "coal"),
                  check=_gain("minecraft:iron_ingot", 1, at_most=1), needs=[("minecraft:iron_ingot", 1)],
                  effect=("minecraft:iron_ingot", 1), budget=15,
                  pre=_sprint_after(3, 400)),
    "hunt": dict(skills=["hunt"], doc="a pen of three cows, a sword → 1 beef", point="A",
                 setup=_floor("grass_block") + _pen("cow", 3, half=5) + [_tp(), "give @p iron_sword"],
                 run=lambda ctx: _skill("hunt")(ctx, "minecraft:beef", 1, ["minecraft:cow"], False),
                 check=_gain("minecraft:beef", 1), needs=[("minecraft:beef", 1)], effect=("minecraft:beef", 1),
                 budget=15, entities=[("minecraft:cow", 3)]),
    "eat": dict(skills=["eat"], doc="hungry, bread carried → the food bar rises", point="A",
                setup=_floor() + [_tp(), "give @p bread 4"],
                pre=lambda ctx: (_chat("effect give @p minecraft:hunger 5 255 true"), time.sleep(5.5),
                                 BASE.update(food_before=__import__("bonobo.api", fromlist=["get"]).get("/state")["food"])),
                run=lambda ctx: _skill("eat")(), check=_food_up(), budget=15,
                combat=True),        # hunger only drains off peaceful: the runner sets normal difficulty for combat rows
    "sleep": dict(skills=["sleep"], doc="night, a bed carried → morning", point="A",
                  setup=_floor() + [_tp(), "give @p white_bed", "time set 18000"],
                  run=lambda ctx: _skill("sleep")(ctx, ctx.policy), check=_is_day(), budget=15),
    "loot": dict(skills=["loot_chest"], bound=("minecraft:iron_ingot", 5, 5), doc="a chest of iron and bread 2 blocks away → the iron", point="A",
                 setup=_floor() + _chest(at(2, 0, 1), "iron_ingot 5", "bread 4") + [_tp()],
                 run=lambda ctx: _skill("loot_chest")(ctx), check=_gain("minecraft:iron_ingot", 5),
                 effect=("minecraft:iron_ingot", 1), budget=15),
}

# -- the full bag: filled after the base's own kit, to leave exactly `free` slots ------------------------------------
def _fill_bag(free, item="dirt", stack=64):
    """`before` hook: fill the bag with `item` until `free` slots are left (the kit the setup gave stays)."""
    def hook(ctx):
        from .world import Inventory
        room = Inventory().free_slots() - free
        if room > 0:
            _chat(f"give @p {item} {room * stack}")
            time.sleep(0.5)
    return hook


def _kept(token):
    """None of `token` left the bag (what the start held is still there)."""
    return lambda api, inv: inv.count(token) >= _base_count(token)


def _product(base):
    return (base.get("effect") or ("minecraft:cobblestone", 1))[0]


def _stack_room_setup(base):
    """A stack of the base's product with room for exactly what the base makes (64 − n), then the bag full."""
    token, n = _product(base), (base.get("needs") or [(None, 4)])[0][1]
    item = "oak_log" if token == "log" else token.split(":")[-1]
    return [f"give @p {item} {64 - n}"]


# -- the conditions ------------------------------------------------------------------------------------------------
# name → dict(axis, doc, bases it applies to, and what it changes). `setup` is appended to the base's; `fails` makes
# the row an expected failure with that reason; `check` (a function of the base) replaces the base's effect check;
# `run` (a function of name, base) wraps the base's run; `before` hooks run after the start snapshot.
H = 6        # canopy height


def _all_bases(*names):
    return set(names) if names else set(BASES)


CONDITIONS = {
    # terrain
    "canopy": dict(axis="terrain", doc="under a closed leaf canopy", bases={"nav", "chop", "hunt", "loot", "sleep"},
                   setup=[f"fill {_c(at(-8, H, -8))} {_c(at(8, H + 1, 8))} oak_leaves[persistent=true]"]),
    # `outline` touches only the shell, and the shell's bottom is the floor layer: what the base built stays.
    "cave": dict(axis="terrain", doc="in a dark stone room under rock",
                 bases={"nav", "mine_stone", "craft", "smelt", "loot", "sleep"},
                 setup=[f"fill {_c(at(-9, -1, -9))} {_c(at(15, 4, 9))} stone outline"]),
    "underwater": dict(axis="terrain", doc="the arena flooded two blocks deep", bases={"nav", "eat", "loot"},
                       setup=[f"fill {_c(at(-9, -1, -9))} {_c(at(15, 3, 9))} glass outline",
                              f"fill {_c(at(-8, 0, -8))} {_c(at(14, 1, 8))} water replace air"]),
    "pillar": dict(axis="terrain", doc="starting on top of a 1×1 pillar 10 high", bases={"nav", "chop", "hunt"},
                   setup=[f"fill {_c(at(0, 0, 0))} {_c(at(0, 9, 0))} dirt", _tp(0, 10, 0)]),
    "cliff_edge": dict(axis="terrain", doc="the arena ends in a 20-block drop two blocks behind the body",
                       bases={"nav", "mine_stone", "chop", "craft"},
                       setup=[f"fill {_c(at(-8, -3, -8))} {_c(at(-3, -1, 8))} air", _tp(-1, 0, 0)]),
    "nether": dict(axis="terrain", doc="the same job in the Nether", bases={"nav", "craft", "smelt", "mine_stone"},
                   dimension="minecraft:the_nether"),
    "night": dict(axis="terrain", doc="at night", bases={"nav", "chop", "hunt", "mine_stone", "loot"},
                  setup=["time set 18000"]),
    "rain": dict(axis="terrain", doc="in the rain", bases={"nav", "chop", "hunt", "sleep"}, setup=["weather rain"]),
    # timing
    "pickup_lag": dict(axis="timing", doc="the server at 8 ticks/s: drops and slots update late",
                       bases={"chop", "mine_stone", "mine_iron", "hunt", "loot", "craft"}, tick_rate=8),
    "inventory_lag": dict(axis="timing", doc="the server at 4 ticks/s: the bag reads a craft, a take, a meal late",
                          bases={"craft", "smelt", "loot", "eat"}, tick_rate=4),
    "interrupt_mid_work": dict(axis="timing", doc="interrupted mid-work, then resumed by what is still missing",
                               bases={"chop", "mine_stone", "mine_iron", "craft", "smelt", "hunt"}, interrupt="mid"),
    "interrupt_twice": dict(axis="timing", doc="interrupted twice, resumed twice",
                            bases={"chop", "mine_stone", "smelt", "hunt"}, interrupt="twice"),
    "interrupt_at_success": dict(axis="timing", doc="interrupted the moment the effect shows in the bag",
                                 bases={"chop", "mine_stone", "craft", "smelt", "loot"}, interrupt="success"),
    "contested": dict(axis="timing", doc="another commander posts a task mid-run (BodyContested), then resume",
                      bases={"chop", "mine_stone", "nav"}, interrupt="contested"),
    "player_takeover": dict(axis="timing", doc="the player takes control mid-run: stand down, no failure counted",
                            bases={"chop", "nav"}, interrupt="player"),
    # hazards mid-job: L0 takes the body, rescues it, and the job resumes by what is still missing
    "buried_by_sand": dict(axis="hazard", doc="sand drops on the head mid-job", hazard="sand",
                           bases={"nav", "chop", "mine_stone", "craft", "smelt"}),
    "lava_edge": dict(axis="hazard", doc="a lava channel runs along the arena, one block beside the work", hazard="lava",
                      bases={"nav", "chop", "mine_stone", "hunt", "loot"},
                      setup=[f"fill {_c(at(-8, -1, 2))} {_c(at(15, -1, 2))} lava"]),
    # inventory
    "one_slot": dict(axis="inventory", doc="one free slot left: the product still fits, the job is done",
                     bases={"chop", "mine_stone", "hunt", "loot"}, before=lambda b: _fill_bag(1)),
    "stack_room": dict(axis="inventory", doc="no free slot, but the product's own stack has room for all of it → "
                                             "done as usual, no 'bag full'",
                       bases={"chop", "mine_stone"}, setup_for=_stack_room_setup, before=lambda b: _fill_bag(0)),
    "valuables_full": dict(axis="inventory", doc="every slot full of diamonds: nothing new fits and nothing in the "
                                                 "bag may be thrown → failed with the bag named, no diamond lost",
                           bases={"chop", "mine_stone", "hunt", "loot"}, before=lambda b: _fill_bag(0, "diamond"),
                           fails=r"bag|full|room|slot", fails_check=lambda base: _kept("minecraft:diamond")),
    "full_bag": dict(axis="inventory", doc="every slot full of dirt: nothing new can be picked up",
                     bases={"chop", "mine_stone", "hunt", "loot"}, setup=["give @p dirt 2304"],
                     fails=r"bag|full|room|slot", fails_check=lambda base: _same_bag()),
    "tool_one_use": dict(axis="inventory", doc="the pickaxe has one use left", bases={"mine_stone", "mine_iron"},
                         setup=["clear @p", "give @p wooden_pickaxe[damage=58]", "give @p stone_pickaxe[damage=130]"],
                         fails=r"pickaxe|tier",
                         fails_check=lambda base: (_gain("minecraft:cobblestone", 0, at_most=2) if base == "mine_stone"
                                                   else _blocks(at(4, 0, 0), at(4, 1, 0), "iron_ore", 1))),
    "wrong_tool": dict(axis="inventory", doc="only a wooden pickaxe for iron ore", bases={"mine_iron"},
                       setup=["clear @p", "give @p wooden_pickaxe"], fails=r"tier-1 pickaxe|tier 1|pickaxe",
                       fails_check=lambda base: _all(_same_bag(), _blocks(at(4, 0, 0), at(4, 1, 0), "iron_ore", 2))),
    "goal_met": dict(axis="inventory", doc="the bag already holds the goal: plan nothing, do nothing",
                     bases={"chop", "mine_stone", "mine_iron", "craft", "smelt", "hunt"}, goal_met=True),
}

# Surprises: one-off rows, each a base with its own twist and its own verdict.
SURPRISES = {
    "leaves_block_trunk": dict(base="chop", doc="leaves packed round the trunk at head height",
                               setup=[f"fill {_c(at(3, 0, -1))} {_c(at(5, 2, 1))} oak_leaves[persistent=true]",
                                      f"fill {_c(at(4, 0, 0))} {_c(at(4, 4, 0))} oak_log"]),
    "floating_logs": dict(base="chop", doc="three logs floating 3 blocks up, no trunk under them",
                          setup=[f"fill {_c(at(-8, -1, -8))} {_c(at(8, -1, 8))} grass_block",
                                 f"fill {_c(at(4, 3, 0))} {_c(at(4, 5, 0))} oak_log", _tp(),
                                 "give @p dirt 16"], replace_setup=True, check=_gain("log", 2), run_n=2),
    "empty_chest": dict(base="loot", doc="the only chest is empty", replace_setup=True,
                        setup=_floor() + [f"setblock {_c(at(5, 0, 1))} chest", _tp(-1, 0, 0)],
                        fails=r"empty|nothing|worth|no unlooted",
                        check=_all(_same_bag(), _blocks(at(5, 0, 1), at(5, 0, 1), "chest", 1, 1))),
    "bed_obstructed": dict(base="sleep", doc="a bed carried, the body boxed in a 1×1 cell", replace_setup=True,
                           setup=_floor() + [f"fill {_c(at(-1, 0, -1))} {_c(at(1, 2, 1))} stone",
                                             f"fill {_c(at(0, 0, 0))} {_c(at(0, 1, 0))} air", _tp(),
                                             "give @p white_bed", "time set 18000"],
                           fails=r"no flat 2-block spot|no room|obstruct",
                           check=_all(_same_bag(), _not(_is_day()),
                                      _no_block_suffix(at(-3, -1, -3), at(3, 3, 3), "_bed"))),
    "bed_in_nether": dict(base="sleep", doc="night in the Nether, a bed carried: must refuse (it explodes)",
                          dimension="minecraft:the_nether", replace_setup=True,
                          setup=_floor("netherrack") + [_tp(), "give @p white_bed", "time set 18000"],
                          fails=r"nether|dimension|explod",
                          check=_all(_alive(18), _no_block_suffix(at(-4, -1, -4), at(4, 2, 4), "_bed"))),
    # Must-fail controls, one per base that had none: the right failure, named, and the world untouched.
    "nav_sealed_in": dict(base="nav", doc="the body sealed in bedrock: no way out, and it says so", replace_setup=True,
                          setup=_floor() + [f"fill {_c(at(-1, -1, -1))} {_c(at(1, 2, 1))} bedrock",
                                            f"fill {_c(at(0, 0, 0))} {_c(at(0, 1, 0))} air", _tp()],
                          fails=r"no route|no path|unreachable|could not get|not reach",
                          check=_all(_same_bag(), _at(at(0, 0, 0), 1.5))),
    "craft_short_of_planks": dict(base="craft", doc="two planks and no sticks for a pickaxe: missing, named",
                                  replace_setup=True,
                                  setup=_floor() + [_tp(), "give @p oak_planks 2", "give @p crafting_table"],
                                  fails=r"missing|short|not enough", check=_same_bag()),
    "smelt_without_fuel": dict(base="smelt", doc="a furnace and raw iron, nothing to burn: no fuel, named",
                               replace_setup=True, setup=_floor() + [_tp(), "give @p furnace", "give @p raw_iron 1"],
                               fails=r"no coal|fuel|burn", check=_gain("minecraft:iron_ingot", 0, at_most=0)),
    "eat_with_nothing": dict(base="eat", doc="hungry, nothing edible carried: nothing to eat, named",
                             replace_setup=True, setup=_floor() + [_tp()], fails=r"nothing edible",
                             check=_same_bag()),
    # The lava directly under the one ore (it was at x 4..5, nowhere near it); mined with it sealed first
    # (skills.seal_plan). One ore gives one raw iron (the check wanted 2 of a single ore: never passable).
    "lava_under_ore": dict(base="mine_iron", doc="lava right under the iron ore, blocks carried → sealed, then mined",
                           setup=[f"setblock {_c(at(2, -1, 0))} lava", "give @p cobblestone 4"],
                           check=_all(_gain("minecraft:raw_iron", 1), _alive(14))),
    "falling_gravel": dict(base="mine_stone", doc="gravel stacked over the stone to be mined",
                           setup=[f"fill {_c(at(-3, 0, -3))} {_c(at(3, 3, 3))} gravel",
                                  f"fill {_c(at(-1, 0, -1))} {_c(at(1, 3, 1))} air"],
                           check=_all(_gain("minecraft:cobblestone", 6), _alive(14))),
    "start_cell_on_a_fence": dict(base="nav", doc="the walk starts standing on a fence post (the mod judged such a "
                                                  "start cell unstandable: '1 positions explored')",
                                  setup=[f"setblock {_c(at(0, 0, 0))} oak_fence", _tp(0, 1.5, 0)]),
    "start_cell_in_a_nook": dict(base="nav", doc="the walk starts in a one-block nook under a slab",
                                 setup=[f"fill {_c(at(-1, 0, -1))} {_c(at(1, 1, 1))} stone",
                                        f"fill {_c(at(0, 0, 0))} {_c(at(1, 1, 0))} air",
                                        f"setblock {_c(at(0, 2, 0))} stone_slab"]),
    "chest_or_tree": dict(base="chop", doc="4 logs in a chest by the body, a tree 12 away: the brain takes the "
                                           "cheaper (plan-driven, test point C)", point="C",
                          setup=_chest(at(1, 0, 1), "oak_log 4"),
                          # The planner takes from containers as last seen open (memory.note_container): opened once.
                          before=lambda ctx: core.BRAIN.mem.note_container(
                              at(1, 0, 1), "minecraft:overworld", [{"id": "minecraft:oak_log", "count": 4}]),
                          run=lambda ctx: _achieve(ctx, [("log", 4)], lambda: _inv_now().count("log") >= 4),
                          # 4 logs gained, the base's tree left whole (its trunk, where the base built it)
                          check=_all(_gain("log", 4), _blocks(at(CHOP_TREE[0], 0, CHOP_TREE[1]),
                                                              at(CHOP_TREE[0], 6, CHOP_TREE[1]), "oak_log", 3))),
}


def _row(name, base, cond=None, extra=None):
    """One row: the base, changed by a condition or a surprise."""
    b, c, x = BASES[base], cond or {}, extra or {}
    setup = list(x["setup"]) if x.get("replace_setup") else list(b["setup"]) + list(c.get("setup", ())) + \
        list(x.get("setup", ()))
    run, check, hooks = x.get("run", b["run"]), x.get("check", b["check"]), [_start(name)]
    fails = x.get("fails", c.get("fails"))
    if c.get("setup_for"):
        setup += c["setup_for"](b)
    if b.get("pre"):
        hooks.append(b["pre"])
    if x.get("before"):
        hooks.append(x["before"])
    if c.get("before"):
        hooks.append(c["before"](b))
    if x.get("run_n"):
        run = (lambda n: lambda ctx: _skill("chop")(ctx, n))(x["run_n"])
    if c.get("goal_met"):
        needs = b["needs"]
        setup += [f"give @p {t.split(':')[-1] if t != 'log' else 'oak_log'} {n}" for t, n in needs]
        run, check = _plan_is_empty(needs), _same_bag_and_place()
    kind = c.get("interrupt")
    if kind:
        resume = _unless_done(b["check"], _achieve_needs(b["needs"]) if b.get("needs") else b["run"])
        # By progress, never by the clock: with the arenas squeezed a hunt ended in 3.9 s and a 4 s interrupt
        # landed after it (and in the next row).
        if kind == "mid":
            hooks.append(_on_progress(name, b, _inject_interrupt))
        elif kind == "twice":
            hooks.append(_on_progress(name, b, _inject_interrupt, times=2))
        elif kind == "success":
            hooks.append(_interrupt_when(*b["effect"]))
        elif kind == "contested":
            hooks.append(_on_progress(name, b, _post_foreign_task))
        elif kind == "player":
            hooks.append(_on_progress(name, b, _take_over))
        run = _resume(name, run, resume)
        if kind == "success" and b.get("bound"):
            check = _gain(*b["bound"])       # the effect once: an interruption at success is not a reason to redo it
        check = _all(check, _interrupted(2 if kind == "twice" else 1))
    if c.get("hazard"):
        resume = _unless_done(b["check"], _achieve_needs(b["needs"]) if b.get("needs") else b["run"])
        if c["hazard"] == "sand":
            hooks.append(_on_progress(name, b, _sand_on_head))
        run = _after_l0(name, run, resume)
        check = _all(check, _alive(8), lambda api, inv: _head_clear(),
                     lambda api, inv: not api.get("/state")["inLava"])
    if fails:
        run = _expect_failure(name, run, fails)
        effect = x.get("check") or (c["fails_check"](base) if c.get("fails_check") else _same_bag())
        check = _all(_failed_as_expected(), _alive(), effect)
    if c.get("also"):
        check = _all(check, c["also"](b))
    target_s = TARGET_S[base] * TARGET_SLACK if not c and not x and base in TARGET_S else None
    if target_s:
        run = _timed(run)
    row = {"doc": f"{b['doc']} — {x.get('doc') or c.get('doc', 'as is')}", "module": "skills", "setup": setup,
           "before": _hooks(*hooks), "run": run, "check": check, **({"target_s": target_s} if target_s else {}),
           # A lagging server multiplies the base's time; the cap is the bench's hard 60 s (runner.ROW_LIMIT_S).
           # A lagging server doubles the base's time (its bases do one unit of work); nothing else earns more. The cap
           # is the bench's hard limit (runner.ROW_LIMIT_S).
           "budget": min(30, b["budget"] * (2 if c.get("tick_rate", 20) < 20 else 1)),
           "skills": list(b["skills"]), "point": x.get("point", b.get("point", "A")),
           "tags": {"base": base, **({c["axis"]: next(k for k, v in CONDITIONS.items() if v is c)} if c else {}),
                    **({"surprise": name} if x else {})}}
    if fails:
        row["fails"] = fails
    for key in ("tick_rate", "dimension"):
        if c.get(key) or x.get(key) or b.get(key):
            row[key] = x.get(key) or c.get(key) or b.get(key)
    if b.get("entities"):
        row["expect_entities"] = list(b["entities"])
    if b.get("combat"):
        row["combat"] = True
    return row


for _base in BASES:
    SHEET[f"{_base}__base"] = _row(f"{_base}__base", _base)
def cover(conditions, bases, pinned=()):
    """Pure: the (condition, base) pairs the sheet runs — coverage, not the full product. Every condition appears,
    and within each axis every base it applies to appears (the pairs that differ: one condition per base per axis,
    one base per condition), and every base that has a must-fail condition keeps one (its control), greedily, most
    new coverage first, ties in table order. `pinned` pairs are kept."""
    order = list(bases)
    pairs = [(c, b) for c, v in conditions.items() for b in order if b in v["bases"]]
    must = lambda c, b: {("must", b)} if conditions[c].get("fails") else set()     # noqa: E731
    need = {("cond", c) for c, _b in pairs} | {("axis", conditions[c]["axis"], b) for c, b in pairs}
    need |= {m for c, b in pairs for m in must(c, b)}
    new = lambda p: ({("cond", p[0]), ("axis", conditions[p[0]]["axis"], p[1])} | must(*p)) & need   # noqa: E731
    out = [p for p in pairs if p in set(pinned)]
    for p in out:
        need -= new(p)
    while need:
        best = max(pairs, key=lambda p: (len(new(p)), -pairs.index(p)))
        out.append(best)
        need -= new(best)
    return sorted(out, key=pairs.index)


# One pair named by another row or check (dig_in_night's night is chop's).
for _cname, _base in cover(CONDITIONS, BASES, pinned=[("night", "chop")]):
    SHEET[f"{_base}__{_cname}"] = _row(f"{_base}__{_cname}", _base, CONDITIONS[_cname])
for _sname, _s in SURPRISES.items():
    SHEET[_sname] = _row(_sname, _s["base"], None, _s)

# -- the rest of the registry, one row each: every skill must be proven in the world somewhere ------------------
_ONE = {
    "take_bed": (["take"], "a village bed 6 blocks away → carried", _floor() + [f"setblock {_c(at(6, 0, 0))} red_bed",
                                                                             _tp()],
                 lambda ctx: _skill("take")(ctx, "bed", 1, ["red_bed"]), _gain("bed", 1), 30),
    "tidy_full_bag": (["room:tidy"], "a full bag of junk → slots free", _floor() + [_tp(), "give @p dirt 2304"],
                      lambda ctx: _skill("tidy_inventory")(ctx), _free_slots(2), 30),
    "deposit_home_chest": (["room:deposit"], "a home chest beside the body, a bag of cobblestone → stored",
                           _floor() + [f"setblock {_c(at(2, 0, 0))} chest", _tp(), "give @p cobblestone 1280",
                                       "give @p dirt 640"],
                           lambda ctx: (ctx.mem.add_site("home", at(2, 0, 0), "minecraft:overworld", name="home"),
                                        _skill("deposit")(ctx))[1], _all(_free_slots(8), _dropped_nothing()), 60),
    "dig_in_night": (["shelter:dig in"], "night on stone, a pickaxe → three down, sealed", _floor(depth=4) +
                     [_tp(), "give @p stone_pickaxe", "give @p cobblestone 8", "time set 18000"],
                     lambda ctx: _skill("dig_in")(ctx),
                     _all(lambda api, inv: api.get("/state")["blockY"] < at(0, 0, 0)[1], lambda api, inv: _enclosed()),
                     40),
    "dig_out_morning": (["dig_out"], "morning, sealed in a 1×1 pocket → out", _floor() +
                        [f"fill {_c(at(-1, 0, -1))} {_c(at(1, 2, 1))} stone", f"fill {_c(at(0, 0, 0))} {_c(at(0, 1, 0))} air",
                         _tp(), "give @p stone_pickaxe"],
                        lambda ctx: _skill("dig_out")(ctx), lambda api, inv: not _enclosed(), 40),
    "pod_open_ground": (["shelter:wall in"], "night, open ground, 16 blocks → walled in", _floor() +
                        [_tp(), "give @p cobblestone 16", "time set 18000"], lambda ctx: _skill("pod")(ctx),
                        _blocks(at(-1, 0, -1), at(1, 2, 1), "cobblestone", 9), 40),
    "pod_in_water": (["shelter:wall in"], "night, standing on a pillar in deep water → walled in anyway",
                     _tank(-6, 6, -6, 6, 0, water_top=-1) + [f"fill {_c(at(0, -3, 0))} {_c(at(0, -1, 0))} stone",
                                                            _tp(), "give @p cobblestone 32", "time set 18000"], lambda ctx: _skill("pod")(ctx),
                     _blocks(at(-1, 0, -1), at(1, 2, 1), "cobblestone", 9), 60),
    "build_shelter_flat": (["build:shelter"], "flat stone, the hut's materials → a shelter standing",
                           _floor() + [_tp(), "give @p cobblestone 32", "give @p oak_door", "give @p torch 2"],
                           lambda ctx: _skill("build_shelter")(ctx),
                           _all(_blocks(at(-6, 0, -6), at(6, 3, 6), "cobblestone", 14),
                                _blocks(at(-6, 0, -6), at(6, 3, 6), "oak_door", 1),
                                _blocks(at(-6, 0, -6), at(6, 3, 6), TORCHES, 1)), 60),
    "burrow_hillside": (["burrow"], "night, a stone hillside beside the body → tunnelled in and sealed",
                        _floor() + [f"fill {_c(at(2, 0, -4))} {_c(at(8, 4, 4))} stone", _tp(),
                                    "give @p stone_pickaxe", "give @p cobblestone 8", "time set 18000"],
                        lambda ctx: _skill("burrow")(ctx), _all(_alive(18), lambda api, inv: _enclosed()), 60),
    "contain_lava_pool": (["contain_lava"], "an open lava pool beside the body → covered", _floor() +
                          [f"fill {_c(at(2, -1, -1))} {_c(at(3, -1, 1))} lava", _tp(), "give @p cobblestone 16"],
                          lambda ctx: _skill("contain_lava")(ctx), _blocks(at(2, -1, -1), at(3, -1, 1), "lava", 0, 0), 30),
    "torch_in_the_dark": (["light"], "a dark room, torches carried → one torch placed",
                          _floor() + [f"fill {_c(at(-4, 0, -4))} {_c(at(4, 3, 4))} stone hollow",
                                      f"fill {_c(at(-3, 0, -3))} {_c(at(3, 2, 3))} air", _tp(), "give @p torch 4"],
                          lambda ctx: _skill("light_area")(ctx, 4, 1),
                          _blocks(at(-3, 0, -3), at(3, 2, 3), TORCHES, 1), 20),
    "light_the_room": (["light_area"], "a dark 9×9 room, 8 torches → several placed",
                       _floor() + [f"fill {_c(at(-6, 0, -6))} {_c(at(6, 3, 6))} stone hollow",
                                   f"fill {_c(at(-5, 0, -5))} {_c(at(5, 2, 5))} air", _tp(), "give @p torch 8"],
                       lambda ctx: _skill("light_area")(ctx, 6, 4), _blocks(at(-5, 0, -5), at(5, 2, 5), TORCHES, 2), 60),
    "find_air_capped": (["find_air"], "under water with a stone cap, out of breath → air",
                        [f"fill {_c(at(-4, -4, -4))} {_c(at(4, 3, 4))} stone", f"fill {_c(at(-3, -3, -3))} {_c(at(3, 2, 3))} water",
                         _tp(0, -3, 0), "give @p stone_pickaxe"],
                        lambda ctx: _skill("find_air")(ctx), _all(_alive(10), _breathing()), 45),
    "surface_from_lake": (["reach:air"], "4 blocks down in open water → up to breathe",
                          _tank(-5, 5, -5, 5, 8, water_top=7) + [_tp(0, -3, 0)], lambda ctx: _skill("find_air")(ctx),
                          _breathing(), 30),
    "reach_land_swim": (["reach:land"], "night, treading water 10 blocks from shore → on dry land",
                        _tank(-8, 9, -8, 8, 1, water_top=-1, open_side="east") +
                        [f"fill {_c(at(10, -3, -8))} {_c(at(14, -1, 8))} stone",
                                                               _tp(), "time set 18000"],
                        lambda ctx: _skill("reach_land")(ctx),
                        lambda api, inv: api.get("/state")["onGround"] and not api.get("/state")["inWater"], 60),
    "footing_in_water": (["reach:footing"], "treading water, cobblestone carried → a block underfoot",
                         _tank(-4, 4, -4, 4, 0, water_top=-1) + [_tp(), "give @p cobblestone 8"],
                         lambda ctx: _skill("stand_on_a_block")(ctx),
                         _all(lambda api, inv: api.get("/state")["onGround"], _under_feet("cobblestone")), 20),
    "unbury_sand": (["unbury"], "sand dropped on the head → dug out", _floor() + [_tp()],
                    lambda ctx: (_chat(f"fill {_c(at(0, 1, 0))} {_c(at(0, 3, 0))} sand"), time.sleep(1),
                                 _skill("unbury")(ctx))[2], lambda api, inv: _head_clear() and _alive(10)(api, inv), 20),
    "seek_remembered": (["seek"], "memory says iron ore 16 blocks away → walked there",
                        _floor(half=10) + [f"fill {_c(at(10, -1, -8))} {_c(at(19, -1, 8))} stone",
                                           f"setblock {_c(at(17, 0, 0))} iron_ore", _tp(-6, 0, 0)],
                        lambda ctx: (ctx.mem.note_seen("iron_ore", at(17, 0, 0), "minecraft:overworld"),
                                     _skill("seek")(ctx, ["iron_ore"]))[1], _at(at(17, 0, 0), 6), 60),
    "smelt_in_background": (["start_smelt_job", "collect_job"], "load a furnace, walk off, come back → ingots",
                            _floor() + [_tp(), "give @p furnace", "give @p raw_iron 2", "give @p coal 1"],
                            lambda ctx: (_skill("start_smelt_job")(ctx, "minecraft:iron_ingot", "minecraft:raw_iron", 2,
                                                                   "coal"), _chat("tick sprint 400"), time.sleep(2),
                                         _skill("collect_job")(ctx, ctx.mem.jobs("minecraft:overworld")[0]))[2],
                            _gain("minecraft:iron_ingot", 2), 40),
    "open_space_from_shaft": (["move_to_open_space"], "a full bag at the bottom of a 1×1 shaft → out where it is open",
                              _floor(depth=4) + [f"fill {_c(at(0, -3, 0))} {_c(at(0, -1, 0))} air", _tp(0, -3, 0),
                                                  "give @p dirt 2304", "give @p stone_pickaxe"],
                              lambda ctx: _skill("move_to_open_space")(ctx),
                              _room_to_work(), 60),
    "repair_two_pickaxes": (["repair_tool"], "two worn stone pickaxes → one", _floor() +
                            [_tp(), "give @p stone_pickaxe[damage=100]", "give @p stone_pickaxe[damage=100]"],
                            lambda ctx: _skill("repair_tool")(ctx, "pickaxe"),
                            lambda api, inv: inv.count("minecraft:stone_pickaxe") == 1 and any(
                                s_["id"] == "minecraft:stone_pickaxe" and s_.get("damage", 999) < 100 for s_ in inv.slots),
                            20),
    "repair_broken_hut": (["repair_site"], "a remembered hut with two wall blocks knocked out → rebuilt",
                          _floor() + [f"fill {_c(at(2, 0, -2))} {_c(at(6, 2, 2))} cobblestone hollow",
                                      f"fill {_c(at(2, 0, 0))} {_c(at(2, 1, 0))} air", _tp(), "give @p cobblestone 8"],
                          lambda ctx: _skill("repair_site")(ctx, _broken_hut(ctx)),
                          _blocks(at(2, 0, 0), at(2, 1, 0), "cobblestone", 2), 60),
    "wait_out_the_night": (["wait:day"], "night in a sealed stone room, no bed → waited until morning",
                           _floor() + [f"fill {_c(at(-2, 0, -2))} {_c(at(2, 4, 2))} stone hollow", _tp(0, 1, 0),
                                       "time set 23500"],     # 25 s before daybreak: the wait itself, not the night
                           lambda ctx: _skill("wait_for_day")(ctx), _is_day(), 45),
    "withdraw_from_chest": (["withdraw"], "a chest of iron beside the body → 4 ingots taken out",
                            _floor() + _chest(at(2, 0, 0), "iron_ingot 9") + [_tp()],
                            lambda ctx: _skill("withdraw")(ctx, "minecraft:iron_ingot", 4, at(2, 0, 0)),
                            _gain("minecraft:iron_ingot", 4), 30),
    "anvil_repair_pickaxe": (["repair"], "an anvil, a worn diamond pickaxe, diamonds, levels → repaired",
                             _floor() + [f"setblock {_c(at(2, 0, 0))} anvil", _tp(),
                                         "give @p diamond_pickaxe[damage=1200]", "give @p diamond 2",
                                         "experience add @p 20 levels"],
                             lambda ctx: _skill("anvil_repair")(ctx, "minecraft:diamond_pickaxe", "minecraft:diamond"),
                             lambda api, inv: any(s_["id"] == "minecraft:diamond_pickaxe" and s_.get("damage", 0) < 1200
                                                  for s_ in inv.slots), 60),
    "enchant_pickaxe": (["enchant"], "an enchanting table, lapis, levels, an iron pickaxe → enchanted (lapis spent)",
                        _floor() + [f"setblock {_c(at(2, 0, 0))} enchanting_table", _tp(), "give @p iron_pickaxe",
                                    "give @p lapis_lazuli 6", "experience add @p 30 levels"],
                        lambda ctx: _skill("enchant_item")(ctx, "minecraft:iron_pickaxe"),
                        _all(lambda api, inv: inv.count("minecraft:lapis_lazuli") < 6,
                             _slot_has("minecraft:iron_pickaxe", "enchant")), 60),
    "trade_bread": (["trade"], "a farmer selling bread for an emerald, 3 emeralds → bread",
                    _floor() + [_tp(), "give @p emerald 3",
                                f'summon villager {_c(at(3, 0, 0))} {{NoAI:1b,VillagerData:{{profession:"minecraft:farmer",'
                                f'level:2,type:"minecraft:plains"}},Offers:{{Recipes:[{{buy:{{id:"minecraft:emerald",'
                                f'count:1}},sell:{{id:"minecraft:bread",count:6}},maxUses:12}}]}}}}'],
                    lambda ctx: _skill("trade")(ctx, "minecraft:bread"), _gain("minecraft:bread", 6), 45),
    "brew_fire_resistance_stand": (["brew:fire_resistance"], "a brewing stand, water bottles, wart, magma cream, "
                                                              "blaze powder → fire resistance (the cream spent)",
                                   _floor() + [f"setblock {_c(at(2, 0, 0))} brewing_stand", _tp(),
                                               'give @p potion[potion_contents={potion:"minecraft:water"}] 3',
                                               "give @p nether_wart", "give @p magma_cream", "give @p blaze_powder 2"],
                                   lambda ctx: _skill("brew_fire_resistance")(ctx),
                                   _slot_has("minecraft:potion", "fire_resistance"), 50),
    "collect_auto_smelter": (["collect_machine"], "a remembered auto smelter whose output chest holds 8 ingots → "
                                                  "taken",
                             _floor() + _chest(at(3, 0, 0), "iron_ingot 8") + [_tp()],
                             lambda ctx: _skill("collect_machine")(ctx, _bench_machine(ctx, at(3, 0, 0))),
                             _gain("minecraft:iron_ingot", 8), 45),
    "bridge_the_gap": (["bridge_toward"], "a 6-block gap in the floor toward the target, blocks carried → across",
                       _floor() + [f"fill {_c(at(2, -3, -8))} {_c(at(7, -1, 8))} air", _tp(), "give @p cobblestone 16"],
                       lambda ctx: _skill("bridge_toward")(ctx, at(9, 0, 0)), _at(at(9, 0, 0), 4), 45),
    "plant_wheat": (["plant_farm"], "grass, seeds, a hoe, a water bucket → a wheat plot growing",
                    _floor("grass_block") + [_tp(), "give @p wheat_seeds 2", "give @p stone_hoe", "give @p water_bucket"],
                    lambda ctx: _skill("plant_farm")(ctx), _blocks(at(-4, 0, -4), at(4, 0, 4), "wheat", 2), 30),
    "breed_cows": (["breed"], "two cows in a pen, wheat carried → wheat spent on them",
                   _floor("grass_block") + _pen("cow", 2) + [_tp(), "give @p wheat 4"],
                   lambda ctx: _skill("breed")(ctx), _mobs_near("minecraft:cow", 3), 45),
    "fill_bottles_at_pond": (["fill_bottles"], "a pond, 3 glass bottles → 3 water bottles",
                             _floor() + [f"fill {_c(at(2, -1, -1))} {_c(at(3, -1, 1))} water", _tp(),
                                         "give @p glass_bottle 3"], lambda ctx: _skill("fill_bottles")(ctx, 3),
                             _gain("minecraft:potion", 3), 30),
}


def _bench_machine(ctx, origin):
    """The arena's auto smelter as memory knows a built one (the dict `upkeep.ready_machine` hands the skill)."""
    name = ctx.mem.add_machine("auto_smelter", origin, 0, "minecraft:overworld", ["smelting"])
    return next(m for m in ctx.mem.machines("minecraft:overworld") if m["name"] == name)


def _broken_hut(ctx):
    """The hut in the arena as a remembered site whose snapshot is the whole wall (taken before it was broken)."""
    lo, hi = at(2, 0, -2), at(6, 2, 2)
    blocks = {f"{x},{y},{z}": "cobblestone" for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1)
              for z in range(lo[2], hi[2] + 1) if x in (lo[0], hi[0]) or y in (lo[1], hi[1]) or z in (lo[2], hi[2])}
    site = ctx.mem.add_site("shelter", at(4, 0, 0), "minecraft:overworld", name="bench-hut",
                            snapshot={"lo": list(lo), "hi": list(hi), "blocks": blocks})
    return site


for _name, (_skills, _doc, _setup, _run, _check, _budget) in _ONE.items():
    _target = TARGET_S[_skills[0]] * TARGET_SLACK if _skills[0] in TARGET_S else None
    SHEET[_name] = {"doc": _doc, "module": "skills", "setup": list(_setup), "before": _start(_name),
                    "run": _timed(_run) if _target else _run, "check": _check, "budget": _budget,
                    "skills": list(_skills), "point": "A", "tags": {"base": _skills[0]},
                    **({"target_s": _target} if _target else {})}

# Searching needs a world bigger than the box: these run on real terrain (raw), judged by what they found.
def _found_near(blocks, r=6):
    def check(api, inv):
        from .world import find
        return bool(find(blocks, radius=r, limit=1))
    return check
# Brewing is game time (20 s a stage): the stand's clock runs at 60 ticks/s, the hands at their own pace.
SHEET["brew_fire_resistance_stand"]["tick_rate"] = 60


# What a *_real row looks for, put 16 blocks off on the real ground: in scan range, not in arm's reach — the walk
# to it is the job, a minute of wandering until the terrain happens to offer one is not (30 s per row).
REAL_TARGET = {
    "seek_blocks_real": ["execute at @p run fill ~16 ~ ~ ~16 ~4 ~ oak_log"],
    "explore_for_animals_real": ["execute at @p run summon cow ~16 ~3 ~", "execute at @p run summon cow ~16 ~3 ~1"],
}
for _name, _row_ in {
    "seek_blocks_real": (["seek_blocks"], "real terrain, a log column 16 blocks off → walked to it",
                         lambda ctx: _skill("seek_blocks")(ctx, ["oak_log", "birch_log", "spruce_log"], 1, 20),
                         _found_near(["oak_log", "birch_log", "spruce_log"], 8), 30),
    "explore_for_animals_real": (["explore_for"], "real terrain, two cows 16 blocks off → found",
                                 lambda ctx: _skill("explore_for")(ctx, ["minecraft:cow", "minecraft:sheep",
                                                                         "minecraft:pig"], 1, 20),
                                 lambda api, inv: bool(__import__("bonobo.world", fromlist=["entities"]).entities(
                                     24, ["minecraft:cow", "minecraft:sheep", "minecraft:pig"])), 30),
    "strip_mine_real": (["strip_mine_step"], "real terrain, a stone pickaxe → a mining tunnel started",
                        lambda ctx: _skill("strip_mine_step")(ctx, 2),
                        _gain("minecraft:cobblestone", 2), 30),
}.items():
    _skills_, _doc_, _run_, _check_, _budget_ = _row_
    _deep = (["execute at @p run fill ~-1 17 ~-1 ~1 19 ~1 air", "execute at @p run tp @p ~ 17 ~"]
             if _name == "strip_mine_real" else [])     # at iron depth already: the tunnel, not a 50-block descent
    _deep += REAL_TARGET.get(_name, [])
    SHEET[_name] = {"doc": _doc_, "module": "skills", "raw": True, "release": True,
                    "setup": ["spreadplayers 14200 14200 0 4 false @p", "clear @p", "give @p stone_pickaxe",
                              "give @p torch 8", "give @p cobblestone 32", "give @p cooked_beef 8"] + _deep,
                    "before": _start(_name), "run": _run_, "check": _check_, "budget": _budget_,
                    "skills": list(_skills_), "point": "A", "tags": {"base": _skills_[0], "terrain": "real"},
                    **({"stochastic": True} if _name == "explore_for_animals_real" else {})}

SHEET["dead_flicker_on_respawn"] = {
    "doc": "Killed at the start of the run: /state reads dead for a moment while the respawn loads — the brain must "
           "respawn, not call every skill dead, and still chop its 4 logs",
    "module": "brain", "point": "A", "skills": ["item:log"], "tags": {"base": "chop", "surprise": "dead_flicker"},
    "setup": _grove((3, 0), (-3, 2)) + [_tp()],
    "before": _hooks(_start("dead_flicker_on_respawn"), lambda ctx: _chat("kill @p")),
    "run": lambda ctx: (_brain_rounds(15, lambda: not __import__("bonobo.api", fromlist=["get"]).get("/state")["dead"])(ctx),
                        _skill("chop")(ctx, 4))[1],
    "check": _all(_alive(10), lambda api, inv: inv.count("log") >= 4), "budget": 60,
}

# -- test point B: L0 hazards (the existing water_clutch, cross_lava_8, cave_escape) and two more ---------------
SHEET["lava_edge_walk"] = {
    "doc": "A 1-wide stone path between two lava pools to a target 12 blocks on → there, not burnt",
    "module": "nav", "point": "B", "skills": ["goto"], "tags": {"base": "nav", "hazard": "lava"},
    "setup": [f"fill {_c(at(-3, -3, -4))} {_c(at(15, -1, 4))} stone", f"fill {_c(at(0, -1, -3))} {_c(at(13, -1, -1))} lava",
              f"fill {_c(at(0, -1, 1))} {_c(at(13, -1, 3))} lava", _tp(-1, 0, 0), "give @p cobblestone 32"],
    "before": _start("lava_edge_walk"),
    "run": lambda ctx: _skill("travel_to")(ctx, at(14, 0, 0), 1.5),
    "check": _all(_at(at(14, 0, 0), 2.5), _alive(16)), "budget": 30,
}
SHEET["buried_by_sand"] = {
    "doc": "Sand dropped on the body mid-task → L0 rescues (unbury) through the brain's own round, then alive",
    "module": "brain", "point": "B", "skills": ["unbury"], "tags": {"base": "l0", "hazard": "suffocating"},
    "setup": _floor() + [_tp(), "give @p stone_pickaxe"],
    "before": _hooks(_start("buried_by_sand"),
                     lambda ctx: _chat(f"fill {_c(at(0, 0, 0))} {_c(at(0, 3, 0))} sand")),
    "run": _brain_rounds(15, _head_clear), "check": _all(lambda api, inv: _head_clear(), _alive(10)), "budget": 20,
}
def _on_rim(top):
    """Standing dry on the rim at `top` (the block under the feet is the rim, not water)."""
    def check(api, inv):
        s = api.get("/state")
        return s["onGround"] and not s["inWater"] and s["y"] >= at(0, top + 1, 0)[1] - 0.5
    return check


def _surfaced(top, hold_s=2.0):
    """Out of the water's grip: on the rim, or the air bar full with the head out of the water for `hold_s` (a
    single sample passed a body bobbing at the surface that went down again)."""
    def check(api, inv):
        if _on_rim(top)(api, inv):
            return True
        if not _breathing(300)(api, inv):
            return False
        time.sleep(hold_s)
        return _breathing(300)(api, inv) or _on_rim(top)(api, inv)
    return check


PIT_TOP = 4
SHEET["drowning_in_a_pit"] = {
    "doc": "Deep in a flooded shaft with little air → L0 surfaces (find_air / surface) before anything else: on the "
           "rim, or breathing with the head out for 2 s",
    "module": "brain", "point": "B", "skills": ["reach:air"], "tags": {"base": "l0", "hazard": "drowning"},
    # A 3×3 shaft 7 blocks deep, a stone rim one block above the water with air over it: somewhere to stand.
    "setup": _tank(-1, 1, -1, 1, PIT_TOP - 1, water_top=PIT_TOP - 1, wall="stone")
    + [f"fill {_c(at(-4, PIT_TOP, -4))} {_c(at(4, PIT_TOP, 4))} stone",
       f"fill {_c(at(-1, PIT_TOP, -1))} {_c(at(1, PIT_TOP, 1))} water",
       f"fill {_c(at(-4, PIT_TOP + 1, -4))} {_c(at(4, PIT_TOP + 3, 4))} air", _tp(0, -3, 0)],
    "before": _start("drowning_in_a_pit"),
    # The rounds run until exactly what the check judges (a looser stop passed the run and failed the row).
    "run": _brain_rounds(28, lambda: _surfaced(PIT_TOP, 0)(__import__("bonobo.api", fromlist=["get"]), None)),
    "check": _all(_alive(8), _surfaced(PIT_TOP)), "budget": 30,
}
SHEET["interrupted_rescue_is_not_a_failure"] = {
    "doc": "Chopping, then lava poured beside the body: the chop is interrupted (not failed), L0 moves away, the "
           "brain resumes and still gets its 4 logs",
    "module": "brain", "point": "B", "skills": ["item:log"], "tags": {"base": "chop", "hazard": "lava"},
    "setup": _grove((3, 0)) + [_tp(), "give @p cobblestone 16"],
    "before": _hooks(_start("interrupted_rescue_is_not_a_failure"),
                     lambda ctx: _threading.Timer(3.0, lambda: _chat(f"setblock {_c(at(0, 0, 1))} lava")).start()),
    "run": _resume("interrupted_rescue_is_not_a_failure", lambda ctx: _skill("chop")(ctx, 4),
                   _achieve_needs([("log", 4)])),
    "check": _all(_gain("log", 4), _alive(10)), "budget": 60,
}
for _name in ("water_clutch", "cross_lava_8", "cave_escape"):
    SCENARIOS[_name]["point"] = "B"

# -- CT3: fights on a walled platform, one per enemy line-up. The whole agent runs (perception decides, fight_loop
# answers, the brain yields); a row is judged by the world — alive, the enemies gone (or, for a neutral mob, left
# alone), health kept — and by the decision rhythm while engaged: no gap between two bids of the threat layer longer
# than 1.5 × FIGHT_POLL_S.
FIGHT_LOG = {"bids": []}


def _record_bids(ctx):
    """`before` hook: time every bid the threat layer makes during this row (the fight's decision clock)."""
    from . import fight_loop
    FIGHT_LOG["bids"] = []
    real = FIGHT_LOG.setdefault("real_bid", fight_loop.bid)

    def bid(*a, **k):
        FIGHT_LOG["bids"].append(time.time())
        return real(*a, **k)
    fight_loop.bid = bid


def _fight_until(kinds, seconds, clear=True):
    """Brain rounds (which yield while the fight holds the body) until the line-up is gone, or `seconds`."""
    def run(ctx):
        from . import fight_loop
        t0 = time.time()
        try:
            while time.time() - t0 < seconds:
                if clear and not _hostiles(24, set(kinds)):
                    return True
                core.BRAIN.round()
            return not clear or not _hostiles(24, set(kinds))
        finally:
            fight_loop.bid = FIGHT_LOG.get("real_bid", fight_loop.bid)
    return run


def _decision_gaps_ok(factor=1.5):
    def check(api, inv):
        from . import fight_loop
        t = FIGHT_LOG["bids"]
        gaps = [b - a for a, b in zip(t, t[1:])]
        return bool(t) and max(gaps, default=0.0) <= fight_loop.FIGHT_POLL_S * factor
    return check


def _gone(kinds):
    return lambda api, inv: not _hostiles(24, set(kinds))


def _hp_kept(least):
    return lambda api, inv: api.get("/state")["health"] >= least and not api.get("/state")["dead"]


# Walls of glass (the fight stays visible), a roof of stone: undead summoned under the sky burned to death before
# the row began ("0 zombie on the server").
_ARENA = [f"fill {_c(at(-9, -2, -9))} {_c(at(9, -1, 9))} stone", f"fill {_c(at(-9, 0, -9))} {_c(at(9, 4, 9))} glass hollow",
          f"fill {_c(at(-9, 4, -9))} {_c(at(9, 4, 9))} stone",
          f"fill {_c(at(-8, 0, -8))} {_c(at(8, 3, 8))} air", f"fill {_c(at(-9, -1, -9))} {_c(at(9, -1, 9))} stone", _tp(),
          "give @p iron_sword", "give @p stone_pickaxe",       # a pickaxe: upkeep's "no pickaxe" row stays quiet
          "item replace entity @p armor.chest with iron_chestplate",
          "item replace entity @p armor.head with iron_helmet", "give @p cooked_beef 16", "give @p cobblestone 64",
          "item replace entity @p weapon.offhand with shield"]
# (name, mob, how many, tier, seconds, health kept at least, cleared?) — cleared False: a neutral mob, left alone
RESOLVE_GAP, RESOLVE_HOLD_S, RESOLVE_HP_LOSS = 6.0, 5.0, 4.0


def _threat_resolved(kinds, gap=RESOLVE_GAP, hold_s=RESOLVE_HOLD_S, hp_loss=RESOLVE_HP_LOSS):
    """The threat is over: every one of `kinds` dead, or all at least `gap` blocks off and not closing in for
    `hold_s` seconds (watched now) — and health within `hp_loss` of where the row began."""
    def check(api, inv):
        from .world import feet
        start_hp = BASE["state"]["health"]
        if api.get("/state")["health"] < start_hp - hp_loss:
            return False

        def gaps():
            here = feet()
            return [math.dist(here, (e["x"], e["y"], e["z"])) for e in _hostiles(32, set(kinds))]
        first = gaps()
        if not first:
            return True
        time.sleep(hold_s)
        last = gaps()
        return (not last or (min(first) >= gap and min(last) >= gap and min(last) >= min(first) - 1.0)) and \
            api.get("/state")["health"] >= start_hp - hp_loss
    return check


FIGHT_CELLS = [
    ("fight_zombie_1", "zombie", 1, "common", 25, 12, True),
    ("fight_zombie_3", "zombie", 3, "exception", 25, 6, True),
    ("fight_skeleton_1", "skeleton", 1, "common", 25, 10, True),
    ("fight_creeper_1", "creeper", 1, "common", 25, 14, "resolved"),
    ("fight_blaze_3", "blaze", 3, "exception", 25, 6, True),
    ("fight_enderman_1", "enderman", 1, "exception", 25, 20, False),
]
for _name, _mob, _n, _tier, _secs, _hp, _clear in FIGHT_CELLS:
    _kinds = [f"minecraft:{_mob}"]
    # Close enough to be in the fight at once: the walk to a far corner is not what these rows measure.
    # A creeper starts outside its blast reach: at 4 it went off before the first decision.
    _spots = ([(7, 0, 0)] if _mob == "creeper" else [(4, 0, 0), (-3, 0, 3), (1, 0, -4)])[:_n]
    # Three blazes at full health outlast the 60 s limit on the approach alone: they start worn (the fight's
    # decisions are the same at 10 hp; the kill count, the health kept and the decision gaps are what is judged).
    _nbt = "{PersistenceRequired:1b,Health:10f}" if _mob == "blaze" and _n > 1 else "{PersistenceRequired:1b}"
    SHEET[_name] = {
        "doc": f"Walled platform, iron kit: {_n} {_mob} → " + {True: "all dead", False: "left alone (neutral)",
                                                              "resolved": "dead, or kept off and not following "
                                                                          f"for {RESOLVE_HOLD_S:.0f} s"}[_clear] +
               f", health ≥ {_hp}, a threat decision every ≤ 1.5 × FIGHT_POLL_S while engaged",
        "module": "fight_loop", "combat": True, "point": "B", "skills": [], "tier_fixed": _tier,
        "tags": {"base": "fight", "enemy": _mob, "count": _n},
        "setup": list(_ARENA) + [f"summon {_mob} {_c(at(x, y, z))} {_nbt}" for x, y, z in _spots],
        "expect_entities": [(f"minecraft:{_mob}", _n)],
        "before": _hooks(_start(_name), _record_bids),
        # "resolved": fight for the window, then the check watches RESOLVE_HOLD_S more — both inside the budget
        "run": _fight_until(_kinds, _secs - RESOLVE_HOLD_S - 2 if _clear == "resolved" else _secs - 2, _clear is not False),
        "check": _all(_threat_resolved(_kinds), _decision_gaps_ok()) if _clear == "resolved"
        else _all(_hp_kept(_hp), _gone(_kinds), _decision_gaps_ok()) if _clear
        else _all(_hp_kept(_hp), lambda api, inv, k=_kinds: bool(_hostiles(24, set(k)))),
        "budget": _secs + 5,
    }

CRYSTAL_AT = at(5, 6, 0)     # on a 6-high obsidian pillar, iron bars around it: one caged tower of the End
_cr = lambda dx, dy, dz: _c(at(5 + dx, 6 + dy, dz))   # noqa: E731  (a cell beside the crystal)


def _one_crystal(ctx):
    rows = __import__("bonobo.world", fromlist=["entities"]).entities(16, ["minecraft:end_crystal"])
    if not rows:
        raise SetupInvalid("no end crystal after setup")
    return rows[0]


def _crystals_left(api, inv):
    return __import__("bonobo.world", fromlist=["entities"]).entities(16, ["minecraft:end_crystal"])


# The dragon rows start with the crystals gone (WORN_DRAGON): breaking one is this row's job, on its own.
SCENARIOS["break_caged_crystal"] = {
    "doc": "A caged end crystal on a 6-high obsidian pillar, blocks + water bucket + sword → towered up, bars "
           "broken, crystal destroyed, alive.",
    "module": "end", "skills": ["break_caged_crystal"],
    "setup": _floor() + [f"fill {_cr(0, -6, 0)} {_cr(0, -1, 0)} obsidian",
                         f"fill {_cr(-1, 0, -1)} {_cr(1, 2, 1)} iron_bars hollow",
                         f"fill {_cr(0, 0, 0)} {_cr(0, 1, 0)} air",
                         f"summon end_crystal {_c(CRYSTAL_AT)} {{ShowBottom:0b}}",
                         _tp(), "give @p cobblestone 32", "give @p water_bucket", "give @p iron_sword",
                         "give @p stone_pickaxe"],
    "expect": [(at(5, 0, 0), at(5, 5, 0), "obsidian", 6, 6)],
    "expect_entities": [("minecraft:end_crystal", 1)],
    "run": lambda ctx: _skill("break_caged_crystal")(ctx, _one_crystal(ctx)),
    "check": lambda api, inv: not _crystals_left(api, inv) and not api.get("/state")["dead"],
    "budget": 60,
}


# A fight on a full bag: the drops cannot be picked up, and that must not change the fight (no pause to collect).
SHEET["fight_zombie_1_full_bag"] = dict(
    SHEET["fight_zombie_1"], doc="Walled platform, iron kit, the bag full of dirt: 1 zombie → dead, health ≥ 12, "
                                 "decisions as often as ever: the drop it cannot pick up changes nothing",
    before=_hooks(SHEET["fight_zombie_1"]["before"], _fill_bag(0)),
    tags={**SHEET["fight_zombie_1"]["tags"], "inventory": "full_bag"}, tier_fixed="exception")

# Tidying a full bag in the Nether, lava at feet level on one side: the junk is thrown the other way and lands.
NETHER_LAVA = [f"fill {_c(at(1, 0, -1))} {_c(at(4, 0, 1))} lava"]
SHEET["nether_full_bag"] = {
    "doc": "Nether platform, lava at feet level on the east, the bag full of netherrack and diamonds → tidied: slots "
           "freed, the netherrack thrown where it lands (on the ground, not in the lava), no diamond lost",
    "module": "skills", "point": "A", "skills": ["tidy_inventory"], "tier_fixed": "exception",
    "dimension": "minecraft:the_nether", "stochastic": False, "tags": {"base": "tidy", "inventory": "full_bag"},
    "setup": [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} netherrack", _tp()] + NETHER_LAVA
    + ["give @p diamond 640"],
    "before": _hooks(_start("nether_full_bag"), _fill_bag(0, "netherrack")),
    "run": lambda ctx: _skill("tidy_inventory")(ctx),
    "check": _all(_free_slots(3), _kept("minecraft:diamond"),
                  lambda api, inv: bool(__import__("bonobo.world", fromlist=["entities"]).entities(
                      10, ["minecraft:item"]))),
    "budget": 30,
}


# -- jar 0.1.39 gaps: placing by facing, boats, awkward start cells --------------------------------------------------
def _placed_facing(pos, facing):
    """The block at `pos` reports `facing` (None: a block with no facing at all, and it stands there)."""
    def check(api, inv):
        from .world import Region
        r = Region(pos, pos, props=True)
        return r.name(pos) != "air" and r.prop(pos, "facing") == facing
    return check


def _place_facing(item, pos, facing):
    def run(ctx):
        from .building import place_oriented
        return place_oriented(ctx, pos, item, facing) or True
    return run


PLACE_ROWS = [   # (name, item, facing asked, facing the block must report, tier)
    ("place_furnace_north", "minecraft:furnace", "north", "north", "common"),
    ("place_furnace_south", "minecraft:furnace", "south", "south", "common"),
    ("place_furnace_east", "minecraft:furnace", "east", "east", "common"),
    ("place_furnace_west", "minecraft:furnace", "west", "west", "common"),
    ("place_observer_up", "minecraft:observer", "up", "up", "exception"),
    ("place_observer_down", "minecraft:observer", "down", "down", "exception"),
    ("place_stairs_east", "minecraft:oak_stairs", "east", "east", "exception"),
    # A block without a facing property: the asked facing is ignored and the place still succeeds.
    ("place_cobblestone_facing_ignored", "minecraft:cobblestone", "north", None, "exception"),
]
for _name, _item, _asked, _want, _tier in PLACE_ROWS:
    _pos = at(3, 0, 0)
    SHEET[_name] = {
        "doc": f"Place {_item.split(':')[1]} asking facing={_asked} (the jar turns the body by the block's own rule) "
               f"→ the block reports facing={_want}",
        "module": "building", "point": "A", "skills": [], "tier_fixed": _tier, "tags": {"base": "place"},
        "variant": (_item, _asked),
        "setup": _floor() + [_tp(), f"give @p {_item.split(':')[1]} 2"],
        "before": _start(_name), "run": _place_facing(_item, _pos, _asked),
        "check": _placed_facing(_pos, _want), "budget": 20,
    }

SHEET["boat_across_the_lake"] = {
    "doc": "A 6-block lake between two shores, a boat carried → across to the far shore (the jar's BoatDriver)",
    "module": "nav", "point": "A", "skills": ["goto"], "tier_fixed": "exception", "tags": {"base": "nav", "terrain": "lake"},
    "setup": [f"fill {_c(at(-4, -4, -6))} {_c(at(12, -4, 6))} stone",
              f"fill {_c(at(-4, -3, -6))} {_c(at(-1, -1, 6))} stone", f"fill {_c(at(6, -3, -6))} {_c(at(12, -1, 6))} stone",
              f"fill {_c(at(0, -3, -6))} {_c(at(5, -1, 6))} water", _tp(-2, 0, 0), "give @p oak_boat"],
    "before": _start("boat_across_the_lake"),
    "run": lambda ctx: _skill("travel_to")(ctx, at(7, 0, 0), 2),
    "check": _all(_at(at(7, 0, 0), 3), lambda api, inv: not api.get("/state")["inWater"]), "budget": 30,
}

def _queue(goal):
    """`before` hook: put a task at the head of the queue, as L3 would."""
    def hook(ctx):
        from . import tasks
        tasks.add(goal, front=True, source="bench")
    return hook


# -- where things come from (decompose.SOURCES): the plan, not the skill, is under test ---------------------------
# A frame at at(-3, 0, 2), along x (blueprints.NETHER_PORTAL, turns 0): obsidian but for the bottom two cells,
# stone corners. The cast resumes it (building.started_builds): two cells, then the light.
PORTAL_8_OF_10 = [f"fill {_c(at(-3, 0, 2))} {_c(at(0, 4, 2))} obsidian",
                  f"fill {_c(at(-2, 0, 2))} {_c(at(-1, 3, 2))} air"] + \
                 [f"setblock {_c(at(x, y, 2))} cobblestone" for x in (-3, 0) for y in (0, 4)]
SHEET["portal_from_cast"] = {
    "doc": "The queue asks for a portal: no obsidian carried, no diamond pickaxe, buckets, blocks and flint, a frame "
           "standing 8 of 10 (its bottom two missing), a lava pool memory knows 3 blocks off → the plan casts the two "
           "in place and lights it, the cast itself ≤ 5 s",
    "module": "decompose", "point": "C", "skills": ["cast:nether_portal"], "tier_fixed": "exception",
    "tags": {"base": "sources"},
    # Both lava buckets carried: the two trips to the pool are not the cast.
    "setup": list(SCENARIOS["cast_portal"]["setup"]) + PORTAL_8_OF_10 + ["give @p lava_bucket", "give @p lava_bucket"],
    "expect": [SCENARIOS["cast_portal"]["expect"][0], (at(-3, 0, 2), at(0, 4, 2), "obsidian", 8, 8)],
    "before": _hooks(_start("portal_from_cast"), _forget_skill_time("cast_portal"),
                     lambda ctx: ctx.mem.note_seen("lava", at(3, -1, 0), "minecraft:overworld"),
                     _queue(__import__("bonobo.goals", fromlist=["make"]).make("build", bp="nether_portal"))),
    "run": _brain_rounds(28, lambda: _count_blocks(None, at(-8, -1, -8), at(8, 6, 8), "nether_portal") >= 1),
    "check": _all(lambda api, inv: _count_blocks(api, at(-8, -1, -8), at(8, 6, 8), "nether_portal") >= 1,
                  _skill_within("cast_portal", 5.0)),
    "budget": 30,
}
SHEET["pearls_from_barter"] = {
    "doc": "In the Nether, 2 gold ingots and a gold helmet, piglins 4 blocks off, no enderman → the plan barters",
    "module": "decompose", "point": "C", "skills": ["barter"], "tier_fixed": "exception", "tags": {"base": "sources"},
    "dimension": "minecraft:the_nether", "combat": True, "tick_rate": 60,
    "setup": list(SCENARIOS["barter_piglin"]["setup"]),
    "expect": list(SCENARIOS["barter_piglin"]["expect"]),
    "expect_entities": [("minecraft:piglin", 3)],
    "before": _start("pearls_from_barter"),
    "run": _achieve_needs([("minecraft:ender_pearl", 1)], rounds=4),
    # A barter's pearls are chance: what is proven is that the plan chose to trade and the gold went.
    "check": lambda api, inv: inv.count("minecraft:gold_ingot") < 2 and _trades(inv) >= 1, "budget": 30,
}

# -- the producers the skills' `gives` added: a farm grows wheat (→ bread), a villager buys for emeralds, the
# plan fills a bucket. Built with the console (a ripe-able plot's soil, a villager with exactly one offer), judged
# by what the bag gained.
def _villager(pos, buy, n_buy, sell, n_sell, profession="farmer"):
    """A villager that stays put (NoAI) with one offer: `n_buy` of `buy` → `n_sell` of `sell`."""
    return (f'summon villager {_c(pos)} {{NoAI:1b,VillagerData:{{profession:"minecraft:{profession}",level:2,'
            f'type:"minecraft:plains"}},Offers:{{Recipes:[{{buy:{{id:"minecraft:{buy}",count:{n_buy}}},'
            f'sell:{{id:"minecraft:{sell}",count:{n_sell}}},maxUses:12}}]}}}}')


FARM_KIT = ["give @p diamond_hoe", "give @p wheat_seeds 8", "give @p water_bucket"]
FARM_TICK_SPEED = 1000      # random ticks per chunk section: a sown crop ripens within seconds (the bench keeps 0)
RIPE_PLOT = [f"fill {_c(at(4, -1, -1))} {_c(at(6, -1, 1))} farmland", f"fill {_c(at(4, 0, -1))} {_c(at(6, 0, 1))} wheat[age=7]"]


def _growing(run):
    """The run with crops growing fast (`gamerule random_tick_speed`), put back to the bench's 0 however it ends —
    the plan's own plot ripens within the row, its planting and harvest still the plan's."""
    def go(ctx):
        _checked(f"execute in minecraft:overworld run gamerule random_tick_speed {FARM_TICK_SPEED}", [])
        try:
            return run(ctx)
        finally:
            _checked("execute in minecraft:overworld run gamerule random_tick_speed 0", [])
    return go


for _name, _doc, _setup, _run, _check in [
        ("bread_from_a_farm", "grass, a hoe, 8 seeds, a water bucket, nothing else → the plan plants a plot (its farm "
         "step), the crop grows (random ticks fast), harvested, bread baked",
         _floor("grass_block") + [_tp()] + FARM_KIT, _growing(_achieve_needs([("minecraft:bread", 1)], rounds=6)),
         _all(_gain("minecraft:bread", 1), _blocks(at(-4, -1, -4), at(4, -1, 4), "farmland", 1))),
        ("bread_from_a_farm_two_wheat_carried", "2 wheat carried, a ripe plot beside the body → the plan harvests "
         "it (no new plot sown) and bakes: bread",
         _floor("grass_block") + RIPE_PLOT + [_tp(), "give @p wheat 2"] + FARM_KIT,
         # the plan harvests the crop already grown (planner: ripe before sowing), then bakes
         _achieve_needs([("minecraft:bread", 1)], rounds=4),
         _all(_gain("minecraft:bread", 1), _blocks(at(4, 0, -1), at(6, 0, 1), "wheat", 0, 8),
              _blocks(at(-8, -1, -8), at(3, -1, 8), "farmland", 0, 0))),        # no plot sown beside it
        ("bread_from_a_farm_no_soil", "stone floor, the same kit → no plot can be made: the plan fails naming the soil "
         "(must fail, never a hang)",
         _floor() + [_tp()] + FARM_KIT,
         _expect_failure("bread_from_a_farm_no_soil", _achieve_needs([("minecraft:bread", 1)], rounds=3),
                         r"soil|no flat|farm"), _same_bag())]:
    SHEET[_name] = {"doc": _doc, "module": "decompose", "point": "C", "skills": ["farm"], "tier_fixed": "exception",
                    "tags": {"base": "sources", "source": "farm"}, "setup": list(_setup),
                    "before": _start(_name), "run": _run, "check": _check, "budget": 30}

TRADER = at(3, 0, 0)
for _name, _doc, _setup, _run, _check in [
        ("emerald_from_a_villager", "a farmer buying 20 wheat for an emerald, 20 wheat carried → the plan sells: an "
         "emerald gained, the wheat gone",
         _floor() + [_tp(), "give @p wheat 20", _villager(TRADER, "wheat", 20, "emerald", 1)],
         _achieve_needs([("minecraft:emerald", 1)], rounds=3),
         _all(_gain("minecraft:emerald", 1), lambda api, inv: inv.count("minecraft:wheat") == 0)),
        ("emerald_villager_without_the_trade", "a villager who only sells bread → no emerald offer: the trade ends "
         "naming it (must fail, never a hang)",
         _floor() + [_tp(), "give @p wheat 20", _villager(TRADER, "emerald", 1, "bread", 6)],
         _expect_failure("emerald_villager_without_the_trade", lambda ctx: _skill("trade")(ctx, "minecraft:emerald"),
                         r"no affordable|emerald trade"), _same_bag()),
        ("emerald_no_villager", "no villager anywhere, wheat carried → the trade fails naming it (must fail)",
         _floor() + [_tp(), "give @p wheat 20"],
         _expect_failure("emerald_no_villager", lambda ctx: _skill("trade")(ctx, "minecraft:emerald"),
                         r"no villager"), _same_bag())]:
    SHEET[_name] = {"doc": _doc, "module": "decompose", "point": "C", "skills": ["trade"], "tier_fixed": "exception",
                    "tags": {"base": "sources", "source": "trade"}, "setup": list(_setup),
                    "before": _start(_name), "run": _run, "check": _check, "budget": 30}

SHEET["water_bucket_from_the_plan"] = {
    "doc": "An empty bucket, a pond 3 blocks off → the plan fills it (the fill producer): a water bucket held",
    "module": "decompose", "point": "C", "skills": ["fill"], "tier_fixed": "exception",
    "tags": {"base": "sources", "source": "fill"},
    "setup": _floor() + [f"fill {_c(at(3, -1, -1))} {_c(at(4, -1, 1))} water", _tp(), "give @p bucket"],
    "before": _start("water_bucket_from_the_plan"),
    "run": _achieve_needs([("minecraft:water_bucket", 1)], rounds=3),
    "check": _gain("minecraft:water_bucket", 1), "budget": 30,
}

SHEET["bucket_before_the_shaft"] = {
    "doc": "An empty bucket, water 2 blocks off, the queue's head needs iron (dug down to) → the bucket is filled "
           "before any digging (WaterClutch needs it in hand)",
    "module": "needs", "point": "C", "skills": ["fill"], "tier_fixed": "exception", "tags": {"base": "upkeep"},
    "setup": _floor(depth=4) + [f"setblock {_c(at(2, -1, 0))} water", _tp(), "clear @p", "give @p bucket",
                                "give @p stone_pickaxe", "give @p cooked_beef 8", "give @p white_bed"],
    "before": _hooks(_start("bucket_before_the_shaft"), _queue(__import__("bonobo.goals", fromlist=["have"]).have(
        ("minecraft:raw_iron", 1)))),
    "run": _brain_rounds(22, lambda: _inv_now().count("minecraft:water_bucket") >= 1),
    # Filled, and not a block dug down yet: the feet are still on the floor they started on.
    "check": _all(lambda api, inv: inv.count("minecraft:water_bucket") >= 1,
                  lambda api, inv: api.get("/state")["blockY"] >= at(0, 0, 0)[1]), "budget": 60,
}

SHEET["night_mines_under_cover"] = {
    "doc": "Night, sealed in a 1×2 hole 9 below the platform, a stone pickaxe, iron ore 3 below and 2 aside, nothing "
           "queued → the night's work is ore dug down to (brain.night_pick → descend), not waiting for day and not "
           "a climb to the surface",
    "module": "brain", "point": "C", "skills": ["mine"], "tier_fixed": "exception", "tags": {"base": "night"},
    "setup": [f"fill {_c(at(-6, -16, -6))} {_c(at(6, -1, 6))} stone",
              f"fill {_c(at(0, -9, 0))} {_c(at(0, -8, 0))} air",
              f"fill {_c(at(1, -13, 1))} {_c(at(2, -12, 2))} iron_ore",
              f"tp @p {_c(at(0.5, -9, 0.5))}", "clear @p", "give @p stone_pickaxe", "give @p cooked_beef 8",
              "give @p cobblestone 16", "time set 13000"],
    "expect": [(at(1, -13, 1), at(2, -12, 2), "iron_ore", 8, 8)],
    "before": _start("night_mines_under_cover"),
    "run": _brain_rounds(30, lambda: _inv_now().count("minecraft:raw_iron") >= 1),
    "check": _all(lambda api, inv: inv.count("minecraft:raw_iron") >= 1,
                  lambda api, inv: api.get("/state")["blockY"] < at(0, 0, 0)[1] - 2), "budget": 30,
}

SHEET["craft_chain_one_sitting"] = {
    "doc": "3 logs and a table carried → planks, sticks and a wooden pickaxe crafted in one sitting (craft_chain: the "
           "table placed and taken back once, not per recipe)",
    "module": "skills", "point": "A", "skills": ["craft_chain"], "tier_fixed": "exception",
    "tags": {"base": "craft"},
    "setup": _floor() + [_tp(), "clear @p", "give @p oak_log 3", "give @p crafting_table"],
    "before": _start("craft_chain_one_sitting"),
    "run": lambda ctx: _skill("craft_chain")(ctx, [("planks", 2), ("minecraft:stick", 1),
                                                   ("minecraft:wooden_pickaxe", 1)]),
    "check": _gain("minecraft:wooden_pickaxe", 1, at_most=1), "budget": 15,
}

START_ROWS = [   # (name, what the start cell is, setup commands after the floor, where the body starts)
    ("nav_from_stairs", "a stair step", [f"setblock {_c(at(0, 0, 0))} oak_stairs[facing=east]"], (0, 0.5, 0)),
    ("nav_from_slab", "a bottom slab", [f"setblock {_c(at(0, 0, 0))} stone_slab"], (0, 0.5, 0)),
    ("nav_from_farmland", "farmland", [f"setblock {_c(at(0, -1, 0))} farmland"], (0, 0, 0)),
    ("nav_from_ladder", "a ladder on a wall",
     [f"fill {_c(at(1, 0, 0))} {_c(at(1, 3, 0))} stone", f"fill {_c(at(0, 0, 0))} {_c(at(0, 3, 0))} ladder[facing=west]"],
     (0, 2, 0)),
    ("nav_from_water", "a pool one deep", [f"fill {_c(at(-1, 0, -1))} {_c(at(1, 0, 1))} water"], (0, 0, 0)),
]
for _name, _what, _start_cmds, (_dx, _dy, _dz) in START_ROWS:
    SHEET[_name] = {
        "doc": f"Walk 10 blocks starting on {_what} (GotoTask's start cell: '1 positions explored' reproduces here) "
               f"→ at the target",
        "module": "nav", "point": "A", "skills": ["goto"], "tier_fixed": "common",
        "tags": {"base": "nav", "start": _what},
        "setup": _floor() + [f"fill {_c(at(8, -3, -3))} {_c(at(12, -1, 3))} stone"] + list(_start_cmds)
                 + [_tp(_dx, _dy, _dz)],
        "before": _start(_name), "run": lambda ctx: _skill("travel_to")(ctx, at(10, 0, 0), 2),
        "check": _at(at(10, 0, 0), 3.5), "budget": 30,
    }

# -- tier "brain": the cerebellum's decisions (upkeep before the queue, repair, bans, resume, memory, the L3 queue).
# Every row runs the whole brain (`_slice`: brain.round on a private task queue), with the world set to the moment
# that matters — dusk, hunger, a tool on its last use — and is judged by the world and by the brain's own log.
BRAIN_LOG = {"replans": 0}


def _log_lines():
    return list(LAST_LINES)


def _log_order(first, then):
    """`first` appears in the brain's log before `then` (both must appear)."""
    def check(api, inv):
        lines = _log_lines()
        a = next((i for i, l in enumerate(lines) if first in l), None)
        b = next((i for i, l in enumerate(lines) if then in l), None)
        return a is not None and b is not None and a < b
    return check


FIRST = {}      # token → the run second it first showed in the bag (a watcher thread, `_first_times`)


def _furnace_lit(radius=16):
    """A furnace within `radius` is lit (its block state), read from the world."""
    from .world import Region, find
    for h in find(["furnace"], radius=radius, limit=8):
        p = (h["x"], h["y"], h["z"])
        if str(Region(p, p, props=True).prop(p, "lit")).lower() == "true":
            return True
    return False


def _first_times(ctx):
    """`before` hook: watch the bag during the run and note when each token first shows more than the row began
    with — the order of the brain's decisions, read from the world, not from its log."""
    FIRST.clear()
    t0 = time.time()

    def watch():
        from .world import Inventory
        while time.time() - t0 < 70:
            try:
                inv = Inventory()
            except Exception:
                time.sleep(0.5)
                continue
            for s_ in inv.slots:
                for tok in (s_["id"], s_["id"].rsplit("_", 1)[-1]):     # "minecraft:white_bed" → also "bed"
                    if tok not in FIRST and inv.count(tok) > _base_count(tok):
                        FIRST[tok] = time.time() - t0
            # Something cooking, read from the world: raw beef gone from the bag ("-beef") and a furnace lit near us
            # ("furnace_lit", its block state). The jar cannot read a furnace's slots without opening it (/container
            # is the open screen only), so the lit state stands for its contents: the only smeltable in the kit.
            try:
                if "-beef" not in FIRST and inv.count("minecraft:beef") < _base_count("minecraft:beef"):
                    FIRST["-beef"] = time.time() - t0
                if "furnace_lit" not in FIRST and _furnace_lit():
                    FIRST["furnace_lit"] = time.time() - t0
            except Exception:
                pass
            time.sleep(0.5)
    _threading.Thread(target=watch, daemon=True).start()


def _before_in_bag(first, then, or_never=False):
    """`first` appeared in the bag before `then` did (with `or_never`: or `then` never did)."""
    def check(api, inv):
        a, b = FIRST.get(first), FIRST.get(then)
        if b is None:
            return or_never and a is not None
        return a is not None and a < b
    return check


def _log_lacks(text):
    return lambda api, inv: not any(text in l for l in _log_lines())


def _count_replans(ctx):
    """`before` hook: count the brain's plans for the row (brain.replan), the repair measure."""
    from . import brain
    BRAIN_LOG["replans"] = 0
    real = BRAIN_LOG.setdefault("real_replan", brain.replan)

    def replan(*a, **k):
        BRAIN_LOG["replans"] += 1
        return real(*a, **k)
    brain.replan = replan


def _replans_at_most(n):
    def check(api, inv):
        from . import brain
        brain.replan = BRAIN_LOG.get("real_replan", brain.replan)
        return 1 <= BRAIN_LOG["replans"] <= n
    return check


def _goal(template, **kw):
    return __import__("bonobo.goals", fromlist=["goals"]).make(template, **kw)


def _have(*needs):
    return __import__("bonobo.goals", fromlist=["goals"]).have(*needs)


def _count(token, n):
    return lambda: _inv_now().count(token) - _base_count(token) >= n


def _remove_table_when_placed(ctx):
    """`before` hook: the moment the plan's crafting table stands in the world, take it away (the plan must repair
    that one step, not start over)."""
    def watch():
        from .world import find
        t0 = time.time()
        while time.time() - t0 < 60:
            try:
                hit = find(["crafting_table"], radius=6, limit=1)
            except Exception:
                hit = []
            if hit:
                h = hit[0]
                _chat(f"setblock {h['x']} {h['y']} {h['z']} air")
                return
            time.sleep(0.2)
    _threading.Thread(target=watch, daemon=True).start()


def _banned(pos):
    return lambda api, inv: core.BRAIN.blacklist.get(tuple(pos), 0) > time.time()


def _not_banned(pos):
    return lambda api, inv: core.BRAIN.blacklist.get(tuple(pos), 0) <= time.time()


def _clear_bans(ctx):
    core.BRAIN.blacklist.clear()


def _seen(kind, pos):
    def before(ctx):
        core.BRAIN.mem.note_seen(kind, pos, "minecraft:overworld")
    return before


def _not_remembered(kind):
    return lambda api, inv: core.BRAIN.mem.seen(kind, "minecraft:overworld") == []


def _forget_all(kind):
    def before(ctx):
        for r in core.BRAIN.mem.seen(kind, "minecraft:overworld"):
            core.BRAIN.mem.forget_seen(kind, r["pos"], "minecraft:overworld")
    return before


_PEN = lambda mob, n: _pen(mob, n, half=6)   # noqa: E731
_ARENA_B = [f"fill {_c(at(-8, -2, -8))} {_c(at(8, -1, 8))} grass_block", "clear @p"]
IRON_ORE_FREE, IRON_ORE_CAGED = at(4, 0, 0), at(-4, 0, 0)
BRAIN_ROWS = {   # (doc, setup, queue, done, minutes, check): every row ≤ 1 min, the world built up to the decision
    "plan_repair_on_event": (
        "Planks + cobblestone + a wooden pickaxe carried (upkeep quiet), a stone pickaxe asked; the table the plan puts down is taken away → that step "
        "is redone, the plan is not started over (≤ 2 plans)",
        _floor() + [_tp(), "give @p oak_planks 12", "give @p cobblestone 3", "give @p wooden_pickaxe"],
        [_have(("tool", "pickaxe", 1))], lambda: _inv_now().count("minecraft:stone_pickaxe") >= 1, 0.75,
        _all(lambda api, inv: inv.count("minecraft:stone_pickaxe") >= 1, _replans_at_most(2))),
    "plan_without_events": (
        "The same with nothing taken away → one plan (control)",
        _floor() + [_tp(), "give @p oak_planks 12", "give @p cobblestone 3", "give @p wooden_pickaxe"],
        [_have(("tool", "pickaxe", 1))], lambda: _inv_now().count("minecraft:stone_pickaxe") >= 1, 0.5,
        _all(lambda api, inv: inv.count("minecraft:stone_pickaxe") >= 1, _replans_at_most(1))),
    "ban_then_other_source": (
        "Two iron ores, one sealed in barrier → that cell is banned, the other is mined",
        _floor() + [f"fill {_c(at(-5, -1, -1))} {_c(at(-3, 1, 1))} barrier", f"setblock {_c(IRON_ORE_CAGED)} iron_ore",
                    f"setblock {_c(IRON_ORE_FREE)} iron_ore", _tp(), "give @p stone_pickaxe"],
        [_have(("minecraft:raw_iron", 1))], _count("minecraft:raw_iron", 1), 1,
        _all(_gain("minecraft:raw_iron", 1), _blocks(IRON_ORE_CAGED, IRON_ORE_CAGED, "iron_ore", 1, 1),
             _blocks(IRON_ORE_FREE, IRON_ORE_FREE, "iron_ore", 0, 0))),
    "ban_needs_a_failure": (
        "Two free iron ores, the walk interrupted once (a zombie) → nothing banned: an interruption teaches nothing "
        "about the place (control)",
        _floor() + [f"setblock {_c(IRON_ORE_CAGED)} iron_ore", f"setblock {_c(IRON_ORE_FREE)} iron_ore", _tp(),
                    "give @p stone_pickaxe", "give @p iron_sword"],
        [_have(("minecraft:raw_iron", 2))], _count("minecraft:raw_iron", 2), 1,
        _all(_gain("minecraft:raw_iron", 2), _not_banned(IRON_ORE_CAGED), _not_banned(IRON_ORE_FREE))),
    "resume_after_combat": (
        # 4 logs held at the end, 2 of them given (console-built to fit 30 s: the goal kept, the best axe carried),
        # a zombie summoned right beside the body mid-way.
        "4 logs wanted, 2 carried, the best axe; a zombie summoned beside it mid-way → fight_loop answers it, then "
        "the chopping resumes for what is still missing",
        _grove((3, 0)) + [_tp(), "give @p iron_sword", "give @p diamond_axe", "give @p oak_log 2",
                          "item replace entity @p armor.chest with iron_chestplate"],
        [_have(("log", 4))], lambda: _inv_now().count("log") >= 4, 1,
        _all(lambda api, inv: 4 <= inv.count("log") <= 7, _gone(["minecraft:zombie"]), _alive(10))),
    "chop_without_interrupt": (
        "The same with no zombie → no fight is logged, the same 4 logs (control)",
        _grove((3, 0)) + [_tp(), "give @p iron_sword", "give @p diamond_axe", "give @p oak_log 2"],
        [_have(("log", 4))], lambda: _inv_now().count("log") >= 4, 0.75,
        _all(lambda api, inv: inv.count("log") >= 4, _hp_kept(20), _gone(["minecraft:zombie"]))),
    "l3_two_goals_in_order": (
        "Two goals queued (logs, then cobblestone) → both done, in queue order",
        _grove((3, 0)) + [f"fill {_c(at(-3, 0, 2))} {_c(at(-2, 1, 3))} stone", _tp(), "give @p wooden_pickaxe"],
        [_have(("log", 2)), _have(("minecraft:cobblestone", 2))],
        lambda: _count("log", 2)() and _count("minecraft:cobblestone", 2)(), 1,
        _all(_before_in_bag("log", "minecraft:cobblestone"), _gain("log", 2), _gain("minecraft:cobblestone", 2))),
    "l3_order_swapped": (
        "The same goals queued the other way → done the other way (control: the queue decides, not the cost)",
        _grove((3, 0)) + [f"fill {_c(at(-3, 0, 2))} {_c(at(-2, 1, 3))} stone", _tp(), "give @p wooden_pickaxe"],
        [_have(("minecraft:cobblestone", 2)), _have(("log", 2))],
        lambda: _count("log", 2)() and _count("minecraft:cobblestone", 2)(), 1,
        _all(_before_in_bag("minecraft:cobblestone", "log"), _gain("log", 2), _gain("minecraft:cobblestone", 2))),
}
_BEFORE = {"plan_repair_on_event": [_count_replans, _remove_table_when_placed],
           "plan_without_events": [_count_replans],
           # 1.5 s in: with 2 logs to chop, a zombie at 4 s came after the work was done — nothing to resume.
           "resume_after_combat": [lambda ctx: _threading.Timer(1.5, lambda: _chat(
               f"summon zombie {_c(at(1, 0, 1))} {{PersistenceRequired:1b}}")).start()],
           "ban_then_other_source": [_clear_bans],
           # the order the goals were met, read from the bag (the slice ends the moment both are held: the second
           # "task done" line was never written)
           "l3_two_goals_in_order": [_first_times], "l3_order_swapped": [_first_times],
           "ban_needs_a_failure": [_clear_bans, lambda ctx: _threading.Timer(2.0, lambda: _chat(
               f"summon zombie {_c(at(3, 0, 3))} {{PersistenceRequired:1b}}")).start()],
           }
for _name, (_doc, _setup, _queue, _done, _minutes, _check) in BRAIN_ROWS.items():
    SHEET[_name] = {
        "doc": _doc, "module": "brain", "point": "C", "skills": [], "tier_fixed": "brain",
        "combat": _name == "resume_after_combat", "tags": {"base": "brain"},
        "setup": list(_setup), "before": _hooks(_start(_name), *_BEFORE.get(_name, [])), "queue": list(_queue),
        "variant": [getattr(h, "__name__", "?") for h in _BEFORE.get(_name, [])],     # what happens to the world
        "run": _slice(_done, min(_minutes, 0.4), queue=_queue), "check": _all(_check, _slice_check(None)),
        "budget": 30,
    }

# -- the brain's decisions as a grid: one world, the moment set by dimensions, one rule table ------------------------
# The world holds everything each decision could reach for (a grove, a stone field with iron and a diamond, bed and
# pickaxe materials, meat and a lit furnace); the dimensions set the moment. Cells come from the fight sheet's own
# walker (`_cells`), four families of ≥ 4 cells, each with its boundary and its must-not.
# Sealed in stone (known only if noted); the surface one in a small stone pod beside the start, not across the field:
# three goals in one slice fit 30 s only with the ore at hand (console-built, the goal kept).
DIAMOND_UP, DIAMOND_DOWN = at(2, 0, -2), at(4, -9, 0)
POCKET = at(0, -9, 0)
LOW_FOOD, LOW_FOOD_MAX_S = 10, 20     # drained until food ≤ 10, then the hunger cleared (closed loop: 4 s left 14,
                                      # 5 and 8 s left 0 and the raw beef was eaten starving)
# Hunger at 255 drains ~6 points a second: a poll every 0.1 s over HTTP overshot to 0. At 60 it is ~1.5 a second,
# slow enough to stop on the point.
LOW_FOOD_AMP = 60


def _drain_to(level, max_s=LOW_FOOD_MAX_S):
    """`before` hook: wait while setup's hunger drains the bar, and clear it the moment food ≤ `level`."""
    def hook(ctx):
        from . import api
        t0 = time.time()
        while time.time() - t0 < max_s and api.get("/state").get("food", 20) > level:
            time.sleep(0.1)
        _chat("effect clear @p minecraft:hunger")
        time.sleep(1.0)                   # what exhaustion was left takes its last point, if any
        food = api.get("/state").get("food", 20)
        BASE["food_drained"] = food
        from .reflexes import EAT_BELOW, STARVE
        if not STARVE < food < EAT_BELOW:
            raise SetupInvalid(f"food {food} after the drain: wanted between {STARVE} and {EAT_BELOW}")
    return hook
BRAIN_DIMS = {
    # "tight": dusk inside the bed's lead (needs.due_now: dusk_s < plan_s × LEAD; the bed from the kit is ~3 s × 1.5).
    # At 11800 dusk was 10 s off: not yet due, the 6 s log task came first and the bed after it (brain__tight).
    "dusk": {"plenty": ["time set 1000"], "tight": ["time set 11930"], "night": ["time set 18000"]},
    # Drained by the run's start to below EAT_BELOW (14): 4 s left the bar at exactly 14, and "food < 14" never held.
    "food": {"full": [], "low": [f"effect give @p minecraft:hunger {LOW_FOOD_MAX_S} {LOW_FOOD_AMP} true"]},
    "tool": {"fresh": ["give @p iron_pickaxe"], "one_use": ["give @p iron_pickaxe[damage=249]"]},
    "head": {"surface": [_tp()], "underground": [_tp(0.5, -9, 0.5)]},
    "seen": {"none": [], "noted": []},                  # a memory note, set by the `before` hook
    "bag": {"room": [], "one_slot": [], "junk_full": [], "valuables_full": []},     # filled by the `before` hook
}
BRAIN_BASE = {"dusk": "plenty", "food": "full", "tool": "fresh", "head": "surface", "seen": "none", "bag": "room"}
BAG_FILL = {"room": None, "one_slot": (1, "dirt"), "junk_full": (0, "dirt"), "valuables_full": (0, "diamond")}
KIT_COBBLE = 16         # the brain rows' kit: a goal of cobblestone must ask for more than this, or it is met at once
BRAIN_WORLD = (_ARENA_B + [f"fill {_c(at(-8, -12, -8))} {_c(at(8, -3, 8))} stone"] + _grove((3, 3))
               + [f"fill {_c(POCKET)} {_c(at(0, -8, 0))} air",
                  f"fill {_c(at(1, -11, 1))} {_c(at(2, -10, 2))} iron_ore",
                  f"fill {_c(at(5, 0, -1))} {_c(at(7, 2, 1))} stone", f"fill {_c(at(1, 0, -3))} {_c(at(3, 2, -1))} stone",
                  f"setblock {_c(DIAMOND_UP)} diamond_ore",
                  f"setblock {_c(DIAMOND_DOWN)} diamond_ore", f"setblock {_c(at(-2, 0, 0))} furnace",
                  "give @p white_wool 3", "give @p oak_planks 8", "give @p crafting_table", "give @p stick 4",
                  "give @p iron_ingot 3", "give @p beef 2", "give @p coal 2", f"give @p cobblestone {KIT_COBBLE}",
                  "give @p diamond_axe"])       # the best axe: logs are not the tool test here (kit rule)


def _diamond_of(cell):
    return DIAMOND_DOWN if cell["head"] == "underground" else DIAMOND_UP


# family → (grid, the queue, what each cell must show). A rule answers (check, why) from the cell; the checks read
# the world (bag, blocks, clock, height) and the order things appeared in the bag (`_first_times`), never log text.
def _bed_then_log(cell):
    if cell["dusk"] == "plenty" and cell["food"] == "full":
        return (_all(_before_in_bag("log", "bed", or_never=True), _gain("log", 2),
                     lambda api, inv: inv.count("bed") == 0),
                "a day ahead: the task first, no bed made (must not)")
    if cell["food"] == "low":
        # Food first, judged from the world: the raw beef out of the bag and a furnace lit, both before any log was
        # gained — or cooked beef in the bag first — and the bar no lower at the end than the drain left it. Logs
        # first with nothing cooking fails.
        food_first = lambda api, inv: ((_before_in_bag("-beef", "log")(api, inv)                   # noqa: E731
                                        and _before_in_bag("furnace_lit", "log")(api, inv))
                                       or _before_in_bag("minecraft:cooked_beef", "log")(api, inv))
        kept = lambda api, inv: api.get("/state")["food"] >= BASE.get("food_drained", 0)        # noqa: E731
        return _all(food_first, kept), "hungry: food before the task (cooking it counts)"
    return _before_in_bag("bed", "log"), "dusk or night on the surface, no bed: the night first"


def _tool_rule(cell):
    if cell["tool"] == "one_use":
        return (_all(lambda api, inv: inv.count("minecraft:iron_pickaxe") >= 1,
                     lambda api, inv: inv.count("minecraft:stone_pickaxe") + inv.count("minecraft:wooden_pickaxe") == 0,
                     _gain("minecraft:cobblestone", 3)), "broken: the best tier this bag crafts (iron)")
    return (_all(lambda api, inv: inv.count("minecraft:iron_ingot") == 3, _gain("minecraft:cobblestone", 3)),
            "fresh: nothing crafted, the ingots kept (must not craft)")


def _night_rule(cell):
    below = lambda api, inv: api.get("/state")["blockY"] < at(0, -3, 0)[1]      # noqa: E731
    if cell["head"] == "underground" and cell["dusk"] == "night":
        return _all(_gain("minecraft:raw_iron", 1), below), "night underground: work there (ore), no climb"
    if cell["head"] == "underground" and cell["dusk"] == "tight":
        return below, "dusk underground: already under cover, no climb to the surface (boundary)"
    if cell["dusk"] != "plenty":
        return _before_in_bag("bed", "minecraft:raw_iron", or_never=True), "dusk or night on the surface: a bed first"
    return (lambda api, inv: int(api.get("/state")["timeOfDay"]) % 24000 < 13000 and inv.count("bed") == 0,
            "daylight: no bed made, no sleep (must not)")


def _bag_rule(cell):
    if cell["bag"] == "valuables_full":
        return _kept("minecraft:diamond"), "a bag of diamonds: not one thrown to make room (must not)"
    if cell["bag"] == "junk_full":
        return (_all(_gain("log", 2), lambda api, inv: inv.count("minecraft:dirt") < _base_count("minecraft:dirt")),
                "a bag of junk: junk thrown, then the task done")
    return _gain("log", 2), "room (or one slot) for it: the task done as usual"


FINDS = {"diamond": 0}


def _count_finds(ctx):
    """`before` hook: count the world scans (/find asked for diamond ore) during the row, at the one door every
    module's `find` goes through (api.get)."""
    from . import api
    FINDS["diamond"] = 0
    real = FINDS.setdefault("real", api.get)

    def get(path, *a, **k):
        if path.startswith("/find") and "diamond" in path:
            FINDS["diamond"] += 1
        return real(path, *a, **k)
    api.get = get


def _no_scan():
    def check(api_, inv):
        from . import api
        api.get = FINDS.get("real", api.get)
        return FINDS["diamond"] == 0
    return check


def _seen_rule(cell):
    # Seeing through stone is allowed (user, 2026-09-27): memory's worth is speed — a noted ore is walked to
    # straight, with no scan; an unnoted one is still found, by scanning.
    if cell["seen"] == "noted":
        return (_all(_gain("minecraft:diamond", 1), _not_remembered("diamond_ore"), _no_scan()),
                "noted: straight there without a scan (must not scan), the note retired")
    return _gain("minecraft:diamond", 1), "not noted: found anyway, by scanning"


# One value off the base at a time (each value once, plus the base), not the product: which combination wins is the
# arbiter's decision, tested offline (arbiter.arbitrate's table, reflexes.TABLE, needs); a row here confirms that a
# decision is carried out in the world.
BRAIN_FAMILIES = {
    "night_first": (list(_cells(BRAIN_BASE, dims=("dusk", "food"), table=BRAIN_DIMS)), [_have(("log", 2))], _bed_then_log),
    "tool_tier": (list(_cells(BRAIN_BASE, dims=("tool", "head"), table=BRAIN_DIMS)),
                  # 3 more than the kit carries: "have 3" was met by the kit's 16 and nothing was mined
                  [_have(("minecraft:cobblestone", KIT_COBBLE + 3))], _tool_rule),
    "night_under": (list(_cells(BRAIN_BASE, dims=("dusk", "head"), table=BRAIN_DIMS)), [], _night_rule),
    "tidy_then_task": (list(_cells(BRAIN_BASE, dims=("bag",), table=BRAIN_DIMS)), [_have(("log", 2))], _bag_rule),
    "seen_store": (list(_cells(BRAIN_BASE, dims=("seen", "head"), table=BRAIN_DIMS)),
                   [_have(("minecraft:diamond", 1))], _seen_rule),
}


def _cell_name(family, cell):
    moved = [cell[d] for d in BRAIN_DIMS if cell[d] != BRAIN_BASE[d]]
    return f"{family}__" + ("_".join(moved) or "base")


def _cell_before(cell):
    hooks = [_clear_bans, _forget_all("diamond_ore"), _first_times]
    if cell["seen"] == "noted":
        hooks.append(_seen("diamond_ore", _diamond_of(cell)))
    hooks.append(_count_finds)
    if cell["food"] == "low":
        hooks.append(_drain_to(LOW_FOOD))       # setup's hunger drains the bar; cleared at LOW_FOOD
    return hooks


def _cell_setup_hooks(cell):
    """`before` hooks that make the row's world and so run before `_start` takes the base: a bag filled after the
    base was read made "dirt < base" (junk thrown) impossible — base dirt 0 (tidy_then_task__junk_full)."""
    return [_fill_bag(*BAG_FILL[cell["bag"]])] if BAG_FILL[cell["bag"]] else []


def _grid_cells():
    """Cells of every family, one row per distinct cell: a cell two families share (the base, dusk, underground) is
    one scenario whose check is every family's expectation and whose queue is every family's goals, in order."""
    cells = {}
    for fam, (grid, queue, rule) in BRAIN_FAMILIES.items():
        for cell in grid:
            key = tuple(cell[d] for d in BRAIN_DIMS)
            entry = cells.setdefault(key, {"cell": cell, "families": [], "queue": [], "rules": []})
            entry["families"].append(fam)
            entry["queue"] += [g for g in queue if g not in entry["queue"]]
            entry["rules"].append(rule)
    return cells


def grid_name(families, cell):
    """The row of a cell: its family's name when only one family has it, else "brain"."""
    return _cell_name(families[0] if len(families) == 1 else "brain", cell)


for _key, _entry in _grid_cells().items():
    _cell = _entry["cell"]
    _name = grid_name(_entry["families"], _cell)
    _judged = [_rule(_cell) for _rule in _entry["rules"]]
    SHEET[_name] = {
        "doc": f"{'+'.join(_entry['families'])}: " + ", ".join(f"{d} {_cell[d]}" for d in BRAIN_DIMS) + " → "
               + "; ".join(why for _c, why in _judged),
        "module": "brain", "point": "C", "skills": [], "tier_fixed": "brain", "combat": False,
        "tags": {"base": "brain", "family": "+".join(_entry["families"]), **{d: _cell[d] for d in BRAIN_DIMS}},
        "setup": BRAIN_WORLD + [c for d in BRAIN_DIMS for c in BRAIN_DIMS[d][_cell[d]]],
        "before": _hooks(*_cell_setup_hooks(_cell), _start(_name), *_cell_before(_cell)),
        "queue": list(_entry["queue"]),
        "run": _slice(None, 0.4, queue=list(_entry["queue"])),
        "check": _all(*[c for c, _why in _judged], _slice_check(None)),
        "budget": 30,
    }


# -- every upkeep line, triggered through the whole brain (nothing queued): the moment is built, upkeep must see it
# and its answer must show in the world. (line, setup, `before` hooks, done, check) — one table, one loop.
def _job_ready_at(pos, item, n):
    """`before` hook: memory holds a finished background furnace job at `pos` (its output already in the furnace)."""
    def hook(ctx):
        ctx.mem.add_job("furnace", pos, "minecraft:overworld", item, n, time.time() - 1, False)
    return hook


def _is_day_now():
    return _is_day()(__import__("bonobo.api", fromlist=["get"]), None)


def _blocked_toward(pos):
    """`before` hook: upkeep's memory of a walk that failed here, toward `pos` (what `Upkeep.failed` writes)."""
    def hook(ctx):
        from . import retry
        from .world import Snapshot
        snap = Snapshot()
        core.BRAIN.reflexes.blocked = {"t": time.time(), "place": retry.place_signature(snap.feet, snap.night),
                                    "pos": pos}
    return hook


def _stuck_for(seconds):
    """`before` hook: upkeep's history says we stood here, bag unchanged, for `seconds`."""
    def hook(ctx):
        from .needs import bag_signature
        from .world import Snapshot
        snap = Snapshot()
        core.BRAIN.reflexes.history = [(time.time() - seconds, snap.feet, bag_signature(snap.inv))]
    return hook


def _machine_due(origin, n):
    """`before` hook: memory holds an auto smelter at `origin` with an order of `n` ingots already due."""
    def hook(ctx):
        m = _bench_machine(ctx, origin)
        ctx.mem.add_pending(m["name"], "minecraft:iron_ingot", n, time.time() - 1)
    return hook


_st = lambda api: api.get("/state")     # noqa: E731
UPKEEP_FURNACE = at(2, 0, 0)
BRIDGE_ACROSS = 9       # needs.bridge_stock from the start to at(9, 0, 0)
UPKEEP_ROWS = [
    ("reach_land", "treading water 6 blocks from a shore → on dry land",
     _tank(-6, 5, -4, 4, 1, water_top=-1) + [f"fill {_c(at(6, -3, -4))} {_c(at(9, -1, 4))} stone", _tp()], [],
     lambda: _st(__import__("bonobo.api", fromlist=["get"]))["onGround"] and not _st(
         __import__("bonobo.api", fromlist=["get"]))["inWater"],
     lambda api, inv: _st(api)["onGround"] and not _st(api)["inWater"]),
    ("dig_out", "daytime, sealed in stone with a pickaxe → out, not enclosed",
     [f"fill {_c(at(-4, -2, -4))} {_c(at(4, 3, 4))} stone", f"fill {_c(at(0, 0, 0))} {_c(at(0, 1, 0))} air",
      _tp(0.5, 0, 0.5), "give @p stone_pickaxe"], [],
     lambda: not _enclosed(), lambda api, inv: not _enclosed()),
    ("collect_job", "a finished background smelt remembered at a furnace 2 blocks off → the ingots in the bag",
     _floor() + [f"setblock {_c(UPKEEP_FURNACE)} furnace",
                 f"item replace block {_c(UPKEEP_FURNACE)} container.2 with iron_ingot 3", _tp()],
     [_job_ready_at(UPKEEP_FURNACE, "minecraft:iron_ingot", 3)],
     _count("minecraft:iron_ingot", 3), _gain("minecraft:iron_ingot", 3)),
    ("empty_the_bag", "a full bag (dirt in every slot) → room made",
     _floor() + [_tp(), "give @p dirt 2304", "give @p stone_pickaxe"], [],
     lambda: _inv_now().used_slots() < 34, lambda api, inv: inv.used_slots() < 34),
    ("no_pickaxe", "no pickaxe, planks + sticks + a table carried → a pickaxe made",
     _floor() + [_tp(), "give @p oak_planks 6", "give @p stick 4", "give @p crafting_table"], [],
     lambda: bool(_inv_now().tools("pickaxe")), lambda api, inv: bool(inv.tools("pickaxe"))),
    ("eat", "hungry, bread carried → eaten (the food bar rises)",
     _floor() + [_tp(), "give @p bread 4", "effect give @p minecraft:hunger 5 255 true"],
     [lambda ctx: (time.sleep(5.5), BASE.update(food_before=__import__("bonobo.api", fromlist=["get"]).get(
         "/state")["food"]))], lambda: _food_up()(__import__("bonobo.api", fromlist=["get"]), None), _food_up()),
    # the path blocked: a gap between us and where the last walk failed to go
    ("path_blocked", "the last walk failed toward the far side of a 6-block gap, 16 blocks carried → bridged across",
     _floor() + [f"fill {_c(at(2, -3, -8))} {_c(at(7, -1, 8))} air", _tp(), "give @p cobblestone 16"],
     [_blocked_toward(at(9, 0, 0))], lambda: _at(at(9, 0, 0), 4)(__import__("bonobo.api", fromlist=["get"]), None),
     _at(at(9, 0, 0), 4)),
    # Blocks fetched to what the way across takes (needs.bridge_stock: 9 to x 9), then bridged: judged by the gap
    # crossed or the stock reached.
    ("bridge_stock", "the same gap with 2 blocks carried (under BRIDGE_MIN), stone underfoot, a pickaxe → blocks "
     "fetched first, to what the way across takes (bridge_stock), then across",
     _floor() + [f"fill {_c(at(2, -3, -8))} {_c(at(7, -1, 8))} air", _tp(), "give @p cobblestone 2",
                 "give @p diamond_pickaxe"],
     [_blocked_toward(at(9, 0, 0))],
     lambda: _at(at(9, 0, 0), 4)(__import__("bonobo.api", fromlist=["get"]), None)
     or _inv_now().count("minecraft:cobblestone") >= BRIDGE_ACROSS,
     lambda api, inv: _at(at(9, 0, 0), 4)(api, inv) or inv.count("minecraft:cobblestone") >= BRIDGE_ACROSS),
    ("unstuck", "a minute in the same block with the same bag (history set), open ground → moved off (≥ 5 blocks)",
     _floor() + [_tp()], [_stuck_for(70)], lambda: not _near(__import__("bonobo.api", fromlist=["get"]),
                                                           at(0, 0, 0), 5),
     lambda api, inv: not _near(api, at(0, 0, 0), 5)),
    ("collect_machine", "a remembered auto smelter whose order is due, 8 ingots in its output chest → taken",
     _floor() + _chest(at(3, 0, 0), "iron_ingot 8") + [_tp()], [_machine_due(at(3, 0, 0), 8)],
     _count("minecraft:iron_ingot", 8), _gain("minecraft:iron_ingot", 8)),
    ("eat_when_full", "fed (food 20), bread carried → not eaten: the bread count unchanged (must not)",
     _floor() + [_tp(), "give @p bread 4"], [], lambda: False,
     lambda api, inv: inv.count("minecraft:bread") == 4),
]
# The night's shelter, by what the bag allows (upkeep.shelter: a pickaxe digs in, else the hut's materials build a
# hut, else blocks wall in) — and a bed makes none of them (must not).
_NIGHT_FLOOR = [f"fill {_c(at(-8, -6, -8))} {_c(at(8, -1, 8))} stone", _tp(), "time set 18000"]
UPKEEP_ROWS += [
    ("shelter_dig_in", "night, a pickaxe → dug in: below the floor, enclosed",
     _NIGHT_FLOOR + ["give @p stone_pickaxe", "give @p cobblestone 8"], [], _enclosed,
     _all(lambda api, inv: _enclosed(), lambda api, inv: api.get("/state")["y"] < at(0, 0, 0)[1] - 0.5)),
    # The hut's materials in the blueprint's own group (cobblestone), no pickaxe: whichever way needs.overnight
    # prices cheapest is built, judged by the world — sheltered. A cell where the hut is the cheapest does not
    # exist: its 14 "stone" are building blocks, so walling in (9 of them, 40 s against 120 s) is always open and
    # cheaper whenever the hut is.
    ("shelter_hut", "night, no pickaxe, the hut's materials (cobblestone, a door, a torch) → sheltered by the way "
     "the night's pricing chose",
     _NIGHT_FLOOR + ["give @p cobblestone 32", "give @p oak_door", "give @p torch 2"], [], _enclosed,
     lambda api, inv: _enclosed()),
    ("shelter_wall_in", "night, no pickaxe, cobblestone only → walled in where it stands",
     _NIGHT_FLOOR + ["give @p cobblestone 16"], [], _enclosed,
     _all(lambda api, inv: _enclosed(), _blocks(at(-1, 0, -1), at(1, 2, 1), "cobblestone", 9))),
    ("shelter_not_with_a_bed", "night, a bed and cobblestone carried → slept, no shelter built (must not)",
     _NIGHT_FLOOR + ["give @p white_bed", "give @p cobblestone 16"], [], _is_day_now,
     _all(_is_day(), _blocks(at(-3, 0, -3), at(3, 2, 3), "cobblestone", 0, 0))),
]
for _line, _doc, _setup, _hooks_, _done, _check in UPKEEP_ROWS:
    _name = f"upkeep__{_line}"
    SHEET[_name] = {
        "doc": f"upkeep, {_doc}", "module": "reflexes", "point": "C", "skills": [], "tier_fixed": "brain",
        "combat": _line == "eat", "tags": {"base": "upkeep", "line": _line},
        "setup": list(_setup), "before": _hooks(_start(_name), *_hooks_),
        "run": _brain_rounds(10 if _line == "eat_when_full" else 22, _done), "check": _check, "budget": 30,
    }


# A fight with no pickaxe in the bag: upkeep's "no pickaxe" must wait until the fight is over — the zombie dealt
# with first, no log gathered while it stands, and the player still inside the arena (it walked off the sky
# platform to look for trees mid-fight and fell 125 blocks).
# Dusk on stone with an empty bag, a patch of dirt three deep 8 blocks along the platform: the night's pricing walks
# there and digs in by hand (terrain.nearest_soft + the walk in the price). The control: the same patch across a
# drop to nothing — it is not on this ground, so the body never goes there.
DIRT_PATCH = (at(7, -3, -1), at(8, -1, 1))
DIRT_FLOOR = (at(7, -4, -1), at(8, -4, 1))      # the stone the dirt lies on


def _in_the_patch_underground(api, inv):
    s = api.get("/state")
    (x0, _y0, z0), (x1, _y1, z1) = DIRT_PATCH
    return (x0 <= s["blockX"] <= x1 and z0 <= s["blockZ"] <= z1 and s["blockY"] <= at(0, 0, 0)[1] - 2
            and _enclosed())


for _name, _doc, _extra, _done, _check in [
        ("night_dig_in_dirt", "dusk on stone, an empty bag, dirt three deep 8 blocks along the platform → walked "
         "there, dug in by hand: two or more down in the dirt, sealed overhead", [],
         lambda: _in_the_patch_underground(__import__("bonobo.api", fromlist=["get"]), None), _in_the_patch_underground),
        ("night_dig_in_dirt_unreachable", "the same dirt across a drop to nothing, a pod's blocks carried → never "
         "walked to (must not): walled in on its own side of the gap",
         # The fallback's blocks given (console-built): the row tests the choice, not mining a wall's blocks.
         [f"fill {_c(at(4, -3, -8))} {_c(at(5, -1, 8))} air", f"give @p cobblestone {POD_BLOCKS}"], _enclosed,
         lambda api, inv: api.get("/state")["blockX"] < at(4, 0, 0)[0])]:
    SHEET[_name] = {
        "doc": _doc, "module": "brain", "point": "C", "skills": ["shelter:dig in"], "tier_fixed": "brain",
        "tags": {"base": "brain", "family": "night_dirt"},
        # Stone under the dirt: soft ground needs something to stand on under its three soft cells (terrain.soft_below);
        # over the void (y 196) there was no soft spot at all and the body walled in instead.
        "setup": _floor() + [f"fill {_c(DIRT_PATCH[0])} {_c(DIRT_PATCH[1])} dirt",
                             f"fill {_c(DIRT_FLOOR[0])} {_c(DIRT_FLOOR[1])} stone"] + _extra + [_tp(), "time set 12500"],
        "before": _start(_name),
        "run": _brain_rounds(25, _done), "check": _check, "budget": 30,
    }


SHEET["fight_before_upkeep"] = {
    "doc": "Arena, iron sword and armour but no pickaxe, a zombie 4 blocks off, nothing queued → the zombie dead "
           "before any log is gathered (must not), the player never leaves the arena",
    "module": "brain", "point": "C", "skills": [], "tier_fixed": "brain", "combat": True, "stochastic": True,
    "tags": {"base": "brain", "family": "fight_first"},
    "setup": [c for c in _ARENA if "stone_pickaxe" not in c] + [
        f"summon zombie {_c(at(4, 0, 0))} {{PersistenceRequired:1b}}"],
    "expect_entities": [("minecraft:zombie", 1)],
    "before": _hooks(_start("fight_before_upkeep"), _first_times, _record_bids),
    "run": _brain_rounds(24, lambda: not _hostiles(24, {"minecraft:zombie"})),
    "check": _all(_gone(["minecraft:zombie"]), _hp_kept(10), lambda api, inv: _near(api, at(0, 0, 0), 9),
                  lambda api, inv: FIRST.get("log") is None),
    "budget": 30,
}


# Eating on the move: hungry, cooked beef carried, one 20-block walk east → fed on the way, by the jar's autoeat
# (no separate eat task), and still walking forward while it chewed. The control: hungry while mining — the mining
# is not interrupted to eat.
WALK = {}
BITE_S = 1.6        # one bite (32 ticks): the window before the bar rises in which the body must keep moving


def ate_on_the_way(frames):
    """Pure, over trace frames ({"t", "x", "food", "task"}, the runner's `_trace` shape): the food bar rose during the
    walk; no frame ran an "eat" task (it was the autoeat, not a stop); and over the bite before the rise, x grew in
    every second (the walk went on while chewing)."""
    fed = [f for f in frames if f.get("food") is not None]
    if not fed or any((f.get("task") or {}).get("type") == "eat" for f in frames):
        return False
    rise = next((f for f in fed if f["food"] > fed[0]["food"]), None)
    if rise is None:
        return False
    window = [f for f in frames if rise["t"] - BITE_S <= f["t"] <= rise["t"] and f.get("x") is not None]
    if len(window) < 2:
        return False
    # x at each whole second of the window, and at its end: every step forward.
    at_s = lambda sec: min(window, key=lambda f: abs(f["t"] - (window[0]["t"] + sec)))["x"]   # noqa: E731
    xs = [at_s(sec) for sec in range(int(window[-1]["t"] - window[0]["t"]) + 1)] + [window[-1]["x"]]
    xs = [x for k, x in enumerate(xs) if k == 0 or x != xs[k - 1] or k < len(xs) - 1]
    return all(b > a for a, b in zip(xs, xs[1:]))


def _walk_once(ctx):
    """Walk 20 east hungry, the whole walk traced (the runner's `_trace`: position, food, the jar's task)."""
    import threading
    frames, stop = [], threading.Event()
    threading.Thread(target=_trace, args=(stop, frames), daemon=True).start()
    try:
        _skill("travel_to")(ctx, at(18, 0, 0), 2)
    finally:
        stop.set()
        WALK["frames"] = frames
    return True


def _hungry(ctx):
    """`before` hook: food drained to about half (hunger at full strength for 5 s), and the level remembered."""
    WALK.clear()
    _chat("effect give @p minecraft:hunger 5 255 true")
    time.sleep(5.5)
    BASE.update(food_before=__import__("bonobo.api", fromlist=["get"]).get("/state")["food"])


SHEET["eat_while_walking"] = {
    "doc": "Hungry, cooked beef carried, 20 blocks to walk → fed on the way without an eat task, still walking "
           "forward while it chewed (ate_on_the_way over the walk's trace)",
    "module": "skills", "point": "A", "skills": ["goto"], "tier_fixed": "common", "combat": False, "stochastic": False,
    "tags": {"base": "nav", "state": "hungry"},
    "setup": _floor() + [f"fill {_c(at(8, -3, -3))} {_c(at(20, -1, 3))} stone", _tp(-2, 0, 0), "give @p cooked_beef 4"],
    "before": _hooks(_start("eat_while_walking"), _hungry),
    "run": _walk_once,
    "check": _all(lambda api, inv: ate_on_the_way(WALK.get("frames", [])), _at(at(18, 0, 0), 3)),
    "budget": 30,
}
SHEET["mine_while_hungry"] = {
    "doc": "Hungry, cooked beef carried, 3 cobblestone to mine → mined without a pause to eat: the beef untouched "
           "(control for eat_while_walking)",
    "module": "skills", "point": "A", "skills": ["mine"], "tier_fixed": "common", "combat": False, "stochastic": False,
    "tags": {"base": "mine_stone", "state": "hungry"},
    "setup": _floor() + [_tp(), "give @p wooden_pickaxe", "give @p cooked_beef 4"],
    "before": _hooks(_start("mine_while_hungry"), _hungry),
    "run": lambda ctx: _skill("mine")(ctx, "minecraft:cobblestone", 3, ["stone"], 0),
    "check": _all(_gain("minecraft:cobblestone", 3), lambda api, inv: inv.count("minecraft:cooked_beef") == 4),
    "budget": 15,
}

# Low health with a walker on top of us: first out of its reach (walls, a pillar, a step away), then eat — health
# ends above where it began. Eating while it keeps hitting (health falling on) fails.
SHEET["combat__low_hp_eat"] = {
    "doc": "6 hp, one zombie 2 blocks off, blocks and cooked beef → away from it or walled in first, then fed: "
           "health ends above 6",
    "module": "fight_loop", "point": "B", "skills": [], "combat": True, "stochastic": True,
    "tags": {"base": "fight", "enemy": "zombie", "blood": "low"},
    "setup": list(_ARENA) + ["damage @p 14 minecraft:magic", f"summon zombie {_c(at(2, 0, 0))} {{PersistenceRequired:1b}}"],
    "expect_entities": [("minecraft:zombie", 1)],
    "before": _hooks(_start("combat__low_hp_eat"), _record_bids),
    "run": _fight_until(["minecraft:zombie"], 22, False),
    "check": _all(lambda api, inv: api.get("/state")["health"] > 6, _alive(1)),
    "budget": 30,
}


# A creeper met with a sword: hit and back out of its blast (jar footwork "keepoff") — dead, or blown up in the air,
# either way gone; the health kept. Next to our own builds it is first led away from them: a bed (the least blast
# resistant block we own) and a furnace within 3 of it, registered as ours, are still standing after.
CREEPER_AT = at(4, 0, 0)
HOME_BED, HOME_FURNACE = (at(4, 0, 2), at(5, 0, 2)), at(4, 0, -2)


def _creeper_row(name, extra_setup=(), before=(), check=()):
    return {
        "doc": "Iron sword, a creeper 4 blocks off" + (", a bed and a furnace of ours within 3 of it" if extra_setup
                                                      else "") + " → the creeper gone (dead or blown up in the air), "
               "health ≥ 16" + (", the bed and the furnace still standing" if extra_setup else ""),
        "module": "fight_loop", "point": "B", "skills": [], "combat": True, "stochastic": True,
        "tags": {"base": "fight", "enemy": "creeper", "ground": "home" if extra_setup else "open"},
        "setup": list(_ARENA) + list(extra_setup) + [f"summon creeper {_c(CREEPER_AT)} {{PersistenceRequired:1b}}"],
        "expect_entities": [("minecraft:creeper", 1)],
        "before": _hooks(_start(name), _record_bids, *before),
        "run": _fight_until(["minecraft:creeper"], 25, True),
        "check": _all(_gone(["minecraft:creeper"]), _hp_kept(16), *check),
        "budget": 30,
    }


def _home_is_ours(ctx):
    """`before` hook: the bed and the furnace are a site of ours (memory.protected_cells) and the furnace a station
    — what the fight has to keep out of the blast."""
    blocks = {f"{c[0]},{c[1]},{c[2]}": "red_bed" for c in HOME_BED}
    blocks[f"{HOME_FURNACE[0]},{HOME_FURNACE[1]},{HOME_FURNACE[2]}"] = "furnace"
    core.BRAIN.mem.add_site("home", HOME_BED[0], "minecraft:overworld", snapshot={"blocks": blocks}, name="home")
    core.BRAIN.mem.add_station("minecraft:furnace", HOME_FURNACE, "minecraft:overworld")


SHEET["fight_creeper_sword"] = _creeper_row("fight_creeper_sword")
SHEET["fight_creeper_by_home"] = _creeper_row(
    "fight_creeper_by_home",
    extra_setup=[f"setblock {_c(HOME_BED[0])} red_bed[facing=east,part=foot]",
                 f"setblock {_c(HOME_BED[1])} red_bed[facing=east,part=head]",
                 f"setblock {_c(HOME_FURNACE)} furnace"],
    before=[_home_is_ours],
    check=[_blocks(HOME_BED[0], HOME_BED[1], "red_bed", 2), _blocks(HOME_FURNACE, HOME_FURNACE, "furnace", 1)])


# Fighting at the edge of a raised platform, knocked off it: a zombie with a strong knockback on a 5×5 platform
# 20 blocks above the floor. The combat kit's water bucket (every combat row carries one) must catch the fall.
EDGE_Y = 4
SHEET["combat__knocked_off_edge"] = {
    "doc": "A zombie that hits hard enough to throw us off a platform 20 blocks up, iron kit + water bucket → "
           "knocked off, the fall caught: alive, health within 4 of the start, the bucket back in the bag",
    "module": "fight_loop", "point": "B", "skills": [], "combat": True, "stochastic": True,
    "tags": {"base": "fight", "enemy": "zombie", "ground": "edge"},
    "setup": [f"fill {_c(at(-8, -17, -8))} {_c(at(8, -17, 8))} stone",
              f"fill {_c(at(-8, -16, -8))} {_c(at(8, EDGE_Y + 3, 8))} air",
              f"fill {_c(at(-2, EDGE_Y - 1, -2))} {_c(at(2, EDGE_Y - 1, 2))} stone",
              _tp(2, EDGE_Y, 0), "give @p iron_sword", "give @p water_bucket",
              "item replace entity @p armor.chest with iron_chestplate",
              f"summon zombie {_c(at(0, EDGE_Y, 0))} {{PersistenceRequired:1b,"
              f"attributes:[{{id:\"minecraft:attack_knockback\",base:3.0}}]}}"],
    "expect_entities": [("minecraft:zombie", 1)],
    "before": _hooks(_start("combat__knocked_off_edge"), _record_bids),
    "run": _fight_until(["minecraft:zombie"], 22, False),
    "check": _all(_alive(1), lambda api, inv: api.get("/state")["y"] < at(0, EDGE_Y - 10, 0)[1],
                  lambda api, inv: api.get("/state")["health"] >= BASE["state"]["health"] - 4,
                  lambda api, inv: inv.count("minecraft:water_bucket") >= 1),
    "budget": 30,
}


# -- test point D: acceptance ------------------------------------------------------------------------------------
SCENARIOS[ACCEPTANCE_D] = {
    "doc": "Acceptance: a fresh spot of a real world, empty-handed, the whole cerebellum → an iron pickaxe within "
           "30 minutes (stone tools → iron pickaxe milestones)",
    "module": "brain", "raw": True, "release": True, "point": "D", "skills": [], "tags": {"base": "acceptance"},
    "setup": ["spreadplayers 13000 13000 0 4 false @p", "clear @p", "time set 0"],
    "run": _slice(lambda: _inv_now().count("minecraft:iron_pickaxe") >= 1, 30, max_idle=60,
                  queue=[__import__("bonobo.goals", fromlist=["goals"]).make("milestone", name="stone tools"),
                         __import__("bonobo.goals", fromlist=["goals"]).make("milestone", name="iron pickaxe")]),
    "check": _slice_check(lambda: _inv_now().count("minecraft:iron_pickaxe") >= 1, max_idle=60),
    "detail": _slice_detail, "budget": 1800,
}
for _i, _name in enumerate(CHAIN_C):
    SCENARIOS[_name].update(point="C", chain=_i)

# -- what the hand-written rows prove (skills) and at which test point --------------------------------------------
COVERS = {
    "cast_portal": ["cast_portal"], "build_light_portal": ["build_blueprint"],
    "fill_water_bucket": ["fill_water_bucket"], "cross_lava_lake": ["travel_to"], "cross_lava_3": ["travel_to"],
    "cross_lava_8": ["travel_to"], "gather_logs": ["chop"], "gather_logs_birch": ["chop"],
    "enter_nether": ["use_portal"], "relight_portal": ["use_portal"], "return_from_nether": ["use_portal"],
    "return_to_portal": ["use_portal"], "retreat_from_nether": ["use_portal"], "barter_piglin": ["barter_piglin"],
    "collect_blaze_rods": ["collect_blaze_rods"], "activate_end_portal": ["activate_end_portal"], "enter_end": ["enter_end"],
    "craft_stone_tools": ["craft", "mine", "chop"], "iron_ingots": ["load_smelter", "start_smelt_job", "smelt"],
    "hunt_food": ["hunt"], "craft_eyes": ["craft"], "locate_stronghold": ["locate_stronghold"],
    "find_portal_room_fresh": ["find_portal_room"], "fight_dragon": ["slay_dragon"], "loot_chest": ["loot_chest"],
    "recover_items": ["recover_items"], "find_fortress": ["find_fortress"], "find_fortress_far": ["find_fortress"],
    "bed_bomb_kill": ["slay_dragon"], "cave_escape": ["travel_to"],
}
for _name, _skills in COVERS.items():
    if _name in SCENARIOS:
        SCENARIOS[_name].setdefault("skills", list(_skills))
        SCENARIOS[_name].setdefault("point", "A")
for _row_ in SHEET.values():              # the runner's setup signature: the box holds what the setup built
    if not _row_.get("raw"):
        _row_.setdefault("expect", [(at(*BOX[0]), at(*BOX[1]), "*", 1, 10 ** 6)])
SCENARIOS.update(SHEET)
for _name in ("slice_retreat",):
    SCENARIOS[_name].setdefault("point", "C")
for _row_ in SCENARIOS.values():          # brain/nav/fight rows prove no one skill: they carry an empty list
    _row_.setdefault("skills", [])
    _row_.setdefault("point", "A")


# ================================================================================================================
# Tiers (docs/refactor.md §1). core: every basic action on the default arena, the three L0 hazards, the start of the
# chain — run on every change. common: core × the conditions play meets daily — run when a related module changed.
# exception: everything else — before a merge.
# acceptance: test point D, its own layer (30 minutes from a fresh world) — never part of another tier's run.
TIERS = ("core", "common", "brain", "combat", "exception", "acceptance")
# Fighting is its own tier: every fight row, sweep shard and fight behaviour cell — never common.
COMBAT_PREFIXES = ("fight_", "combat_arena", "siege__", "escape__", "fight_before_upkeep", "combat__")
# The chain's first slice (slice_start_tools: minutes on real terrain, a release row) is common, not core: core is
# what every change can afford to run.
CORE = tuple(f"{b}__base" for b in BASES) + ("lava_edge_walk", "drowning_in_a_pit", "buried_by_sand",
                                             "iron_ingots", "bed_in_nether", "slice_start_tools", "water_clutch")
COMMON_CONDITIONS = ("night", "canopy", "cave", "full_bag", "interrupt_mid_work")
# Upkeep's own rows and the test-point-B hazards: everyday, so common whatever their shape; the chain's last leg too.
COMMON = ("dig_in_night", "reach_land_swim", "chest_or_tree", "cross_lava_8", "cave_escape",
          "slice_nether_kit")
ACCEPTANCE = (ACCEPTANCE_D,)


def tier_of(name, row):
    """Pure: the tier a row belongs to (a row that states its own tier keeps it)."""
    if name.startswith(COMBAT_PREFIXES) or row.get("module") == "fight_loop":
        return "combat"
    if row.get("tier_fixed") in ("core", "common", "brain", "combat", "exception"):
        return row["tier_fixed"]
    if name in CORE:
        return "core"
    if name in ACCEPTANCE:
        return "acceptance"
    if name in COMMON:
        return "common"
    tags = row.get("tags", {})
    if tags.get("base") in BASES and any(tags.get(ax) in COMMON_CONDITIONS for ax in ("terrain", "timing", "inventory")):
        return "common"
    return "exception"


for _name, _row_ in SCENARIOS.items():
    _row_["tier"] = tier_of(_name, _row_)
    if _row_["tier"] != "acceptance" and _row_["budget"] > runner.ROW_LIMIT_S:
        # The runner stops every row at 30 s (the user's speedrun rule). A row whose job was not cut down to fit
        # keeps its work and meets the limit: it is reported TIMEOUT, the honest answer, until its setup is squeezed.
        _row_["budget"] = runner.ROW_LIMIT_S


def proves(entry, registry):
    """The skill names a row's `skills` entry proves: a registered name, or every skill providing that effect."""
    by_effect = {n for n, c in registry.items() if entry in getattr(c, "provides", {})}
    return ({entry} if entry in registry else set()) | by_effect


def select(rows, tier="core", changed=None, registry=None):
    """Pure: the names to run. `tier` one of TIERS or "all". `changed` (skill names whose code changed; None = not
    asked) narrows to the rows proving any of them, by name or by an effect they provide — and when a change touches
    no skill any row proves, the core rows stand in for it."""
    picked = [n for n, r in rows.items() if tier == "all" or r.get("tier") == tier]
    if changed is None:
        return picked
    registry = registry or {}
    changed = set(changed)
    hit = [n for n in picked if any(proves(e, registry) & changed for e in rows[n].get("skills", ()))]
    if hit:
        return hit
    return [n for n, r in rows.items() if r.get("tier") == "core"]


def touched_skills(hunks, spans):
    """Pure: skills whose function body a diff touched. `hunks` {path: [changed line numbers]} (new-file numbering);
    `spans` {skill name: (path, first line, last line)}."""
    out = set()
    for name, (path, lo, hi) in spans.items():
        if any(lo <= ln <= hi for ln in hunks.get(path, ())):
            out.add(name)
    return out


def diff_hunks(diff_text):
    """Pure: {path: [line numbers]} of lines added or changed, from `git diff -U0` output."""
    out, path = {}, None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and path:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start, n = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(path, []).extend(range(start, start + max(n, 1)))
    return out


def skill_spans(registry, root):
    """{skill name: (path relative to `root`, first line, last line)} of each registered skill's function."""
    import inspect
    out = {}
    for name, c in registry.items():
        try:
            lines, first = inspect.getsourcelines(c.fn)
            path = os.path.relpath(inspect.getsourcefile(c.fn), root)
        except (OSError, TypeError):
            continue
        out[name] = (path, first, first + len(lines) - 1)
    return out


# -- the kit rule, applied (bench.core.BEST_TOOLS / weapon_for): rows whose work uses a tool, by the tools it uses.
# Rows that test getting a tool (tool_tier, wrong_tool, craft_stone_tools, hand digs, fight_before_upkeep), the sweeps
# whose weapon is the measured dimension, and brain cells whose input is tool state are not in here. One table, one pass.
KIT_JOBS = {
    ("axe",): [
        "brain__night", "brain__tight", "chest_or_tree", "chop__base", "chop__lava_edge", "chop__night",
        "chop__pickup_lag", "chop__stack_room", "chop__valuables_full", "chop_without_interrupt",
        "dead_flicker_on_respawn", "floating_logs", "gather_logs", "gather_logs_birch",
        "interrupted_rescue_is_not_a_failure", "leaves_block_trunk", "night_first__low", "resume_after_combat",
        "seek_blocks_real", "tidy_then_task__junk_full", "tidy_then_task__one_slot",
        "tidy_then_task__valuables_full",
    ],
    ("axe", "pickaxe",): [
        "brain__base", "l3_order_swapped", "l3_two_goals_in_order",
    ],
    ("pickaxe",): [
        "ban_needs_a_failure", "ban_then_other_source", "brain__underground", "bridge_the_gap", "burrow_hillside",
        "cast_portal", "dig_out_morning", "falling_gravel", "find_air_capped", "lava_under_ore", "mine_iron__base",
        "mine_iron__pickup_lag", "mine_stone__base", "mine_stone__buried_by_sand", "mine_stone__cave",
        "mine_stone__full_bag", "mine_stone__interrupt_mid_work", "mine_while_hungry", "night_mines_under_cover",
        "seen_store__noted", "strip_mine_real",
    ],
    ("pickaxe", "shovel",): [
        "dig_in_night",
    ],
    ("shovel",): [
        "buried_by_sand", "unbury_sand",
    ],
    ("sword",): [
        "bed_bomb_kill", "break_caged_crystal", "combat__block_gap", "combat__dig_in", "combat__fight_and_block",
        "combat__fight_without_shield", "combat__knocked_off_edge", "combat__low_hp_eat", "combat__pillar",
        "combat__shield_arrows", "combat__surrounded_low", "combat__wall_in", "fight_blaze_3", "fight_creeper_1",
        "fight_creeper_by_home", "fight_creeper_sword", "fight_enderman_1", "fight_skeleton_1", "fight_zombie_1",
        "fight_zombie_1_full_bag", "fight_zombie_3", "hunt__base", "hunt__lava_edge", "hunt__one_slot",
        "hunt__pickup_lag", "hunt__pillar", "hunt__valuables_full", "hunt_food", "siege__w1", "siege__w2",
        "siege__w3", "siege__w4", "siege__w5", "siege__w6", "siege__w7",
    ],
}


def _kit_gives(row, jobs):
    """The gives the kit rule adds to `row`: the best work tool per job, and for a fight the sword its mobs call for."""
    import re
    from .bench.core import BEST_TOOLS, weapon_for
    mobs = set(re.findall(r"summon (?:minecraft:)?(\w+)", " ".join(map(str, row.get("setup", ())))))
    enemy = (row.get("tags") or {}).get("enemy")
    mobs |= {enemy} if enemy else set()
    if any(k in s for s in list(row.get("skills", ())) + [str(row.get("doc", ""))] for k in ("dragon", "crystal")):
        mobs.add("ender_dragon")          # the End fight: the dragon is there, not summoned
    return [weapon_for(sorted(mobs)) if j == "sword" else BEST_TOOLS[j] for j in jobs]


for _jobs, _rows in KIT_JOBS.items():
    for _name in _rows:
        SCENARIOS[_name]["setup"] = list(SCENARIOS[_name]["setup"]) + _kit_gives(SCENARIOS[_name], _jobs)

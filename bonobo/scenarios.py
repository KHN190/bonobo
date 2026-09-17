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
    return core.BRAIN.survival(Snapshot(), ctx)


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
    "run": lambda ctx: core.BRAIN.reflexes(),
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
            core.BRAIN.reflexes()
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
    loop (the same decision line 4×) or an idle hold longer than `max_idle` — a report, not a timeout.

    `done=None` means the window itself is the test: run the full `minutes` and let the scenario's own check say
    whether it went well. That is what the e2e markers need — surviving a night has no completion, only an end.
    """
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
        core.BRAIN.idle_since, core.BRAIN.committed = None, None
        t0, positions, idle = time.time(), [], 0.0
        start_line = len(sys.stdout.lines) if hasattr(sys.stdout, "lines") else 0
        stopped = None
        try:
            while time.time() - t0 < minutes * 60:
                if done is not None and done():
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
        if done is not None and not done():
            raise api.McError(f"slice not done after {minutes} min")
        return True
    return run


def _slice_detail(inv):
    if not SLICE:
        return ""
    rep = slice_report(LAST_LINES, SLICE["positions"], SLICE["target"], SLICE["idle"])
    return (f"{SLICE['seconds']:.0f}s, longest idle {rep['idle_s']}s, walked away {rep['away_m']} m, "
            f"loops: {'; '.join(rep['loops'][:3]) or 'none'}")




def _slice_check(done, max_idle=15, max_loops=0):
    def check(api, inv):
        if not SLICE or (done is not None and not done()):
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



from .bench import decide, fight   # noqa: E402,F401  (the other sheets register themselves)
from .bench.decide import *        # noqa: E402,F403
from .bench.fight import *         # noqa: E402,F403
from .bench.decide import (_decision, _decision_ok, _knows_how, _marker, _multitasks, _never_idle, _picked,
                           _plans_far, _rounds_since)   # noqa: E402
from .bench.fight import (_build, _cells, _combat_execute, _combat_intent, _fought, _hostiles,
                          _siege_cells, _summon)        # noqa: E402

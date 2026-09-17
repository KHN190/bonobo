"""Static game knowledge (Minecraft Java 1.21). Pure data, no I/O."""


# Both of these are called tens of millions of times a session — the action table asks them in its innermost
# loop, once per ingredient per recipe per column per round — and they are pure functions of a few hundred
# distinct strings. Memoised, they cost a dict lookup; unmemoised they were nine seconds of a 129-second replay
# spent re-deciding whether "oak_planks" needs a colon.
_MID, _BARE = {}, {}


def mid(name):
    """The full id: "oak_planks" → "minecraft:oak_planks". Already-qualified names pass through."""
    got = _MID.get(name)
    if got is None:
        got = _MID[name] = name if ":" in name else "minecraft:" + name
    return got


def bare(name):
    """The short id: "minecraft:oak_planks" → "oak_planks"."""
    got = _BARE.get(name)
    if got is None:
        got = _BARE[name] = name.removeprefix("minecraft:")
    return got


WOODS = ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry", "pale_oak"]
COLORS = ["white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan",
          "purple", "blue", "brown", "green", "red", "black"]
LOG_TO_PLANKS = {f"minecraft:{w}_log": f"minecraft:{w}_planks" for w in WOODS}
LOG_TO_PLANKS.update({"minecraft:crimson_stem": "minecraft:crimson_planks",
                      "minecraft:warped_stem": "minecraft:warped_planks"})

# Interchangeable items. Recipes that accept "any X" use the group name as a token.
GROUPS = {
    "log": list(LOG_TO_PLANKS),
    "planks": sorted(set(LOG_TO_PLANKS.values())),
    "stone": ["minecraft:cobblestone", "minecraft:cobbled_deepslate", "minecraft:blackstone"],
    "coal": ["minecraft:coal", "minecraft:charcoal"],
    "wool": [f"minecraft:{c}_wool" for c in COLORS],
    "bed": [f"minecraft:{c}_bed" for c in COLORS],
    "boat": [f"minecraft:{w}_boat" for w in WOODS],
    "door": [f"minecraft:{w}_door" for w in WOODS],
    # Anything solid we'd otherwise throw away is building material: bridges, pillars and walls use it up first.
    "building": ["minecraft:andesite", "minecraft:diorite", "minecraft:granite", "minecraft:tuff",
                 "minecraft:dripstone_block", "minecraft:calcite", "minecraft:dirt", "minecraft:cobbled_deepslate",
                 "minecraft:cobblestone", "minecraft:blackstone"],
}

TIER_OF_MATERIAL = {"wooden": 0, "golden": 0, "stone": 1, "iron": 2, "diamond": 3, "netherite": 4}
TOOL_MATERIALS = ["wooden", "stone", "iron", "diamond"]
MATERIAL_TOKEN = {"wooden": "planks", "stone": "stone", "iron": "minecraft:iron_ingot", "diamond": "minecraft:diamond"}
# Minimum pickaxe tier that yields drops.
ORE_TIER = {"coal_ore": 0, "copper_ore": 1, "iron_ore": 1, "lapis_ore": 1, "gold_ore": 2, "redstone_ore": 2,
            "diamond_ore": 2, "emerald_ore": 2, "obsidian": 3, "ancient_debris": 3, "nether_gold_ore": 0,
            "nether_quartz_ore": 0}
ORE_DROP = {"coal_ore": "coal", "copper_ore": "raw_copper", "iron_ore": "raw_iron", "lapis_ore": "lapis_lazuli",
            "gold_ore": "raw_gold", "redstone_ore": "redstone", "diamond_ore": "diamond", "emerald_ore": "emerald",
            "nether_gold_ore": "gold_nugget", "nether_quartz_ore": "quartz"}
ANIMALS = {"minecraft:cow": "beef", "minecraft:pig": "porkchop", "minecraft:sheep": "mutton",
           "minecraft:chicken": "chicken", "minecraft:rabbit": "rabbit"}
COOKED = {f"minecraft:{raw}": f"minecraft:cooked_{raw}" for raw in ANIMALS.values()}
# output item -> input item/group for one furnace operation
SMELTS = {"minecraft:stone": "minecraft:cobblestone", "minecraft:glass": "minecraft:sand",
          "minecraft:iron_ingot": "minecraft:raw_iron", "minecraft:gold_ingot": "minecraft:raw_gold",
          "minecraft:copper_ingot": "minecraft:raw_copper", "minecraft:charcoal": "log",
          **{cooked: raw for raw, cooked in COOKED.items()}}
FUEL_SMELTS = {"coal": 8, "planks": 1.5, "log": 1.5}
FOOD = ["cooked_beef", "cooked_porkchop", "cooked_mutton", "cooked_chicken", "cooked_rabbit", "cooked_salmon",
        "cooked_cod", "bread", "baked_potato", "golden_carrot", "apple", "carrot", "sweet_berries", "glow_berries",
        "melon_slice", "cookie"]

# Food is a group like planks or wool: recipes and plans want "something to eat", the world hands out a cooked
# chop. Without the group, "food" was not a dimension the solver could reach, so the one terminal good the agent
# needs most often could not be priced at all.
GROUPS["food"] = list(FOOD)


def recipes():
    """item -> (row-major pattern of item ids / group tokens / None, output count). 4 entries = 2×2, 9 = 3×3."""
    s, i = "minecraft:stick", "minecraft:iron_ingot"
    r = {
        "minecraft:stick": (["planks", None, "planks", None], 4),
        "minecraft:crafting_table": (["planks"] * 4, 1),
        "minecraft:torch": (["coal", None, s, None], 4),
        "minecraft:furnace": (["stone"] * 4 + [None] + ["stone"] * 4, 1),
        "minecraft:chest": (["planks"] * 4 + [None] + ["planks"] * 4, 1),
        "minecraft:ladder": ([s, None, s, s, s, s, s, None, s], 3),
        "minecraft:shield": (["planks", i, "planks", "planks", "planks", "planks", None, "planks", None], 1),
        "minecraft:bucket": ([i, None, i, None, i, None, None, None, None], 1),
        "minecraft:flint_and_steel": ([i, None, None, "minecraft:flint"], 1),
        "minecraft:iron_helmet": ([i, i, i, i, None, i, None, None, None], 1),
        "minecraft:iron_chestplate": ([i, None, i, i, i, i, i, i, i], 1),
        "minecraft:iron_leggings": ([i, i, i, i, None, i, i, None, i], 1),
        "minecraft:iron_boots": ([i, None, i, i, None, i, None, None, None], 1),
        "minecraft:bow": ([None, s, "minecraft:string", s, None, "minecraft:string", None, s, "minecraft:string"], 1),
        "minecraft:arrow": ([None, "minecraft:flint", None, None, s, None, None, "minecraft:feather", None], 4),
        "minecraft:blaze_powder": (["minecraft:blaze_rod", None, None, None], 2),
        "minecraft:ender_eye": (["minecraft:ender_pearl", "minecraft:blaze_powder", None, None], 1),
        # Automation / redstone
        "minecraft:hopper": ([i, None, i, i, "minecraft:chest", i, None, i, None], 1),
        "minecraft:redstone_torch": (["minecraft:redstone", None, s, None], 1),
        "minecraft:lever": ([s, None, "stone", None], 1),
        "minecraft:piston": (["planks", "planks", "planks", "stone", i, "stone", "stone", "minecraft:redstone", "stone"], 1),
        "minecraft:observer": (["stone", "stone", "stone", "minecraft:redstone", "minecraft:redstone", "minecraft:quartz",
                                "stone", "stone", "stone"], 1),
        "minecraft:repeater": ([None, None, None, "minecraft:redstone_torch", "minecraft:redstone",
                                "minecraft:redstone_torch", "minecraft:stone", "minecraft:stone", "minecraft:stone"], 1),
        "minecraft:comparator": ([None, "minecraft:redstone_torch", None, "minecraft:redstone_torch", "minecraft:quartz",
                                  "minecraft:redstone_torch", "minecraft:stone", "minecraft:stone", "minecraft:stone"], 1),
        "minecraft:dispenser": (["stone", "stone", "stone", "stone", "minecraft:bow", "stone", "stone",
                                 "minecraft:redstone", "stone"], 1),
    }
    for log_id, planks in LOG_TO_PLANKS.items():
        r[planks] = ([log_id, None, None, None], 4)
    for wood in WOODS:
        p = f"minecraft:{wood}_planks"
        r[f"minecraft:{wood}_boat"] = ([p, None, p, p, p, p, None, None, None], 1)
        r[f"minecraft:{wood}_door"] = ([p, p, None, p, p, None, p, p, None], 3)
    # Wool from string (spiders: caves, nights) when sheep can't be found.
    r["minecraft:white_wool"] = (["minecraft:string"] * 4, 1)
    g = "minecraft:gold_ingot"
    # Nether and End kit: gold helmet (piglins), brewing, bottles, magma cream.
    r["minecraft:golden_helmet"] = ([g, g, g, g, None, g, None, None, None], 1)
    r["minecraft:brewing_stand"] = ([None, "minecraft:blaze_rod", None, "stone", "stone", "stone",
                                     None, None, None], 1)
    r["minecraft:glass_bottle"] = (["minecraft:glass", None, "minecraft:glass", None, "minecraft:glass", None,
                                    None, None, None], 3)
    r["minecraft:magma_cream"] = (["minecraft:blaze_powder", "minecraft:slime_ball", None, None], 1)
    # Enchanting and anvils.
    d, o, p = "minecraft:diamond", "minecraft:obsidian", "minecraft:paper"
    r["minecraft:paper"] = (["minecraft:sugar_cane"] * 3 + [None] * 6, 3)
    r["minecraft:book"] = ([p, p, None, p, "minecraft:leather", None, None, None, None], 1)
    r["minecraft:enchanting_table"] = ([None, "minecraft:book", None, d, o, d, o, o, o], 1)
    r["minecraft:iron_block"] = (["minecraft:iron_ingot"] * 9, 1)
    r["minecraft:anvil"] = (["minecraft:iron_block"] * 3 + [None, i, None, i, i, i], 1)
    for color in COLORS:
        w = f"minecraft:{color}_wool"
        r[f"minecraft:{color}_bed"] = ([w, w, w, "planks", "planks", "planks", None, None, None], 1)
    for material, tok in MATERIAL_TOKEN.items():
        r[f"minecraft:{material}_pickaxe"] = ([tok, tok, tok, None, s, None, None, s, None], 1)
        r[f"minecraft:{material}_axe"] = ([tok, tok, None, tok, s, None, None, s, None], 1)
        r[f"minecraft:{material}_shovel"] = ([None, tok, None, None, s, None, None, s, None], 1)
        r[f"minecraft:{material}_sword"] = ([None, tok, None, None, tok, None, None, s, None], 1)
        r[f"minecraft:{material}_hoe"] = ([tok, tok, None, None, s, None, None, s, None], 1)
    return r


RECIPES = recipes()

# Block classification for planning (names without the minecraft: prefix).
PASSABLE_SUFFIX = ("_sapling", "torch", "_carpet", "_button", "_pressure_plate", "_sign", "_tulip", "_orchid")
PASSABLE = {"nether_portal", "end_portal", "end_gateway",   # standing in one isn't being buried
            "short_grass", "tall_grass", "fern", "large_fern", "dandelion", "poppy", "wildflowers", "snow", "vine",
            "glow_lichen", "leaf_litter", "bush", "firefly_bush", "short_dry_grass", "tall_dry_grass", "dead_bush",
            "allium", "azure_bluet", "oxeye_daisy", "cornflower", "lily_of_the_valley", "pink_petals", "rail",
            "brown_mushroom", "red_mushroom", "seagrass", "kelp", "redstone_wire", "lever", "cave_air", "ladder"}
HAZARD = {"lava", "water", "fire", "soul_fire", "magma_block", "powder_snow", "pointed_dripstone", "cactus"}
FALLING = {"sand", "red_sand", "gravel", "suspicious_sand", "suspicious_gravel"}
UNBREAKABLE = {"bedrock", "end_portal_frame", "barrier", "spawner"}
PLAYER_MADE_SUFFIX = ("_bed", "_door", "_trapdoor", "chest", "barrel", "furnace", "crafting_table", "torch", "ladder",
                      "hopper", "piston", "observer", "repeater", "comparator", "dispenser", "dropper", "lever")
# Blocks that break quickly without a pickaxe (suffix match on the bare id). Everything else solid needs one.
HAND_MINEABLE_SUFFIX = ("dirt", "sand", "gravel", "grass_block", "clay", "snow", "snow_block", "leaves", "log", "wood",
                        "planks", "mud", "farmland", "dirt_path", "mycelium", "podzol", "soul_soil", "air", "water",
                        "torch", "crafting_table", "_bed", "_door", "ladder", "chest", "wool", "melon", "pumpkin")
PLACEABLE_AS = {"grass_block": "dirt", "dirt_path": "dirt", "farmland": "dirt", "stone": "cobblestone",
                "deepslate": "cobbled_deepslate"}

JUNK = {"minecraft:dirt", "minecraft:gravel", "minecraft:granite", "minecraft:diorite", "minecraft:andesite",
        "minecraft:tuff", "minecraft:wheat_seeds", "minecraft:rotten_flesh", "minecraft:wildflowers",
        "minecraft:dandelion", "minecraft:poppy", "minecraft:short_grass"}
KEEP_BUILDING_BLOCKS = 128
# Inventory hygiene on the spot (no chest needed): stacks never worth a slot, and caps beyond which extra is thrown.
# Only things that are never useful are thrown (thrown items get swept up again by the next collect). Solid
# junk counts as building material instead, and surplus of useful things goes to a chest (deposit / cache).
DISCARD = {"minecraft:tuff_bricks", "minecraft:pointed_dripstone", "minecraft:rotten_flesh",   # seeds feed the farm
           "minecraft:poisonous_potato", "minecraft:wildflowers", "minecraft:dandelion", "minecraft:poppy",
           "minecraft:short_grass", "minecraft:spider_eye"}
EXCESS_CAP = {"minecraft:gravel": 16, "minecraft:sweet_berries": 32, "minecraft:raw_copper": 0}

ARMOR_SLOTS = {"helmet": "head", "chestplate": "chest", "leggings": "legs", "boots": "feet"}
ARMOR_RANK = {"leather": 0, "golden": 1, "chainmail": 2, "iron": 3, "diamond": 4, "netherite": 5}
IRON_COST = {"minecraft:iron_pickaxe": 3, "minecraft:iron_sword": 2, "minecraft:shield": 1, "minecraft:bucket": 3,
             "minecraft:flint_and_steel": 1, "minecraft:iron_helmet": 5, "minecraft:iron_chestplate": 8,
             "minecraft:iron_leggings": 7, "minecraft:iron_boots": 4}

BASE_MARKERS = {
    "bed": [f"{c}_bed" for c in COLORS],
    "crafting_table": ["crafting_table"],
    "chest": ["chest", "barrel"],
    "furnace": ["furnace", "smoker", "blast_furnace"],
    "light": ["torch", "wall_torch", "lantern"],
    "door": [f"{w}_door" for w in WOODS],
}
MARKER_WEIGHT = {"bed": 10, "chest": 5, "furnace": 5, "crafting_table": 4, "door": 3, "light": 1}

# Game clock and movement estimates.
DAY_END = 12500               # beds usable, hostiles spawn
NIGHT_END = 23400
WALK_BLOCKS_PER_TICK = 0.12   # measured on real routes (hills, water, re-plans)
ROUTE_FACTOR = 1.5            # real route length / straight line


# Sky light at or below this means "under rock" — a cave with a distant opening reads 1–3. A world fact, and it
# lives here because the action table needs it: a constant defined in `brain` drags the whole decision layer into
# whatever imports it, and the bench keys its re-runs on exactly that dependency graph.
COVERED_SKY = 4

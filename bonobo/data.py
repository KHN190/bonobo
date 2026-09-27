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
MATERIAL_TOKEN = {"wooden": "planks", "stone": "stone", "iron": "minecraft:iron_ingot", "diamond": "minecraft:diamond"}
TOOL_KINDS = ("pickaxe", "axe", "shovel", "sword", "hoe")     # every kind of tool, in one place
# The tiers a tool is crafted at, and its material: the inverse of TIER_OF_MATERIAL over the craftable materials.
TOOL_MATERIAL_FOR_TIER = {TIER_OF_MATERIAL[m]: m for m in MATERIAL_TOKEN}
ANIMALS = {"minecraft:cow": "beef", "minecraft:pig": "porkchop", "minecraft:sheep": "mutton",
           "minecraft:chicken": "chicken", "minecraft:rabbit": "rabbit"}
COOKED = {f"minecraft:{raw}": f"minecraft:cooked_{raw}" for raw in ANIMALS.values()}
# output item -> input item/group for one furnace operation
SMELTS = {"minecraft:stone": "minecraft:cobblestone", "minecraft:glass": "minecraft:sand",
          "minecraft:iron_ingot": "minecraft:raw_iron", "minecraft:gold_ingot": "minecraft:raw_gold",
          "minecraft:copper_ingot": "minecraft:raw_copper", "minecraft:charcoal": "log",
          **{cooked: raw for raw, cooked in COOKED.items()}}
FOOD = ["cooked_beef", "cooked_porkchop", "cooked_mutton", "cooked_chicken", "cooked_rabbit", "cooked_salmon",
        "cooked_cod", "bread", "baked_potato", "golden_carrot", "apple", "carrot", "sweet_berries", "glow_berries",
        "melon_slice", "cookie"]
# Hunger points one item restores (vanilla), cooked and raw: what a bite is worth against the gap to a full bar.
NUTRITION = {"cooked_beef": 8, "cooked_porkchop": 8, "cooked_mutton": 6, "cooked_chicken": 6, "cooked_rabbit": 5,
             "cooked_salmon": 6, "cooked_cod": 5, "bread": 5, "baked_potato": 5, "golden_carrot": 6, "apple": 4,
             "carrot": 3, "sweet_berries": 2, "glow_berries": 2, "melon_slice": 2, "cookie": 2,
             "beef": 3, "porkchop": 3, "mutton": 2, "chicken": 2, "rabbit": 3}
FULL_BAR = 20

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
    r["minecraft:bread"] = (["minecraft:wheat"] * 3 + [None] * 6, 1)
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
# What memory keeps of what was seen (memory.note_seen / seen), by how fast it changes. One table, one mechanism;
# the clock is game ticks (/state gameTime), never the wall.
#   static  exact position, never expires; retired when we mine it or it is missing on arrival
#   slow    exact position, expires after `ttl` ticks; our own digging near it marks it to-verify
#   mobile  a coarse area (`area`-block cells), expires after `ttl` ticks
#   hostile never stored: perception answers where hostiles are, now
#   here    "standing at one": the arrival note of a kind no other class keeps (memory.note_here), for `at:<kind>`
#           in the next plan, gone in two minutes
#   never   not worth a note: common ore, furniture, anything unknown. Stations and containers have their own
#           records (memory.stations / containers), the only source for them.
# `merge`: notes of one kind closer than this are one note.
VOLATILITY = {
    "static": {"ttl": None, "merge": 1, "area": None},
    "slow": {"ttl": 3 * 24000, "merge": 12, "area": None},
    "mobile": {"ttl": 6000, "merge": 0, "area": 16},
    "here": {"ttl": 2400, "merge": 1, "area": None},
    "hostile": None,
    "never": None,
}
# Rare blocks the travel scan looks for on purpose (the common ones it meets anyway).
RARE_SIGHTINGS = ("diamond_ore", "deepslate_diamond_ore", "obsidian", "ancient_debris")
SEEN_CLASS = dict(
    # Worth remembering for good: rare resources (iron: the mine step's walk is priced from its notes, cost.py)
    # and structures.
    [(k, "static") for k in RARE_SIGHTINGS + (
        "iron_ore", "deepslate_iron_ore", "gold_ore", "deepslate_gold_ore", "nether_gold_ore",
        "village", "fortress", "portal", "nether_portal", "stronghold", "bastion")]
    + [(k, "slow") for k in ("tree", "water", "lava", "wheat", "carrots", "potatoes", "beetroots",
                            "pumpkin", "carved_pumpkin", "melon")]
    + [(k, "mobile") for k in ("herd", "cow", "sheep", "pig", "chicken", "rabbit", "horse", "llama", "goat",
                              "mooshroom", "villager", "piglin")]
    + [(k, "hostile") for k in ("zombie", "husk", "drowned", "skeleton", "stray", "creeper", "spider",
                               "cave_spider", "enderman", "witch", "slime", "phantom", "blaze", "ghast",
                               "wither_skeleton", "magma_cube", "hoglin", "zombified_piglin", "silverfish")])


def seen_class(kind):
    """The volatility class of a kind (a bare block or mob name, or an alias like "tree"). Beds, doors and wool
    — village furniture worth taking — are static; anything else unknown is never noted."""
    kind = bare(kind)
    if kind in SEEN_CLASS:
        return SEEN_CLASS[kind]
    if kind.endswith(("_bed", "_door", "_wool")):
        return "static"
    return "never"


# Step kinds a night under cover can carry on with: no sun, no open ground (brain.plan_proposals, the bed tonight).
NIGHT_WORK = frozenset({"mine", "craft", "smelt"})
HAND_MINEABLE_SUFFIX = ("dirt", "sand", "gravel", "grass_block", "clay", "snow", "snow_block", "leaves", "log", "wood",
                        "planks", "mud", "farmland", "dirt_path", "mycelium", "podzol", "soul_soil", "air", "water",
                        "torch", "crafting_table", "_bed", "_door", "ladder", "chest", "wool", "melon", "pumpkin")
PLACEABLE_AS = {"grass_block": "dirt", "dirt_path": "dirt", "farmland": "dirt", "stone": "cobblestone",
                "deepslate": "cobbled_deepslate"}

JUNK = {"minecraft:dirt", "minecraft:gravel", "minecraft:granite", "minecraft:diorite", "minecraft:andesite",
        "minecraft:tuff", "minecraft:wheat_seeds", "minecraft:rotten_flesh", "minecraft:wildflowers",
        "minecraft:dandelion", "minecraft:poppy", "minecraft:short_grass"}
ARMOR_SLOTS = {"helmet": "head", "chestplate": "chest", "leggings": "legs", "boots": "feet"}
ARMOR_RANK = {"leather": 0, "golden": 1, "chainmail": 2, "iron": 3, "diamond": 4, "netherite": 5}

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

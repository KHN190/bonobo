"""Static game knowledge (Minecraft Java 1.21). Pure data, no I/O."""
import math
from typing import TYPE_CHECKING

from .game import TICKS_PER_S

STATION_R = 8.0         # a station or machine of ours this near is one we have
DOOR_NEAR = 2.0         # a door this near the straight way here → there is on the way

if TYPE_CHECKING:
    from .shapes import Cause, Source

# memoised: tens of millions of calls a session (9 s of a 129 s replay unmemoised)
_MID, _BARE = {}, {}

# what the mod says when the body could not get there — a fact about ways (make one: skills.way_to); api and retry read it
UNREACHABLE = ("unreachable", "not reachable", "no reachable face", "cannot reach", "can't reach", "no path",
               "positions explored", "gave up after", "could not get",
               "cannot hold a stand spot")      # jar ≥ 0.1.48: mining ↔ approaching flipped on one block (MineTask)
_CANNOT_REACH = __import__("re").compile(r"cannot reach (-?\d+), (-?\d+), (-?\d+)")


def memo_ttl(cache, key, ttl, make, now, one=False):
    """cache[key]'s value while younger than `ttl` s (cache: {key: (at, value)}), else `make()` kept with `now`.
    `one`: a single slot — any other key is forgotten when a new value is kept. The one short-lived memo (perception's
    ground, needs' plan prices)."""
    hit = cache.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    value = make()
    if one:
        cache.clear()
    cache[key] = (now, value)
    return value


def cannot_reach(message):
    """Pure: the cells a mod answer names as "cannot reach x, y, z" — {(x, y, z)}, empty when it names none."""
    return {tuple(int(g) for g in m.groups()) for m in _CANNOT_REACH.finditer(message or "")}

def mid(name) -> str:
    """The full id: "oak_planks" → "minecraft:oak_planks". Already-qualified names pass through."""
    got = _MID.get(name)
    if got is None:
        got = _MID[name] = name if ":" in name else "minecraft:" + name
    return got

# a pod on open ground: 4 sides at the feet, 4 at the head, the roof and the cap it is placed against (9 left an opening)
POD_BLOCKS = 10
# terrain facts: how long a read is kept (P1); our own block-changing sends drop it, the game's block events are not read
FACT_TTL_S = {"look": 3.0, "ground": 2.0, "kit": 2.0, "reach": 2.0}
READ_EVERY_S = 0.1     # the fastest useful re-read of the game while waiting on it: two ticks (api waits on it; a fight's estimate charges it per target)

# never thrown whatever a price says (an unpriced diamond went out as junk)
VALUABLES = frozenset(mid(v) for v in (
    "diamond", "emerald", "iron_ingot", "gold_ingot", "copper_ingot", "netherite_ingot", "netherite_scrap",
    "ancient_debris", "raw_iron", "raw_gold", "ender_pearl", "ender_eye", "blaze_rod", "blaze_powder", "obsidian",
    "lapis_lazuli", "redstone", "diamond_block", "emerald_block", "iron_block", "gold_block", "enchanted_book",
    "golden_apple", "nether_star", "shulker_shell", "totem_of_undying"))

def item_ids(tokens):
    """Pure: the jar's item ids for a task's "only" list — a group token (log, planks, wool…) as its members' full ids, an id as itself."""

    out = []
    for t in tokens:
        if t in GROUPS:
            ids = [mid(m) for m in GROUPS[t]]
        elif ":" in t:
            ids = [t]
        else:
            raise ValueError(f"'only' token {t!r} is neither a group nor a namespaced item id")
        out += [i for i in ids if i not in out]
    return out

def living(entities):
    """Pure: what /entities lists less the dead — a living entity at health ≤ 0 still shows while it dies
    (hunt 03:54: a cow the row's setup killed, picked as prey)."""
    return [e for e in entities if e.get("health", 1) > 0]

def bare(name) -> str:
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

TIER_OF_MATERIAL = {"wooden": 0, "golden": 0, "stone": 1, "iron": 2, "diamond": 3, "netherite": 4}
MATERIAL_TOKEN = {"wooden": "planks", "stone": "stone", "iron": "minecraft:iron_ingot", "diamond": "minecraft:diamond"}
TOOL_KINDS = ("pickaxe", "axe", "shovel", "sword", "hoe")     # every kind of tool, in one place
# breaking (Minecraft Wiki, Breaking): a block's hardness; a tool's speed on the blocks its kind is for; a hand is 1
HARDNESS = {"stone": 1.5, "cobblestone": 2.0, "mossy_cobblestone": 2.0, "granite": 1.5, "diorite": 1.5, "andesite": 1.5,
            "tuff": 1.5, "calcite": 0.75, "deepslate": 3.0, "cobbled_deepslate": 3.5, "dirt": 0.5, "coarse_dirt": 0.5,
            "rooted_dirt": 0.5, "grass_block": 0.6, "podzol": 0.5, "mycelium": 0.6, "mud": 0.5, "farmland": 0.6,
            "dirt_path": 0.65, "sand": 0.5, "red_sand": 0.5, "gravel": 0.6, "clay": 0.6, "soul_sand": 0.5,
            "soul_soil": 0.5, "snow": 0.1, "snow_block": 0.2, "sandstone": 0.8, "red_sandstone": 0.8,
            "smooth_sandstone": 2.0, "netherrack": 0.4, "basalt": 1.25, "blackstone": 1.5, "end_stone": 3.0,
            "obsidian": 50.0, "crying_obsidian": 50.0, "ancient_debris": 30.0, "ice": 0.5, "packed_ice": 0.5,
            "glass": 0.3, "crafting_table": 2.5, "chest": 2.5, "barrel": 2.5, "furnace": 3.5, "bookshelf": 1.5,
            "cobweb": 4.0, "terracotta": 1.25, "bricks": 2.0, "stone_bricks": 1.5, "melon": 1.0, "pumpkin": 1.0,
            "hay_block": 0.5}
HARDNESS_SUFFIX = (("_log", 2.0), ("_wood", 2.0), ("_planks", 2.0), ("_leaves", 0.2), ("_wool", 0.8),
                   ("_terracotta", 1.25), ("_concrete", 1.8), ("_glass", 0.3), ("_ore", 3.0))
DEEPSLATE_ORE_HARDNESS = 4.5
UNLISTED_HARDNESS = 1.5        # a block not listed is priced as stone
TOOL_SPEED = {"wooden": 2.0, "stone": 4.0, "iron": 6.0, "diamond": 8.0, "netherite": 9.0, "golden": 12.0}
# (item kind, block suffix) → speed where no tool kind is the block's (shears on leaves, a sword on a cobweb)
SPECIAL_SPEED = {("shears", "cobweb"): 15.0, ("shears", "leaves"): 15.0, ("shears", "wool"): 5.0,
                 ("sword", "cobweb"): 15.0, ("sword", "leaves"): 1.5}
DROP_KINDS = {"cobweb": ("shears", "sword")}     # blocks that drop only to these item kinds (no pickaxe block)
HOE_BLOCKS = ("leaves", "hay_block", "moss_block", "sponge", "target", "sculk")
BREAK_DIVISOR = {True: 30, False: 100}     # per tick: speed / hardness / this (the tool is right for the drop, or not)
# attacking (Minecraft Wiki, Damage): a weapon's damage and attacks per second; the hand 1 and 4
WEAPON_DAMAGE = {"sword": {"wooden": 4, "golden": 4, "stone": 5, "iron": 6, "diamond": 7, "netherite": 8},
                 "axe": {"wooden": 7, "golden": 7, "stone": 9, "iron": 9, "diamond": 9, "netherite": 10}}
ATTACKS_PER_S = {"sword": {m: 1.6 for m in TOOL_SPEED},
                 "axe": {"wooden": 0.8, "golden": 1.0, "stone": 0.8, "iron": 0.9, "diamond": 1.0, "netherite": 1.0}}
HAND_DAMAGE, HAND_ATTACKS_PER_S = 1, 4.0

def weapon_hit(item):
    """Pure: (damage, hits/s) of `item`; not a sword or axe: the hand."""
    material, _, kind = ("" if item in (None, "hand") else bare(item)).rpartition("_")
    if kind not in WEAPON_DAMAGE or material not in WEAPON_DAMAGE[kind]:
        return float(HAND_DAMAGE), float(HAND_ATTACKS_PER_S)
    return float(WEAPON_DAMAGE[kind][material]), float(ATTACKS_PER_S[kind][material])
# The tiers a tool is crafted at, and its material: the inverse of TIER_OF_MATERIAL over the craftable materials.
# piglin bartering (data/minecraft/loot_table/gameplay/piglin_bartering.json, 1.21.11): item → (weight, the pool's
# total weight, count min, count max) — one gold ingot a barter
PIGLIN_BARTER = {"minecraft:ender_pearl": (10, 469, 2, 4)}
# what a built machine provides, by the tag its blueprint carries
MACHINE_PROVIDES = {"smelting": "minecraft:furnace", "crafting": "minecraft:crafting_table"}
# the dimensions by the game's ids
OVERWORLD, NETHER, THE_END = "minecraft:overworld", "minecraft:the_nether", "minecraft:the_end"
TOOL_MATERIAL_FOR_TIER = {TIER_OF_MATERIAL[m]: m for m in MATERIAL_TOKEN}
ANIMALS = {"minecraft:cow": "beef", "minecraft:pig": "porkchop", "minecraft:sheep": "mutton",
           "minecraft:chicken": "chicken", "minecraft:rabbit": "rabbit"}
COOKED = {f"minecraft:{raw}": f"minecraft:cooked_{raw}" for raw in ANIMALS.values()}
# output item -> input item/group for one furnace operation
SMELTS = {"minecraft:stone": "minecraft:cobblestone", "minecraft:glass": "minecraft:sand",
          "minecraft:iron_ingot": "minecraft:raw_iron", "minecraft:gold_ingot": "minecraft:raw_gold",
          "minecraft:copper_ingot": "minecraft:raw_copper", "minecraft:charcoal": "log",
          **{cooked: raw for raw, cooked in COOKED.items()}}
# the one food table: hunger points per item, best first, raw last; FOOD, RAW and bite sizes read from here
NUTRITION = {"cooked_beef": 8, "cooked_porkchop": 8, "cooked_mutton": 6, "cooked_chicken": 6, "cooked_rabbit": 5,
             "cooked_salmon": 6, "cooked_cod": 5, "bread": 5, "baked_potato": 5, "golden_carrot": 6, "apple": 4,
             "carrot": 3, "sweet_berries": 2, "glow_berries": 2, "melon_slice": 2, "cookie": 2,
             "beef": 3, "porkchop": 3, "mutton": 2, "chicken": 2, "rabbit": 3}
RAW = ("beef", "porkchop", "mutton", "chicken", "rabbit")
FOOD = [f for f in NUTRITION if f not in RAW]
FULL_BAR = 20
MAX_HP = 20.0

# interchangeable items; "any X" recipes use the group name
GROUPS = {
    "log": list(LOG_TO_PLANKS),
    "planks": sorted(set(LOG_TO_PLANKS.values())),
    "stone": ["minecraft:cobblestone", "minecraft:cobbled_deepslate", "minecraft:blackstone"],
    "coal": ["minecraft:coal", "minecraft:charcoal"],
    "wool": [f"minecraft:{c}_wool" for c in COLORS],
    "bed": [f"minecraft:{c}_bed" for c in COLORS],
    "boat": [f"minecraft:{w}_boat" for w in WOODS],
    "door": [f"minecraft:{w}_door" for w in WOODS],
    # anything solid we'd otherwise throw away is building material
    "building": ["minecraft:andesite", "minecraft:diorite", "minecraft:granite", "minecraft:tuff",
                 "minecraft:dripstone_block", "minecraft:calcite", "minecraft:dirt", "minecraft:cobbled_deepslate",
                 "minecraft:cobblestone", "minecraft:blackstone"],
    "food": list(FOOD),
}

def recipes() -> dict:
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
    r.update(vanilla_recipes())
    return r

def vanilla_recipes():
    """The recipes copied from the 1.21.11 jar (vanilla/recipe), in recipes()' form."""
    import json
    import os
    out = {}
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vanilla", "recipe")
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            got = json.load(f)
        if got["type"] != "minecraft:crafting_shapeless":
            raise ValueError(f"{name}: recipe type {got['type']} not read")
        cells = list(got["ingredients"])
        out[got["result"]["id"]] = (cells + [None] * ((4 if len(cells) <= 4 else 9) - len(cells)), got["result"]["count"])
    return out

RECIPES = recipes()

# Block classification for planning (names without the minecraft: prefix).
PASSABLE_SUFFIX = ("_sapling", "torch", "_carpet", "_button", "_pressure_plate", "_sign", "_tulip", "_orchid")
# solid, yet a head inside does not suffocate: not a full cube
PARTIAL_SUFFIX = ("_slab", "_stairs", "snow")
OPEN_PROP = "open"         # the block state a door, trapdoor or gate stands open by (a barrel has one too)
DOOR_SUFFIX = ("_door", "_trapdoor", "_fence_gate")     # what opens to let a body through: no full cube, never a wall


def is_door(name):
    """Pure: a door, trapdoor or fence gate (bare or namespaced name) — not every block with an `open` state."""
    return str(name).endswith(DOOR_SUFFIX)
PASSABLE = {"nether_portal", "end_portal", "end_gateway",   # standing in one isn't being buried
            "short_grass", "tall_grass", "fern", "large_fern", "dandelion", "poppy", "wildflowers", "snow", "vine",
            "glow_lichen", "leaf_litter", "bush", "firefly_bush", "short_dry_grass", "tall_dry_grass", "dead_bush",
            "allium", "azure_bluet", "oxeye_daisy", "cornflower", "lily_of_the_valley", "pink_petals", "rail",
            "brown_mushroom", "red_mushroom", "seagrass", "kelp", "redstone_wire", "lever", "cave_air", "ladder"}
HAZARD = {"lava", "water", "fire", "soul_fire", "magma_block", "powder_snow", "pointed_dripstone", "cactus"}
UNBREAKABLE = {"bedrock", "end_portal_frame", "barrier", "spawner"}
PLAYER_MADE_SUFFIX = ("_bed", "_door", "_trapdoor", "chest", "barrel", "furnace", "crafting_table", "torch", "ladder",
                      "hopper", "piston", "observer", "repeater", "comparator", "dispenser", "dropper", "lever")
DAY_TICKS = 24000
# what memory keeps of a sighting, by how fast it changes (game ticks): static, slow (ttl), mobile (coarse area), hostile (never), here (two minutes, for at:<kind>), never; `merge` joins close notes, `absent` is how long "looked, none here" holds
VOLATILITY = {
    "static": {"ttl": None, "merge": 1, "area": None, "absent": 2 * DAY_TICKS},
    "slow": {"ttl": 3 * DAY_TICKS, "merge": 12, "area": None, "absent": DAY_TICKS},
    "mobile": {"ttl": 6000, "merge": 0, "area": 16, "absent": 2400},
    "here": {"ttl": 2400, "merge": 1, "area": None, "absent": 2400},
    "hostile": None,
    "never": None,
}
# Rare blocks the travel scan looks for on purpose (the common ones it meets anyway).
RARE_SIGHTINGS = ("diamond_ore", "deepslate_diamond_ore", "obsidian", "ancient_debris")
SEEN_CLASS = dict(
    # rare resources and structures are remembered for good
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
    """The volatility class of a kind (a bare block or mob name, or an alias like "tree")."""

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

ARMOR_SLOTS = {"helmet": "head", "chestplate": "chest", "leggings": "legs", "boots": "feet"}
ARMOR_RANK = {"leather": 0, "golden": 1, "chainmail": 2, "iron": 3, "diamond": 4, "netherite": 5}
# defense points per piece (Minecraft Wiki, Armor)
ARMOR_POINTS = {"leather": {"helmet": 1, "chestplate": 3, "leggings": 2, "boots": 1},
                "golden": {"helmet": 2, "chestplate": 5, "leggings": 3, "boots": 1},
                "chainmail": {"helmet": 2, "chestplate": 5, "leggings": 4, "boots": 1},
                "iron": {"helmet": 2, "chestplate": 6, "leggings": 5, "boots": 2},
                "diamond": {"helmet": 3, "chestplate": 8, "leggings": 6, "boots": 3},
                "netherite": {"helmet": 3, "chestplate": 8, "leggings": 6, "boots": 3}}

BASE_MARKERS = {
    "bed": [f"{c}_bed" for c in COLORS],
    "crafting_table": ["crafting_table"],
    "chest": ["chest", "barrel"],
    "furnace": ["furnace", "smoker", "blast_furnace"],
    "light": ["torch", "wall_torch", "lantern"],
    "door": [f"{w}_door" for w in WOODS],
}
MARKER_WEIGHT = {"bed": 10, "chest": 5, "furnace": 5, "crafting_table": 4, "door": 3, "light": 1}

# a home's parts, found by the scan: block-name suffixes (a bed is two cells, a chest one or two)
HOME_BEDS = ("_bed",)
HOME_CHESTS = ("chest", "barrel")
HOME_STATIONS = ("crafting_table", "furnace", "blast_furnace", "smoker", "anvil", "chipped_anvil", "damaged_anvil",
                 "smithing_table", "stonecutter", "grindstone", "enchanting_table")


def home_part_kind(name):
    """Pure: "beds", "chests" or "stations" for a block (bare name) a home keeps as a part, else None."""
    if name.endswith(HOME_BEDS):
        return "beds"
    if name.endswith(HOME_CHESTS) and "ender" not in name:
        return "chests"
    return "stations" if name in HOME_STATIONS else None


def in_box(box, p):
    """Pure: cell `p` inside `box` ((lo, hi), inclusive)."""
    lo, hi = box
    return all(min(lo[i], hi[i]) <= p[i] <= max(lo[i], hi[i]) for i in range(3))


def home_box_of(boxes, p) -> tuple | None:
    """Pure: the box of `boxes` (a home's) the cell of point `p` lies in, or None."""
    cell = tuple(math.floor(v) for v in p)
    return next((b for b in boxes if in_box(b, cell)), None)



def placed_cell(task, feet):
    """Pure: the cell a place task fills — its own, or the feet's for a pillar; None when unknown."""
    if "x" in task:
        return (int(task["x"]), int(task["y"]), int(task["z"]))
    return tuple(int(v) for v in feet) if feet is not None else None


def home_may_hold(item):
    """Pure: may `item` be placed inside a home — a part it keeps (station, container, bed) or a light."""
    name = bare(item) if item else ""
    return home_part_kind(name) is not None or name in BASE_MARKERS["light"]

DAY_END = 12500               # beds usable, hostiles spawn
NIGHT_END = 23400

def is_night(time_of_day, dimension="minecraft:overworld"):
    """Pure: night; the Overworld's only."""
    return dimension == "minecraft:overworld" and DAY_END <= time_of_day % DAY_TICKS <= NIGHT_END

REACH = 4.5            # the jar's block interaction range (survival: getBlockInteractionRange)
HOLD_MARGIN = 0.5      # the jar's MineTask.holds works within the reach less this
WORK_REACH = REACH - HOLD_MARGIN     # how far a block is worked from a stand (holds; fluids' fill spot)
FALLING = {"sand", "red_sand", "gravel", "suspicious_sand", "suspicious_gravel"}   # fall when the cell below opens
FALLING_SUFFIX = "_concrete_powder"
def is_falling(name):
    """Pure: a block that falls into the cell under it once that cell opens."""
    n = bare(name or "")
    return n in FALLING or n.endswith(FALLING_SUFFIX)

ANIMAL_HP = {"minecraft:cow": 10, "minecraft:pig": 10, "minecraft:sheep": 8, "minecraft:chicken": 4,
             "minecraft:rabbit": 3}      # Minecraft Wiki: each animal's health (a hunt's work)
# a bed refuses sleep while one of these is within BED_BOX of it, through walls (1.21.11 ServerPlayerEntity.trySleep:
# every HostileEntity subclass, isAngryAt true; ZombifiedPiglinEntity only when angry — SLEEP_BLOCKERS_ANGRY)
SLEEP_BLOCKERS = {f"minecraft:{n}" for n in (
    "piglin", "piglin_brute", "skeleton", "bogged", "blaze", "breeze", "cave_spider", "creaking", "creeper", "drowned",
    "elder_guardian", "enderman", "endermite", "evoker", "giant", "guardian", "husk", "illusioner", "parched",
    "pillager", "ravager", "silverfish", "spider", "stray", "vex", "vindicator", "warden", "witch", "wither",
    "wither_skeleton", "zoglin", "zombie", "zombie_villager")}
SLEEP_BLOCKERS_ANGRY = {"minecraft:zombified_piglin"}
BED_BOX = (8.0, 5.0, 8.0)        # trySleep's monster box: the bed's bottom centre ± these
BED_REACH = (3.0, 2.0, 3.0)      # trySleep's isBedWithinRange: the player within these of the bed's bottom centre
TORCH_LIGHT = 14                 # a torch's block light (Minecraft Wiki, Light: torch 14); one less per block away
REPAIR_BONUS_PARTS = 20          # combining two tools adds 1/this of the max durability
DEEPSLATE_TOP = 0        # below this y the overworld's rock is deepslate
# overworld soil over the rock, in blocks, where the column under the feet is not read (knowledge.soil_depth):
# a prior — worldgen's surface rule lays dirt under the grass a few blocks deep — not a measurement
SOIL_DEPTH = 4
# the sight depth from a level stand: the deepest an open block beside it is held from it (nav.holds over an open
# shaft: the rim hides a deeper one; test_plan_way.LevelReach). A fact of the reach, not a choice of way: plan_way
# chooses by seconds; where no region is read (cost, gather) a deeper one is priced as a staircase
LEVEL_SIGHT_DEPTH = 2
STAIR_CELLS = 3          # cells one staircase step clears: feet, head and the head room the walk down passes
BAN_MAX_S = 600          # the longest any cell stays banned, however often it failed
TASK_WAIT_S = 900
NAV_NODES = 6000
WALK_BLOCKS_PER_TICK = 0.12   # measured on real routes (hills, water, re-plans)
WALK_BLOCKS_PER_S = WALK_BLOCKS_PER_TICK * TICKS_PER_S
ROUTE_FACTOR = 1.5            # real route length / straight line
MEASURED_BAND = 4.0           # a run moves its average, and a measured price strays from its prior, at most this factor


# every exception an attempt can end in, by class name (this module imports nothing): (cause it is counted and
# cooled under, interrupt source — arbiter.RESUME_OF says what that source means: resumed, or failed)
EXCEPTIONS: "dict[str, tuple[Cause, Source]]" = {
    "Exception": ("error", "crash"),                       # a bug of ours: anything not declared below
    "McError": ("error", "stuck"),                         # a mod task failed (its text may say "nav": cause_of)
    "GameUnreachable": ("game", "game lost"),
    "NotAvailable": ("unavailable", "stuck"), "NavFailed": ("nav", "stuck"), "Unreachable": ("nav", "stuck"),
    "TaskStuck": ("stuck", "stuck"),
    "ToolMissing": ("tool", "stuck"), "NeedMissing": ("tool", "stuck"),
    "StationMissing": ("replan", "stuck"),                 # the plan counted on a station that is gone
    "CommitmentExpired": ("replan", "layer:plan"),         # the plan grew stale: nothing failed
    "Interrupted": ("interrupt", "layer:safety"), "NightFell": ("interrupt", "night"),
    "PlayerTookControl": ("interrupt", "player"), "FightHolds": ("interrupt", "layer:tactic"),
    "BodyContested": ("interrupt", "manual"), "Died": ("interrupt", "death"),
    "DimensionChanged": ("interrupt", "dimension change"),
    "Unplannable": ("error", "stuck"), "Dearer": ("error", "stuck"), "Unsolvable": ("error", "crash"), "ReplayMiss": ("error", "crash"),
    "SetupInvalid": ("error", "crash"),
}

ITEM_DESPAWN_S = 300      # a dropped item despawns after 5 minutes (6000 ticks)

# The chance a search finds a kind never seen, by how the game makes it; kinds not here use play.toml's exists_prior.
# Minecraft Wiki, "Mob spawning": grassland passive weights sheep 12, pig 10, chicken 10, cow 8, rabbit 4.
PASSIVE_WEIGHT = {"sheep": 12, "pig": 10, "chicken": 10, "cow": 8, "rabbit": 4}
# Minecraft Wiki, "Village": one village per 34×34-chunk region; a search covers SEARCH_LEGS looks of SEARCH_LOOK_R.
VILLAGE_REGION_BLOCKS = 34 * 16
SEARCH_LEGS, SEARCH_LOOK_R = 6, 48
VILLAGE_P = min(1.0, SEARCH_LEGS * __import__("math").pi * SEARCH_LOOK_R ** 2 / VILLAGE_REGION_BLOCKS ** 2)
VILLAGE_ONLY = ([f"{c}_bed" for c in COLORS] + [f"{c}_wool" for c in COLORS] + ["villager", "hay_block", "bell",
                "smithing_table", "stonecutter", "cauldron", "bookshelf", "wheat", "carrots", "potatoes", "beetroots"])
FIND_P = {**{k: w / max(PASSIVE_WEIGHT.values()) for k, w in PASSIVE_WEIGHT.items()},
          **{k: VILLAGE_P for k in VILLAGE_ONLY}}

CRITICAL_HP = 4            # health at or below which danger overrides everything (hazard's "critical")
CRITICAL_HP_END = 12       # in the End: a breath or head butt takes 10+


def critical_hp(state) -> float:
    """Pure: the critical-health floor where the body stands."""
    return CRITICAL_HP_END if state.get("dimension") == "minecraft:the_end" else CRITICAL_HP

# published durability (uses) per material
TOOL_USES = {"wooden": 59, "stone": 131, "iron": 250, "diamond": 1561, "netherite": 2031, "golden": 32}


# finished goods the world already holds (village beds, furnaces…), so "take that one" competes with "craft one"; no theft price — the game has none
TAKEABLE = {
    "bed": {"blocks": [f"{c}_bed" for c in COLORS], "gives": {"bed": 1}, "tool": None, "break_s": 1.0},
    "wool": {"blocks": [f"{c}_wool" for c in COLORS], "gives": {"wool": 1}, "tool": None, "break_s": 1.2},
    "minecraft:crafting_table": {"blocks": ["crafting_table"], "gives": {"minecraft:crafting_table": 1},
                                 "tool": None, "break_s": 2.5},
    "minecraft:furnace": {"blocks": ["furnace", "blast_furnace", "smoker"],
                          "gives": {"minecraft:furnace": 1}, "tool": ("pickaxe", 0), "break_s": 5.5},
    "minecraft:chest": {"blocks": ["chest", "barrel"], "gives": {"minecraft:chest": 1}, "tool": None,
                        "break_s": 3.0},
    "minecraft:cauldron": {"blocks": ["cauldron"], "gives": {"minecraft:cauldron": 1}, "tool": ("pickaxe", 0),
                           "break_s": 6.0},
    "door": {"blocks": [f"{w}_door" for w in WOODS], "gives": {"door": 1}, "tool": None, "break_s": 3.0},
    "minecraft:ladder": {"blocks": ["ladder"], "gives": {"minecraft:ladder": 1}, "tool": None, "break_s": 0.6},
    "minecraft:torch": {"blocks": ["torch", "wall_torch"], "gives": {"minecraft:torch": 1}, "tool": None,
                        "break_s": 0.3},
    "minecraft:bookshelf": {"blocks": ["bookshelf"], "gives": {"minecraft:book": 3}, "tool": None, "break_s": 2.3},
    "minecraft:smithing_table": {"blocks": ["smithing_table"], "gives": {"minecraft:smithing_table": 1},
                                 "tool": None, "break_s": 3.8},
    "minecraft:stonecutter": {"blocks": ["stonecutter"], "gives": {"minecraft:stonecutter": 1},
                              "tool": ("pickaxe", 0), "break_s": 5.5},
    "minecraft:hay_block": {"blocks": ["hay_block"], "gives": {"minecraft:wheat": 9}, "tool": None,
                            "break_s": 1.2},
    "minecraft:wheat": {"blocks": ["wheat"], "gives": {"minecraft:wheat": 1, "minecraft:wheat_seeds": 1},
                        "tool": None, "break_s": 0.4},
    "minecraft:carrot": {"blocks": ["carrots"], "gives": {"minecraft:carrot": 3}, "tool": None, "break_s": 0.4},
    "minecraft:potato": {"blocks": ["potatoes"], "gives": {"minecraft:potato": 3}, "tool": None, "break_s": 0.4},
    "minecraft:beetroot": {"blocks": ["beetroots"], "gives": {"minecraft:beetroot": 1}, "tool": None,
                           "break_s": 0.4},
    "minecraft:pumpkin": {"blocks": ["pumpkin", "carved_pumpkin"], "gives": {"minecraft:pumpkin": 1},
                          "tool": None, "break_s": 1.5},
    "minecraft:melon_slice": {"blocks": ["melon"], "gives": {"minecraft:melon_slice": 5}, "tool": None,
                              "break_s": 1.5},
}


HUNT_YIELD = {"minecraft:beef": 2, "minecraft:porkchop": 2, "minecraft:mutton": 1.5, "minecraft:chicken": 1,
              "minecraft:rabbit": 1, "wool": 1, "minecraft:leather": 1, "minecraft:feather": 1,
              "minecraft:string": 1, "minecraft:ender_pearl": 0.5, "minecraft:blaze_rod": 0.5}


# items per block broken (average)
MINE_YIELD = {"minecraft:flint": 0.12, "minecraft:redstone": 4.5, "minecraft:lapis_lazuli": 6,
              "minecraft:wheat_seeds": 0.125}

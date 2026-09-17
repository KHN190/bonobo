"""Where things come from: the requirement graph the planner resolves (recipes, smelting, mining, hunting)."""
from .data import COLORS, FOOD, GROUPS, RECIPES, SMELTS, WOODS, mid

# Group-level recipes: output type follows the input variant (spruce logs → spruce planks, white wool → white bed).
# The craft skill resolves each group token to ONE owned member with enough items.
GROUP_RECIPES = {
    "planks": (["log", None, None, None], 4),
    "boat": (["planks", None, "planks", "planks", "planks", "planks", None, None, None], 1),
    "door": (["planks", "planks", None, "planks", "planks", None, "planks", "planks", None], 3),
    "bed": (["wool", "wool", "wool", "planks", "planks", "planks", None, None, None], 1),
}

# item -> (block names to break, minimum pickaxe tier or None if no tool needed)
MINE = {
    "minecraft:raw_iron": (["iron_ore", "deepslate_iron_ore"], 1),
    "minecraft:coal": (["coal_ore", "deepslate_coal_ore"], 0),
    "minecraft:raw_copper": (["copper_ore", "deepslate_copper_ore"], 1),
    "minecraft:raw_gold": (["gold_ore", "deepslate_gold_ore"], 2),
    "minecraft:diamond": (["diamond_ore", "deepslate_diamond_ore"], 2),
    "minecraft:redstone": (["redstone_ore", "deepslate_redstone_ore"], 2),
    "minecraft:lapis_lazuli": (["lapis_ore", "deepslate_lapis_ore"], 1),
    "minecraft:cobblestone": (["stone"], 0),  # natural stone only: cobblestone blocks are usually someone's wall
    "minecraft:cobbled_deepslate": (["deepslate"], 0),
    "minecraft:flint": (["gravel"], None),
    "minecraft:gravel": (["gravel"], None),
    "minecraft:dirt": (["dirt", "grass_block"], None),
    "minecraft:sand": (["sand"], None),
    "minecraft:obsidian": (["obsidian"], 3),
    "minecraft:quartz": (["nether_quartz_ore"], 0),   # Nether only
    "minecraft:wheat_seeds": (["short_grass", "tall_grass"], None),   # grass drops seeds (~1 in 8)
    "minecraft:nether_wart": (["nether_wart"], None),     # grows in fortress soul sand gardens
    "minecraft:sugar_cane": (["sugar_cane"], None),       # by water: paper → books → enchanting table
}
# items per block broken (average)
MINE_YIELD = {"minecraft:flint": 0.12, "minecraft:redstone": 4.5, "minecraft:lapis_lazuli": 6,
              "minecraft:wheat_seeds": 0.125}

HUNT = {
    "minecraft:beef": ["minecraft:cow"], "minecraft:porkchop": ["minecraft:pig"],
    "minecraft:mutton": ["minecraft:sheep"], "minecraft:chicken": ["minecraft:chicken"],
    "minecraft:rabbit": ["minecraft:rabbit"], "wool": ["minecraft:sheep"], "minecraft:leather": ["minecraft:cow"],
    "minecraft:feather": ["minecraft:chicken"], "minecraft:string": ["minecraft:spider"],
    "minecraft:ender_pearl": ["minecraft:enderman"], "minecraft:blaze_rod": ["minecraft:blaze"],
    "minecraft:slime_ball": ["minecraft:slime"],
}
HUNT_YIELD = {"minecraft:beef": 2, "minecraft:porkchop": 2, "minecraft:mutton": 1.5, "minecraft:chicken": 1,
              "minecraft:rabbit": 1, "wool": 1, "minecraft:leather": 1, "minecraft:feather": 1,
              "minecraft:string": 1, "minecraft:ender_pearl": 0.5, "minecraft:blaze_rod": 0.5}

# Things the world has already MADE. A village is a bag of finished goods — beds, furnaces, tables, chests, hay —
# and the planner could not say "take that one", only "craft one", so it spent mornings shearing sheep next to a
# row of beds. One row per thing worth carrying away:
#
#   blocks   what to look for, as the world names it to `find` (bare, no namespace; variants included)
#   gives    what ends up in the bag, in tokens the rest of the planner already knows
#   tool     (kind, tier) needed for the block to drop anything, or None for bare hands
#   break_s  seconds to break it, once we are standing there (the walk is priced separately)
#
# Breaking these costs nothing socially: villagers take offence at trades and at hurting their golem, not at a
# missing bed. So there is no theft price here — that would be a belief about a rule the game does not have.
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


def takeable_blocks():
    """Every block worth walking over to break, flat — one list for the travel scan and the resource map."""
    return sorted({b for row in TAKEABLE.values() for b in row["blocks"]})


# Stations are required by a step but not consumed.
STATIONS = {"minecraft:crafting_table", "minecraft:furnace"}

# Cooked food the planner may choose from (cheapest reachable animal wins).
COOKABLE_FOOD = ["minecraft:cooked_porkchop", "minecraft:cooked_beef", "minecraft:cooked_mutton",
                 "minecraft:cooked_chicken", "minecraft:cooked_rabbit"]
ALL_FOOD = [mid(f) for f in FOOD]

# Step kinds that are safe underground / at night.
UNDERGROUND_KINDS = {"craft", "smelt", "mine"}

TOOL_MATERIAL_FOR_TIER = {0: "wooden", 1: "stone", 2: "iron", 3: "diamond"}


def members(token):
    if token == "food":
        return ALL_FOOD
    return GROUPS.get(token, [mid(token)])


def source(token):
    """How a token is produced: ('craft', pattern, out) | ('smelt', input) | ('mine', blocks, tier) |
    ('hunt', types) | ('gather',) | None."""
    if token == "log":
        return ("gather",)
    if token in ("stone", "building"):
        return ("mine",) + MINE["minecraft:cobblestone"]
    if token in GROUP_RECIPES:
        pattern, out = GROUP_RECIPES[token]
        return ("craft", pattern, out)
    if token == "stone":
        return ("mine",) + MINE["minecraft:cobblestone"]
    if token == "coal":
        return ("mine",) + MINE["minecraft:coal"]
    if token in HUNT:
        return ("hunt", HUNT[token])
    item = mid(token)
    if item in HUNT:
        return ("hunt", HUNT[item])
    if item in SMELTS and item != "minecraft:charcoal":
        return ("smelt", SMELTS[item])
    if item in RECIPES:
        pattern, out = RECIPES[item]
        return ("craft", pattern, out)
    if item in MINE:
        return ("mine",) + MINE[item]
    if item == "minecraft:water_bucket":
        # Filling is its own kind of production: an empty bucket plus a water source. Without it the planner called
        # every water-dependent goal "no known way to obtain minecraft:water_bucket".
        return ("fill", "minecraft:bucket")
    return None

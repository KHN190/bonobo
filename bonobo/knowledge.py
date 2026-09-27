"""Where things come from: the requirement graph the planner resolves (recipes, smelting, mining, hunting)."""
from .data import COLORS, FOOD, GROUPS, NUTRITION, RAW, RECIPES, SMELTS, WOODS, bare, mid

# Group-level recipes: output type follows the input variant (spruce logs → spruce planks, white wool → white bed).
# The craft skill resolves each group token to ONE owned member with enough items.
GROUP_RECIPES = {
    "planks": (["log", None, None, None], 4),
    "boat": (["planks", None, "planks", "planks", "planks", "planks", None, None, None], 1),
    "door": (["planks", "planks", None, "planks", "planks", None, "planks", "planks", None], 3),
    "bed": (["wool", "wool", "wool", "planks", "planks", "planks", None, None, None], 1),
}

# Optional tools a skill runs faster with (`@skill(speed=...)`): seconds saved per unit of work, against bare hands.
# A tool is made only when making it takes less than it saves.
CHOP_AXE_S = 1.5         # a log: ~3 s by hand, ~1.5 s with a wooden axe
HUNT_SWORD_S = 3.0       # a kill: a cow takes ten fist hits, four with a wooden sword
DIG_SHOVEL_S = 0.35      # a block of dirt, sand or gravel: 0.75 s by hand, 0.4 s with a wooden shovel
SKILL_SPEED = {}         # skill name → its declared speed, filled by the `skill` decorator (the planner's view)
STEP_SKILL = {"gather": "chop", "hunt": "hunt", "mine": "mine"}     # which skill carries out a planned step kind

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
# What each animal is bred with (two of them, one each).
BREED_FOOD = {"minecraft:cow": "minecraft:wheat", "minecraft:sheep": "minecraft:wheat",
              "minecraft:pig": "minecraft:carrot", "minecraft:chicken": "minecraft:wheat_seeds"}
# A wheat plot (farming.plant_farm): 8 cells sown around one water source; what one harvest brings.
PLOT_CELLS = 8
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
# Raw meat: food that wants cooking — eaten raw only when starving, counted as the next meal while cooked is short.
RAW_MEAT = [mid(f) for f in RAW]

# One definition of "enough food for the Nether trip". Six, not twelve: a speedrun crosses on a handful of steaks,
# while twelve cooked items means a dozen kills plus smelting.
KIT_FOOD = 6
# Beds carried into the End. Human runners take 8–10 and call five the bare minimum: each perch window is worth one
# or two blasts, and a wasted bed must not end the fight.
DRAGON_BEDS = 8


def food_count(inv):
    """The one definition of 'food carried': cooked/ready food only (raw meat must be cooked first)."""
    return sum(inv.count(f) for f in ALL_FOOD)


def food_points(inv):
    """Hunger points the ready food carried restores, from the one table (data.NUTRITION)."""
    return sum(inv.count(mid(f)) * NUTRITION[f] for f in FOOD)


def nether_kit_missing(inv):
    """Pure: what a Nether trip still lacks (empty = ready): cooked food, building blocks for bridges/shelter, a gold
    helmet for piglins, bag room for the loot. No bow required (first trip: shield + melee)."""
    missing = []
    if food_count(inv) < KIT_FOOD:
        missing.append(f"food {food_count(inv)}/{KIT_FOOD}")
    if inv.count("building") < 32:
        missing.append(f"blocks {inv.count('building')}/32")
    if not (inv.count("minecraft:golden_helmet") or bare(inv.worn("head") or "") == "golden_helmet"):
        missing.append("gold helmet")
    # Two free slots for the first loot: a stricter target flickered with every pickup.
    if 36 - inv.used_slots() < 2:
        missing.append(f"bag room {36 - inv.used_slots()}/2 free")
    return missing


def kit_needs(inv):
    """Planner needs that close the kit's gaps."""
    needs = []
    if food_count(inv) < KIT_FOOD:
        needs.append(("food", KIT_FOOD))     # the same constant the readiness check uses
    if inv.count("building") < 32:
        needs.append(("stone", 32))
    if not (inv.count("minecraft:golden_helmet") or bare(inv.worn("head") or "") == "golden_helmet"):
        needs.append(("minecraft:golden_helmet", 1))
    return needs


# Where to look when nothing is known nearby: the height band a kind is richest in (None = the surface). The one
# fixed table the brain consults before spiralling out (`explore`).
FIND_AT = {
    "minecraft:raw_iron": 16, "minecraft:coal": 48, "minecraft:raw_copper": 48, "minecraft:raw_gold": -16,
    "minecraft:diamond": -58, "minecraft:redstone": -58, "minecraft:lapis_lazuli": 0,
    "log": None, "minecraft:sand": None, "minecraft:clay_ball": None, "food": None,
}


# Every block kind the cost model asks "how far is the nearest" about: one scan per round answers them all
# (world.nearest over this union).
SOURCE_BLOCKS = sorted({b for blocks, _tier in MINE.values() for b in blocks} | set(GROUPS["log"])
                       | {"dirt", "grass_block", "water", "lava"} | {bare(s) for s in STATIONS})


def members(token):
    if token == "food":
        return ALL_FOOD
    return GROUPS.get(token, [mid(token)])


# -- where a token comes from: the producing skills' `gives`, read from the skill registry ------------------------
# A skill declares what it produces (`@skill(gives=...)`) as one of these tables (rank, lookup); the planner's
# `source` and the solver's columns are both read off the registry, so a producer exists in one place — the skill.
# Rank settles a token two skills could make (the old if-chain's order): a group recipe before a hunt, a smelt
# before a recipe (iron ingots from ore, not from a block), a mine last among the base sources.
RANK = {"gather": 0, "craft_group": 10, "hunt": 20, "smelt": 30, "trade": 35, "craft": 40, "mine": 50, "fill": 60,
        "farm": 70, "take": 90}
# Tokens that are another token's source by definition: "stone"/"building" are what cobblestone is used as.
ALIASES = {"stone": "minecraft:cobblestone", "building": "minecraft:cobblestone", "coal": "minecraft:coal"}


class Produces:
    """What one skill produces: `get(token)` → the source tuple (`source`'s shape) or None, `keys()` → every token.
    Read from the live tables on every call, so a table patched in a test is what the planners see."""

    def __init__(self, kind, get, keys, rows=None):
        self.kind, self.rank, self._get, self._keys = kind, RANK[kind], get, keys
        self._rows = rows or (lambda: [(t, None) for t in keys()])

    def get(self, token):
        return self._get(token)

    def keys(self):
        return list(self._keys())

    def rows(self):
        """(token, the table's own row) for every entry — what the solver builds its columns from."""
        return list(self._rows())


def _table(kind, table, make, skip=()):
    """A producing table read live from `table`; `skip` keeps an entry out of `source` (a planner never smelts
    charcoal for a coal need) while the solver still gets its column."""
    return Produces(kind, lambda t: make(t, table[t]) if t in table and t not in skip else None,
                    lambda: [t for t in table if t not in skip], lambda: list(table.items()))


GIVES_GATHER = Produces("gather", lambda t: ("gather",) if t == "log" else None, lambda: ["log"])
GIVES_CRAFT_GROUP = _table("craft_group", GROUP_RECIPES, lambda t, r: ("craft", r[0], r[1]))
GIVES_CRAFT = _table("craft", RECIPES, lambda t, r: ("craft", r[0], r[1]))
GIVES_HUNT = _table("hunt", HUNT, lambda t, types: ("hunt", types))
GIVES_SMELT = _table("smelt", SMELTS, lambda t, inp: ("smelt", inp), skip=("minecraft:charcoal",))
GIVES_MINE = _table("mine", MINE, lambda t, row: ("mine",) + row)
def _one(kind, token, row, src):
    """A producer of one token: `src` its source tuple, `row` what the solver's column reads."""
    return Produces(kind, lambda t: src if t == token else None, lambda: [token], lambda: [(token, row)])


GIVES_FILL = _one("fill", "minecraft:water_bucket", "minecraft:bucket", ("fill", "minecraft:bucket"))
GIVES_FARM = _one("farm", "minecraft:wheat", ("minecraft:wheat_seeds", PLOT_CELLS),
                  ("farm", "minecraft:wheat_seeds", PLOT_CELLS))
GIVES_TRADE = _one("trade", "minecraft:emerald", ["minecraft:villager"], ("trade", ["minecraft:villager"]))
GIVES_TAKE = _table("take", TAKEABLE, lambda t, row: ("take", row["blocks"]))


PRODUCERS = []       # the registered skills' producing tables, filled by the `skill` decorator (like SKILL_SPEED)
# The modules whose skills produce: loaded by name before the tables are read, so a reader does not depend on who
# happened to import what (a string, not an import: knowledge stays below the skills).
SKILL_MODULES = ("brewing", "building", "combat", "end", "explore", "farming", "fluids", "loot", "needs", "nether",
                 "reflexes", "skills", "ui", "wood")


def producers():
    """Every producing table the registered skills declare, in rank order (the skill modules loaded first)."""
    if not PRODUCERS:
        import importlib
        for m in SKILL_MODULES:
            importlib.import_module(f"{__package__}.{m}")
    return sorted(PRODUCERS, key=lambda g: g.rank)


def produced(kind):
    """[(token, table row)] of every registered producer of this kind — what the solver builds its columns from."""
    return [row for g in producers() if g.kind == kind for row in g.rows()]


def source(token):
    """How a token is produced: ('craft', pattern, out) | ('smelt', input) | ('mine', blocks, tier) |
    ('hunt', types) | ('gather',) | ('fill', container) | ('farm', seeds, per plot) | ('trade', types) |
    ('take', blocks) | None — the first by rank of the registered skills that give it."""
    token = ALIASES.get(token, token)
    item = mid(token)
    for g in producers():
        src = g.get(token) or (g.get(item) if item != token else None)
        if src is not None:
            return src
    return None


# The remainder math goals (goals.desired) and skills (skill `remaining`) share: what the world still lacks of a
# desired state, {} when met. Pure, and here at the bottom so a skill module reads it without the planner.
TOOL_MIN_DURABILITY = 10


def tool_ok(inv, kind, tier, min_left=TOOL_MIN_DURABILITY):
    if not hasattr(inv, "tools"):
        return False
    return any(t >= tier and d >= min_left for t, d, _ in inv.tools(kind))


def held(inv, token):
    """How many of `token` the bag holds, groups and "food" (cooked meals) included."""
    if token == "food":
        return food_count(inv)
    return inv.count(token)


def reconcile(want, have):
    """Pure: what of `want` ({key: amount}) `have` does not cover — {key: missing}, {} when all is there."""
    return {k: n - have.get(k, 0) for k, n in want.items() if have.get(k, 0) < n}


def have_remainder(inv, rows):
    """Pure: what of `rows` ([token, n] / ["tool", kind, tier]) the bag does not hold — {token: missing n,
    "tool:<kind>": tier}, {} when all is held."""
    items = {r[0]: int(r[1]) for r in rows if r[0] != "tool"}
    out = reconcile(items, {t: held(inv, t) for t in items})
    for r in rows:
        if r[0] == "tool" and not tool_ok(inv, r[1], int(r[2])):
            out[f"tool:{r[1]}"] = int(r[2])
    return out


def blocks_remainder(want, name_at):
    """Pure: the cells of `want` ({pos: block}) the world does not show (`name_at(pos)` → the block there) —
    {pos: block}; a structure's remainder, grown back when a block is taken away."""
    return {p: b for p, b in want.items() if bare(name_at(p) or "air") != bare(b)}

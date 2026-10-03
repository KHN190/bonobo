"""Where things come from: the requirement graph the planner resolves (recipes, smelting, mining, hunting)."""
import functools
import math

from .game import BREAK_COOLDOWN, COVERED_SKY, DAYLIT_SKY, EAT_TICKS, EYE_HEIGHT, SPAWN_BLOCK_LIGHT
from .data import ANIMAL_HP, BASE_MARKERS, DAY_TICKS, NIGHT_END, TICKS_PER_S, SOIL_DEPTH, FOOD, GROUPS, RAW, RECIPES, SMELTS, HAND_MINEABLE_SUFFIX, TIER_OF_MATERIAL, bare, mid, BREAK_DIVISOR, HARDNESS, HOE_BLOCKS, SPECIAL_SPEED, TOOL_KINDS, TOOL_MATERIAL_FOR_TIER, TOOL_SPEED, UNLISTED_HARDNESS, WEAPON_DAMAGE, DROP_KINDS, weapon_hit
from .data import TAKEABLE
from .data import BIOME_CREATURES, BIOME_PATCH, TREES_PER_CHUNK, VILLAGE_BIOMES
from .data import SEARCH_RINGS
from .data import CHUNK_BLOCKS, CREATURE_CHUNK_P, DEEPSLATE_TOP, ORE_VEINS, PASSIVE_WEIGHT, ROUTE_FACTOR, SEARCH_LOOK_R, VILLAGE_ONLY, VILLAGE_REGION_BLOCKS, WALK_BLOCKS_PER_TICK
from .data import COLORS, WOODS, ATTACKS_PER_S, HAND_ATTACKS_PER_S, HAND_DAMAGE, NETHER, OVERWORLD, PIGLIN_BARTER, is_night

# group recipes: the output follows the input variant; the craft skill picks one owned member with enough
GROUP_RECIPES = {
    "planks": (["log", None, None, None], 4),
    "boat": (["planks", None, "planks", "planks", "planks", "planks", None, None, None], 1),
    "door": (["planks", "planks", None, "planks", "planks", None, "planks", "planks", None], 3),
    "bed": (["wool", "wool", "wool", "planks", "planks", "planks", None, None, None], 1),
}

# fn(step) → the needs of what carries out a planned step; wired by skill.py so knowledge stays below the skills
TABLES_VERSION = [0]    # bumped at each registration or wiring: what the planner's bound is built from changed
STEP_CALL = None
STEP_WHEN = None       # fn(step, facts) → [(fact, value)] it needs first, or why it cannot run (skill.when_of_step)
STEP_SETS = None       # fn(step) → {fact: value} its run leaves (skill.sets_of_step)
STEP_USES = None       # fn(step) → {item: n} of its needs its run uses up (skill.step_uses)
FIGHT_LINE = None      # fn(contract, args, state, inv) → (ok, why): S5's one judge (brain.fight_line_holds)
LINE_KIT = None        # fn(contract, args, state, inv) → [needs rows] that each clear it (brain.line_kit)
FACT_STEPS = None      # fn(fact, value) → [(kind, token)] of the steps that set it (skill.steps_for_fact)
STEP_STATION = None    # fn(step) → its contract's station or None (skill.station_of_step)

def step_call(step) -> dict:
    """The needs of what carries out `step`, skill modules loaded first; {} when none is wired in."""

    producers()
    return STEP_CALL(step) if STEP_CALL is not None else {}

def step_station(step) -> str | None:
    """The station `step`'s contract works at, carried or standing (None when none, or none is wired in)."""
    producers()
    return STEP_STATION(step) if STEP_STATION is not None else None

def fact_steps(fact, value):
    """[(kind, token)] of the steps that set `fact` to `value` ([] when none is wired in)."""
    producers()
    return FACT_STEPS(fact, value) if FACT_STEPS is not None else []

# item → (blocks to break, minimum pickaxe tier or None)
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
def takeable_blocks():
    """Every block worth walking over to break, flat — one list for the travel scan and the resource map."""
    return sorted({b for row in TAKEABLE.values() for b in row["blocks"]})

# Stations are required by a step but not consumed.
STATIONS = {"minecraft:crafting_table", "minecraft:furnace"}

# Every food, by item id (the planner prices each way to any of them).
ALL_FOOD = [mid(f) for f in FOOD]
# Raw meat: food that wants cooking — eaten raw only when starving, counted as the next meal while cooked is short.
RAW_MEAT = [mid(f) for f in RAW]

# enough food for the Nether: a speedrun crosses on a handful of steaks
KIT_FOOD = 6
# runners take 8–10 beds: one or two blasts per perch, and a wasted bed must not end the fight
DRAGON_BEDS = 8

def food_count(inv) -> int:
    """The one definition of 'food carried': cooked/ready food only (raw meat must be cooked first)."""
    return sum(inv.count(f) for f in ALL_FOOD)

def nether_kit_missing(inv):
    """Pure: what a Nether trip still lacks (empty = ready): cooked food, blocks, a gold helmet, bag room."""

    missing = []
    if food_count(inv) < KIT_FOOD:
        missing.append(f"food {food_count(inv)}/{KIT_FOOD}")
    if inv.count("building") < 32:
        missing.append(f"blocks {inv.count('building')}/32")
    if not (inv.count("minecraft:golden_helmet") or bare(inv.worn("head") or "") == "golden_helmet"):
        missing.append("gold helmet")
    # two free slots: a stricter target flickered with every pickup
    if inv.free_slots() < 2:
        missing.append(f"bag room {inv.free_slots()}/2 free")
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

# where to look when nothing is known: a kind's richest height band (None = surface)
FIND_AT = {
    "minecraft:raw_iron": 16, "minecraft:coal": 48, "minecraft:raw_copper": 48, "minecraft:raw_gold": -16,
    "minecraft:diamond": -58, "minecraft:redstone": -58, "minecraft:lapis_lazuli": 0,
    "log": None, "minecraft:sand": None, "minecraft:clay_ball": None, "food": None,
}

# what the game has no one table for (biome-made), per chunk of ground: priors E4 measures
FIND_DENSITY = {"tree": 1.0, "water": 0.5, "sand": 0.25, "clay": 0.1, "other": 0.05}
TUNNEL_FACES = 6          # block faces a 1×2 tunnel's step lays open: each cell's two sides, the roof, the floor
WALK_TICKS_PER_BLOCK = ROUTE_FACTOR / WALK_BLOCKS_PER_TICK     # ~5.3 ticks a block, sprinting with detours
SOIL_KINDS = {"stone", "cobblestone", "dirt", "grass_block", "coarse_dirt"}
AREA_KINDS = {"water": "water", "sand": "sand", "red_sand": "sand", "clay": "clay", "clay_ball": "clay"}
SURFACE_CLASSES = {"tree", "animal", "village", "water", "sand", "clay", "other"}
KNOWN_BIOMES = set(TREES_PER_CHUNK) | set(BIOME_CREATURES) | VILLAGE_BIOMES


def find_class(kind):
    """Pure: (how the game places `kind`, the price item that says how often): an ore's veins, the soil under the
    feet, a tree, a passive animal, a village's, or a biome-made patch (FIND_DENSITY)."""
    k = bare(kind).removeprefix("deepslate_")
    if k in ORE_VEINS:
        return "ore", "data.ORE_VEINS"
    if k in SOIL_KINDS:
        return "soil", "data.SOIL_DEPTH"
    if k == "deepslate":
        return "deep", "data.DEEPSLATE_TOP"
    if k == "log" or k.endswith(("_log", "_stem")):
        return "tree", "knowledge.FIND_DENSITY.tree"
    if k in PASSIVE_WEIGHT:
        return "animal", "data.CREATURE_CHUNK_P"
    if k in VILLAGE_ONLY:
        return "village", "data.VILLAGE_REGION_BLOCKS"
    area = AREA_KINDS.get(k, "other")
    return area, f"knowledge.FIND_DENSITY.{area}"


def ore_layer_blocks(ore, y):
    """Pure: blocks of `ore` a chunk holds in the one layer at `y` (ORE_VEINS: veins × size, spread uniform or
    triangular over each placement's range)."""
    total = 0.0
    for veins, size, lo, hi, shape in ORE_VEINS[ore]:
        if not lo <= y <= hi:
            continue
        span = hi - lo
        share = 1.0 / span if shape == "uniform" else (2.0 / span) * (1.0 - abs(y - (lo + hi) / 2) / (span / 2))
        total += veins * size * share
    return total


def next_look(radius):
    """Pure: the ring a search looks at after one at `radius` (SEARCH_RINGS), None past the last: a seek then."""
    return next((r for r in SEARCH_RINGS if r > radius), None)


def _area_s(per_block2):
    """Seconds to the first one of a kind spread `per_block2` over the ground: a walk sweeping a band as wide as a
    look sees (2 × SEARCH_LOOK_R) until one is in sight, then the walk to it (its mean distance in the look)."""
    blocks = 1.0 / (2 * SEARCH_LOOK_R * per_block2) + 2 * SEARCH_LOOK_R / 3
    return blocks * WALK_TICKS_PER_BLOCK / TICKS_PER_S


def _density(cls, k, biome=None):
    """Pure: per block² of `k` (of class `cls`) in `biome` (the biome tables), or everywhere when the biome is not
    known (None or in no table): the global density."""
    if biome is not None and biome in KNOWN_BIOMES:
        if cls == "tree":
            return TREES_PER_CHUNK.get(biome, FIND_DENSITY["tree"]) / CHUNK_BLOCKS ** 2
        if cls == "animal":
            spawns = BIOME_CREATURES.get(biome, {})
            return CREATURE_CHUNK_P / CHUNK_BLOCKS ** 2 * spawns.get(k, 0) / sum(spawns.values()) if spawns else 0.0
        if cls == "village":
            return 1.0 / VILLAGE_REGION_BLOCKS ** 2 if biome in VILLAGE_BIOMES else 0.0
        patch = BIOME_PATCH.get(cls, {})
        if biome in patch:
            return patch[biome] / CHUNK_BLOCKS ** 2
    if cls == "animal":
        return CREATURE_CHUNK_P / CHUNK_BLOCKS ** 2 * PASSIVE_WEIGHT[k] / sum(PASSIVE_WEIGHT.values())
    if cls == "village":
        return 1.0 / VILLAGE_REGION_BLOCKS ** 2
    return FIND_DENSITY.get(cls, FIND_DENSITY["other"]) / CHUNK_BLOCKS ** 2


def biome_options(kind, facts):
    """Pure: [(column (x, z) or None, seconds)] to find one `kind` (a surface class) — each chunk in view whose biome
    holds it: the walk to its centre and a search there (_area_s); None: past the view, the global density."""
    cls, _item = find_class(kind)
    k = bare(kind).removeprefix("deepslate_")
    fx, _fy, fz = facts["feet"]
    out, view = [], 0.0
    for cx, cz, biome in facts.get("biomes") or ():
        col = (cx * CHUNK_BLOCKS + CHUNK_BLOCKS // 2, cz * CHUNK_BLOCKS + CHUNK_BLOCKS // 2)
        d = math.dist((fx, fz), col)
        view = max(view, d)
        rho = _density(cls, k, bare(biome))
        if rho > 0:
            out.append((col, d * WALK_TICKS_PER_BLOCK / TICKS_PER_S + _area_s(rho)))
    out.append((None, view * WALK_TICKS_PER_BLOCK / TICKS_PER_S + _area_s(_density(cls, k))))
    return out


def search_target(kind, facts):
    """Pure: the column a search for `kind` heads to — the chunk in view that finds one soonest (biome_options:
    P(find) per second, the walk there included) — or None: no biome known, or nothing in view beats the legs."""
    if not facts.get("biomes") or find_class(kind)[0] not in SURFACE_CLASSES:
        return None
    return min(biome_options(kind, facts), key=lambda o: o[1])[0]


def expected_find_s(kind, facts):
    """Pure: expected seconds to find one `kind` never seen, from how the game places it (find_class). `facts`:
    {"y": the feet's y, "held": {tool kind: tier}, "biomes": [(cx, cz, biome)] in view, "feet"}. An ore: the dig to its
    richest band (FIND_AT) and a tunnel there until one shows; the soil's rock: its depth dug; a surface class: the
    soonest of biome_options when biomes are known (the price the search walks by), else a walk over the ground."""
    cls, _item = find_class(kind)
    held = facts.get("held", {})
    k = bare(kind).removeprefix("deepslate_")
    if cls in SURFACE_CLASSES and facts.get("biomes"):
        return min(s for _col, s in biome_options(kind, facts))
    if cls == "ore":
        drop = next(d for d, (blocks, _t) in MINE.items() if k in [bare(b) for b in blocks])
        band = FIND_AT[drop]
        rock = "deepslate" if band < DEEPSLATE_TOP else "stone"
        per_block = ore_layer_blocks(k, band) / CHUNK_BLOCKS ** 2
        step_s = work_s([rock, rock], [], held, TICKS_PER_S) + WALK_TICKS_PER_BLOCK / TICKS_PER_S
        descent = work_s([rock] * abs(int(facts.get("y", band)) - band), [], held, TICKS_PER_S)
        return descent + step_s / (TUNNEL_FACES * per_block)
    if cls == "soil":
        return work_s(["dirt"] * SOIL_DEPTH, [], held, TICKS_PER_S)
    if cls == "deep":
        return work_s(["stone"] * max(0, int(facts.get("y", DEEPSLATE_TOP)) - DEEPSLATE_TOP + 1), [], held, TICKS_PER_S)
    return _area_s(_density(cls, k))


def step_kinds(step):
    """Pure: the blocks or mobs a step's source is (what a search for it looks for)."""
    k = step.kind
    if k == "gather":
        return list(GROUPS["log"])
    if k in ("mine", "take"):
        return list(step.detail.get("blocks") or ())
    if k == "fill":
        return ["water"]
    if k in ("hunt", "trade"):
        return list(step.detail.get("types") or ())
    if k == "seek":
        return list(step.detail.get("kinds") or [step.token])
    return []

# every block the cost model and the reflexes ask "how far" about: one scan per round answers all (world.nearest)
SOURCE_BLOCKS = sorted({b for blocks, _tier in MINE.values() for b in blocks} | set(GROUPS["log"])
                       | {"dirt", "grass_block", "water", "lava"} | {bare(s) for s in STATIONS}
                       | set(BASE_MARKERS["bed"]) | set(BASE_MARKERS["chest"]))

def members(token) -> list:
    if token == "food":
        return ALL_FOOD
    return GROUPS.get(token, [mid(token)])

# -- where a token comes from: the skills' `gives` in the registry, one place; rank settles a token two skills make
RANK = {"gather": 0, "craft_group": 10, "hunt": 20, "smelt": 30, "trade": 35, "craft": 40, "barter": 45, "mine": 50, "fill": 60,
        "farm": 70, "take": 90}

class Produces:
    """What one skill produces: `get(token)` → the source tuple (`source`'s shape) or None, `keys()` → every token."""

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
    """A producing table read live; `skip` keeps an entry out of `source` while the solver still gets its column."""

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

def barter_yield(item):
    """Pure: the expected count of `item` one bartered gold ingot brings (data.PIGLIN_BARTER: its share of the
    pool's weight × its mean count)."""
    weight, total, lo, hi = PIGLIN_BARTER[item]
    return weight / total * (lo + hi) / 2

GIVES_BARTER = _table("barter", PIGLIN_BARTER, lambda t, row: ("barter", ["minecraft:piglin"], barter_yield(t)))

# where a mob lives, by the game's spawning rules: the facts a hunt of it needs first (the planner's `when`)
LIVES_IN = {"minecraft:blaze": [("dimension", NETHER), ("state:fortress_found", True)],
            "minecraft:piglin": [("dimension", NETHER)]}

def lives_in(types):
    """Pure: [(fact, value)] a hunt of `types` needs first — where the first of them lives (LIVES_IN)."""
    return list(LIVES_IN.get(types[0], [])) if types else []

# -- the body as facts: what work where it stands asks of it, and what the snapshot says it has
DROWNING_TICKS = 100     # ~5 s of air: below this a breath comes before any work
FALL_TAKES_HANDS = 2.0   # blocks: a fall longer than this takes the hands (the fall is under way)

def body_facts(state):
    """Pure: footing, hands_free, night, covered from the body; no readings: standing, by day, in the open."""
    state = state or {}
    swimming = bool(state.get("inWater")) and not state.get("onGround", False)
    falling = float(state.get("fallDistance", 0) or 0) > FALL_TAKES_HANDS
    held = bool((state.get("control") or {}).get("paused"))
    drowning = float(state.get("air", AIR_FULL) or 0) <= DROWNING_TICKS
    night = "timeOfDay" in state and is_night(int(state["timeOfDay"]), state.get("dimension", OVERWORLD))
    return {"footing": not swimming and bool(state.get("onGround", True)),
            "hands_free": not (falling or held or drowning),
            "night": night, "covered": "skyLight" in state and under_rock(state["skyLight"])}

def body_when(footing=True, surface=False):
    """A contract's `when` for hand work (`footing`: standing too), the night's added (night_when)."""
    need, at_night = [("hands_free", True)] + ([("footing", True)] if footing else []), night_when(surface)
    return lambda step, facts: list(need) + at_night(step, facts)

def night_when(surface=False):
    """At night: `surface` work waits for day (S4), other work needs cover; by day nothing."""
    need = [("night", False)] if surface else [("covered", True)]
    return lambda step, facts: list(need) if facts.get("night") else []

PRODUCERS = []  # the registered skills' producing tables, filled by the `skill` decorator
CONTRACT_FACTS = set()  # every fact a registered contract makes true (its `state:` gives, its `sets`), filled likewise
# loaded by name before the tables are read (a string, not an import: knowledge stays below the skills)
SKILL_MODULES = ("brewing", "building", "combat", "dragon", "end", "explore", "farming", "fluids", "loot", "needs", "nether",
                 "reflexes", "skills", "ui", "wood")

def producers():
    """Every producing table the registered skills declare, in rank order (every skill module loaded first: one
    already imported registers only its own — gather alone left no craft producer, so no tool could be planned)."""
    import sys
    if any(f"{__package__}.{m}" not in sys.modules for m in SKILL_MODULES):
        import importlib
        for m in SKILL_MODULES:
            importlib.import_module(f"{__package__}.{m}")
    return sorted(PRODUCERS, key=lambda g: g.rank)

def produced(kind):
    """[(token, table row)] of every registered producer of this kind — what the solver builds its columns from."""
    return [row for g in producers() if g.kind == kind for row in g.rows()]

def sources(token) -> list:
    """Every way a token is produced: [(the token made, its source tuple)], the registered skills' in rank order — a
    group's own and each member's (any of them is the group)."""

    out = []
    for made in [token] + [m for m in GROUPS.get(token, ()) if m != token]:
        item = mid(made)
        for g in producers():
            src = g.get(made) or (g.get(item) if item != made else None)
            if src is not None and (made if made in GROUPS or made == "food" else item, src) not in out:
                out.append((made if made in GROUPS or made == "food" else item, src))
    return out

# -- tool wear, one reading each
TOOL_USABLE = 2       # the jar's rule (InvUtil.java:109, Pathfinder:188/220: remaining > 1): a tool with 1 use left is never held
TOOL_WORKING = 3      # a tool with this many uses left counts as working: one more than the jar holds (planner, solver, needs)


def usable(left):
    """Pure: can the jar still hold a tool with `left` uses (TOOL_USABLE, the jar's own rule)?"""
    return left >= TOOL_USABLE


def working(left, uses=0):
    """Pure: the one "is the tool enough" rule (planner and solver): `left` uses do `uses` more and still work."""
    return left >= uses + TOOL_WORKING


def spare_uses(left) -> int:
    """Pure: the uses a tool spends before it stops working — the solver's uses row (working(left, n) ⇔ n ≤ this)."""
    return max(0, left - TOOL_WORKING)


# -- the remainder math goals and skills' `remaining` share ({} when met), here so skills need no planner
def tool_ok(inv, kind, tier, min_left=TOOL_WORKING) -> bool:
    if not hasattr(inv, "tools"):
        return False
    return any(t >= tier and d >= min_left for t, d, _ in inv.tools(kind))

# -- what to hold: the one choice of tool and weapon (the jar holds exactly the item a task names)
AXE_BLOCKS = ("log", "wood", "planks", "crafting_table", "chest", "barrel", "_door", "ladder", "bookshelf", "fence",
              "melon", "pumpkin", "stem")
SHOVEL_BLOCKS = ("dirt", "sand", "gravel", "grass_block", "clay", "snow", "snow_block", "mud", "farmland", "dirt_path",
                 "mycelium", "podzol", "soul_sand", "soul_soil", "concrete_powder")
HAND_BLOCKS = ("leaves", "wool", "torch", "_bed", "air", "water", "lava", "short_grass", "tall_grass", "fern", "wheat",
               "carpet", "flower", "sapling", "vine")

@functools.cache
def tool_kind(block) -> str | None:
    """Pure: the tool kind that breaks `block` fastest — "axe", "shovel", "pickaxe", or None (the hand does)."""
    name = bare(block or "")
    if not name or name in DROP_KINDS or any(name.endswith(h) or name == h.strip("_") for h in HAND_BLOCKS):
        return None
    if any(name.endswith(a) for a in AXE_BLOCKS):
        return "axe"
    if any(name.endswith(sv) for sv in SHOVEL_BLOCKS):
        return "shovel"
    return "pickaxe"

def soil_depth(region, feet):
    """Pure: the soil (shovel blocks, tool_kind) straight under `feet` down to the rock, as read; where the
    column is not read through, at least the prior SOIL_DEPTH."""
    x, y, z = feet
    n = 0
    while region is not None and region.inside((x, y - 1 - n, z)):
        if tool_kind(region.name((x, y - 1 - n, z))) != "shovel":
            return n
        n += 1
    return max(n, SOIL_DEPTH)

UNBREAKABLE = ("bedrock", "barrier", "end_portal_frame", "end_portal", "nether_portal")
NEEDS_DIAMOND = ("obsidian", "crying_obsidian", "ancient_debris", "respawn_anchor")


def diggable(block, pick_tier=None):
    """Pure: can we break `block` with what we carry — the hand for what it breaks (dirt, sand, gravel …), a pickaxe
    for the rest (`pick_tier`: the best carried, None for none; obsidian wants diamond), never bedrock."""
    name = bare(block or "")
    if not name or name in UNBREAKABLE:
        return False
    if name.endswith(HAND_MINEABLE_SUFFIX):
        return True
    if pick_tier is None:
        return False
    return pick_tier >= TIER_OF_MATERIAL["diamond"] if name in NEEDS_DIAMOND else True


def cheapest_equal(candidates, time_of, tier_of):
    """Pure: of `candidates`, the lowest tier whose time equals the best (a tie goes to the cheaper one; the hand is
    the lowest of all). None when there are none."""
    timed = [(time_of(c), tier_of(c), i, c) for i, c in enumerate(candidates)]
    if not timed:
        return None
    best = min(t for t, _tier, _i, _c in timed)
    return min((x for x in timed if x[0] == best), key=lambda x: (x[1], x[2]))[3]

def item_tier(item):
    """Pure: an item's tier for the choice (its material's; shears iron's); the hand below every tool."""
    if item == "hand":
        return -1
    name = bare(item)
    if name == "shears":
        return TIER_OF_MATERIAL["iron"]
    return TIER_OF_MATERIAL.get(name.rpartition("_")[0], 0)

def hardness(block):
    """Pure: a block's hardness (data.HARDNESS: every block the game has; one it has not priced as stone)."""
    return HARDNESS.get(bare(block or ""), UNLISTED_HARDNESS)

def drop_need(block):
    """Pure: (the item kinds, the least tier) that make `block` drop — a pickaxe block needs its MINE tier (else the
    wooden one's), a cobweb shears or a sword; None when the hand drops it."""
    name = bare(block or "")
    if name in DROP_KINDS:
        return DROP_KINDS[name], None
    if tool_kind(name) != "pickaxe":
        return None
    return ("pickaxe",), next((tier for blocks, tier in MINE.values() if name in blocks), 0)

@functools.cache
def break_ticks(block, item):
    """Pure: whole ticks `item` (or "hand") takes to break `block` (Minecraft Wiki, Breaking: speed / hardness /
    30 when right for the drop, else 100, per tick; 0 when that reaches a whole block in one)."""
    name, h = bare(block or ""), hardness(block)
    base = "hand" if item == "hand" else bare(item)
    kind, material = ("shears", "") if base == "shears" else (base.rpartition("_")[2], base.rpartition("_")[0])
    if kind in ("pickaxe", "axe", "shovel") and kind == tool_kind(name) or kind == "hoe" and name.endswith(HOE_BLOCKS):
        speed = TOOL_SPEED.get(material, 1.0)
    else:
        speed = next((v for (k, suffix), v in SPECIAL_SPEED.items() if k == kind and name.endswith(suffix)), 1.0)
    need = drop_need(name)
    right = need is None or kind in need[0] and (need[1] is None or TIER_OF_MATERIAL.get(material, -1) >= need[1])
    per_tick = speed / h / BREAK_DIVISOR[right] if h > 0 else 1.0
    return 0 if per_tick >= 1 else math.ceil(1 / per_tick)

def _carried_tools(inv, min_left):
    """The tool items the bag holds with wear left (every kind, shears too)."""
    return [s["id"] for s in getattr(inv, "slots", ())
            if (bare(s["id"]).rpartition("_")[2] in TOOL_KINDS or bare(s["id"]) == "shears")
            and s.get("maxDamage", 0) - s.get("damage", 0) >= min_left]

def tool_for(inv, block, min_left=2):
    """Pure: the item a task that breaks `block` holds — of the hand and every tool carried, the lowest tier that
    breaks it in the fewest ticks (cheapest_equal over break_ticks)."""
    return cheapest_equal(["hand"] + _carried_tools(inv, min_left), lambda i: break_ticks(block, i), item_tier)

def dig_ticks(blocks, inv):
    """Pure: ticks the breaks of `blocks` (a block name per cell) take, each with the item tool_for holds for it."""
    return sum(break_ticks(b, tool_for(inv, b)) + break_overhead() for b in blocks)


def break_overhead():
    """Ticks a mine task takes per block past the game's break: the game's cooldown and the task's own."""
    return BREAK_COOLDOWN + PRIOR_TICKS["break_task"]

def tool_item(kind, tier):
    """Pure: the tool of `kind` at `tier` ("minecraft:stone_shovel")."""
    return mid(f"{TOOL_MATERIAL_FOR_TIER[tier]}_{kind}")

def work_s(breaks, kills, held, ticks_per_s):
    """Pure: seconds the work takes — each block of `breaks` broken, each hp of `kills` dealt — with the best of the
    hand and `held` ({tool kind: tier}) for each."""
    items = ["hand"] + [tool_item(k, t) for k, t in held.items()]
    return (sum(min(break_ticks(b, i) for i in items) + break_overhead() for b in breaks) / ticks_per_s
            + sum(min(kill_s(i, hp) for i in items) for hp in kills))

def own_work(step):
    """Pure: (breaks, kills) a step's own work makes: a mine's blocks, a gather's logs, a hunt's kills (each
    animal's hp) — a block or an hp per unit (its `breaks`, `kills` or count)."""
    units = int(step.detail.get("breaks") or step.detail.get("kills") or step.count or 0)
    if step.kind == "mine" and step.detail.get("blocks"):
        return [step.detail["blocks"][0]] * units, []
    if step.kind == "gather":
        return [GROUPS["log"][0]] * units, []
    if step.kind == "hunt":
        hp = [ANIMAL_HP[t] for t in step.detail.get("types", ()) if t in ANIMAL_HP]
        return [], [min(hp)] * units if hp else []
    return [], []

def held_tiers(inv, min_left=TOOL_WORKING) -> dict:
    """Pure: {tool kind: the best tier the bag holds with wear left}."""
    out = {}
    for kind in TOOL_KINDS:
        tiers = [t for t, d, _ in inv.tools(kind) if d >= min_left and t in TOOL_MATERIAL_FOR_TIER]
        if tiers:
            out[kind] = max(tiers)
    return out

def kill_s(item, hp):
    """Pure: seconds `item` (or "hand") takes to deal `hp` (whole hits × one attack's cooldown, no crits)."""
    damage, rate = weapon_hit(item)
    return math.ceil(hp / damage) / rate

def weapon_for(inv, hp):
    """Pure: the weapon an attack holds — of the hand and every sword and axe carried with wear left, the lowest tier
    that deals `hp` (the mob's health) soonest (cheapest_equal over kill_s)."""
    weapons = [i for i in _carried_tools(inv, 2) if bare(i).rpartition("_")[2] in WEAPON_DAMAGE]
    return cheapest_equal(["hand"] + weapons, lambda i: kill_s(i, hp), item_tier)

def attack_weapon(inv, foe_hp):
    """Pure: the weapon an attack holds (weapon_for), None for the hand."""
    w = weapon_for(inv, foe_hp)
    return None if w == "hand" else w

def held_count(inv, token):
    """How many of `token` the bag holds, groups and "food" (cooked meals) included."""
    if token == "food":
        return food_count(inv)
    return inv.count(token)

def reconcile(want, have):
    """Pure: what of `want` ({key: amount}) `have` does not cover — {key: missing}, {} when all is there."""
    return {k: n - have.get(k, 0) for k, n in want.items() if have.get(k, 0) < n}

def needs_rows(needs):
    """Pure: a skill's `needs` ({dim: n}, "tool:<kind>:<tier>" for a tool) as have_remainder's rows."""
    return [["tool", k.split(":")[1], int(k.split(":")[2])] if k.startswith("tool:") else [k, n]
            for k, n in needs.items()]

def have_remainder(inv, rows, pending=None):
    """Pure: what of `rows` the bag does not hold — {token: missing, "tool:<kind>": tier}, {} when all held."""

    pending = pending or {}
    items = {r[0]: int(r[1]) for r in rows if r[0] != "tool"}
    out = reconcile(items, {t: held_count(inv, t) + pending.get(t, 0) for t in items})
    for r in rows:
        if r[0] == "tool" and not tool_ok(inv, r[1], int(r[2])):
            out[f"tool:{r[1]}"] = int(r[2])
    return out

def blocks_remainder(want, name_at):
    """Pure: the cells of `want` the world does not show; grows back when a block is taken away."""

    return {p: b for p, b in want.items() if bare(name_at(p) or "air") != bare(b)}

# -- what is left of a world-effect skill: `remaining` readers over body_state's shape; a reading not taken is not "done"
AIR_FULL = 300          # the air meter's top, in ticks

def swimming(state) -> bool:
    """The one "in the water" test: in water and not standing, or standing with the head under (breath below full)."""

    return bool(state.get("inWater")) and (not state.get("onGround", False)
                                            or float(state.get("air", AIR_FULL) or 0) < AIR_FULL)

def left(ok, what, n=1):
    """{} when `ok`, else {what: n}."""
    return {} if ok else {what: n}

def body(st):
    return st.get("state") or {}

# -- where the body is
def is_in_dimension(dimension_of):
    """In the dimension `dimension_of(call)` names."""
    def fn(st, c):
        want = dimension_of(c)
        return left(body(st).get("dimension") == want, f"dimension:{bare(want)}")
    return fn

def near(pos_of, range_of=lambda c: 2.0):
    """Within `range_of(call)` of `pos_of(call)` (feet to the point, in 3-D): the rest is the distance left."""
    def fn(st, c):
        pos = pos_of(c)
        d = math.dist(tuple(st["feet"]), tuple(pos)) - float(range_of(c))
        return {} if d <= 0 else {"blocks away": round(d, 1)}
    return fn

def on_dry_ground(st, c):
    s = body(st)
    return left(bool(s.get("onGround")) and not s.get("inWater"), "state:ashore")

def standing(st, c):
    return left(bool(body(st).get("onGround")), "state:footing")

def breathing(st, c):
    s = body(st)
    return left(int(s.get("air", AIR_FULL)) >= AIR_FULL, "state:air", AIR_FULL - int(s.get("air", 0)))

def daytime(st, c):
    """The day wanted: {} while it is not night (data.is_night: the one day cycle, absolute ticks taken mod a day)."""
    t = body(st).get("timeOfDay")
    return left(t is not None and not is_night(int(t)), "state:day")

def fed(st, c):
    food = int(body(st).get("food", 0))
    return left(food >= 20, "food", 20 - food)

# -- the blocks read around us
def names(st):
    region = st.get("region")
    return [bare(n) for n in region.blocks.values()] if region is not None else []

def blocks_there(*kinds, least=1):
    """`least` of these blocks stand in the region read (a portal lit, bricks found)."""
    want = {bare(k) for k in kinds}

    def fn(st, c):
        n = sum(1 for b in names(st) if b in want)
        return left(n >= least, f"blocks:{'|'.join(sorted(want))}", least - n)
    return fn

def blocks_gone(*kinds):
    """None of these blocks left in the region read (lava covered). Unread: not gone."""
    want = {bare(k) for k in kinds}

    def fn(st, c):
        if st.get("region") is None:
            return {f"unread:{'|'.join(sorted(want))}": 1}
        n = sum(1 for b in names(st) if b in want)
        return left(n == 0, f"blocks:{'|'.join(sorted(want))}", n)
    return fn

def structure(cells_of):
    """A structure's cells ({pos: block}, from the call) against the region read (knowledge.blocks_remainder)."""
    
    def fn(st, c):
        want = cells_of(c)
        region = st.get("region")
        if region is None:
            return dict(want)
        return blocks_remainder(want, lambda p: region.name(p) if region.inside(p) else None)
    return fn

# -- the bag

def more_than_at_start(token_of, n_of=lambda c: 1):
    """`n_of(call)` more of `token_of(call)` than the call started with (`call.base`, the skill's own start)."""
    def fn(st, c):
        base = getattr(c, "base", None)
        base = base if isinstance(base, int) else 0
        return have_remainder(st["inv"], [[token_of(c), base + n_of(c)]])
    return fn

def less_than_at_start(token_of, n_of=lambda c: 1):
    """`n_of(call)` fewer of `token_of(call)` in the bag than at the start (handed over: into a furnace, a chest)."""
    def fn(st, c):
        base = getattr(c, "base", None)
        base = base if isinstance(base, int) else held_count(st["inv"], token_of(c))
        now = held_count(st["inv"], token_of(c))
        return left(now <= base - n_of(c), f"to hand over:{token_of(c)}", now - (base - n_of(c)))
    return fn

def slots_free(target):
    def fn(st, c):
        free = st["inv"].free_slots()
        return left(free >= target, "free slots", target - free)
    return fn

def worn(item_of, below=0.25):
    """The bag's `item_of(call)` worn less than `below` of its life (repaired)."""
    def fn(st, c):
        item = mid(item_of(c))
        stacks = [s for s in st["inv"].slots if s["id"] == item and s.get("maxDamage")]
        worst = max((s.get("damage", 0) / s["maxDamage"] for s in stacks), default=None)
        return left(worst is not None and worst < below, f"repair:{bare(item)}")
    return fn

# -- what moves around us
def entity_kinds(st):
    return st.get("entities")

def none_of(*types, within=24.0):
    """None of these entity types within `within` (the /entities rows read). Unread: not none."""
    want = {mid(t) for t in types}

    def fn(st, c):
        rows = entity_kinds(st)
        if rows is None:
            return {f"unread:{'|'.join(sorted(bare(t) for t in want))}": 1}
        n = sum(1 for e in rows if e.get("type") in want and e.get("distance", 0) <= within)
        return left(n == 0, f"entities:{'|'.join(sorted(bare(t) for t in want))}", n)
    return fn

def some_of(types_of, within=48.0):
    """One of `types_of(call)` in sight (the /entities rows read)."""
    def fn(st, c):
        want = {mid(t) for t in types_of(c)}
        rows = entity_kinds(st) or []
        return left(any(e.get("type") in want and e.get("distance", 0) <= within for e in rows),
                    f"seen:{'|'.join(sorted(bare(t) for t in want))}")
    return fn

def dragon_phase(phases):
    def fn(st, c):
        rows = entity_kinds(st) or []
        dragon = next((e for e in rows if e.get("type") == "minecraft:ender_dragon"), None)
        return left(dragon is not None and dragon.get("phase") in phases, "state:dragon_perched")
    return fn

def few_dark(st, c):
    """No dark spot left (the /dark spots read)."""
    spots = st.get("dark")
    if spots is None:
        return {"unread:dark": 1}
    return left(not spots, "dark spots", len(spots))

def _base(c, default=0):
    b = getattr(c, "base", None)
    return b if b is not None else default

# -- the skills' own readers
def bartered(st, c):
    """More carried than gold at the start (what a piglin tosses back)."""
    now = sum(int(s.get("count", 1)) for s in st["inv"].slots if s["id"] != "minecraft:gold_ingot")
    return left(now > _base(c), "trades")

def window_over(perch_phases):
    """The attack window closed: the dragon no longer perched (or gone)."""
    def fn(st, c):
        rows = entity_kinds(st)
        if rows is None:
            return {"unread:dragon": 1}
        dragon = next((e for e in rows if e.get("type") == "minecraft:ender_dragon"), None)
        return left(dragon is None or dragon.get("phase") not in perch_phases, "state:window")
    return fn

def babies(st, c):
    rows = entity_kinds(st)
    if rows is None:
        return {"unread:animals": 1}
    n = sum(1 for e in rows if e.get("baby"))
    return left(n > _base(c), "babies")

def potions(pred):
    """More potions matching `pred(stack)` than at the start."""
    def fn(st, c):
        n = sum(int(s.get("count", 1)) for s in st["inv"].slots if pred(s))
        return left(n > _base(c), "potions")
    return fn

def walled_sides(st, c):
    """The body in a pit: every side at feet level solid (the dragon's breath cannot reach in)."""
    region = st.get("region")
    if region is None:
        return {"unread:pit": 1}
    x, y, z = st["feet"]
    open_ = [d for d in ((1, 0), (-1, 0), (0, 1), (0, -1)) if not region.solid((x + d[0], y, z + d[1]))]
    return left(not open_, "state:in_pit", len(open_))

def built(name_of):
    """A machine of this blueprint remembered (memory rows the caller read: "machines")."""
    def fn(st, c):
        rows = st.get("machines") or []
        return left(any(m.get("blueprint") == name_of(c) for m in rows), f"built:{name_of(c)}")
    return fn

def planned_items(st, c):
    """craft_chain's start is {item: (held, made)}: every item at held + made."""
    base = getattr(c, "base", None) or {}
    return have_remainder(st["inv"], [[i, h + n] for i, (h, n) in base.items()])

def machine_emptied(name_of):
    def fn(st, c):
        rows = [m for m in (st.get("machines") or []) if m.get("name") == name_of(c)]
        pending = sum(p.get("count", 0) for m in rows for p in m.get("pending", []))
        return left(bool(rows) and pending == 0, "pending", pending)
    return fn

def enchanted(item_of):
    def fn(st, c):
        n = sum(1 for s in st["inv"].slots if s["id"] == mid(item_of(c)) and s.get("enchanted"))
        return left(n > _base(c), f"enchanted:{bare(item_of(c))}")
    return fn

def site_known(kind):
    def fn(st, c):
        return left(any(s.get("kind") == kind for s in (st.get("sites") or [])), f"site:{kind}")
    return fn

def container_known(pos_of):
    """The container at pos_of(call) has a record (a /container reading noted: `containers`)."""
    def fn(st, c):
        return left(any(tuple(r["pos"]) == tuple(pos_of(c)) for r in (st.get("containers") or [])), "look")
    return fn

def gained_any(st, c):
    total = sum(int(s.get("count", 1)) for s in st["inv"].slots)
    return left(total > _base(c), "loot")

def fewer_tools(kind_of):
    def fn(st, c):
        n = sum(1 for s in st["inv"].slots if s["id"].endswith("_" + kind_of(c)))
        return left(n < _base(c, n + 1), f"combine:{kind_of(c)}")
    return fn

def found(kinds_of):
    """One of `kinds_of(call)` in sight: a block of it in the region read, or an entity of it."""
    def fn(st, c):
        want = {bare(k) for k in kinds_of(c)}
        seen = any(b in want for b in names(st)) or any(bare(e.get("type", "")) in want for e in (entity_kinds(st) or []))
        return left(seen, f"seen:{'|'.join(sorted(want))}")
    return fn

def tunnelled(length_of):
    """A strip-mine step: stone won (the tunnel's own yield) — half its length's worth over the start."""
    def fn(st, c):
        base = getattr(c, "base", None) or (0, 0)
        stone = held_count(st["inv"], "stone") + held_count(st["inv"], "minecraft:cobbled_deepslate")
        need = base[1] + max(1, length_of(c) // 2)
        return left(stone >= need, "stone", need - stone)
    return fn

def head_clear(st, c):
    region = st.get("region")
    if region is None:
        return {"unread:head": 1}
    s = body(st)
    x, z = st["feet"][0], st["feet"][2]
    eye = (x, math.floor(float(s.get("y", st["feet"][1])) + EYE_HEIGHT), z)
    return left(not region.buries(eye), "state:head_clear")




def under_rock(sky_light) -> bool:
    """Pure: rock over the feet (sky light at most COVERED_SKY) — underground: no surface work at night, a surface
    trip starts with the climb. The one reading of it."""
    return sky_light <= COVERED_SKY


def dark_here(s):
    """Pure over /state: standing where mobs spawn — block light 0, and not under open sky by day."""
    return "blockLight" in s and s["blockLight"] <= SPAWN_BLOCK_LIGHT and \
        not (s["skyLight"] > DAYLIT_SKY and not is_night(int(s["timeOfDay"])))


def sheltered(sky_light, enclosed, in_site=lambda: False):
    """Pure given its readers: the night's one judgement of cover — under rock, walled in (`enclosed()`: the
    shelter's remainder, terrain.openings, is empty) or inside a site's interior (`in_site()`). Sky light alone
    never says walled in: a cave mouth or a pit under open sky is none of them. The readers are asked only when
    the cheaper answer did not settle it (perception reads the walls once a night)."""
    return under_rock(sky_light) or bool(enclosed()) or bool(in_site())


# -- a step's prior work in ticks: the one table (cost.Cost before anything is measured, and planner.NullCost)
PRIOR_TICKS = {"craft": 60, "smelt_each": 200, "smelt_setup": 300, "mine_each": 60, "gather_each": 60,
               "hunt_each": 300, "fill": 20, "goto": 0, "build": 2400, "sleep": 400, "skill": 1200, "take": 200,
               "withdraw": 40, "look": 40, "cast": 3000,       # cast: a portal frame, ten cells of lava and water
               "farm": 1200, "trade": 600,         # farm: without the growth (GROW_S)
               "reach": 200, "breed": 400, "eat": EAT_TICKS, "pickup_each": 20,   # pickup: mine_stone__base 181618, ~1 s a drop
               "shelter:dig_in": 500, "shelter:pod": 800, "shelter:hut": 2400,
               "room:tidy": 300, "room:deposit": 1200,
               "surface": 200, "surface_per_block": 30,     # out from under rock: a base and per block below SURFACE_Y
               "break_task": 1}     # a mine's own ticks past the break and BREAK_COOLDOWN: the segment boundary
#                                    (bench q5: 2-4 ticks a segment of 2-4 mines)
SURFACE_Y = 64
GROW_S = {"crop": 900, "animal": 1200}     # seconds (jobs.DURATION)
NIGHT_S = 420.0               # a night, when the clock is not read
# where each price comes from (static R14): game (the game's own data), measured (fitted from runs), prior (a guess,
# E4's to do), policy (a choice, not a measurable price)
PRICE_SOURCE = {
    "knowledge.PRIOR_TICKS": {
        "craft": "prior", "smelt_each": "game", "smelt_setup": "prior", "mine_each": "prior", "gather_each": "prior",
        "hunt_each": "prior", "fill": "prior", "goto": "policy", "build": "prior", "sleep": "prior", "skill": "prior",
        "take": "prior", "withdraw": "prior", "look": "prior", "cast": "prior", "farm": "prior", "trade": "prior",
        "reach": "prior", "breed": "prior", "eat": "game", "pickup_each": "measured", "shelter:dig_in": "prior", "shelter:pod": "prior",
        "shelter:hut": "prior", "room:tidy": "prior", "room:deposit": "prior", "surface": "prior",
        "surface_per_block": "prior",
        "break_task": "measured"},      # bench q5 ticks (readiness: mine_stone__base, ore_buried, chop__base)
    "knowledge.SURFACE_Y": "game", "data.MEASURED_BAND": "policy", "knowledge.GROW_S": {"crop": "prior", "animal": "game"}, "knowledge.NIGHT_S": "game",
    "knowledge.FIND_AT": "game", "knowledge.FIND_DENSITY": {"tree": "prior", "water": "prior", "sand": "prior", "clay": "prior", "other": "prior"}, "knowledge.TUNNEL_FACES": "game",
    "data.ORE_VEINS": "game", "data.TREES_PER_CHUNK": "game", "data.BIOME_CREATURES": "game", "data.VILLAGE_BIOMES": "game", "data.BIOME_PATCH": "prior", "data.CREATURE_CHUNK_P": "game", "data.VILLAGE_REGION_BLOCKS": "game", "data.SOIL_DEPTH": "prior", "data.DEEPSLATE_TOP": "game",
    "data.WALK_BLOCKS_PER_TICK": "mineflayer prior", "data.ROUTE_FACTOR": "prior", "data.HARDNESS": "game",
    "data.TOOL_SPEED": "game", "data.BREAK_DIVISOR": "game", "data.PASSIVE_WEIGHT": "game", "data.SEARCH_LEGS": "prior",
    "data.SEARCH_LOOK_R": "prior", "game.EAT_TICKS": "game", "game.BREAK_COOLDOWN": "game", "game.PLAYER_SPRINT": "game",
}
PRIOR_ORIGIN = {}     # a fitted price item → its first value (tools.fit_prices bounds every fit by it)


def dawn_s(state):
    """Pure: seconds until sunrise from the /state clock."""
    if "timeOfDay" not in (state or {}):
        return NIGHT_S
    t = int(state["timeOfDay"]) % DAY_TICKS
    return max(1.0, ((NIGHT_END - t) % DAY_TICKS) / TICKS_PER_S)


def dig_to_ticks(breaks, step, held, tps):
    """Pure: ticks the breaks of `breaks` beyond the step's own (`own_work`) take — the digging to its work — with the
    best of the hand and `held` ({tool kind: tier}) for each."""
    own, _kills = own_work(step)
    reach = list(breaks)[len(own):]
    return round(work_s(reach, [], held, tps) * tps) if reach else 0


def prior_work_ticks(step, held, tps):
    """Pure: the prior (`prior_ticks`, the hand's) less what `held` ({tool kind: tier}) saves on the step's own work,
    never below the game's own time for that work with those tools (`tps`: the game's ticks a second)."""
    breaks, kills = own_work(step)
    with_tools = work_s(breaks, kills, held, tps)
    saved = work_s(breaks, kills, {}, tps) - with_tools
    return max(round(with_tools * tps), prior_ticks(step) - round(saved * tps)) + pickup_ticks(step)


def pickup_ticks(step):
    """Pure: the walk onto each drop the step's own breaks leave (PRIOR_TICKS pickup_each a break)."""
    breaks, _kills = own_work(step)
    return PRIOR_TICKS["pickup_each"] * len(breaks)


def prior_ticks(step):
    """Pure: the ticks a step's work takes before anything is measured (PRIOR_TICKS, per unit where it has units)."""
    k = step.kind
    if k == "smelt":
        return PRIOR_TICKS["smelt_each"] * step.count + PRIOR_TICKS["smelt_setup"]
    if k == "mine":
        return PRIOR_TICKS["mine_each"] * step.detail.get("breaks", step.count)
    if k == "gather":
        return PRIOR_TICKS["gather_each"] * step.count
    if k == "hunt":
        return PRIOR_TICKS["hunt_each"] * step.detail.get("kills", step.count)
    if k == "fill":
        return PRIOR_TICKS["fill"] * step.count
    if k == "eat":
        return PRIOR_TICKS["eat"] * max(1, int(step.count))      # count: bites
    if k == "take" and step.token in TAKEABLE:
        return round(float(TAKEABLE[step.token]["break_s"]) * TICKS_PER_S) * max(1, int(step.count))
    if k == "farm":
        return (PRIOR_TICKS["farm"] + GROW_S["crop"] * TICKS_PER_S) * max(1, int(step.count))
    if k == "breed":
        return (PRIOR_TICKS["breed"] + GROW_S["animal"] * TICKS_PER_S + PRIOR_TICKS["hunt_each"]) * max(1, int(step.count))
    if f"{k}:{step.token}" in PRIOR_TICKS:
        return PRIOR_TICKS[f"{k}:{step.token}"]
    return PRIOR_TICKS.get(k, 1000)

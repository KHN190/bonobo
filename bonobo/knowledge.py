"""Where things come from: the requirement graph the planner resolves (recipes, smelting, mining, hunting)."""
import math

from .data import BASE_MARKERS, COLORS, FOOD, GROUPS, NUTRITION, RAW, RECIPES, SMELTS, WOODS, bare, mid

# group recipes: the output follows the input variant; the craft skill picks one owned member with enough
GROUP_RECIPES = {
    "planks": (["log", None, None, None], 4),
    "boat": (["planks", None, "planks", "planks", "planks", "planks", None, None, None], 1),
    "door": (["planks", "planks", None, "planks", "planks", None, "planks", "planks", None], 3),
    "bed": (["wool", "wool", "wool", "planks", "planks", "planks", None, None, None], 1),
}

# seconds a speed tool saves per unit of work; it is made only when that beats making it
CHOP_AXE_S = 1.5         # a log: ~3 s by hand, ~1.5 s with a wooden axe
HUNT_SWORD_S = 3.0       # a kill: a cow takes ten fist hits, four with a wooden sword
DIG_SHOVEL_S = 0.35      # a block of dirt, sand or gravel: 0.75 s by hand, 0.4 s with a wooden shovel
# fn(step) → (needs, speed) of what carries out a planned step; wired by skill.py so knowledge stays below the skills
STEP_CALL = None

def step_call(step):
    """(needs, speed) of what carries out `step`, skill modules loaded first; ({}, {}) when none is wired in."""

    producers()
    return STEP_CALL(step) if STEP_CALL is not None else ({}, {})

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

# enough food for the Nether: a speedrun crosses on a handful of steaks
KIT_FOOD = 6
# runners take 8–10 beds: one or two blasts per perch, and a wasted bed must not end the fight
DRAGON_BEDS = 8

def food_count(inv):
    """The one definition of 'food carried': cooked/ready food only (raw meat must be cooked first)."""
    return sum(inv.count(f) for f in ALL_FOOD)

def food_points(inv):
    """Hunger points the ready food carried restores, from the one table (data.NUTRITION)."""
    return sum(inv.count(mid(f)) * NUTRITION[f] for f in FOOD)

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

# every block the cost model and the reflexes ask "how far" about: one scan per round answers all (world.nearest)
SOURCE_BLOCKS = sorted({b for blocks, _tier in MINE.values() for b in blocks} | set(GROUPS["log"])
                       | {"dirt", "grass_block", "water", "lava"} | {bare(s) for s in STATIONS}
                       | set(BASE_MARKERS["bed"]) | set(BASE_MARKERS["chest"]))

def members(token):
    if token == "food":
        return ALL_FOOD
    return GROUPS.get(token, [mid(token)])

# -- where a token comes from: the skills' `gives` in the registry, one place; rank settles a token two skills make
RANK = {"gather": 0, "craft_group": 10, "hunt": 20, "smelt": 30, "trade": 35, "craft": 40, "mine": 50, "fill": 60,
        "farm": 70, "take": 90}
# Tokens that are another token's source by definition: "stone"/"building" are what cobblestone is used as.
ALIASES = {"stone": "minecraft:cobblestone", "building": "minecraft:cobblestone", "coal": "minecraft:coal"}

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

PRODUCERS = []  # the registered skills' producing tables, filled by the `skill` decorator
# loaded by name before the tables are read (a string, not an import: knowledge stays below the skills)
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
    """How a token is produced (a source tuple by kind), the first by rank of the registered skills that give it, or None."""

    token = ALIASES.get(token, token)
    item = mid(token)
    for g in producers():
        src = g.get(token) or (g.get(item) if item != token else None)
        if src is not None:
            return src
    return None

# -- the remainder math goals and skills' `remaining` share ({} when met), here so skills need no planner
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

def needs_rows(needs):
    """Pure: a skill's `needs` ({dim: n}, "tool:<kind>:<tier>" for a tool) as have_remainder's rows."""
    return [["tool", k.split(":")[1], int(k.split(":")[2])] if k.startswith("tool:") else [k, n]
            for k, n in needs.items()]

def have_remainder(inv, rows, pending=None):
    """Pure: what of `rows` the bag does not hold — {token: missing, "tool:<kind>": tier}, {} when all held."""

    pending = pending or {}
    items = {r[0]: int(r[1]) for r in rows if r[0] != "tool"}
    out = reconcile(items, {t: held(inv, t) + pending.get(t, 0) for t in items})
    for r in rows:
        if r[0] == "tool" and not tool_ok(inv, r[1], int(r[2])):
            out[f"tool:{r[1]}"] = int(r[2])
    return out

def blocks_remainder(want, name_at):
    """Pure: the cells of `want` the world does not show; grows back when a block is taken away."""

    return {p: b for p, b in want.items() if bare(name_at(p) or "air") != bare(b)}

# -- what is left of a world-effect skill: `remaining` readers over body_state's shape; a reading not taken is not "done"
AIR_FULL = 300

def left(ok, what, n=1):
    """{} when `ok`, else {what: n}."""
    return {} if ok else {what: n}

def body(st):
    return st.get("state") or {}

# -- where the body is
def in_dimension(dimension_of):
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
    t = body(st).get("timeOfDay")
    return left(t is not None and int(t) % 24000 < 12500, "state:day")

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
        base = base if isinstance(base, int) else held(st["inv"], token_of(c))
        now = held(st["inv"], token_of(c))
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
def entities(st):
    return st.get("entities")

def none_of(*types, within=24.0):
    """None of these entity types within `within` (the /entities rows read). Unread: not none."""
    want = {mid(t) for t in types}

    def fn(st, c):
        rows = entities(st)
        if rows is None:
            return {f"unread:{'|'.join(sorted(bare(t) for t in want))}": 1}
        n = sum(1 for e in rows if e.get("type") in want and e.get("distance", 0) <= within)
        return left(n == 0, f"entities:{'|'.join(sorted(bare(t) for t in want))}", n)
    return fn

def some_of(types_of, within=48.0):
    """One of `types_of(call)` in sight (the /entities rows read)."""
    def fn(st, c):
        want = {mid(t) for t in types_of(c)}
        rows = entities(st) or []
        return left(any(e.get("type") in want and e.get("distance", 0) <= within for e in rows),
                    f"seen:{'|'.join(sorted(bare(t) for t in want))}")
    return fn

def dragon_phase(phases):
    def fn(st, c):
        rows = entities(st) or []
        dragon = next((e for e in rows if e.get("type") == "minecraft:ender_dragon"), None)
        return left(dragon is not None and dragon.get("phase") in phases, "state:dragon_perched")
    return fn

def entity_gone(id_of):
    def fn(st, c):
        rows = entities(st)
        if rows is None:
            return {"unread:entity": 1}
        gone = all(e.get("id") != id_of(c) for e in rows)
        return left(gone, f"entity:{id_of(c)}")
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
        rows = entities(st)
        if rows is None:
            return {"unread:dragon": 1}
        dragon = next((e for e in rows if e.get("type") == "minecraft:ender_dragon"), None)
        return left(dragon is None or dragon.get("phase") not in perch_phases, "state:window")
    return fn

def babies(st, c):
    rows = entities(st)
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
        seen = any(b in want for b in names(st)) or any(bare(e.get("type", "")) in want for e in (entities(st) or []))
        return left(seen, f"seen:{'|'.join(sorted(want))}")
    return fn

def tunnelled(length_of):
    """A strip-mine step: stone won (the tunnel's own yield) — half its length's worth over the start."""
    def fn(st, c):
        base = getattr(c, "base", None) or (0, 0)
        stone = held(st["inv"], "stone") + held(st["inv"], "minecraft:cobbled_deepslate")
        need = base[1] + max(1, length_of(c) // 2)
        return left(stone >= need, "stone", need - stone)
    return fn

def head_clear(st, c):
    region = st.get("region")
    if region is None:
        return {"unread:head": 1}
    s = body(st)
    x, z = st["feet"][0], st["feet"][2]
    eye = (x, math.floor(float(s.get("y", st["feet"][1])) + 1.62), z)
    return left(not region.solid(eye), "state:head_clear")


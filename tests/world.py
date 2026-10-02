"""What the offline tests are fed: readings, not a world.

The game is the only simulator. Offline tests are pure functions over what the game ANSWERED: a `/state`, an
`/inventory`, what `/find` saw (`state`, `inventory`, `Seen`), and — for pure geometry — a read-only box of block
names (`FakeRegion`, the interface `world.Region` offers with nothing behind it). The sweeps (`worlds`, `dangers`,
`fights`) are cross-products of such readings, so one relation is claimed over hundreds of situations.
"""
from bonobo.data import FALLING, HAZARD, PASSABLE, PASSABLE_SUFFIX, PLAYER_MADE_SUFFIX, UNBREAKABLE  # noqa: F401
from bonobo.world import Region


class FakeRegion:
    """A box of named blocks, the interface `Region` offers and nothing else behind it."""

    def __init__(self, lo, hi, blocks):
        self.lo, self.hi, self.blocks = tuple(lo), tuple(hi), dict(blocks)

    def inside(self, p):
        return all(self.lo[i] <= p[i] <= self.hi[i] for i in range(3))

    def name(self, p):
        return self.blocks.get(tuple(p), "air")

    def solid(self, p):
        n = self.name(p)
        return n != "air" and n not in PASSABLE and not n.endswith(PASSABLE_SUFFIX) and n not in HAZARD

    def hazard(self, p):
        return self.name(p) in HAZARD

    def prop(self, p, key):
        return None               # no block states recorded

    buries = Region.buries

    def falling(self, p):
        return self.name(p) in FALLING

    def unbreakable(self, p):
        return self.name(p) in UNBREAKABLE

    def player_made(self, p):
        return self.name(p).endswith(PLAYER_MADE_SUFFIX)


def flat(lo=(-20, 60, -20), hi=(20, 76, 20), floor_y=63, block="stone"):
    """Solid ground up to `floor_y`, open air above: the simplest standable world."""
    blocks = {}
    for x in range(lo[0], hi[0] + 1):
        for z in range(lo[2], hi[2] + 1):
            for y in range(lo[1], floor_y + 1):
                blocks[(x, y, z)] = block
    return FakeRegion(lo, hi, blocks)


# ---------------------------------------------------------------- readings, in the shapes the game answers with
# Offline tests are pure functions fed real readings: a `/state` dict, an `/inventory` dict, what `/find` and
# `/entities` saw. These are those shapes (copied from a recorded round), with the fields a row moves. Nothing here
# answers a request or runs anything: the game is the only simulator.

STATE = {"x": 0.5, "y": 64.0, "z": 0.5, "blockX": 0, "blockY": 64, "blockZ": 0, "yaw": 0.0, "pitch": 0.0,
         "dimension": "minecraft:overworld", "blockLight": 0, "skyLight": 15, "timeOfDay": 2658, "health": 20.0,
         "maxHealth": 20.0, "food": 20, "saturation": 5.0, "air": 300, "armor": 0, "xpLevel": 0, "onGround": True,
         "inWater": False, "inPortal": False, "inLava": False, "onFire": False, "dead": False, "selectedSlot": 0,
         "mainHand": {"id": "minecraft:air", "count": 0}, "screen": "none", "lookingAt": {"kind": "none"},
         "control": {"active": False, "paused": False, "allowed": True, "task": None, "queued": 0}}
AIR = {"id": "minecraft:air", "count": 0}
TOOL_MAX = {"wooden": 59, "stone": 131, "iron": 250, "golden": 32, "diamond": 1561, "netherite": 2031}


def state(**changes):
    """A `/state` answer: the recorded one with `changes` applied (blockX/Y/Z follow x/y/z when those move)."""
    s = dict(STATE, **changes)
    for axis, block in (("x", "blockX"), ("y", "blockY"), ("z", "blockZ")):
        if axis in changes and block not in changes:
            s[block] = int(changes[axis] // 1)
    return s


def slot(item, count=1, worn=0, i=0):
    """One `/inventory` slot. A tool carries damage/maxDamage like the mod reports it; `worn` = uses already spent."""
    item = item if ":" in item else f"minecraft:{item}"
    out = {"id": item, "count": int(count), "slot": i}
    material, _, kind = item.split(":")[1].rpartition("_")
    if material in TOOL_MAX and kind in ("pickaxe", "axe", "sword", "shovel", "hoe"):
        out.update(damage=int(worn), maxDamage=TOOL_MAX[material])
    return out


def inventory(*items, head=None, offhand=None, **counts):
    """An `/inventory` answer. `items` are slot dicts or (item, count[, worn]) tuples; `counts` is item=count."""
    slots = []
    for it in list(items) + list(counts.items()):
        s = it if isinstance(it, dict) else slot(*it)
        slots.append(dict(s, slot=len(slots)))
    eq = {k: dict(AIR) for k in ("head", "chest", "legs", "feet", "offhand")}
    if head:
        eq["head"] = {"id": f"minecraft:{head}", "count": 1}
    if offhand:
        eq["offhand"] = {"id": f"minecraft:{offhand}", "count": 1}
    return {"slots": slots, "selectedSlot": 0, "equipment": eq}


def full_bag(filler="dirt"):
    """Thirty-six stacks of something: no room for anything new."""
    return inventory(*[(filler, 64)] * 36)


def bag(data):
    """The real `world.Inventory`, built from an answer instead of a request."""
    from bonobo.world import Inventory
    return Inventory(data)


def snapshot(st=None, inv=None):
    """The real `world.Snapshot`, from readings (`Snapshot.from_readings`): no world read."""
    from bonobo.world import Snapshot
    return Snapshot.from_readings(st if st is not None else state(), inv if inv is not None else inventory())


def finds(**seen):
    """What /find and /entities saw, {name: distance}, keyed both ways the cost model may ask (bare and namespaced)."""
    out = {}
    for k, v in seen.items():
        name = k.replace("minecraft:", "")
        out[name] = out[f"minecraft:{name}"] = float(v)
    return out


def cost(snap=None, mem=None, **seen):
    """The real `cost.Cost` over readings (`finds=`): what is in sight at what distance, no query made."""
    from bonobo.cost import Cost
    return Cost(snap if snap is not None else snapshot(), mem=mem, finds=finds(**seen))


def places(seconds):
    """A cost model with no snapshot where every kind is `seconds` of walking away (None: nowhere known) — for the
    column solver's tables, which ask only how far things are."""
    from bonobo.cost import TICKS_PER_S, WALK_TICKS_PER_BLOCK, Cost
    blocks = None if seconds is None else max(0.0, (float(seconds) - 2.0) * TICKS_PER_S / WALK_TICKS_PER_BLOCK)
    return Cost(None, known=lambda kinds: blocks)


def places_by(fn):
    """Like `places`, with the seconds decided per kind: fn(kinds) -> seconds or None."""
    from bonobo.cost import TICKS_PER_S, WALK_TICKS_PER_BLOCK, Cost

    def known(kinds):
        s = fn(kinds)
        return None if s is None else max(0.0, (float(s) - 2.0) * TICKS_PER_S / WALK_TICKS_PER_BLOCK)
    return Cost(None, known=known)


# The planner's sweep, as readings. Three dimensions of a situation a plan is made in: what is AROUND us (what the
# look-around saw), what we ARE (the body's /state), and what we already CARRY (/inventory). Every decompose row
# runs over the product, so "the plan contains X" is claimed for every situation and not for one.
RESOURCES = {
    "bare": {},
    "forest": {"oak_log": 6.0, "stone": 3.0},
    "village": {"white_bed": 12.0, "furnace": 10.0, "crafting_table": 9.0, "oak_log": 20.0, "stone": 3.0},
    "seam": {"stone": 1.0, "coal_ore": 5.0, "iron_ore": 7.0, "oak_log": 30.0},
    "herd": {"cow": 10.0, "sheep": 14.0, "pig": 40.0, "oak_log": 12.0, "stone": 3.0},
}
SELF = {
    "ready": {},
    "hungry": {"food": 3},
    "hurt": {"health": 7.0},
    "swimming": {"inWater": True, "onGround": False},
    "underground": {"skyLight": 0, "y": 20.0},
    "nether": {"dimension": "minecraft:the_nether", "skyLight": 0},
}
# The clock, as its own dimension: what upkeep's lead time (bed before dark) is judged against.
TIME = {"day": 2000, "dusk_near": 11700, "night": 18000}
STOCK = {
    "none": {},
    "logs": {"oak_log": 3},
    "wood_tools": {"wooden_pickaxe": 1, "oak_planks": 4, "stick": 4, "crafting_table": 1},
    "stone_tools": {"stone_pickaxe": 1, "stone_sword": 1, "stone_axe": 1, "crafting_table": 1, "furnace": 1},
    "worn_pickaxe": {"stone_pickaxe": ("stone_pickaxe", 1, 130), "crafting_table": 1},
    "iron": {"iron_ingot": 3, "stick": 2, "crafting_table": 1, "furnace": 1, "coal": 8},
    "kit": {"iron_pickaxe": 1, "iron_sword": 1, "cooked_beef": 8, "cobblestone": 64, "golden_helmet": 1,
            "crafting_table": 1, "furnace": 1, "bucket": 1},
    "full_bag": "full",
}


def stock_inventory(name):
    rows = STOCK[name]
    if rows == "full":
        return full_bag()
    return inventory(*[v if isinstance(v, tuple) else (k, v) for k, v in rows.items()])


PLANNER_DIMS = ("resource", "self_", "stock", "time")


class World:
    """One cell of the sweep. The planner reads it as readings (`game_state`, `inventory`, `snapshot`, `cost`);
    the threat model and the dragon fight read it as their own state dicts (`threat_state`, `fight_state`).

    Every dimension is named once, in `DIMS`, and `with_` moves exactly one of them — which is how a relation is
    stated: "the same world, one thing changed, and the number may only move this way".
    """

    def __init__(self, **dims):
        unknown = set(dims) - set(DIMS) - {"elapsed"}
        if unknown:
            raise KeyError(f"no such dimension: {sorted(unknown)}")
        self.dims = dict(DEFAULTS, **{k: v for k, v in dims.items() if k in DIMS})
        self.elapsed = float(dims.get("elapsed", 0.0))
        self.kit = dict(KIT[self.dims["kit"]],
                        sword=WEAPON[self.dims["weapon"]], armour=ARMOUR[self.dims["armour"]],
                        hp=BLOOD[self.dims["blood"]])

    def __getattr__(self, name):
        if name != "dims" and name in self.__dict__.get("dims", {}):
            return self.dims[name]
        raise AttributeError(name)

    # -- the planner's reading: the game's own answers ---------------------------------------------------------
    def game_state(self):
        return state(**dict({"timeOfDay": TIME[self.dims["time"]]}, **SELF[self.dims["self_"]]))

    def inventory(self):
        return stock_inventory(self.dims["stock"])

    def snapshot(self):
        return snapshot(self.game_state(), self.inventory())

    def cost(self):
        return cost(self.snapshot(), **RESOURCES[self.dims["resource"]])

    # -- what is coming at us --------------------------------------------------------------------------------
    @property
    def sword(self):
        return self.kit["sword"]

    @property
    def armour(self):
        return self.kit["armour"]

    @property
    def hp(self):
        return self.kit["hp"]

    @property
    def blocks(self):
        return self.kit["blocks"]

    @property
    def food(self):
        return self.kit["food"]

    @property
    def shield(self):
        return self.kit["shield"]

    def mob_of(self, hazard):
        from bonobo import beliefs
        return beliefs.mob(hazard[3])

    @property
    def here(self):
        return HERE

    def rows(self):
        from bonobo import estimate
        out, i = [], 0
        for kind, count in ENEMIES[self.dims["enemy"]]:
            for _ in range(count):
                gap = RANGE[self.dims["distance"]] + i
                out.append(estimate.row((gap, HERE[1], float(i)), estimate.MOBS[kind]["reach"],
                                        (0.0, 0.0, 0.0), kind))
                i += 1
        return out

    def ground(self):
        from bonobo import field
        bucket, blocks = GROUND[self.dims["ground"]]
        return field.Field(bucket=bucket, blocks=blocks, terrain=field.Terrain(prior=1.0))

    def threat_state(self):
        rows = self.rows()
        return {"here": HERE, "hp": self.kit["hp"], "sword": self.kit["sword"],
                "protection": self.kit["armour"], "night": False, "blocks": self.kit["blocks"],
                "hazards": rows, "ids": list(range(len(rows))),
                "food_items": self.kit["food"], "shield": self.kit["shield"], "field": self.ground(),
                "golden_apples": self.kit.get("golden", 0)}

    def fight_sstate(self):
        from bonobo import threat
        return threat.price_state(hp=max(1, int(self.kit["hp"])), sword=self.kit["sword"], pickaxe=1,
                                 food_items=self.kit["food"], shield=self.kit["shield"], bed=True)

    def price(self):
        """What health costs this body, in seconds (`threat.hp_seconds`), as the fight's caller passes it."""
        from bonobo import threat
        sstate = self.fight_sstate()
        return lambda dhp: threat.hp_seconds(sstate, float(dhp))

    # -- the dragon ------------------------------------------------------------------------------------------
    def fight_state(self):
        from bonobo import fight_plan
        tunnel, bed = BUILT[self.dims["built"]]
        hp, in_cover = FIGHT_BODY[self.dims["fight_body"]]
        return fight_plan.fight_state(
            self_={"pos": (8.0, 65.0, 0.0), "hp": hp, "in_cover": in_cover,
                   "cover": (8, 65, 0) if in_cover else None},
            boss={"phase": PHASES[self.dims["phase"]], "phase_elapsed_s": self.elapsed,
                  "hp": BOSS[self.dims["boss"]]},
            resources={"beds": CARRY[self.dims["carry"]], "obsidian": 0, "water": True, "bow": 0, "arrows": 0},
            terrain={"tunnel_ready": tunnel, "bed_placed": bed, "reinforced": False, "crystals_open": 0})

    def model(self):
        from bonobo import fight_plan
        return fight_plan.Fight()

    # -- moving one variable ---------------------------------------------------------------------------------
    def with_(self, **changes):
        return World(**dict(self.dims, elapsed=self.elapsed, **changes))

    def along(self, dimension):
        """This world, once per value of `dimension`, in declared order: the only honest way to state a direction."""
        return [self.with_(**{dimension: value}) for value in DIMS[dimension]]

    def __repr__(self):
        named = "/".join(str(self.dims[k]) for k in PLANNER_DIMS)
        fight = "/".join(str(self.dims[k]) for k in COMBAT_DIMS)
        return f"World({named} | {fight})"


def _axis(value, whole):
    if value is None:
        return list(whole)
    return [value] if isinstance(value, str) else list(value)


def sweep(**fixed):
    """Every cell of the product over the dimensions named; the rest at their default."""
    import itertools
    varying = {name: _axis(fixed.get(name), DIMS[name]) if name in fixed else [DEFAULTS[name]] for name in DIMS}
    keys = list(varying)
    for combination in itertools.product(*(varying[k] for k in keys)):
        yield World(**dict(zip(keys, combination)), elapsed=fixed.get("elapsed", 0.0))


def worlds(**fixed):
    """The planner's slice of the sweep: around × body × bag × clock."""
    for name in PLANNER_DIMS:
        fixed.setdefault(name, list(DIMS[name]))
    return sweep(**fixed)


HERE = (0.0, 64.0, 0.0)


# ---------------------------------------------------------------- what is coming at us, and what we are in a fight
# The same idea as the planner's dimensions: named once, varied by the sweep. A body is a kit, an enemy is what is
# out there, distance is how far, ground is what we can put between us and it.

ENEMIES = {
    "none": [],
    "walker": [("minecraft:zombie", 1)],
    "archer": [("minecraft:skeleton", 1)],
    "bomb": [("minecraft:creeper", 1)],
    # What the ground cannot answer: a spider climbs the pillar, an enderman appears on top of it. Every shape the
    # threat model offers has to be tested against something it does not work on, or it looks free.
    "climber": [("minecraft:spider", 1)],
    "teleporter": [("minecraft:enderman", 1)],
    "pack": [("minecraft:zombie", 3)],
    "mixed": [("minecraft:zombie", 2), ("minecraft:skeleton", 1)],
}

RANGE = {"touching": 2.0, "near": 5.0, "across": 12.0, "far": 24.0}

GROUND = {"open": ("open", 0), "cave": ("underground", 0), "corridor": ("enclosed", 0),
          "walled": ("enclosed", 2)}

# The body, as four dimensions rather than four bundles. A bundle cannot state a relation: "armed beats bare" used
# to move the weapon, the armour, the blocks, the food AND the health at once, so the baseline moved with it and
# no single-variable claim could be made about anything.
WEAPON = {"fist": 0, "stone": 1, "iron": 2, "diamond": 3}
ARMOUR = {"skin": 0.0, "leather": 0.2, "iron": 0.4, "diamond": 0.7}
BLOOD = {"whole": 20.0, "half": 10.0, "low": 6.0, "dregs": 2.0}
KIT = {                                      # what is in the bag, other than a weapon
    "nothing": {"blocks": 0, "food": 0, "shield": False, "golden": 0},
    "blocks": {"blocks": 32, "food": 0, "shield": False, "golden": 0},
    "full": {"blocks": 64, "food": 16, "shield": True, "golden": 1},     # a golden apple: the one food a fight eats
}

PHASES = {"circling": 0, "landing": 2, "flaming": 3, "sitting": 6}
BOSS = {"whole": 200.0, "hurt": 80.0, "dead": 0.0}
BUILT = {"nothing": (False, False), "tunnel": (True, False), "ready": (True, True)}
CARRY = {"empty": 0, "beds": 6}
FIGHT_BODY = {"fresh": (20.0, True), "hurt": (8.0, True), "exposed": (20.0, False), "dying": (2.0, False)}

HERE = (0.0, 64.0, 0.0)


# ---------------------------------------------------------------- traces, as worlds a measurement reads
# A measurement is only as good as the trace it reads, so the trace is a dimension too: how the body moved, how
# the world moved, and whether the samples arrived on time. Synthetic, because a property about a reading must
# hold for readings nobody could have produced by playing well.

MOTION = {"still": 0.0, "walking": 4.0, "sprinting": 5.6}
APPROACH = {"closing": -3.0, "holding": 0.0, "fleeing": +3.0}     # how the mob's distance changes per second
SAMPLING = {"even": 0.2, "coarse": 1.0, "gappy": 3.0}             # seconds between samples
TELEPORTS = {"none": 0, "once": 1}


def trace(motion="still", approach="closing", sampling="even", teleports="none", seconds=6.0,
          kind="minecraft:zombie", hp=20.0, reach=3.0, start=8.0, bleed=0.0):
    """A trace as the bench records one: {t, hp, pos, near}, built from the dimensions above.

    `bleed` is health lost per second, applied evenly — a measurement of a rate must be checkable against a rate
    it was given, or nothing it says can be trusted.
    """
    step = SAMPLING[sampling]
    speed, closing = MOTION[motion], APPROACH[approach]
    out, t, x, distance, health = [], 0.0, 0.0, start, hp
    jumped = 0
    while t <= seconds + 1e-9:
        out.append({"t": round(t, 2), "hp": round(health, 3), "pos": [round(x, 3), 64.0, 0.0],
                    "near": [(kind, round(max(0.0, distance), 2), 20.0)]})
        t += step
        x += speed * step
        if teleports != "none" and jumped < TELEPORTS[teleports] and t >= seconds / 2:
            x += 5000.0           # a respawn or a /tp: distance the body did not travel
            jumped += 1
        distance = max(0.0, distance + closing * step)
        health = max(0.0, health - bleed * step)
    return out


# ---------------------------------------------------------------- what a walk turned out to cost
# The terrain factor is the one part of arrival that is measured rather than believed, so the samples it learns
# from are a dimension like any other: how much longer the walk took than the straight line.

WALKS = {"quicker": 0.5, "as_the_crow_flies": 1.0, "slower": 2.0, "much_slower": 4.0, "absurd": 10_000.0}

AWARENESS = {"hunting": 1.0, "glimpsed": 0.5, "oblivious": 0.05}     # how much of a mob is actually coming at us


def unaware(rows, aware):
    """The same rows, with the awareness dimension moved — a row is rebuilt through its one constructor."""
    from bonobo import estimate
    return [estimate.row(r[0], r[1], r[2], r[3], aware=aware) for r in rows]


# ---------------------------------------------------------------- the plain numbers, as ladders too
# A property about arithmetic still needs values to feed it, and a test that writes its own is a table nobody else
# can see. These are the ladders for the bare quantities: seconds an action takes, health it spends, the rate we
# are under, and the prices health might be converted at. Order runs worse-to-better like every other ladder.

SECONDS = (0.0, 1.0, 3.0, 8.0)
BLOOD_LOST = (0.0, 1.0, 6.0, 25.0)
RATES = (0.0, 1.0, 4.0, 12.0)
HP_PRICES = {"cheap": lambda hp: 0.5 * hp, "plain": lambda hp: 3.0 * hp, "dear": lambda hp: 40.0 * hp}


# ---------------------------------------------------------------- the survival state, as ladders rather than cases
# The day's state has a dozen fields and each one has an ORDER — more food is never worse, a missed night is never
# better. Declared once, here, so a test states "along this ladder" instead of keeping its own list of values and
# its own idea of which way they run.

LADDERS = {                                  # left to right: worse to better
    "hp": (4, 10, 14, 20), "food": (2, 8, 14, 20), "food_items": (0, 1, 3, 8), "sword": (0, 1, 2, 3),
    "pickaxe": (0, 1, 2, 3), "armor": (0, 2, 4), "shield": (False, True), "bed": (False, True),
    "sheltered": (False, True), "torches": (False, True), "bag_free": (0, 4, 36),
}
WORSE = {                                    # left to right: better to worse
    "nights_missed": (0, 1, 3), "dark": (False, True), "night": (False, True),
}
DUSK = (11000, 6000, 500)                    # ticks until dusk: far off, midday, nearly dark
DAY = dict(hp=14, food=14, food_items=3, sword=1, pickaxe=1, armor=0, shield=False, bed=False, sheltered=False,
           torches=False, bag_free=36, nights_missed=0, dark=False, night=False, ticks_until_dusk=6000)


def day_state(**changes):
    """One survival state: the middle of an ordinary day, with whatever the caller moves."""
    from bonobo import threat
    return threat.price_state(**dict(DAY, **changes))


def along_day(dimension, **fixed):
    """The day's states along one ladder, in the order the ladder declares — the survival half of `World.along`."""
    values = LADDERS.get(dimension) or WORSE[dimension] if dimension != "ticks_until_dusk" else DUSK
    return [day_state(**dict(fixed, **{dimension: value})) for value in values]


# ---------------------------------------------------------------- who holds the body
# The arbiter's own dimensions: what kind of work is running, how long it has been running, and what the layer
# asking for the body says its answer is worth. A bundle would hide the very thing these are for — "has been
# running for a while" must be separable from "costs a lot to abandon".

INTENT = {                                   # {resumable, redo_s}: what abandoning this work would throw away
    "walk": {"resumable": True, "redo_s": 0.0},
    "dig": {"resumable": True, "redo_s": 0.0},
    "window": {"resumable": False, "redo_s": 3.0},        # open-loop: stopping means starting again
}
ELAPSED = {"just_started": 0.0, "a_while": 20.0, "long": 300.0}
WORTH = {"none": 0.0, "small": 5.0, "large": 500.0}
LAYERS = {"reflex": "reflex", "safety": "safety", "tactic": "tactic", "plan": "plan"}

# Who is already holding the body when someone else speaks, relative to the speaker, and how the new answer
# compares with what is held. The rule is the kernel's (kernel.switches): a held decision is kept unless a
# challenger's gain pays the switch, and the arbiter must not invent a second rule for the same thing.
HOLDER = {"none": None, "faster": -1, "same_layer": 0, "slower": +1}
HELD_WORTH = 100.0

# How old the reading a decision was made from is, in seconds. A comparison between two layers is only honest if
# both are looking at roughly the same world; without a timestamp neither can tell.
FRESHNESS = {"now": 0.0, "a_moment": 0.3, "stale": 30.0}


class FakeHeld:
    """A layer's held decision, as the arbiter is allowed to see it: it can be asked whether it still pays, and
    told that the body was refused. It cannot be re-priced from outside — that is the layer's own business."""

    def __init__(self, paying=True):
        self.paying, self.denials = paying, []

    def release(self):
        return not self.paying

    def note_denied(self, why):
        self.denials.append(why)
        self.paying = False      # an assumption that cannot reach the body is not an assumption that holds


def CHALLENGE(kind):
    """What a challenger is worth against a held answer of `HELD_WORTH`."""
    return {"worse": HELD_WORTH / 2.0, "equal": HELD_WORTH, "better": HELD_WORTH * 2.0}[kind]


CHALLENGES = ("worse", "equal", "better")


def faster_than(layer, step=-1):
    """The layer `step` places away in the subsumption order, or None at the end."""
    from bonobo import arbiter
    order = sorted(arbiter.SCALES, key=lambda name: arbiter.SCALES[name])
    i = order.index(layer) + step
    return order[i] if 0 <= i < len(order) else None


def intent(kind="walk", layer="plan", elapsed="just_started", at=0.0, **kw):
    """One running intent, built from the dimensions rather than from a pile of keywords."""
    from bonobo import arbiter
    return arbiter.Intent(LAYERS[layer], lambda: None, kind, at=at, cost_rate=1.0, cost_s=600.0,
                          **dict({"key": kind}, **dict(INTENT[kind], **kw)))


# ---------------------------------------------------------------- one table of dimensions, one product
# Named once, here, where every table above is already in scope. `World` reads them at call time, so the order in
# this file does not matter — what matters is that there is exactly one list of what can vary.

DIMS = {
    # what the planner reads: the game's answers
    "resource": RESOURCES, "self_": SELF, "stock": STOCK, "time": TIME,
    # what is coming at us
    "enemy": ENEMIES, "distance": RANGE, "ground": GROUND,
    "weapon": WEAPON, "armour": ARMOUR, "blood": BLOOD, "kit": KIT,
    # the dragon
    "phase": PHASES, "boss": BOSS, "built": BUILT, "carry": CARRY, "fight_body": FIGHT_BODY,
}

DEFAULTS = {"resource": "bare", "self_": "ready", "stock": "none", "time": "day",
            "enemy": "walker", "distance": "near", "ground": "open",
            # The combat baseline carries everything, so that moving ONE dimension can reach every column.
            "weapon": "stone", "armour": "leather", "blood": "whole", "kit": "full",
            "phase": "sitting", "boss": "whole", "built": "ready", "carry": "beds", "fight_body": "fresh"}


COMBAT_DIMS = ("enemy", "distance", "ground", "weapon", "armour", "blood", "kit")


def dangers(**fixed):
    """The combat slice of the sweep. A view, not a second world: `World.threat_state()` is the reading.

    One dimension off the default at a time, not the full product: seven dimensions multiply to twenty-four
    thousand cells that say the same thing a hundred times, and a relation is stated against ONE variable moving
    anyway. Naming a dimension (`dangers(enemy="bomb")`) pins it and sweeps the rest around it.
    """
    for name in COMBAT_DIMS:
        if name in fixed:
            continue
        for value in DIMS[name]:
            yield World(**dict(fixed, **{name: value}))


def fights(**fixed):
    """The dragon slice of the sweep. `World.fight_state()` is the reading."""
    fixed.setdefault("phase", list(PHASES))
    fixed.setdefault("boss", list(BOSS))
    fixed.setdefault("built", list(BUILT))
    fixed.setdefault("carry", list(CARRY))
    fixed.setdefault("fight_body", list(FIGHT_BODY))
    return sweep(**fixed)


# The names the combat tests were written against. One world, three readings — these are the readings.
Danger = World
Fight = World

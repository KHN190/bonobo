"""Stand-ins the offline tests share: a world without a world.

`world.Region` fetches its blocks from the game the moment it is built, so nothing that reads a region can be
tested without one — and both the route pricing and the building code read regions. This is the same interface,
filled from a dict, so a test can state the terrain it means in three lines.
"""
from bonobo.data import FALLING, HAZARD, PASSABLE, PASSABLE_SUFFIX, PLAYER_MADE_SUFFIX, UNBREAKABLE


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


def planner_brain(mem=None):
    """A Brain with just enough wired up to price things, and no world behind it.

    The pricing door (`Brain.worth_of_change`) needs three things: a memory, an action table, and to know which
    stages of the run are done. A test about prices should not have to start a game to say so.
    """
    import os
    import tempfile
    from bonobo import actions, brain, memory

    b = brain.Brain.__new__(brain.Brain)
    b.mem = mem or memory.Memory(os.path.join(tempfile.mkdtemp(prefix="brain"), "notes.json"))
    b.blacklist = {}
    b.expand_hint = 5

    def action_table(snap, cost_model=None):
        state = {}
        for slot in getattr(snap, "inv", None).slots if getattr(snap, "inv", None) else []:
            state[slot["id"]] = state.get(slot["id"], 0) + slot.get("count", 1)
        return actions.table(actions.Costs(lambda kinds: 20.0), state), state

    b.action_table = action_table
    b.stage_done = lambda name, snap: True          # nothing of the run is outstanding in a pricing test
    return b


class PricingSnap:
    """The snapshot a pricing test needs: a bag, a clock, and nothing else.

    `survival_state` reads a dozen fields off a real snapshot; a test about what a stack of iron is worth should
    not have to build one, and every such test was building its own half of it.
    """
    dimension = "minecraft:overworld"
    feet = (0, 64, 0)
    night = False
    ticks_until_dusk = 6000

    def __init__(self, counts=None, **state):
        counts = counts or {}
        slots = [{"id": item, "count": n} for item, n in counts.items()]
        self.state = {"health": 20, "food": 20, "skyLight": 15, "armor": 0, **state}
        self.inv = _Inv(slots)

    def get(self, key, default=None):
        return self.state.get(key, default)


class _Inv:
    def __init__(self, slots):
        self.slots = slots
        self.equipment = {}

    def tools(self, kind):
        return []

    def count(self, token):
        return sum(s["count"] for s in self.slots if s["id"] == token)

    def usable(self, token):
        return self.count(token)

    def used_slots(self):
        return len(self.slots)

    def free_slots(self):
        return max(0, 36 - len(self.slots))

    def offhand(self):
        return "minecraft:air"


# ---------------------------------------------------------------- the parameterised world the property tests use
# Four dimensions, swept as a product rather than written out as cases: what is AROUND us, what is BETWEEN us and
# it, what we ARE, and what we already CARRY. Every property test runs over the same worlds, so a relation that
# holds "for every world" is stated once and covers hundreds of situations — and adding a dimension covers them
# all again without another test being written.

RESOURCES = {
    "bare": {},
    "village": {"at:white_bed": 1, "at:furnace": 1, "at:crafting_table": 1},
    "seam": {"at:coal_ore": 1, "at:stone": 1},
    "herd": {"at:minecraft:sheep": 1},
}

TERRAIN = {"flat": 6.0, "room": 30.0, "water": 60.0, "lava": 240.0}   # seconds to reach what is over there

# Levers are ABSOLUTE here (what the body is), not deltas: a delta only means something against a base, and a
# world of the sweep IS the base. `value.worth_s` adds deltas on top when it imagines a change.
# What the BODY is. The last three are the body's own preconditions (`actions.body_dims`) as sweep values: a
# swimmer can act but has nothing to stand on, a drowning body must breathe first, a falling one owns nothing.
# They are dimensions of the sweep rather than tests of their own, so every relation this file already states —
# V, Δt, p, κ, worth, the pool's rules — is asked about them too, for free.
SELF = {
    "ready": {"bag_free": 30, "food": 16, "lever:hp": 20, "footing": 1, "hands_free": 1},
    "full_bag": {"bag_free": 1, "food": 16, "lever:hp": 20, "footing": 1, "hands_free": 1},
    "hungry": {"bag_free": 30, "food": 2, "lever:hp": 20, "footing": 1, "hands_free": 1},
    "hurt": {"bag_free": 30, "food": 16, "lever:hp": 8, "footing": 1, "hands_free": 1},
    "swimming": {"bag_free": 30, "food": 16, "lever:hp": 20, "hands_free": 1},
    "drowning": {"bag_free": 30, "food": 16, "lever:hp": 20},
    "falling": {"bag_free": 30, "food": 16, "lever:hp": 16},
}

# A station is not a thing carried, it is a thing that makes half the action table possible: the fuzz table had
# 137 of 218 columns never once admissible, and "needs minecraft:crafting_table, have 0" was almost all of it.
# Without these two values the sweep asks what the planner does in a world where nothing can be crafted.
STOCK = {
    "none": {},
    "has_wool": {"wool": 3, "planks": 3},
    "has_iron": {"minecraft:iron_ingot": 3, "planks": 8},
    "has_tools": {"tool:pickaxe:1": 1, "uses:pickaxe": 120, "tool:sword:1": 1},
    "has_station": {"minecraft:crafting_table": 1, "minecraft:furnace": 1, "planks": 8},
    "has_kit": {"minecraft:crafting_table": 1, "minecraft:furnace": 1, "planks": 16,
                "minecraft:iron_ingot": 3, "minecraft:coal": 8, "minecraft:stick": 4},
}

CONFIDENCE = {"unmeasured": 0.0, "measured": 40.0}    # observations behind the beliefs a world's rates come from


class World:
    """One cell of the sweep: everything a property test needs about a situation, in one object.

    The same cell is read three ways, because the planner, the threat model and the dragon are looking at the same
    world from different sides:

        state()         the solver's state vector — what we hold, what is in reach, what we are
        threat_state()  what is coming at us, on the ground we are standing on
        fight_state()   the dragon's phase, what is left of it, what we have built

    Every dimension is named once, in `DIMS`, and `with_` moves exactly one of them — which is how a relation is
    stated: "the same world, one thing changed, and the number may only move this way".
    """

    def __init__(self, **dims):
        unknown = set(dims) - set(DIMS) - {"elapsed"}
        if unknown:
            raise KeyError(f"no such dimension: {sorted(unknown)}")
        self.dims = dict(DEFAULTS, **{k: v for k, v in dims.items() if k in DIMS})
        self.elapsed = float(dims.get("elapsed", 0.0))
        self.walk_s = TERRAIN[self.dims["terrain"]]
        self.mem = _Notes(CONFIDENCE[self.dims["confidence"]])
        self.kit = dict(KIT[self.dims["kit"]],
                        sword=WEAPON[self.dims["weapon"]], armour=ARMOUR[self.dims["armour"]],
                        hp=BLOOD[self.dims["blood"]])

    def __getattr__(self, name):
        """A dimension reads like an attribute (`world.terrain`), without shadowing the readings: `ground` is a
        method that builds the field, and `world.dims["ground"]` is the name of the ground it builds."""
        if name != "dims" and name in self.__dict__.get("dims", {}):
            return self.dims[name]
        raise AttributeError(name)

    # -- the planner's reading -------------------------------------------------------------------------------
    def state(self):
        """What the solver sees: what we hold, what is within reach, what we are."""
        out = {"tool:pickaxe:0": 1, "uses:pickaxe": 100}
        for part in (RESOURCES[self.dims["resource"]], SELF[self.dims["self_"]], STOCK[self.dims["stock"]]):
            out.update(part)
        return out

    def costs(self):
        """One cost model per world, kept: the columns it answers for are cached against its identity, and a new
        one per call threw that away (and with it most of the time a sweep spends)."""
        from bonobo import actions
        if getattr(self, "_costs", None) is None:
            self._costs = actions.Costs(lambda kinds: self.walk_s)
        return self._costs

    def columns(self, state=None):
        """The columns this world offers, for the state given (its own by default). A FACT the doors are handed."""
        from bonobo import actions
        return actions.table(self.costs(), state if state is not None else self.state())

    def situation(self, state=None, region=None, policy=None):
        """This cell as the four doors see it. One builder, so no test assembles a situation by hand."""
        from bonobo import gates
        state = self.state() if state is None else state
        from bonobo import nav
        return gates.Situation(state=state, columns=self.columns(state), costs=self.costs(), mem=self.mem,
                               region=region, policy=policy, route=nav.estimate_price_s, here=HERE,
                               hp=state.get("lever:hp", 20), inv_free=state.get("bag_free", 36),
                               dark=bool(state.get("lever:dark")))

    def sstate(self):
        from bonobo import gates
        return gates.survival_of(self.state())

    # -- what is coming at us --------------------------------------------------------------------------------
    # What we are, as readings rather than a dict a test indexes into. `world.sword` is the world answering;
    # `world.kit["sword"]` was the test reaching past it into how the kit happens to be stored.
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
        """What the beliefs say about the mob a row came from: the table is the authority, the row is the sample."""
        from bonobo import beliefs
        return beliefs.mob(hazard[3])

    @property
    def here(self):
        """Where we are standing in this world. A reading, not a constant a test keeps: every property about a
        rate or an arrival is about the distance between this and the rows, and a test that carries its own copy
        of one end of that distance is carrying half the world."""
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
                "food_items": self.kit["food"], "shield": self.kit["shield"], "field": self.ground()}

    def fight_sstate(self):
        from bonobo import survival
        return survival.make_state(hp=max(1, int(self.kit["hp"])), sword=self.kit["sword"], pickaxe=1,
                                   food_items=self.kit["food"], shield=self.kit["shield"], bed=True)

    def price(self):
        """What health costs this body, in seconds — the scarcity door, as the caller would pass it."""
        from bonobo import gates
        sstate = self.fight_sstate()
        per_hp = gates.marginal("blood", sstate=sstate)
        return lambda dhp: per_hp * float(dhp)

    # -- the dragon ------------------------------------------------------------------------------------------
    def fight_state(self):
        from bonobo import fight_plan
        tunnel, bed = BUILT[self.dims["built"]]
        hp, in_cover = FIGHT_BODY[self.dims["fight_body"]]
        return fight_plan.make_state(
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
        """The same world with one dimension changed — how a relation between two cells is stated."""
        return World(**dict(self.dims, elapsed=self.elapsed, **changes))

    def along(self, dimension):
        """This world, once per value of `dimension`, in the order the dimension is declared in.

        The only honest way to state a direction. `with_(blood="low")` says nothing on its own — "low" is only
        lower than what the cell happened to start at, and when the sweep moved the cell to `dregs` the claim
        inverted. A ladder has an order; two cells picked by hand do not.
        """
        return [self.with_(**{dimension: value}) for value in DIMS[dimension]]

    def __repr__(self):
        named = "/".join(str(self.dims[k]) for k in ("resource", "terrain", "self_", "stock", "confidence"))
        fight = "/".join(str(self.dims[k])
                         for k in ("enemy", "distance", "ground", "weapon", "armour", "blood", "kit"))
        return f"World({named} | {fight})"


class _Notes:
    """A memory with a dial: how much this world has been observed. Zero means every rate is still a prior."""

    def __init__(self, observations):
        self.observations = float(observations)

    def yield_rate(self, _name):
        return 1.0 if self.observations else 0.5

    def encounter_rate(self, _dark, _underground=False, prior_rate=None):
        return float(prior_rate or 0.0) * (1.0 if self.observations else 1.0)

    def tool_use_rate(self, _kind, prior_rate):
        return float(prior_rate)

    def success_rate(self, _key):
        return 1.0 if self.observations else None

    def duration(self, _key):
        return None

    def search_distance(self, _kind):
        return None

    def resources(self, *_a, **_k):
        return []

    def sightings(self, *_a, **_k):
        return []

    def note_age_s(self, _kinds, _dimension, now=None):
        return 0.0 if self.observations else 3600.0


def _axis(value, whole):
    """One name, a list of names, or the whole dimension."""
    if value is None:
        return list(whole)
    return [value] if isinstance(value, str) else list(value)


def sweep(**fixed):
    """Every cell of the product, or a slice of it.

    `sweep(terrain="flat")` holds terrain still and varies the rest; `sweep(terrain=["water", "lava"])` takes two.
    Dimensions nobody names stay at their default, so a planner test does not pay for the dragon's phases and a
    fight test does not pay for the planner's stock — the product is over what the test actually varies.
    """
    import itertools
    varying = {name: _axis(fixed.get(name), DIMS[name]) if name in fixed else [DEFAULTS[name]] for name in DIMS}
    keys = list(varying)
    for combination in itertools.product(*(varying[k] for k in keys)):
        yield World(**dict(zip(keys, combination)), elapsed=fixed.get("elapsed", 0.0))


def worlds(**fixed):
    """The planner's slice of the sweep: everything it varies, the rest at its default."""
    fixed.setdefault("resource", list(RESOURCES))
    fixed.setdefault("terrain", list(TERRAIN))
    fixed.setdefault("self_", list(SELF))
    fixed.setdefault("stock", list(STOCK))
    fixed.setdefault("confidence", list(CONFIDENCE))
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
    "nothing": {"blocks": 0, "food": 0, "shield": False},
    "blocks": {"blocks": 32, "food": 0, "shield": False},
    "full": {"blocks": 64, "food": 16, "shield": True},
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


def learner(prior=1.0, memory=0.5):
    """A terrain that has observed nothing, for a property about what observing does to it."""
    from bonobo import field
    return field.Terrain(prior=prior, memory=memory)


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
    from bonobo import survival
    return survival.make_state(**dict(DAY, **changes))


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
# compares with what is held. `MARGIN` is the kernel's: a held decision is kept unless a challenger clearly beats
# it, and the arbiter must not invent a second rule for the same thing.
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
    from bonobo import kernel
    return {"worse": HELD_WORTH / kernel.MARGIN / 2.0,
            "equal": HELD_WORTH,
            "better": HELD_WORTH * kernel.MARGIN * 2.0}[kind]


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
                          **dict(INTENT[kind], **kw))


# ---------------------------------------------------------------- one table of dimensions, one product
# Named once, here, where every table above is already in scope. `World` reads them at call time, so the order in
# this file does not matter — what matters is that there is exactly one list of what can vary.

DIMS = {
    # what the planner sees
    "resource": RESOURCES, "terrain": TERRAIN, "self_": SELF, "stock": STOCK, "confidence": CONFIDENCE,
    # what is coming at us
    "enemy": ENEMIES, "distance": RANGE, "ground": GROUND,
    "weapon": WEAPON, "armour": ARMOUR, "blood": BLOOD, "kit": KIT,
    # the dragon
    "phase": PHASES, "boss": BOSS, "built": BUILT, "carry": CARRY, "fight_body": FIGHT_BODY,
}

DEFAULTS = {"resource": "bare", "terrain": "flat", "self_": "ready", "stock": "none", "confidence": "unmeasured",
            "enemy": "walker", "distance": "near", "ground": "open",
            # The combat baseline carries everything, so that moving ONE dimension can reach every column: with
            # an empty-handed baseline no cell of the sweep could both be hurt and hold food, and "eat" was a
            # column no world could offer.
            "weapon": "stone", "armour": "leather", "blood": "whole", "kit": "full",
            "phase": "sitting", "boss": "whole", "built": "ready", "carry": "beds", "fight_body": "fresh"}


# The four dimensions a running game also builds are named in `bonobo.bench.cells` — one vocabulary for the
# offline sweep and the in-game sheet, so a value added to one cannot go missing from the other. Here they are
# seconds and state vectors, there they are setup commands; the NAMES are not ours to redefine.
_SHARED = {"resource": "resource", "terrain": "terrain", "self_": "self", "stock": "stock"}


def _check_shared_vocabulary():
    from bonobo.bench.cells import DIMENSIONS
    for ours, theirs in _SHARED.items():
        here, there = tuple(DIMS[ours]), tuple(DIMENSIONS[theirs])
        if set(here) != set(there):
            raise AssertionError(f"dimension {theirs!r} differs: offline {here} vs in-game {there}")


_check_shared_vocabulary()


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

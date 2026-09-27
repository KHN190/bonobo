"""The action table: every column the solver may use, and the state vector it acts on. Pure given a cost model (cost.Cost). This is the layer that used to be `knowledge.source()` plus `planner.need()`'s recursive descent. The difference is not the data — the recipes are the same — but the shape: each way of changing the world is one column, and the solver combines columns. Three things fall out of that which the descent could not express. Fixed cost versus variable cost. Mining ten coal is one walk and ten breaks. A recursive planner folded the walk into the step and multiplied it by ten, or folded it in once and lost it when the step was split. Here the walk is its own column — `travel:coal_ore` produces the dimension `at:coal_ore` — and `mine:coal` requires that dimension without consuming it. The walk is paid once however much is mined, because the solver runs that column once. Position, therefore, is part of the plan rather than an assumption inside a skill. So is being sheltered, having slept, or standing at the place where the last death dropped its items: they are rows like any other. Several ways to reach one state are several columns (`dig in` / `wall in` / `sleep in a hut`), so the choice is the solver's and depends on what this world costs right now — not an if-chain's order inside a skill. Dimension names are strings, and the only rules are: an item's dimension is its planner token ("log", "planks", "minecraft:coal"), a tool is `tool:<kind>:<tier>` and is cumulative downward (an iron pickaxe produces tier 1 and 2, so "two wooden pickaxes" can never add up to an iron one), a place is `at:<what>`, and a fact is a bare word."""

import math

from .data import (COVERED_SKY, DAY_END, GROUPS, NIGHT_END, TOOL_KINDS, TOOL_MATERIAL_FOR_TIER, VOLATILITY, bare,
                   mid, seen_class)
from .knowledge import (BREED_FOOD, HUNT, HUNT_YIELD, MINE, MINE_YIELD, PLOT_CELLS, RECIPES, STATIONS, TAKEABLE, produced)
from .beliefs import slot_cost_s  # noqa: F401  (one definition, shared with the looter)
from . import estimate
from .solve import Action

TICKS_PER_S = 20.0

# What a fight-back mob costs to hunt: these need a weapon, and the threat layer refuses the fight without one.
# Blocks a tool of each material breaks before it is gone (Minecraft's durability, published, not fitted).
TOOL_USES = {"wooden": 59, "stone": 131, "iron": 250, "diamond": 1561, "netherite": 2031, "golden": 32}

FIGHTERS = {"minecraft:spider", "minecraft:enderman", "minecraft:blaze", "minecraft:slime"}

def exposure_of(action, state):
    """Seconds of damage an action's shape implies, priced by the threat layer."""

    from . import threat
    hazards = [h for h in state.get("hazards", ()) if h[3] in threat.MOBS]
    if not hazards:
        return 0.0
    here = tuple(state["here"])
    prot = float(state.get("protection", 0.0))
    tag = action.tag or ("",)
    press = estimate.pressure_hp_s(here, hazards, prot)
    if tag[0] == "threat" and len(tag) > 1 and tag[1] in ("evade", "wall_in"):
        return estimate.leaving_hp(press, action.cost_s)
    return press * action.cost_s

def with_exposure(action):
    """Teach one column how to price its own exposure. Returns it, so it can wrap a construction."""
    action._exposure = exposure_of
    return action

def uses_dim(kind):
    """How many more blocks this kind of tool can break before it is gone."""

    return f"uses:{kind}"

def tool_dim(kind, tier):
    return f"tool:{kind}:{tier}"

def at(what):
    return f"at:{what}"

# Which groups a token belongs to never changes — the tables are loaded once — but this was being recomputed by
# walking every group for every ingredient of every recipe, several million times a session (21 s of a 129 s
# replay). The membership is worked out once per token, the amounts on top of it.
_GROUPS_OF = {}

def groups_of(token):
    """The groups this token counts toward ("oak_planks" → ("planks",)). Computed once per token."""
    got = _GROUPS_OF.get(token)
    if got is None:
        short, full = bare(token), mid(token)
        got = _GROUPS_OF[token] = tuple(
            group for group, members in GROUPS.items()
            if group != token and (token in members or short in members or full in members))
    return got

def produce(token, n):
    """{dimension: amount} for making `n` of `token`: the item itself and every group it belongs to."""

    out = {token: n}
    for group in groups_of(token):
        out[group] = out.get(group, 0) + n
    return out

def consume(token, n):
    """{dimension: -amount} for spending `n` of `token`."""

    return {d: -v for d, v in produce(token, n).items()}

# ------------------------------------------------------------------------------------------------- the state vector

# The body's own preconditions, as state dimensions. Two, because they fail differently and are MENDED
# differently:
#
#   footing      something solid under the feet — what breaking, placing and building need. Mended by standing
#                on ground, or by putting one block down where we are.
#   hands_free   the body is ours to command and not about to drown. Swimming is NOT a loss of it: crossing
#                water is ordinary movement, and a body that calls itself helpless the moment it gets wet cannot
#                plan its way anywhere across a river. Only real trouble takes it: drowning, a long fall, the
#                player holding the controls.
#
# This distinction is the whole reason there are two: with them merged, "I am swimming" made every column
# unplannable, nothing could be planned, and the one column that could mend it competed against nothing.
BODY_DIMS = ("footing", "hands_free")
# Daylight, the same way: `day` is present while the sun is up. Work out in the open — gathering trees, hunting,
# going to anything on the surface — requires it; sleeping and waiting under cover produce it. So a plan made at
# dusk puts the mining first and the tree after the night, instead of walking into the dark for a log.
DAY_DIM = "day"
NIGHT_S = 420.0               # a night, when the clock cannot say how much of it is left
DROWNING_TICKS = 100          # about five seconds of air left: below this, getting a breath comes first

def body_dims(state):
    """{dimension: 1} for what the body can do where it is. Pure: reads the snapshot, asks the world nothing."""
    state = state or {}
    swimming = bool(state.get("inWater")) and not state.get("onGround", False)
    falling = float(state.get("fallDistance", 0) or 0) > 2.0
    held = bool((state.get("control") or {}).get("paused"))
    drowning = float(state.get("air", 300) or 0) <= DROWNING_TICKS
    out = {}
    if not swimming and state.get("onGround", True):
        out["footing"] = 1
    if not (falling or held or drowning):
        out["hands_free"] = 1
    return out

def state_of(snap, mem, extra=None, reachable=None):
    """The world as a vector, from the snapshot and memory only — no world reads, so recorded rounds still replay."""

    inv = snap.inv
    x = {}
    for slot in inv.slots:
        item = slot["id"]
        n = slot.get("count", 1)
        x[item] = x.get(item, 0) + n
        for group, members in GROUPS.items():
            if item in members or bare(item) in members:
                x[group] = x.get(group, 0) + n
    for kind in TOOL_KINDS:
        usable = [(t, d) for t, d, _ in inv.tools(kind) if d >= 3]
        best = max((t for t, _d in usable), default=None)
        if best is not None:
            for tier in range(0, best + 1):
                x[tool_dim(kind, tier)] = 1
        # Everything of this kind we carry, added up: two half-worn pickaxes dig as far as one whole one.
        x[uses_dim(kind)] = sum(d for _t, d in usable)
    for station in STATIONS:
        if inv.count(station):
            x[station] = x.get(station, 0)
    # A station standing in the world, close enough to walk to, is the same fact as one in the bag: `smelt`
    # requires "minecraft:furnace" and only a CARRIED one ever set it, so the agent beside the furnace it built
    # planned eight more cobblestone and another furnace, round after round. Memory only — no world read.
    for block, pos in _stations_near(snap, mem):
        x[block] = max(x.get(block, 0), 1)
    # Where we already ARE. Without this the state vector never satisfied an `at:` dimension, so the plan's first
    # step stayed `seek` for ever: standing on stone, the agent "sought stone" twice a second, each time arriving
    # instantly and changing nothing, and every stone tool died of it. Read from remembered resource points and
    # sightings only — no world queries, so recorded rounds still replay.
    # Being AT something means being able to work on it, not being near it in a straight line. Standing on the rim
    # of a flooded pit is five blocks from the coal and no route to it, and the radius alone said "at: coal" — so
    # the plan's next step was to mine, the mine failed for want of a way there, and the round repeated. `reachable`
    # is whoever can price a route; without one this is the radius test.
    for what, kinds in _findable():
        if _standing_at(kinds, snap, mem) and (reachable is None or reachable(kinds)):
            x[at(what)] = 1
    x["sheltered"] = 1 if _sheltered(snap, mem) else 0
    x["bag_free"] = max(0, 36 - inv.used_slots())
    x["bed"] = x.get("bed", 0)
    # What the BODY can do here, as dimensions like any other. A column that needs somewhere to stand says so
    # (`"footing": 1`) the same way it says it needs a pickaxe, and the solver drops it when the dimension is
    # zero — instead of every goal planning the same craft, walking into the same water, and discovering the
    # same "no free spot" for itself. Treading water there is nothing to stand on and nothing to place against;
    # falling, riding or being held there is no body to work with.
    x.update(body_dims(getattr(snap, "state", None)))
    night = _is_night(snap)
    if not night:
        x[DAY_DIM] = 1
    else:
        x["clock:dawn_s"] = _dawn_s(snap)
    x.update(extra or {})
    return {d: v for d, v in x.items() if v}

def _is_night(snap):
    night = getattr(snap, "night", None)
    if night is not None:
        return bool(night)
    t = int((getattr(snap, "state", None) or {}).get("timeOfDay", 0)) % 24000
    return DAY_END <= t <= NIGHT_END

def _dawn_s(snap):
    """Seconds until the sun is up again, from the snapshot's clock; a whole night when it cannot say."""
    state = getattr(snap, "state", None) or {}
    if "timeOfDay" not in state:
        return NIGHT_S
    t = int(state["timeOfDay"]) % 24000
    return max(1.0, ((NIGHT_END - t) % 24000) / 20.0)

# Close enough to work on it without walking: the skills' own reach.
ARRIVED_R = 5.0
# Close enough to walk over and use: a station a few steps away is one we have.
STATION_R = 8.0
# What a built machine provides, by the tag the blueprint carries. One table, so a new machine kind is one line.
MACHINE_PROVIDES = {"smelting": "minecraft:furnace", "crafting": "minecraft:crafting_table"}

def _stations_near(snap, mem):
    """[(block id, position)] of the stations and machines within reach of where we stand, from memory alone."""
    out = []
    for s in mem.stations(snap.dimension, near=snap.feet, within=STATION_R):
        out.append((s["block"], tuple(s["pos"])))
    for m in mem.machines(snap.dimension):
        if math.dist(m["origin"], snap.feet) > STATION_R:
            continue
        for tag in m.get("tags", ()):
            if tag in MACHINE_PROVIDES:
                out.append((MACHINE_PROVIDES[tag], tuple(m["origin"])))
    return out

def _standing_at(kinds, snap, mem):
    """Is one of these within arm's reach of where we stand, as far as memory knows?"""
    for kind in kinds:
        # A mob that moves counts only when seen just now: the "here" note's life (data.VOLATILITY).
        within = VOLATILITY["here"]["ttl"] if seen_class(kind) == "mobile" else None
        if any(math.dist(r["pos"], snap.feet) <= ARRIVED_R for r in mem.seen(kind, snap.dimension, within)):
            return True
    return False

def _sheltered(snap, mem):
    if snap.get("skyLight", 15) <= COVERED_SKY:
        return True
    site = mem.nearest_site(snap.feet, snap.dimension, kinds=["home", "shelter"])
    return bool(site) and math.dist(site["pos"], snap.feet) <= 64

# ------------------------------------------------------------------------------------------------- the columns

def work_s(cost, kind, token, count=1):
    """Seconds this piece of work takes: what this kind costs per unit, times how many."""
    return float(cost.work_s(kind, token)) * float(count)

class Table(list):
    """The round's columns, carrying their own identity."""

    __slots__ = ("key",)

    def __init__(self, columns):
        super().__init__(columns)
        self.key = tuple(sorted((a.name, a.cost_s) for a in self))

def table(cost, state, wants=()):
    """Every action available in this world, as solver columns."""

    return Table(base_table(cost) + [with_exposure(a) for a in
                                     _shelter(cost, state) + _room(cost, state)
                                     + _body(cost, state)])

def base_table(cost):
    """The columns that do NOT depend on what we hold — everything but shelter and room."""

    # Cached ON the cost model, not in a table keyed by its id: an id is reused the moment the object is
    # collected, and a recycled one served another world's columns (a room that cost less than flat ground).
    hit = getattr(cost, "_base_columns", None)
    if hit is None:
        out = (_seek(cost) + _gather(cost) + _mine(cost) + _take(cost) + _hunt(cost) + _farm(cost) + _craft(cost)
               + _smelt(cost) + _fill(cost) + _trade(cost))
        hit = [with_exposure(a) for a in out]
        try:
            cost._base_columns = hit
        except AttributeError:
            pass                    # a cost model that will not hold it simply rebuilds; correctness first
    return hit

def _seek(cost):
    """One column per findable thing: go to where one of these is."""

    out = []
    for what, kinds in _findable():
        go_s = cost.seek_s(kinds)
        chance = max(MIN_FIND_P, cost.find_p(kinds))
        out.append(Action(f"seek:{what}", {at(what): 1}, round(go_s / chance, 1), limit=1,
                          requires={DAY_DIM: 1} if what in _SURFACE else {},
                          tag=("seek", what, kinds, cost.where(kinds))))
    return out

# A look that succeeds one time in fifty is not impossible, it is a day's work. The floor keeps the division from
# becoming a wall — "unreachable" is still a price, which is the whole point of the seek column.
MIN_FIND_P = 0.02

def _surface():
    """What is found on the surface, where the dark is dangerous: trees, animals, villages."""
    out = {"tree"}
    out |= {types[0] for types in HUNT.values()}
    out |= {row["blocks"][0] for row in TAKEABLE.values()}
    return frozenset(out)

_SURFACE = _surface()

def _findable():
    """(dimension name, block/entity kinds) for everything worth going to."""
    seen = {}
    for _token, (blocks, _tier) in MINE.items():
        seen.setdefault(blocks[0], list(blocks))
    for _token, types in HUNT.items():
        seen.setdefault(types[0], list(types))
    for row in TAKEABLE.values():
        seen.setdefault(row["blocks"][0], list(row["blocks"]))
    for _token, types in produced("trade"):
        seen.setdefault(types[0], list(types))
    seen.setdefault("tree", list(GROUPS["log"]))
    seen.setdefault("water", ["minecraft:water"])
    return sorted(seen.items())

def _gather(cost):
    return [Action("gather:log", produce("log", 1), work_s(cost, "gather", "log"),
                   requires={at("tree"): 1, "bag_free": 1, "hands_free": 1, DAY_DIM: 1}, tag=("gather", "log"))]

def _mine(cost):
    out = []
    for token, (blocks, tier) in produced("mine"):
        per = MINE_YIELD.get(mid(token), 1)
        # Room to put it is a requirement like any other: a full bag does not stop the digging, it stops the
        # keeping, so the solver must see "make room" as part of the plan rather than discover it by failing.
        requires = {at(blocks[0]): 1, "bag_free": 1}
        effect = produce(token, per)
        if tier is not None:
            requires[tool_dim("pickaxe", tier)] = 1
            effect[uses_dim("pickaxe")] = effect.get(uses_dim("pickaxe"), 0) - 1
        if token == "minecraft:cobblestone":
            effect["stone"] = effect.get("stone", 0) + per     # what recipes and shelters ask for
        requires.update({"hands_free": 1, "footing": 1})
        out.append(Action(f"mine:{token}", effect, work_s(cost, "mine", token), requires=requires,
                          tag=("mine", token, blocks, tier)))
    return out

def _take(cost):
    """One column per finished thing the world already holds: go to it, break it, keep it."""

    out = []
    for token, row in produced("take"):
        requires = {at(row["blocks"][0]): 1, "bag_free": 1}
        tool = row["tool"]
        effect = {}
        for given, count in row["gives"].items():
            for d, v in produce(given, count).items():
                effect[d] = effect.get(d, 0) + v
        if tool is not None:
            kind, tier = tool
            requires[tool_dim(kind, tier)] = 1
            effect[uses_dim(kind)] = effect.get(uses_dim(kind), 0) - 1
        requires.update({"hands_free": 1, "footing": 1})
        out.append(Action(f"take:{token}", effect, float(row["break_s"]), requires=requires,
                          tag=("take", token, list(row["blocks"]))))
    return out

GROW_S = {"crop": 15 * 60, "animal": 20 * 60}     # a wheat plot to ripe; a bred animal to grown (jobs.DURATION)

def _farm(cost):
    """Food that is grown rather than found: the other half of "hunt or farm"."""

    if not produced("farm"):
        return []
    out = [Action("farm:wheat", dict(produce("minecraft:wheat", PLOT_CELLS), **{"minecraft:water_bucket": -1}),
                  work_s(cost, "farm", "wheat") + GROW_S["crop"],
                  requires={tool_dim("hoe", 0): 1, "minecraft:wheat_seeds": PLOT_CELLS, "footing": 1,
                            "hands_free": 1, DAY_DIM: 1}, limit=1, tag=("farm", "wheat"))]
    for meat, types in HUNT.items():
        kind = types[0]
        food = BREED_FOOD.get(kind)
        if food is None or meat not in ("minecraft:beef", "minecraft:porkchop", "minecraft:mutton",
                                        "minecraft:chicken"):
            continue
        per = HUNT_YIELD.get(meat, 1)
        out.append(Action(f"breed:{kind}", dict(produce(meat, per), **{food: -2}),
                          work_s(cost, "breed", kind) + GROW_S["animal"] + work_s(cost, "hunt", meat),
                          requires={at(kind): 1, "hands_free": 1, DAY_DIM: 1, "bag_free": 1}, limit=2,
                          tag=("breed", kind)))
    return out

def _fill(cost):
    """A container filled at a source (fluids.fill_water_bucket): the empty one in, the full one out, at water."""
    return [Action(f"fill:{token}", {token: 1, container: -1}, work_s(cost, "fill", token),
                   requires={at("water"): 1, "hands_free": 1}, tag=("fill", token, container))
            for token, container in produced("fill")]

def _trade(cost):
    """Sold to someone who buys: the trader names the price; the one requirement is standing at one."""

    return [Action(f"trade:{token}", {token: 1}, work_s(cost, "trade", token),
                   requires={at(types[0]): 1, "bag_free": 1, "hands_free": 1, DAY_DIM: 1}, tag=("trade", token, types))
            for token, types in produced("trade")]

def _hunt(cost):
    out = []
    for token, types in produced("hunt"):
        per = HUNT_YIELD.get(token, HUNT_YIELD.get(mid(token), 1))
        requires = {at(types[0]): 1, "bag_free": 1}
        if any(t in FIGHTERS for t in types):
            # It fights back, so it needs a weapon — the same fact the threat layer uses to refuse the fight.
            requires[tool_dim("sword", 1)] = 1
        requires.update({"hands_free": 1, DAY_DIM: 1})     # a fight can happen in water; not in the dark
        out.append(Action(f"hunt:{token}", produce(token, per), work_s(cost, "hunt", token), requires=requires,
                          tag=("hunt", token, types)))
    return out

# What a craft or a smelt DOES never changes — the recipes are a table loaded once. Only what it COSTS depends on
# the round (`work_s` reads the measured durations). Rebuilding the effects every time meant walking every recipe,
# every ingredient and every group on every call: 36 of a 129-second replay, spent re-deriving that four planks
# make a crafting table. The shapes are built once; each round puts its own seconds on them.
_CRAFT_SPEC = None
_SMELT_SPEC = None

def _craft_specs():
    """[(name, token, effect, requires, tag)] for every recipe, computed once."""
    global _CRAFT_SPEC
    if _CRAFT_SPEC is not None:
        return _CRAFT_SPEC
    specs = []
    for token, (pattern, made) in produced("craft_group") + produced("craft"):
        effect = produce(token, made)
        for item in pattern:
            if item:
                for d, v in consume(item, 1).items():
                    effect[d] = effect.get(d, 0) + v
        # Crafting needs the body and, for a 3x3, a station — which may have to be PUT DOWN, so it needs ground
        # to stand on as well. Declared here, once, rather than discovered by each goal when the skill fails.
        requires = {"hands_free": 1}
        if len(pattern) == 9:
            requires.update({"minecraft:crafting_table": 1, "footing": 1})
        specs.append([f"craft:{token}", token, effect, requires, ("craft", token, pattern, made)])
    # Tools are craftable at every tier; the dimensions are cumulative so a tier-2 tool also satisfies tier-1 needs.
    by_name = {spec[0]: spec for spec in specs}
    for name, (pattern, made) in RECIPES.items():
        kind = bare(name).rpartition("_")[2]
        material = bare(name).rpartition("_")[0]
        tier = next((t for t, m in TOOL_MATERIAL_FOR_TIER.items() if m == material), None)
        if tier is None or kind not in TOOL_KINDS:
            continue
        spec = by_name.get(f"craft:{name}")
        if spec is None:
            continue
        for t in range(0, tier + 1):
            spec[2][tool_dim(kind, t)] = 1
        spec[2][uses_dim(kind)] = spec[2].get(uses_dim(kind), 0) + TOOL_USES.get(material, 100)
    _CRAFT_SPEC = specs
    return specs

def _craft(cost):
    return [Action(name, dict(effect), work_s(cost, "craft", token), requires=dict(requires), tag=tag)
            for name, token, effect, requires, tag in _craft_specs()]

def _smelt_specs():
    """[(token, effect, tag)] for every smelt, computed once."""
    global _SMELT_SPEC
    if _SMELT_SPEC is not None:
        return _SMELT_SPEC
    specs = []
    for token, source in produced("smelt"):
        effect = produce(token, 1)
        for d, v in consume(source, 1).items():
            effect[d] = effect.get(d, 0) + v
        for d, v in consume("minecraft:coal", 0.125).items():
            effect[d] = effect.get(d, 0) + v
        specs.append((token, effect, ("smelt", token, source)))
    _SMELT_SPEC = specs
    return specs

def _smelt(cost):
    return [Action(f"smelt:{token}", dict(effect), work_s(cost, "smelt", token),
                   requires={"minecraft:furnace": 1, "hands_free": 1, "footing": 1}, tag=tag)
            for token, effect, tag in _smelt_specs()]

def _body(cost, state):
    """Mending the body's own preconditions — each in the way that is actually cheapest."""

    out = []
    if not state.get("hands_free"):
        # Up. The one answer to being out of air, and never more than the depth away.
        out.append(Action("reach:air", {"hands_free": 1}, work_s(cost, "reach", "air"), limit=1,
                          tag=("reach", "air")))
    if not state.get("footing"):
        out.append(Action("reach:land", {"footing": 1}, work_s(cost, "reach", "land"), limit=1,
                          tag=("reach", "land")))
        # A block under the feet IS footing, and it costs one block and a second. Requires something to place;
        # the solver compares it with the swim and takes whichever is cheaper from here.
        out.append(Action("place:footing", {"footing": 1, "building": -1},
                          work_s(cost, "place", "footing"), limit=1, tag=("reach", "footing")))
    return out

def _room(cost, state):
    """Ways to free bag space. Without these the requirement above would simply make a full bag unplannable."""
    return [
        Action("room:tidy", {"bag_free": 8}, work_s(cost, "room", "tidy"), limit=1, tag=("room", "tidy")),
        Action("room:deposit", {"bag_free": 16}, work_s(cost, "room", "deposit"), limit=1, tag=("room", "deposit")),
    ]

def _shelter(cost, state):
    """Several ways to survive a night, each with its own price. The solver chooses; no if-chain decides."""
    out = [
        Action("shelter:dig in", {"sheltered": 1}, work_s(cost, "shelter", "dig in"),
               requires={tool_dim("pickaxe", 0): 1}, limit=1, tag=("shelter", "dig_in")),
        Action("shelter:wall in", {"sheltered": 1, "building": -9}, work_s(cost, "shelter", "wall in"),
               limit=1, tag=("shelter", "pod")),
        Action("shelter:hut", {"sheltered": 1, "stone": -14, "door": -1, "minecraft:torch": -1},
               work_s(cost, "shelter", "hut"), limit=1, tag=("shelter", "hut")),
    ]
    out.append(Action("sleep", {"slept": 1, at("bed"): 0, DAY_DIM: 1}, work_s(cost, "sleep", "bed"),
                      requires={"bed": 1, "sheltered": 1}, limit=1, tag=("sleep",)))
    if not state.get(DAY_DIM):
        # The other way to morning: sit it out under cover. Priced by what is left of the night, so a bed wins
        # when there is one and waiting wins an hour before dawn.
        out.append(Action("wait:day", {DAY_DIM: 1}, float(state.get("clock:dawn_s", NIGHT_S)),
                          requires={"sheltered": 1}, limit=1, tag=("wait", "day")))
    return out


def target_of(needs):
    """A goal's `needs` as a target vector."""

    target = {}
    for item in needs or ():
        if item and item[0] == "tool" and len(item) == 3:
            _, kind, tier = item
            target[tool_dim(kind, tier)] = 1
        else:
            token, n = item[0], (item[1] if len(item) > 1 else 1)
            target[token] = max(target.get(token, 0), n)
    return target

# ------------------------------------------------------------------------------------------------- execution

def to_step(action, times):
    """A solver column, as the Step the executor already understands, carrying the seconds the column was priced at."""

    step = _shape(action, times)
    step.est = int(round(action.cost_s * times * TICKS_PER_S))
    return step

def _shape(action, times):
    from .planner import Step
    tag = action.tag or ()
    kind = tag[0] if tag else "craft"
    if kind == "seek":
        # The position, when one is known: the seek skill walks there (explore.seek).
        return Step("seek", tag[1], 1, {"kinds": list(tag[2]),
                                        "pos": list(tag[3]) if len(tag) > 3 and tag[3] else None})
    if kind == "reach":
        return Step("reach", tag[1], 1, {})       # "air" (surface), "land" (shore), "footing" (a block underfoot)
    if kind == "gather":
        return Step("gather", "log", times, {})
    if kind == "mine":
        _, token, blocks, tier = tag
        per = MINE_YIELD.get(mid(token), 1)
        return Step("mine", token, max(1, int(round(times * per))),
                    {"blocks": list(blocks), "tier": tier, "breaks": times})
    if kind == "take":
        _, token, blocks = tag
        return Step("take", token, times, {"blocks": list(blocks)})
    if kind == "hunt":
        _, token, types = tag
        per = HUNT_YIELD.get(token, HUNT_YIELD.get(mid(token), 1))
        return Step("hunt", token, max(1, int(round(times * per))), {"types": list(types), "kills": times})
    if kind == "craft":
        _, token, pattern, made = tag
        inputs = {}
        for item in pattern:
            if item:
                inputs[item] = inputs.get(item, 0) + times
        return Step("craft", token, times * made, {"times": times, "inputs": inputs})
    if kind == "smelt":
        _, token, source = tag
        fuel = "coal"
        return Step("smelt", token, times, {"input": source, "fuel": fuel,
                                            "inputs": {source: times, fuel: math.ceil(times / 8)}})
    if kind == "shelter":
        return Step("shelter", tag[1], 1, {})
    if kind == "room":
        return Step("room", tag[1], 1, {})
    if kind == "sleep":
        return Step("sleep", "bed", 1, {})
    if kind == "wait":
        return Step("wait", tag[1], 1, {})
    if kind == "farm":
        return Step("farm", tag[1], times, {})
    if kind == "fill":
        _, token, container = tag
        return Step("fill", token, times, {"container": container})
    if kind == "trade":
        _, token, types = tag
        return Step("trade", token, times, {"types": list(types)})
    if kind == "breed":
        return Step("breed", tag[1], times, {})
    return Step("craft", action.name, times, {"times": times, "inputs": {}})

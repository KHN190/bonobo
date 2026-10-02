"""The action table: every column the solver may use, and the state vector it acts on. Pure given a cost model (cost.Cost). This is the layer that used to be `knowledge.source()` plus `planner.need()`'s recursive descent. The difference is not the data — the recipes are the same — but the shape: each way of changing the world is one column, and the solver combines columns. Three things fall out of that which the descent could not express. Fixed cost versus variable cost. Mining ten coal is one walk and ten breaks. A recursive planner folded the walk into the step and multiplied it by ten, or folded it in once and lost it when the step was split. Here the walk is its own column — `travel:coal_ore` produces the dimension `at:coal_ore` — and `mine:coal` requires that dimension without consuming it. The walk is paid once however much is mined, because the solver runs that column once. Position, therefore, is part of the plan rather than an assumption inside a skill. So is being sheltered, having slept, or standing at the place where the last death dropped its items: they are rows like any other. Several ways to reach one state are several columns (`dig in` / `wall in` / `sleep in a hut`), so the choice is the solver's and depends on what this world costs right now — not an if-chain's order inside a skill. Dimension names are strings, and the only rules are: an item's dimension is its planner token ("log", "planks", "minecraft:coal"), a tool is `tool:<kind>:<tier>` and is cumulative downward (an iron pickaxe produces tier 1 and 2, so "two wooden pickaxes" can never add up to an iron one), a place is `at:<what>`, and a fact is a bare word."""

import math

from .data import (DAY_TICKS, GROUPS, NIGHT_END, is_night, TOOL_KINDS, TOOL_MATERIAL_FOR_TIER, VOLATILITY, bare,
                   mid, seen_class)
from .knowledge import (working, BREED_FOOD, HUNT, HUNT_YIELD, MINE, MINE_YIELD, PLOT_CELLS, RECIPES, STATIONS, TAKEABLE, produced,
                        under_rock, dawn_s, NIGHT_S, MIN_FIND_P)  # noqa: F401
from .beliefs import slot_cost_s  # noqa: F401  (one definition, shared with the looter)
from . import estimate, threat
from .solve import Action
from .planner import Step

TICKS_PER_S = 20.0

# fight-back mobs need a weapon (the threat layer refuses the fight without one); TOOL_USES: published durability
TOOL_USES = {"wooden": 59, "stone": 131, "iron": 250, "diamond": 1561, "netherite": 2031, "golden": 32}

FIGHTERS = {"minecraft:spider", "minecraft:enderman", "minecraft:blaze", "minecraft:slime"}

def exposure_of(action, state):
    """Seconds of damage an action's shape implies, priced by the threat layer."""

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
    """Teach one column to price its own exposure; returns it, so it wraps a construction."""
    action._exposure = exposure_of
    return action

def uses_dim(kind):
    """How many more blocks this kind of tool can break before it is gone."""

    return f"uses:{kind}"

def tool_dim(kind, tier):
    return f"tool:{kind}:{tier}"

def at(what):
    return f"at:{what}"

# token → groups never changes: recomputing it cost 21 s of a 129 s replay
_GROUPS_OF = {}

def groups_of(token):
    """The groups this token counts toward ("oak_planks" → ("planks",)), computed once per token."""
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

# -- the state vector

# `day` while the sun is up: surface work needs it, so a dusk plan mines first and gets the tree after the night
DAY_DIM = "day"
DROWNING_TICKS = 100  # ~5 s of air: below this a breath comes first

def body_dims(state):
    """{dimension: 1} for what the body can do where it is (the snapshot only)."""
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
    """The world as a vector from snapshot and memory only, so recorded rounds replay."""

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
        usable = [(t, d) for t, d, _ in inv.tools(kind) if working(d)]
        best = max((t for t, _d in usable), default=None)
        if best is not None:
            for tier in range(0, best + 1):
                x[tool_dim(kind, tier)] = 1
        # two half-worn pickaxes dig as far as one whole one
        x[uses_dim(kind)] = sum(d for _t, d in usable)
    for station in STATIONS:
        if inv.count(station):
            x[station] = x.get(station, 0)
    # a station standing near is the same fact as one carried (else the agent beside its furnace planned another); memory only
    for block, pos in _stations_near(snap, mem):
        x[block] = max(x.get(block, 0), 1)
    # where we already are, from memory only: being at something means a route to work it, not a radius (a flooded pit's rim is no "at: coal")
    for what, kinds in _findable():
        if _standing_at(kinds, snap, mem) and (reachable is None or reachable(kinds)):
            x[at(what)] = 1
    x["sheltered"] = 1 if _sheltered(snap, mem) else 0
    x["bag_free"] = inv.free_slots()
    x["bed"] = x.get("bed", 0)
    # the body's abilities as dimensions: a column needing footing is dropped when there is none, instead of every goal failing on it
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
    state = getattr(snap, "state", None) or {}
    return is_night(int(state.get("timeOfDay", 0)), state.get("dimension", "minecraft:overworld"))

def _dawn_s(snap):
    return dawn_s(getattr(snap, "state", None) or {})

# Close enough to work on it without walking: the skills' own reach.
ARRIVED_R = 5.0
# Close enough to walk over and use: a station a few steps away is one we have.
STATION_R = 8.0
# What a built machine provides, by the tag the blueprint carries. One table, so a new machine kind is one line.
MACHINE_PROVIDES = {"smelting": "minecraft:furnace", "crafting": "minecraft:crafting_table"}

def _stations_near(snap, mem):
    """[(block id, position)] of stations and machines within reach, from memory alone."""
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
        # a mob counts only when seen just now (the "here" note's life)
        within = VOLATILITY["here"]["ttl"] if seen_class(kind) == "mobile" else None
        if any(math.dist(r["pos"], snap.feet) <= ARRIVED_R for r in mem.seen(kind, snap.dimension, within)):
            return True
    return False

def _sheltered(snap, mem):
    if under_rock(snap.get("skyLight", 15)):
        return True
    site = mem.nearest_site(snap.feet, snap.dimension, kinds=["home", "shelter"])
    return bool(site) and math.dist(site["pos"], snap.feet) <= 64

# -- the columns


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
    """The columns that do not depend on what we hold."""

    # cached on the cost model, not by its id: a recycled id served another world's columns
    hit = getattr(cost, "_base_columns", None)
    if hit is None:
        out = (_seek(cost) + _gather(cost) + _mine(cost) + _take(cost) + _hunt(cost) + _farm(cost) + _craft(cost)
               + _smelt(cost) + _fill(cost) + _trade(cost))
        hit = [with_exposure(a) for a in out]
        try:
            cost._base_columns = hit
        except AttributeError:
            pass  # a cost model that will not hold it simply rebuilds
    return hit

def _seek(cost):
    """One column per findable thing: go to where one of these is."""

    out = []
    for what, kinds in _findable():
        out.append(priced(cost, f"seek:{what}", {at(what): 1}, ("seek", what, kinds, cost.where(kinds)), limit=1,
                          requires={DAY_DIM: 1} if what in _SURFACE else {}))
    return out

def _surface():
    """Surface finds, where the dark is dangerous: trees, animals, villages."""
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
    return [priced(cost, "gather:log", produce("log", 1), ("gather", "log"),
                   requires={at("tree"): 1, "bag_free": 1, "hands_free": 1, DAY_DIM: 1})]

def _mine(cost):
    out = []
    for token, (blocks, tier) in produced("mine"):
        per = MINE_YIELD.get(mid(token), 1)
        # room is a requirement so the solver plans "make room" instead of failing at the keeping
        requires = {at(blocks[0]): 1, "bag_free": 1}
        effect = produce(token, per)
        if tier is not None:
            requires[tool_dim("pickaxe", tier)] = 1
            effect[uses_dim("pickaxe")] = effect.get(uses_dim("pickaxe"), 0) - 1
        if token == "minecraft:cobblestone":
            effect["stone"] = effect.get("stone", 0) + per     # what recipes and shelters ask for
        requires.update({"hands_free": 1, "footing": 1})
        out.append(priced(cost, f"mine:{token}", effect, ("mine", token, blocks, tier), requires=requires))
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
        out.append(priced(cost, f"take:{token}", effect, ("take", token, list(row["blocks"])), requires=requires))
    return out


def _farm(cost):
    """Food that is grown rather than found: the other half of "hunt or farm"."""

    if not produced("farm"):
        return []
    out = [priced(cost, "farm:wheat", dict(produce("minecraft:wheat", PLOT_CELLS), **{"minecraft:water_bucket": -1}),
                  ("farm", "wheat"), requires={tool_dim("hoe", 0): 1, "minecraft:wheat_seeds": PLOT_CELLS, "footing": 1,
                                               "hands_free": 1, DAY_DIM: 1}, limit=1)]
    for meat, types in HUNT.items():
        kind = types[0]
        food = BREED_FOOD.get(kind)
        if food is None or meat not in ("minecraft:beef", "minecraft:porkchop", "minecraft:mutton",
                                        "minecraft:chicken"):
            continue
        per = HUNT_YIELD.get(meat, 1)
        out.append(priced(cost, f"breed:{kind}", dict(produce(meat, per), **{food: -2}), ("breed", kind),
                          requires={at(kind): 1, "hands_free": 1, DAY_DIM: 1, "bag_free": 1}, limit=2))
    return out

def _fill(cost):
    """A container filled at a source (fluids.fill_water_bucket): the empty one in, the full one out, at water."""
    return [priced(cost, f"fill:{token}", {token: 1, container: -1}, ("fill", token, container),
                   requires={at("water"): 1, "hands_free": 1})
            for token, container in produced("fill")]

def _trade(cost):
    """Sold to someone who buys: the trader names the price; the one requirement is standing at one."""

    return [priced(cost, f"trade:{token}", {token: 1}, ("trade", token, types),
                   requires={at(types[0]): 1, "bag_free": 1, "hands_free": 1, DAY_DIM: 1})
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
        out.append(priced(cost, f"hunt:{token}", produce(token, per), ("hunt", token, types), requires=requires))
    return out

# a craft's effect never changes, only its cost: shapes built once (rebuilding cost 36 s of a 129 s replay)
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
        # a 3×3 craft may need its table put down, so ground too
        requires = {"hands_free": 1}
        if len(pattern) == 9:
            requires.update({"minecraft:crafting_table": 1, "footing": 1})
        specs.append([f"craft:{token}", token, effect, requires, ("craft", token, pattern, made)])
    # tool dimensions are cumulative: a tier-2 tool satisfies tier-1 needs
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
    return [priced(cost, name, dict(effect), tag, requires=dict(requires))
            for name, _token, effect, requires, tag in _craft_specs()]

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
    return [priced(cost, f"smelt:{token}", dict(effect), tag,
                   requires={"minecraft:furnace": 1, "hands_free": 1, "footing": 1})
            for token, effect, tag in _smelt_specs()]

def _body(cost, state):
    """Mending the body's own preconditions, each the cheapest way."""

    out = []
    if not state.get("hands_free"):
        # up: the one answer to being out of air
        out.append(priced(cost, "reach:air", {"hands_free": 1}, ("reach", "air"), limit=1))
    if not state.get("footing"):
        out.append(priced(cost, "reach:land", {"footing": 1}, ("reach", "land"), limit=1))
        # a block underfoot is footing for one block and a second; the solver weighs it against the swim
        out.append(priced(cost, "place:footing", {"footing": 1, "building": -1}, ("reach", "footing"), limit=1))
    return out

def _room(cost, state):
    """Ways to free bag space; without them a full bag is unplannable."""
    return [
        priced(cost, "room:tidy", {"bag_free": 8}, ("room", "tidy"), limit=1),
        priced(cost, "room:deposit", {"bag_free": 16}, ("room", "deposit"), limit=1),
    ]

def _shelter(cost, state):
    """Ways to survive a night, each priced; the solver chooses."""
    out = [
        priced(cost, "shelter:dig in", {"sheltered": 1}, ("shelter", "dig_in"), requires={tool_dim("pickaxe", 0): 1},
               limit=1),
        priced(cost, "shelter:wall in", {"sheltered": 1, "building": -9}, ("shelter", "pod"), limit=1),
        priced(cost, "shelter:hut", {"sheltered": 1, "stone": -14, "door": -1, "minecraft:torch": -1}, ("shelter", "hut"),
               limit=1),
    ]
    out.append(priced(cost, "sleep", {"slept": 1, at("bed"): 0, DAY_DIM: 1}, ("sleep",),
                      requires={"bed": 1, "sheltered": 1}, limit=1))
    if not state.get(DAY_DIM):
        # the other way to morning: priced by what is left of the night
        out.append(priced(cost, "wait:day", {DAY_DIM: 1}, ("wait", "day"), requires={"sheltered": 1}, limit=1))
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

# -- execution

def priced(cost, name, effect, tag, requires=None, limit=None):
    """A column priced per unit by Cost.work_ticks (walks are the seek columns')."""
    from types import SimpleNamespace
    # at least one tick: solve.Action refuses a free column
    seconds = max(1, cost.work_ticks(_shape(SimpleNamespace(tag=tag, name=name), 1))) / TICKS_PER_S
    return Action(name, effect, seconds, requires=requires, limit=limit, tag=tag)

def to_step(action, times, cost=None):
    """A solver column as a Step, priced by Cost.estimate when a cost is given."""

    step = _shape(action, times)
    step.est = int(cost.estimate(step)) if cost is not None else int(round(action.cost_s * times * TICKS_PER_S))
    return step

def _shape(action, times):
    tag: tuple = action.tag or ()     # (kind, token, ...) by kind
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

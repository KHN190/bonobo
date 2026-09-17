"""The action table: every column the solver may use, and the state vector it acts on. Pure given a cost oracle.

This is the layer that used to be `knowledge.source()` plus `planner.need()`'s recursive descent. The difference is
not the data — the recipes are the same — but the shape: each way of changing the world is one column, and the
solver combines columns. Three things fall out of that which the descent could not express.

Fixed cost versus variable cost. Mining ten coal is one walk and ten breaks. A recursive planner folded the walk
into the step and multiplied it by ten, or folded it in once and lost it when the step was split. Here the walk is
its own column — `travel:coal_ore` produces the dimension `at:coal_ore` — and `mine:coal` requires that dimension
without consuming it. The walk is paid once however much is mined, because the solver runs that column once.

Position, therefore, is part of the plan rather than an assumption inside a skill. So is being sheltered, having
slept, or standing at the place where the last death dropped its items: they are rows like any other.

Several ways to reach one state are several columns (`dig in` / `wall in` / `sleep in a hut`), so the choice is the
solver's and depends on what this world costs right now — not an if-chain's order inside a skill.

Dimension names are strings, and the only rules are: an item's dimension is its planner token ("log", "planks",
"minecraft:coal"), a tool is `tool:<kind>:<tier>` and is cumulative downward (an iron pickaxe produces tier 1 and 2,
so "two wooden pickaxes" can never add up to an iron one), a place is `at:<what>`, and a fact is a bare word.
"""
import math

from .data import COVERED_SKY, GROUPS, bare, mid
from .survival import CONFIG as _PLAY
from .knowledge import (GROUP_RECIPES, HUNT, HUNT_YIELD, MINE, MINE_YIELD, RECIPES, SMELTS, STATIONS, TAKEABLE,
                        TOOL_MATERIAL_FOR_TIER)
from . import beliefs
from .beliefs import slot_cost_s  # noqa: F401  (one definition, shared with the looter)
from . import estimate
from .solve import Action

TICKS_PER_S = 20.0

# What a fight-back mob costs to hunt: these need a weapon, and the threat layer refuses the fight without one.
# Blocks a tool of each material breaks before it is gone (Minecraft's durability, published, not fitted).
TOOL_USES = {"wooden": 59, "stone": 131, "iron": 250, "diamond": 1561, "netherite": 2031, "golden": 32}

FIGHTERS = {"minecraft:spider", "minecraft:enderman", "minecraft:blaze", "minecraft:slime"}


def exposure_of(action, state):
    """Seconds of damage an action's shape implies, priced by the threat layer.

    Lives here, not in `solve`: the solver is arithmetic and must not know what a zombie is. `table()` attaches it
    to every column it builds, so an action can answer for its own exposure without the solver importing the
    threat model — the shape is the action's business, the pricing is the threat layer's.

    Standing work (mining, crafting, smelting) takes the pressure for its whole duration. Leaving takes it only
    until it is out of reach, which is the difference between "running from a zombie is as lethal as fighting it"
    and the truth.
    """
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


# The rate beliefs live at the bottom (`beliefs`), where the doors can reach them without importing this module.
# These names stay because the columns read them.
use_rate = beliefs.use_rate
expected_uses = beliefs.expected_uses
encounter_prior = beliefs.encounter_prior


def with_exposure(action):
    """Teach one column how to price its own exposure. Returns it, so it can wrap a construction."""
    action._exposure = exposure_of
    return action


def facility_dims(actions):
    """Dimensions that work REQUIRES but never spends: benches, furnaces, being at the ore, being sheltered.

    Leaving one of these behind is a gift to whatever comes next — nobody uses it up, so its lower price is a real
    saving to every later goal. Materials are the opposite: a plan that ends holding less wood has not left wood
    behind, it has spent it, and treating that fall as generosity charges the same work twice (it blew the live
    pool up to a hundred and seventy thousand seconds).

    Read off the actions rather than listed here, so a facility added tomorrow is covered tomorrow.
    """
    required, consumed = set(), set()
    for a in actions:
        required.update(a.requires)
        consumed.update(d for d, delta in a.effect.items() if delta < 0)
    return required - consumed


def left_behind_dims(actions):
    """Facilities that OUTLAST the plan that made them: benches, furnaces, shelter — things the next goal finds
    already there.

    Not every facility qualifies, and the two exceptions are the same shape: they travel with the body rather than
    staying in the world.

    Standing at the coal is required and never spent, but it is not left for anyone — the next goal starts from
    wherever the body ends up, one place and not every place this plan walked through.

    A tool is carried. The one pricing door (`value.worth_s`) already prices it through the fall in the terminal
    goods' own prices, and
    what it wears out is a spent dimension (`uses:`), so treating it as something left behind paid twice over: a
    diamond pickaxe came out as a legacy of eighteen thousand seconds.
    """
    return {d for d in facility_dims(actions)
            if not d.startswith(("at:", "tool:", "uses:"))}


def uses_dim(kind):
    """How many more blocks this kind of tool can break before it is gone.

    A tier says a tool EXISTS; this says how much of it is left. Without it the planner prices a twenty-block
    tunnel against a pickaxe with three points on it, the plan dies in the middle, and `ToolMissing` is discovered
    by failing — the same shape as a full bag, and the same fix: make it a resource the plan can spend and refill.
    """
    return f"uses:{kind}"


def tool_dim(kind, tier):
    return f"tool:{kind}:{tier}"


def at(what):
    return f"at:{what}"


def produce(token, n):
    """{dimension: amount} for making `n` of `token`: the item itself and every group it belongs to.

    Recipes are written against groups ("planks", "coal", "wool") and the world hands out members ("oak_planks",
    "minecraft:coal"). Without this the two halves of the requirement graph never meet — a torch was unplannable
    because mining produced `minecraft:coal` and the recipe asked for `coal`.
    """
    out = {token: n}
    for group, members in GROUPS.items():
        if group == token:
            continue
        if token in members or bare(token) in members or mid(token) in members:
            out[group] = out.get(group, 0) + n
    return out


def consume(token, n):
    """{dimension: -amount} for spending `n` of `token`. Spending a member spends the group with it, or the plan
    could craft with the same logs twice — once as "log", once as "minecraft:oak_log"."""
    return {d: -v for d, v in produce(token, n).items()}


# ------------------------------------------------------------------------------------------------- the state vector

def state_of(snap, mem, extra=None, reachable=None):
    """The world as a vector, from the snapshot and memory only — no world reads, so recorded rounds still replay.

    Items are counted by every token that names them (an oak log counts as "log" and as "minecraft:oak_log"),
    because recipes are written against groups and inventories hold members.
    """
    inv = snap.inv
    x = {}
    for slot in inv.slots:
        item = slot["id"]
        n = slot.get("count", 1)
        x[item] = x.get(item, 0) + n
        for group, members in GROUPS.items():
            if item in members or bare(item) in members:
                x[group] = x.get(group, 0) + n
    for kind in ("pickaxe", "axe", "sword", "shovel", "hoe"):
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
    # is whoever can price a route (`Brain.LiveCost.reach_s`); without one this is the old radius test.
    for what, kinds in _findable():
        if _standing_at(kinds, snap, mem) and (reachable is None or reachable(kinds)):
            x[at(what)] = 1
    x["sheltered"] = 1 if _sheltered(snap, mem) else 0
    x["bag_free"] = max(0, 36 - inv.used_slots())
    x["bed"] = x.get("bed", 0)
    x.update(extra or {})
    return {d: v for d, v in x.items() if v}


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
        for pos in mem.resources(kind, snap.dimension):
            if math.dist(pos, snap.feet) <= ARRIVED_R:
                return True
        for s in mem.sightings(kind, snap.dimension, max_age_min=2):
            if math.dist(s["pos"], snap.feet) <= ARRIVED_R:
                return True
    return False


def _sheltered(snap, mem):
    if snap.get("skyLight", 15) <= COVERED_SKY:
        return True
    site = mem.nearest_site(snap.feet, snap.dimension, kinds=["home", "shelter"])
    return bool(site) and math.dist(site["pos"], snap.feet) <= 64


# ------------------------------------------------------------------------------------------------- the columns

def work_s(cost, kind, token, count=1):
    """Seconds this piece of work takes: what this kind costs per unit, times how many.

    The ENGINE of `gates.takes_s(s, Do(kind, token))`. It lives here, with the table it reads, and the door asks it —
    never the other way round, or the bottom of the package would depend on the top.
    """
    return float(cost.work_s(kind, token)) * float(count)


def table(cost, state, wants=()):
    """Every action available in this world, as solver columns. `cost` answers the two questions an estimate needs:
    `walk_s(kinds)` (seconds to reach the nearest of these blocks/mobs, or None when none is known) and
    `work_s(kind, token)` (seconds per unit of work once there). `wants` narrows the table to what is relevant;
    empty means everything.
    """
    return base_table(cost) + [with_exposure(a) for a in
                               _shelter(cost, state) + _room(cost, state) + _resume(cost, state)]


def base_table(cost):
    """The columns that do NOT depend on what we hold — everything but shelter, room and a resumed step.

    Pricing the future asks for a table once per imagined state, and rebuilding all of it every time is most of
    what that costs (63 of 69 ms in a `gates.V` call). These columns answer to the WORLD, which is not what the
    imagining changes, so they are built once per cost model and shared.
    """
    # Cached ON the cost model, not in a table keyed by its id: an id is reused the moment the object is
    # collected, and a recycled one served another world's columns (a room that cost less than flat ground).
    hit = getattr(cost, "_base_columns", None)
    if hit is None:
        out = _seek(cost) + _gather(cost) + _mine(cost) + _take(cost) + _hunt(cost) + _craft(cost) + _smelt(cost)
        hit = [with_exposure(a) for a in out]
        try:
            cost._base_columns = hit
        except AttributeError:
            pass                    # a cost model that will not hold it simply rebuilds; correctness first
    return hit


def _seek(cost):
    """One column per findable thing: go to where one of these is.

    Ore used to have `prospect` (dig until you hit some) and everything else had `travel`, which only existed when
    the resource map already knew a location. So "no sheep recorded" meant sheep did not exist: wool unreachable,
    therefore no bed, no food, and every goal wanting a furnace unplannable. Finding is one action, and not knowing
    where is a price, not an absence.

    The price is an EXPECTED time, because a look can fail:

        seek_s = (go there + sweep for one) / p(it is here × the note still holds × there is a route)

    Time over chance, and nothing else — κ and V are the outer doors' business. Without the division a thing that
    is not in this biome cost exactly what one underfoot costs: "could not find white_wool" sixty times, at nine
    seconds a try, winning the round every time. Each failed look lowers `p` (`memory.note_look`), so the errand
    prices itself out and the hunt takes over, without anything being banned.
    """
    out = []
    for what, kinds in _findable():
        reach = cost.reach_s(kinds)
        if reach is None:
            go_s = cost.seek_s(kinds)                  # nobody priced a route: the straight line, as before
        elif reach == math.inf:
            go_s = cost.seek_s(kinds, ignore_known=True)   # no route to that one: this errand is another one
        else:
            go_s = max(1.0, round(reach, 1))
        chance = max(MIN_FIND_P, cost.find_p(kinds))
        out.append(Action(f"seek:{what}", {at(what): 1}, round(go_s / chance, 1), limit=1,
                          tag=("seek", what, kinds, cost.where(kinds))))
    return out


# A look that succeeds one time in fifty is not impossible, it is a day's work. The floor keeps the division from
# becoming a wall — "unreachable" is still a price, which is the whole point of the seek column.
MIN_FIND_P = 0.02


# What walks away on its own. A note about one of these decays at the mob half-life, not the block one.
_MOBS = frozenset(sum((list(v) for v in HUNT.values()), []))


def _findable():
    """(dimension name, block/entity kinds) for everything worth going to."""
    seen = {}
    for _token, (blocks, _tier) in MINE.items():
        seen.setdefault(blocks[0], list(blocks))
    for _token, types in HUNT.items():
        seen.setdefault(types[0], list(types))
    for row in TAKEABLE.values():
        seen.setdefault(row["blocks"][0], list(row["blocks"]))
    seen.setdefault("tree", list(GROUPS["log"]))
    seen.setdefault("water", ["minecraft:water"])
    return sorted(seen.items())


def _gather(cost):
    return [Action("gather:log", produce("log", 1), work_s(cost, "gather", "log"),
                   requires={at("tree"): 1, "bag_free": 1}, tag=("gather", "log"))]


def _mine(cost):
    out = []
    for token, (blocks, tier) in MINE.items():
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
        out.append(Action(f"mine:{token}", effect, work_s(cost, "mine", token), requires=requires,
                          tag=("mine", token, blocks, tier)))
    return out


def _take(cost):
    """One column per finished thing the world already holds: go to it, break it, keep it.

    The village the agent spawned in had beds, furnaces, tables and hay in it, and the table could only say
    "craft". A column that says "take" makes the comparison arithmetic — a bed twenty blocks away beats three
    sheep and a shearing, the same bed half a kilometre away does not — and nobody writes the rule.

    Priced like mining, because it IS mining: being there is a requirement (`seek` satisfies it), room in the bag
    is a requirement, the tool is a requirement where the block needs one, and the seconds are the block's own.
    """
    out = []
    for token, row in TAKEABLE.items():
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
        out.append(Action(f"take:{token}", effect, float(row["break_s"]), requires=requires,
                          tag=("take", token, list(row["blocks"]))))
    return out


def _hunt(cost):
    out = []
    for token, types in HUNT.items():
        per = HUNT_YIELD.get(token, HUNT_YIELD.get(mid(token), 1))
        requires = {at(types[0]): 1, "bag_free": 1}
        if any(t in FIGHTERS for t in types):
            # It fights back, so it needs a weapon — the same fact the threat layer uses to refuse the fight.
            requires[tool_dim("sword", 1)] = 1
        out.append(Action(f"hunt:{token}", produce(token, per), work_s(cost, "hunt", token), requires=requires,
                          tag=("hunt", token, types)))
    return out


def _craft(cost):
    out = []
    for token, (pattern, made) in list(GROUP_RECIPES.items()) + [(t, r) for t, r in RECIPES.items()]:
        effect = produce(token, made)
        for item in pattern:
            if item:
                for d, v in consume(item, 1).items():
                    effect[d] = effect.get(d, 0) + v
        requires = {}
        if len(pattern) == 9:
            requires["minecraft:crafting_table"] = 1
        out.append(Action(f"craft:{token}", effect, work_s(cost, "craft", token), requires=requires,
                          tag=("craft", token, pattern, made)))
    # Tools are craftable at every tier; the dimensions are cumulative so a tier-2 tool also satisfies tier-1 needs.
    for name, (pattern, made) in RECIPES.items():
        kind = bare(name).rpartition("_")[2]
        material = bare(name).rpartition("_")[0]
        tier = next((t for t, m in TOOL_MATERIAL_FOR_TIER.items() if m == material), None)
        if tier is None or kind not in ("pickaxe", "axe", "sword", "shovel", "hoe"):
            continue
        for a in out:
            if a.name == f"craft:{name}":
                for t in range(0, tier + 1):
                    a.effect[tool_dim(kind, t)] = 1
                a.effect[uses_dim(kind)] = a.effect.get(uses_dim(kind), 0) + TOOL_USES.get(material, 100)
    return out


def _smelt(cost):
    out = []
    for token, source in SMELTS.items():
        effect = produce(token, 1)
        for d, v in consume(source, 1).items():
            effect[d] = effect.get(d, 0) + v
        for d, v in consume("minecraft:coal", 0.125).items():
            effect[d] = effect.get(d, 0) + v
        out.append(Action(f"smelt:{token}", effect,
                          work_s(cost, "smelt", token), requires={"minecraft:furnace": 1},
                          tag=("smelt", token, source)))
    return out


def _resume(cost, state):
    """One column per piece of half-finished work: the same effect, priced by what is LEFT to do.

    This is where inertia comes from. A commitment is not defended by the time already sunk into it (that would
    weld the agent to whatever it happened to start); it wins because finishing a hole that is two thirds dug is
    cheap. Nothing remembers "I was digging that" — the hole is in the world, and the column is re-derived from it
    every round, so any goal that wants shelter can pick the work up.
    """
    out = []
    for half in cost.half_finished():
        kind, pos = half["kind"], tuple(half["pos"])
        done, of = float(half.get("done", 0)), float(half.get("of", 0) or 0)
        if of <= 0 or done >= of:
            continue
        remaining = (of - done) / of
        where = f"{int(pos[0])},{int(pos[1])},{int(pos[2])}"
        walk = cost.walk_to(pos) or 0.0
        out.append(Action(f"resume:{kind}@{where}", {RESUMES[kind]: 1},
                          max(0.5, work_s(cost, "shelter", kind) * remaining + walk), limit=1,
                          tag=("resume", kind, pos)))
    return out


# What finishing a piece of half-done work produces. Its own dimension, so an unfinished hole is not "sheltered".
RESUMES = {"dig_in": "sheltered", "pod": "sheltered", "hut": "sheltered", "tunnel": "at:depth"}


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
    out.append(Action("sleep", {"slept": 1, at("bed"): 0}, work_s(cost, "sleep", "bed"),
                      requires={"bed": 1, "sheltered": 1}, limit=1, tag=("sleep",)))
    return out


def marginal_batch(step, shadow, demand, bag_free, stack=64):
    """How much to actually take, from the margin rather than from the shortfall.

    The plan asks for what THIS goal needs — one cobblestone for a furnace — and the body pays the same approach,
    task chain and re-decision for one as for forty. But "always take eight" is a constant nobody can defend, and
    it hoards gold as eagerly as stone.

    The margin can be defended, and every term is already computed:

        take one more   while   shadow[token]  >=  pick_s + slot_cost_s(free)/stack

    `shadow` is the round's dual (`solve.reach_cost` / `Plan.shadow`): what one more unit of this token saves
    everything that wants it. `demand` is what the open goals still want of it in total, so the extra is never
    imaginary — gold nothing asks for has demand 0 and is taken exactly as planned, while logs and stone, which
    half the plan passes through, are taken until the bag says stop. No dimensionless factors, no per-item table.
    """
    if step.kind not in ("mine", "gather", "take"):
        return step
    per_unit = float(shadow.get(step.token, 0.0) or 0.0)
    want = int(max(step.count, min(int(demand.get(step.token, 0) or 0), _cap_for(bag_free, stack))))
    pick_s = beliefs.cautious("batch.pick_s", "cost")
    while want > step.count:
        # Cost of the LAST unit of this batch: picking it up, plus the slot it eats, at the fullness it leaves.
        free_after = float(bag_free) - float(want) / float(stack)
        slots_needed = math.ceil(want / float(stack))
        slot_cost = beliefs.slots_cost_s(slots_needed, float(bag_free))
        if per_unit * want >= pick_s * want + slot_cost and free_after >= 1.0:
            break
        want -= 1
    if want <= step.count:
        return step
    per_tick = step.est / step.count if step.count else 0
    step.est = int(round(per_tick * want))
    step.detail["batched"] = True        # one task chain: the body is busy for all of it (priority.step_commitment)
    if step.kind == "mine" and step.detail.get("breaks"):
        step.detail["breaks"] = int(math.ceil(step.detail["breaks"] * want / step.count))
    step.count = want
    return step


def _cap_for(bag_free, stack):
    """The most units the bag could hold, leaving a slot to move in."""
    return int(max(0.0, float(bag_free) - 1.0) * float(stack))


def target_of(needs):
    """A goal's `needs` as a target vector. Accepts both shapes the goals are written in: ("token", n) and
    ("tool", kind, tier)."""
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
    """A solver column, as the Step the executor already understands, carrying the seconds the column was priced at.

    The executor's interface is `Step(kind, token, count, detail)` and it stays that way: which skill carries out a
    piece of work is not the planner's business, and keeping the boundary meant replacing the planner without
    touching a single skill. `tag` is what each column carries for exactly this translation.

    `est` matters as much as the rest of it. A Step built without one defaults to zero ticks, and a step that
    costs nothing is promised the body for nothing: every commitment collapsed to its floor and a fifteen-second
    mine was interrupted every six seconds, round after round, "mine_many outlived the 6.0s commitment". The
    column already knows what it costs — this is the one place that was throwing the number away.
    """
    step = _shape(action, times)
    step.est = int(round(action.cost_s * times * TICKS_PER_S))
    return step


def _shape(action, times):
    from .planner import Step
    tag = action.tag or ()
    kind = tag[0] if tag else "craft"
    if kind == "seek":
        # The position, when one is known, is what lets an errand be priced as an offset from this leg of the
        # journey rather than as a round trip from where we stand (priority.detour_s).
        return Step("seek", tag[1], 1, {"kinds": list(tag[2]),
                                        "pos": list(tag[3]) if len(tag) > 3 and tag[3] else None})
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
    if kind == "resume":
        return Step("resume", tag[1], 1, {"pos": list(tag[2])})
    if kind == "sleep":
        return Step("sleep", "bed", 1, {})
    return Step("craft", action.name, times, {"times": times, "inputs": {}})


# ------------------------------------------------------------------------------------------------- default costs

class Costs:
    """The cost oracle the table needs, over the brain's existing estimates. Distances come from the resource map
    and the snapshot, never from a fresh world query: an estimate that touches the world breaks decision replays."""

    WORK = {("gather", None): 4.0, ("craft", None): 3.0, ("smelt", None): 10.0, ("mine", None): 3.0,
            ("hunt", None): 15.0, ("shelter", "dig in"): 25.0, ("shelter", "wall in"): 40.0,
            ("shelter", "hut"): 120.0, ("sleep", "bed"): 8.0,
            ("room", "tidy"): 15.0, ("room", "deposit"): 60.0}

    def __init__(self, find_distance, unknown_walk_s=300.0):
        self._distance = find_distance
        self.unknown = unknown_walk_s

    def distance(self, kinds):
        """How far the nearest of these is, or None. A FACT about the world, not a duration: what it costs in
        seconds is the time door's business (`gates.takes_s`)."""
        return self._distance(list(kinds))

    def seek_s(self, kinds, ignore_known=False):
        """Seconds to go to one of these: the nearest one we know of, else how far these turn out to be, else the
        declared prior. The ENGINE of `gates.takes_s(s, Seek(kinds))`.

        `ignore_known` prices going to ANOTHER one — the nearest has no route from here, so its distance says
        nothing about what this errand costs."""
        speed = float(_PLAY["player"]["speed"])
        known = None if ignore_known else self.distance(list(kinds))
        if known is None:
            known = self.searched(list(kinds))
        if known is None:
            return float(_PLAY["pool"]["seek_prior_s"])
        return max(1.0, round(float(known) / speed + 2.0, 1))

    def walk_s(self, kinds, misses=0):
        """Seconds to reach the nearest KNOWN one, or None. Each "nothing of this kind here" widens the radius the
        next look must cover, so the errand never dies — it stops being the cheapest thing to do."""
        d = self.distance(list(kinds))
        if d is None:
            return None
        if misses:
            measured = self.searched(list(kinds))
            d = max(d, measured * misses) if measured else d * float(_PLAY["pool"]["search_growth"]) ** int(misses)
        return max(1.0, round(float(d) / float(_PLAY["player"]["speed"]) + 2.0, 1))

    def searched(self, kinds):
        """How far one of these was found at on average, from experience. None until it has happened."""
        return None

    def find_p(self, kinds):
        """The chance a look for one of these finds it. 1.0 here: a cost model with no memory to consult knows of
        no reason to doubt; `LiveCosts` asks `gates.p("find")`."""
        return 1.0

    def where(self, kinds):
        """The position of the nearest known one, or None. Distance alone cannot say whether two errands are in the
        same direction, which is what "on the way" means — and "on the way" is most of a speedrun's saving."""
        return None

    def find_p(self, kinds):
        """The chance a look for one of these finds it. 1.0 here: a cost model with no memory knows of no reason
        to doubt, and the live one (below) asks `gates.p("find")`."""
        return 1.0

    def note_age_s(self, kinds):
        """How old the note about the nearest of these is, in seconds. Zero when nobody has looked (a guess is not
        stale, it is a guess), and what `gates.marginal("staleness")` turns into the extra seconds it costs."""
        return 0.0

    def reach_s(self, kinds):
        """Seconds to get within working reach of the nearest one — the route-aware half of the time door
        (`gates.takes_s(s, Go(here, there))`), asked through the cost oracle because the columns hold the oracle.

        A number, `math.inf` when there is no route from here, or None when nobody has looked.

        This is the one answer "I can see it but cannot get to it" needs. Water, lava, a wall, a drop, nothing to
        bridge with — every reason is already priced by the moves a route is allowed to make, so the planner never
        has to enumerate them, and a dear route simply loses to a cheaper errand."""
        return None

    def work_s(self, kind, token):
        return self.WORK.get((kind, token), self.WORK.get((kind, None), 10.0))

    def half_finished(self):
        """Half-done work the planner may resume. Empty unless the cost oracle knows a memory."""
        return []

    def walk_to(self, pos):
        """Seconds to reach a known position, or None. A FACT provider: the door (`gates.takes_s(s, Go(...))`) is
        what turns a position into seconds when the terrain matters."""
        return None


class LiveCosts(Costs):
    """The cost oracle over the brain's existing distance estimates, so the numbers keep coming from the same place
    (resource map, snapshot, learned durations) and no estimate touches the world."""

    def __init__(self, cost_model):
        super().__init__(self._distance_from(cost_model))
        self.model = cost_model

    # Resource map kinds the travel scan records, and the blocks that count as each.
    MAPPED = {"tree": "log", "water": "water", "iron": "iron_ore", "coal": "coal_ore"}

    def where(self, kinds):
        return self._position(list(kinds))

    def reach_s(self, kinds):
        ask = getattr(self.model, "reach_s", None)
        return ask(list(kinds)) if ask else None

    def find_p(self, kinds):
        """The chance a look for one of these finds it, through the one door that answers chances."""
        from . import gates
        mem = getattr(self.model, "mem", None)
        moving = any(str(k).startswith("minecraft:") and k in _MOBS for k in kinds)
        return gates.p(None, "find", mem=mem, kinds=list(kinds), age_s=self.note_age_s(kinds),
                       moving=moving, reachable=self.reach_s(kinds) != math.inf)

    def note_age_s(self, kinds):
        mem, snap = getattr(self.model, "mem", None), getattr(self.model, "snap", None)
        if mem is None or snap is None or not hasattr(mem, "note_age_s"):
            return 0.0
        return mem.note_age_s(list(kinds), snap.dimension)

    def searched(self, kinds):
        mem = getattr(self.model, "mem", None)
        if mem is None or not hasattr(mem, "search_distance"):
            return None
        seen = [mem.search_distance(k) for k in kinds]
        seen = [d for d in seen if d]
        return sum(seen) / len(seen) if seen else None

    def half_finished(self):
        mem, snap = getattr(self.model, "mem", None), getattr(self.model, "snap", None)
        if mem is None or snap is None or not hasattr(mem, "progress"):
            return []
        return mem.progress(snap.dimension, near=snap.feet)

    def walk_to(self, pos):
        import math as _m
        snap = getattr(self.model, "snap", None)
        if snap is None:
            return None
        return max(0.0, _m.dist(pos, snap.feet)) / 4.3

    def _position(self, kinds):
        import math as _m
        mem, snap = getattr(self.model, "mem", None), getattr(self.model, "snap", None)
        if mem is None or snap is None:
            return None
        here, dim = snap.feet, snap.dimension
        best, best_d = None, None
        for kind, marker in LiveCosts.MAPPED.items():
            if any(marker in k for k in kinds):
                for p in mem.resources(kind, dim):
                    d = _m.dist(p, here)
                    if best_d is None or d < best_d:
                        best, best_d = tuple(p), d
        for k in kinds:
            for sighting in mem.sightings(k, dim) or ():
                p = sighting["pos"] if isinstance(sighting, dict) else sighting
                d = _m.dist(p, here)
                if best_d is None or d < best_d:
                    best, best_d = tuple(p), d
        return best

    @staticmethod
    def _distance_from(model):
        """Distances from memory only: the resource map and remembered sightings, never a fresh query.

        An estimate that touches the world cannot be replayed — a recorded round then misses a /find it never made
        — and this runs for every column of every goal, so it would also be the round's whole HTTP budget.
        """
        import math as _m

        def distance(kinds):
            mem, snap = getattr(model, "mem", None), getattr(model, "snap", None)
            if mem is None or snap is None:
                return None
            here, dim = snap.feet, snap.dimension
            best = None
            for kind, marker in LiveCosts.MAPPED.items():
                if any(marker in k for k in kinds):
                    for p in mem.resources(kind, dim):
                        d = _m.dist(p, here)
                        best = d if best is None else min(best, d)
            for k in kinds:
                for sighting in mem.sightings(k, dim) or ():
                    p = sighting["pos"] if isinstance(sighting, dict) else sighting
                    d = _m.dist(p, here)
                    best = d if best is None else min(best, d)
            return best
        return distance

    def work_s(self, kind, token):
        learned = getattr(self.model, "per_unit_s", None)
        if learned:
            got = learned(kind, token)
            if got:
                return got
        return super().work_s(kind, token)

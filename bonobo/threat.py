"""Ordinary play's threat layer: who can hurt us, how soon, and whether to fight, walk away or wall in. Pure.

The old rule was two if-statements — "hostile within 5 blocks and hp > 10 → attack it", "hp ≤ 10 and hostile within
6 → run straight away" — so a skeleton twelve blocks out shot the agent dead while it kept mining, and a zombie was
punched bare-handed for thirty seconds. Neither statement knew what the enemy does, only where it stands.

Same shape as the fight planner: threats are (centre, reach, velocity, kind) rows with several futures each
(combat_model.hypotheses), the state is a dict, the currency is seconds and hit points, and the answer is the cheapest
option — fight when the expected damage of killing everything leaves a reserve, otherwise leave its reach, or wall in
when leaving costs more than building does. Numbers live in play.toml under [mobs], [player], [engage].
"""
import math

from . import beliefs, combat_model, estimate

CONFIG = beliefs.CONFIG
MOBS = beliefs.MOBS
PLAYER = beliefs.PLAYER
ENGAGE = CONFIG["engage"]
# How long the account runs is `estimate.horizon_s`, and there is only one of it: a second horizon here meant
# pressure was measured over four seconds and charged over twenty.


class Decision:
    """kind: ignore | fight | evade | wall_in. target: entity id (fight) or spot (evade). why: for the log."""

    def __init__(self, kind, target=None, why="", cost_hp=0.0):
        self.kind, self.target, self.why, self.cost_hp = kind, target, why, cost_hp

    def __repr__(self):
        return f"Decision({self.kind}, {self.target}, {self.why!r})"


# -- perception → rows -------------------------------------------------------------------------------------------------

# Facts about mobs. Not decisions: whether to fight one is priced below, but WHICH ones are worth noticing at all
# is a property of the mob.

NEUTRAL_MOBS = {"minecraft:zombified_piglin", "minecraft:piglin", "minecraft:enderman", "minecraft:wolf",
                "minecraft:bee", "minecraft:iron_golem", "minecraft:polar_bear", "minecraft:llama", "minecraft:panda",
                "minecraft:dolphin", "minecraft:spider", "minecraft:cave_spider"}


def awareness(e, here=None):
    """0..1: how much of this mob's damage is actually coming at us.

    Three things about a threat vary, and all three used to be constants: how far it is, whether it has noticed
    us, and how hard it hits. This is the second one, and it replaces the old boolean `is_threat`. A neutral mob
    that has not been provoked is a 0 — not "not a threat", but a threat with nothing coming out of it, which is
    the same number the model already knows how to handle. Anger it and it becomes a 1 without a single branch
    changing anywhere. Outside its detection range a hostile mob is not yet a fight, but it is not nothing either:
    it fades in over the last stretch, so the planner starts drifting away before the aggro line rather than
    discovering it.
    """
    if e.get("type") in NEUTRAL_MOBS and not e.get("angry"):
        return 0.0
    # Being in the hazard table IS the hostility test: `rows` is only ever given types its caller already calls
    # dangerous. Reading `hostile` here as well dropped every dragon body part, which carries no such flag.
    if here is None:
        return 1.0
    d = math.dist(here, (e["x"], e["y"], e["z"]))
    notice = float(ENGAGE["notice_r"])
    if d <= notice:
        return 1.0
    return max(0.0, 1.0 - (d - notice) / notice)


def is_threat(e):
    """Whether this mob is worth noticing at all: hostile, and not a neutral standing about. Kept as a name
    because the perception watcher reads it."""
    return bool(e.get("hostile")) and awareness(e) > 0.0





def rows(near, memory, now, kinds, here=None):
    """(centre, reach, velocity, kind, aware, dps) for every entity whose type is in `kinds` ({type: reach}).

    Velocity is differenced against `memory` ({entity id: (pos, when)}), which the caller keeps between rounds.
    Declaring (0,0,0) instead made every closed-form root return infinity: "nothing is coming" no matter what came.

    The last two are the two things about a threat that a table cannot know: whether it has noticed us, and how
    hard this particular one hits. Rows with no awareness at all are dropped — a neutral mob standing there is not
    a hazard to route around — but the moment it is angered `awareness` returns 1 and it appears, with no branch
    anywhere else changing.
    """
    out = []
    for e in near or []:
        kind = e.get("type")
        if kind not in kinds:
            continue
        pos = (e["x"], e["y"], e["z"])
        vel = (0.0, 0.0, 0.0)
        key = e.get("id")
        if key is not None:
            prev = memory.get(key)
            if prev and 0.01 < now - prev[1] < 2.0:
                dt = now - prev[1]
                vel = tuple((pos[i] - prev[0][i]) / dt for i in range(3))
            memory[key] = (pos, now)
        seen = awareness(e, here)
        if seen <= 0.0:
            continue
        out.append(row(pos, kinds[kind], vel, kind, aware=seen, dps=e.get("dps")))
    for key in [k for k, (_, t) in memory.items() if now - t > 10.0]:
        del memory[key]          # or the table grows for the length of the session
    return out


def hostile_rows(near, memory, now, here=None):
    """Rows for the mobs the table knows. Neutral and unnoticing mobs fall out by having no awareness, not by a
    filter: `rows` drops what is not coming at us (see `awareness`).

    `here` is where we stand; without it every hostile counts as having noticed us, which is the old behaviour and
    the safe direction to be wrong in.
    """
    kinds = {k: float(m["reach"]) for k, m in MOBS.items()}
    return rows(near or [], memory, now, kinds, here=here)


def ids_by_row(near, hazards):
    """Entity id for each row (matched on position), so a fight decision can name its target."""
    by_pos = {(e["x"], e["y"], e["z"]): e.get("id") for e in near or []}
    return [by_pos.get(h[0]) for h in hazards]


# The quantities live in `estimate`; these are the names this module's readers know them by. Aliases, not copies —
# a second definition here is exactly what put four of this agent's deaths in the log. Anything this module adds is
# a COLUMN (an answer, priced) or a SHAPE (a row, a zone), never another arithmetic for one of the five.
protection = beliefs.protection       # one definition, in the belief table
row = estimate.row
arrival = estimate.arrival_s
pressure = estimate.pressure_hp_s
burst_damage = estimate.burst_hp
time_to_die = estimate.time_to_die_s
fight_cost = estimate.fight_cost
leaving_cost = estimate.leaving_hp
hide_ratio = estimate.reaches_share


# -- the model ---------------------------------------------------------------------------------------------------------

def escape_spot(here, hazards, blocks=None, cover=None):
    """Where to walk to leave every threat's reach: away from the dps-weighted centre of the threats, `blocks` far,
    checked with the same slack rule the fight uses; `cover` (a known safe cell) is offered as a candidate."""
    blocks = float(ENGAGE["evade_blocks"]) if blocks is None else blocks
    weights = [float(MOBS.get(h[3], {}).get("dps", 1.0)) for h in hazards]
    wsum = sum(weights) or 1.0
    cx = sum(h[0][0] * w for h, w in zip(hazards, weights)) / wsum
    cz = sum(h[0][2] * w for h, w in zip(hazards, weights)) / wsum
    dx, dz = here[0] - cx, here[2] - cz
    norm = math.hypot(dx, dz)
    if norm < 1e-6:
        dx, dz, norm = 1.0, 0.0, 1.0
    away = (here[0] + blocks * dx / norm, here[1], here[2] + blocks * dz / norm)
    options = [away]
    for a in (math.pi / 4, -math.pi / 4):
        rx = dx * math.cos(a) - dz * math.sin(a)
        rz = dx * math.sin(a) + dz * math.cos(a)
        options.append((here[0] + blocks * rx / norm, here[1], here[2] + blocks * rz / norm))
    if cover is not None:
        options.append(tuple(cover))
    speed = float(PLAYER["speed"])
    best, best_key = None, None
    for opt in options:
        p = pressure(opt, hazards)
        walk_s = math.dist(here, opt) / speed
        key = (-round(p, 3), -round(walk_s, 2))
        if best_key is None or key > best_key:
            best, best_key = opt, key
    return tuple(round(c) for c in best)


def evade_cost(here, spot, hazards, prot):
    """hp lost walking from here to `spot`: the pressure here, over the walk, as an integral."""
    walk_s = math.dist(here, spot) / float(PLAYER["speed"])
    return estimate.leaving_hp(estimate.pressure_hp_s(here, hazards, prot), walk_s)


class Option:
    """One answer to the threats, priced: `hp` lost, `seconds` spent acting, and what it leaves behind.

    Priced, not ranked. The pool converts health into seconds with the survival model and compares these against
    mining and crafting in the same currency — which is the whole point: "run away" and "keep digging" were never
    comparable while one was an if-statement above the other.
    """

    def __init__(self, kind, target, hp, seconds, why, leaves=0.0, heals=0.0, protects=0.0, blast_after=0.0):
        self.kind, self.target, self.hp, self.seconds, self.why = kind, target, hp, seconds, why
        self.heals, self.protects = heals, protects
        self.leaves = leaves          # hp/s still coming at us after this answer (fleeing does not kill anything)
        self.blast_after = blast_after   # one-off damage still owed afterwards; a rate cannot carry an explosion

    def __repr__(self):
        return f"Option({self.kind}, {self.hp}hp, {self.seconds}s, {self.why!r})"

    # -- kernel's action contract (see kernel.py). An option is an action; the field below is the model.

    @property
    def name(self):
        return self.kind

    def effect(self, state):
        """The state afterwards: what is still coming at us for the rest of the work.

        `hp` is not here — it is what the answer COSTS, not what the world looks like after it, and putting a
        transition cost into a state is how the same health came to be counted twice.
        """
        return dict(state, pending_hp=self.leaves * state["work_s"] + self.blast_after)


SHAPES = ("between", "under", "down")


def reshape_options(state, grid, hazards, here, press, prot, blast_here, work_s):
    """Blocking the way, standing on a block and digging down are one column with a different place to put the
    work: each is n units of the same currency (seconds, and blood while exposed) buying the same two effects —
    they take longer to reach us, or they stop being able to see us.

    The effects are heuristics; what they are worth is not. Seconds come from the one price function, as always.
    """
    carried = int(state.get("blocks", 0))
    most = min(carried, int(ENGAGE.get("block_max", 4)))
    if most < 1:
        return []
    nearest = min(hazards, key=lambda h: math.dist(here, h[0]))
    cell = grid.choke(here, nearest[0], within=float(ENGAGE.get("block_reach", 4.0)))
    out = []
    for where in SHAPES:
        if where == "between" and (cell is None or not grid.blocks_worth_placing()):
            continue
        each_s = float(ENGAGE["dig_s"] if where == "down" else ENGAGE["block_s"])
        after = grid
        for n in range(1, most + 1):
            if where == "between":
                after = after.with_block()
            seconds = each_s * n
            # The one pressure function, with the shaped ground and the shape's reach in it. Shaping kills
            # nothing, so what it leaves can never fall below what walking away leaves: the mob is still under the
            # pillar when the shape ends. Without that floor, standing on two blocks priced as if the fight were
            # over and outbid killing a single zombie with an iron sword.
            leaves = max(estimate.pressure_hp_s(here, hazards, prot, ground=after, shape=(where, n)),
                         press * float(ENGAGE["follow_p"]))
            still = [h for h in hazards
                     if arrival(here, h, ground=after) <= work_s]
            blast_after = burst_damage(here, still, prot) if still else 0.0
            if leaves > press - float(ENGAGE["shape_min_gain"]) * max(press, 1e-6) \
                    and blast_after >= blast_here - 1e-6:
                continue
            out.append(Option("reshape", (where, n), round(press * seconds, 2), seconds,
                              f"{where}: {n} × {each_s}s", leaves=round(leaves, 3), blast_after=blast_after))
    return out


def horizon_for(state):
    """Seconds of "carrying on" the options are priced over, which is `estimate.horizon_s` and nothing else.

    It used to stop at our own death, because with a flat horizon every answer priced out as the same certain
    death and the cheapest one (doing nothing) won. That flattening had a different cause — `ignore` was charged
    for its damage AND for the state that damage is — and truncating the horizon was how the symptom was held
    down. With the double count gone the cap does the harm instead: the account ends at death, so dying costs
    whatever health is left and any answer dearer than that looks worse than dying. A hundred and eight cells of
    the swept table stood still with a sword in hand; without the cap, twelve, and those are the ones where
    nothing genuinely helps.
    """
    return estimate.horizon_s(state.get("work_s"))


def options(state):
    """Pure: every answer worth considering, priced. Always includes `ignore` — carrying on is a choice with a cost.

    state: here (x,y,z), hp, sword (tier), protection (0..1), night (bool), blocks (building blocks carried),
           hazards (rows), ids (entity id per row, optional), cover (spot, optional), work_s (how long we would
           stay exposed if we carried on)
    """
    here, hp = tuple(state["here"]), float(state["hp"])
    hazards = [h for h in state.get("hazards", ()) if h[3] in MOBS]
    prot = float(state.get("protection", 0.0))
    grid = state.get("field")
    press = pressure(here, hazards, prot, ground=grid) if hazards else 0.0
    blast_here = burst_damage(here, hazards, prot) if hazards else 0.0
    work_s = horizon_for(state)
    if not hazards or (press <= 0.0 and blast_here <= 0.0):
        # A creeper exerts no pressure — a blast is not a rate — so testing pressure alone made the one threat that
        # must never be ignored the only one that always was.
        return [Option("ignore", None, 0.0, 0.0, "nothing in reach")]
    ids = list(state.get("ids") or [None] * len(hazards))
    out = [Option("ignore", None, 0.0, 0.0,
                  f"carrying on takes ~{press:.1f} hp/s"
                  + (f" and a {blast_here:.0f} hp blast" if blast_here else ""),
                  leaves=press, blast_after=blast_here)]
    # Fighting: kill them, then nothing is coming. Creepers are never traded with — the burst is not a rate.
    if not any(MOBS[h[3]].get("burst") for h in hazards):
        t_fight, lost = fight_cost(here, hazards, state.get("sword", 0), prot)
        nearest = min(range(len(hazards)), key=lambda i: math.dist(here, hazards[i][0]))
        out.append(Option("fight", ids[nearest], lost + blast_here, t_fight,
                          f"kill {len(hazards)} in ~{t_fight}s for ~{lost} hp"))
    spot = escape_spot(here, hazards, cover=state.get("cover"))
    walk_s = round(math.dist(here, spot) / float(PLAYER["speed"]), 2)
    # Leaving costs the walk out AND the walk back: the work is where we were standing. What it does not cost is a
    # discounted forecast of being chased — leaving their reach ends the pressure, and if they follow, that is the
    # next round's situation with its own answer. Predicting it here meant paying for the same threat twice and
    # made every escape look fatal.
    # Leaving does not kill anything. Most of what was coming at us follows, at its own speed, and the same
    # account is opened again next round — which is exactly why killing a zombie can be worth the blood it costs.
    # With `leaves` at zero, walking away was free of everything but the walk, so the fight column existed and was
    # never once chosen: 56 evades, 0 fights in a session's log.
    follows = round(press * float(ENGAGE["follow_p"]), 3)
    out.append(Option("evade", spot, evade_cost(here, spot, hazards, prot),
                      round(walk_s * 2, 2), f"leave their reach, ~{walk_s}s out and back", leaves=follows,
                      blast_after=burst_damage(spot, hazards, prot)))
    hurt = hp < float(PLAYER.get("max_hp", 20))
    if hurt and int(state.get("food_items", 0)) > 0:
        heal = min(float(ENGAGE["eat_heals"]), float(PLAYER.get("max_hp", 20)) - hp)
        eat_s = float(ENGAGE["eat_s"])
        out.append(Option("eat", None, round(press * eat_s + blast_here, 2), eat_s,
                          f"eat: +{heal:.0f} hp for {eat_s}s exposed", leaves=press, heals=heal))
    if state.get("shield") and prot < float(PLAYER["protection_cap"]):
        up = float(ENGAGE["shield_protects"])
        shield_s = float(ENGAGE["shield_s"])
        out.append(Option("shield", None, round(press * shield_s, 2), shield_s,
                          f"shield up: -{up:.0%} of what lands", leaves=press * (1.0 - up), protects=up))
    if grid is not None and int(state.get("blocks", 0)) >= 1:
        for option in reshape_options(state, grid, hazards, here, press, prot, blast_here, work_s):
            out.append(option)
    if int(state.get("blocks", 0)) >= int(ENGAGE["wall_in_blocks"]):
        wall_s = float(ENGAGE["wall_in_s"])
        # A pod stops what has to walk in; what climbs, squeezes or teleports is still coming. That is the one
        # pressure function again, asked about the rows a wall does nothing to — a `leaves` of zero said otherwise
        # and the sweep found the model walling itself in against spiders and endermen.
        through = estimate.pressure_hp_s(here, [h for h in hazards if MOBS[h[3]].get("squeezes")], prot,
                                         ground=grid)
        out.append(Option("wall_in", None, round(press * wall_s + blast_here, 2), wall_s,
                          f"wall in, ~{wall_s}s exposed", leaves=round(through, 3)))
    return out


def action_cost(option, price):
    """kernel's `cost_s` for an option: `estimate.act_cost_s` of what the option spends.

    Health spent acting is a cost, not a state: after the fight the mobs are gone either way, and what separates
    the answers is what getting there took out of us.
    """
    return estimate.act_cost_s(option.seconds, option.hp, price)


class Answer:
    """One option, wearing kernel's action contract. The option prices health and time; what turns that into a
    single `cost_s` is the price of health, which belongs to the caller, so the two are joined here and not in
    `Option` — the same option costs different seconds to a full-health agent and a dying one."""

    __slots__ = ("option", "name", "cost_s")

    def __init__(self, option, price):
        self.option, self.name, self.cost_s = option, option.kind, action_cost(option, price)

    def effect(self, state):
        return self.option.effect(state)

    def __repr__(self):
        return f"Answer({self.name}, {self.cost_s:.1f}s)"


class Field:
    """The threats around us, as a kernel model: one state, one price, a column per answer.

    The same planner the fight uses (kernel.choose), with a horizon of one decision round instead of one fight.
    `decide` and the pool's `saves` are both this — there is no second opinion left to disagree with.

    The state is one number: the health still owed to us if we carry on for `work_s`. Everything else about the
    field (who, where, how fast) is in the options, which price it.
    """

    def __init__(self, state, price=None):
        self.field = state
        self.price_hp = price or (lambda dhp: dhp)
        self.work_s = horizon_for(state)
        self.opts = [Answer(o, self.price_hp) for o in options(state)]
        self.default = next(a for a in self.opts if a.name == "ignore")

    def state(self):
        """The kernel state: what carrying on still owes us, which is exactly what the `ignore` column leaves.

        Read off that column rather than recomputed, so the state and the do-nothing action cannot disagree — when
        they did, `ignore` scored its own damage twice and every other answer looked four times better than the
        planner thought it was.
        """
        return {"pending_hp": self.default.option.leaves * self.work_s + self.default.option.blast_after,
                "work_s": self.work_s}

    def price(self, state):
        return self.price_hp(state["pending_hp"])

    def actions(self, state):
        return self.opts

    def admissible(self, state, option):
        """Nothing here is refused. The veto exists for actions that can strand us — an answer to a threat cannot:
        the worst of them is priced, and a price is the pool's business, not the veto's."""
        return True, ""


def owed(option, work_s):
    """Pure: health still owed to us after this answer — the rate it leaves, over the work, plus any blast that
    still reaches us. The state, in other words: `Option.effect` is this and nothing else."""
    return option.leaves * work_s + option.blast_after


def total_cost(option, price, work_s):
    """Pure: everything this answer costs — the time and health it takes (`action_cost`) plus what it leaves
    owed. THE comparison; there is only this one."""
    return action_cost(option, price) + price(owed(option, work_s))


def saves(option, opts, price, work_s):
    """Pure: seconds this answer saves against carrying on.

    Literally kernel's score — `price(state) − price(effect) − cost_s` — for a caller that already has the options
    in hand (the pool offers each one separately and lets them compete with mining). It was written as the
    difference of two `total_cost`s, which charged carrying on for its damage AND for the state that damage IS:
    the numbers came out about four times too big, and the live bench caught the planner holding `ignore` in a row
    whose columns claimed to save a hundred and sixty seconds.
    """
    doing_nothing = next((o for o in opts if o.kind == "ignore"), None)
    if doing_nothing is None:
        return 0.0
    return estimate.saved_s(price, owed(doing_nothing, work_s), owed(option, work_s),
                            action_cost(option, price))


def decide(state, price=None):
    """Pure: the best answer, decided by `kernel.choose` like every other plan this agent makes.

    `price(hp)` turns health into seconds; without it health counts as itself, which is only right for tests — the
    pool passes the survival model.
    """
    from . import kernel
    field = Field(state, price)
    best = (kernel.choose(field, field.state()).action or field.default).option
    return Decision(best.kind, best.target, best.why, best.hp)


# -- what the fight tells ordinary play ---------------------------------------------------------------------------
# Two things, never a decision: a RATE and a set of circles. The rate is `estimate.pressure_hp_s` — the same
# quantity the columns are priced against, read by the other planner under its own name (`brain.hp_tax_rate`,
# which takes the worse of it and what the health bar is actually doing). There is no tax function here: a second
# name for one number is how the two planners came to disagree about what a corridor costs.


def no_go(state, margin=None):
    """Pure: [(centre, radius)] the normal planner must not route through or plan work inside.

    A field, not an answer. It is true of the world for as long as those mobs are there, so it can be consulted
    when a plan is MADE — which is the whole reason the two planners exchange prices and not decisions.
    """
    margin = float(ENGAGE["no_go_margin"] if margin is None else margin)
    return [(tuple(h[0]), float(h[1]) + margin) for h in state.get("hazards", ()) if h[3] in MOBS]


def inside_no_go(spot, zones):
    """Pure: does this position sit in one of `no_go`'s circles?"""
    return any(math.dist(spot, centre) <= radius for centre, radius in zones)

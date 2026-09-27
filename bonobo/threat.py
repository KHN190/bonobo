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

from . import beliefs, estimate

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
keepoff_cost = estimate.keepoff_cost
time_to_die = estimate.time_to_die_s
fight_cost = estimate.fight_cost
hide_ratio = estimate.reaches_share


# -- the model ---------------------------------------------------------------------------------------------------------

def escape_spot(here, hazards, blocks=None, cover=None, footing=None):
    """Where to walk to leave every threat's reach: away from the dps-weighted centre of the threats, `blocks` far,
    checked with the same slack rule the fight uses; `cover` (a known safe cell) is offered as a candidate.
    `footing(spot)` (terrain.landing over the ground read around us) turns each candidate into the cell a walk
    toward it reaches on connected ground, or None: a spot past a lethal drop is no escape. None when no
    candidate is left — there is nowhere to leave to."""
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
    if footing is not None:
        options = [s for s in (footing(o) for o in options) if s is not None]
    if cover is not None:
        options.append(tuple(cover))
    if not options:
        return None
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
    cap = int(ENGAGE.get("block_max", 4))
    # Digging down spends no blocks — only ground that digs (`dig_ok`: by hand or with the pickaxe carried); the
    # other two shapes spend what is carried. Gating all three on blocks left open ground with a sword and no
    # cobblestone without its cheapest hole.
    most_of = {"between": min(carried, cap), "under": min(carried, cap),
               "down": cap if state.get("dig_ok") else 0}
    if not any(most_of.values()):
        return []
    nearest = min(hazards, key=lambda h: math.dist(here, h[0]))
    cell = grid.choke(here, nearest[0], within=float(ENGAGE.get("block_reach", 4.0)))
    out = []
    for where in SHAPES:
        if where == "between" and (cell is None or not grid.blocks_worth_placing()):
            continue
        each_s = float(ENGAGE["dig_s"] if where == "down" else ENGAGE["block_s"])
        after = grid
        for n in range(1, most_of[where] + 1):
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


def eat_options(state, hp, press, blast_here):
    """Pure: eating in a fight. Ordinary food gives health back only through saturation regen, over the seconds
    after, and only while nothing is hitting us: it is an answer once walled in or left behind (pressure below
    `eat_safe_press`), never mid-melee. A golden apple heals at once, so it is an answer anywhere. A full hunger
    bar cannot eat ordinary food at all."""
    max_hp = float(PLAYER.get("max_hp", 20))
    if hp >= max_hp:
        return []
    eat_s = float(ENGAGE["eat_s"])
    if int(state.get("golden_apples", 0)) > 0:
        heal = min(float(ENGAGE["golden_heals"]), max_hp - hp)
        return [Option("eat", "minecraft:golden_apple", round(press * eat_s + blast_here, 2), eat_s,
                       f"a golden apple: +{heal:.0f} hp now", leaves=press, heals=heal)]
    if int(state.get("food_items", 0)) > 0 and estimate.eat_due(float(state.get("hunger", 0)), hp, 0, max_hp, float(_R["food_full"])) \
            and press <= float(ENGAGE["eat_safe_press"]):
        heal = min(float(ENGAGE["eat_heals"]), max_hp - hp)
        return [Option("eat", None, round(press * eat_s + blast_here, 2), eat_s,
                       f"eat: +{heal:.0f} hp by regen, nothing reaching us", leaves=press, heals=heal)]
    return []


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


def _evade_option(here, spot, hazards, prot, press, out):
    """Pure: the evade column to `spot`, priced against the options already in `out` (a fight on offer)."""
    walk_s = round(math.dist(here, spot) / float(PLAYER["speed"]), 2)
    # Leaving costs the walk out AND the walk back: the work is where we were standing. What it does not cost is a
    # discounted forecast of being chased — leaving their reach ends the pressure, and if they follow, that is the
    # next round's situation with its own answer. Predicting it here meant paying for the same threat twice and
    # made every escape look fatal.
    # Leaving does not kill anything. Most of what was coming at us follows, at its own speed, and the same
    # account is opened again next round — which is exactly why killing a zombie can be worth the blood it costs.
    # With `leaves` at zero, walking away was free of everything but the walk, so the fight column existed and was
    # never once chosen: 56 evades, 0 fights in a session's log.
    # ...unless nothing is left behind at all: when every threat would still be after us at the spot (it notices
    # us there, or shoots that far) and a fight is on offer, leaving only postpones the same account — a
    # skeleton, a zombie, a creeper out-walked for a minute was a fight never had (bench: evade for 60 s at 20 hp).
    # With no fight to have (打不过就走), leaving is still the relief it was.
    postpones = any(o.kind == "fight" for o in out) and all(estimate.follows_to(spot, h) for h in hazards)
    follows = round(press if postpones else press * float(ENGAGE["follow_p"]), 3)
    # A creeper is not a rate that leaving ends: out and back, it is still there, or it followed. With a fight on
    # offer its blast stays owed (as if it reached us); with none, leaving is the relief it always was.
    fight_on = any(o.kind == "fight" for o in out)
    blast = burst_damage(here, hazards, prot, fuse_s=float("inf")) if fight_on else burst_damage(spot, hazards, prot)
    return Option("evade", spot, evade_cost(here, spot, hazards, prot),
                  round(walk_s * 2, 2), f"leave their reach, ~{walk_s}s out and back", leaves=follows,
                  blast_after=blast)


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
    # Fighting: kill them, then nothing is coming. A creeper is never traded with standing — the burst is not a
    # rate — but with a sword it is fought hit-and-back (`estimate.keepoff_cost`, jar footwork "keepoff"), first:
    # it is met sooner or later wherever we go, and walking away from it only postpones the same creeper.
    sword = int(state.get("sword", 0))
    creepers = [i for i, h in enumerate(hazards) if MOBS[h[3]].get("burst") and h[3] == "minecraft:creeper"]
    if creepers and sword >= 1 and all(MOBS[h[3]].get("burst") is None or i in creepers
                                       for i, h in enumerate(hazards)):
        first = min(creepers, key=lambda i: math.dist(here, hazards[i][0]))
        t_c, lost_c = keepoff_cost(here, hazards[first], sword, prot)
        rest = [h for i, h in enumerate(hazards) if i != first]
        t_r, lost_r = fight_cost(hazards[first][0], rest, sword, prot) if rest else (0.0, 0.0)
        if lost_c + lost_r < hp:
            out.append(Option("fight", ids[first], round(lost_c + lost_r, 2), round(t_c + t_r, 2),
                              f"kill the creeper hit-and-back in ~{t_c}s"
                              + (f", then {len(rest)} more" if rest else "")))
    t_fight, lost = fight_cost(here, hazards, sword, prot)
    # A fight we expect to lose is not an answer (打不过就走): what it takes has to leave us standing.
    if not any(MOBS[h[3]].get("burst") for h in hazards) and lost + blast_here < hp:
        nearest = min(range(len(hazards)), key=lambda i: math.dist(here, hazards[i][0]))
        out.append(Option("fight", ids[nearest], lost + blast_here, t_fight,
                          f"kill {len(hazards)} in ~{t_fight}s for ~{lost} hp"))
        if state.get("shield"):
            # The same fight with the shield up between swings (the attack's cooldown): what lands is cut by what a
            # raised shield stops, for the time raising it takes once per kill.
            kept = round(lost * (1.0 - float(ENGAGE["shield_protects"])), 2)
            t_guard = round(t_fight + float(ENGAGE["shield_s"]) * len(hazards), 2)
            out.append(Option("fight_shielded", ids[nearest], kept + blast_here, t_guard,
                              f"kill {len(hazards)} in ~{t_guard}s behind the shield for ~{kept} hp"))
    spot = escape_spot(here, hazards, cover=state.get("cover"), footing=state.get("footing"))
    if spot is not None:             # else nowhere to leave to (a lethal drop all round): fight, eat, wall in
        out.append(_evade_option(here, spot, hazards, prot, press, out))
    for option in eat_options(state, hp, press, blast_here):
        out.append(option)
    if state.get("shield") and prot < float(PLAYER["protection_cap"]):
        up = float(ENGAGE["shield_protects"])
        shield_s = float(ENGAGE["shield_s"])
        out.append(Option("shield", None, round(press * shield_s, 2), shield_s,
                          f"shield up: -{up:.0%} of what lands", leaves=press * (1.0 - up), protects=up))
    if grid is not None:
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


def action_cost(option, price, work_s=None):
    """kernel's `cost_s` for an option: its seconds, plus the health it spends priced ON TOP of what it still
    leaves owed (`work_s` given) — one price of all the damage, not two. Priced apart, two sums past what kills
    each counted a whole death, so at four hearts leaving cost more than staying and the agent stood still.

    Health spent acting is a cost, not a state: after the fight the mobs are gone either way, and what separates
    the answers is what getting there took out of us.
    """
    if work_s is None:
        return estimate.act_cost_s(option.seconds, option.hp, price)
    left = owed(option, work_s)
    return float(option.seconds) + price(left + float(option.hp)) - price(left)


class Answer:
    """One option, wearing kernel's action contract. The option prices health and time; what turns that into a
    single `cost_s` is the price of health, which belongs to the caller, so the two are joined here and not in
    `Option` — the same option costs different seconds to a full-health agent and a dying one."""

    __slots__ = ("option", "name", "cost_s")

    def __init__(self, option, price, work_s=None):
        self.option, self.name, self.cost_s = option, option.kind, action_cost(option, price, work_s)

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
        self.opts = [Answer(o, self.price_hp, self.work_s) for o in options(state)]
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
    return action_cost(option, price, work_s) + price(owed(option, work_s))


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
                            action_cost(option, price, work_s))


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


# ------------------------------------------------------------------------------- the price of health
# What losing health costs in seconds depends on the state it is lost from: a chance of dying plus a loss of margin
# against a day of ordinary risk. The threat layer prices every answer through `hp_seconds`; nothing else here is a
# planner any more — the survival brain runs a fixed order and does not score.
_T, _R, _K = beliefs.CONFIG["time"], beliefs.CONFIG["risk"], beliefs.CONFIG["tools"]


def bag_loss(s):
    """Seconds lost to a full bag: work whose output falls on the floor.

    A full bag does not stop the agent, it stops the agent from KEEPING anything — so the next stretch of mining
    is time spent for nothing. That cost was never in the model; tidying was worth a legacy three points (thirty
    seconds) and lost to whatever else was going, while the bag stayed full and the ore kept dropping.
    """
    free = float(s.get("bag_free", 36))
    if free >= _R["bag_comfortable"]:
        return 0.0
    share = (_R["bag_comfortable"] - free) / _R["bag_comfortable"]
    return share * _T["day_s"] * _K["mining_share_of_day"]



def price_state(**kw):
    """The survival state health and time are priced in (`hp_seconds`); unknown keys are refused."""
    s = {"night": False, "ticks_until_dusk": 6000, "hp": 20, "food": 20, "bed": False, "sheltered": False,
         "torches": False, "sword": 0, "pickaxe": 0, "food_items": 0, "nights_missed": 0, "armor": 0,
         "shield": False, "bag_free": 36,
         # Dark where we stand, which is where mobs come from. Not the same as night: a torch-lit camp at midnight
         # is safe and a cave at noon is not.
         "dark": False}
    unknown = set(kw) - set(s)
    if unknown:
        raise KeyError(f"not survival state: {sorted(unknown)}")
    s.update(kw)
    return s


def _protection(s):
    """This state's damage reduction, from the belief table. Private: `beliefs.protection` is the one owner, and a
    second public function of the same name is how the same chestplate came to be worth two different things."""
    return beliefs.protection(s["armor"], s["shield"])


def encounter_damage(s):
    """(seconds, health) one ordinary encounter costs at this weapon and armour.

    One reference mob, met at arm's length, priced by the same `estimate.fight_cost` the threat layer uses to
    decide whether to swing at the real thing. They were two arithmetics over one question — what a fight costs —
    so a sword could be worth making and not worth using.
    """
    kind = _R["reference_mob"]
    here = (0.0, 0.0, 0.0)
    row = estimate.row((float(beliefs.PLAYER["melee_reach"]), 0.0, 0.0), beliefs.mob(kind)["reach"],
                       (0.0, 0.0, 0.0), kind)
    return estimate.fight_cost(here, [row], s["sword"], _protection(s))


_fatal_chance = estimate.fatal_chance     # one curve, in the module that owns the five quantities


def fight_loss(s):
    """Seconds a day of ordinary encounters costs at this weapon and armour — at full health.

    Full health on purpose: over a day the bar refills, so the day's risk is a property of the gear, not of this
    minute's health. What being hurt RIGHT NOW costs is `hurt_loss`, and keeping them apart is what stopped the
    model from saying that dying was cheaper than being at four hearts (it priced four hearts as if they lasted
    all day, which came to more than the cost of respawning).
    """
    kill_s, damage = encounter_damage(s)
    return _R["encounters_per_day"] * (kill_s + _fatal_chance(20, damage) * _T["death_cost_s"])


def hurt_loss(s):
    """Seconds the current health deficit costs: the time to regenerate it, plus the extra chance of dying in the
    encounters that happen before it is back."""
    hp = max(0.1, float(s["hp"]))
    if hp >= 20:
        return 0.0
    _kill_s, damage = encounter_damage(s)
    regen_s = (20.0 - hp) * _R["regen_s_per_hp"]
    meetings = _R["encounters_per_day"] * regen_s / _T["day_s"]
    extra = _fatal_chance(hp, damage) - _fatal_chance(20, damage)
    return regen_s + meetings * max(0.0, extra) * _T["death_cost_s"]


def night_loss(s):
    """Seconds the coming night is expected to cost. A bed skips it — but only where we can sleep: a bed in the
    open is interrupted by the mobs standing over it, which is why a shelter is worth building even carrying one."""
    if s["bed"]:
        return 0.0 if s["sheltered"] else _R["night_bed_open"] * (1.0 - _protection(s)) * _T["death_cost_s"]
    p = _R["night_sheltered"] if s["sheltered"] else _R["night_open"]
    if s["sword"] == 0:
        p += _R["no_sword_night"]
    if s["nights_missed"] >= 3:
        p += _R["phantom_night_death"]
    # Without a bed the night is also 420 s of not working (mining underground counts as working; the open does not).
    idle = 0.0 if s["sheltered"] else _T["night_s"]
    return p * (1.0 - _protection(s)) * _T["death_cost_s"] + idle


def hunger_loss(s):
    """Seconds the CURRENT hunger costs before the next meal: work lost to not sprinting and not regenerating.

    The bar itself, not the larder. Without this term eating was worth exactly nothing — `food_loss` looked only at
    how many meals were carried, and eating one does not change that count, so the benefit of eating was zero at
    every hunger level and the agent starved with a full bag of cooked pork.
    """
    food = float(s["food"])
    if food >= _R["food_full"]:
        return 0.0
    span = _T["day_s"] * _R["meal_share_of_day"]        # how long this hunger has to be carried
    slowed = (_R["food_full"] - food) / _R["food_full"] * _R["hunger_slowdown"]
    if food <= _R["food_low"]:
        slowed = max(slowed, _R["starving_slowdown"])   # below the floor nothing sprints and nothing heals
    return span * slowed


def larder_loss(s):
    """Seconds the lack of MEALS costs over the next day: hunger we will not be able to answer."""
    if s["food_items"] >= 8:
        return 0.0
    if s["food_items"] >= 2:
        return 0.15 * _T["day_s"]           # will run out before the day is done
    loss = _R["starving_slowdown"] * _T["day_s"]
    if s["food_items"] == 0:
        loss += _R["starving_death"] * _T["death_cost_s"]
    return loss


def food_loss(s):
    """What hunger costs: what it is costing now, plus what having nothing to eat will cost.

    Two terms because there are two actions. Eating answers the first; cooking and hunting answer the second. One
    number could only ever justify one of them, and it justified the wrong one.
    """
    return hunger_loss(s) + larder_loss(s)


def tool_loss(s):
    """Seconds the day's mining costs beyond what an iron pickaxe would take, plus fighting unarmed."""
    mult = {0: _K["mine_time_no_pickaxe"], 1: _K["mine_time_stone"]}.get(s["pickaxe"], _K["mine_time_iron"])
    mining = _K["mining_share_of_day"] * _T["day_s"]
    loss = mining * (mult - _K["mine_time_iron"])
    return loss


def light_loss(s):
    return 0.0 if s["torches"] else _R["dark_work_death"] * _T["death_cost_s"]


def expected_loss(s):
    """The fifth quantity for ordinary play: seconds expected to be lost from here, given what we lack.

    `kernel` reaches it through `estimate.state_price_s`, the pool through `benefit`; both are the same number,
    and every goal is worth exactly the reduction it makes to it.
    """
    return (night_loss(s) + food_loss(s) + tool_loss(s) + light_loss(s) + fight_loss(s) + hurt_loss(s)
            + bag_loss(s))


def hp_seconds(s, dhp):
    """The fourth quantity, implemented here because health is only worth what being hurt costs FROM THIS STATE:
    seconds that expecting to lose `dhp` health costs.

    Damage is a chance of dying plus a loss of margin, both continuous. The version with a branch at `dhp >= hp`
    priced every answer in a bad spot as the same certain death, so fighting, fleeing and carrying on all came out
    equal and the cheapest one (doing nothing) won. The curve is `estimate.fatal_chance`, the same one the fight
    planner's two risks and `fight_loss` read.
    """
    if dhp <= 0:
        return 0.0
    hp = float(s["hp"])
    p = _fatal_chance(hp, dhp)
    survived = dict(s, hp=max(1.0, hp - min(dhp, hp - 1.0)))
    margin = expected_loss(survived) - expected_loss(s)
    # A death costs the respawn and the walk back — never less for being hurt already: crediting the respawn's full
    # health made dying at four hearts cheaper than at twenty, and the price of the same blow fell as health did.
    reset = max(0.0, expected_loss(dict(s, hp=20)) - expected_loss(s))
    return round(p * (_T["death_cost_s"] + reset) + (1.0 - p) * margin, 1)

"""Ordinary play's threat layer: who can hurt us, how soon, and whether to fight, walk away or wall in. Pure. The old rule was two if-statements — "hostile within 5 blocks and hp > 10 → attack it", "hp ≤ 10 and hostile within 6 → run straight away" — so a skeleton twelve blocks out shot the agent dead while it kept mining, and a zombie was punched bare-handed for thirty seconds. Neither statement knew what the enemy does, only where it stands. Same shape as the fight planner: threats are (centre, reach, velocity, kind) rows with several futures each (combat_model.hypotheses), the state is a dict, the currency is seconds and hit points, and the answer is the cheapest option — fight when the expected damage of killing everything leaves a reserve, otherwise leave its reach, or wall in when leaving costs more than building does. Numbers live in play.toml under [mobs], [player], [engage]."""

import math

from . import beliefs, estimate, kernel, lifecycle

CONFIG = beliefs.CONFIG
MOBS = beliefs.MOBS
PLAYER = beliefs.PLAYER
ENGAGE = CONFIG["engage"]
# the account's length is `estimate.horizon_s`, only one (two horizons measured over 4 s and charged over 20)

class Decision:
    """kind: ignore | fight | evade | wall_in. target: entity id (fight) or spot (evade). why: for the log."""

    def __init__(self, kind, target=None, why="", cost_hp=0.0):
        self.kind, self.target, self.why, self.cost_hp = kind, target, why, cost_hp

    def __repr__(self):
        return f"Decision({self.kind}, {self.target}, {self.why!r})"

# -- perception → rows

# facts about mobs: which ones are worth noticing is the mob's property; fighting is priced below

NEUTRAL_MOBS = {"minecraft:zombified_piglin", "minecraft:piglin", "minecraft:enderman", "minecraft:wolf",
                "minecraft:bee", "minecraft:iron_golem", "minecraft:polar_bear", "minecraft:llama", "minecraft:panda",
                "minecraft:dolphin", "minecraft:spider", "minecraft:cave_spider"}

def awareness(e, here=None):
    """0..1: how much of this mob's damage is coming at us (an unprovoked neutral is 0)."""

    if e.get("type") in NEUTRAL_MOBS and not e.get("angry"):
        return 0.0
    # being in the hazard table is the hostility test (reading `hostile` too dropped the dragon's parts)
    if here is None:
        return 1.0
    d = math.dist(here, (e["x"], e["y"], e["z"]))
    notice = float(ENGAGE["notice_r"])
    if d <= notice:
        return 1.0
    return max(0.0, 1.0 - (d - notice) / notice)

def rows(near, memory, now, kinds, here=None):
    """(centre, reach, velocity, kind, aware, dps) for every entity whose type is in `kinds` ({type: reach})."""

    out = []
    for e in near or []:
        kind = e.get("type")
        if kind not in kinds:
            continue
        if e.get("health") is not None and float(e["health"]) <= 0:
            # dying: listed through its death animation (~1 s), no longer a target — as the nearest row it was
            # chosen again and again, 'target not found' with a live zombie beside us (fight_zombie_3 23:46:01)
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
    """Rows for the mobs the table knows."""

    kinds = {k: float(m["reach"]) for k, m in MOBS.items()}
    return rows(near or [], memory, now, kinds, here=here)

def ids_by_row(near, hazards):
    """Entity id for each row (matched on position), so a fight decision can name its target."""
    by_pos = {(e["x"], e["y"], e["z"]): e.get("id") for e in near or []}
    return [by_pos.get(h[0]) for h in hazards]

# names for `estimate`'s quantities: aliases, never copies; this module adds only columns and shapes
protection = beliefs.protection       # one definition, in the belief table
row = estimate.row
arrival = estimate.arrival_s
pressure = estimate.pressure_hp_s
burst_damage = estimate.burst_hp
keepoff_cost = estimate.keepoff_cost
time_to_die = estimate.time_to_die_s
fight_cost = estimate.fight_cost
hide_ratio = estimate.reaches_share

# -- the model

def escape_spot(here, hazards, blocks=None, cover=None, footing=None):
    """Where to leave every threat's reach: `blocks` away from their dps-weighted centre, under the fight's slack rule; `cover` a candidate."""

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
    assert best is not None, "options is not empty"
    return tuple(round(c) for c in best)

def evade_cost(here, spot, hazards, prot):
    """hp lost walking from here to `spot`: the pressure here, over the walk, as an integral."""
    walk_s = math.dist(here, spot) / float(PLAYER["speed"])
    return estimate.leaving_hp(estimate.pressure_hp_s(here, hazards, prot), walk_s)

class Option:
    """One answer to the threats, priced: `hp` lost, `seconds` spent acting, and what it leaves behind."""

    def __init__(self, kind, target, hp, seconds, why, leaves=0.0, heals=0.0, protects=0.0, blast_after=0.0):
        self.kind, self.target, self.hp, self.seconds, self.why = kind, target, hp, seconds, why
        self.heals, self.protects = heals, protects
        self.leaves = leaves          # hp/s still coming at us after this answer (fleeing does not kill anything)
        self.blast_after = blast_after   # one-off damage still owed afterwards; a rate cannot carry an explosion

    def __repr__(self):
        return f"Option({self.kind}, {self.hp}hp, {self.seconds}s, {self.why!r})"

    # -- kernel's action contract: an option is an action, the field below the model

    @property
    def name(self):
        return self.kind

    def effect(self, state):
        """The state afterwards: what is still coming at us for the rest of the work."""

        return dict(state, pending_hp=self.leaves * state["work_s"] + self.blast_after)

SHAPES = ("between", "under", "down")

def reshape_options(state, grid, hazards, here, press, prot, blast_here, work_s):
    """Blocking, standing on a block and digging down are one column: seconds (and blood while exposed) buying delay or no sight."""

    carried = int(state.get("blocks", 0))
    cap = int(ENGAGE.get("block_max", 4))
    # digging down spends no blocks, only diggable ground; the other two shapes spend what is carried
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
            # shaping kills nothing: what can still come at us afterwards follows as walking away's does (else a pillar
            # outbid killing a zombie) — but what the shape shuts out for good (a sealed passage: arrival inf over the
            # ground after) follows no one, and leaves nothing
            coming = [h for h in hazards if arrival(here, h, ground=after) != float("inf")]
            follows = pressure(here, coming, prot, ground=grid) * float(ENGAGE["follow_p"]) if coming else 0.0
            leaves = max(estimate.pressure_hp_s(here, hazards, prot, ground=after, shape=(where, n)), follows)
            still = [h for h in coming if arrival(here, h, ground=after) <= work_s]
            blast_after = burst_damage(here, still, prot) if still else 0.0
            if leaves > press - float(ENGAGE["shape_min_gain"]) * max(press, 1e-6) \
                    and blast_after >= blast_here - 1e-6:
                continue
            out.append(Option("reshape", (where, n), round(press * seconds, 2), seconds,
                              f"{where}: {n} × {each_s}s", leaves=round(leaves, 3), blast_after=blast_after))
    return out

def eat_options(state, hp, press, blast_here):
    """Pure: eating in a fight."""

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
    """Seconds of "carrying on" the options are priced over, which is `estimate.horizon_s` and nothing else."""

    return estimate.horizon_s(state.get("work_s"))

def _evade_option(here, spot, hazards, prot, press, out):
    """Pure: the evade column to `spot`, priced against the options already in `out` (a fight on offer)."""
    walk_s = round(math.dist(here, spot) / float(PLAYER["speed"]), 2)
    # leaving costs the walk out and back; what follows is the next round's account — unless every threat still reaches us there and a fight is on offer (then leaving only postpones it)
    postpones = any(o.kind == "fight" for o in out) and all(estimate.follows_to(spot, h) for h in hazards)
    follows = round(press if postpones else press * float(ENGAGE["follow_p"]), 3)
    # a creeper is not a rate that leaving ends: with a fight on offer its blast stays owed
    fight_on = any(o.kind == "fight" for o in out)
    blast = burst_damage(here, hazards, prot, fuse_s=float("inf")) if fight_on else burst_damage(spot, hazards, prot)
    return Option("evade", spot, evade_cost(here, spot, hazards, prot),
                  round(walk_s * 2, 2), f"leave their reach, ~{walk_s}s out and back", leaves=follows,
                  blast_after=blast)

def options(state):
    """Pure: every answer worth considering, priced."""

    here, hp = tuple(state["here"]), float(state["hp"])
    hazards = [h for h in state.get("hazards", ()) if h[3] in MOBS]
    prot = float(state.get("protection", 0.0))
    grid = state.get("field")
    press = pressure(here, hazards, prot, ground=grid) if hazards else 0.0
    blast_here = burst_damage(here, hazards, prot) if hazards else 0.0
    work_s = horizon_for(state)
    if not hazards or (press <= 0.0 and blast_here <= 0.0):
        # a creeper exerts no pressure (a blast is not a rate): tested separately, or it is always ignored
        return [Option("ignore", None, 0.0, 0.0, "nothing in reach")]
    ids = list(state.get("ids") or [None] * len(hazards))
    out = [Option("ignore", None, 0.0, 0.0,
                  f"carrying on takes ~{press:.1f} hp/s"
                  + (f" and a {blast_here:.0f} hp blast" if blast_here else ""),
                  leaves=press, blast_after=blast_here)]
    # fight: kill them and nothing is coming; a creeper with a sword is fought hit-and-back first (walking away only postpones it)
    sword = int(state.get("sword", 0))
    creepers = [i for i, h in enumerate(hazards) if MOBS[h[3]].get("burst") and h[3] == "minecraft:creeper"]
    if creepers and sword >= 1 and all(MOBS[h[3]].get("burst") is None or i in creepers
                                       for i, h in enumerate(hazards)):
        first = min(creepers, key=lambda i: math.dist(here, hazards[i][0]))
        t_c, lost_c = keepoff_cost(here, hazards[first], sword, prot)
        rest = [h for i, h in enumerate(hazards) if i != first]
        t_r, lost_r = fight_cost(hazards[first][0], rest, sword, prot) if rest else (0.0, 0.0)
        out.append(Option("fight", ids[first], round(lost_c + lost_r, 2), round(t_c + t_r, 2),
                          f"kill the creeper hit-and-back in ~{t_c}s"
                          + (f", then {len(rest)} more" if rest else "")))
    t_fight, lost = fight_cost(here, hazards, sword, prot)
    # a fight we expect to lose is not an answer: `survivable` below, the same for every column
    if not any(MOBS[h[3]].get("burst") for h in hazards):
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
        # a fight the veto removes is not "a fight on offer" for leaving to postpone (before the one veto, the fight's
        # own gate kept it out of `out`: evade at low health priced as postponing a fight nobody could take)
        out.append(_evade_option(here, spot, hazards, prot, press, [o for o in out if survivable(o, hp)]))
    for option in eat_options(state, hp, press, blast_here):
        out.append(option)
    # the shield alone is the use key held: offered only where the jar can hold it (state "hold_use", the jar's
    # feature list — 0.1.62 cannot: the answer did nothing); a fight behind the shield is the attack's own
    if state.get("shield") and state.get("hold_use", True) and prot < float(PLAYER["protection_cap"]):
        up = float(ENGAGE["shield_protects"])
        shield_s = float(ENGAGE["shield_s"])
        out.append(Option("shield", None, round(press * shield_s, 2), shield_s,
                          f"shield up: -{up:.0%} of what lands", leaves=press * (1.0 - up), protects=up))
    if grid is not None:
        for option in reshape_options(state, grid, hazards, here, press, prot, blast_here, work_s):
            out.append(option)
    if int(state.get("blocks", 0)) >= int(ENGAGE["wall_in_blocks"]):
        wall_s = float(ENGAGE["wall_in_s"])
        # a pod stops only what walks in; climbers and teleporters are still coming
        through = estimate.pressure_hp_s(here, [h for h in hazards if MOBS[h[3]].get("squeezes")], prot,
                                         ground=grid)
        out.append(Option("wall_in", None, round(press * wall_s + blast_here, 2), wall_s,
                          f"wall in, ~{wall_s}s exposed", leaves=round(through, 3)))
    return survivors(out, hp)

def survivable(option, hp):
    """Pure: what this answer expects to lose over its own seconds stays under the health we have, less a margin."""

    return float(option.hp) < hp - float(ENGAGE["survive_margin_hp"])

def survivors(out, hp):
    """Pure: the one veto every column passes (`survivable`) — only the fight had it, and a 1.2 s pillar at 3.1 hp
    beside three zombies was offered, taken, and died on (fight_zombie_3). It removes an answer only while a survivable
    one remains: when none does, the one expected to lose least is kept (leaving, usually), or low health would have
    no way out left at all — never a fight, which ends nothing before it has cost what we have. Carrying on is never vetoed: it is the account the others are priced against."""

    acts = [o for o in out if o.kind != "ignore"]
    kept = [o for o in acts if survivable(o, hp)]
    # a fight expected to cost all we have is never the way out (the fight's own gate before): it kills nothing first
    way_out = [o for o in acts if not o.kind.startswith("fight")]
    if way_out and not kept:
        kept = [min(way_out, key=lambda o: float(o.hp))]
    return [o for o in out if o.kind == "ignore" or o in kept]

def action_cost(option, price, work_s=None):
    """kernel's `cost_s` for an option: its seconds plus its health spent, priced on top of what it leaves owed — one damage price."""

    if work_s is None:
        return estimate.act_cost_s(option.seconds, option.hp, price)
    left = owed(option, work_s)
    return float(option.seconds) + price(left + float(option.hp)) - price(left)

class Answer:
    """One option, wearing kernel's action contract."""

    __slots__ = ("option", "name", "cost_s")

    def __init__(self, option, price, work_s=None):
        self.option, self.name, self.cost_s = option, option.kind, action_cost(option, price, work_s)

    def effect(self, state):
        return self.option.effect(state)

    def __repr__(self):
        return f"Answer({self.name}, {self.cost_s:.1f}s)"

class Field:
    """The threats around us, as a kernel model: one state, one price, a column per answer."""

    def __init__(self, state, price=None, refused=None):
        self.field = state
        self.price_hp = price or (lambda dhp: dhp)
        self.refused = refused          # option → why it may not be chosen now (fight_loop: it just failed), or None
        self.work_s = horizon_for(state)
        self.opts = [Answer(o, self.price_hp, self.work_s) for o in options(state)]
        self.default = next(a for a in self.opts if a.name == "ignore")

    def state(self):
        """The kernel state: what carrying on still owes us, which is exactly what the `ignore` column leaves."""

        return {"pending_hp": self.default.option.leaves * self.work_s + self.default.option.blast_after,
                "work_s": self.work_s}

    def price(self, state):
        return self.price_hp(state["pending_hp"])

    def actions(self, state):
        return self.opts

    def admissible(self, state, option):
        """Refused only what the caller says it may not choose now (an answer that just failed)."""

        why = self.refused(getattr(option, "option", option)) if self.refused is not None else None
        return (False, why) if why else (True, "")

def owed(option, work_s):
    """Pure: health still owed to us after this answer — the rate it leaves, over the work, plus any blast that still reaches us."""

    return option.leaves * work_s + option.blast_after

def saves(option, opts, price, work_s):
    """Pure: seconds this answer saves against carrying on."""

    doing_nothing = next((o for o in opts if o.kind == "ignore"), None)
    if doing_nothing is None:
        return 0.0
    return estimate.saved_s(price, owed(doing_nothing, work_s), owed(option, work_s),
                            action_cost(option, price, work_s))

def decide(state, price=None):
    """Pure: the best answer, decided by `kernel.choose` like every other plan this agent makes."""

    field = Field(state, price)
    best = (kernel.choose(field, field.state()).action or field.default).option
    return Decision(best.kind, best.target, best.why, best.hp)

# -- what the fight tells ordinary play: a rate (estimate.pressure_hp_s) and no-go circles, never a decision

def no_go(state, margin=None):
    """Pure: [(centre, radius)] the normal planner must not route through or plan work inside."""

    margin = float(ENGAGE["no_go_margin"] if margin is None else margin)
    return [(tuple(h[0]), float(h[1]) + margin) for h in state.get("hazards", ()) if h[3] in MOBS]

# -- the price of health: depends on the state it is lost from (death chance plus lost margin)
_T, _R, _K = beliefs.CONFIG["time"], beliefs.CONFIG["risk"], beliefs.CONFIG["tools"]

def bag_loss(s):
    """Seconds lost to a full bag: work whose output falls on the floor."""

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
         # dark where we stand, where mobs come from — not the same as night
         "dark": False}
    unknown = set(kw) - set(s)
    if unknown:
        raise KeyError(f"not survival state: {sorted(unknown)}")
    s.update(kw)
    return s

def _protection(s):
    """This state's damage reduction, from the belief table."""

    return beliefs.protection(s["armor"], s["shield"])

def encounter_damage(s):
    """(seconds, health) one ordinary encounter costs at this weapon and armour."""

    kind = _R["reference_mob"]
    here = (0.0, 0.0, 0.0)
    row = estimate.row((float(beliefs.PLAYER["melee_reach"]), 0.0, 0.0), beliefs.mob(kind)["reach"],
                       (0.0, 0.0, 0.0), kind)
    return estimate.fight_cost(here, [row], s["sword"], _protection(s))

_fatal_chance = estimate.fatal_chance     # one curve, in the module that owns the five quantities

def fight_loss(s):
    """Seconds a day of ordinary encounters costs at this weapon and armour — at full health."""

    kill_s, damage = encounter_damage(s)
    return _R["encounters_per_day"] * (kill_s + _fatal_chance(20, damage) * _T["death_cost_s"])

def hurt_loss(s):
    """Seconds the current health deficit costs: regeneration time plus the extra death chance before it is back."""

    hp = max(0.1, float(s["hp"]))
    if hp >= 20:
        return 0.0
    _kill_s, damage = encounter_damage(s)
    regen_s = (20.0 - hp) * _R["regen_s_per_hp"]
    meetings = _R["encounters_per_day"] * regen_s / _T["day_s"]
    extra = _fatal_chance(hp, damage) - _fatal_chance(20, damage)
    return regen_s + meetings * max(0.0, extra) * _T["death_cost_s"]

def night_loss(s):
    """Seconds the coming night is expected to cost."""

    if s["bed"]:
        return 0.0 if s["sheltered"] else _R["night_bed_open"] * (1.0 - _protection(s)) * _T["death_cost_s"]
    p = _R["night_sheltered"] if s["sheltered"] else _R["night_open"]
    if s["sword"] == 0:
        p += _R["no_sword_night"]
    if s["nights_missed"] >= 3:
        p += _R["phantom_night_death"]
    # without a bed the night is also 420 s of not working (underground counts as working)
    idle = 0.0 if s["sheltered"] else _T["night_s"]
    return p * (1.0 - _protection(s)) * _T["death_cost_s"] + idle

def hunger_loss(s):
    """Seconds the CURRENT hunger costs before the next meal: work lost to not sprinting and not regenerating."""

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
    """What hunger costs: what it is costing now, plus what having nothing to eat will cost."""

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
    """The fifth quantity for ordinary play: seconds expected to be lost from here, given what we lack."""

    return (night_loss(s) + food_loss(s) + tool_loss(s) + light_loss(s) + fight_loss(s) + hurt_loss(s)
            + bag_loss(s))

def hp_seconds(s, dhp):
    """Seconds that expecting to lose `dhp` health costs from this state."""

    if dhp <= 0:
        return 0.0
    hp = float(s["hp"])
    p = _fatal_chance(hp, dhp)
    survived = dict(s, hp=max(1.0, hp - min(dhp, hp - 1.0)))
    margin = expected_loss(survived) - expected_loss(s)
    # a death costs the respawn and walk back, never less for being hurt already
    reset = max(0.0, expected_loss(dict(s, hp=20)) - expected_loss(s))
    return round(p * (_T["death_cost_s"] + reset) + (1.0 - p) * margin, 1)

THREAT_ROWS, THREAT_IDS, THREAT_AT = [], [], 0.0


def _forget_threats():
    """The last life's threats (their ids, their rows) are nobody's now."""
    global THREAT_ROWS, THREAT_IDS, THREAT_AT
    THREAT_ROWS, THREAT_IDS, THREAT_AT = [], [], 0.0


lifecycle.on_reset(_forget_threats, covers=("THREAT_ROWS", "THREAT_IDS", "THREAT_AT"))

def threats_seen(max_age_s=3.0, now=None):
    """(rows, ids) as perception last saw them, or ([], []) when it has not looked recently enough to be trusted."""
    import time as _t
    if not THREAT_ROWS or (now or _t.time()) - THREAT_AT > max_age_s:
        return [], []
    return list(THREAT_ROWS), list(THREAT_IDS)

def seen_at():
    """When the rows above were read."""

    return THREAT_AT

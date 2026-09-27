"""The five quantities everything this agent estimates is built from. One definition each, and no decisions. Forty-odd functions across nine modules were estimating five things, and the copies drifted: arrival meant one journey in `threat` and another in `field`, pressure ran over a four-second horizon while the options reading it charged for twenty, "a block delays it" and "a block hides us" shared a function, and carrying on was charged both as a state and as a cost. Every live death this agent has been debugged out of was one of those drifts. The five (docs/architect.md): arrival_s(here, row, ground)        when it can touch us pressure_hp_s(here, rows, ...)      how fast health comes off while it can act_cost_s(seconds, hp, price)      an action's health and time, as one number price(dhp)                          what health is worth in seconds — the CALLER's, because it depends on the state being priced (`threat.hp_seconds`), which is why it arrives as an argument here and is never re-implemented state_price_s(model, state)         what the future costs from a state, in the model that owns it and the one rule that turns them into a decision: saved_s(price, before, after, cost) price(before) − price(after) − cost Everything else here is a composition of those, named because more than one caller needs it: `burst_hp` (what goes off at once, which is not a rate), `fatal_chance`, `time_to_die_s`, `fight_cost`, `leaving_hp`, `reaches_share` (what a shape does to whether a mob reaches us at all) and `horizon_s` (the length of the account, which must be one number or the quantities cannot be compared). Nothing here decides anything, nothing here reads the world, and nothing here knows what a planner is: give it rows and it gives numbers back. `threat`, `fight_plan` and `perception` hold the adapters that put their own vocabulary on these — aliases and one-line wrappers, never a second arithmetic. A row is `(centre, reach, velocity, kind, aware, dps)` and `row()` is the only way to make one."""

import math

from . import beliefs, combat_model

MOBS = beliefs.MOBS
PLAYER = beliefs.PLAYER
ENGAGE = beliefs.CONFIG["engage"]

def row(centre, reach, velocity, kind, aware=1.0, dps=None):
    """Build a threat row."""

    return (tuple(centre), float(reach), tuple(velocity), kind, float(aware),
            float(MOBS.get(kind, {}).get("dps", 0.0) if dps is None else dps))

def follows_to(spot, hazard):
    """Pure: would this threat still be after us at `spot` — inside its notice radius (it walks after us from there), or, ranged, inside its reach (it shoots from where it stands)."""

    centre, reach, _vel, kind = hazard[:4]
    mob = MOBS.get(kind, {})
    d = math.dist(spot, centre)
    return d <= float(mob.get("notice_r", 16.0)) or (bool(mob.get("ranged")) and d <= float(reach))

def horizon_s(horizon=None):
    """The account's length."""

    return float(ENGAGE["work_horizon_s"] if horizon is None else horizon)

def arrival_s(here, hazard, ground=None, horizon=None):
    """Seconds until this threat's reach covers `here`, under its worst plausible future and over this ground."""

    mob = MOBS.get(hazard[3], {})
    horizon = horizon_s(horizon)
    slower = 1.0 if ground is None else ground.slowdown(bool(mob.get("squeezes")))
    futures = combat_model.hypotheses(hazard, here, closing=float(mob.get("speed", 2.5)))
    # The geometry is asked for a window this ground can actually deliver inside, and the answer is put back on
    # the same clock. Truncating before the ground was applied and returning after it meant the cut-off and the
    # answer were different quantities: a mob could come back "arriving in thirty seconds" from a twenty-second
    # account, and every caller that reads infinity as "outside the account" was reading a number that was not.
    seconds = combat_model.min_tti(here, futures, horizon=horizon / slower)
    return seconds if seconds == float("inf") else seconds * slower

def reaches_share(shape, mob):
    """How much of this mob still reaches us once we have stood a block up, dug down, or done neither."""

    if shape is None:
        return 1.0
    where, n = shape
    if where == "between" or mob.get("squeezes"):
        return 1.0
    if mob.get("ranged"):
        return max(0.0, 1.0 - float(ENGAGE["hide_per_block"]) * n) if where == "down" else 1.0
    return max(0.0, 1.0 - n / float(ENGAGE["melee_stop_blocks"]))

def pressure_hp_s(here, hazards, prot=0.0, ground=None, horizon=None, shape=None):
    """Health per second expected at `here` over the horizon: each threat's damage rate, weighted by the share of the horizon during which it can actually reach us, and by how much of it has noticed us."""

    horizon = horizon_s(horizon)
    total, hardest = 0.0, 0.0
    for hazard in hazards:
        mob = MOBS.get(hazard[3])
        if not mob or mob.get("burst"):
            continue
        when = arrival_s(here, hazard, ground, horizon)
        # Further out than a decision takes to act on is the next decision's: perception looks again every tick,
        # and pricing now what arrives in ten seconds made a zombie across the field worth stopping work for.
        if when == float("inf") or when > float(ENGAGE["react_s"]):
            continue
        total += hazard[5] * hazard[4] * (max(0.0, horizon - when) / horizon) * reaches_share(shape, mob)
        hardest = max(hardest, float(mob.get("attack", 0.0)))
    return min(total, incoming_cap(hardest)) * (1.0 - prot)

def incoming_cap(hardest_hit):
    """The most health per second anything can take off us: after a hit the game ignores damage for `hurt_immunity_s`, so a crowd does not add up — three zombies land what one zombie's hit allows every 10 ticks."""

    return float(hardest_hit) / float(PLAYER["hurt_immunity_s"]) if hardest_hit > 0 else float("inf")

def burst_hp(spot, hazards, prot=0.0, fuse_s=None):
    """Damage from one-shot threats that can still reach `spot` before their fuse runs out."""

    fuse_s = float(ENGAGE["fuse_s"] if fuse_s is None else fuse_s)
    total = sum(float(MOBS[h[3]]["dps"]) for h in hazards
                if MOBS.get(h[3], {}).get("burst") and arrival_s(spot, h) <= fuse_s)
    return total * (1.0 - prot)

def fatal_chance(hp, damage, cap=1.0):
    """Probability that a loss of `damage` is the end of us at `hp`."""

    if damage <= 0:
        return 0.0
    return min(float(cap), math.exp(-max(0.1, float(hp)) / float(damage)))

def time_to_die_s(hp, hp_per_s):
    return float("inf") if hp_per_s <= 1e-9 else float(hp) / float(hp_per_s)

def damage_over(rate, seconds):
    """Health a rate takes off over a stretch of time."""

    return max(0.0, float(rate)) * max(0.0, float(seconds))

def act_cost_s(seconds, hp, price):
    """What doing something costs: the time it takes plus the health it spends, in one currency."""

    return float(seconds) + price(float(hp))

def leaving_hp(press, seconds):
    """Health lost during `seconds` of walking out from under `press`."""

    return round(float(press) * float(seconds) * 0.5, 2)

def _row_dps(row):
    """A threat row's damage rate: its own (`threat.row` puts it at [5]) where it carries one, else the table's."""
    return float(row[5]) if len(row) > 5 and row[5] is not None else float(MOBS[row[3]]["dps"])

def keepoff_cost(here, hazard, sword, prot, speed=None):
    """(seconds, hp lost) to kill a creeper hit-and-back: step in, one swing, back out past its blast (`keep_out`) before the fuse runs, again until it is dead (jar AttackTask footwork "keepoff")."""

    speed = float(PLAYER["speed"]) if speed is None else float(speed)
    mob = MOBS[hazard[3]]
    swing = float(ENGAGE["swing_s"])
    per_hit = float(PLAYER["dps"][str(min(3, max(0, int(sword))))]) * swing
    hits = math.ceil(float(mob["hp"]) / per_hit)
    walk = max(0.0, math.dist(here, hazard[0]) - float(PLAYER["melee_reach"])) / speed
    cycle = 2.0 * float(mob.get("keep_out", 3.0)) / speed + swing
    lost = hits * float(ENGAGE["keepoff_risk"]) * float(mob["attack"]) * (1.0 - prot)
    return round(walk + hits * cycle, 2), round(lost, 2)

def fight_cost(here, hazards, sword, prot, speed=None):
    """(seconds, hp lost) to kill every threat in melee, nearest first, while the rest keep hitting."""

    speed = float(PLAYER["speed"]) if speed is None else float(speed)
    dps = float(PLAYER["dps"][str(min(3, max(0, int(sword))))])
    order = sorted((h for h in hazards if h[3] in MOBS), key=lambda h: math.dist(here, h[0]))
    seconds = lost = 0.0
    pos = here
    for i, hazard in enumerate(order):
        mob = MOBS[hazard[3]]
        walk = max(0.0, math.dist(pos, hazard[0]) - float(PLAYER["melee_reach"])) / speed
        kill = float(mob["hp"]) / dps
        # The row's own rate (what THIS one hits for, `threat.row`), as pressure reads it — not the table's.
        cap = incoming_cap(max(float(MOBS[r[3]].get("attack", 0.0)) for r in order[i:]))
        under_fire = min(cap, sum(_row_dps(r) for r in order[i:] if MOBS[r[3]].get("ranged")))
        under_everything = min(cap, sum(_row_dps(r) for r in order[i:]))
        lost += (walk * under_fire + kill * under_everything) * (1.0 - prot)
        seconds += walk + kill
        pos = hazard[0]
    return round(seconds, 2), round(lost, 2)

def state_price_s(model, state):
    """The fifth quantity: seconds the future costs from `state`, according to the model that owns it."""

    return model.price(state)

def saved_s(price, before, after, cost_s=0.0):
    """What a change is worth: the price of the state before it, less the price of the state after, less what making the change costs."""

    return price(before) - price(after) - float(cost_s)

def eat_due(food, hp, hungry_below, max_hp, full_bar):
    """Pure: eat now — hungry (food below `hungry_below`), or hurt with the bar short of full: vanilla heals only at food ≥ 18, and fast only at a full bar (food 15, hp 10 stayed at 10)."""

    return food < hungry_below or (hp < max_hp and food < full_bar)

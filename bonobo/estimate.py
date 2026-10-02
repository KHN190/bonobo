"""The five quantities everything this agent estimates is built from. One definition each, and no decisions. Forty-odd functions across nine modules were estimating five things, and the copies drifted: arrival meant one journey in `threat` and another in `field`, pressure ran over a four-second horizon while the options reading it charged for twenty, "a block delays it" and "a block hides us" shared a function, and carrying on was charged both as a state and as a cost. Every live death this agent has been debugged out of was one of those drifts. The five (docs/architect.md): arrival_s(here, row, ground)        when it can touch us pressure_hp_s(here, rows, ...)      how fast health comes off while it can act_cost_s(seconds, hp, price)      an action's health and time, as one number price(dhp)                          what health is worth in seconds — the CALLER's, because it depends on the state being priced (`threat.hp_seconds`), which is why it arrives as an argument here and is never re-implemented state_price_s(model, state)         what the future costs from a state, in the model that owns it and the one rule that turns them into a decision: saved_s(price, before, after, cost) price(before) − price(after) − cost Everything else here is a composition of those, named because more than one caller needs it: `burst_hp` (what goes off at once, which is not a rate), `fatal_chance`, `time_to_die_s`, `fight_cost`, `leaving_hp`, `reaches_share` (what a shape does to whether a mob reaches us at all) and `horizon_s` (the length of the account, which must be one number or the quantities cannot be compared). Nothing here decides anything, nothing here reads the world, and nothing here knows what a planner is: give it rows and it gives numbers back. `threat`, `fight_plan` and `perception` hold the adapters that put their own vocabulary on these — aliases and one-line wrappers, never a second arithmetic. A row is `(centre, reach, velocity, kind, aware, dps)` and `row()` is the only way to make one."""

import math

from . import beliefs, combat_model
from .data import READ_EVERY_S     # the fight loop's poll: a target is acted on one read after it is there
from .data import HAND_ATTACKS_PER_S, HAND_DAMAGE, TIER_OF_MATERIAL, WEAPON_DAMAGE, ATTACKS_PER_S

ENGAGE = beliefs.CONFIG["engage"]

def row(centre, reach, velocity, kind, aware=1.0, dps=None):
    """Build a threat row."""

    return (tuple(centre), float(reach), tuple(velocity), kind, float(aware),
            float(beliefs.MOBS.get(kind, {}).get("dps", 0.0) if dps is None else dps))

def follows_to(spot, hazard):
    """Pure: would this threat still chase us at `spot` — inside its notice radius, or, ranged, inside its reach."""

    centre, reach, _vel, kind = hazard[:4]
    mob = beliefs.MOBS.get(kind, {})
    d = math.dist(spot, centre)
    return d <= float(mob.get("notice_r", 16.0)) or (bool(mob.get("ranged")) and d <= float(reach))

def horizon_s(horizon=None):
    """The account's length."""

    return float(ENGAGE["work_horizon_s"] if horizon is None else horizon)

def arrival_s(here, hazard, ground=None, horizon=None):
    """Seconds until this threat's reach covers `here`, under its worst plausible future and over this ground."""

    mob = beliefs.MOBS.get(hazard[3], {})
    horizon = horizon_s(horizon)
    side = ground.side_of(here, hazard[0]) if ground is not None and hasattr(ground, "side_of") else None
    slower = 1.0 if ground is None else ground.slowdown(bool(mob.get("squeezes")), side)
    if slower == float("inf"):
        return slower                # the way is sealed: it never arrives
    if not mob.get("burst") and share_of(getattr(ground, "shape_now", ()), mob) <= 0.0:
        return float("inf")          # where we stand now it can't reach us (a pillar, a hole, a cover)
    futures = combat_model.hypotheses(hazard, here, closing=float(mob.get("speed", 2.5)))
    # ask the geometry for a window this ground can deliver, and answer on the same clock (else "30 s" came from a 20 s account)
    seconds = combat_model.min_tti(here, futures, horizon=horizon / slower)
    return seconds if seconds == float("inf") else seconds * slower

def reaches_share(shape, mob):
    """How much of this mob still reaches us once we have stood a block up, dug down, or done neither."""

    if shape is None:
        return 1.0
    where, n = shape
    if where == "roof":
        # a 3×3 lid 2 high: a tall mob (enderman) can't stand where it reaches us
        return 0.0 if mob.get("tall") and n >= 9 else 1.0
    if where == "between" or mob.get("squeezes"):
        return 1.0
    if mob.get("ranged"):
        return max(0.0, 1.0 - float(ENGAGE["hide_per_block"]) * n) if where == "down" else 1.0
    # a step, not a slope: a walking mob hits while its attack box meets ours — one block down (or up) the boxes still
    # overlap and every hit lands; at melee_stop_blocks they no longer do. A 1-deep hole was priced as halving the
    # zombies' damage, chosen at 12 hp, and the bot died in it (combat__dig_in 01:38:51)
    return 0.0 if n >= float(ENGAGE["melee_stop_blocks"]) else 1.0

def share_of(shapes, mob):
    """How much of this mob reaches us through all of `shapes` (the least of them); 1 with none."""
    return min((reaches_share(s, mob) for s in shapes or ()), default=1.0)

def pressure_hp_s(here, hazards, prot=0.0, ground=None, horizon=None, shape=None):
    """Health per second expected at `here`: each threat's rate, weighted by its reachable share of the horizon and its notice."""

    horizon = horizon_s(horizon)
    shapes = ((shape,) if shape else ()) + tuple(getattr(ground, "shape_now", ()) or ())    # planned and current
    total, hardest = 0.0, 0.0
    for hazard in hazards:
        mob = beliefs.MOBS.get(hazard[3])
        if not mob or mob.get("burst"):
            continue
        when = arrival_s(here, hazard, ground, horizon)
        # further than a decision takes to act is the next decision's (a far zombie was worth stopping work for)
        if when == float("inf") or when > float(ENGAGE["react_s"]):
            continue
        total += hazard[5] * hazard[4] * (max(0.0, horizon - when) / horizon) * share_of(shapes, mob)
        hardest = max(hardest, float(mob.get("attack", 0.0)))
    return min(total, incoming_cap(hardest)) * (1.0 - prot)

def incoming_cap(hardest_hit):
    """The most health per second anything can take: hurt immunity means a crowd lands one hit per `hurt_immunity_s`."""

    return float(hardest_hit) / float(beliefs.PLAYER["hurt_immunity_s"]) if hardest_hit > 0 else float("inf")

def burst_hp(spot, hazards, prot=0.0, fuse_s=None):
    """Damage from one-shot threats that can still reach `spot` before their fuse runs out."""

    fuse_s = float(ENGAGE["fuse_s"] if fuse_s is None else fuse_s)
    total = sum(float(beliefs.MOBS[h[3]]["dps"]) for h in hazards
                if beliefs.MOBS.get(h[3], {}).get("burst") and arrival_s(spot, h) <= fuse_s)
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
    return float(row[5]) if len(row) > 5 and row[5] is not None else float(beliefs.MOBS[row[3]]["dps"])

def melee_reachable(here, hazard, ground=None):
    """Pure: a sword fight with it is on from where we stand — its feet within reach of our eyes, and the shape we
    stand in now (a pillar, a hole: Field.shape_now) not keeping it off us: we don't step down to trade blows."""
    mob = beliefs.MOBS.get(hazard[3], {})
    if share_of(getattr(ground, "shape_now", ()), mob) <= 0.0:
        return False
    dy = float(hazard[0][1]) - float(here[1])
    reach = float(beliefs.PLAYER["melee_reach"])
    return -reach <= dy <= reach + float(beliefs.PLAYER["eye_height"])

def shoot_cost(here, hazards, prot, speed=None):
    """Pure: seconds to shoot `hazards` down with a bow."""
    shots = sum(math.ceil(float(beliefs.MOBS[h[3]]["hp"]) / float(ENGAGE["arrow_hp"])) / float(ENGAGE["bow_hit_p"])
                for h in hazards if h[3] in beliefs.MOBS)
    return round(shots * float(ENGAGE["shot_s"]), 2)

def sword_hit(level):
    """(damage per hit, hits per second) of a sword level (perception.sword_level: 0 = fist, else the clipped tier
    max(1, min(3, tier))), from the game's weapon data with its attack cooldown. A level holds several swords (wood,
    gold and stone are all 1); it is priced as the weakest of them, never as a better sword than the one carried."""
    level = min(3, int(level))
    if level <= 0:
        return float(HAND_DAMAGE), float(HAND_ATTACKS_PER_S)
    held = [m for m, t in TIER_OF_MATERIAL.items() if max(1, min(3, t)) == level]
    weakest = min(held, key=lambda m: WEAPON_DAMAGE["sword"][m])
    return float(WEAPON_DAMAGE["sword"][weakest]), float(ATTACKS_PER_S["sword"][weakest])

def keepoff_cost(here, hazard, sword, prot, speed=None):
    """(seconds, hp lost) to kill a creeper hit-and-back: swing, back past its blast before the fuse, repeat."""

    speed = float(beliefs.PLAYER["speed"]) if speed is None else float(speed)
    mob = beliefs.MOBS[hazard[3]]
    per_hit, rate = sword_hit(sword)
    swing = 1.0 / rate
    hits = math.ceil(float(mob["hp"]) / per_hit)
    walk = max(0.0, math.dist(here, hazard[0]) - float(beliefs.PLAYER["melee_reach"])) / speed
    cycle = 2.0 * float(mob.get("keep_out", 3.0)) / speed + swing
    lost = hits * float(ENGAGE["keepoff_risk"]) * float(mob["attack"]) * (1.0 - prot)
    return round(walk + hits * cycle, 2), round(lost, 2)

def _ranged_dps(rows):
    """What the ranged ones among `rows` put on us while we are not trading blows, capped by hurt immunity."""
    if not rows:
        return 0.0
    cap = incoming_cap(max(float(beliefs.MOBS[r[3]].get("attack", 0.0)) for r in rows))
    return min(cap, sum(_row_dps(r) for r in rows if beliefs.MOBS[r[3]].get("ranged")))

def fight_cost(here, hazards, sword, prot, speed=None):
    """(seconds, hp lost) to kill every threat in melee, nearest first, while the rest keep hitting. Each kill is the
    whole of what the fight loop does for it: see it (one read of the game, `api.READ_EVERY_S`), walk into reach,
    swing it dead, walk onto its drops (they lie where it died, `pickup_r` short of it) — no hidden work (D6)."""

    speed = float(beliefs.PLAYER["speed"]) if speed is None else float(speed)
    per_hit, rate = sword_hit(sword)
    order = sorted((h for h in hazards if h[3] in beliefs.MOBS), key=lambda h: math.dist(here, h[0]))
    pickup = max(0.0, float(beliefs.PLAYER["melee_reach"]) - float(beliefs.PLAYER["pickup_r"])) / speed
    seconds = lost = 0.0
    pos = here
    for i, hazard in enumerate(order):
        mob = beliefs.MOBS[hazard[3]]
        walk = max(0.0, math.dist(pos, hazard[0]) - float(beliefs.PLAYER["melee_reach"])) / speed
        kill = math.ceil(float(mob["hp"]) / per_hit) / rate
        # The row's own rate (what THIS one hits for, `threat.row`), as pressure reads it — not the table's.
        cap = incoming_cap(max(float(beliefs.MOBS[r[3]].get("attack", 0.0)) for r in order[i:]))
        under_everything = min(cap, sum(_row_dps(r) for r in order[i:]))
        lost += ((READ_EVERY_S + walk) * _ranged_dps(order[i:]) + kill * under_everything
                 + pickup * _ranged_dps(order[i + 1:])) * (1.0 - prot)
        seconds += READ_EVERY_S + walk + kill + pickup
        pos = hazard[0]
    return round(seconds, 2), round(lost, 2)

def loss_q(mean_hp, hit_hp, q=None):
    """Pure: the `q` quantile (engage.fight_line_q) of the health a fight takes — its hits a Poisson count of mean
    mean_hp / hit_hp, each `hit_hp`: the spread around fight_cost's mean, in the one place the fight is priced."""
    q = float(ENGAGE["fight_line_q"] if q is None else q)
    if mean_hp <= 0 or hit_hp <= 0:
        return 0.0
    lam = mean_hp / hit_hp
    n, p = 0, math.exp(-lam)
    total = p
    while total < q:
        n += 1
        p *= lam / n
        total += p
    return n * hit_hp

def melee_loss(kinds, sword, prot):
    """(mean health lost, the hardest hit) of fighting one each of `kinds` from melee reach: fight_cost's own rows."""
    rows = [row((float(beliefs.PLAYER["melee_reach"]), 0.0, 0.0), float(beliefs.MOBS[k]["reach"]), (0.0, 0.0, 0.0), k)
            for k in kinds if k in beliefs.MOBS]
    if not rows:
        return 0.0, 0.0
    _s, lost = fight_cost((0.0, 0.0, 0.0), rows, sword, prot)
    return lost, max(float(beliefs.MOBS[r[3]]["attack"]) * (1.0 - prot) for r in rows)

def fight_line_ok(hp, floor, mean_hp, hit_hp, q=None):
    """Pure (S5): an optional fight starts only when the health above `floor` covers its loss's `q` quantile."""
    return hp - floor >= loss_q(mean_hp, hit_hp, q)

def state_price_s(model, state):
    """The fifth quantity: seconds the future costs from `state`, according to the model that owns it."""

    return model.price(state)

def saved_s(price, before, after, cost_s=0.0):
    """What a change is worth: the price of the state before it, less the price of the state after, less what making the change costs."""

    return price(before) - price(after) - float(cost_s)

def eat_due(food, hp, hungry_below, max_hp, full_bar):
    """Pure: eat now — hungry, or hurt short of a full bar (vanilla heals only at food ≥ 18, fast only when full)."""

    return food < hungry_below or (hp < max_hp and food < full_bar)

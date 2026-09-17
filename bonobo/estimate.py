"""The five quantities everything this agent estimates is built from. One definition each, and no decisions.

Forty-odd functions across nine modules were estimating five things, and the copies drifted: arrival meant one
journey in `threat` and another in `field`, pressure ran over a four-second horizon while the options reading it
charged for twenty, "a block delays it" and "a block hides us" shared a function, and carrying on was charged both
as a state and as a cost. Every live death this agent has been debugged out of was one of those drifts.

The five (docs/architect.md):

    arrival_s(here, row, ground)        when it can touch us
    pressure_hp_s(here, rows, ...)      how fast health comes off while it can
    act_cost_s(seconds, hp, price)      an action's health and time, as one number
    price(dhp)                          what health is worth in seconds — the CALLER's, because it depends on the
                                        state being priced (`survival.hp_seconds`), which is why it arrives as an
                                        argument here and is never re-implemented
    state_price_s(model, state)         what the future costs from a state, in the model that owns it

and the one rule that turns them into a decision:

    saved_s(price, before, after, cost) price(before) − price(after) − cost

Everything else here is a composition of those, named because more than one caller needs it: `burst_hp` (what goes
off at once, which is not a rate), `fatal_chance`, `time_to_die_s`, `fight_cost`, `leaving_hp`, `reaches_share`
(what a shape does to whether a mob reaches us at all) and `horizon_s` (the length of the account, which must be
one number or the quantities cannot be compared).

Nothing here decides anything, nothing here reads the world, and nothing here knows what a planner is: give it
rows and it gives numbers back. `threat`, `survival`, `fight_plan` and `perception` hold the adapters that put
their own vocabulary on these — aliases and one-line wrappers, never a second arithmetic.

A row is `(centre, reach, velocity, kind, aware, dps)` and `row()` is the only way to make one.
"""
import math

from . import beliefs, combat_model

MOBS = beliefs.MOBS
PLAYER = beliefs.PLAYER
ENGAGE = beliefs.CONFIG["engage"]


def row(centre, reach, velocity, kind, aware=1.0, dps=None):
    """Build a threat row. The one constructor, so a row always carries all six: readers that filled in a missing
    awareness or damage rate were a second definition of what a row is, and the two drifted."""
    return (tuple(centre), float(reach), tuple(velocity), kind, float(aware),
            float(MOBS.get(kind, {}).get("dps", 0.0) if dps is None else dps))


def horizon_s(horizon=None):
    """The account's length. One number: what presses us, what an answer leaves and what work we would do have to
    be measured over the same seconds or they cannot be compared."""
    return float(ENGAGE["work_horizon_s"] if horizon is None else horizon)


def arrival_s(here, hazard, ground=None, horizon=None):
    """Seconds until this threat's reach covers `here`, under its worst plausible future and over this ground.

    Beyond the horizon the answer is infinity: not "far", but "outside the account we are keeping". The ground
    only ever delays — what climbs, squeezes or teleports is barely delayed at all, which `field` knows and this
    does not need to.
    """
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
    """How much of this mob still reaches us once we have stood a block up, dug down, or done neither.

    Two different things, and reading them as one is why an agent with a stack of cobblestone stood still at four
    hearts with a zombie on it: standing up on a block breaks a MELEE mob's reach (it cannot hit what it cannot
    step to), a hole with cover breaks an ARCHER's line, and a block in the way does neither — it only delays,
    which is `field`'s business. What climbs, squeezes or teleports is unmoved by any of it.
    """
    if shape is None:
        return 1.0
    where, n = shape
    if where == "between" or mob.get("squeezes"):
        return 1.0
    if mob.get("ranged"):
        return max(0.0, 1.0 - float(ENGAGE["hide_per_block"]) * n) if where == "down" else 1.0
    return max(0.0, 1.0 - n / float(ENGAGE["melee_stop_blocks"]))


def pressure_hp_s(here, hazards, prot=0.0, ground=None, horizon=None, shape=None):
    """Health per second expected at `here` over the horizon: each threat's damage rate, weighted by the share of
    the horizon during which it can actually reach us, and by how much of it has noticed us.

    A skeleton fifteen blocks away is in reach now (arrows); a zombie eight blocks away is in reach in two seconds
    and then stays; both count, in proportion. What explodes is not here: see `burst_hp`.

    `ground` is what the ground and any blocks we placed do to how soon it arrives; `shape` is what standing on
    those blocks (or in a hole) does to whether it reaches us at all. Every caller asks THIS function — the
    reshape column used to run its own copy of this loop with its own horizon, and a hole that delayed nothing
    and hid nothing still priced better than standing still.
    """
    horizon = horizon_s(horizon)
    total = 0.0
    for hazard in hazards:
        mob = MOBS.get(hazard[3])
        if not mob or mob.get("burst"):
            continue
        when = arrival_s(here, hazard, ground, horizon)
        if when == float("inf"):
            continue
        total += hazard[5] * hazard[4] * (max(0.0, horizon - when) / horizon) * reaches_share(shape, mob)
    return total * (1.0 - prot)


def burst_hp(spot, hazards, prot=0.0, fuse_s=None):
    """Damage from one-shot threats that can still reach `spot` before their fuse runs out.

    Once, not per second. A creeper's twenty-five multiplied by a twenty-second horizon made every answer cost
    more health than a body has, so standing still and running away were priced the same and standing still won
    on the walking time.
    """
    fuse_s = float(ENGAGE["fuse_s"] if fuse_s is None else fuse_s)
    total = sum(float(MOBS[h[3]]["dps"]) for h in hazards
                if MOBS.get(h[3], {}).get("burst") and arrival_s(spot, h) <= fuse_s)
    return total * (1.0 - prot)


def fatal_chance(hp, damage, cap=1.0):
    """Probability that a loss of `damage` is the end of us at `hp`.

    Continuous and monotone in both, with no threshold: a branch at `damage >= hp` priced every answer in a bad
    spot as the same certain death, so the cheapest certain death won and the agent stood still. `cap` is for a
    planner that must still choose when everything is fatal — it bounds the answer without bending the curve, and
    it is an argument rather than a second function because a fight window and an ordinary encounter had one each.
    """
    if damage <= 0:
        return 0.0
    return min(float(cap), math.exp(-max(0.1, float(hp)) / float(damage)))


def time_to_die_s(hp, hp_per_s):
    return float("inf") if hp_per_s <= 1e-9 else float(hp) / float(hp_per_s)


def sunk_s(rate, elapsed_s, cost_s=None):
    """Seconds of work already put into something, which abandoning it would throw away.

    The marginal price of time, and the only one: `arbiter` asks it of a running intent, and nothing else may
    compute "how much have we invested" for itself. Capped at the whole job — you cannot throw away more than the
    thing was ever going to cost.
    """
    spent = max(0.0, float(elapsed_s)) * float(rate)
    return spent if cost_s is None else min(spent, float(cost_s))


def damage_over(rate, seconds):
    """Health a rate takes off over a stretch of time. Trivial, and written down because it was not: a fight
    window converted its exposure into a risk with its own constant, so the same seconds meant different damage
    to the two planners."""
    return max(0.0, float(rate)) * max(0.0, float(seconds))


def act_cost_s(seconds, hp, price):
    """What doing something costs: the time it takes plus the health it spends, in one currency.

    `price` turns health into seconds and belongs to the caller — the same four hearts cost a healthy agent and a
    dying one quite different amounts, and that difference is the whole of fight-or-flight.
    """
    return float(seconds) + price(float(hp))


def leaving_hp(press, seconds):
    """Health lost during `seconds` of walking out from under `press`.

    The integral, not the rate held flat: pressure falls as the distance opens, which is what leaving IS. Charging
    the full rate for the whole walk made escaping as lethal as fighting, so the agent did neither.
    """
    return round(float(press) * float(seconds) * 0.5, 2)


def fight_cost(here, hazards, sword, prot, speed=None):
    """(seconds, hp lost) to kill every threat in melee, nearest first, while the rest keep hitting.

    Walking to a target happens under everything that shoots; killing it happens under everything still alive.
    Coarse on purpose: it only has to separate "a zombie with a stone sword" from "three of them bare-handed".
    """
    speed = float(PLAYER["speed"]) if speed is None else float(speed)
    dps = float(PLAYER["dps"][str(min(3, max(0, int(sword))))])
    order = sorted((h for h in hazards if h[3] in MOBS), key=lambda h: math.dist(here, h[0]))
    seconds = lost = 0.0
    pos = here
    for i, hazard in enumerate(order):
        mob = MOBS[hazard[3]]
        walk = max(0.0, math.dist(pos, hazard[0]) - float(PLAYER["melee_reach"])) / speed
        kill = float(mob["hp"]) / dps
        under_fire = sum(float(MOBS[r[3]]["dps"]) for r in order[i:] if MOBS[r[3]].get("ranged"))
        under_everything = sum(float(MOBS[r[3]]["dps"]) for r in order[i:])
        lost += (walk * under_fire + kill * under_everything) * (1.0 - prot)
        seconds += walk + kill
        pos = hazard[0]
    return round(seconds, 2), round(lost, 2)


def state_price_s(model, state):
    """The fifth quantity: seconds the future costs from `state`, according to the model that owns it.

    One line, and it earns its place: it is the only thing `kernel.choose` needs to know about any model, and
    writing it down here is what stops a second scoring rule from growing beside a model's own price.
    """
    return model.price(state)


def saved_s(price, before, after, cost_s=0.0):
    """What a change is worth: the price of the state before it, less the price of the state after, less what
    making the change costs.

    The only scoring rule in the agent. A fight's `benefit`, a day's `benefit`, a threat column's `saves` and
    `kernel.choose`'s score were four spellings of this line; one of them charged the do-nothing column twice and
    came out four times too big, which is what the live bench caught. There is nothing to add to this expression —
    no urgency multiplier, no weight, no prior. If something is worth more, it is because it changes the state
    more, and that shows up in `price`.
    """
    return price(before) - price(after) - float(cost_s)

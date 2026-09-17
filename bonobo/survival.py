"""Ordinary play's objective: expected seconds lost from here on, and what each goal saves. Pure.

The goal pool used to rank on hand-written points (bed 9, food 8, torches 6) against costs in ticks. Nothing in
that could say why a bed matters — so on the night it mattered, a cheap craft outscored it. This is the same move
as the fight planner: one currency (seconds), a model of what goes wrong without each thing, and a goal's value is
the difference its declared effect makes. Change a belief about the world in play.toml, not a number in a goal.

State (built by brain.survival_state from a snapshot and memory):
    night          bool      ticks_until_dusk  int     hp  food  int
    bed  sheltered torches   bool              sword  pickaxe  tier ints (0 none, 1 stone, 2 iron)
    food_items     int       nights_missed     int
"""
import math

from . import beliefs, estimate
from .beliefs import protection as _belief_protection

CONFIG = beliefs.CONFIG
UNMEASURED = list(beliefs.UNMEASURED)
_T, _R, _K = CONFIG["time"], CONFIG["risk"], CONFIG["tools"]

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



def make_state(**kw):
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
    return _belief_protection(s["armor"], s["shield"])


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
    return round(p * (_T["death_cost_s"] + expected_loss(dict(s, hp=20)) - expected_loss(s))
                 + (1.0 - p) * margin, 1)


def advance(s, dt):
    """The state `dt` seconds from now if nothing is done. Pure.

    This is what makes "wait" an action like any other: its effect is the passage of time, and its value is the
    ordinary difference of two prices. Without it, doing nothing had no price, so it needed a floor, a desperation
    switch and an idle timer to keep it from either winning forever or never winning — three rules for one thing
    the model could have said by itself.
    """
    out = dict(s)
    out["ticks_until_dusk"] = max(0, s["ticks_until_dusk"] - dt * 20)
    if out["ticks_until_dusk"] <= 0 and not s["night"]:
        out["night"] = True
    out["food"] = max(0, s["food"] - dt / _R["food_drain_s"])
    if s["food"] >= _R["regen_food_floor"] and s["hp"] < 20:
        out["hp"] = min(20.0, s["hp"] + dt / _R["regen_s_per_hp"])
    return out


def benefit(s, effect):
    """Pure: seconds a goal saves — the one scoring rule (`estimate.saved_s`) over this model's price."""
    after = dict(s)
    after.update(effect)
    return round(estimate.saved_s(expected_loss, s, after), 1)


# ------------------------------------------------------------------------------------- the terminal goods
# The arguments of `expected_loss` that a PLAN can move, and the dimension each one is in the solver's world.
# These are the top of the value graph: nothing is wanted beyond them, and each is its own term of the loss, so
# adding them up counts nothing twice. Every item in the game is a means to one of these and is priced only
# through the fall in their prices — which is what stops a supply chain being paid once per link.
#
# `TORCHES_MEAN` is how many torches the model's "torches" means, so a per-unit price meets a per-unit worth.
# (Armour and the shield are terminal too, but they belong to the combat model and are priced there.)
TORCHES_MEAN = 8
END_DIMS = {"sheltered": "sheltered", "bed": "bed", "food_items": "food", "torches": "minecraft:torch",
            "sword": "tool:sword:1", "pickaxe": "tool:pickaxe:1"}


def urgency(s):
    """How soon the night's part of the loss falls due: 1 at dusk, decaying toward 0.5 a day out. Used to prefer
    the bed at dusk over the same bed at dawn without inventing a separate curve per goal."""
    if s["night"]:
        return 1.0
    return max(0.5, 1.0 - 0.5 * min(1.0, s["ticks_until_dusk"] / 12000.0))

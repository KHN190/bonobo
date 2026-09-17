"""One priority pool for everything, scored in one currency: seconds.

    score = success × weight × (benefit_s discounted by how long until it pays) + unlocked − cost_s

Every term is seconds, and the score is a difference, not a ratio. The old formula was

    base × urgency × unlock × weight × success / cost

— four dimensionless corrections multiplied onto a value and divided by a time. Multiplying by unnamed factors
destroys the unit: the result was a hand-written score again, only with a different denominator, and nobody could
say what any of the five factors was worth in seconds. The corrections are still here, but each enters where it
actually belongs:

  benefit_s  what this candidate saves, in seconds, already discounted for WHEN it lands — the one pricing door
             (`value.worth_s`) walks the horizon, moves the clock on by how long the work takes, and weighs each
             moment. A second discount here, on a delay the pool guessed at, was one set of seconds counted twice
  unlocks    goals this one makes possible: their benefit × probability, ADDED to this one's
  success    measured success rate: the benefit is expected (p × benefit), the cost is paid either way
  weight     Claude's hot-reloaded adjustment — the one honest multiplier, on the benefit, and shown in explain()
  cost_s     seconds of work, subtracted

A candidate is worth doing when its score is positive. That sentence was not available before.

Switching needs the challenger to beat the current task by `margin` (small, for tie jitter only). A commitment is
released when the assumptions it was made under stop holding — see `Candidate.assumptions` — not when a timer runs
out. Pure (time and file paths passed in): offline-testable."""
from . import beliefs
import json
import os
import time

from . import paths
from .survival import CONFIG as _PLAY

FILE = paths.data("priorities.json", env="MC_PRIORITIES")
CLAMP = (0.05, 20.0)
# No floor under the measured success rate. A floor was needed while `exhausted` did the giving-up: the rate could
# not be trusted to sink a candidate, so a veto removed it. With the veto gone the rate IS the giving-up — three
# failures take a three-hundred-second goal down to fifteen, and it loses to the next thing on its own arithmetic.
# Bounded away from zero only so a goal can climb back after one success.
MIN_SUCCESS = 0.02
BACKGROUND = 0.25
# No hysteresis constant. Switching costs seconds — abandoned progress, and the walk to somewhere else — and those
# seconds are computable, so they belong in the comparison rather than in a unitless margin. See `switch_cost`.
TICKS_PER_S = 20.0

# A benefit that pays a horizon away is worth half. The horizon is a day: that is the span over which this agent's
# plans are made, and it is already the survival model's unit.
DISCOUNT_HORIZON_S = float(_PLAY["time"]["day_s"])
# The shortest a commitment may be: what it costs to change your mind. See `step_commitment`.
COMMIT_FLOOR_S = float(_PLAY["pool"]["commit_floor_s"])
class NotPricedInSeconds(ValueError):
    """A candidate was built without saying, in seconds, what it is worth.

    There used to be a fallback here — points times a belief — and it was not a unit, it was a way of not having
    one: a flint and steel at "value 6" outbid a bed that saves a whole night, and nobody could see it happening
    because both numbers looked like numbers. Everything is priced in seconds now (`survival.benefit`, the credit
    table, `[yield]`, `[progress]`), so the absence of a price is a bug, and this is where it surfaces.
    """


class Candidate:
    """One thing the agent could do now, priced in seconds.

    `benefit_s` is what it is worth (already discounted for when it lands); `cost` is in ticks, as the estimates
    are; `unlocks` is [(benefit_s, probability)] for goals this one makes possible.
    """

    def __init__(self, name, base, cost, run, key=None, unlocks=(), weight=1.0, success=1.0,
                 kind="goal", cap=None, detail="", reserve=None, seconds=None, assumptions=(), risk_s=0.0,
                 share=1.0, commitment_s=None):
        if seconds is None:
            raise NotPricedInSeconds(f"{name}: say what it is worth in seconds")
        self.seconds = float(seconds)
        # `base` is kept only as the weight a caller may put on top (a directive's boost) and for the ranking log.
        self.name, self.base, self.cost, self.run = name, float(base), cost, run
        self.reserve = reserve or set()   # item ids this candidate's plan consumes (bag.RESERVED once committed)
        self.key = key or name          # retry-policy key (a goal's current step)
        self.unlocks, self.weight, self.success = list(unlocks), weight, success
        self.kind, self.cap, self.detail = kind, cap, detail
        # How much of this candidate's worth counts here. BACKGROUND for a side project: stocking blocks is real
        # work with real value, but it is not what we are doing. It scales the WHOLE benefit, unlocks included —
        # a background goal that was discounted on its own seconds and not on what it opened up scored 425 s of
        # unlocks against a 2 s body, and topped the pool over running from a zombie.
        self.share = float(share)
        self._commitment_s = None if commitment_s is None else float(commitment_s)
        # What doing this is expected to cost in blood, already priced in seconds (LiveCost.risk_s). Part of the
        # cost, not a separate concern: "this route runs past two skeletons" belongs in the number the pool sorts
        # on, or it only ever reaches the agent as a rescue after the damage.
        self.risk_s = float(risk_s)
        # Seconds this costs beyond the work itself: being off the route, being in another dimension, being
        # somewhere that charges a health tax while the work happens. Each was a veto or a multiplier before, and
        # neither could say how much.
        self.precheck = None      # () -> (ok, why): the skill's own preconditions, asked before this is offered
        self.runs = None          # (skill, args): what this candidate would actually call. `offer` derives the
                                  # precheck from it, so no candidate source can forget to attach one.
        self.off_route_s = 0.0
        self.dimension_s = 0.0
        self.goes_to = None       # where this candidate's plan takes the body; errands are priced as offsets from it
        # World facts this candidate was planned under. When one stops holding, the commitment is released and the
        # round re-plans — the fight planner's `assumptions`, in ordinary play.
        self.assumptions = list(assumptions)

    @property
    def cost_s(self):
        """What doing it costs, in seconds: the work, the blood (`gates.marginal("blood")` priced it), the detour
        (`gates.marginal("detour")`) and the portal. Every term arrives already in seconds from a door — this is
        a sum, not an estimator."""
        return self.cost / TICKS_PER_S + self.risk_s + self.off_route_s + self.dimension_s

    @property
    def commitment_s(self):
        return self.cost_s if self._commitment_s is None else self._commitment_s

    @property
    def benefit_s(self):
        """What this is worth, in seconds, before the success expectation.

        Nothing is discounted here any more. `seconds` arrives already discounted from the one pricing door
        (`value.worth_s`), which knows WHEN the benefit lands — it walks the horizon, moves the clock on by how
        long the work takes and weighs each moment by `value.discount`. Discounting again, with a `delay_s` the
        pool guessed at, was the second pipeline for the same seconds: a bed behind lava came out worth MORE than
        one underfoot because one half of the arithmetic knew about the lava and the other did not.

        `weight` and `share` remain, because neither is a price: one is Claude's thumb on the scale, the other is
        how much of a background errand's worth counts here.
        """
        # A sum, not an estimator: `seconds` and every `unlocks` entry came from the value door already
        # discounted for when they land.
        opened = sum(b * p for b, p in self.unlocks)
        return self.share * (self.seconds * self.weight + opened)

    @property
    def score(self):
        """Expected seconds gained: the benefit happens with probability `success`, the work is paid regardless.

        A subtraction of two sums, both already in seconds — nothing here estimates anything."""
        return self.success * self.benefit_s - self.cost_s

    def explain(self):
        parts = [f"{self.seconds:.0f}s"]
        if self.weight != 1.0:
            parts.append(f"×w {self.weight:g}")
        if self.unlocks:
            parts.append(f"+{sum(b * p for b, p in self.unlocks):.0f}s unlocked")
        if self.success != 1.0:
            parts.append(f"×ok {self.success:.2f}")
        work = f"{self.cost / TICKS_PER_S:.0f}s work"
        if self.risk_s:
            work += f" + {self.risk_s:.0f}s risk"
        if self.off_route_s:
            work += f" + {self.off_route_s:.0f}s off route"
        if self.dimension_s:
            work += f" + {self.dimension_s:.0f}s portal"
        return f"{self.name}: " + " ".join(parts) + f" − {work} = {self.score:+.0f}s"


# -- delays: how long until a benefit is realised. Replaces the urgency multipliers, which said "this matters
# more" without saying when, so nothing could be discounted against anything else.

SLOT_FILL_S = float(_PLAY["pool"]["slot_fill_s"])


# Geometry lives with the other facts (`beliefs.detour_s`); this name stays because the pool reads it.
detour_s = beliefs.detour_s


def effective_success(rate, state_changed):
    """The measured chance this works, or a fresh prior when the situation has changed.

    "The situation changed" is what makes this recover: somewhere else, with other tools, the old failures say
    nothing. That is also why the floor can be this low — a sunken candidate is not banned, it is waiting for the
    world to be different, and the world changes constantly.
    """
    return 1.0 if state_changed else max(MIN_SUCCESS, rate)


# -- Claude's weights (priorities.json), hot-reloaded every round
def load(path=None, now=None):
    """{target: {"x", "ban", "pin", "why"}} for unexpired entries. An entry expires at `until`, or `ttl` seconds
    after `set` (or after the file's modification time)."""
    path = path or FILE
    now = now or time.time()
    try:
        with open(path) as f:
            data = json.load(f)
        mtime = os.path.getmtime(path)
    except (OSError, ValueError):
        return {}
    out = {}
    for w in data.get("weights", []):
        until = w.get("until") or (w.get("set", mtime) + w.get("ttl", 0))
        if until > now and w.get("target"):
            out[w["target"]] = w
    return out


def weight_for(name, weights):
    """(multiplier, banned). Pins take the ceiling; multipliers are clamped so one tweak can't freeze the system."""
    w = weights.get(name)
    if not w:
        return 1.0, False
    if w.get("ban"):
        return 0.0, True
    if w.get("pin"):
        return CLAMP[1], False
    return min(CLAMP[1], max(CLAMP[0], float(w.get("x", 1.0)))), False


def add_weight(target, path=None, now=None, **fields):
    """Write/replace one weight entry (mc.py prio). ttl is required: adjustments must expire."""
    path = path or FILE
    now = now or time.time()
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    items = [w for w in data.get("weights", []) if w.get("target") != target and
             (w.get("until") or 0) > now]
    ttl = fields.pop("ttl")
    items.append({"target": target, "until": now + ttl, "set": now, **fields})
    data["weights"] = items
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    return items[-1]


# -- named weight profiles (mc.py prio profile NAME)
PROFILES = {
    # Speedrun: the dragon route only. Side projects are banned; the chain portal → blaze rods / pearls → eyes →
    # stronghold → End → dragon is boosted. Survival, safety and maintenance still run (they aren't goals).
    "speedrun": {
        "ban": ["wheat farm", "breed animals", "enchanting table", "enchant the pickaxe", "stock logs",
                "stock building blocks", "stock coal", "stock planks", "stock torches", "spare pickaxe",
                "carried door", "carried chest", "ladders (≥4)", "auto smelter", "repair worn pickaxe",
                "fire resistance potions", "stone axe", "iron sword", "stock food",
                # The bow is NOT optional any more: the dragon heals 2 hp/s off any crystal within 32 blocks, perched
                # included, and 2 of the 10 crystals sit in iron cages. Without arrows the only way to those is
                # towering up next to an exploding crystal — the most dangerous thing this agent can do.
                # No storage treks or cache chests in a speedrun: a full bag is tidied (junk thrown), never stored.
                "deposit",
                # Picked 17× in a 20-min slice without helping the route: no torches, no carried stations.
                "torches (≥8)", "carried furnace", "carried chest",
                # Placing torches doesn't advance a speedrun: with nothing else runnable, exploring finds what's missing
                # (the idle fix picked "light up" over "explore").
                "light up",
                # No sleeping in a 30-min run; dragon beds come from bartered string or seen sheep (beds for the dragon).
                "bed to carry", "bed from string"],
        "boost": {"gold helmet for piglins": 6, "nether fortress": 6, "blaze rods (7)": 8, "piglin barter": 6,
                  "ender pearls (12)": 6, "eyes of ender (12)": 8, "stronghold located": 8, "portal room found": 9,
                  "activate end portal": 10, "enter the End": 10, "defeat the ender dragon": 12,
                  "beds for the dragon": 8, "bow": 6, "arrows (32)": 6,
                  # Ready-made iron/food/obsidian in ruined portals, shipwrecks and village chests beats mining.
                  "loot nearby chests": 3,
                  "food (≥8)": 3},   # never boost a banned target: the later boost replaced the ban (bow ran anyway)
    },
}


def apply_profile(name, path=None, now=None, ttl=6 * 3600):
    """Write a profile's bans and boosts (each expiring after `ttl`), replacing earlier entries for those targets and
    lifting bans the profile boosts. Returns the number of entries written."""
    prof = PROFILES[name]
    n = 0
    for target in prof.get("ban", []):
        add_weight(target, path=path, now=now, ttl=ttl, ban=True, why=f"profile {name}")
        n += 1
    for target, x in prof.get("boost", {}).items():
        add_weight(target, path=path, now=now, ttl=ttl, x=x, why=f"profile {name}")
        n += 1
    return n


# -- choosing with persistence
def switch_cost(current, challenger, here=None):
    """Seconds it costs to drop `current` for `challenger`. Forward-looking only.

    The extra walking: going to the challenger from HERE rather than from where the current plan was already taking
    us. Nothing else — in particular NOT the time already spent on the current task. That is sunk, and charging for
    it would weld the agent to whatever it happened to start: the longer a task ran the more expensive leaving it
    became, so a task that could never finish could never be dropped.

    Inertia does not come from here. It comes from the remaining cost falling as work gets done: a job eight parts
    finished is cheap to finish and wins on its own, while a job that never progresses stays expensive and loses.
    """
    import math
    if current is None or challenger is None or current is challenger:
        return 0.0
    there, going = getattr(challenger, "goes_to", None), getattr(current, "goes_to", None)
    if here is None or there is None:
        return 0.0
    detour = math.dist(here, there) - (math.dist(going, there) if going else 0.0)
    return max(0.0, detour) / float(_PLAY["player"]["speed"])


def choose(pool, committed, held=True, here=None):
    """The candidate to run, or None when nothing is worth doing.

    Two rules, both in seconds. A challenger must beat the current task by more than the cost of switching to it
    (the extra walking). And a candidate that costs more than it saves is not worth doing at all — the score says
    so, which is what a difference in seconds is for, but nothing was listening, so `light up` at −14.7 s was
    chosen every thirty seconds forever. Nothing worth doing means nothing: the caller waits, and the idle rule
    brings back the cooling fallbacks.

    `held` is the premise check. A plan whose premise has failed is not defended: it competes from scratch.

    There is no floor and no "nothing is worth doing" case. Waiting is in the pool, priced by what the passage of
    time does to us, so the best candidate is simply the best candidate: when waiting is the right move it wins on
    its own number, and when it is not it loses to whatever loses least.
    """
    if not pool:
        return None
    ranked = sorted(pool, key=lambda c: c.score, reverse=True)
    current = next((c for c in ranked if c.name == committed), None) if (committed and held) else None
    if current is None:
        return ranked[0]
    best = max(ranked, key=lambda c: c.score - switch_cost(current, c, here))
    chosen = current if best is current or \
        best.score - switch_cost(current, best, here) <= current.score else best
    return chosen



def step_commitment(est_ticks, count=1, atomic=False):
    # An ENGINE of Δt: how long the body is promised, given what `gates.takes_s` said the work takes.
    """Seconds the body is promised to one step — never less than what changing your mind costs.

    Without the floor a step estimated at nothing ("seek 1× stone", and the stone is underfoot) was promised the
    body for zero seconds, so the commitment expired in the same tick it was made and the whole round was re-run
    at the loop's frequency. That is how one refusal — "no torches to spare" — appeared a hundred times in twenty
    seconds: not a hundred bad decisions, one decision replayed a hundred times, faster than any cooldown or
    precheck could matter. The floor is a belief (`pool.commit_floor_s`), not a constant, because it is a measured
    property of switching: re-planning, leaving the old route, putting down what was in hand.
    """
    # The boundary is where the body next comes up for air, which is one unit of work — unless the step was
    # batched into a single task chain (`actions.batched`), and then the body is busy for all of it. Promising a
    # parcel of eight blocks one block's worth is what kept the log full of "travel outlived the 6.0s commitment
    # of … re-planning" while the vein under the agent's feet went unmined.
    units = 1 if atomic else max(1, int(count))
    return max(COMMIT_FLOOR_S, (float(est_ticks) / TICKS_PER_S) / units)

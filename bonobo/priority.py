"""One priority pool for everything, scored in one currency: seconds.

    score = success × weight × (benefit_s discounted by how long until it pays) + unlocked − cost_s

Every term is seconds, and the score is a difference, not a ratio. The old formula was

    base × urgency × unlock × weight × success / cost

— four dimensionless corrections multiplied onto a value and divided by a time. Multiplying by unnamed factors
destroys the unit: the result was a hand-written score again, only with a different denominator, and nobody could
say what any of the five factors was worth in seconds. The corrections are still here, but each enters where it
actually belongs:

  benefit_s  what this candidate saves (survival.benefit) or earns, in seconds
  delay_s    how long until that saving is realised; a benefit that pays tomorrow is discounted, not multiplied
  unlocks    goals this one makes possible: their benefit × probability, ADDED to this one's
  success    measured success rate: the benefit is expected (p × benefit), the cost is paid either way
  weight     Claude's hot-reloaded adjustment — the one honest multiplier, on the benefit, and shown in explain()
  cost_s     seconds of work, subtracted

A candidate is worth doing when its score is positive. That sentence was not available before.

Switching needs the challenger to beat the current task by `margin` (small, for tie jitter only). A commitment is
released when the assumptions it was made under stop holding — see `Candidate.assumptions` — not when a timer runs
out. Pure (time and file paths passed in): offline-testable."""
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
# Legacy `value` points → seconds, for candidates that have no survival effect yet (fallbacks, directives).
SECONDS_PER_VALUE = 10.0


class Candidate:
    """One thing the agent could do now, priced in seconds.

    `benefit_s` is what it is worth; `cost` is in ticks (the estimates are); `delay_s` is how long until the benefit
    is realised — zero for "this pays the moment it is done", `ticks_until_dusk/20` for a bed. `unlocks` is
    [(benefit_s, probability)] for goals this one makes possible.
    """

    def __init__(self, name, base, cost, run, key=None, delay_s=0.0, unlocks=(), weight=1.0, success=1.0,
                 kind="goal", cap=None, detail="", reserve=None, seconds=None, assumptions=(), risk_s=0.0,
                 share=1.0, commitment_s=None):
        self.seconds = float(seconds if seconds is not None else base * SECONDS_PER_VALUE)
        self.name, self.base, self.cost, self.run = name, self.seconds / SECONDS_PER_VALUE, cost, run
        self.reserve = reserve or set()   # item ids this candidate's plan consumes (bag.RESERVED once committed)
        self.key = key or name          # retry-policy key (a goal's current step)
        self.delay_s, self.unlocks, self.weight, self.success = float(delay_s), list(unlocks), weight, success
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
        return self.cost / TICKS_PER_S + self.risk_s + self.off_route_s + self.dimension_s

    @property
    def commitment_s(self):
        return self.cost_s if self._commitment_s is None else self._commitment_s

    @property
    def benefit_s(self):
        """Discounted, weighted, and with what it unlocks — all in seconds, before the success expectation.

        Both halves are discounted, and for the same reason: seconds that arrive later are worth less. The body's
        own saving waits `delay_s` (a bed pays at dusk); what it UNLOCKS waits for the plan to finish as well,
        because nothing is unlocked until the work is done. Leaving the unlocks raw made every long plan collect
        the same future value as the short one that would have produced the same thing now — an enchanting table
        four hundred seconds away "already" made the bench and the pickaxe, so the agent stopped making either.
        """
        discounted = self.seconds / (1.0 + self.delay_s / DISCOUNT_HORIZON_S)
        arrives_in = self.delay_s + self.cost / TICKS_PER_S
        opened = sum(b * p for b, p in self.unlocks) / (1.0 + arrives_in / DISCOUNT_HORIZON_S)
        return self.share * (discounted * self.weight + opened)

    @property
    def score(self):
        """Expected seconds gained: the benefit happens with probability `success`, the work is paid regardless."""
        return self.success * self.benefit_s - self.cost_s

    def explain(self):
        parts = [f"{self.seconds:.0f}s"]
        if self.delay_s:
            parts.append(f"÷{1.0 + self.delay_s / DISCOUNT_HORIZON_S:.2f} (pays in {self.delay_s:.0f}s)")
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


def bag_delay(used_slots, total=36):
    """Seconds until the bag is full and mining starts throwing away what it digs. Full already → no delay."""
    return max(0.0, (total - used_slots)) * SLOT_FILL_S


def dusk_delay(ticks_until_dusk, night):
    """Seconds until the night's cost falls due. At night it is due now."""
    return 0.0 if night else max(0.0, ticks_until_dusk) / TICKS_PER_S


def food_delay(food_level, food_items, seconds_per_food_point=None):
    """Seconds until hunger starts costing: the food bar drains, then what is carried runs out."""
    per = seconds_per_food_point or float(_PLAY["pool"]["food_point_s"])
    return max(0.0, (food_level - _PLAY["risk"]["food_low"]) + food_items * 6) * per


def tool_delay(left_fraction, mining_share=None):
    """Seconds of work left in the best pickaxe before the next mining step stalls."""
    day = float(_PLAY["time"]["day_s"])
    share = mining_share if mining_share is not None else float(_PLAY["tools"]["mining_share_of_day"])
    return max(0.0, left_fraction) * day * share


def detour_s(distance, speed=None, here=None, there=None, via=None):
    """Seconds an errand adds. With `via` — where we are already headed — it is the OFFSET, not the round trip.

    `proximity` used to multiply the VALUE of a nearby job by two. Being close does not make a chest worth more; it
    makes it cheaper, and as a multiplier it was also incomparable with everything else in the pool.

    The offset is the part that matters for a speedrun: a chest thirty blocks away is nearly free when it sits
    between us and the portal, and costs a full minute when it is behind us. Measured against the committed path,
    a straight line from the player says neither.
    """
    import math
    speed = speed or float(_PLAY["player"]["speed"])
    if via is not None and here is not None and there is not None:
        offset = math.dist(here, there) + math.dist(there, via) - math.dist(here, via)
        return max(0.0, offset) / speed
    return 2.0 * max(0.0, distance) / speed


# -- what a goal is worth to everything that comes after it
def future_value(before, after, ends):
    """Seconds this work saves everything that comes after it, computed from the TOP so it is counted once.

        Σ_end  min(worth, before[end]) − min(worth, after[end])

    `ends` is {dimension: seconds one unit of it is worth} — the terminal goods, `survival.END_DIMS`: shelter, a
    bed, food, light, a sword, a pickaxe. They are terminal because nothing is wanted beyond them, and independent
    because each is a separate term of the survival loss. Every item in the game is a MEANS to one of them and is
    paid for here only through the fall in their prices.

    Why not sum over everything that is wanted: one saving then gets paid once per link of a supply chain. A bed
    wants wool, which wants a sword, which wants planks, a bench and a stick, and the bed's price already contains
    all of them. That is how the live pool reached two hundred thousand seconds for an enchanting table.

    Why the cap: nobody pays more for a thing than the thing saves. An end that costs more than it is worth
    contributes nothing however dear it is, and the total can never exceed what survival is worth — so no plan can
    ever be valued in hours again. This is the same cap that makes "dear is not wanted" true by construction.

    It also subsumes the two functions this replaces. A product made shows up as its own end getting cheaper; a
    bench left on the ground shows up as every end behind it getting cheaper; wood spent shows up as nothing,
    because its price did not fall — it was consumed, not left. Nothing has to be classified, so nothing can be
    classified wrongly.
    """
    total = 0.0
    for dim, worth in (ends or {}).items():
        worth = float(worth)
        if worth <= 0:
            continue
        was = min(worth, before.get(dim, float("inf")))
        now = min(worth, after.get(dim, float("inf")))
        if now < was:
            total += was - now
    return round(total, 1)


# -- success with a floor and a reset on state change
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



def step_commitment(est_ticks, count=1):
    """Seconds the body is promised to one step — never less than what changing your mind costs.

    Without the floor a step estimated at nothing ("seek 1× stone", and the stone is underfoot) was promised the
    body for zero seconds, so the commitment expired in the same tick it was made and the whole round was re-run
    at the loop's frequency. That is how one refusal — "no torches to spare" — appeared a hundred times in twenty
    seconds: not a hundred bad decisions, one decision replayed a hundred times, faster than any cooldown or
    precheck could matter. The floor is a belief (`pool.commit_floor_s`), not a constant, because it is a measured
    property of switching: re-planning, leaving the old route, putting down what was in hand.
    """
    return max(COMMIT_FLOOR_S, (float(est_ticks) / TICKS_PER_S) / max(1, int(count)))
